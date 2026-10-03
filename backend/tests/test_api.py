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
    "/api/v1/system/tls/ca.crt",          # the public certificate to trust, no secret
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



def test_files_app_is_turned_on_and_off_with_systemctl(backend, monkeypatch):
    import api_files
    module, state = backend
    set_up(state)
    token = auth_manager._create_session("root", role="admin")
    headers = {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}
    calls = []

    def fake(cmd, timeout=30):
        import subprocess
        calls.append(cmd[1:])
        out = {"is-enabled": "enabled", "is-active": "active"}.get(cmd[1], "")
        return subprocess.CompletedProcess(cmd, 0, out, ""), None

    monkeypatch.setattr(api_files, "run_sudo_command", fake)
    client = module.app.test_client()
    data = client.post("/api/v1/files-app", json={"enabled": True}, headers=headers).get_json()
    assert calls[0] == ["enable", "--now", "alvaos-files.service"]
    assert data["enabled"] is True and data["running"] is True and data["port"] == 8090
    client.post("/api/v1/files-app", json={"enabled": False}, headers=headers)
    assert ["disable", "--now", "alvaos-files.service"] in calls


def test_assistant_reads_through_the_admin_session(backend, monkeypatch, tmp_path):
    import ai_assistant
    module, state = backend
    set_up(state)
    monkeypatch.setattr(ai_assistant, "SETTINGS_FILE", str(tmp_path / "ai.json"))
    token = auth_manager._create_session("root", role="admin")
    headers = {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}
    client = module.app.test_client()
    question = {"messages": [{"role": "user", "content": "How is my NAS?"}]}
    assert client.post("/api/v1/ai/chat", json=question, headers=headers).status_code == 409
    saved = client.post("/api/v1/ai/settings", json={"enabled": True, "base_url": "http://10.0.0.5:11434/v1"},
                        headers=headers).get_json()
    assert saved["enabled"] is True and "api_key" not in saved

    class Answer:
        status_code = 200

        def __init__(self, message):
            self.message = message

        def json(self):
            return {"choices": [{"message": self.message}]}

    seen = []

    def post(url, headers, json, timeout):
        seen.append(json["messages"][-1])
        if len(seen) == 1:
            return Answer({"tool_calls": [{"id": "1", "function": {"name": "updates"}}]})
        return Answer({"content": "All good."})

    import requests
    monkeypatch.setattr(requests, "post", post)
    data = client.post("/api/v1/ai/chat", json=question, headers=headers).get_json()
    assert data["reply"] == "All good." and data["looked_at"] == ["updates"]
    assert seen[1]["role"] == "tool" and "answered 401" not in seen[1]["content"]


def test_a_bad_personal_folder_stops_before_the_person_is_made(backend, monkeypatch):
    import api_auth
    module, state = backend
    set_up(state)
    token = auth_manager._create_session("root", role="admin")
    headers = {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}
    made = []
    monkeypatch.setattr(api_auth, "run_sudo_command", lambda cmd, **kw: made.append(cmd) or (None, "no"))
    monkeypatch.setattr(api_auth, "system_user_exists", lambda name: False)
    res = module.app.test_client().post("/api/v1/users", headers=headers, json={
        "username": "anna", "password": "long enough", "personal_folder": {"pool_id": "missing", "limit_gb": 50}})
    assert res.status_code == 400 and "pool" in res.get_json()["error"] and made == []


