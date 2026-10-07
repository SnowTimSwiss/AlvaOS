"""The phone backup, kept in sync both ways (backend/hub_photos_sync.py)."""

import json
import os

import pytest

import files_manager
import files_server as fs
from test_files_server import H, client, sign_in  # noqa: F401 - the fixture

ROOT = "/mnt/alvaos/main/anna-home"
PERSONAL = {"name": "anna-home", "path": ROOT, "protocol": "smb", "smb_permissions": {"anna": "write"},
            "personal_for": "anna"}


class Disk:
    """The person's share as a dict: folder (below the share) -> {name: size}; a trash; app data."""

    def __init__(self):
        self.folders = {"": {}, "Photos": {}}
        self.trash = []
        self.data = {}
        self.calls = []

    def rel(self, path):
        return os.path.relpath(path, ROOT).replace(".", "", 1) if path == ROOT else os.path.relpath(path, ROOT)

    def run(self, args, timeout=600, user=None):
        self.calls.append((args, user))
        op = args[0]
        if op == "files-data-read":
            key = args[2]
            return ({"found": True, "text": self.data[key]} if key in self.data else {"found": False}), ""
        if op == "files-data-delete":
            self.data.pop(args[2], None)
            folder, name = os.path.split(args[2])
            self.folders.get(folder, {}).pop(name, None)
            return {}, ""
        if op == "files-mkdir":
            parent = "" if args[1] == ROOT else os.path.relpath(args[1], ROOT)
            self.folders.setdefault("/".join(p for p in (parent, args[2]) if p), {})
            return {}, ""
        if op == "files-trash":
            folder = os.path.relpath(args[2], ROOT)
            if args[3] not in self.folders.get(folder, {}):
                return None, "not there"
            del self.folders[folder][args[3]]
            self.trash.append({"folder": folder, "name": args[3], "type": "file"})
            return {"id": "t"}, ""
        if op == "files-trash-list":
            return {"items": list(self.trash)}, ""
        return {}, ""

    def pipe(self, args, stream, chunk=0, user=None):
        self.data[args[2]] = stream.read().decode()
        folder, name = os.path.split(args[2])
        self.folders.setdefault(folder, {})[name] = 1
        return {"written": 1}, ""

    def listing(self, path, user=None):
        folder = "" if path == ROOT else os.path.relpath(path, ROOT)
        if folder not in self.folders:
            return None, "not there"
        return [{"name": n, "type": "file", "size_bytes": s} for n, s in self.folders[folder].items()], ""


@pytest.fixture
def phone(client, monkeypatch):  # noqa: F811 - the fixture
    disk = Disk()
    monkeypatch.setattr(files_manager, "run_helper", disk.run)
    monkeypatch.setattr(files_manager, "pipe_helper", disk.pipe)
    monkeypatch.setattr(files_manager, "list_entries", disk.listing)
    shares = json.loads(open(fs.SHARES_FILE).read())
    shares["4"] = PERSONAL
    open(fs.SHARES_FILE, "w").write(json.dumps(shares))
    sign_in(client, "anna", "anna-pass")
    made = client.post("/api/photos/phones", json={"name": "Pixel 8"}, headers=H)
    assert made.status_code == 201, made.get_json()
    client.disk, client.phone = disk, made.get_json()["phone"]["id"]
    return client


def pic(local_id, name, album="Camera", size=1000):
    return {"id": local_id, "album": album, "name": name, "size": size, "modified": 1791380000000}


def plan(c, **body):
    got = c.post(f"/api/photos/phones/{c.phone}/plan", json=body, headers=H)
    assert got.status_code == 200, got.get_json()
    return got.get_json()


def upload_all(c, answer, size=1000):
    """What the app does with the plan: upload, then report."""
    for u in answer["upload"]:
        c.disk.folders.setdefault(u["path"], {})[u["name"]] = size
    c.post(f"/api/photos/phones/{c.phone}/done", headers=H, json={"items": [
        {"id": u["id"], "path": u["path"], "name": u["name"], "size": size, "modified": 1} for u in answer["upload"]]})


def test_a_phone_backs_up_its_albums_into_folders_once(phone):
    assert "Photos/Pixel 8" in phone.disk.folders
    answer = plan(phone, items=[pic("1", "IMG_1.jpg"), pic("2", "shot.png", album="Screenshots")])
    assert answer["upload"] == [{"id": "1", "share": "anna-home", "path": "Photos/Pixel 8/Camera", "name": "IMG_1.jpg"},
                                {"id": "2", "share": "anna-home", "path": "Photos/Pixel 8/Screenshots", "name": "shot.png"}]
    assert "Photos/Pixel 8/Screenshots" in phone.disk.folders           # the album folder is made as anna
    upload_all(phone, answer)
    assert plan(phone, items=[pic("1", "IMG_1.jpg"), pic("2", "shot.png", album="Screenshots")])["upload"] == []
    listed = phone.get("/api/photos/phones").get_json()["phones"][0]
    assert (listed["name"], listed["count"], listed["albums"]) == ("Pixel 8", 2, ["Camera", "Screenshots"])
    assert all(user == "anna" for _, user in phone.disk.calls)          # always as the person


