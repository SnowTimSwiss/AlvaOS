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
