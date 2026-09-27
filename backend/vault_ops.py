#!/usr/bin/env python3
"""
Root side of Buddy Backup vaults, run by the alvaos-priv helper.

A vault is an image file on a buddy NAS, exported over the WireGuard tunnel by
the buddy's NBD server. This NAS attaches it as /dev/nbdN, unlocks it with
LUKS and mounts the Btrfs filesystem inside at /run/alvaos-vault/<name>.
The buddy never sees the key; it only stores encrypted blocks.

Only two fixed operations exist, so the policy does not need general rules
for cryptsetup, nbd-client or mount:

    vault-open NAME HOST EXPORT MODE    (key on stdin; MODE: rw, ro or create)
    vault-close NAME

`create` formats the device, but only if it carries no LUKS header yet, so an
existing vault is never overwritten.
"""

import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Callable, List, Optional

NAME_RE = re.compile(r"^[a-z0-9]{4,32}$")
EXPORT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
KEY_RE = re.compile(r"^[0-9a-f]{64}$")
BUDDY_SUBNET = ipaddress.ip_network("100.95.95.0/24")
NBD_PORT = "10809"
NBD_DEVICES = 16

VAULT_MOUNT_BASE = "/run/alvaos-vault"
STATE_DIR = "/run/alvaos-vault-state"
MAPPER_PREFIX = "alvaos-vault-"

# Resolved at run time, and only from system directories.
NBD_CLIENT = "nbd-client"
CRYPTSETUP = "cryptsetup"
MKFS_BTRFS = "mkfs.btrfs"
BTRFS = "btrfs"
BLKID = "blkid"
MODPROBE = "modprobe"
MOUNT = "mount"
UMOUNT = "umount"
SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"

MOUNT_OPTIONS = {
    "rw": "noatime,compress=zstd:3,discard=async,nosuid,nodev,noexec",
    "ro": "ro,noatime,nosuid,nodev,noexec",
}


class VaultError(Exception):
    pass


Runner = Callable[..., subprocess.CompletedProcess]


def _default_run(argv: List[str], input: Optional[bytes] = None) -> subprocess.CompletedProcess:
    binary = shutil.which(argv[0], path=SYSTEM_PATH)
    if binary is None:
        raise VaultError(f"{argv[0]} is not installed")
    return subprocess.run([binary] + argv[1:], input=input, capture_output=True, timeout=300,
                          env={"PATH": SYSTEM_PATH, "LC_ALL": "C"})


