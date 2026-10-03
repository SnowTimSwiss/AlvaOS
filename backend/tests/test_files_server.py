"""AlvaOS Files as its own app (backend/files_server.py)."""

import json

import pytest

import files_manager
import files_server as fs
from password_utils import hash_password

SHARES = {
    "1": {"name": "Family", "path": "/mnt/alvaos/main/Family", "protocol": "smb",
          "smb_permissions": {"anna": "write", "ben": "read"}},
    "2": {"name": "Anna", "path": "/mnt/alvaos/main/Anna", "protocol": "smb", "smb_permissions": {"anna": "write"}},
    "3": {"name": "Backups", "path": "/mnt/alvaos/main/Backups", "protocol": "nfs"},
}
H = {"X-AlvaOS-Files": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    users = {"anna": {"files_auth": hash_password("anna-pass")}, "ben": {"files_auth": hash_password("ben-pass")},
             "old": {"created_at": "2025"}}
    for name, data in (("users.json", users), ("shares.json", SHARES),
                       ("auth.json", hash_password("admin-pass"))):
        (tmp_path / name).write_text(json.dumps(data))
    monkeypatch.setattr(fs, "USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setattr(fs, "SHARES_FILE", str(tmp_path / "shares.json"))
    monkeypatch.setattr(fs, "AUTH_FILE", str(tmp_path / "auth.json"))
    monkeypatch.setattr(fs, "SESSIONS_FILE", str(tmp_path / "sessions.json"))
    monkeypatch.setattr(fs, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fs, "LINKS_FILE", str(tmp_path / "links.json"))
    monkeypatch.setattr(fs, "SECRET_FILE", str(tmp_path / "secret"))
    fs._attempts.clear()
    calls = []
    monkeypatch.setattr(files_manager, "run_helper",
                        lambda args, timeout=600, user=None: (calls.append((args, user)) or {}, ""))
    monkeypatch.setattr(files_manager, "list_entries",
                        lambda path, user=None: (calls.append((["files-list", path], user)) or [], ""))
    c = fs.app.test_client()
    c.calls = calls
    return c


def sign_in(c, user, password):
    return c.post("/api/login", json={"username": user, "password": password})


def test_people_sign_in_with_their_share_password(client):
    assert sign_in(client, "anna", "wrong").status_code == 401
    assert sign_in(client, "nobody", "x").status_code == 401
    old = sign_in(client, "old", "whatever")
    assert old.status_code == 401 and "set your password once more" in old.get_json()["error"]
    ok = sign_in(client, "Anna", "anna-pass")
    assert ok.status_code == 200
    assert "HttpOnly" in ok.headers["Set-Cookie"] and "SameSite=Strict" in ok.headers["Set-Cookie"]


def test_each_person_sees_their_shares_with_their_rights(client):
    sign_in(client, "ben", "ben-pass")
    assert client.get("/api/me").get_json()["shares"] == [{"name": "Family", "access": "read"}]
    assert client.get("/api/list?share=Anna").status_code == 404
    client.get("/api/list?share=Family&path=Holidays")
    assert client.calls[-1] == (["files-list", "/mnt/alvaos/main/Family/Holidays"], "ben")
    refused = client.post("/api/mkdir", json={"share": "Family", "path": "", "name": "x"}, headers=H)
    assert refused.status_code == 403


def test_the_admin_sees_everything_and_acts_as_root(client):
    sign_in(client, "admin", "admin-pass")
    assert [s["name"] for s in client.get("/api/me").get_json()["shares"]] == ["Anna", "Backups", "Family"]
    client.post("/api/mkdir", json={"share": "Anna", "path": "", "name": "New"}, headers=H)
    assert client.calls[-1] == (["files-mkdir", "/mnt/alvaos/main/Anna", "New"], None)


def test_changes_need_the_page_header_and_a_session(client):
    assert client.get("/api/me").status_code == 401
    sign_in(client, "anna", "anna-pass")
    assert client.post("/api/mkdir", json={"share": "Anna", "path": "", "name": "x"}).status_code == 403
    assert client.post("/api/mkdir", json={"share": "Anna", "path": "", "name": "x"}, headers=H).status_code == 200
    assert client.calls[-1] == (["files-mkdir", "/mnt/alvaos/main/Anna", "x"], "anna")
    assert client.post("/api/mkdir", json={"share": "Anna", "path": "../Family", "name": "x"},
                       headers=H).status_code == 404
    client.post("/api/logout", headers=H)
    assert client.get("/api/me").status_code == 401


def test_only_the_admin_empties_a_trash(client):
    sign_in(client, "anna", "anna-pass")
    assert client.post("/api/trash/empty", json={"share": "Anna"}, headers=H).status_code == 403
    client.post("/api/delete", json={"share": "Anna", "path": "", "names": ["a.txt"]}, headers=H)
    assert client.calls[-1] == (["files-trash", "/mnt/alvaos/main/Anna", "/mnt/alvaos/main/Anna", "a.txt"], "anna")


def test_download_links_read_as_the_person(client, monkeypatch):
    opened = []
    monkeypatch.setattr(files_manager, "file_size", lambda path, user=None: 4)
    monkeypatch.setattr(files_manager, "open_stream",
                        lambda path, part=None, user=None: (opened.append((path, user)) or iter([b"data"]), ""))
    sign_in(client, "ben", "ben-pass")
    url = client.post("/api/link", json={"share": "Family", "path": "a.jpg", "inline": True}, headers=H).get_json()["url"]
    got = client.get(url)
    assert got.status_code == 200 and got.data == b"data" and opened == [("/mnt/alvaos/main/Family/a.jpg", "ben")]
    assert got.headers["Content-Type"] == "image/jpeg"


def test_admin_two_step_code_is_asked_for(client, tmp_path):
    pyotp = pytest.importorskip("pyotp")
    secret = pyotp.random_base32()
    (tmp_path / "auth.json").write_text(json.dumps({**hash_password("admin-pass"), "totp_secret": secret}))
    first = sign_in(client, "admin", "admin-pass")
    assert first.status_code == 401 and first.get_json()["needs_code"] is True
    assert client.post("/api/login", json={"username": "admin", "password": "admin-pass",
                                           "code": "000000"}).status_code == 401
    ok = client.post("/api/login", json={"username": "admin", "password": "admin-pass",
                                         "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200


def test_too_many_attempts_are_slowed_down(client):
    for _ in range(fs.LOGIN_MAX_ATTEMPTS):
        sign_in(client, "anna", "wrong")
    assert sign_in(client, "anna", "anna-pass").status_code == 429


def test_thumbnails_are_made_here_and_cached(client, monkeypatch, tmp_path):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (1200, 800), (200, 100, 50)).save(buf, "JPEG")
    reads = []
    monkeypatch.setattr(fs, "THUMB_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setattr(files_manager, "file_size", lambda path, user=None: len(buf.getvalue()))
    monkeypatch.setattr(files_manager, "open_stream",
                        lambda path, part=None, user=None: (reads.append(user) or iter([buf.getvalue()]), ""))
    sign_in(client, "ben", "ben-pass")
    first = client.get("/api/thumb?share=Family&path=a.jpg&v=1")
    assert first.status_code == 200 and first.mimetype == "image/jpeg"
    assert max(Image.open(BytesIO(first.data)).size) == fs.THUMB_SIZE
    assert client.get("/api/thumb?share=Family&path=a.jpg&v=1").data == first.data
    assert reads == ["ben"]                       # second time from the cache
    assert client.get("/api/thumb?share=Family&path=notes.txt").status_code == 404
    assert client.get("/api/thumb?share=Anna&path=a.jpg").status_code == 404     # not Ben's share
    monkeypatch.setattr(files_manager, "open_stream", lambda path, part=None, user=None: (iter([b"not an image"]), ""))
    assert client.get("/api/thumb?share=Family&path=b.jpg").status_code == 404


def test_moving_stays_in_the_same_share(client):
    sign_in(client, "anna", "anna-pass")
    client.post("/api/move", json={"share": "Family", "path": "", "names": ["a.jpg"], "to": "Holidays"}, headers=H)
    assert client.calls[-1] == (["files-move", "/mnt/alvaos/main/Family", "a.jpg", "/mnt/alvaos/main/Family/Holidays"], "anna")
    assert client.post("/api/move", json={"share": "Family", "path": "", "names": ["a.jpg"], "to": "../Anna"},
                       headers=H).status_code == 404



def test_share_links_open_as_their_maker_and_can_be_removed(client, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(files_manager, "file_size", lambda path, user=None: 4)
    monkeypatch.setattr(files_manager, "open_stream",
                        lambda path, part=None, user=None: (opened.append((path, user)) or iter([b"data"]), ""))
    sign_in(client, "anna", "anna-pass")
    assert client.post("/api/links", json={"share": "Family", "path": ""}, headers=H).status_code == 400
    made = client.post("/api/links", json={"share": "Family", "path": "Holidays", "kind": "folder", "days": 7},
                       headers=H).get_json()
    token = made["url"].split("/")[-1]
    assert made["kind"] == "folder" and made["expires_at"]
    assert [x["id"] for x in client.get("/api/links").get_json()["links"]] == [made["id"]]

    visitor = fs.app.test_client()           # no account at all
    assert visitor.get(f"/api/public/{token}").get_json()["name"] == "Holidays"
    visitor.get(f"/api/public/{token}/list?path=2025")
    assert client.calls[-1] == (["files-list", "/mnt/alvaos/main/Family/Holidays/2025"], "anna")
    assert visitor.get(f"/api/public/{token}/list?path=../../Anna").status_code == 404
    got = visitor.get(f"/api/public/{token}/file?path=2025/a.jpg")
    assert got.data == b"data" and opened[-1] == ("/mnt/alvaos/main/Family/Holidays/2025/a.jpg", "anna")
    assert visitor.post(f"/api/links/{made['id']}/delete", headers=H).status_code == 401

    sign_in(client, "ben", "ben-pass")       # someone else cannot remove Anna's link
    assert client.post(f"/api/links/{made['id']}/delete", headers=H).status_code == 403
    sign_in(client, "anna", "anna-pass")
    assert client.post(f"/api/links/{made['id']}/delete", headers=H).status_code == 200
    assert visitor.get(f"/api/public/{token}").status_code == 404


def test_link_passwords_expiry_and_lost_access(client, monkeypatch, tmp_path):
    monkeypatch.setattr(files_manager, "file_size", lambda path, user=None: 4)
    monkeypatch.setattr(files_manager, "open_stream", lambda path, part=None, user=None: (iter([b"data"]), ""))
    sign_in(client, "anna", "anna-pass")
    made = client.post("/api/links", json={"share": "Anna", "path": "secret.pdf", "kind": "file", "days": 0,
                                           "password": "open-sesame"}, headers=H).get_json()
    token = made["url"].split("/")[-1]
    assert made["expires_at"] is None and made["has_password"] is True
    visitor = fs.app.test_client()
    locked = visitor.get(f"/api/public/{token}")
    assert locked.status_code == 401 and locked.get_json()["needs_password"] is True
    assert visitor.post(f"/api/public/{token}/unlock", json={"password": "wrong"}).status_code == 401
    assert visitor.post(f"/api/public/{token}/unlock", json={"password": "open-sesame"}).status_code == 200
    assert visitor.get(f"/api/public/{token}/file").data == b"data"
    assert visitor.get(f"/api/public/{token}/file?path=other.pdf").status_code == 404   # a file link is one file

    # Anna loses access to her share: the link stops working.
    shares = json.loads((tmp_path / "shares.json").read_text())
    shares["2"]["smb_permissions"] = {}
    (tmp_path / "shares.json").write_text(json.dumps(shares))
    assert visitor.get(f"/api/public/{token}").status_code == 404

    # An expired link is gone.
    links = json.loads((tmp_path / "links.json").read_text())
    for v in links.values():
        v["expires_at"] = "2000-01-01T00:00:00+00:00"
    (tmp_path / "links.json").write_text(json.dumps(links))
    assert visitor.get(f"/api/public/{token}").status_code == 404
    assert client.post("/api/links", json={"share": "Family", "path": "x", "days": 5}, headers=H).status_code == 400



def test_folders_download_as_zip_as_the_person(client, monkeypatch):
    zipped = []
    monkeypatch.setattr(files_manager, "open_zip", lambda path, user=None: (zipped.append((path, user)) or iter([b"PK"]), ""))
    sign_in(client, "ben", "ben-pass")
    got = client.get("/api/zip?share=Family&path=Holidays")
    assert got.status_code == 200 and got.mimetype == "application/zip"
    assert "Holidays.zip" in got.headers["Content-Disposition"]
    assert zipped == [("/mnt/alvaos/main/Family/Holidays", "ben")]
    assert client.get("/api/zip?share=Anna").status_code == 404



def test_uploads_go_in_pieces_as_the_person(client, monkeypatch):
    piped = []
    monkeypatch.setattr(files_manager, "pipe_helper",
                        lambda args, stream, chunk=1048576, user=None: (piped.append((args, stream.read(), user)) or {"size": 3}, ""))
    sign_in(client, "anna", "anna-pass")
    q = "share=Family&path=Holidays&name=film.mkv"
    client.get(f"/api/upload/status?{q}")
    assert client.calls[-1] == (["files-part-size", "/mnt/alvaos/main/Family/Holidays", "film.mkv"], "anna")
    assert client.post(f"/api/upload/piece?{q}&offset=0", data=b"abc", headers=H).get_json()["size"] == 3
    assert piped == [(["files-part-write", "/mnt/alvaos/main/Family/Holidays", "film.mkv", "0"], b"abc", "anna")]
    assert client.post(f"/api/upload/piece?{q}&offset=-1", data=b"x", headers=H).status_code == 400
    client.post("/api/upload/finish", json={"share": "Family", "path": "Holidays", "name": "film.mkv", "size": 3}, headers=H)
    assert client.calls[-1] == (["files-part-finish", "/mnt/alvaos/main/Family/Holidays", "film.mkv", "3"], "anna")
    sign_in(client, "ben", "ben-pass")      # read-only in Family
    assert client.post(f"/api/upload/piece?{q}&offset=0", data=b"x", headers=H).status_code == 403


def test_copy_runs_as_the_person_in_one_share(client):
    sign_in(client, "anna", "anna-pass")
    client.post("/api/copy", json={"share": "Family", "path": "", "names": ["a.jpg"], "to": ""}, headers=H)
    assert client.calls[-1] == (["files-copy", "/mnt/alvaos/main/Family", "a.jpg", "/mnt/alvaos/main/Family"], "anna")


def test_search_runs_as_the_person_and_gives_share_paths(client, monkeypatch):
    seen = []
    monkeypatch.setattr(files_manager, "run_helper", lambda args, timeout=600, user=None: (
        seen.append((args, user)) or {"results": [{"name": "a.jpg", "folder": "2024", "type": "file"}],
                                      "complete": True}, ""))
    sign_in(client, "ben", "ben-pass")
    assert client.get("/api/search?share=Family&q=").status_code == 400
    assert client.get("/api/search?share=Anna&q=a").status_code == 404
    data = client.get("/api/search?share=Family&path=Photos&q=a").get_json()
    assert seen == [(["files-search", "/mnt/alvaos/main/Family/Photos", "a"], "ben")]
    assert data["results"][0]["folder"] == "Photos/2024" and data["complete"] is True
