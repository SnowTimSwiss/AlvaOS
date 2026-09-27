"""WireGuard identity of a Buddy Backup node (backend/buddy_backup_manager.py)."""

import base64
import json

import pytest

import buddy_backup_manager as bbm


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(tmp_path))
    return bbm.BuddyBackupManager(lambda *a, **k: (None, "no commands in tests"))


def derive_public(private_b64):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    key = X25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def test_new_identity_has_a_real_key_pair(manager):
    public = manager._identity_public()
    private = manager._load_identity()["private_key"]
    assert public["key_source"] == "wireguard"
    assert public["key_error"] == ""
    assert derive_public(private) == public["public_key"]
    assert bbm.WG_KEY_RE.match(public["public_key"])


def test_key_generation_needs_no_commands(manager):
    calls = []
    manager.run_command = lambda *a, **k: calls.append(a) or (None, "should not be called")
    private, public, err, source = manager._generate_wg_keypair()
    assert err is None and source == "wireguard"
    assert derive_public(private) == public
    assert calls == []


def test_placeholder_keys_from_older_versions_are_replaced(manager):
    # Older versions stored unrelated random keys when wg was not installed.
    fake = base64.b64encode(b"\x01" * 32).decode()
    manager._save_identity({
        "node_id": "abcd1234abcd1234", "name": "nas", "private_key": fake, "public_key": fake,
        "api_secret": "s", "key_source": "placeholder", "key_error": "wg missing",
    })
    public = manager._identity_public()
    stored = json.load(open(manager.identity_file))
    assert public["public_key"] != fake
    assert stored["key_source"] == "wireguard"
    assert derive_public(stored["private_key"]) == stored["public_key"]
    assert stored["node_id"] == "abcd1234abcd1234"   # the node keeps its identity


def test_peer_api_goes_through_the_tunnel_only(manager):
    peer = {"node_id": "p", "tunnel_ip": "100.95.95.9", "api_endpoint": "203.0.113.5:8080",
            "endpoint": "203.0.113.5:51820"}
    assert manager._peer_api_urls(peer, "/api/v1/backup/buddy/peer/list") == [
        "http://100.95.95.9:8080/api/v1/backup/buddy/peer/list"
    ]
    assert manager._peer_api_urls({"api_endpoint": "203.0.113.5:8080"}, "/x") == []


def test_buddy_behind_nat_is_still_in_the_tunnel_config(manager):
    public = manager._identity_public()
    peers = {
        "a": {"public_key": public["public_key"], "tunnel_ip": "100.95.95.10", "endpoint": ""},
        "b": {"public_key": public["public_key"], "tunnel_ip": "100.95.95.11", "endpoint": "198.51.100.7:51820"},
    }
    config = manager._render_wg_config(manager._load_identity(), peers)
    assert "AllowedIPs = 100.95.95.10/32" in config
    assert "AllowedIPs = 100.95.95.11/32" in config
    assert config.count("Endpoint =") == 1
