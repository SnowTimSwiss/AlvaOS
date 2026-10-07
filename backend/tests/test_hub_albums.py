"""Albums and favourites of a person (backend/hub_albums.py)."""

import json

import pytest

import files_manager
import files_server as fs
from test_files_server import H, client, sign_in  # noqa: F401 - the fixture
from test_photos_sync import PERSONAL, Disk

PIC = "anna-home/Photos/Pixel 8/Camera/IMG_1.jpg"
PIC2 = "anna-home/Photos/Pixel 8/Camera/IMG_2.jpg"
LIB = "Family/Holiday/beach.jpg"


@pytest.fixture
def albums(client, monkeypatch):  # noqa: F811
    disk = Disk()
    disk.folders["Photos/.alvaos/albums"] = {}
    monkeypatch.setattr(files_manager, "run_helper", disk.run)
    monkeypatch.setattr(files_manager, "pipe_helper", disk.pipe)
    monkeypatch.setattr(files_manager, "list_entries", disk.listing)
    shares = json.loads(open(fs.SHARES_FILE).read())
    shares["4"] = PERSONAL
    open(fs.SHARES_FILE, "w").write(json.dumps(shares))
    sign_in(client, "anna", "anna-pass")
    client.disk = disk
    return client


def lib(c):
    r = c.get("/api/photos/library")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_an_album_is_made_filled_renamed_and_deleted(albums):
    assert lib(albums) == {"albums": [], "favourites": [], "writable": True}
    made = albums.post("/api/photos/albums", json={"name": "  Summer\n 2026 ", "items": [PIC, LIB]}, headers=H)
    assert made.status_code == 201
    album = made.get_json()["album"]
    assert album["name"] == "Summer 2026" and album["items"] == [PIC, LIB]
    # it is a small file in the person's own place, written as the person
    assert f"Photos/.alvaos/albums/{album['id']}.json" in albums.disk.data
    url = f"/api/photos/albums/{album['id']}"
    changed = albums.patch(url, json={"add": [PIC2, PIC], "remove": [LIB], "name": "Summer"}, headers=H).get_json()["album"]
    assert changed["items"] == [PIC, PIC2] and changed["name"] == "Summer"      # no picture twice
    assert [a["name"] for a in lib(albums)["albums"]] == ["Summer"]
    assert albums.delete(url, headers=H).status_code == 200
    assert lib(albums)["albums"] == []


def test_hearts_are_a_list_of_their_own(albums):
    r = albums.patch("/api/photos/favourites", json={"add": [PIC, PIC2]}, headers=H)
    assert r.get_json()["favourites"] == [PIC, PIC2]
    albums.patch("/api/photos/favourites", json={"remove": [PIC]}, headers=H)
    assert lib(albums)["favourites"] == [PIC2]
    assert "Photos/.alvaos/favourites.json" in albums.disk.data


def test_what_is_not_a_picture_of_a_shared_folder_is_refused(albums):
    for bad in (["no-path"], ["/x"], ["Family/../etc/passwd"], ["Family/a//b.jpg"], "text", [PIC, 7]):
        r = albums.post("/api/photos/albums", json={"name": "x", "items": bad}, headers=H)
        assert r.status_code == 400, bad
    assert albums.post("/api/photos/albums", json={"name": " ", "items": []}, headers=H).status_code == 400
    too_many = [f"Family/{i}.jpg" for i in range(5001)]
    assert albums.patch("/api/photos/favourites", json={"add": too_many}, headers=H).status_code == 400


def test_an_unknown_album_is_not_found(albums):
    assert albums.patch("/api/photos/albums/0123456789ab", json={"name": "x"}, headers=H).status_code == 404
    assert albums.delete("/api/photos/albums/nothex", headers=H).status_code == 404


def test_only_the_person_sees_their_albums(albums):
    albums.post("/api/photos/albums", json={"name": "Private", "items": [PIC]}, headers=H)
    ben = fs.app.test_client()
    sign_in(ben, "ben", "ben-pass")
    # Ben has no personal folder for photos: nothing of Anna's reaches him
    r = ben.get("/api/photos/library")
    assert r.status_code == 409


def test_changes_need_the_hubs_header(albums):
    assert albums.post("/api/photos/albums", json={"name": "x"}).status_code == 403
    assert albums.patch("/api/photos/favourites", json={"add": [PIC]}).status_code == 403


def test_a_person_without_photos_turned_on_is_refused(albums, monkeypatch, tmp_path):
    import hub_apps
    monkeypatch.setattr(hub_apps, "SETTINGS_FILE", str(tmp_path / "hub.json"))
    hub_apps.save({"apps": {"photos": {"enabled": False}}}, [])
    r = albums.get("/api/photos/library")
    assert r.status_code == 403 and r.get_json()["app_off"] is True
