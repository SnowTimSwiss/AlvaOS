#!/usr/bin/env python3
"""
Buddy Backup vaults: encrypted replication without ever resending everything.

On the buddy, every NAS it backs up owns one sparse image file, exported over
the WireGuard tunnel by a small NBD server (VaultStore). The owning NAS
attaches the image, unlocks it with a key only it has (LUKS) and mounts the
Btrfs filesystem inside (vault_ops.py, as root). Replication then works like
ZFS replication in TrueNAS, but entirely on the owner's side:

    btrfs send -p <newest snapshot both sides have> <new snapshot>
        | btrfs receive /run/alvaos-vault/<vault>/<source>/

Every received snapshot is a complete, independent subvolume that shares
unchanged blocks with the others, so any of them can be deleted at any time;
the next sync only needs the newest one both sides still have. The buddy only
ever stores encrypted blocks.

Layout inside a vault:
    src-<base64url(source path)>/.alvaos-buddy-<slug>-<YYYYmmdd-HHMMSS>-<hex>
"""

import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import requests

import buddy_crypto
import nbd_server
from common import PRIV_HELPER, build_privileged_cmd, is_root_user

if TYPE_CHECKING:
    from buddy_backup_manager import BuddyBackupManager

VAULT_API = "/api/v1/backup/buddy/peer/vault"
SNAPSHOT_RE = re.compile(r"^\.alvaos-buddy-[a-z0-9._-]+-(\d{8}-\d{6})-[0-9a-f]{4}$")
SOURCE_DIR_RE = re.compile(r"^src-[A-Za-z0-9_-]{1,340}$")
# Deleting snapshots when free space in the vault drops below this share.
MIN_FREE_RATIO = 0.15
DEFAULT_RETENTION = {"keep_daily": 14, "keep_weekly": 8, "keep_monthly": 12}


class VaultError(Exception):
    """A vault operation failed; the message is shown to the user."""


class TransferError(Exception):
    """A snapshot transfer failed; the message is shown to the user."""


# ── Streaming root commands ──────────────────────────────────────────────────

def spawn_privileged(cmd: List[str], stdin_pipe: bool = False) -> Tuple[subprocess.Popen, Any]:
    """Start a root command that streams: (process, stderr file).

    By default its stdout is a pipe to read from (btrfs send); with
    stdin_pipe its stdin is a pipe to write to (btrfs receive).
    """
    stderr = tempfile.TemporaryFile()
    env = dict(os.environ, LC_ALL="C")
    proc = subprocess.Popen(
        build_privileged_cmd(cmd),
        stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL if stdin_pipe else subprocess.PIPE,
        stderr=stderr, env=env,
    )
    return proc, stderr


def read_stderr(stderr) -> str:
    try:
        stderr.seek(0)
        return stderr.read()[-4000:].decode("utf-8", "replace").strip()
    except Exception:
        return ""


def stop_process(proc) -> None:
    """End a streaming child. Closing the pipe stops `btrfs send` with EPIPE;
    the process runs as root, so signals from the backend may not reach it."""
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=60)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ── Pure helpers ─────────────────────────────────────────────────────────────

def vault_name(peer_node_id: str) -> str:
    """Short, filesystem-safe name for the vault kept on one buddy."""
    return hashlib.sha256(str(peer_node_id).encode("utf-8")).hexdigest()[:16]


def source_dir_name(source_path: str) -> str:
    encoded = base64.urlsafe_b64encode(source_path.encode("utf-8")).decode("ascii").rstrip("=")
    return f"src-{encoded}"


def source_from_dir(name: str) -> Optional[str]:
    if not SOURCE_DIR_RE.match(name or ""):
        return None
    body = name[4:]
    try:
        path = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return path if path.startswith("/") else None


