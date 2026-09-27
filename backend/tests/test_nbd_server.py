"""NBD server for Buddy Backup vaults (backend/nbd_server.py).

A small hand-written client checks the protocol details; when libnbd's
`nbdsh` is installed, an independent, widely used client is run against the
server as well.
"""

import os
import shutil
import socket
import struct
import subprocess

import pytest

import nbd_server as ns

OWNER_IP = "127.0.0.1"
IMAGE_SIZE = 1024 * 1024


@pytest.fixture
def server(tmp_path):
    image = tmp_path / "owner.img"
    with open(image, "wb") as f:
        f.truncate(IMAGE_SIZE)
    readonly = tmp_path / "ro.img"
    with open(readonly, "wb") as f:
        f.truncate(IMAGE_SIZE)
    state = {"space_ok": True}

    def resolve(remote_ip, name):
        if remote_ip != OWNER_IP:
            return None
        if name == "owner":
            return ns.Export(key="owner", path=str(image), space_ok=lambda: state["space_ok"])
        if name == "ro":
            return ns.Export(key="ro", path=str(readonly), read_only=True)
        return None

    srv = ns.NbdServer(resolve, log=lambda msg: None)
    srv.listen("127.0.0.1", 0)
    yield srv, image, state
    srv.close()


class Client:
    """Just enough of an NBD client to drive the server."""

    def __init__(self, port, name="owner", use_go=True):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        magic, opt_magic, flags = struct.unpack(">8sQH", self._recv(18))
        assert magic == ns.NBDMAGIC and opt_magic == ns.IHAVEOPT
        assert flags & ns.FLAG_FIXED_NEWSTYLE
        self.sock.sendall(struct.pack(">I", ns.CLIENT_FLAG_NO_ZEROES | 1))
        self.handle = 0
        if use_go:
            self.size, self.flags = self._go(name)
        else:
            payload = name.encode()
            self.sock.sendall(struct.pack(">QII", ns.IHAVEOPT, ns.OPT_EXPORT_NAME, len(payload)) + payload)
            self.size, self.flags = struct.unpack(">QH", self._recv(10))

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def _go(self, name):
        payload = struct.pack(">I", len(name)) + name.encode() + struct.pack(">H", 0)
        self.sock.sendall(struct.pack(">QII", ns.IHAVEOPT, ns.OPT_GO, len(payload)) + payload)
        size = flags = None
        while True:
            magic, option, reply, length = struct.unpack(">QIII", self._recv(20))
            assert magic == ns.OPT_REPLY_MAGIC and option == ns.OPT_GO
            data = self._recv(length)
            if reply == ns.REP_ACK:
                return size, flags
            if reply == ns.REP_INFO and struct.unpack(">H", data[:2])[0] == ns.INFO_EXPORT:
                _, size, flags = struct.unpack(">HQH", data)
            elif reply & (1 << 31):
                raise PermissionError(reply)

    def request(self, cmd, offset=0, length=0, data=b"", flags=0):
        self.handle += 1
        self.sock.sendall(struct.pack(">IHHQQI", ns.REQUEST_MAGIC, flags, cmd, self.handle, offset,
                                      length or len(data)) + data)
        magic, error, handle = struct.unpack(">IIQ", self._recv(16))
        assert magic == ns.SIMPLE_REPLY_MAGIC and handle == self.handle
        if error or cmd != ns.CMD_READ:
            return error, b""
        return 0, self._recv(length)

    def close(self):
        try:
            self.sock.sendall(struct.pack(">IHHQQI", ns.REQUEST_MAGIC, 0, ns.CMD_DISC, 0, 0, 0))
        finally:
            self.sock.close()


@pytest.mark.parametrize("use_go", [True, False])
def test_read_write_flush(server, use_go):
    srv, image, _ = server
    client = Client(srv.address[1], use_go=use_go)
    assert client.size == IMAGE_SIZE
    assert client.flags & ns.TX_SEND_FLUSH and not client.flags & ns.TX_READ_ONLY
    block = os.urandom(8192)
    assert client.request(ns.CMD_WRITE, 4096, data=block, flags=ns.CMD_FLAG_FUA) == (0, b"")
    assert client.request(ns.CMD_FLUSH) == (0, b"")
    assert client.request(ns.CMD_READ, 4096, 8192) == (0, block)
    client.close()
    with open(image, "rb") as f:
        f.seek(4096)
        assert f.read(8192) == block


def test_requests_outside_the_image_fail(server):
    srv, _, _ = server
    client = Client(srv.address[1])
    assert client.request(ns.CMD_READ, IMAGE_SIZE - 10, 20)[0] == ns.EINVAL
    assert client.request(ns.CMD_WRITE, IMAGE_SIZE, data=b"x")[0] == ns.ENOSPC
    client.close()


def test_trim_zeroes_the_range(server):
    srv, _, _ = server
    client = Client(srv.address[1])
    client.request(ns.CMD_WRITE, 0, data=b"\xff" * 65536)
    assert client.request(ns.CMD_TRIM, 0, 65536) == (0, b"")
    _, data = client.request(ns.CMD_READ, 0, 65536)
    assert data == bytes(65536)
    client.close()


def test_read_only_export_refuses_writes(server):
    srv, _, _ = server
    client = Client(srv.address[1], name="ro")
    assert client.flags & ns.TX_READ_ONLY
    assert client.request(ns.CMD_WRITE, 0, data=b"x")[0] == ns.EPERM
    client.close()


def test_unknown_export_is_refused(server):
    srv, _, _ = server
    with pytest.raises(PermissionError):
        Client(srv.address[1], name="someone-else")


def test_writes_stop_when_the_buddy_runs_out_of_space(server):
    srv, _, state = server
    state["space_ok"] = False
    client = Client(srv.address[1])
    assert client.request(ns.CMD_WRITE, 0, data=b"x" * 512)[0] == ns.ENOSPC
    client.close()


def test_a_new_connection_replaces_a_stale_one(server):
    srv, _, _ = server
    stale = Client(srv.address[1])
    fresh = Client(srv.address[1])
    assert fresh.request(ns.CMD_READ, 0, 512)[0] == 0
    with pytest.raises((ConnectionError, OSError)):
        stale.request(ns.CMD_READ, 0, 512)
    fresh.close()


@pytest.mark.skipif(shutil.which("nbdcopy") is None or shutil.which("nbdinfo") is None,
                    reason="libnbd tools (nbdcopy, nbdinfo) not installed")
def test_libnbd_client_interoperates(server, tmp_path):
    srv, image, _ = server
    uri = f"nbd://127.0.0.1:{srv.address[1]}/owner"
    info = subprocess.run(["nbdinfo", "--json", uri], capture_output=True, text=True, timeout=30)
    assert info.returncode == 0, info.stderr
    assert f'"export-size": {IMAGE_SIZE}' in info.stdout
    assert '"can_flush": true' in info.stdout and '"can_trim": true' in info.stdout

    source = tmp_path / "source.bin"
    source.write_bytes(os.urandom(IMAGE_SIZE))
    upload = subprocess.run(["nbdcopy", "--flush", str(source), uri], capture_output=True, text=True, timeout=60)
    assert upload.returncode == 0, upload.stderr
    assert image.read_bytes() == source.read_bytes()

    back = tmp_path / "back.bin"
    download = subprocess.run(["nbdcopy", uri, str(back)], capture_output=True, text=True, timeout=60)
    assert download.returncode == 0, download.stderr
    assert back.read_bytes() == source.read_bytes()
