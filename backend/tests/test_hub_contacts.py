"""Contacts in the Hub: the page's API, vCards, and CardDAV for phones
(hub_contacts.py, hub_vcard.py, hub_carddav.py)."""

import base64

import pytest

import files_dav
import hub_apps
import hub_vcard
from test_files_server import H, client, sign_in  # noqa: F401 - the fixture
from test_hub_calendar_chat import store  # noqa: F401 - the fixture


def auth(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


ANNA = auth("anna", "anna-pass")
BEN = auth("ben", "ben-pass")

PHONE_CARD = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nPRODID:-//Apple Inc.//iPhone OS 18//EN\r\nUID:7C1F-22@icloud.com\r\n"
    "N:Muster;Max;Peter;;\r\nFN:Max Peter Muster\r\nORG:ACME;Sales\r\nTITLE:Boss\r\n"
    "item1.TEL;type=pref:+41 79 123 45 67\r\nitem1.X-ABLabel:_$!<Mobile>!$_\r\n"
    "TEL;type=WORK;type=VOICE:+41 44 111 22 33\r\n"
    "EMAIL;type=INTERNET;type=HOME;type=pref:max@example.org\r\n"
    "ADR;type=HOME;type=pref:;;Bahnhofstr. 1;Zurich;ZH;8000;Switzerland\r\n"
    "BDAY:1985-03-09\r\nNOTE:Likes\\, cake\\nand tea\r\nPHOTO;ENCODING=b;TYPE=JPEG:AAAA\r\nEND:VCARD\r\n")


@pytest.fixture
def book(store, monkeypatch):  # noqa: F811 - the fixture
    files_dav._good_credentials.clear()
    return store


def ok(response, code=207):
    assert response.status_code == code, response.get_data(as_text=True)
    return response.get_data(as_text=True)


# ── vCards ──────────────────────────────────────────────────────────────────

def test_a_phones_card_is_read_and_written_back_the_same():
    card = hub_vcard.cards(PHONE_CARD)[0]
    assert (card["first"], card["last"], card["org"], card["title"]) == ("Max Peter", "Muster", "ACME, Sales", "Boss")
    assert card["phones"] == [{"type": "mobile", "value": "+41 79 123 45 67"},
                              {"type": "work", "value": "+41 44 111 22 33"}]
    assert card["emails"] == [{"type": "home", "value": "max@example.org"}]
    assert card["addresses"][0]["value"] == "Bahnhofstr. 1\n8000 Zurich\nZH\nSwitzerland"
    assert card["birthday"] == "1985-03-09" and card["notes"] == "Likes, cake\nand tea" and card["uid"].startswith("7C1F")
    text = hub_vcard.vcard_of(card, "u1")
    assert hub_vcard.cards(text)[0] == {**card, "uid": "u1"}
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def test_old_cards_and_odd_ones_do_not_break_the_reader():
    old = ("BEGIN:VCARD\r\nVERSION:2.1\r\nFN:Zoë Test\r\nTEL;CELL:079 1\r\n"
           "NOTE;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:Caf=C3=A9\r\nBDAY:--0504\r\nEND:VCARD\r\n"
           "BEGIN:VCARD\r\nFN:Only Name\r\nEND:VCARD\r\nrubbish\r\n")
    a, b = hub_vcard.cards(old)
    assert (a["first"], a["last"]) == ("Zoë", "Test") and a["phones"][0]["type"] == "mobile"
    assert a["notes"] == "Café" and a["birthday"] == "--05-04"
    assert (b["first"], b["last"]) == ("Only", "Name")
    long_note = hub_vcard.vcard_of({"first": "A", "notes": "ä" * 200, "phones": [], "emails": [], "addresses": []}, "x")
    assert hub_vcard.cards(long_note)[0]["notes"] == "ä" * 200


# ── The page ────────────────────────────────────────────────────────────────

def test_contacts_are_kept_in_the_personal_folder_as_the_person(book):
    sign_in(book, "anna", "anna-pass")
    assert book.get("/api/contacts").get_json() == {"contacts": [], "has_own": True, "writable": True}
    item = {"first": " Max ", "last": "Muster", "phones": [{"type": "mobile", "value": "079 1"}, {"value": " "}],
            "emails": [{"type": "bogus", "value": "max@example.org"}], "birthday": "1985-03-09"}
    saved = book.post("/api/contacts/item", json={"item": item}, headers=H)
    assert saved.status_code == 200, saved.get_data(as_text=True)
    got = saved.get_json()["result"]
    assert got["first"] == "Max" and got["id"] and got["phones"] == [{"type": "mobile", "value": "079 1"}]
    assert got["emails"][0]["type"] == "other" and "uid" not in got
    assert (["files-data-write", "/mnt/alvaos/main/anna-home", ".alvaos/contacts/contacts.json"], "anna") \
        in book.data_calls
    assert book.post("/api/contacts/item", json={"item": {"first": " "}}, headers=H).status_code == 400
    assert book.post("/api/contacts/item", json={"item": {"first": "A", "birthday": "yesterday"}},
                     headers=H).status_code == 400
    edited = book.post("/api/contacts/item", json={"item": {**got, "org": "ACME"}}, headers=H).get_json()["result"]
    listed = book.get("/api/contacts").get_json()["contacts"]
    assert [(c["id"], c["org"]) for c in listed] == [(got["id"], "ACME")] and edited["id"] == got["id"]
    assert book.post("/api/contacts/delete", json={"ids": [got["id"]]}, headers=H).get_json()["result"] == {"deleted": 1}
    assert book.get("/api/contacts").get_json()["contacts"] == []