def snapshot_time(name: str) -> Optional[datetime]:
    match = SNAPSHOT_RE.match(name or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def retention_keep(snapshots: List[Tuple[str, datetime]], keep_daily: int, keep_weekly: int,
                   keep_monthly: int, now: datetime) -> Set[str]:
    """Which snapshots to keep: the newest one of each of the last N days,
    weeks and months (like TrueNAS/Time Machine), and always the newest."""
    if not snapshots:
        return set()
    ordered = sorted(snapshots, key=lambda item: item[1], reverse=True)
    keep = {ordered[0][0]}
    today = now.date()

    def weeks_between(a: date, b: date) -> int:
        return (a - timedelta(days=a.weekday()) - (b - timedelta(days=b.weekday()))).days // 7

    buckets: List[Tuple[int, Callable[[date], Any], Callable[[date], int]]] = [
        (keep_daily, lambda d: d, lambda d: (today - d).days),
        (keep_weekly, lambda d: d.isocalendar()[:2], lambda d: weeks_between(today, d)),
        (keep_monthly, lambda d: (d.year, d.month), lambda d: (today.year - d.year) * 12 + today.month - d.month),
    ]
    for count, bucket_of, age_of in buckets:
        seen = set()
        for name, stamp in ordered:
            day = stamp.date()
            bucket = bucket_of(day)
            if bucket in seen or age_of(day) >= count:
                continue
            seen.add(bucket)
            keep.add(name)
    return keep


def parse_usage(text: str) -> Tuple[int, int]:
    """(device size, estimated free) in bytes from `btrfs filesystem usage -b`."""
    size = free = 0
    for line in (text or "").splitlines():
        label, _, value = line.strip().partition(":")
        numbers = re.findall(r"\d+", value)
        if not numbers:
            continue
        if label == "Device size":
            size = int(numbers[0])
        elif label == "Free (estimated)":
            free = int(numbers[0])
    return size, free


# ── Buddy side: the images other NAS keep here ───────────────────────────────

class VaultStore:
    IMAGE_NAME = "vault.img"
    KEY_NAME = "vault.key"

    def __init__(self, manager: "BuddyBackupManager"):
        self.m = manager
        self.server: Optional[nbd_server.NbdServer] = None
        self._server_ip = ""
        self._lock = threading.Lock()

    def _paths(self, owner: str) -> Tuple[str, str, str]:
        root, owner_dir, err = self.m._select_writable_stream_owner_dir(owner)
        if not root or not owner_dir:
            raise VaultError(err or "No place to keep the buddy's vault")
        return owner_dir, os.path.join(owner_dir, self.IMAGE_NAME), os.path.join(owner_dir, self.KEY_NAME)

    def info(self, owner: str) -> Dict[str, Any]:
        owner_dir, image, key_file = self._paths(owner)
        quota = self.m._owner_quota_bytes(owner)
        result: Dict[str, Any] = {"exists": os.path.exists(image), "quota_bytes": quota,
                                  "size_bytes": 0, "used_bytes": 0, "key_blob": ""}
        if result["exists"]:
            st = os.stat(image)
            result["size_bytes"] = st.st_size
            result["used_bytes"] = st.st_blocks * 512
        if os.path.exists(key_file):
            with open(key_file, encoding="ascii", errors="replace") as f:
                result["key_blob"] = f.read(4096).strip()
        return result

    def ensure(self, owner: str) -> Dict[str, Any]:
        """Create the owner's image, or grow it to a raised quota (never shrink)."""
        owner_dir, image, _ = self._paths(owner)
        quota = self.m._owner_quota_bytes(owner)
        with self._lock:
            if not os.path.exists(image):
                fd = os.open(image, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    os.ftruncate(fd, quota)   # sparse: takes no space until written
                finally:
                    os.close(fd)
            elif os.path.getsize(image) < quota:
                os.truncate(image, quota)
        return self.info(owner)

    def store_key(self, owner: str, blob: str) -> None:
        blob = str(blob or "").strip()
        if not blob.startswith(buddy_crypto.VAULT_KEY_PREFIX) or len(blob) > 4096:
            raise VaultError("Invalid vault key blob")
        owner_dir, _, key_file = self._paths(owner)
        tmp = f"{key_file}.{secrets.token_hex(4)}"
        with open(tmp, "w", encoding="ascii") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, key_file)

    def delete(self, owner: str) -> bool:
        owner_dir, image, key_file = self._paths(owner)
        existed = os.path.exists(image)
        for path in (image, key_file):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        return existed

    def resolve(self, remote_ip: str, export_name: str) -> Optional[nbd_server.Export]:
        """NBD access check: a buddy may only open its own vault, from its tunnel address."""
        peer = self.m.peer_for_tunnel_ip(remote_ip)
        if peer is None or str(peer.get("node_id") or "") != export_name:
            return None
        owner_dir, image, _ = self._paths(export_name)
        if not os.path.exists(image):
            return None

        def space_ok() -> bool:
            try:
                return self.m._free_bytes(owner_dir) > self.m._free_space_reserve_bytes(owner_dir)
            except OSError:
                return False
        return nbd_server.Export(key=export_name, path=image, space_ok=space_ok)

    def serve_forever(self, interval: float = 30.0, stop: Optional[threading.Event] = None) -> None:
        """Keep the NBD server listening on this NAS's tunnel address."""
        stop = stop or threading.Event()
        warned = ""
        while not stop.is_set():
            try:
                has_peers = bool(self.m._load_peers())
                ip = str(self.m._identity_public().get("tunnel_ip") or "") if has_peers else ""
                if self.server is not None and ip != self._server_ip:
                    self.server.close()
                    self.server, self._server_ip = None, ""
                if ip and self.server is None:
                    server = nbd_server.NbdServer(self.resolve)
                    server.listen(ip, nbd_server.NBD_PORT)
                    self.server, self._server_ip = server, ip
                    warned = ""
            except OSError as exc:
                # The tunnel interface may not be up yet; try again later.
                if warned != str(exc):
                    print(f"Buddy vault server not listening yet: {exc}")
                    warned = str(exc)
            stop.wait(interval)


# ── Owner side: replicating into a vault on a buddy ─────────────────────────

class VaultReplicator:
    def __init__(self, manager: "BuddyBackupManager",
                 helper: Optional[Callable[[List[str], bytes], Tuple[bool, Any]]] = None):
        self.m = manager
        self.helper = helper or self._run_helper
        self.keys_file = os.path.join(manager.state_dir, "buddy_vault_keys.json")

    # Root operations ------------------------------------------------------

    @staticmethod
    def _run_helper(args: List[str], stdin: bytes) -> Tuple[bool, Any]:
        cmd = [PRIV_HELPER] + args
        if not is_root_user():
            cmd = ["sudo", "-n"] + cmd
        try:
            result = subprocess.run(cmd, input=stdin, capture_output=True, timeout=900, env={"LC_ALL": "C"})
        except Exception as exc:
            return False, str(exc)
        out = result.stdout.decode("utf-8", "replace").strip().splitlines()
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", "replace").strip()
            return False, err.replace("alvaos-priv: ", "") or f"exit code {result.returncode}"
        try:
            return True, json.loads(out[-1]) if out else {}
        except ValueError:
            return True, {}

    # Keys -----------------------------------------------------------------

    def _load_keys(self) -> Dict[str, Dict[str, str]]:
        data = self.m._load_json(self.keys_file, {})
        return data if isinstance(data, dict) else {}

    def _save_keys(self, keys: Dict[str, Dict[str, str]]) -> None:
        fd = os.open(self.keys_file + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(keys, f)
        os.replace(self.keys_file + ".tmp", self.keys_file)

    def has_local_key(self, peer_id: str) -> bool:
        return peer_id in self._load_keys()

    def forget_key(self, peer_id: str) -> None:
        keys = self._load_keys()
        if keys.pop(peer_id, None) is not None:
            self._save_keys(keys)

    def _remote(self, peer: Dict, method: str, path: str = "", **kwargs) -> Dict:
        try:
            status, body = self.m._peer_request(peer, method, VAULT_API + path, **kwargs)
        except (requests.RequestException, TransferError) as exc:
            raise VaultError(f"Buddy is not reachable: {exc}") from exc
        if not (200 <= status < 300):
            raise VaultError(body.get("error") or f"Buddy answered HTTP {status}")
        return body

    def _seal_and_store(self, peer: Dict, key: bytes, material: "buddy_crypto.KeyMaterial") -> None:
        blob = buddy_crypto.seal_vault_key(key, material)
        self._remote(peer, "PUT", "/key", json={"key_blob": blob})
        keys = self._load_keys()
        keys[str(peer["node_id"])] = {"key": key.hex(),
                                      "sealed_salt": base64.b64encode(material.kdf_salt).decode("ascii")}
        self._save_keys(keys)

    def _key_for(self, peer: Dict, info: Dict, create: bool, passphrase: str) -> str:
        peer_id = str(peer["node_id"])
        material = self.m._encryption_material()
        local = self._load_keys().get(peer_id)
        if local:
            # Re-seal after a password change so a replacement NAS can use the new one.
            if material is not None and local.get("sealed_salt") != base64.b64encode(material.kdf_salt).decode():
                self._seal_and_store(peer, bytes.fromhex(local["key"]), material)
            return local["key"]
        blob = str(info.get("key_blob") or "")
        if blob:
            try:
                key = buddy_crypto.open_vault_key(blob, passphrase=passphrase, material=material)
            except buddy_crypto.BuddyCryptoError as exc:
                raise VaultError(str(exc)) from exc
            keys = self._load_keys()
            keys[peer_id] = {"key": key.hex(), "sealed_salt": base64.b64encode(
                buddy_crypto.vault_key_salt(blob)).decode("ascii")}
            self._save_keys(keys)
            return key.hex()
        if not create:
            raise VaultError("There is no backup on this buddy yet")
        if material is None:
            raise VaultError("Set an encryption password in the Buddy Backup settings first; "
                             "backups on a buddy are always encrypted")
        key = secrets.token_bytes(32)
        # The sealed key is on the buddy before the vault is formatted with it,
        # so no vault can exist whose key is lost.
        self._seal_and_store(peer, key, material)
        return key.hex()

    # Sessions -------------------------------------------------------------

    @contextmanager
    def session(self, peer: Dict, mode: str = "rw", passphrase: str = "") -> Iterator[str]:
        """Attach, unlock and mount the vault on `peer`; yields the mount point."""
        info = self._remote(peer, "POST")
        key = self._key_for(peer, info, create=(mode == "rw"), passphrase=passphrase)
        name = vault_name(str(peer["node_id"]))
        own_id = str(self.m._identity_public().get("node_id") or "")
        ok, result = self.helper(["vault-open", name, str(peer.get("tunnel_ip") or ""), own_id,
                                  "create" if mode == "rw" else "ro"], key.encode("ascii"))
        if not ok:
            raise VaultError(f"Could not open the backup vault on the buddy: {result}")
        try:
            yield str(result.get("mountpoint") or "")
        finally:
            self.helper(["vault-close", name], b"")

    # Vault contents -------------------------------------------------------

    def _btrfs(self) -> str:
        btrfs = self.m._btrfs_cmd()
        if not btrfs:
            raise VaultError("btrfs command not found")
        return btrfs

    def list_snapshots(self, mountpoint: str) -> List[Dict[str, Any]]:
        res, err = self.m.run_command([self._btrfs(), "subvolume", "list", mountpoint], timeout=60)
        if err or not res or res.returncode != 0:
            raise VaultError(err or "Could not list the vault")
        items = []
        for line in (res.stdout or "").splitlines():
            _, marker, rel = line.partition(" path ")
            if not marker:
                continue
            parts = rel.strip().split("/")
            if len(parts) != 2:
                continue
            source = source_from_dir(parts[0])
            stamp = snapshot_time(parts[1])
            if source is None or stamp is None:
                continue
            items.append({"id": f"{parts[0]}/{parts[1]}", "source_path": source, "snapshot_name": parts[1],
                          "created_at": stamp.isoformat(), "kind": "vault"})
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def usage(self, mountpoint: str) -> Tuple[int, int]:
        res, err = self.m.run_command([self._btrfs(), "filesystem", "usage", "-b", mountpoint], timeout=60)
        if err or not res or res.returncode != 0:
            return 0, 0
        return parse_usage(res.stdout or "")

    def _delete(self, path: str) -> None:
        res, err = self.m.run_command([self._btrfs(), "subvolume", "delete", path], timeout=300)
        if err or not res or res.returncode != 0:
            raise VaultError(err or f"Could not delete {os.path.basename(path)}")

    def prune(self, mountpoint: str, retention: Dict[str, int], protected: Set[str],
              now: Optional[datetime] = None) -> List[str]:
        """Apply retention per source, then free space if the vault is nearly full.

        The newest snapshot of each source is never deleted: it is the base
        for the next sync. Neither are names in `protected`.
        """
        now = now or datetime.now(timezone.utc)
        snapshots = self.list_snapshots(mountpoint)
        by_source: Dict[str, List[Dict[str, Any]]] = {}
        for item in snapshots:
            by_source.setdefault(item["id"].split("/")[0], []).append(item)

        deleted = []
        keep_all: Set[str] = set(protected)
        for items in by_source.values():
            pairs = [(item["snapshot_name"], datetime.fromisoformat(item["created_at"])) for item in items]
            keep = retention_keep(pairs, retention["keep_daily"], retention["keep_weekly"],
                                  retention["keep_monthly"], now)
            keep_all |= {items[0]["snapshot_name"]}
            for item in items:
                if item["snapshot_name"] not in keep and item["snapshot_name"] not in keep_all:
                    self._delete(os.path.join(mountpoint, item["id"]))
                    deleted.append(item["id"])

        remaining = [item for item in snapshots if item["id"] not in deleted]
        newest = {items[0]["id"] for items in by_source.values()}
        candidates = sorted((item for item in remaining
                             if item["id"] not in newest and item["snapshot_name"] not in keep_all),
                            key=lambda item: item["created_at"])
        for item in candidates:
            size, free = self.usage(mountpoint)
            if not size or free >= size * MIN_FREE_RATIO:
                break
            self._delete(os.path.join(mountpoint, item["id"]))
            deleted.append(item["id"])
            # Deleted subvolumes are cleaned up in the background; wait so the
            # next usage reading reflects the freed space.
            self.m.run_command([self._btrfs(), "subvolume", "sync", mountpoint], timeout=900)
        return deleted

    # Transfers ------------------------------------------------------------

    def _send_receive(self, snapshot: Dict, base: Optional[Dict], target_dir: str) -> Tuple[bool, str]:
        """Pipe `btrfs send [-p base] snapshot` into `btrfs receive target_dir`."""
        btrfs = self._btrfs()
        before = set(self.m._list_btrfs_subvolume_names_under(target_dir))
        cmd = [btrfs, "send"] + (["-p", base["snapshot_path"]] if base else []) + [snapshot["snapshot_path"]]
        proc, stderr = self.m.spawn_privileged(cmd)
        try:
            ok, err = self.m._receive_stream(btrfs, self._stream_output(proc, stderr, "btrfs send"), target_dir)
        finally:
            stop_process(proc)
            stderr.close()
        if not ok:
            for name in set(self.m._list_btrfs_subvolume_names_under(target_dir)) - before:
                self.m._delete_subvolume_if_exists(os.path.join(target_dir, name), btrfs)
            if "No space left" in err:
                err = "The vault on the buddy is full. Raise the space your buddy grants you or keep fewer snapshots."
        return ok, err

    @staticmethod
    def _stream_output(proc, stderr, what: str) -> Iterator[bytes]:
        assert proc.stdout is not None
        for block in iter(lambda: proc.stdout.read(1024 * 1024), b""):
            yield block
        if proc.wait() != 0:
            raise TransferError(f"{what} failed: {read_stderr(stderr) or f'exit code {proc.returncode}'}")

    def replicate_source(self, peer: Dict, mountpoint: str, source_path: str) -> Dict[str, Any]:
        """Send one source: only the changes if the vault has our last snapshot."""
        peer_id = str(peer["node_id"])
        target_dir = os.path.join(mountpoint, source_dir_name(source_path))
        made, err = self.m._mkdir_p(target_dir)
        if not made:
            raise VaultError(err or "Could not prepare the vault")
        in_vault = set(self.m._list_btrfs_subvolume_names_under(target_dir))
        base = self.m._load_send_state().get(peer_id, {}).get(source_path)
        if not (isinstance(base, dict) and base.get("snapshot_name") in in_vault
                and self.m._path_is_btrfs_subvolume(str(base.get("snapshot_path") or ""))):
            base = None

        ok, snapshot = self.m._take_send_snapshot(source_path)
        if not ok:
            raise VaultError(snapshot.get("error", "Could not snapshot the source"))
        sent = False
        try:
            sent, err = self._send_receive(snapshot, base, target_dir)
            if not sent and base is not None:
                # The vault copy of the base may be damaged; a full send still works.
                base = None
                sent, err = self._send_receive(snapshot, None, target_dir)
            if not sent:
                raise VaultError(err)
        finally:
            if not sent:
                self.m._cleanup_temp_subvolume(snapshot["snapshot_path"], snapshot["btrfs_cmd"], attempts=3)
        self.m._keep_as_base(peer_id, source_path, snapshot)
        return {"source_path": source_path, "snapshot_name": snapshot["snapshot_name"],
                "incremental": base is not None, "encrypted": True}

    def restore_stream(self, mountpoint: str, snapshot_id: str) -> Tuple[Iterator[bytes], Callable[[], None]]:
        """`btrfs send` of a vault snapshot, as a byte stream, plus a cleanup callback."""
        folder, _, name = snapshot_id.partition("/")
        if source_from_dir(folder) is None or snapshot_time(name) is None:
            raise VaultError("Unknown snapshot")
        proc, stderr = self.m.spawn_privileged([self._btrfs(), "send", os.path.join(mountpoint, folder, name)])

        def cleanup() -> None:
            stop_process(proc)
            stderr.close()
        return self._stream_output(proc, stderr, "Reading the snapshot from the vault"), cleanup

    def delete_snapshot(self, mountpoint: str, snapshot_id: str) -> None:
        folder, _, name = snapshot_id.partition("/")
        if source_from_dir(folder) is None or snapshot_time(name) is None:
            raise VaultError("Unknown snapshot")
        self._delete(os.path.join(mountpoint, folder, name))
