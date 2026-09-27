"""Root side of Buddy Backup vaults (backend/vault_ops.py) against a simulated system."""

import subprocess

import pytest

import priv_policy as policy
import vault_ops as vo

KEY = "ab" * 32
BUDDY = "100.95.95.8"


class FakeHost(vo.Host):
    """Records commands and simulates nbd, LUKS, blkid and mounts."""

    def __init__(self, luks=True, fs="btrfs", busy_devices=()):
        super().__init__(run=self._run)
        self.commands = []
        self.inputs = []
        self.luks = luks
        self.fs = fs
        self.files = {f"/sys/block/nbd{i}": "" for i in range(4)}
        for i in range(4):
            self.files[f"/sys/block/nbd{i}/size"] = "0"
        for i in busy_devices:
            self.files[f"/sys/block/nbd{i}/pid"] = "123"
        self.mounted = set()
        self.dirs = set()
        self.fail = {}
        self.stuck = set()

    def _run(self, argv, input=None):
        self.commands.append(argv)
        self.inputs.append(input)
        name = argv[0]
        rc, out = 0, b""
        if name in self.fail and self.fail[name](argv):
            rc = 1
        elif name == "cryptsetup" and argv[1] == "isLuks":
            rc = 0 if self.luks else 1
        elif name == "cryptsetup" and argv[1] == "luksFormat":
            self.luks = True
        elif name == "cryptsetup" and argv[1] == "open":
            self.files["/dev/mapper/" + argv[-1]] = ""
        elif name == "cryptsetup" and argv[1] == "close":
            self.files.pop("/dev/mapper/" + argv[2], None)
        elif name == "nbd-client" and argv[1] != "-d":
            device = argv[3]
            if device in self.stuck:
                rc = 1
                return subprocess.CompletedProcess(argv, rc, b"", b"Error: Failed to setup device, check dmesg")
            self.files[f"/sys/block/{device[5:]}/pid"] = "1"
        elif name == "nbd-client" and argv[1] == "-d":
            self.files.pop(f"/sys/block/{argv[2][5:]}/pid", None)
        elif name == "blkid":
            out = self.fs.encode()
        elif name == "mkfs.btrfs":
            self.fs = "btrfs"
        elif name == "mount":
            self.mounted.add(argv[-1])
        elif name == "umount":
            self.mounted.discard(argv[-1])
        return subprocess.CompletedProcess(argv, rc, out, b"simulated failure" if rc else b"")

    def exists(self, path):
        return path in self.files or path in self.dirs

    def read(self, path):
        return self.files[path]

    def write(self, path, text):
        self.files[path] = text

    def remove(self, path):
        self.files.pop(path, None)

    def rmdir(self, path):
        self.dirs.discard(path)

    def makedirs(self, path):
        self.dirs.add(path)

    def is_mounted(self, path):
        return path in self.mounted

    def sleep(self, seconds):
        pass

    def ran(self, *prefix):
        return [c for c in self.commands if c[:len(prefix)] == list(prefix)]


def test_open_attaches_unlocks_and_mounts():
    host = FakeHost(busy_devices=[0])
    result = vo.open_vault("papa1234", BUDDY, "node-a", "rw", KEY, host)
    assert result == {"mountpoint": "/run/alvaos-vault/papa1234", "device": "/dev/nbd1", "formatted": False}
    assert host.ran("nbd-client", BUDDY, "10809", "/dev/nbd1", "-N", "node-a")
    assert not host.ran("cryptsetup", "luksFormat") and not host.ran("mkfs.btrfs")
    unlock = host.ran("cryptsetup", "open")[0]
    assert "--allow-discards" in unlock and unlock[-2:] == ["/dev/nbd1", "alvaos-vault-papa1234"]
    # The key only ever travels on stdin.
    assert KEY not in " ".join(" ".join(c) for c in host.commands)
    assert host.inputs[host.commands.index(unlock)] == KEY.encode()
    assert "/run/alvaos-vault/papa1234" in host.mounted
    assert host.ran("btrfs", "filesystem", "resize", "max")


def test_create_formats_only_an_empty_device():
    host = FakeHost(luks=False, fs="")
    result = vo.open_vault("papa1234", BUDDY, "node-a", "create", KEY, host)
    assert result["formatted"]
    assert host.ran("cryptsetup", "luksFormat") and host.ran("mkfs.btrfs", "-K", "-L", "alvaos-vault")

    existing = FakeHost(luks=True, fs="btrfs")
    vo.open_vault("papa1234", BUDDY, "node-a", "create", KEY, existing)
    assert not existing.ran("cryptsetup", "luksFormat") and not existing.ran("mkfs.btrfs")


