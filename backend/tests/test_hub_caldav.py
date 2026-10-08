"""The Hub calendar over CalDAV (backend/hub_caldav.py)."""

import base64
import json

import pytest

import files_dav
import hub_apps
import hub_caldav as cd
from test_files_server import H, client, sign_in  # noqa: F401 - the fixture
from test_hub_calendar_chat import store  # noqa: F401 - the fixture


def auth(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


ANNA = auth("anna", "anna-pass")
BEN = auth("ben", "ben-pass")

PROPFIND_HOME = b"""<?xml version="1.0"?><D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"
 xmlns:CS="http://calendarserver.org/ns/"><D:prop><D:resourcetype/><D:displayname/><CS:getctag/>
 <C:supported-calendar-component-set/><D:current-user-privilege-set/></D:prop></D:propfind>"""

PHONE_EVENT = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iOS 18//EN\r\n"
    "BEGIN:VTIMEZONE\r\nTZID:Europe/Zurich\r\nEND:VTIMEZONE\r\n"
    "BEGIN:VEVENT\r\nUID:5F3A-11@icloud.com\r\nDTSTAMP:20261005T080000Z\r\n"
    "DTSTART;TZID=Europe/Zurich:20261012T180000\r\nDTEND;TZID=Europe/Zurich:20261012T193000\r\n"
    "SUMMARY:Choir\\, with Ben\r\nLOCATION:Church hall\r\nDESCRIPTION:Bring the\\nnotes\r\n"
    "RRULE:FREQ=WEEKLY;COUNT=4\r\n"
    "BEGIN:VALARM\r\nTRIGGER:-PT15M\r\nACTION:DISPLAY\r\nSUMMARY:not the event\r\nEND:VALARM\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n")


@pytest.fixture
def dav(store, monkeypatch):  # noqa: F811 - the fixture
    files_dav._good_credentials.clear()
    monkeypatch.setenv("TZ", "Europe/Zurich")
    import time
    time.tzset()
    return store


def ok(response, code=207):
    assert response.status_code == code, response.get_data(as_text=True)
    return response.get_data(as_text=True)


def test_phones_find_the_calendars_and_need_a_password(dav):
    assert dav.open("/.well-known/caldav", method="PROPFIND").status_code == 301
    assert dav.open("/dav/", method="PROPFIND").status_code == 401
    assert dav.open("/dav/", method="PROPFIND", headers=auth("admin", "x")).status_code == 401
    assert "/dav/anna/" in ok(dav.open("/", method="PROPFIND", headers={**ANNA, "Depth": "0"}))
    root = ok(dav.open("/dav/", method="PROPFIND", headers={**ANNA, "Depth": "0"}))
    assert "<D:current-user-principal><D:href>/dav/anna/</D:href>" in root
    home = ok(dav.open("/dav/anna/", method="PROPFIND", headers={**ANNA, "Depth": "1"}, data=PROPFIND_HOME))
    assert "/dav/anna/own.main/" in home and "/dav/anna/own~tasks/" in home
    assert '<C:comp name="VEVENT"/>' in home and '<C:comp name="VTODO"/>' in home
    assert "<D:write/>" in home
    # Someone else's calendars are not there for her.
    assert dav.open("/dav/ben/", method="PROPFIND", headers=ANNA).status_code == 404


def test_an_event_from_the_page_reaches_the_phone(dav):
    sign_in(dav, "anna", "anna-pass")
    event = {"title": "Dentist; at 9", "start": "2026-10-05T09:00", "end": "2026-10-05T10:00", "calendar": "main",
             "repeat": "weekdays", "until": "2026-10-30"}
    item = dav.post("/api/calendar/item", json={"place": "own", "kind": "event", "item": event},
                    headers=H).get_json()["item"]
    listing = ok(dav.open("/dav/anna/own.main/", method="PROPFIND", headers={**ANNA, "Depth": "1"}))
    assert f"/dav/anna/own.main/{item['id']}.ics" in listing and "<D:getetag>" in listing
    got = dav.get(f"/dav/anna/own.main/{item['id']}.ics", headers=ANNA)
    text = got.get_data(as_text=True)
    assert got.headers["ETag"] == cd.etag(item)
    assert "SUMMARY:Dentist\\; at 9\r\n" in text and "DTSTART:20261005T090000\r\n" in text
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;UNTIL=20261030T235959\r\n" in text
    multiget = (b'<C:calendar-multiget xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop><D:getetag/>'
                b'<C:calendar-data/></D:prop><D:href>/dav/anna/own.main/' + item["id"].encode() + b'.ics</D:href>'
                b'<D:href>/dav/anna/own.main/gone.ics</D:href></C:calendar-multiget>')
    report = ok(dav.open("/dav/anna/own.main/", method="REPORT", headers=ANNA, data=multiget))
    assert "BEGIN:VEVENT" in report and "HTTP/1.1 404 Not Found" in report


