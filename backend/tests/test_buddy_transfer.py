"""Buddy snapshot transfer: chunked upload, streaming send and restore.

The sender and the receiver are two real BuddyBackupManager instances. HTTP
between them is replaced by direct calls and `btrfs send/receive` by fake
processes, so the whole path from snapshot bytes to stored (and restored)
bytes is exercised without Btrfs or a network.
"""

import hashlib
import io
import os

import pytest
import requests

import buddy_backup_manager as bbm
import buddy_crypto as bc

PASSWORD = "correct horse battery staple"
SENDER_ID = "node-sender"


class FakeSend:
    """Stands in for a `btrfs send` process writing to a pipe."""

    def __init__(self, data, returncode=0):
        self.stdout = io.BytesIO(data)
        self.stdin = None
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


class FakeReceive:
    """Stands in for a `btrfs receive` process reading from a pipe."""

    def __init__(self, sink, returncode=0):
        self.stdin = sink
        self.stdout = None
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode


class Sink(io.BytesIO):
    def close(self):
        self.final = self.getvalue()
        super().close()


@pytest.fixture
def nodes(tmp_path, monkeypatch):
    def make(name):
        state = tmp_path / name
        state.mkdir()
        monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(state))
        node = bbm.BuddyBackupManager(lambda *a, **k: (None, "no commands in tests"))
        monkeypatch.setattr(node, "_free_space_reserve_bytes", lambda path: 0)
        return node

    sender, receiver = make("sender"), make("receiver")
    receiver._save_peers({SENDER_ID: {"node_id": SENDER_ID, "tunnel_ip": "100.95.95.7", "name": "Sender"}})
    return sender, receiver


def wire(sender, receiver, monkeypatch, drop_answers=()):
    """Route the sender's buddy requests straight into the receiver.

    `drop_answers` lists request numbers whose work is done on the receiver
    but whose answer is "lost", as on a flaky connection.
    """
    calls = []

    def peer_request(peer, method, path, timeout=60, **kwargs):
        calls.append((method, path))
        base = "/api/v1/backup/buddy/peer/upload"
        if method == "POST" and path == f"{base}/start":
            ok, body = receiver.begin_peer_upload(owner_node_id=SENDER_ID, **{
                k: kwargs["json"].get(k, "") for k in ("source_path", "snapshot_name", "created_at",
                                                       "encrypted", "parent_id")})
        elif method == "PUT":
            upload_id = path.rsplit("/", 1)[1]
            ok, body = receiver.append_peer_upload(SENDER_ID, upload_id, int(kwargs["params"]["offset"]),
                                                   io.BytesIO(kwargs["data"]))
        elif method == "POST" and path.endswith("/finish"):
            upload_id = path.split("/")[-2]
            ok, body = receiver.finish_peer_upload(SENDER_ID, upload_id, kwargs["json"]["size"],
                                                   kwargs["json"]["sha256"])
        elif method == "DELETE":
            ok, body = receiver.abort_peer_upload(SENDER_ID, path.rsplit("/", 1)[1])
        else:
            raise AssertionError(path)
        if len(calls) in drop_answers:
            raise requests.ConnectionError("connection reset")
        return (200 if ok else body.pop("status", 400)), body

    monkeypatch.setattr(sender, "_peer_request", peer_request)
    monkeypatch.setattr(bbm.time, "sleep", lambda s: None)
    return calls


def send(sender, data, material=None, returncode=0):
    sender.spawn_privileged = lambda cmd, stdin_pipe=False: (FakeSend(data, returncode), io.BytesIO(b"send error"))
    snapshot = {"btrfs_cmd": "/usr/bin/btrfs", "snapshot_path": "/mnt/alvaos/main/.alvaos-buddy-x",
                "snapshot_name": ".alvaos-buddy-x", "created_at": "2026-01-01T00:00:00+00:00"}
    meta = {"owner_node_id": SENDER_ID, "source_path": "/mnt/alvaos/main/media",
            "snapshot_name": snapshot["snapshot_name"], "created_at": snapshot["created_at"]}
    peer = {"node_id": "receiver", "api_secret": "s", "tunnel_ip": "100.95.95.8"}
    return sender._stream_snapshot_to_peer(peer, snapshot, meta, material)