def test_an_uninitialised_vault_is_not_formatted_without_create():
    host = FakeHost(luks=False, fs="")
    with pytest.raises(vo.VaultError, match="not initialised"):
        vo.open_vault("papa1234", BUDDY, "node-a", "rw", KEY, host)
    assert not host.ran("cryptsetup", "luksFormat")
    assert host.ran("nbd-client", "-d", "/dev/nbd0")   # detached again


def test_foreign_filesystem_is_never_reformatted():
    host = FakeHost(luks=True, fs="ext4")
    with pytest.raises(vo.VaultError, match="does not contain a Btrfs"):
        vo.open_vault("papa1234", BUDDY, "node-a", "create", KEY, host)
    assert not host.ran("mkfs.btrfs")


def test_read_only_open():
    host = FakeHost()
    vo.open_vault("papa1234", BUDDY, "node-a", "ro", KEY, host)
    assert "--readonly" in host.ran("cryptsetup", "open")[0]
    assert host.ran("mount")[0][4].startswith("ro,")
    assert not host.ran("btrfs", "filesystem", "resize", "max")


def test_wrong_key_leaves_nothing_attached():
    host = FakeHost()
    host.fail["cryptsetup"] = lambda argv: argv[1] == "open"
    with pytest.raises(vo.VaultError, match="Unlocking"):
        vo.open_vault("papa1234", BUDDY, "node-a", "rw", KEY, host)
    assert host.ran("nbd-client", "-d", "/dev/nbd0")
    assert not host.mounted


def test_close_undoes_everything():
    host = FakeHost()
    vo.open_vault("papa1234", BUDDY, "node-a", "rw", KEY, host)
    vo.close_vault("papa1234", host)
    assert not host.mounted
    assert host.ran("cryptsetup", "close", "alvaos-vault-papa1234")
    assert host.ran("nbd-client", "-d", "/dev/nbd0")
    assert "/run/alvaos-vault-state/papa1234.dev" not in host.files


@pytest.mark.parametrize("name,remote,export,key", [
    ("../etc", BUDDY, "node-a", KEY),
    ("papa1234", "203.0.113.5", "node-a", KEY),       # not a tunnel address
    ("papa1234", "not-an-ip", "node-a", KEY),
    ("papa1234", BUDDY, "node a; reboot", KEY),
    ("papa1234", BUDDY, "node-a", "short"),
])
def test_bad_arguments_are_rejected(name, remote, export, key):
    host = FakeHost()
    with pytest.raises(vo.VaultError):
        vo.open_vault(name, remote, export, "rw", key, host)
    assert host.commands == []


def test_helper_entry_point():
    host = FakeHost()
    assert vo.main(["vault-open", "papa1234", BUDDY, "node-a", "rw"], KEY.encode() + b"\n", host) == 0
    assert vo.main(["vault-close", "papa1234"], b"", host) == 0
    assert vo.main(["vault-open", "papa1234"], b"", host) == 1


def test_policy_allows_btrfs_inside_an_open_vault():
    for argv in (
        ["/usr/bin/btrfs", "receive", "/run/alvaos-vault/papa1234/src-bWVkaWE"],
        ["/usr/bin/btrfs", "subvolume", "delete", "/run/alvaos-vault/papa1234/src-bWVkaWE/.alvaos-buddy-x"],
        ["/usr/bin/btrfs", "send", "/run/alvaos-vault/papa1234/src-bWVkaWE/.alvaos-buddy-x"],
        ["/usr/bin/btrfs", "filesystem", "usage", "-b", "/run/alvaos-vault/papa1234"],
        ["/usr/bin/btrfs", "subvolume", "sync", "/run/alvaos-vault/papa1234"],
        ["/usr/bin/btrfs", "subvolume", "list", "/run/alvaos-vault/papa1234"],
        ["/usr/bin/mkdir", "-p", "/run/alvaos-vault/papa1234/src-bWVkaWE"],
    ):
        policy.validate(argv)
    with pytest.raises(policy.PolicyError):
        policy.validate(["/usr/bin/btrfs", "receive", "/run/other"])


def test_a_device_still_being_released_is_skipped():
    host = FakeHost()
    host.stuck.add("/dev/nbd0")
    result = vo.open_vault("papa1234", BUDDY, "node-a", "rw", KEY, host)
    assert result["device"] == "/dev/nbd1"


def test_reopening_after_close_works():
    host = FakeHost()
    first = vo.open_vault("papa1234", BUDDY, "node-a", "rw", KEY, host)["device"]
    vo.close_vault("papa1234", host)
    assert vo.open_vault("papa1234", BUDDY, "node-a", "ro", KEY, host)["device"] == first