def test_a_reinstalled_app_uploads_nothing_twice_and_a_different_file_gets_its_own_name(phone):
    phone.disk.folders["Photos/Pixel 8/Camera"] = {"IMG_1.jpg": 1000, "IMG_2.jpg": 5}
    answer = plan(phone, items=[pic("1", "IMG_1.jpg"), pic("2", "IMG_2.jpg")])
    assert [u["name"] for u in answer["upload"]] == ["IMG_2 (2).jpg"]   # same name, other size


def test_deleted_on_the_nas_is_deleted_on_the_phone_but_moved_is_only_forgotten(phone):
    answer = plan(phone, items=[pic("1", "a.jpg"), pic("2", "b.jpg"), pic("3", "c.jpg")])
    upload_all(phone, answer)
    camera = phone.disk.folders["Photos/Pixel 8/Camera"]
    del camera["a.jpg"]
    phone.disk.trash.append({"folder": "Photos/Pixel 8/Camera", "name": "a.jpg"})   # deleted in Photos or Files
    del camera["b.jpg"]                                                              # moved somewhere else
    answer = plan(phone, items=[pic("1", "a.jpg"), pic("2", "b.jpg"), pic("3", "c.jpg")])
    assert answer["delete_on_phone"] == ["1"] and answer["forgotten"] == 1 and answer["upload"] == []
    phone.post(f"/api/photos/phones/{phone.phone}/deleted", json={"ids": ["1"]}, headers=H)
    assert plan(phone, items=[pic("3", "c.jpg")])["delete_on_phone"] == []


def test_a_whole_album_deleted_on_the_nas(phone):
    upload_all(phone, plan(phone, items=[pic("1", "a.jpg", album="WhatsApp Images")]))
    del phone.disk.folders["Photos/Pixel 8/WhatsApp Images"]
    phone.disk.trash.append({"folder": "Photos/Pixel 8", "name": "WhatsApp Images", "type": "folder"})
    assert plan(phone, items=[])["delete_on_phone"] == ["1"]


def test_deleted_on_the_phone_goes_to_the_nas_trash_unless_freed_up(phone):
    upload_all(phone, plan(phone, items=[pic("1", "a.jpg"), pic("2", "b.jpg")]))
    answer = plan(phone, items=[], deleted=["1", "2"], keep=["2"])
    assert answer["trashed"] == 1
    assert phone.disk.folders["Photos/Pixel 8/Camera"] == {"b.jpg": 1000}           # freed up: kept on the NAS
    assert phone.disk.trash[-1] == {"folder": "Photos/Pixel 8/Camera", "name": "a.jpg", "type": "file"}
    # Both are forgotten now: putting b back on the phone does not upload it again.
    assert plan(phone, items=[pic("9", "b.jpg")])["upload"] == []


def test_freed_up_in_advance_is_kept_when_the_phone_deletes_it_later(phone):
    upload_all(phone, plan(phone, items=[pic("1", "a.jpg")]))
    plan(phone, items=[pic("1", "a.jpg")], keep=["1"])
    assert plan(phone, items=[], deleted=["1"])["trashed"] == 0
    assert "a.jpg" in phone.disk.folders["Photos/Pixel 8/Camera"]


def test_nothing_strange_gets_through(phone):
    bad = phone.post(f"/api/photos/phones/{phone.phone}/plan", headers=H,
                     json={"items": [{"id": "1", "album": "x", "name": "a", "size": "lots"}]})
    assert bad.status_code == 400
    sneaky = plan(phone, items=[pic("1", "../../etc/passwd", album="../..")])["upload"][0]
    assert sneaky["path"] == "Photos/Pixel 8/Camera" and sneaky["name"] == "etc passwd"
    phone.post(f"/api/photos/phones/{phone.phone}/done", headers=H, json={"items": [
        {"id": "7", "path": "Photos/Other phone/Camera", "name": "x.jpg", "size": 1}]})
    assert phone.get("/api/photos/phones").get_json()["phones"][0]["count"] == 0     # not its own folder
    assert phone.post("/api/photos/phones/../plan", json={}, headers=H).status_code in (404, 405)
    assert phone.post("/api/photos/phones/0123456789ab/plan", json={}, headers=H).status_code == 404


def test_two_phones_get_their_own_folders_and_can_be_forgotten(phone):
    other = phone.post("/api/photos/phones", json={"name": "Pixel 8"}, headers=H).get_json()["phone"]
    assert other["folder"] == "Pixel 8 2"
    assert phone.delete(f"/api/photos/phones/{other['id']}", headers=H).status_code == 200
    assert [p["name"] for p in phone.get("/api/photos/phones").get_json()["phones"]] == ["Pixel 8"]
