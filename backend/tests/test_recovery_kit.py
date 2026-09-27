"""Buddy recovery kit: a fresh install becomes the old node again."""

import json

import pytest

import buddy_backup_manager as bbm
import buddy_crypto

PASSWORD = "kit password 123"


@pytest.fixture
def make_node(tmp_path, monkeypatch):
    def make(name):
        state = tmp_path / name
        state.mkdir()
        monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(state))
        node = bbm.BuddyBackupManager(lambda *a, **k: (None, "no commands in tests"))
        # The tunnel cannot be brought up in tests.
        monkeypatch.setattr(node, "apply_tunnel_config", lambda: (True, {"message": "ok"}))
        return node
    return make


def buddy_entry(node):
    public = node._identity_public()
    private = node._identity_private()
    return {
        "node_id": public["node_id"], "name": "Buddy", "public_key": public["public_key"],
        "api_secret": private["api_secret"], "tunnel_ip": "100.95.95.40",
        "endpoint": "198.51.100.20:51820", "api_endpoint": "198.51.100.20:8080",
    }


def test_fresh_install_becomes_the_old_node(make_node):
    old = make_node("old")
    buddy = make_node("buddy")
    old._save_peers({buddy._identity_public()["node_id"]: buddy_entry(buddy)})
    old_identity = old._identity_private()

    ok, exported = old.export_recovery_kit(PASSWORD)
    assert ok, exported
    assert exported["kit"].startswith("ALVAKIT1.")
    assert old_identity["private_key"] not in exported["kit"]

    new = make_node("new")
    assert new._identity_public()["node_id"] != old_identity["node_id"]
    ok, result = new.import_recovery_kit(exported["kit"], PASSWORD)
    assert ok, result

    restored = new._identity_private()
    for key in ("node_id", "private_key", "public_key", "api_secret", "tunnel_ip"):
        assert restored[key] == old_identity[key]
    assert set(new._load_peers()) == set(old._load_peers())
    # The buddy authenticates us by the same secret and tunnel address as before.
    assert new.verify_buddy_api_secret(old_identity["api_secret"])


def test_wrong_password_or_tampering_is_rejected(make_node):
    old = make_node("old")
    ok, exported = old.export_recovery_kit(PASSWORD)
    new = make_node("new")
    ok, result = new.import_recovery_kit(exported["kit"], "wrong password!")
    assert not ok and "Wrong password" in result["error"]
    kit = exported["kit"]
    tampered = kit[:-5] + ("A" if kit[-5] != "A" else "B") + kit[-4:]
    ok, result = new.import_recovery_kit(tampered, PASSWORD)
    assert not ok


def test_kit_with_malicious_content_is_rejected(make_node):
    old = make_node("old")
    identity = old._identity_private()
    base = {
        "kind": "alvaos-buddy-recovery", "v": 1,
        "identity": {k: identity[k] for k in ("node_id", "name", "private_key", "public_key",
                                              "api_secret", "tunnel_ip", "listen_port")},
        "peers": {},
    }
    new = make_node("new")

    bad_identity = json.loads(json.dumps(base))
    bad_identity["identity"]["public_key"] = identity["public_key"][:-2] + "A="  # not the matching key
    evil_peer = json.loads(json.dumps(base))
    evil_peer["peers"] = {"abcdef0123456789": {
        "public_key": "x\\nPostUp = id", "tunnel_ip": "100.95.95.9"}}
    bad_endpoint = json.loads(json.dumps(base))
    bad_endpoint["peers"] = {"abcdef0123456789": {
        "public_key": identity["public_key"], "tunnel_ip": "100.95.95.9",
        "endpoint": "1.2.3.4:51820\nPostUp = id"}}
    for payload in (bad_identity, evil_peer, bad_endpoint):
        ok, result = new.import_recovery_kit(buddy_crypto.seal_json(payload, PASSWORD), PASSWORD)
        assert not ok, payload
        assert "not valid" in result["error"]


def test_import_does_not_silently_replace_existing_buddies(make_node):
    old = make_node("old")
    ok, exported = old.export_recovery_kit(PASSWORD)

    busy = make_node("busy")
    other = make_node("other")
    busy._save_peers({other._identity_public()["node_id"]: buddy_entry(other)})
    ok, result = busy.import_recovery_kit(exported["kit"], PASSWORD)
    assert not ok and result.get("needs_confirmation")
    ok, result = busy.import_recovery_kit(exported["kit"], PASSWORD, replace=True)
    assert ok, result


def test_status_reports_an_outdated_kit_after_pairing_changes(make_node):
    node = make_node("node")
    assert node.recovery_kit_status()["up_to_date"] is False
    node.export_recovery_kit(PASSWORD)
    assert node.recovery_kit_status()["up_to_date"] is True
    buddy = make_node("buddy")
    node._save_peers({buddy._identity_public()["node_id"]: buddy_entry(buddy)})
    assert node.recovery_kit_status()["up_to_date"] is False


def test_short_kit_password_is_rejected(make_node):
    ok, result = make_node("n").export_recovery_kit("short")
    assert not ok and "8 characters" in result["error"]