def stored(receiver):
    entries = receiver._load_stream_entries()
    assert len(entries) == 1
    with open(entries[0]["payload_path"], "rb") as f:
        return entries[0], f.read()


def test_encrypted_snapshot_arrives_in_chunks(nodes, monkeypatch):
    sender, receiver = nodes
    monkeypatch.setattr(bbm.BuddyBackupManager, "UPLOAD_CHUNK_BYTES", 1024 * 1024)
    calls = wire(sender, receiver, monkeypatch)
    data = os.urandom(3 * 1024 * 1024 + 123)
    material = bc.new_key_material(PASSWORD)

    ok, body = send(sender, data, material)
    assert ok, body
    entry, blob = stored(receiver)
    assert entry["encrypted"] and entry["sha256"] == hashlib.sha256(blob).hexdigest()
    assert sum(1 for method, _ in calls if method == "PUT") == 4
    # The buddy only ever holds ciphertext; the password alone opens it.
    plain = b"".join(sender._decrypted_stream(iter([blob]), PASSWORD))
    assert plain == data
    assert not [n for n in os.listdir(os.path.dirname(entry["payload_path"])) if n.startswith(".partial-")]


def test_lost_answer_does_not_duplicate_a_chunk(nodes, monkeypatch):
    sender, receiver = nodes
    monkeypatch.setattr(bbm.BuddyBackupManager, "UPLOAD_CHUNK_BYTES", 1024 * 1024)
    wire(sender, receiver, monkeypatch, drop_answers={2})   # first chunk: stored, answer lost
    data = os.urandom(2 * 1024 * 1024 + 5)
    ok, body = send(sender, data)
    assert ok, body
    assert stored(receiver)[1] == data


def test_failed_send_leaves_nothing_behind(nodes, monkeypatch):
    sender, receiver = nodes
    calls = wire(sender, receiver, monkeypatch)
    ok, body = send(sender, b"half a stream", returncode=1)
    assert not ok and "btrfs send failed" in body["error"]
    assert calls[-1][0] == "DELETE"
    assert receiver._load_stream_entries() == []
    assert receiver._uploads == {}


def test_quota_is_checked_while_receiving(nodes, monkeypatch):
    sender, receiver = nodes
    monkeypatch.setattr(bbm.BuddyBackupManager, "UPLOAD_CHUNK_BYTES", 1024 * 1024)
    monkeypatch.setattr(receiver, "_owner_quota_bytes", lambda owner: 1024 * 1024 + 10)
    wire(sender, receiver, monkeypatch)
    ok, body = send(sender, os.urandom(2 * 1024 * 1024))
    assert not ok and "larger than" in body["error"]
    assert receiver._load_stream_entries() == []
    owner_dir = receiver._select_writable_stream_owner_dir(SENDER_ID)[1]
    assert os.listdir(owner_dir) == []


def test_receiver_keeps_a_reserve_of_free_space(nodes, monkeypatch):
    sender, receiver = nodes
    monkeypatch.setattr(receiver, "_free_space_reserve_bytes", lambda path: 1 << 62)
    wire(sender, receiver, monkeypatch)
    ok, body = send(sender, b"x" * 100)
    assert not ok and "free space" in body["error"]


def test_damaged_upload_is_discarded(nodes):
    _, receiver = nodes
    ok, body = receiver.begin_peer_upload(SENDER_ID, "/mnt/x", "snap", "", False)
    assert ok
    upload_id = body["upload_id"]
    assert receiver.append_peer_upload(SENDER_ID, upload_id, 0, io.BytesIO(b"abc"))[0]
    ok, body = receiver.finish_peer_upload(SENDER_ID, upload_id, 3, hashlib.sha256(b"abd").hexdigest())
    assert not ok and body["status"] == 422
    assert receiver._load_stream_entries() == []


