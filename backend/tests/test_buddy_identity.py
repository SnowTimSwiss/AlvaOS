"""Identity and pairing of a Buddy Backup node (backend/buddy_backup_manager.py): the address of a
buddy is its AlvaOS Link key, and buddies reach each other through Link (link_daemon.py)."""

import base64

import pytest

import buddy_backup_manager as bbm
import link_client

LINK_A = "a1" * 32
LINK_B = "b2" * 32


class FakeLink:
    """AlvaOS Link as the manager sees it: a list of buddies and an address for each."""

    def __init__(self, node_id=LINK_A):
        self.node_id = node_id
        self.buddies = []
        self.sent = []
        self.secret = "5e" * 32
        self.running = True

    def install(self, monkeypatch):
        monkeypatch.setattr(link_client, "node_id", lambda: self.node_id if self.running else "")
        monkeypatch.setattr(link_client, "set_buddies", self.set_buddies)
        monkeypatch.setattr(link_client, "status", self.status)
        monkeypatch.setattr(link_client, "pair", self.pair)
        monkeypatch.setattr(link_client, "ping", lambda peer: {"ok": True, "ms": 12})
        monkeypatch.setattr(link_client, "secret_key", lambda: self.secret)
        monkeypatch.setattr(link_client, "import_secret_key", lambda s: setattr(self, "secret", s) or True)

    def set_buddies(self, buddies):
        if not self.running:
            return None
        self.buddies = buddies
        return {"peers": [{"id": b["id"], "kind": "buddy", "alias": f"127.95.1.{i + 1}"} for i, b in enumerate(buddies)]}

    def status(self):
        if not self.running:
            return None
        return {"enabled": True, "running": True, "node_id": self.node_id, "relay": "https://relay/",
                "peers": [{"id": b["id"], "kind": "buddy", "connected": True, "last_seen": 1.0} for b in self.buddies]}

    def pair(self, peer_id, request):
        self.sent.append((peer_id, request))
        return {"success": True}


@pytest.fixture
def link(monkeypatch):
    fake = FakeLink()
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def manager(tmp_path, monkeypatch, link):
    monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(tmp_path))
    return bbm.BuddyBackupManager(lambda *a, **k: (None, "no commands in tests"))


def other_nas(tmp_path, monkeypatch, node_id=LINK_B):
    """A second NAS: its own state directory and its own Link."""
    path = tmp_path / "other"
    path.mkdir()
    monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(path))
    fake = FakeLink(node_id)
    fake.install(monkeypatch)
    return bbm.BuddyBackupManager(lambda *a, **k: (None, "no commands in tests")), fake


def test_the_address_of_a_buddy_is_the_key_of_alvaos_link(manager, link):
    public = manager._identity_public()
    assert public["public_key"] == LINK_A and public["key_error"] == "" and public["key_source"] == "link"
    assert "private_key" not in manager._load_identity()
    link.running = False
    down = manager._identity_public()
    assert down["public_key"] == "" and "AlvaOS Link is not running" in down["key_error"]
    ok, error = manager.generate_pairing_token()
    assert not ok and "Link" in error["error"]


def test_peer_api_goes_through_link_only(manager):
    peer = {"node_id": "p", "tunnel_ip": "127.95.1.9", "api_endpoint": "203.0.113.5:8080"}
    assert manager._peer_api_urls(peer, "/api/v1/backup/buddy/peer/list") == [
        "http://127.95.1.9:18080/api/v1/backup/buddy/peer/list"
    ]
    assert manager._peer_api_urls({"api_endpoint": "203.0.113.5:8080"}, "/x") == []


def test_pairing_codes_carry_the_link_address_and_old_ones_are_refused(manager):
    ok, made = manager.generate_pairing_token()
    assert ok
    payload, _ = manager._token_decode(made["token"])
    assert payload["v"] == 2 and payload["public_key"] == LINK_A
    assert "endpoint" not in payload and "tunnel_ip" not in payload     # nothing about routers or ports
    old = {"v": 1, "node_id": "ab" * 8, "public_key": base64.b64encode(b"x" * 32).decode(), "tunnel_ip": "100.95.95.3"}
    peer, error = manager._token_to_peer(old)
    assert peer is None and "older AlvaOS" in error
    bad = {"v": 2, "node_id": "ab" * 8, "public_key": "not-a-link-address"}
    assert "Link address" in manager._token_to_peer(bad)[1]


