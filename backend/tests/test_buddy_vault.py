"""Buddy Backup vaults: replication, retention, restore (backend/buddy_vault.py).

Two real BuddyBackupManager instances play owner and buddy. HTTP between them
becomes direct calls, the root helper that attaches the vault is recorded, and
Btrfs is simulated closely enough to matter: `btrfs receive` of an incremental
stream fails when its parent is missing, like the real one.
"""

import io
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest

import buddy_backup_manager as bbm
import buddy_crypto as bc
import buddy_vault as bv

PASSWORD = "correct horse battery staple"
SOURCE = "/mnt/alvaos/main/media"
BUDDY_ID = "buddy-node"


# ── Simulated Btrfs ──────────────────────────────────────────────────────────

class FakeBtrfs:
    def __init__(self):
        self.dirs = defaultdict(set)
        self.sends = []
        self.size, self.free = 100, 90

    def add(self, path):
        self.dirs[os.path.dirname(path)].add(os.path.basename(path))

    def remove(self, path):
        self.dirs[os.path.dirname(path)].discard(os.path.basename(path))

    def exists(self, path):
        return os.path.basename(path) in self.dirs.get(os.path.dirname(path), set())

    def under(self, mountpoint):
        for folder, names in self.dirs.items():
            if folder.startswith(mountpoint + "/"):
                for name in sorted(names):
                    yield f"{os.path.relpath(folder, mountpoint)}/{name}"


class Proc:
    def __init__(self, stdout=None, stdin=None, returncode=0):
        self.stdout, self.stdin, self.returncode = stdout, stdin, returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


class ReceiveSink(io.BytesIO):
    def __init__(self, fs, target, proc_holder):
        super().__init__()
        self.fs, self.target, self.proc_holder = fs, target, proc_holder

    def close(self):
        data = self.getvalue()
        proc = self.proc_holder[0]
        try:
            stream = json.loads(data.decode())
        except ValueError:
            proc.returncode = 1
        else:
            if stream["parent"] and stream["parent"] not in self.fs.dirs[self.target]:
                proc.returncode = 1
            else:
                self.fs.add(os.path.join(self.target, stream["name"]))
        super().close()