def test_unknown_owner_cannot_upload(nodes):
    _, receiver = nodes
    ok, body = receiver.begin_peer_upload("stranger", "/mnt/x", "snap", "", False)
    assert not ok


def test_a_new_upload_replaces_an_unfinished_one(nodes):
    _, receiver = nodes
    first = receiver.begin_peer_upload(SENDER_ID, "/mnt/x", "snap", "", False)[1]["upload_id"]
    partial = receiver._uploads[first]["path"]
    receiver.begin_peer_upload(SENDER_ID, "/mnt/x", "snap", "", False)
    assert first not in receiver._uploads and not os.path.exists(partial)


def test_stale_partial_files_are_removed(nodes):
    _, receiver = nodes
    owner_dir = receiver._select_writable_stream_owner_dir(SENDER_ID)[1]
    leftover = os.path.join(owner_dir, ".partial-from-before-a-restart")
    open(leftover, "wb").close()
    old = os.path.getmtime(leftover) - receiver.UPLOAD_STALE_SECONDS - 60
    os.utime(leftover, (old, old))
    receiver.begin_peer_upload(SENDER_ID, "/mnt/x", "snap", "", False)
    assert not os.path.exists(leftover)


# ── Restore side ─────────────────────────────────────────────────────────────

def test_checked_stream_holds_back_the_end_until_verified():
    data = [b"a" * 10, b"b" * 10, b"c" * 10]
    good = hashlib.sha256(b"".join(data)).hexdigest()
    assert b"".join(bbm.BuddyBackupManager._checked_stream(iter(data), good)) == b"".join(data)

    received = []
    with pytest.raises(bbm._TransferError):
        for chunk in bbm.BuddyBackupManager._checked_stream(iter(data), "0" * 64):
            received.append(chunk)
    assert b"".join(received) == b"a" * 10 + b"b" * 10   # never the end of the stream


def test_restore_pipes_decrypted_data_into_receive(nodes):
    sender, _ = nodes
    material = bc.new_key_material(PASSWORD)
    data = os.urandom(2 * 1024 * 1024 + 9)
    enc = bc.StreamEncryptor(material)
    blob = enc.update(data) + enc.finalize()

    sink = Sink()
    sender.spawn_privileged = lambda cmd, stdin_pipe=False: (FakeReceive(sink), io.BytesIO())
    chunks = (blob[i:i + 65536] for i in range(0, len(blob), 65536))
    ok, err = sender._receive_stream("/usr/bin/btrfs", sender._decrypted_stream(chunks, PASSWORD), "/mnt/x")
    assert ok, err
    assert sink.final == data


def test_restore_stops_on_tampered_data(nodes):
    sender, _ = nodes
    material = bc.new_key_material(PASSWORD)
    enc = bc.StreamEncryptor(material)
    blob = bytearray(enc.update(os.urandom(3 * 1024 * 1024)) + enc.finalize())
    blob[-100] ^= 1

    sink = Sink()
    sender.spawn_privileged = lambda cmd, stdin_pipe=False: (FakeReceive(sink), io.BytesIO())
    ok, err = sender._receive_stream("/usr/bin/btrfs", sender._decrypted_stream(iter([bytes(blob)]), PASSWORD),
                                     "/mnt/x")
    assert not ok and "modified" in err


# ── Incremental sends ────────────────────────────────────────────────────────