def test_assistant_proposals_run_only_on_the_persons_click(backend, monkeypatch, tmp_path):
    import ai_assistant
    import api_ai
    module, state = backend
    set_up(state)
    monkeypatch.setattr(ai_assistant, "SETTINGS_FILE", str(tmp_path / "ai.json"))
    token = auth_manager._create_session("root", role="admin")
    headers = {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}
    client = module.app.test_client()
    client.post("/api/v1/ai/settings", json={"enabled": True, "level": "ask", "base_url": "http://10.0.0.5:11434/v1"},
                headers=headers)
    monkeypatch.setattr(ai_assistant, "chat", lambda settings, history, read, post=None, page="": {
        "reply": "Shall I?", "looked_at": [], "proposals": [{
            "title": "Check services", "detail": "d", "method": "POST", "path": "/api/v1/watchdog/check", "body": {}}]})
    ran = []
    import api_system
    monkeypatch.setattr(api_system.watchdog_manager, "run_check", lambda: ran.append(1) or {"checked": 3})
    answer = client.post("/api/v1/ai/chat", json={"messages": [{"role": "user", "content": "x"}]},
                         headers=headers).get_json()
    [proposal] = answer["proposals"]
    assert set(proposal) == {"id", "title", "detail"} and ran == []          # nothing ran yet
    other = auth_manager._create_session("root", role="admin")
    refused = client.post(f"/api/v1/ai/actions/{proposal['id']}", json={"decision": "run"},
                          headers={"Authorization": other, "X-CSRF-Token": auth_manager.SESSIONS[other]["csrf_token"]})
    assert refused.status_code == 404 and ran == []                           # another session cannot run it
    answer = client.post("/api/v1/ai/chat", json={"messages": [{"role": "user", "content": "x"}]},
                         headers=headers).get_json()
    pid = answer["proposals"][0]["id"]
    done = client.post(f"/api/v1/ai/actions/{pid}", json={"decision": "run"}, headers=headers).get_json()
    assert done["done"] is True and ran == [1]
    assert client.post(f"/api/v1/ai/actions/{pid}", json={"decision": "run"}, headers=headers).status_code == 404
    api_ai._proposals.clear()


def test_https_only_redirects_plain_http_but_not_peers_or_the_certificate(backend, monkeypatch, tmp_path):
    import tls_manager
    module, state = backend
    set_up(state)
    monkeypatch.setattr(tls_manager, "SETTINGS_FILE", str(tmp_path / "https.json"))
    token = auth_manager._create_session("root", role="admin")
    headers = {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}
    lan = {"REMOTE_ADDR": "192.168.1.5"}
    client = module.app.test_client()
    turned = client.post("/api/v1/system/tls", json={"https_only": True}, headers=headers, environ_base=lan)
    assert turned.status_code == 409                              # not from a plain-HTTP page
    ok = client.post("/api/v1/system/tls", json={"https_only": True}, headers=headers, environ_base=lan,
                     base_url="https://nas.local:8443")
    assert ok.status_code == 200 and tls_manager.https_only()
    res = client.get("/api/v1/system/info?x=1", headers=headers, environ_base=lan, base_url="http://nas.local:8080")
    assert res.status_code == 308 and res.headers["Location"] == "https://nas.local:8443/api/v1/system/info?x=1"
    assert client.get("/api/v1/system/tls/ca.crt", environ_base=lan).status_code != 308
    assert client.get("/api/v1/backup/buddy/peer/status", environ_base=lan).status_code != 308
    assert client.get("/api/v1/system/info", headers=headers).status_code != 308   # the NAS itself
    off = client.post("/api/v1/system/tls", json={"https_only": False}, headers=headers, environ_base=lan)
    assert off.status_code == 308                                 # plain HTTP is sent to HTTPS, also this
    client.post("/api/v1/system/tls", json={"https_only": False}, headers=headers, environ_base=lan,
                base_url="https://nas.local:8443")
    assert not tls_manager.https_only()