class Completed:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(name):
        state = tmp_path / name
        state.mkdir()
        monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(state))
        return bbm.BuddyBackupManager(lambda *a, **k: (None, "no commands in tests"))

    owner, buddy = make("owner"), make("buddy")
    owner_id = owner._identity_public()["node_id"]
    owner._save_peers({BUDDY_ID: {"node_id": BUDDY_ID, "name": "Buddy", "tunnel_ip": "127.95.1.8",
                                  "api_secret": "s"}})
    buddy._save_peers({owner_id: {"node_id": owner_id, "name": "Owner", "tunnel_ip": "127.95.1.7"}})
    monkeypatch.setattr(buddy, "_owner_quota_bytes", lambda owner: 64 * 1024 * 1024)
    ok, _ = owner.save_settings({"encryption_enabled": True, "encryption_password": PASSWORD})
    assert ok

    fs = FakeBtrfs()
    helper_calls = []
    today = datetime.now(timezone.utc).replace(hour=2, minute=0, second=0, microsecond=0)
    # Snapshots are taken once a day, starting ten days ago (tests may move this).
    clock = {"day": 0, "base": today - timedelta(days=10)}

    def take(source_path):
        stamp = clock["base"] + timedelta(days=clock["day"])
        clock["day"] += 1
        name = f".alvaos-buddy-media-{stamp:%Y%m%d-%H%M%S}-{clock['day']:04x}"
        path = f"/mnt/alvaos/main/{name}"
        fs.add(path)
        return True, {"btrfs_cmd": "/usr/bin/btrfs", "snapshot_path": path, "snapshot_name": name,
                      "created_at": stamp.isoformat()}

    def run_command(cmd, timeout=None):
        args = cmd[1:]
        if args[:2] == ["subvolume", "list"]:
            lines = [f"ID {256 + i} gen 1 top level 5 path {rel}" for i, rel in enumerate(fs.under(args[2]))]
            return Completed("\n".join(lines)), None
        if args[:2] == ["filesystem", "usage"]:
            return Completed(f"Overall:\n    Device size:\t\t{fs.size}\n    Free (estimated):\t\t{fs.free}\t(min: 1)\n"), None
        if args[:2] == ["subvolume", "delete"]:
            fs.remove(args[2])
            fs.free += 10
            return Completed(), None
        return Completed(), None

    def spawn(cmd, stdin_pipe=False):
        if cmd[1] == "send":
            parent = os.path.basename(cmd[3]) if cmd[2] == "-p" else None
            fs.sends.append(cmd[2:])
            payload = json.dumps({"name": os.path.basename(cmd[-1]), "parent": parent}).encode()
            return Proc(stdout=io.BytesIO(payload)), io.BytesIO()
        holder = []
        proc = Proc(stdin=ReceiveSink(fs, cmd[2], holder))
        holder.append(proc)
        return proc, io.BytesIO(b"ERROR: cannot find parent subvolume")

    def peer_request(peer, method, path, timeout=60, **kwargs):
        if path == "/api/v1/backup/buddy/peer/list":
            return 200, {"streams": buddy.list_peer_streams(owner_id)[1]["streams"]}
        assert path.startswith(bv.VAULT_API)
        store = buddy.vault_store
        try:
            if path.endswith("/key"):
                store.store_key(owner_id, kwargs["json"]["key_blob"])
                return 200, {"success": True}
            if method == "POST":
                return 200, store.ensure(owner_id)
            return 200, store.info(owner_id)
        except bv.VaultError as exc:
            return 400, {"error": str(exc)}

    def helper(args, stdin):
        helper_calls.append((args, stdin))
        if args[0] == "vault-open":
            return True, {"mountpoint": f"/run/alvaos-vault/{args[1]}"}
        return True, {"closed": args[1]}

    for node in (owner, buddy):
        monkeypatch.setattr(node, "_btrfs_cmd", lambda: "/usr/bin/btrfs")
        monkeypatch.setattr(node, "_mkdir_p", lambda path: (True, ""))
        monkeypatch.setattr(node, "_path_is_btrfs_subvolume", fs.exists)
        monkeypatch.setattr(node, "_list_btrfs_subvolume_names_under", lambda path: sorted(fs.dirs.get(path, ())))
        monkeypatch.setattr(node, "_cleanup_temp_subvolume", lambda path, cmd, attempts=3: (fs.remove(path), (True, ""))[1])
        monkeypatch.setattr(node, "_delete_subvolume_if_exists", lambda path, cmd: (fs.remove(path), (True, "", True))[1])
    monkeypatch.setattr(owner, "_take_send_snapshot", take)
    monkeypatch.setattr(owner, "run_command", run_command)
    monkeypatch.setattr(owner, "_peer_request", peer_request)
    owner.spawn_privileged = spawn
    owner.vault.helper = helper

    class World:
        pass
    w = World()
    w.owner, w.buddy, w.owner_id, w.fs, w.helper_calls, w.clock = owner, buddy, owner_id, fs, helper_calls, clock
    w.mount = f"/run/alvaos-vault/{bv.vault_name(BUDDY_ID)}"
    w.vault_dir = os.path.join(w.mount, bv.source_dir_name(SOURCE))

    def sync():
        ok, result = owner.sync_to_peer(BUDDY_ID, sources=[SOURCE])
        assert ok and result["status"] == "success", result
        return result["created"][0]
    w.sync = sync
    return w


# ── Replication ──────────────────────────────────────────────────────────────

def test_first_sync_creates_an_encrypted_vault(world):
    first = world.sync()
    assert not first["incremental"]
    args, stdin = world.helper_calls[0]
    assert args[:2] == ["vault-open", bv.vault_name(BUDDY_ID)]
    assert args[2:] == ["127.95.1.8", world.owner_id, "create"]
    key = stdin.decode()
    assert len(key) == 64 and key not in " ".join(args)
    assert world.helper_calls[-1][0][0] == "vault-close"

    # The buddy has an image and the sealed key, which opens with the password.
    info = world.buddy.vault_store.info(world.owner_id)
    assert info["exists"] and info["size_bytes"] == 64 * 1024 * 1024
    assert bc.open_vault_key(info["key_blob"], passphrase=PASSWORD).hex() == key
    assert world.fs.dirs[world.vault_dir] == {first["snapshot_name"]}


def test_later_syncs_only_send_changes_forever(world):
    names = [world.sync()["snapshot_name"] for _ in range(40)]
    assert world.fs.sends[0] == [f"/mnt/alvaos/main/{names[0]}"]
    # Every later sync is a delta against the previous one; no periodic full.
    for previous, sent in zip(names[:-1], world.fs.sends[1:], strict=True):
        assert sent[:2] == ["-p", f"/mnt/alvaos/main/{previous}"]
    # Only the newest snapshot is kept locally.
    assert world.fs.dirs["/mnt/alvaos/main"] == {names[-1]}