@pytest.fixture
def syncing(nodes, monkeypatch):
    """A sender that syncs /mnt/alvaos/main/media to the receiver, with fake Btrfs."""
    sender, receiver = nodes
    wire(sender, receiver, monkeypatch)
    sender._save_peers({"receiver": {"node_id": "receiver", "api_secret": "s", "tunnel_ip": "100.95.95.8",
                                     "name": "Receiver"}})
    snapshots = set()
    deleted = []
    commands = []
    counter = iter(range(1, 1000))

    def take(source_path):
        name = f".alvaos-buddy-media-{next(counter)}"
        path = f"/mnt/alvaos/main/{name}"
        snapshots.add(path)
        return True, {"btrfs_cmd": "/usr/bin/btrfs", "snapshot_path": path, "snapshot_name": name,
                      "created_at": sender._now_iso()}

    def cleanup(path, btrfs_cmd, attempts=3):
        snapshots.discard(path)
        deleted.append(path)
        return True, ""

    def spawn(cmd, stdin_pipe=False):
        commands.append(cmd)
        return FakeSend(f"stream for {cmd[-1]}".encode()), io.BytesIO()

    monkeypatch.setattr(sender, "_take_send_snapshot", take)
    monkeypatch.setattr(sender, "_cleanup_temp_subvolume", cleanup)
    monkeypatch.setattr(sender, "_path_is_btrfs_subvolume", lambda path: path in snapshots)
    monkeypatch.setattr(sender, "_btrfs_cmd", lambda: "/usr/bin/btrfs")
    sender.spawn_privileged = spawn

    def sync():
        ok, result = sender.sync_to_peer("receiver", sources=["/mnt/alvaos/main/media"])
        assert ok and result["status"] == "success", result
        return result["created"][0]

    return sender, receiver, sync, commands, deleted, snapshots


def test_second_sync_sends_only_the_changes(syncing):
    sender, receiver, sync, commands, deleted, snapshots = syncing
    first = sync()
    assert not first["incremental"] and "-p" not in commands[-1]
    second = sync()
    assert second["incremental"]
    assert commands[-1] == ["/usr/bin/btrfs", "send", "-p", "/mnt/alvaos/main/.alvaos-buddy-media-1",
                            "/mnt/alvaos/main/.alvaos-buddy-media-2"]
    assert second["remote"]["parent_id"] == first["remote"]["id"]
    # Only the newest snapshot is kept locally as the next base.
    assert snapshots == {"/mnt/alvaos/main/.alvaos-buddy-media-2"}
    assert sender._load_send_state()["receiver"]["/mnt/alvaos/main/media"]["chain_length"] == 1


def test_full_send_when_the_buddy_dropped_the_base(syncing):
    sender, receiver, sync, commands, deleted, snapshots = syncing
    first = sync()
    receiver.delete_peer_stream(SENDER_ID, first["remote"]["id"])
    again = sync()
    assert not again["incremental"] and "-p" not in commands[-1]
    assert again["remote"]["parent_id"] == ""


def test_chain_restarts_with_a_full_snapshot(syncing, monkeypatch):
    sender, receiver, sync, commands, deleted, snapshots = syncing
    monkeypatch.setattr(bbm.BuddyBackupManager, "MAX_DELTA_CHAIN", 2)
    kinds = [sync()["incremental"] for _ in range(5)]
    assert kinds == [False, True, True, False, True]


def test_failed_sync_keeps_the_previous_base(syncing, monkeypatch):
    sender, receiver, sync, commands, deleted, snapshots = syncing
    sync()
    sender.spawn_privileged = lambda cmd, stdin_pipe=False: (FakeSend(b"", returncode=1), io.BytesIO(b"boom"))
    ok, result = sender.sync_to_peer("receiver", sources=["/mnt/alvaos/main/media"])
    assert result["status"] == "error"
    assert snapshots == {"/mnt/alvaos/main/.alvaos-buddy-media-1"}
    assert sender._load_send_state()["receiver"]["/mnt/alvaos/main/media"]["snapshot_path"].endswith("-1")


def test_removing_a_buddy_deletes_its_bases(syncing, monkeypatch):
    sender, receiver, sync, commands, deleted, snapshots = syncing
    sync()
    monkeypatch.setattr(sender, "apply_tunnel_config", lambda: (True, {}))
    ok, _ = sender.remove_peer("receiver", reciprocal=False)
    assert ok
    assert snapshots == set() and sender._load_send_state() == {}