def test_import_skips_what_is_there_and_export_gives_cards_back(book):
    sign_in(book, "anna", "anna-pass")
    res = book.post("/api/contacts/import", json={"text": PHONE_CARD + PHONE_CARD}, headers=H).get_json()["result"]
    assert res == {"added": 1, "skipped": 1}
    assert book.post("/api/contacts/import", json={"text": PHONE_CARD}, headers=H).get_json()["result"] == \
        {"added": 0, "skipped": 1}
    assert book.post("/api/contacts/import", json={"text": "not a card"}, headers=H).status_code == 400
    out = book.get("/api/contacts/export")
    assert out.mimetype == "text/vcard" and "FN:Max Peter Muster" in out.get_data(as_text=True)


def test_contacts_are_closed_when_they_are_off_for_someone(book):
    hub_apps.save({"apps": {"contacts": {"people": ["anna"]}}}, ["anna", "ben"])
    sign_in(book, "ben", "ben-pass")
    assert book.get("/api/contacts").status_code == 403
    sign_in(book, "anna", "anna-pass")
    assert book.get("/api/contacts").status_code == 200


# ── The phone ───────────────────────────────────────────────────────────────

PROPFIND_BOOKS = b"""<?xml version="1.0"?><D:propfind xmlns:D="DAV:" xmlns:CR="urn:ietf:params:xml:ns:carddav"
 xmlns:CS="http://calendarserver.org/ns/"><D:prop><D:resourcetype/><D:displayname/><CS:getctag/>
 <D:current-user-privilege-set/></D:prop></D:propfind>"""


def test_a_phone_finds_the_address_book(book):
    assert book.open("/.well-known/carddav", method="PROPFIND").status_code == 301
    root = ok(book.open("/dav/anna/", method="PROPFIND", headers={**ANNA, "Depth": "0"}))
    assert "<CR:addressbook-home-set><D:href>/dav/anna/</D:href>" in root
    home = ok(book.open("/dav/anna/", method="PROPFIND", headers={**ANNA, "Depth": "1"}, data=PROPFIND_BOOKS))
    assert "/dav/anna/contacts/" in home and "<CR:addressbook/>" in home and "/dav/anna/own.main/" in home
    shelf = ok(book.open("/dav/anna/contacts/", method="PROPFIND", headers={**ANNA, "Depth": "0"}, data=PROPFIND_BOOKS))
    assert "<D:displayname>Contacts</D:displayname>" in shelf and "<D:write/>" in shelf
    assert book.open("/dav/ben/contacts/", method="PROPFIND", headers=ANNA).status_code == 404
    assert book.open("/dav/anna/contacts/", method="PROPFIND").status_code == 401


def test_a_card_from_the_phone_shows_on_the_page_and_back(book):
    put = book.open("/dav/anna/contacts/7C1F-22.vcf", method="PUT", headers=ANNA, data=PHONE_CARD.encode())
    assert put.status_code == 201 and put.headers["ETag"]
    sign_in(book, "anna", "anna-pass")
    page = book.get("/api/contacts").get_json()["contacts"]
    assert len(page) == 1 and page[0]["last"] == "Muster" and page[0]["org"] == "ACME, Sales"
    assert "uid" not in page[0] and "href" not in page[0]
    listing = ok(book.open("/dav/anna/contacts/", method="PROPFIND", headers={**ANNA, "Depth": "1"}))
    assert "/dav/anna/contacts/7C1F-22.vcf" in listing
    got = book.open("/dav/anna/contacts/7C1F-22.vcf", headers=ANNA)
    assert got.status_code == 200 and "UID:7C1F-22@icloud.com" in got.get_data(as_text=True)
    assert got.headers["ETag"] == put.headers["ETag"]

    # Changed on the page: the phone sees a new ETag, and the same card name.
    changed = book.post("/api/contacts/item", json={"item": {**page[0], "title": "Chief"}}, headers=H)
    assert changed.status_code == 200
    again = book.open("/dav/anna/contacts/7C1F-22.vcf", headers=ANNA)
    assert again.headers["ETag"] != put.headers["ETag"] and "TITLE:Chief" in again.get_data(as_text=True)

    # Changed on the phone with an old ETag: refused; with the right one: kept.
    stale = book.open("/dav/anna/contacts/7C1F-22.vcf", method="PUT", data=PHONE_CARD.encode(),
                      headers={**ANNA, "If-Match": put.headers["ETag"]})
    assert stale.status_code == 412
    fresh = book.open("/dav/anna/contacts/7C1F-22.vcf", method="PUT",
                      data=PHONE_CARD.replace("TITLE:Boss", "TITLE:Owner").encode(),
                      headers={**ANNA, "If-Match": again.headers["ETag"]})
    assert fresh.status_code == 204
    assert book.get("/api/contacts").get_json()["contacts"][0]["title"] == "Owner"
    assert len(book.get("/api/contacts").get_json()["contacts"]) == 1