def test_two_nas_pair_through_link(tmp_path, monkeypatch):
    # NAS A makes a code.
    link_a = FakeLink(LINK_A)
    link_a.install(monkeypatch)
    path_a = tmp_path / "a"
    path_a.mkdir()
    monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(path_a))
    nas_a = bbm.BuddyBackupManager(lambda *a, **k: (None, ""))
    token_a = nas_a.generate_pairing_token()[1]["token"]
    secret_a = nas_a._identity_private()["api_secret"]

    # NAS B enters it: A becomes B's buddy, Link on B is told, and B gives A its own code through Link.
    nas_b, link_b = other_nas(tmp_path, monkeypatch)
    ok, result = nas_b.validate_pairing_token(token_a, auto_reciprocal=True)
    assert ok and result["reciprocal"]["success"] and result["peer"]["status"] == "configured"
    assert link_b.buddies == [{"id": LINK_A, "name": nas_a._identity_public()["name"]}]
    assert result["peer"]["tunnel_ip"] == "127.95.1.1"           # A's address on B's loopback network
    sent_to, request = link_b.sent[0]
    assert sent_to == LINK_A and request["op"] == "buddy" and request["secret"] == secret_a
    # What B sent is a code of B that A accepts (this is what A's backend does when Link hands it over).
    link_a.install(monkeypatch)
    monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(path_a))
    ok, accepted = nas_a.validate_pairing_token(request["token"], auto_reciprocal=False)
    assert ok and accepted["peer"]["public_key"] == LINK_B and link_a.buddies[0]["id"] == LINK_B
    # A code is used once.
    assert nas_a.validate_pairing_token(request["token"])[0] is False


def test_the_connection_test_asks_link(manager, link):
    peers = {"p1": {"node_id": "p1", "name": "Bella", "public_key": LINK_B, "api_secret": "s", "tunnel_ip": "127.95.1.1"}}
    manager._save_peers(peers)
    manager._probe_peer_api = lambda peer: {"attempted": True, "success": True, "url": "x", "error": ""}
    ok, answer = manager.test_peer_connection("p1")
    assert ok and answer["connection_ok"] and "12 ms" in answer["message"]
    manager._probe_peer_api = lambda peer: {"attempted": True, "success": False, "url": "x", "error": "HTTP 403"}
    assert not manager.test_peer_connection("p1")[1]["connection_ok"]
    link.running = False
    assert "Link" in manager.test_peer_connection("p1")[1]["message"]


def test_a_buddy_from_the_wireguard_days_must_pair_again(manager, link):
    manager._save_peers({"old": {"node_id": "old", "name": "Old", "public_key": base64.b64encode(b"k" * 32).decode(),
                                 "tunnel_ip": "100.95.95.7", "api_secret": "s"}})
    status = manager.get_status()
    assert status["peers"][0]["status"] == "repair" and "Pair again" in status["peers"][0]["last_error"]
    assert link.buddies == [] or manager.sync_link()[0]
    assert link.buddies == []                                     # not handed to Link


def test_the_recovery_kit_carries_the_link_key(manager, link, tmp_path, monkeypatch):
    manager._save_peers({"b0b0b0b0b0b0b0b0": {"node_id": "b0b0b0b0b0b0b0b0", "name": "Bella", "public_key": LINK_B, "api_secret": "s" * 24,
                                "tunnel_ip": "127.95.1.1"},
                         "d1d1d1d1d1d1d1d1": {"node_id": "d1d1d1d1d1d1d1d1", "name": "Old", "public_key": base64.b64encode(b"k" * 32).decode(),
                                 "api_secret": "s" * 24, "tunnel_ip": "100.95.95.2"}})
    ok, made = manager.export_recovery_kit("a long password")
    assert ok
    opened = bbm.buddy_crypto.open_json(made["kit"], "a long password")
    assert opened["v"] == 2 and opened["identity"]["link_secret"] == link.secret
    assert set(opened["identity"]) == {"node_id", "name", "api_secret", "link_secret"}
    # A new install takes it over: same node id, same Link key, the buddies that can still be used.
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(new_dir))
    fresh_link = FakeLink("c3" * 32)
    fresh_link.install(monkeypatch)
    fresh = bbm.BuddyBackupManager(lambda *a, **k: (None, ""))
    ok, result = fresh.import_recovery_kit(made["kit"], "a long password")
    assert ok, result
    assert result["peer_count"] == 1 and "paired again" in result["message"]
    assert fresh_link.secret == link.secret
    assert fresh._load_identity()["node_id"] == manager._load_identity()["node_id"]
    assert list(fresh._load_peers()) == ["b0b0b0b0b0b0b0b0"]


def test_wireguard_is_retired_once(manager, tmp_path):
    calls = []
    manager.run_command = lambda cmd, timeout=0: calls.append(cmd) or (None, "")
    config = tmp_path / "wireguard" / "buddy0.conf"
    config.parent.mkdir()
    config.write_text("[Interface]\n")
    assert manager.retire_wireguard() is True
    assert calls == [["/usr/bin/wg-quick", "down", str(config)]] and not config.exists()
    assert manager.retire_wireguard() is False
