"""API-level checks across all blueprints (backend/alvaos-backend.py + api_*.py).

These guard the properties that must survive any refactor of the routing
layer: nothing is reachable before setup, every endpoint needs a session,
state changes need the CSRF token, admin endpoints need an admin.
"""

import importlib.util
import os

import pytest

import auth_manager

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Endpoints that are reachable without an operator session by design.
PUBLIC = {
    "/api/v1/setup/status",
    "/api/v1/setup/complete",
    "/api/v1/auth/login",
    "/api/v1/auth/2fa/complete",
    "/api/v1/backup/pairing/accept",
    "/api/v1/backup/pairing/remove/accept",
    "/api/v1/files/get/<token>",    # the expiring download link is the permission
}
PEER_PREFIX = "/api/v1/backup/buddy/peer/"   # authenticated with X-Buddy-Secret


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    state = tmp_path_factory.mktemp("state")
    mp = pytest.MonkeyPatch()
    mp.setattr(auth_manager, "SETUP_STATUS_FILE", str(state / "setup_complete.json"))
    mp.setattr(auth_manager, "SESSIONS_FILE", str(state / "sessions.json"))
    mp.setattr(auth_manager, "AUTH_FILE", str(state / "auth.json"))
    spec = importlib.util.spec_from_file_location("alvaos_backend_under_test",
                                                  os.path.join(BACKEND_DIR, "alvaos-backend.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.testing = True
    yield module, state
    mp.undo()


def api_rules(app):
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/api/"):
            methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
            path = rule.rule
            for arg in rule.arguments:
                path = path.replace(f"<{arg}>", "x").replace(f"<path:{arg}>", "x")
            yield rule.rule, path, methods


def set_up(state):
    (state / "setup_complete.json").write_text("{}")


def test_all_blueprints_are_registered(backend):
    module, _ = backend
    rules = {r for r, _, _ in api_rules(module.app)}
    for expected in ("/api/v1/auth/login", "/api/v1/system/info", "/api/v1/updates/status",
                     "/api/v1/storage/pools", "/api/v1/storage/shares", "/api/v1/backup/status",
                     "/api/v1/apps/installed", "/api/v1/watchdog/status"):
        assert expected in rules
    assert len(rules) >= 95  # unique paths; some paths have separate GET and POST routes


def test_nothing_but_setup_is_reachable_before_setup(backend):
    module, state = backend
    (state / "setup_complete.json").unlink(missing_ok=True)
    client = module.app.test_client()
    assert client.get("/api/v1/setup/status").status_code == 200
    for rule, path, methods in api_rules(module.app):
        if rule.rstrip("/") in module.PRE_SETUP_ALLOWLIST:
            continue
        response = client.open(path, method=methods[0])
        assert response.status_code == 403, rule
        assert response.get_json().get("setup_required") is True, rule


def test_every_endpoint_requires_a_session(backend):
    module, state = backend
    set_up(state)
    client = module.app.test_client()
    for rule, path, methods in api_rules(module.app):
        if rule in PUBLIC or rule.startswith(PEER_PREFIX):
            continue
        for method in methods:
            response = client.open(path, method=method)
            assert response.status_code == 401, f"{method} {rule} answered {response.status_code}"


def test_peer_endpoints_require_the_buddy_secret(backend):
    module, state = backend
    set_up(state)
    client = module.app.test_client()
    for rule, path, methods in api_rules(module.app):
        if rule.startswith(PEER_PREFIX):
            response = client.open(path, method=methods[0], headers={"X-Buddy-Secret": "wrong"})
            assert response.status_code in (401, 403), rule


def test_state_changes_need_the_csrf_token(backend):
    module, state = backend
    set_up(state)
    token = auth_manager._create_session("root", role="admin")
    csrf = auth_manager.SESSIONS[token]["csrf_token"]
    client = module.app.test_client()

    denied = client.post("/api/v1/auth/logout", headers={"Authorization": token})
    assert denied.status_code == 403
    assert token in auth_manager.SESSIONS

    allowed = client.post("/api/v1/auth/logout", headers={"Authorization": token, "X-CSRF-Token": csrf})
    assert allowed.status_code == 200
    assert token not in auth_manager.SESSIONS


def test_admin_endpoints_reject_regular_users(backend):
    module, state = backend
    set_up(state)
    token = auth_manager._create_session("tim", role="user")
    csrf = auth_manager.SESSIONS[token]["csrf_token"]
    client = module.app.test_client()
    response = client.post("/api/v1/system/power", json={"action": "reboot"},
                           headers={"Authorization": token, "X-CSRF-Token": csrf})
    assert response.status_code == 403
    assert "Admin" in response.get_json()["error"]


def test_security_headers_on_api_and_ui(backend):
    module, _ = backend
    client = module.app.test_client()
    for path in ("/api/v1/setup/status", "/"):
        response = client.get(path)
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert "frame-ancestors" in response.headers.get("Content-Security-Policy", "")
        assert "Access-Control-Allow-Origin" not in response.headers


# ── Buddy peer-to-peer endpoints ─────────────────────────────────────────────

PEER_TUNNEL_IP = "100.95.95.77"
OTHER_TUNNEL_IP = "100.95.95.78"


@pytest.fixture
def paired_buddy(backend):
    module, state = backend
    set_up(state)
    import app_services

    manager = app_services.buddy_backup_manager
    saved = manager._load_peers()
    peers = {
        "peer-a": {"node_id": "peer-a", "tunnel_ip": PEER_TUNNEL_IP, "public_key": "x", "name": "A"},
        "peer-b": {"node_id": "peer-b", "tunnel_ip": OTHER_TUNNEL_IP, "public_key": "y", "name": "B"},
    }
    manager._save_peers(peers)
    secret = manager._identity_private()["api_secret"]
    yield module.app.test_client(), secret
    manager._save_peers(saved)


def test_pairing_accept_requires_the_pairing_secret(backend):
    module, state = backend
    set_up(state)
    client = module.app.test_client()
    forged = client.post("/api/v1/backup/pairing/accept", json={"token": "eyJ2IjoxfQ"})
    assert forged.status_code == 403


def test_peer_requests_must_come_through_the_tunnel(paired_buddy):
    client, secret = paired_buddy
    headers = {"X-Buddy-Secret": secret}
    # right secret, but from the internet instead of the tunnel
    outside = client.get("/api/v1/backup/buddy/peer/list?owner_node_id=peer-a", headers=headers,
                         environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert outside.status_code == 403
    inside = client.get("/api/v1/backup/buddy/peer/list?owner_node_id=peer-a", headers=headers,
                        environ_base={"REMOTE_ADDR": PEER_TUNNEL_IP})
    assert inside.status_code == 200


def test_a_buddy_cannot_touch_another_buddys_snapshots(paired_buddy):
    client, secret = paired_buddy
    headers = {"X-Buddy-Secret": secret}
    as_a = {"REMOTE_ADDR": PEER_TUNNEL_IP}
    for method, path in (
        ("GET", "/api/v1/backup/buddy/peer/list?owner_node_id=peer-b"),
        ("GET", "/api/v1/backup/buddy/peer/download/s1?owner_node_id=peer-b"),
        ("DELETE", "/api/v1/backup/buddy/peer/delete/s1?owner_node_id=peer-b"),
    ):
        response = client.open(path, method=method, headers=headers, environ_base=as_a)
        assert response.status_code == 403, path
    unpair = client.post("/api/v1/backup/pairing/remove/accept", headers=headers, environ_base=as_a,
                         json={"node_id": "peer-b"})
    assert unpair.status_code == 403


def test_a_buddy_only_ever_gets_its_own_vault(paired_buddy, monkeypatch):
    import app_services

    manager = app_services.buddy_backup_manager
    client, secret = paired_buddy
    headers = {"X-Buddy-Secret": secret}
    as_a = {"REMOTE_ADDR": PEER_TUNNEL_IP}
    as_b = {"REMOTE_ADDR": OTHER_TUNNEL_IP}

    created = client.post("/api/v1/backup/buddy/peer/vault", headers=headers, environ_base=as_a)
    assert created.status_code == 200, created.get_json()
    assert created.get_json()["size_bytes"] == manager._owner_quota_bytes("peer-a")
    assert created.get_json()["used_bytes"] < 1024 * 1024        # sparse until written

    stored = client.put("/api/v1/backup/buddy/peer/vault/key", headers=headers, environ_base=as_a,
                        json={"key_blob": "ALVAVKEY1.not-checked-here"})
    assert stored.status_code == 200
    bad = client.put("/api/v1/backup/buddy/peer/vault/key", headers=headers, environ_base=as_a,
                     json={"key_blob": "something else"})
    assert bad.status_code == 400

    other = client.get("/api/v1/backup/buddy/peer/vault", headers=headers, environ_base=as_b)
    assert other.status_code == 200 and not other.get_json()["exists"] and not other.get_json()["key_blob"]
    outside = client.post("/api/v1/backup/buddy/peer/vault", headers=headers,
                          environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert outside.status_code == 403

    # NBD access follows the same rule.
    assert manager.vault_store.resolve(PEER_TUNNEL_IP, "peer-a") is not None
    assert manager.vault_store.resolve(OTHER_TUNNEL_IP, "peer-a") is None
    assert manager.vault_store.resolve(PEER_TUNNEL_IP, "peer-b") is None

    deleted = client.delete("/api/v1/backup/buddy/peer/vault", headers=headers, environ_base=as_a)
    assert deleted.get_json()["deleted"] is True


# ── First-run setup ──────────────────────────────────────────────────────────

def test_setup_status_suggests_the_hostname_only_before_setup(backend):
    module, state = backend
    client = module.app.test_client()
    (state / "setup_complete.json").unlink(missing_ok=True)
    before = client.get("/api/v1/setup/status").get_json()
    assert before["setup_complete"] is False and "hostname" in before
    set_up(state)
    after = client.get("/api/v1/setup/status").get_json()
    assert after["setup_complete"] is True and "hostname" not in after


def test_setup_refuses_unknown_time_zones(backend):
    module, state = backend
    (state / "setup_complete.json").unlink(missing_ok=True)
    client = module.app.test_client()
    for tz in ("../../etc/passwd", "Europe/Zurich; reboot", "", "x" * 80):
        response = client.post("/api/v1/setup/complete", json={"password": "longenough", "timezone": tz})
        assert response.status_code == 400, tz
    set_up(state)


def test_time_zone_names():
    import api_auth
    for ok in ("UTC", "Europe/Zurich", "America/Argentina/Buenos_Aires", "Etc/GMT+1"):
        assert api_auth.TIMEZONE_RE.match(ok), ok
    for bad in ("/etc/passwd", "Europe/../x", "Europe Zurich", "-UTC"):
        assert not api_auth.TIMEZONE_RE.match(bad), bad


def test_signed_in_devices_can_be_listed_and_signed_out(backend):
    import auth_manager as am
    module, state = backend
    set_up(state)
    client = module.app.test_client()
    mine = am._create_session("root", role="admin")
    other = am._create_session("root", role="admin")
    headers = {"Authorization": mine, "X-CSRF-Token": am.SESSIONS[mine]["csrf_token"]}

    listed = client.get("/api/v1/auth/sessions", headers=headers).get_json()["sessions"]
    assert listed[0]["current"] and other not in repr(listed)

    # Changing sessions needs the CSRF token like every other change.
    no_csrf = client.post("/api/v1/auth/sessions/revoke-others", headers={"Authorization": mine})
    assert no_csrf.status_code == 403

    own = client.delete(f"/api/v1/auth/sessions/{am.session_public_id(mine)}", headers=headers)
    assert own.status_code == 400
    response = client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    assert response.get_json()["signed_out"] >= 1
    assert other not in am.SESSIONS and mine in am.SESSIONS
    am._destroy_session(mine)


def test_the_admin_password_can_be_changed(backend, monkeypatch):
    import api_auth
    import auth_manager as am
    from password_utils import hash_password, verify_password

    module, state = backend
    set_up(state)
    auth_file = state / "auth.json"
    auth_file.write_text(__import__("json").dumps({**hash_password("old password"), "totp_secret": "KEEPME"}))
    monkeypatch.setattr(api_auth, "AUTH_FILE", str(auth_file))
    monkeypatch.setattr(api_auth.platform, "system", lambda: "Darwin")  # no chpasswd here
    am._reset_rate_limit("127.0.0.1")

    client = module.app.test_client()
    mine = am._create_session("root", role="admin")
    other = am._create_session("root", role="admin")
    headers = {"Authorization": mine, "X-CSRF-Token": am.SESSIONS[mine]["csrf_token"]}

    wrong = client.post("/api/v1/auth/password", headers=headers,
                        json={"current_password": "nope", "new_password": "a new password"})
    assert wrong.status_code == 403
    short = client.post("/api/v1/auth/password", headers=headers,
                        json={"current_password": "old password", "new_password": "short"})
    assert short.status_code == 400

    ok = client.post("/api/v1/auth/password", headers=headers,
                     json={"current_password": "old password", "new_password": "a new password"})
    assert ok.status_code == 200, ok.get_json()
    stored = __import__("json").loads(auth_file.read_text())
    assert verify_password("a new password", stored)[0] and not verify_password("old password", stored)[0]
    assert stored["totp_secret"] == "KEEPME"
    assert mine in am.SESSIONS and other not in am.SESSIONS
    am._destroy_session(mine)


def test_files_download_links(backend, monkeypatch):
    import api_files
    import files_manager
    module, state = backend
    set_up(state)
    token = auth_manager._create_session("root", role="admin")
    headers = {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}
    monkeypatch.setattr(api_files, "load_shares_state",
                        lambda: {"a": {"name": "Media", "path": "/mnt/alvaos/main/Media"}})
    opened = []
    monkeypatch.setattr(files_manager, "open_stream",
                        lambda path, part=None: (opened.append(path) or iter([b"data"]), ""))
    monkeypatch.setattr(files_manager, "file_size", lambda path: 4)
    client = module.app.test_client()

    assert client.post("/api/v1/files/link", json={"share": "Media", "path": "../../etc/shadow"},
                       headers=headers).status_code == 404
    assert client.post("/api/v1/files/link", json={"share": "Media", "path": ""}, headers=headers).status_code == 404
    link = client.post("/api/v1/files/link", json={"share": "Media", "path": "Films/a b.mkv"}, headers=headers)
    url = link.get_json()["url"]

    got = client.get(url)      # no session: the link is the permission
    assert got.status_code == 200 and got.data == b"data"
    assert opened == ["/mnt/alvaos/main/Media/Films/a b.mkv"]
    assert got.headers["Content-Disposition"] == "attachment; filename*=UTF-8''a%20b.mkv"
    assert "sandbox" in got.headers["Content-Security-Policy"]
    assert client.get("/api/v1/files/get/not-a-real-token").status_code == 404

    for entry in api_files._links.values():
        entry["expires"] = 0
    assert client.get(url).status_code == 404


def test_files_changes_go_to_the_helper_with_share_paths_only(backend, monkeypatch):
    import api_files
    import files_manager
    module, state = backend
    set_up(state)
    token = auth_manager._create_session("root", role="admin")
    headers = {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}
    monkeypatch.setattr(api_files, "load_shares_state",
                        lambda: {"a": {"name": "Media", "path": "/mnt/alvaos/main/Media"}})
    calls = []
    monkeypatch.setattr(files_manager, "run_helper", lambda args, timeout=600: (calls.append(args) or {}, ""))
    uploads = []
    monkeypatch.setattr(files_manager, "upload",
                        lambda folder, name, stream: (uploads.append((folder, name, stream.read())) or {"written": 3}, ""))
    client = module.app.test_client()

    up = client.post("/api/v1/files/upload?share=Media&path=Films&name=a.txt", data=b"abc", headers=headers)
    assert up.status_code == 201 and uploads == [("/mnt/alvaos/main/Media/Films", "a.txt", b"abc")]
    assert client.post("/api/v1/files/upload?share=Media&path=../..&name=x", data=b"x",
                       headers=headers).status_code == 404
    client.post("/api/v1/files/mkdir", json={"share": "Media", "path": "", "name": "New"}, headers=headers)
    client.post("/api/v1/files/rename", json={"share": "Media", "path": "Films", "old": "a", "new": "b"}, headers=headers)
    client.post("/api/v1/files/delete", json={"share": "Media", "path": "Films", "names": ["a", "b"]}, headers=headers)
    client.post("/api/v1/files/trash/restore", json={"share": "Media", "id": "20261001-100000-abc"}, headers=headers)
    client.post("/api/v1/files/trash/empty", json={"share": "Media"}, headers=headers)
    assert calls == [
        ["files-mkdir", "/mnt/alvaos/main/Media", "New"],
        ["files-rename", "/mnt/alvaos/main/Media/Films", "a", "b"],
        ["files-trash", "/mnt/alvaos/main/Media", "/mnt/alvaos/main/Media/Films", "a"],
        ["files-trash", "/mnt/alvaos/main/Media", "/mnt/alvaos/main/Media/Films", "b"],
        ["files-trash-restore", "/mnt/alvaos/main/Media", "20261001-100000-abc"],
        ["files-trash-purge", "/mnt/alvaos/main/Media", "0"],
    ]
    assert client.post("/api/v1/files/delete", json={"share": "Media", "path": "", "names": []},
                       headers=headers).status_code == 400


def test_files_links_answer_range_requests(backend, monkeypatch):
    import api_files
    import files_manager
    module, state = backend
    set_up(state)
    token = auth_manager._create_session("root", role="admin")
    headers = {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}
    monkeypatch.setattr(api_files, "load_shares_state",
                        lambda: {"a": {"name": "Media", "path": "/mnt/alvaos/main/Media"}})
    data = bytes(range(100))
    monkeypatch.setattr(files_manager, "file_size", lambda path: len(data))
    monkeypatch.setattr(files_manager, "open_stream",
                        lambda path, part=None: (iter([data[part[0]:part[0] + part[1]] if part else data]), ""))
    client = module.app.test_client()
    url = client.post("/api/v1/files/link", json={"share": "Media", "path": "film.mp4", "inline": True},
                      headers=headers).get_json()["url"]

    whole = client.get(url)
    assert whole.status_code == 200 and whole.headers["Accept-Ranges"] == "bytes" and len(whole.data) == 100
    part = client.get(url, headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.data == data[10:20]
    assert part.headers["Content-Range"] == "bytes 10-19/100" and part.headers["Content-Length"] == "10"
    assert client.get(url, headers={"Range": "bytes=200-"}).status_code == 416