def test_every_assistant_proposal_points_at_a_real_endpoint(backend):
    import ai_assistant
    module, _ = backend
    pools = {"pools": [{"id": "u1", "name": "main"}]}
    shares = {"shares": [{"id": "share-1", "name": "Family", "protocol": "smb", "smb_permissions": {"anna": "write"}}]}
    state = {
        "/api/v1/storage/pools": pools, "/api/v1/storage/shares": shares,
        "/api/v1/users": {"users": [{"username": "anna"}, {"username": "ben"}]},
        "/api/v1/containers": {"containers": [{"ID": "abc123def456", "Names": "jellyfin"}]},
        "/api/v1/apps/installed": {"apps": [{"app_id": "jellyfin"}]},
        "/api/v1/backup/copy": {"enabled": True, "connected": True},
        "/api/v1/updates/alvaos/check": {"update_available": True, "latest_version": "1.0", "release": {"assets": [
            {"name": "a.deb", "browser_download_url": "https://x/a.deb"},
            {"name": "a.deb.sig", "browser_download_url": "https://x/a.deb.sig"}]}},
    }
    args = {"start_data_check": {"pool_id": "main"}, "restart_app": {"container": "jellyfin"},
            "turn_on_automatic_backups": {"every": "day"}, "update_app": {"app_id": "jellyfin"},
            "set_disk_sleep": {"minutes": 20}, "create_shared_folder": {"name": "X", "pool_id": "u1", "everyone": True},
            "set_folder_access": {"folder": "Family", "person": "ben", "access": "read"},
            "set_folder_limit": {"folder": "Family", "gigabytes": 10}}
    adapter = module.app.url_map.bind("localhost")
    for name in ai_assistant.ACTION_SPECS:
        action = ai_assistant.build_action(name, args.get(name, {}), lambda p: (200, state.get(p, {})))
        endpoint, _ = adapter.match(action["path"], method=action["method"])   # raises when there is none
        assert endpoint, name
    for name, path, _ in ai_assistant.TOOLS:
        assert adapter.match(path, method="GET"), name
    for name, (_, _, _, _, template) in ai_assistant.PARAM_TOOLS.items():
        assert adapter.match(template.format("sda").split("?")[0], method="GET"), name


def test_renaming_the_nas_touches_only_its_own_name_in_hosts():
    from api_system import replace_host_name
    assert replace_host_name("127.0.0.1\tlocalhost", "local", "nas") == "127.0.0.1\tlocalhost"
    assert replace_host_name("192.168.1.5 nas nas.home nasty", "nas", "alva") == "192.168.1.5\talva\talva.home\tnasty"
    assert replace_host_name("::1 localhost ip6-localhost", "localhost", "x") == "::1 localhost ip6-localhost"
    assert replace_host_name("# nas is here", "nas", "alva") == "# nas is here"
    assert replace_host_name("10.0.0.1 nas # the nas", "nas", "alva") == "10.0.0.1\talva # the nas"


def test_copies_on_the_backup_disk_say_whether_the_disk_is_there(backend, monkeypatch, tmp_path):
    import api_backup
    module, state = backend
    set_up(state)
    there = tmp_path / "copy-1"
    there.mkdir()
    entries = [{"snapshot_path": str(there), "snapshot_class": "copy"},
               {"snapshot_path": str(tmp_path / "unplugged"), "snapshot_class": "copy"}]
    monkeypatch.setattr(api_backup.backup_manager, "list_snapshots", lambda source_path=None, snapshot_class=None: entries)
    token = auth_manager._create_session("root", role="admin")
    res = module.app.test_client().get("/api/v1/backup/snapshots?snapshot_class=copy", headers={"Authorization": token})
    assert [s["available"] for s in res.get_json()["snapshots"]] == [True, False]


def test_pools_whose_disks_are_gone_say_so(backend, monkeypatch, tmp_path):
    import api_storage
    import backup_copy
    module, state = backend
    set_up(state)
    pools = {"lost": {"name": "main", "mount_point": str(tmp_path / "main")},
             "usb-1": {"name": "backup", "mount_point": str(tmp_path / "usb")}}
    (tmp_path / "main").mkdir()
    monkeypatch.setattr(api_storage, "detect_btrfs_pools", lambda: ([], None))
    monkeypatch.setattr(api_storage, "load_pools_state", lambda: pools)
    monkeypatch.setattr(backup_copy, "backup_disk_pool", lambda path=None: "usb-1")
    with api_storage._storage_cache_lock:
        api_storage.STORAGE_CACHE["pools"]["expires"] = 0
    token = auth_manager._create_session("root", role="admin")
    got = {p["id"]: p for p in module.app.test_client().get(
        "/api/v1/storage/pools", headers={"Authorization": token}).get_json()["pools"]}
    assert got["lost"]["status"] == "missing" and got["lost"]["mounted"] is False
    assert got["lost"]["total_size"] == "Unknown"            # not the size of the system disk
    assert got["usb-1"]["is_backup_disk"] and not got["lost"]["is_backup_disk"]