def test_deleting_old_snapshots_in_the_vault_keeps_deltas_working(world):
    names = [world.sync()["snapshot_name"] for _ in range(3)]
    ok, _ = world.owner.delete_remote_snapshot(BUDDY_ID, f"{bv.source_dir_name(SOURCE)}/{names[0]}")
    assert ok
    assert world.sync()["incremental"]


def test_full_send_when_the_vault_lost_the_base(world):
    names = [world.sync()["snapshot_name"] for _ in range(2)]
    world.fs.remove(os.path.join(world.vault_dir, names[-1]))
    again = world.sync()
    assert not again["incremental"] and world.fs.sends[-1][0] != "-p"


def test_a_failed_delta_falls_back_to_a_full_send(world, monkeypatch):
    world.sync()
    real = world.owner.spawn_privileged

    def spawn(cmd, stdin_pipe=False):
        if cmd[1] == "send" and cmd[2] == "-p":
            return Proc(stdout=io.BytesIO(b"garbage")), io.BytesIO()
        return real(cmd, stdin_pipe)
    world.owner.spawn_privileged = spawn
    result = world.sync()
    assert not result["incremental"]
    assert result["snapshot_name"] in world.fs.dirs[world.vault_dir]


def test_no_password_no_vault(world):
    world.owner._delete_encryption_material()
    ok, result = world.owner.sync_to_peer(BUDDY_ID, sources=[SOURCE])
    assert result["status"] == "error" and "encryption password" in result["failed"][0]["error"]
    assert not [c for c in world.helper_calls if c[0][0] == "vault-open"]


def test_vault_is_closed_even_when_a_sync_fails(world):
    world.owner._take_send_snapshot = lambda source: (False, {"error": "source vanished"})
    ok, result = world.owner.sync_to_peer(BUDDY_ID, sources=[SOURCE])
    assert result["status"] == "error"
    assert world.helper_calls[-1][0][0] == "vault-close"


# ── Retention ────────────────────────────────────────────────────────────────

def days_ago(now, *days):
    return [(f"s{d}", now - timedelta(days=d)) for d in days]


def test_retention_keeps_daily_weekly_monthly():
    now = datetime(2026, 6, 30, 12, tzinfo=timezone.utc)
    snaps = days_ago(now, *range(0, 400))
    keep = bv.retention_keep(snaps, keep_daily=7, keep_weekly=4, keep_monthly=6, now=now)
    assert {f"s{d}" for d in range(7)} <= keep                 # every day of the last week
    assert "s10" not in keep and "s200" not in keep
    assert len(keep) <= 7 + 4 + 6
    assert "s399" not in keep                                   # older than every rule


def test_retention_always_keeps_the_newest():
    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    assert bv.retention_keep(days_ago(now, 900, 1000), 0, 0, 0, now) == {"s900"}


def test_sync_applies_retention(world):
    world.clock["base"] -= timedelta(days=19)
    names = [world.sync()["snapshot_name"] for _ in range(30)]   # the last 30 days, one per day
    kept = world.fs.dirs[world.vault_dir]
    expected = bv.retention_keep([(n, bv.snapshot_time(n)) for n in names], 14, 8, 12,
                                 datetime.now(timezone.utc))
    assert kept == expected
    assert names[-1] in kept and len(kept) < 30


def test_old_snapshots_go_but_the_base_stays(world):
    world.clock["base"] -= timedelta(days=800)       # all far older than any retention rule
    names = [world.sync()["snapshot_name"] for _ in range(5)]
    assert world.fs.dirs[world.vault_dir] == {names[-1]}
    assert world.owner._load_send_state()[BUDDY_ID][SOURCE]["snapshot_name"] == names[-1]


def test_prune_frees_space_oldest_first(world):
    names = [world.sync()["snapshot_name"] for _ in range(5)]
    world.fs.free = 0            # full: below the 15 % floor, each delete frees 10 %
    deleted = world.owner.vault.prune(world.mount, {"keep_daily": 30, "keep_weekly": 0, "keep_monthly": 0},
                                      protected=set())
    assert [d.split("/")[1] for d in deleted] == names[:2]      # +10 each until >= 15
    assert names[-1] in world.fs.dirs[world.vault_dir]


# ── Listing, restore, replacement NAS ────────────────────────────────────────

def test_listing_is_saved_at_sync(world):
    first = world.sync()
    ok, listing = world.owner.fetch_remote_snapshots(BUDDY_ID)
    assert ok
    vault_items = [s for s in listing["streams"] if s.get("kind") == "vault"]
    assert [s["snapshot_name"] for s in vault_items] == [first["snapshot_name"]]
    assert vault_items[0]["source_path"] == SOURCE and not listing["needs_passphrase"]