def stream_entry(receiver, sid, parent="", size=1, received="2026-01-01T00:00:00"):
    owner_dir = receiver._select_writable_stream_owner_dir(SENDER_ID)[1]
    path = os.path.join(owner_dir, f"{sid}.stream")
    with open(path, "wb") as f:
        f.write(b"x")
    return {"id": sid, "owner_node_id": SENDER_ID, "parent_id": parent, "size_bytes": size,
            "received_at": received, "payload_path": path}


def test_quota_drops_whole_old_chains(nodes, monkeypatch):
    _, receiver = nodes
    gib = 1024 ** 3
    receiver._save_stream_entries([
        stream_entry(receiver, "a", size=4 * gib, received="2026-01-01"),
        stream_entry(receiver, "a1", parent="a", size=gib, received="2026-01-02"),
        stream_entry(receiver, "b", size=4 * gib, received="2026-01-03"),
        stream_entry(receiver, "b1", parent="b", size=gib, received="2026-01-04"),
    ])
    monkeypatch.setattr(receiver, "_owner_quota_bytes", lambda owner: 8 * gib)
    receiver._enforce_stream_quota(SENDER_ID)
    assert [e["id"] for e in receiver._load_stream_entries()] == ["b", "b1"]

    # The newest chain stays even when it alone exceeds the quota.
    monkeypatch.setattr(receiver, "_owner_quota_bytes", lambda owner: gib)
    receiver._enforce_stream_quota(SENDER_ID)
    assert [e["id"] for e in receiver._load_stream_entries()] == ["b", "b1"]


def test_deleting_a_base_deletes_what_builds_on_it(nodes):
    _, receiver = nodes
    receiver._save_stream_entries([
        stream_entry(receiver, "a"), stream_entry(receiver, "a1", parent="a"),
        stream_entry(receiver, "a2", parent="a1"), stream_entry(receiver, "b"),
    ])
    ok, body = receiver.delete_peer_stream(SENDER_ID, "a1")
    assert ok and sorted(body["removed_ids"]) == ["a1", "a2"]
    assert [e["id"] for e in receiver._load_stream_entries()] == ["a", "b"]


def test_restore_chain_is_resolved_oldest_first():
    streams = [{"id": "c", "parent_id": "b"}, {"id": "a", "parent_id": ""}, {"id": "b", "parent_id": "a"}]
    chain, err = bbm.BuddyBackupManager._snapshot_chain(streams, streams[0])
    assert not err and [s["id"] for s in chain] == ["a", "b", "c"]
    chain, err = bbm.BuddyBackupManager._snapshot_chain(streams[:1], streams[0])
    assert chain == [] and "no longer stored" in err


def test_restore_receives_the_chain_and_removes_the_bases(nodes, monkeypatch):
    node, _ = nodes
    received, removed = [], []
    subvolumes = set()

    def receive(btrfs_cmd, stream, target_parent):
        received.append(stream)
        subvolumes.add(stream)
        return True, ""

    monkeypatch.setattr(node, "_btrfs_cmd", lambda: "/usr/bin/btrfs")
    monkeypatch.setattr(node, "_is_mounted_path", lambda path: False)
    monkeypatch.setattr(node, "_mkdir_p", lambda path: (True, ""))
    monkeypatch.setattr(node, "_clear_restore_leftover", lambda *a: "")
    monkeypatch.setattr(node, "_list_btrfs_subvolume_names_under", lambda path: sorted(subvolumes))
    monkeypatch.setattr(node, "_receive_stream", receive)
    monkeypatch.setattr(node, "_receive_and_apply", lambda stream, *a: receive(None, stream, None) and (True, {}))
    monkeypatch.setattr(node, "_delete_subvolume_if_exists",
                        lambda path, cmd: removed.append(os.path.basename(path)) or (True, "", True))
    ok, _ = node._restore_from_stream(lambda: (True, "snap-3"), "snap-3", "/mnt/alvaos/main/media",
                                      ancestors=[("snap-1", lambda: (True, "snap-1")),
                                                 ("snap-2", lambda: (True, "snap-2"))])
    assert ok
    assert received == ["snap-1", "snap-2", "snap-3"]
    assert removed == ["snap-1", "snap-2"]