class Host:
    """Filesystem and process access; replaced in tests."""

    def __init__(self, run: Runner = _default_run):
        self.run = run

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def read(self, path: str) -> str:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read()

    def write(self, path: str, text: str) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(text)

    def remove(self, path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def rmdir(self, path: str) -> None:
        try:
            os.rmdir(path)
        except OSError:
            pass

    def makedirs(self, path: str) -> None:
        os.makedirs(path, mode=0o700, exist_ok=True)
        if os.path.islink(path) or os.stat(path).st_uid != 0:
            raise VaultError(f"{path} is not a root-owned directory")
        os.chmod(path, 0o700)

    def is_mounted(self, path: str) -> bool:
        with open("/proc/self/mounts", encoding="utf-8", errors="replace") as f:
            return any(line.split()[1] == path for line in f if len(line.split()) > 1)


def validate(name: str, host: str = "0.0.0.0", export: str = "x", key: str = "0" * 64) -> None:
    if not NAME_RE.match(name or ""):
        raise VaultError("Invalid vault name")
    try:
        if ipaddress.ip_address(host) not in BUDDY_SUBNET and host != "0.0.0.0":
            raise VaultError("Vaults can only be attached from a buddy tunnel address")
    except ValueError:
        raise VaultError("Invalid buddy address") from None
    if not EXPORT_RE.match(export or ""):
        raise VaultError("Invalid vault export name")
    if not KEY_RE.match(key or ""):
        raise VaultError("Invalid vault key")


def paths(name: str):
    return (os.path.join(VAULT_MOUNT_BASE, name), f"/dev/mapper/{MAPPER_PREFIX}{name}",
            os.path.join(STATE_DIR, f"{name}.dev"))


def _check(result: subprocess.CompletedProcess, what: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
        raise VaultError(f"{what} failed: {detail or f'exit code {result.returncode}'}")


def _free_nbd_device(host: Host) -> str:
    if not host.exists("/sys/block/nbd0"):
        _check(host.run([MODPROBE, "nbd", f"nbds_max={NBD_DEVICES}"]), "Loading the nbd module")
    for i in range(NBD_DEVICES):
        sys_dir = f"/sys/block/nbd{i}"
        if not host.exists(sys_dir) or host.exists(f"{sys_dir}/pid"):
            continue
        try:
            if host.read(f"{sys_dir}/size").strip() not in ("", "0"):
                continue
        except OSError:
            continue
        return f"/dev/nbd{i}"
    raise VaultError("No free network block device")


def close_vault(name: str, host: Host) -> None:
    """Unmount, lock and detach. Safe to call when nothing is open."""
    validate(name)
    mountpoint, mapper, dev_file = paths(name)
    errors = []
    if host.is_mounted(mountpoint):
        if host.run([UMOUNT, mountpoint]).returncode != 0:
            # A hung network device must not keep the vault open forever.
            host.run([UMOUNT, "-l", mountpoint])
    host.rmdir(mountpoint)
    if host.exists(mapper):
        result = host.run([CRYPTSETUP, "close", MAPPER_PREFIX + name])
        if result.returncode != 0:
            errors.append("cryptsetup close: " + result.stderr.decode("utf-8", "replace").strip())
    if host.exists(dev_file):
        device = host.read(dev_file).strip()
        if re.match(r"^/dev/nbd\d{1,2}$", device):
            host.run([NBD_CLIENT, "-d", device])
        host.remove(dev_file)
    if errors:
        raise VaultError("; ".join(errors))


def open_vault(name: str, remote: str, export: str, mode: str, key: str, host: Host) -> dict:
    """Attach, unlock and mount a vault. Returns where it is mounted."""
    validate(name, remote, export, key)
    if mode not in ("rw", "ro", "create"):
        raise VaultError("Invalid vault mode")
    close_vault(name, host)
    mountpoint, mapper, dev_file = paths(name)
    host.makedirs(STATE_DIR)
    key_bytes = key.encode("ascii")
    formatted = False
    try:
        device = _free_nbd_device(host)
        _check(host.run([NBD_CLIENT, remote, NBD_PORT, device, "-N", export, "-b", "4096", "-t", "120"]),
               "Connecting to the buddy vault")
        host.write(dev_file, device)

        if host.run([CRYPTSETUP, "isLuks", device]).returncode != 0:
            if mode != "create":
                raise VaultError("The buddy vault is not initialised")
            _check(host.run([CRYPTSETUP, "luksFormat", "--type", "luks2", "--batch-mode",
                             "--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000",
                             "--label", "alvaos-vault", "--key-file", "-", device], input=key_bytes),
                   "Encrypting the buddy vault")
            formatted = True

        unlock = [CRYPTSETUP, "open", "--type", "luks2", "--key-file", "-"]
        unlock += ["--readonly"] if mode == "ro" else ["--allow-discards"]
        _check(host.run(unlock + [device, MAPPER_PREFIX + name], input=key_bytes), "Unlocking the buddy vault")

        fs_type = host.run([BLKID, "-p", "-o", "value", "-s", "TYPE", mapper]).stdout.decode().strip()
        if fs_type != "btrfs":
            if fs_type or mode != "create":
                raise VaultError("The buddy vault does not contain a Btrfs filesystem")
            _check(host.run([MKFS_BTRFS, "-K", "-L", "alvaos-vault", mapper]), "Creating the vault filesystem")
            formatted = True

        host.makedirs(VAULT_MOUNT_BASE)
        host.makedirs(mountpoint)
        options = MOUNT_OPTIONS["ro" if mode == "ro" else "rw"]
        _check(host.run([MOUNT, "-t", "btrfs", "-o", options, mapper, mountpoint]), "Mounting the buddy vault")
        if mode != "ro":
            # The buddy may have grown the image since the last sync.
            host.run([BTRFS, "filesystem", "resize", "max", mountpoint])
    except Exception:
        try:
            close_vault(name, host)
        except VaultError:
            pass
        raise
    return {"mountpoint": mountpoint, "device": device, "formatted": formatted}


def main(argv: List[str], stdin: bytes, host: Optional[Host] = None) -> int:
    host = host or Host()
    try:
        if argv[:1] == ["vault-close"] and len(argv) == 2:
            close_vault(argv[1], host)
            print(json.dumps({"closed": argv[1]}))
            return 0
        if argv[:1] == ["vault-open"] and len(argv) == 5:
            key = stdin.decode("ascii", "replace").strip()
            print(json.dumps(open_vault(argv[1], argv[2], argv[3], argv[4], key, host)))
            return 0
        raise VaultError("usage: vault-open NAME HOST EXPORT MODE | vault-close NAME")
    except VaultError as exc:
        print(f"alvaos-priv: {exc}", file=sys.stderr)
        return 1