def test_restore_sends_from_the_vault_read_only(world, monkeypatch):
    first = world.sync()
    received = {}

    def restore(stream, snapshot_name, source_path):
        received.update(data=b"".join(stream), name=snapshot_name, source=source_path)
        return True, {"message": "Restore completed"}
    monkeypatch.setattr(world.owner, "_restore_from_stream", restore)
    sid = f"{bv.source_dir_name(SOURCE)}/{first['snapshot_name']}"
    ok, result = world.owner.restore_from_remote_snapshot(BUDDY_ID, sid)
    assert ok, result
    assert received["name"] == first["snapshot_name"] and received["source"] == SOURCE
    opens = [c for c in world.helper_calls if c[0][0] == "vault-open"]
    assert opens[-1][0][-1] == "ro"
    assert world.fs.sends[-1] == [os.path.join(world.vault_dir, first["snapshot_name"])]


def test_replacement_nas_unlocks_with_the_password(world):
    world.sync()
    world.owner.vault.forget_key(BUDDY_ID)          # the NAS was reinstalled
    world.owner._delete_encryption_material()
    ok, listing = world.owner.fetch_remote_snapshots(BUDDY_ID, refresh=True)
    assert listing["needs_passphrase"] and "password" in listing["vault_error"]
    ok, listing = world.owner.fetch_remote_snapshots(BUDDY_ID, refresh=True, passphrase="wrong password!")
    assert "Wrong encryption password" in listing["vault_error"]
    ok, listing = world.owner.fetch_remote_snapshots(BUDDY_ID, refresh=True, passphrase=PASSWORD)
    assert not listing["vault_error"] and not listing["needs_passphrase"]
    assert world.owner.vault.has_local_key(BUDDY_ID)


def test_password_change_reseals_the_key_on_the_buddy(world):
    world.sync()
    world.owner.save_settings({"encryption_enabled": True, "encryption_password": "a brand new password"})
    world.sync()
    blob = world.buddy.vault_store.info(world.owner_id)["key_blob"]
    assert bc.open_vault_key(blob, passphrase="a brand new password")


def test_removing_the_owner_on_the_buddy_deletes_its_vault(world, monkeypatch):
    world.sync()
    monkeypatch.setattr(world.buddy, "sync_link", lambda: (True, {}))
    ok, _ = world.buddy.remove_peer(world.owner_id, reciprocal=False)
    assert ok and not world.buddy.vault_store.info(world.owner_id)["exists"]


# ── Pure helpers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/", "/mnt/alvaos/main/media", "/mnt/alvaos/main/Fotos 2026/ä"])
def test_source_dir_round_trip(path):
    assert bv.source_from_dir(bv.source_dir_name(path)) == path


def test_bad_names_are_rejected():
    assert bv.source_from_dir("src-../etc") is None
    assert bv.source_from_dir("other") is None
    assert bv.snapshot_time("../../etc") is None
    assert bv.snapshot_time(".alvaos-buddy-media-20260101-020000-00ab") is not None


def test_parse_usage():
    text = ("Overall:\n    Device size:\t\t 107374182400\n    Device allocated:\t\t 5368709120\n"
            "    Free (estimated):\t\t 99857989632\t(min: 97173635072)\n")
    assert bv.parse_usage(text) == (107374182400, 99857989632)


# ── Restore streaming (shared with old-format snapshots) ─────────────────────

class Sink(io.BytesIO):
    def close(self):
        self.final = self.getvalue()
        super().close()


def test_checked_stream_holds_back_the_end_until_verified():
    import hashlib
    data = [b"a" * 10, b"b" * 10, b"c" * 10]
    good = hashlib.sha256(b"".join(data)).hexdigest()
    assert b"".join(bbm.BuddyBackupManager._checked_stream(iter(data), good)) == b"".join(data)
    received = []
    with pytest.raises(bbm._TransferError):
        for chunk in bbm.BuddyBackupManager._checked_stream(iter(data), "0" * 64):
            received.append(chunk)
    assert b"".join(received) == b"a" * 10 + b"b" * 10


def test_restore_stops_on_tampered_old_format_data(world):
    material = bc.new_key_material(PASSWORD)
    enc = bc.StreamEncryptor(material)
    blob = bytearray(enc.update(os.urandom(3 * 1024 * 1024)) + enc.finalize())
    blob[-100] ^= 1
    sink = Sink()
    world.owner.spawn_privileged = lambda cmd, stdin_pipe=False: (Proc(stdin=sink), io.BytesIO())
    ok, err = world.owner._receive_stream(
        "/usr/bin/btrfs", world.owner._decrypted_stream(iter([bytes(blob)]), PASSWORD), "/mnt/x")
    assert not ok and "modified" in err
