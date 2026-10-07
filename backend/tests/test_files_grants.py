"""Sharing a folder with people on the NAS (files_server.py _granted, /api/grants)."""

import base64

import files_dav
import files_manager
from test_files_server import H, client, sign_in  # noqa: F401 - the fixture

HOLIDAY = "Holiday (from anna)"


def share_holiday(c, access="read", to=("ben",)):
    sign_in(c, "anna", "anna-pass")
    return c.post("/api/grants", json={"share": "Anna", "path": "Photos/Holiday", "to": list(to), "access": access},
                  headers=H)


def test_a_person_shares_a_folder_and_the_other_sees_it_as_their_own(client):  # noqa: F811
    sign_in(client, "ben", "ben-pass")
    assert "Anna" not in [s["name"] for s in client.get("/api/me").get_json()["shares"]]
    assert client.get("/api/people").get_json()["people"] == ["anna"]
    made = share_holiday(client)
    assert made.status_code == 200, made.get_json()
    assert made.get_json()["grant"]["to"] == ["ben"]
    sign_in(client, "ben", "ben-pass")
    shares = {s["name"]: s for s in client.get("/api/me").get_json()["shares"]}
    assert shares[HOLIDAY] == {"name": HOLIDAY, "access": "read", "from": "anna"}
    client.calls.clear()
    assert client.get("/api/list", query_string={"share": HOLIDAY, "path": "Day 1"}).status_code == 200
    # As anna, below her folder only: Linux checks her rights, not more.
    assert client.calls == [(["files-list", "/mnt/alvaos/main/Anna/Photos/Holiday/Day 1"], "anna")]
    assert client.get("/api/list", query_string={"share": HOLIDAY, "path": "../.."}).status_code == 404
    refused = client.post("/api/mkdir", json={"share": HOLIDAY, "path": "", "name": "x"}, headers=H)
    assert refused.status_code == 403


def test_changing_too_and_deleting_go_to_the_owners_trash(client):  # noqa: F811
    share_holiday(client, "write")
    sign_in(client, "ben", "ben-pass")
    client.calls.clear()
    assert client.post("/api/mkdir", json={"share": HOLIDAY, "path": "", "name": "New"}, headers=H).status_code == 200
    client.post("/api/delete", json={"share": HOLIDAY, "path": "", "names": ["old.jpg"]}, headers=H)
    assert (["files-mkdir", "/mnt/alvaos/main/Anna/Photos/Holiday", "New"], "anna") in client.calls
    assert (["files-trash", "/mnt/alvaos/main/Anna", "/mnt/alvaos/main/Anna/Photos/Holiday", "old.jpg"],
            "anna") in client.calls
    # Anna's whole trash is not his to look through; links stay hers to make.
    assert client.get("/api/trash", query_string={"share": HOLIDAY}).get_json()["items"] == []
    link = client.post("/api/links", json={"share": HOLIDAY, "path": "a.jpg", "days": 7}, headers=H)
    assert link.status_code == 403
    again = client.post("/api/grants", json={"share": HOLIDAY, "path": "", "to": ["anna"], "access": "read"},
                        headers=H)
    assert again.status_code == 403


def test_never_more_than_the_owner_has_and_it_ends_with_her_access(client):  # noqa: F811
    sign_in(client, "ben", "ben-pass")
    # Ben may only read Family: he cannot let anyone change it.
    refused = client.post("/api/grants", json={"share": "Family", "path": "", "to": ["anna"], "access": "write"},
                          headers=H)
    assert refused.status_code == 403
    assert share_holiday(client, to=["nobody"]).status_code == 400
    assert share_holiday(client, to=["anna"]).status_code == 400
    share_holiday(client, "write")
    # Anna loses her folder: so does ben.
    import json

    import files_server as fs
    shares = json.loads(open(fs.SHARES_FILE).read())
    shares["2"]["smb_permissions"] = {"anna": "read"}
    open(fs.SHARES_FILE, "w").write(json.dumps(shares))
    sign_in(client, "ben", "ben-pass")
    assert {s["name"]: s["access"] for s in client.get("/api/me").get_json()["shares"]}[HOLIDAY] == "read"
    shares["2"]["smb_permissions"] = {}
    open(fs.SHARES_FILE, "w").write(json.dumps(shares))
    assert HOLIDAY not in [s["name"] for s in client.get("/api/me").get_json()["shares"]]


def test_stopping_to_share(client):  # noqa: F811
    grant = share_holiday(client).get_json()["grant"]
    assert client.get("/api/grants", query_string={"share": "Anna", "path": "Photos/Holiday"}).get_json()[
        "grants"][0]["id"] == grant["id"]
    sign_in(client, "ben", "ben-pass")
    assert client.post(f"/api/grants/{grant['id']}/delete", headers=H).status_code == 404   # not his
    sign_in(client, "anna", "anna-pass")
    assert client.post(f"/api/grants/{grant['id']}/delete", headers=H).status_code == 200
    sign_in(client, "ben", "ben-pass")
    assert HOLIDAY not in [s["name"] for s in client.get("/api/me").get_json()["shares"]]
    # An empty list also stops it.
    share_holiday(client)
    client.post("/api/grants", json={"share": "Anna", "path": "Photos/Holiday", "to": [], "access": "read"},
                headers=H)
    assert client.get("/api/grants").get_json()["grants"] == []


def test_webdav_shows_it_too_and_acts_as_the_owner(client, monkeypatch):  # noqa: F811
    share_holiday(client)
    files_dav._good_credentials.clear()
    seen = []
    monkeypatch.setattr(files_manager, "list_entries", lambda path, user=None: (seen.append((path, user)) or [], ""))
    dav = files_dav.app.test_client()
    ben = {"Authorization": "Basic " + base64.b64encode(b"ben:ben-pass").decode(), "Depth": "1"}
    root = dav.open("/", method="PROPFIND", headers=ben).get_data(as_text=True)
    assert "Holiday%20%28from%20anna%29" in root
    assert dav.open("/Holiday (from anna)/", method="PROPFIND", headers=ben).status_code == 207
    assert ("/mnt/alvaos/main/Anna/Photos/Holiday", "anna") in seen
