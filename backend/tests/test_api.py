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
