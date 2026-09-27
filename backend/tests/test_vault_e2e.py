"""End-to-end check of a Buddy Backup vault on a real kernel.

Runs only as root with ALVAOS_E2E=1 (the CI "vault-e2e" job): it needs the nbd
kernel module, cryptsetup, btrfs-progs and loop devices. It exercises exactly
the pieces the unit tests simulate:

    NBD server (buddy) → nbd-client → LUKS → Btrfs   (vault_ops.open_vault)
    btrfs send [-p] | btrfs receive into the vault    (replication)
    deleting an old snapshot does not break the next delta
    the buddy's image file contains no plaintext
    restore: btrfs send from the vault | btrfs receive locally
"""

import os
import secrets
import subprocess

import pytest

import nbd_server
import vault_ops

pytestmark = pytest.mark.skipif(
    os.environ.get("ALVAOS_E2E") != "1" or os.geteuid() != 0,
    reason="needs root and ALVAOS_E2E=1 (runs in the vault-e2e CI job)",
)

BUDDY_IP = "100.95.95.8"
MARKER = b"ALVAOS-PLAINTEXT-MARKER-" + secrets.token_hex(8).encode()


def sh(*argv, input=None):
    result = subprocess.run(argv, input=input, capture_output=True)
    assert result.returncode == 0, f"{argv}: {result.stderr.decode()}"
    return result.stdout.decode()


def pipe(send_argv, receive_argv):
    send = subprocess.Popen(send_argv, stdout=subprocess.PIPE)
    receive = subprocess.run(receive_argv, stdin=send.stdout, capture_output=True)
    send.stdout.close()
    assert send.wait() == 0 and receive.returncode == 0, receive.stderr.decode()


@pytest.fixture
def pool(tmp_path):
    """A small local Btrfs pool with a data subvolume, on a loop device."""
    image = tmp_path / "pool.img"
    with open(image, "wb") as f:
        f.truncate(512 * 1024 * 1024)
    loop = sh("losetup", "--find", "--show", str(image)).strip()
    mnt = tmp_path / "pool"
    mnt.mkdir()
    sh("mkfs.btrfs", "-q", loop)
    sh("mount", loop, str(mnt))
    sh("btrfs", "subvolume", "create", str(mnt / "media"))
    yield mnt
    subprocess.run(["umount", str(mnt)])
    subprocess.run(["losetup", "-d", loop])


@pytest.fixture
def buddy(tmp_path):
    """The buddy's side: a sparse image served by our NBD server on a tunnel address."""
    subprocess.run(["ip", "addr", "add", f"{BUDDY_IP}/32", "dev", "lo"], capture_output=True)
    subprocess.run(["modprobe", "nbd", "nbds_max=16", "max_part=0"], capture_output=True)
    image = tmp_path / "vault.img"
    with open(image, "wb") as f:
        f.truncate(1024 * 1024 * 1024)
    server = nbd_server.NbdServer(
        lambda ip, name: nbd_server.Export(key=name, path=str(image)) if name == "owner-node" else None)
    server.listen(BUDDY_IP, nbd_server.NBD_PORT)
    yield image
    server.close()


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def test_vault_replication_on_a_real_kernel(pool, buddy, tmp_path):
    key = secrets.token_hex(32)
    host = vault_ops.Host()
    name = "e2etest" + secrets.token_hex(2)

    def snapshot(n):
        path = pool / f".alvaos-buddy-media-2026010{n}-020000-000{n}"
        sh("btrfs", "subvolume", "snapshot", "-r", str(pool / "media"), str(path))
        return path

    write(pool / "media" / "a.txt", MARKER * 1000)
    write(pool / "media" / "big.bin", os.urandom(20 * 1024 * 1024))
    s1 = snapshot(1)

    result = vault_ops.open_vault(name, BUDDY_IP, "owner-node", "create", key, host)
    assert result["formatted"]
    target = os.path.join(result["mountpoint"], "src-bWVkaWE")
    try:
        os.makedirs(target)
        pipe(["btrfs", "send", str(s1)], ["btrfs", "receive", target])

        write(pool / "media" / "b.txt", MARKER + b" second")
        s2 = snapshot(2)
        pipe(["btrfs", "send", "-p", str(s1), str(s2)], ["btrfs", "receive", target])

        # Deleting the oldest snapshot in the vault must not break the next delta.
        sh("btrfs", "subvolume", "delete", os.path.join(target, s1.name))
        os.remove(pool / "media" / "a.txt")
        s3 = snapshot(3)
        pipe(["btrfs", "send", "-p", str(s2), str(s3)], ["btrfs", "receive", target])
    finally:
        vault_ops.close_vault(name, host)

    # Reopen read-only, as a restore would, and check the content.
    result = vault_ops.open_vault(name, BUDDY_IP, "owner-node", "ro", key, host)
    try:
        latest = os.path.join(result["mountpoint"], "src-bWVkaWE", s3.name)
        assert sorted(os.listdir(latest)) == ["b.txt", "big.bin"]
        with open(os.path.join(latest, "b.txt"), "rb") as f:
            assert f.read() == MARKER + b" second"
        assert not os.path.exists(os.path.join(result["mountpoint"], "src-bWVkaWE", s1.name))

        restored_parent = pool / "restored"
        restored_parent.mkdir()
        pipe(["btrfs", "send", latest], ["btrfs", "receive", str(restored_parent)])
        with open(restored_parent / s3.name / "big.bin", "rb") as a, open(pool / "media" / "big.bin", "rb") as b:
            assert a.read() == b.read()
    finally:
        vault_ops.close_vault(name, host)

    # A second "create" must not reformat an existing vault.
    result = vault_ops.open_vault(name, BUDDY_IP, "owner-node", "create", key, host)
    assert not result["formatted"]
    vault_ops.close_vault(name, host)

    # A wrong key does not open it.
    with pytest.raises(vault_ops.VaultError):
        vault_ops.open_vault(name, BUDDY_IP, "owner-node", "ro", secrets.token_hex(32), host)

    # The buddy only ever stored ciphertext.
    with open(buddy, "rb") as f:
        while chunk := f.read(16 * 1024 * 1024):
            assert MARKER not in chunk