def test_an_event_from_the_phone_shows_on_the_page_and_keeps_its_name(dav):
    put = dav.put("/dav/anna/own.main/5F3A-11.ics", headers={**ANNA, "If-None-Match": "*"}, data=PHONE_EVENT)
    assert put.status_code == 201 and put.headers["ETag"]
    sign_in(dav, "anna", "anna-pass")
    event = dav.get("/api/calendar").get_json()["places"][0]["events"][0]
    assert (event["title"], event["location"], event["notes"]) == ("Choir, with Ben", "Church hall", "Bring the\nnotes")
    assert (event["start"], event["end"], event["repeat"], event["until"]) == \
        ("2026-10-12T18:00", "2026-10-12T19:30", "weekly", "2026-11-02")
    # The page changes it: the phone still finds it under its own name and UID.
    dav.post("/api/calendar/item", json={"place": "own", "kind": "event",
                                         "item": {**event, "title": "Choir practice"}}, headers=H)
    text = dav.get("/dav/anna/own.main/5F3A-11.ics", headers=ANNA).get_data(as_text=True)
    assert "UID:5F3A-11@icloud.com" in text and "SUMMARY:Choir practice" in text
    # A phone with an old copy may not overwrite the newer one.
    assert dav.put("/dav/anna/own.main/5F3A-11.ics", headers={**ANNA, "If-Match": put.headers["ETag"]},
                   data=PHONE_EVENT).status_code == 412
    assert dav.put("/dav/anna/own.main/5F3A-11.ics", headers={**ANNA, "If-None-Match": "*"},
                   data=PHONE_EVENT).status_code == 412
    assert dav.delete("/dav/anna/own.main/5F3A-11.ics", headers=ANNA).status_code == 204
    assert dav.get("/api/calendar").get_json()["places"][0]["events"] == []


def test_whole_days_and_tasks(dav):
    whole = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART;VALUE=DATE:20261224\r\n"
             "DTEND;VALUE=DATE:20261227\r\nSUMMARY:Christmas\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert dav.put("/dav/anna/own.main/x.ics", headers=ANNA, data=whole).status_code == 201
    text = dav.get("/dav/anna/own.main/x.ics", headers=ANNA).get_data(as_text=True)
    assert "DTSTART;VALUE=DATE:20261224\r\nDTEND;VALUE=DATE:20261227\r\n" in text
    todo = ("BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:t1\r\nSUMMARY:Call grandma\r\nDUE:20261006T170000Z\r\n"
            "STATUS:NEEDS-ACTION\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")
    assert dav.put("/dav/anna/own~tasks/t1.ics", headers=ANNA, data=todo).status_code == 201
    assert dav.put("/dav/anna/own.main/t1.ics", headers=ANNA, data=todo).status_code == 415
    sign_in(dav, "anna", "anna-pass")
    place = dav.get("/api/calendar").get_json()["places"][0]
    assert place["events"][0]["all_day"] and place["events"][0]["end"] == "2026-12-26"
    task = place["tasks"][0]
    assert (task["title"], task["date"], task["time"], task["done"]) == ("Call grandma", "2026-10-06", "19:00", False)
    done = todo.replace("NEEDS-ACTION", "COMPLETED")
    assert dav.put("/dav/anna/own~tasks/t1.ics", headers=ANNA, data=done).status_code == 204
    assert dav.get("/api/calendar").get_json()["places"][0]["tasks"][0]["done"] is True


def test_family_calendars_sync_and_read_only_ones_stay_so(dav):
    hub_apps.save({"apps": {"calendar": {"libraries": ["Family"]}}}, ["anna", "ben"], shares=["Family"])
    home = ok(dav.open("/dav/ben/", method="PROPFIND", headers={**BEN, "Depth": "1"}, data=PROPFIND_HOME))
    assert "/dav/ben/shared-Family.main/" in home and "<D:write/>" not in home
    assert dav.put("/dav/ben/shared-Family.main/a.ics", headers=BEN, data=PHONE_EVENT).status_code == 403
    assert dav.put("/dav/anna/shared-Family.main/a.ics", headers=ANNA, data=PHONE_EVENT).status_code == 201


def test_calendar_off_means_no_sync_and_nothing_strange_is_accepted(dav):
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><D:propfind xmlns:D="DAV:"/>'
    assert dav.open("/dav/anna/", method="PROPFIND", headers=ANNA, data=bomb).status_code == 400
    assert dav.put("/dav/anna/own.main/../x.ics", headers=ANNA, data=PHONE_EVENT).status_code in (403, 404)
    assert dav.open("/dav/anna/new/", method="MKCALENDAR", headers=ANNA).status_code == 403
    assert dav.delete("/dav/anna/own.main/", headers=ANNA).status_code == 403
    hub_apps.save({"apps": {"calendar": {"people": ["ben"]}}}, ["anna", "ben"])
    assert dav.open("/dav/anna/own.main/", method="PROPFIND", headers=ANNA).status_code == 403
    assert "own.main" not in ok(dav.open("/dav/anna/", method="PROPFIND", headers=ANNA))   # only Contacts is left


def test_a_phone_may_rename_and_recolour_a_calendar(dav):
    body = (b'<D:propertyupdate xmlns:D="DAV:" xmlns:A="http://apple.com/ns/ical/"><D:set><D:prop>'
            b'<D:displayname>Me</D:displayname><A:calendar-color>#0B8043FF</A:calendar-color>'
            b'<A:calendar-order>2</A:calendar-order></D:prop></D:set></D:propertyupdate>')
    ok(dav.open("/dav/anna/own.main/", method="PROPPATCH", headers=ANNA, data=body))
    sign_in(dav, "anna", "anna-pass")
    c = dav.get("/api/calendar").get_json()["places"][0]["calendars"][0]
    assert (c["name"], c["color"]) == ("Me", "#0b8043")


def test_long_lines_are_folded_without_breaking_letters():
    item = {"id": "a", "title": "ü" * 100, "start": "2026-10-05", "end": "2026-10-05", "all_day": True}
    text = cd.ics_of(item, "event")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    props = dict(cd.components(text))["VEVENT"]
    assert cd._unescape(props["SUMMARY"][1]) == "ü" * 100
    assert json.dumps(cd._repeat("FREQ=MONTHLY;COUNT=3", __import__("datetime").date(2026, 1, 31))) == \
        '["monthly", "2026-03-31"]'
