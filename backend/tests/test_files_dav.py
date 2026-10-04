"""AlvaOS Files over WebDAV (backend/files_dav.py)."""

import base64
import json

import pytest

import files_dav as dav
import files_manager
import files_server as fs
from password_utils import hash_password

SHARES = {
    "1": {"name": "Family", "path": "/mnt/alvaos/main/Family", "protocol": "smb",
          "smb_permissions": {"anna": "write", "ben": "read"}},
    "2": {"name": "Anna", "path": "/mnt/alvaos/main/Anna", "protocol": "smb", "smb_permissions": {"anna": "write"}},
}
LISTING = {
    "/mnt/alvaos/main/Family": [{"name": "Docs", "type": "folder", "size_bytes": 0, "modified_at": "2026-10-01T10:00:00+00:00"},
                                {"name": "a.txt", "type": "file", "size_bytes": 3, "modified_at": "2026-10-01T10:00:00+00:00"},
                                {"name": ".b.txt.alvaos-upload", "type": "file", "size_bytes": 1, "modified_at": None}],
    "/mnt/alvaos/main/Family/Docs": [],
    "/mnt/alvaos/main": [{"name": "Family", "type": "folder"}, {"name": "Anna", "type": "folder"}],
}


def auth(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


ANNA = auth("anna", "anna-pass")
BEN = auth("ben", "ben-pass")


@pytest.fixture
def client(tmp_path, monkeypatch):
    users = {"anna": {"files_auth": hash_password("anna-pass")}, "ben": {"files_auth": hash_password("ben-pass")}}
    (tmp_path / "users.json").write_text(json.dumps(users))
    (tmp_path / "shares.json").write_text(json.dumps(SHARES))
    monkeypatch.setattr(fs, "USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setattr(fs, "SHARES_FILE", str(tmp_path / "shares.json"))
    fs._attempts.clear()
    dav._good_credentials.clear()
    calls = []
    monkeypatch.setattr(files_manager, "list_entries", lambda path, user=None: (LISTING.get(path), ""))
    monkeypatch.setattr(files_manager, "run_helper",
                        lambda args, timeout=600, user=None: (calls.append((args, user)) or {"name": args[2]}, ""))
    monkeypatch.setattr(files_manager, "pipe_helper", lambda args, stream, chunk=None, user=None: (
        calls.append((args + [stream.read().decode()], user)) or {"size": 5}, ""))
    monkeypatch.setattr(files_manager, "file_size", lambda path, user=None: 3 if path.endswith("a.txt") else None)
    monkeypatch.setattr(files_manager, "open_stream",
                        lambda path, chunk=0, part=None, user=None: (iter([b"abc"[part[0]:part[0] + part[1]] if part else b"abc"]), ""))
    c = dav.app.test_client()
    c.calls = calls
    return c


def test_signing_in_is_needed_and_admin_is_not_offered(client):
    assert client.open("/", method="PROPFIND").status_code == 401
    assert "WWW-Authenticate" in client.open("/", method="PROPFIND").headers
    assert client.open("/", method="PROPFIND", headers=auth("anna", "nope")).status_code == 401
    assert client.open("/", method="PROPFIND", headers=auth("admin", "x")).status_code == 401
    assert client.options("/").headers["DAV"] == "1, 2"


def test_the_root_lists_each_persons_shares(client):
    body = client.open("/", method="PROPFIND", headers={**BEN, "Depth": "1"}).get_data(as_text=True)
    assert "<D:href>/Family/</D:href>" in body and "/Anna/" not in body
    body = client.open("/", method="PROPFIND", headers={**ANNA, "Depth": "1"}).get_data(as_text=True)
    assert "/Anna/" in body and "/Family/" in body


def test_a_folder_lists_files_and_hides_unfinished_uploads(client):
    res = client.open("/Family/", method="PROPFIND", headers={**BEN, "Depth": "1"})
    body = res.get_data(as_text=True)
    assert res.status_code == 207
    assert "<D:href>/Family/Docs/</D:href>" in body and "<D:href>/Family/a.txt</D:href>" in body
    assert "alvaos-upload" not in body and "<D:getcontentlength>3</D:getcontentlength>" in body
    assert client.open("/Family/nothere.txt", method="PROPFIND", headers={**BEN, "Depth": "0"}).status_code == 404
    assert client.open("/Anna/", method="PROPFIND", headers={**BEN, "Depth": "0"}).status_code == 404


def test_files_are_read_as_the_person_with_ranges(client):
    assert client.get("/Family/a.txt", headers=BEN).data == b"abc"
    part = client.get("/Family/a.txt", headers={**BEN, "Range": "bytes=1-2"})
    assert part.status_code == 206 and part.data == b"bc"


def test_read_only_people_cannot_change_anything(client):
    for method in ("PUT", "DELETE", "MKCOL"):
        assert client.open("/Family/x.txt", method=method, headers=BEN).status_code == 403, method
    assert client.calls == []


def test_put_new_file_and_replace_one_keeps_the_old_in_the_trash(client):
    assert client.put("/Family/new.txt", data=b"hello", headers=ANNA).status_code == 201
    fam = "/mnt/alvaos/main/Family"
    assert client.calls[0] == (["files-part-write", fam, "new.txt", "0", "hello"], "anna")
    assert client.calls[1] == (["files-part-finish", fam, "new.txt", "5"], "anna")
    client.calls.clear()
    assert client.put("/Family/a.txt", data=b"hello", headers=ANNA).status_code == 204
    hidden = client.calls[0][0][2]
    assert hidden.startswith(".") and hidden.endswith(".a.txt")
    assert [c[0][0] for c in client.calls] == ["files-part-write", "files-part-finish", "files-trash", "files-rename"]
    assert client.calls[2][0] == ["files-trash", fam, fam, "a.txt"]
    assert client.calls[3][0] == ["files-rename", fam, hidden, "a.txt"]


def test_mkcol_delete_move_and_copy(client):
    fam = "/mnt/alvaos/main/Family"
    assert client.open("/Family/New", method="MKCOL", headers=ANNA).status_code == 201
    assert client.calls[-1] == (["files-mkdir", fam, "New"], "anna")
    assert client.delete("/Family/a.txt", headers=ANNA).status_code == 204
    assert client.calls[-1] == (["files-trash", fam, fam, "a.txt"], "anna")
    assert client.delete("/Family/missing.txt", headers=ANNA).status_code == 404
    client.calls.clear()
    res = client.open("/Family/a.txt", method="MOVE", headers={**ANNA, "Destination": "http://nas:8091/Family/Docs/b%20c.txt"})
    assert res.status_code == 201
    assert [c[0] for c in client.calls] == [["files-move", fam, "a.txt", fam + "/Docs"],
                                            ["files-rename", fam + "/Docs", "a.txt", "b c.txt"]]
    client.calls.clear()
    client.open("/Family/a.txt", method="MOVE", headers={**ANNA, "Destination": "/Family/z.txt"})
    assert client.calls == [(["files-rename", fam, "a.txt", "z.txt"], "anna")]
    res = client.open("/Family/a.txt", method="MOVE", headers={**ANNA, "Destination": "/Family/Docs", "Overwrite": "F"})
    assert res.status_code == 412                                   # Docs exists
    assert client.open("/Family/a.txt", method="COPY", headers={**ANNA, "Destination": "/Anna/a.txt"}).status_code == 502
    client.calls.clear()
    assert client.open("/Family/a.txt", method="COPY", headers={**BEN, "Destination": "/Family/c.txt"}).status_code == 403


def test_locks_are_acknowledged(client):
    res = client.open("/Family/a.txt", method="LOCK", headers=ANNA, data="<lockinfo/>")
    assert res.status_code == 200 and res.headers["Lock-Token"].startswith("<opaquelocktoken:")
    assert client.open("/Family/a.txt", method="UNLOCK", headers=ANNA).status_code == 204


def test_a_new_password_ends_remembered_sign_ins_at_once(client, tmp_path):
    assert client.open("/", method="PROPFIND", headers=ANNA).status_code == 207
    users = json.loads((tmp_path / "users.json").read_text())
    users["anna"]["files_auth"] = hash_password("a-new-password")
    (tmp_path / "users.json").write_text(json.dumps(users))
    assert client.open("/", method="PROPFIND", headers=ANNA).status_code == 401
    assert client.open("/", method="PROPFIND", headers=auth("anna", "a-new-password")).status_code == 207


def test_only_wrong_passwords_count_towards_the_limit(client):
    dav._failures.clear()
    for _ in range(30):                                        # many right sign-ins, no cache hits
        dav._good_credentials.clear()
        assert client.open("/", method="PROPFIND", headers=ANNA).status_code == 207
    for _ in range(dav.MAX_FAILURES):
        assert client.open("/", method="PROPFIND", headers=auth("anna", "nope")).status_code == 401
    assert client.open("/", method="PROPFIND", headers=auth("anna", "nope")).status_code == 429
    dav._failures.clear()
