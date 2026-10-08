"""Buddy recovery kit: a fresh install becomes the old node again (same node id, same AlvaOS Link key)."""

import json

import pytest

import buddy_backup_manager as bbm
import buddy_crypto
import link_client

PASSWORD = "kit password 123"
KEYS = {}


@pytest.fixture
def make_node(tmp_path, monkeypatch):
    """Nodes with a pretend AlvaOS Link each: the key of a node is what its Link says (and takes over)."""
    def make(name):
        state = tmp_path / name
        state.mkdir()
        monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(state))
        node = bbm.BuddyBackupManager(lambda *a, **k: (None, "no commands in tests"))
        key = KEYS.setdefault(name, f"{len(KEYS) + 1:02x}" * 32)
        node._link_key = key
        monkeypatch.setattr(node, "sync_link", lambda: (True, {"message": "ok"}))
        return node

    def use(node):
        monkeypatch.setattr(link_client, "node_id", lambda: node._link_key)
        monkeypatch.setattr(link_client, "secret_key", lambda: node._link_key)
        monkeypatch.setattr(link_client, "import_secret_key", lambda s: setattr(node, "_link_key", s) or True)
        return node
    make.use = use
    return make


def buddy_entry(node, make_node):
    make_node.use(node)
    public = node._identity_public()
    private = node._identity_private()
    return {
        "node_id": public["node_id"], "name": "Buddy", "public_key": public["public_key"],
        "api_secret": private["api_secret"], "tunnel_ip": "127.95.1.40",
    }


def test_fresh_install_becomes_the_old_node(make_node):
    old = make_node.use(make_node("old"))
    buddy = make_node("buddy")
    old._save_peers({buddy._identity_public()["node_id"]: buddy_entry(buddy, make_node)})
    make_node.use(old)
    old_identity = old._identity_private()
    old_key = old._link_key

    ok, exported = old.export_recovery_kit(PASSWORD)
    assert ok, exported
    assert exported["kit"].startswith("ALVAKIT1.")
    assert old_key not in exported["kit"]                       # sealed with the password, not readable

    new = make_node.use(make_node("new"))
    assert new._identity_public()["node_id"] != old_identity["node_id"]
    ok, result = new.import_recovery_kit(exported["kit"], PASSWORD)
    assert ok, result

    restored = new._identity_private()
    assert restored["node_id"] == old_identity["node_id"] and restored["api_secret"] == old_identity["api_secret"]
    assert new._link_key == old_key                              # the same Link address as before
    assert set(new._load_peers()) == set(old._load_peers())
    # The buddy authenticates us by the same secret as before.
    assert new.verify_buddy_api_secret(old_identity["api_secret"])


def test_wrong_password_or_tampering_is_rejected(make_node):
    old = make_node.use(make_node("old"))
    ok, exported = old.export_recovery_kit(PASSWORD)
    new = make_node("new")
    ok, result = new.import_recovery_kit(exported["kit"], "wrong password!")
    assert not ok and "Wrong password" in result["error"]
    kit = exported["kit"]
    tampered = kit[:-5] + ("A" if kit[-5] != "A" else "B") + kit[-4:]
    ok, result = new.import_recovery_kit(tampered, PASSWORD)
    assert not ok


def test_kit_with_malicious_content_is_rejected(make_node):
    old = make_node.use(make_node("old"))
    identity = old._identity_private()
    base = {
        "kind": "alvaos-buddy-recovery", "v": 2,
        "identity": {"node_id": identity["node_id"], "name": identity["name"], "api_secret": identity["api_secret"],
                     "link_secret": old._link_key},
        "peers": {},
    }
    new = make_node("new")

    bad_key = json.loads(json.dumps(base))
    bad_key["identity"]["link_secret"] = "not a key\nPostUp = id"
    bad_node = json.loads(json.dumps(base))
    bad_node["identity"]["node_id"] = "../../etc"
    evil_peer = json.loads(json.dumps(base))
    evil_peer["peers"] = {"../evil": {"public_key": "ab" * 32}}
    evil_secret = json.loads(json.dumps(base))
    evil_secret["peers"] = {"abcdef0123456789": {"public_key": "ab" * 32, "api_secret": "x y\nz"}}
    for payload in (bad_key, bad_node, evil_peer, evil_secret):
        ok, result = new.import_recovery_kit(buddy_crypto.seal_json(payload, PASSWORD), PASSWORD)
        assert not ok, payload
        assert "not valid" in result["error"]


def test_a_kit_from_the_wireguard_days_still_gives_back_the_node_id(make_node):
    old = make_node.use(make_node("old"))
    identity = old._identity_private()
    kit = {"kind": "alvaos-buddy-recovery", "v": 1,
           "identity": {"node_id": identity["node_id"], "name": "nas", "api_secret": identity["api_secret"],
                        "private_key": "x", "public_key": "y", "tunnel_ip": "100.95.95.1", "listen_port": 51820},
           "peers": {"abcdef0123456789": {"name": "Bella", "public_key": "AAAA" * 11, "tunnel_ip": "100.95.95.2"}}}
    new = make_node.use(make_node("new"))
    ok, result = new.import_recovery_kit(buddy_crypto.seal_json(kit, PASSWORD), PASSWORD)
    assert ok, result
    assert new._identity_private()["node_id"] == identity["node_id"]    # their vaults on buddies stay theirs
    assert result["peer_count"] == 0 and "paired again" in result["message"]


def test_import_does_not_silently_replace_existing_buddies(make_node):
    old = make_node.use(make_node("old"))
    ok, exported = old.export_recovery_kit(PASSWORD)

    busy = make_node.use(make_node("busy"))
    other = make_node("other")
    busy._save_peers({other._identity_public()["node_id"]: buddy_entry(other, make_node)})
    make_node.use(busy)
    ok, result = busy.import_recovery_kit(exported["kit"], PASSWORD)
    assert not ok and result.get("needs_confirmation")
    ok, result = busy.import_recovery_kit(exported["kit"], PASSWORD, replace=True)
    assert ok, result


def test_status_reports_an_outdated_kit_after_pairing_changes(make_node):
    node = make_node.use(make_node("node"))
    assert node.recovery_kit_status()["up_to_date"] is False
    node.export_recovery_kit(PASSWORD)
    assert node.recovery_kit_status()["up_to_date"] is True
    buddy = make_node("buddy")
    node._save_peers({buddy._identity_public()["node_id"]: buddy_entry(buddy, make_node)})
    make_node.use(node)
    assert node.recovery_kit_status()["up_to_date"] is False


def test_short_kit_password_is_rejected(make_node):
    ok, result = make_node.use(make_node("n")).export_recovery_kit("short")
    assert not ok and "8 characters" in result["error"]