def test_the_phone_asks_for_cards_by_name_and_deletes_them(book):
    book.open("/dav/anna/contacts/a.vcf", method="PUT", headers=ANNA, data=PHONE_CARD.encode())
    ask = (b'<?xml version="1.0"?><CR:addressbook-multiget xmlns:D="DAV:" xmlns:CR="urn:ietf:params:xml:ns:carddav">'
           b'<D:prop><D:getetag/><CR:address-data/></D:prop><D:href>/dav/anna/contacts/a.vcf</D:href>'
           b'<D:href>/dav/anna/contacts/nope.vcf</D:href></CR:addressbook-multiget>')
    out = ok(book.open("/dav/anna/contacts/", method="REPORT", headers=ANNA, data=ask))
    assert "BEGIN:VCARD" in out and "404 Not Found" in out
    query = (b'<?xml version="1.0"?><CR:addressbook-query xmlns:D="DAV:" xmlns:CR="urn:ietf:params:xml:ns:carddav">'
             b'<D:prop><D:getetag/></D:prop></CR:addressbook-query>')
    assert "a.vcf" in ok(book.open("/dav/anna/contacts/", method="REPORT", headers=ANNA, data=query))
    assert book.open("/dav/anna/contacts/", method="DELETE", headers=ANNA).status_code == 403
    assert book.open("/dav/anna/contacts/a.vcf", method="DELETE", headers=ANNA).status_code == 204
    assert book.open("/dav/anna/contacts/a.vcf", method="DELETE", headers=ANNA).status_code == 404
    assert book.open("/dav/anna/contacts/x.vcf", method="PUT", headers=ANNA, data=b"nonsense").status_code == 415
    assert book.open("/dav/anna/contacts/", method="MKCOL", headers=ANNA).status_code == 403


def test_contacts_work_for_a_phone_when_the_calendar_is_off(book):
    hub_apps.save({"apps": {"calendar": {"enabled": False}}}, ["anna", "ben"])
    home = ok(book.open("/dav/anna/", method="PROPFIND", headers={**ANNA, "Depth": "1"}, data=PROPFIND_BOOKS))
    assert "/dav/anna/contacts/" in home and "own.main" not in home
    assert book.open("/dav/anna/own.main/", method="PROPFIND", headers=ANNA).status_code == 403
    hub_apps.save({"apps": {"calendar": {"enabled": True}, "contacts": {"enabled": False}}}, ["anna", "ben"])
    home = ok(book.open("/dav/anna/", method="PROPFIND", headers={**ANNA, "Depth": "1"}, data=PROPFIND_BOOKS))
    assert "/dav/anna/contacts/" not in home and "own.main" in home
    assert book.open("/dav/anna/contacts/", method="PROPFIND", headers=ANNA).status_code == 403


def test_birthdays_of_the_contacts_show_in_the_calendar_read_only(book):
    sign_in(book, "anna", "anna-pass")
    assert [p["id"] for p in book.get("/api/calendar").get_json()["places"]] == ["own"]
    book.post("/api/contacts/item", json={"item": {"first": "Max", "last": "Muster", "birthday": "1985-03-09"}}, headers=H)
    book.post("/api/contacts/item", json={"item": {"first": "Berta", "birthday": "--06-30"}}, headers=H)
    book.post("/api/contacts/item", json={"item": {"first": "Nobody"}}, headers=H)
    places = book.get("/api/calendar").get_json()["places"]
    day = places[-1]
    assert day["id"] == "birthdays" and day["writable"] is False and day["calendars"][0]["id"] == "birthdays"
    assert sorted((e["title"], e["born"]) for e in day["events"]) == [("Berta's birthday", None), ("Max Muster's birthday", 1985)]
    assert sorted((e["title"], e["start"], e["repeat"], e["all_day"]) for e in day["events"]) == [
        ("Berta's birthday", "2000-06-30", "yearly", True), ("Max Muster's birthday", "1985-03-09", "yearly", True)]
    refused = book.post("/api/calendar/item", json={"place": "birthdays", "kind": "event", "item": day["events"][0]},
                        headers=H)
    assert refused.status_code == 404
    hub_apps.save({"apps": {"contacts": {"enabled": False}}}, ["anna", "ben"])
    assert [p["id"] for p in book.get("/api/calendar").get_json()["places"]] == ["own"]
