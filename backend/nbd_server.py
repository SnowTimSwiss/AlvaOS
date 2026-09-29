#!/usr/bin/env python3
"""
Minimal NBD server for Buddy Backup vaults.

A buddy keeps one image file per NAS it backs up. The owning NAS attaches the
image over the WireGuard tunnel as a network block device, unlocks it with a
key only the owner has (LUKS), and replicates Btrfs snapshots into it. This
side therefore only ever sees encrypted blocks.

Only what the Linux kernel client (nbd-client) and libnbd need is implemented:
the fixed-newstyle handshake with NBD_OPT_GO / INFO / EXPORT_NAME, and the
READ, WRITE, FLUSH, TRIM and DISC commands with simple replies. The spec is at
https://github.com/NetworkBlockDevice/nbd/blob/master/doc/proto.md

Who may open which export is decided by the `resolve` callback, from the
client's tunnel address and the requested export name. The server runs as the
unprivileged backend user and only touches the image files it is handed.
"""

import ctypes
import ctypes.util
import os
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

NBD_PORT = 10809

NBDMAGIC = b"NBDMAGIC"
IHAVEOPT = 0x49484156454F5054
OPT_REPLY_MAGIC = 0x3E889045565A9
REQUEST_MAGIC = 0x25609513
SIMPLE_REPLY_MAGIC = 0x67446698

FLAG_FIXED_NEWSTYLE = 1 << 0
FLAG_NO_ZEROES = 1 << 1
CLIENT_FLAG_NO_ZEROES = 1 << 1

OPT_EXPORT_NAME = 1
OPT_ABORT = 2
OPT_INFO = 6
OPT_GO = 7

REP_ACK = 1
REP_INFO = 3
REP_ERR_UNSUP = (1 << 31) + 1
REP_ERR_POLICY = (1 << 31) + 2
REP_ERR_INVALID = (1 << 31) + 3
REP_ERR_UNKNOWN = (1 << 31) + 6

INFO_EXPORT = 0
INFO_BLOCK_SIZE = 3

TX_HAS_FLAGS = 1 << 0
TX_READ_ONLY = 1 << 1
TX_SEND_FLUSH = 1 << 2
TX_SEND_FUA = 1 << 3
TX_SEND_TRIM = 1 << 5

CMD_READ = 0
CMD_WRITE = 1
CMD_DISC = 2
CMD_FLUSH = 3
CMD_TRIM = 4
CMD_FLAG_FUA = 1 << 0

EPERM = 1
EIO = 5
EINVAL = 22
ENOSPC = 28

MAX_REQUEST = 32 * 1024 * 1024
MAX_OPTION = 64 * 1024
BLOCK_SIZE = 4096

_REQUEST = struct.Struct(">IHHQQI")
_REPLY = struct.Struct(">IIQ")


@dataclass
class Export:
    """An image a client may open. `key` identifies it for the one-client rule."""
    key: str
    path: str
    read_only: bool = False
    # Returns False when the buddy's own pool is too full to accept writes.
    space_ok: Callable[[], bool] = lambda: True


def _punch_hole(fd: int, offset: int, length: int) -> None:
    """Give trimmed ranges back to the buddy's filesystem (sparse image)."""
    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        return
    libc = ctypes.CDLL(libc_name, use_errno=True)
    FALLOC_FL_KEEP_SIZE, FALLOC_FL_PUNCH_HOLE = 0x01, 0x02
    libc.fallocate(ctypes.c_int(fd), ctypes.c_int(FALLOC_FL_KEEP_SIZE | FALLOC_FL_PUNCH_HOLE),
                   ctypes.c_longlong(offset), ctypes.c_longlong(length))


class _Closed(Exception):
    pass


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1024 * 1024))
        if not chunk:
            raise _Closed()
        buf += chunk
    return bytes(buf)


class NbdServer:
    def __init__(self, resolve: Callable[[str, str], Optional[Export]], log: Callable[[str], None] = print):
        self.resolve = resolve
        self.log = log
        self._sock: Optional[socket.socket] = None
        self._address = ("", 0)
        self._stop = threading.Event()
        # One client per image: a new connection from the owner replaces an
        # old one (left over after a crash or a dropped link).
        self._active: Dict[str, socket.socket] = {}
        self._active_lock = threading.Lock()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @property
    def address(self):
        return self._address

    def listen(self, host: str, port: int = NBD_PORT) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(8)
        sock.settimeout(1.0)
        self._sock = sock
        self._address = sock.getsockname()
        self._stop.clear()
        threading.Thread(target=self._accept_loop, name="nbd-accept", daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        with self._active_lock:
            for conn in self._active.values():
                self._shutdown(conn)
            self._active.clear()

    @staticmethod
    def _shutdown(conn: socket.socket) -> None:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn, addr[0]), name="nbd-conn", daemon=True).start()

    # ── Handshake ────────────────────────────────────────────────────────────

    def _serve(self, conn: socket.socket, remote_ip: str) -> None:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(600)
        export = None
        try:
            export = self._handshake(conn, remote_ip)
            if export is None:
                return
            self._transmit(conn, export)
        except (_Closed, OSError, struct.error):
            pass
        except Exception as exc:  # never let one client take the server down
            self.log(f"NBD: connection from {remote_ip} failed: {exc}")
        finally:
            if export is not None:
                with self._active_lock:
                    if self._active.get(export.key) is conn:
                        del self._active[export.key]
            try:
                conn.close()
            except OSError:
                pass

    def _option_reply(self, conn: socket.socket, option: int, reply: int, data: bytes = b"") -> None:
        conn.sendall(struct.pack(">QIII", OPT_REPLY_MAGIC, option, reply, len(data)) + data)

    def _handshake(self, conn: socket.socket, remote_ip: str) -> Optional[Export]:
        conn.sendall(NBDMAGIC + struct.pack(">QH", IHAVEOPT, FLAG_FIXED_NEWSTYLE | FLAG_NO_ZEROES))
        (client_flags,) = struct.unpack(">I", _recv_exact(conn, 4))
        no_zeroes = bool(client_flags & CLIENT_FLAG_NO_ZEROES)
        while True:
            magic, option, length = struct.unpack(">QII", _recv_exact(conn, 16))
            if magic != IHAVEOPT or length > MAX_OPTION:
                return None
            data = _recv_exact(conn, length)

            if option == OPT_EXPORT_NAME:
                export = self._open(remote_ip, data.decode("utf-8", "replace"))
                if export is None:
                    return None  # this option has no error reply; hang up
                size = os.path.getsize(export.path)
                reply = struct.pack(">QH", size, self._tx_flags(export))
                conn.sendall(reply if no_zeroes else reply + bytes(124))
                return self._claim(export, conn)

            if option in (OPT_INFO, OPT_GO):
                if length < 6:
                    self._option_reply(conn, option, REP_ERR_INVALID)
                    continue
                (name_len,) = struct.unpack(">I", data[:4])
                if 4 + name_len + 2 > length:
                    self._option_reply(conn, option, REP_ERR_INVALID)
                    continue
                name = data[4:4 + name_len].decode("utf-8", "replace")
                export = self._open(remote_ip, name)
                if export is None:
                    self._option_reply(conn, option, REP_ERR_UNKNOWN)
                    continue
                size = os.path.getsize(export.path)
                self._option_reply(conn, option, REP_INFO,
                                   struct.pack(">HQH", INFO_EXPORT, size, self._tx_flags(export)))
                self._option_reply(conn, option, REP_INFO,
                                   struct.pack(">HIII", INFO_BLOCK_SIZE, 1, BLOCK_SIZE, MAX_REQUEST))
                self._option_reply(conn, option, REP_ACK)
                if option == OPT_GO:
                    return self._claim(export, conn)
                continue

            if option == OPT_ABORT:
                self._option_reply(conn, option, REP_ACK)
                return None

            self._option_reply(conn, option, REP_ERR_UNSUP)

    def _open(self, remote_ip: str, name: str) -> Optional[Export]:
        try:
            export = self.resolve(remote_ip, name)
        except Exception as exc:
            self.log(f"NBD: could not resolve export {name!r} for {remote_ip}: {exc}")
            return None
        if export is None:
            self.log(f"NBD: refused export {name!r} for {remote_ip}")
        return export

    def _claim(self, export: Export, conn: socket.socket) -> Export:
        with self._active_lock:
            old = self._active.get(export.key)
            self._active[export.key] = conn
        if old is not None and old is not conn:
            self._shutdown(old)
        return export

    @staticmethod
    def _tx_flags(export: Export) -> int:
        flags = TX_HAS_FLAGS | TX_SEND_FLUSH | TX_SEND_FUA
        if export.read_only:
            flags |= TX_READ_ONLY
        else:
            flags |= TX_SEND_TRIM
        return flags

    # ── Transmission ─────────────────────────────────────────────────────────

    def _transmit(self, conn: socket.socket, export: Export) -> None:
        mode = os.O_RDONLY if export.read_only else os.O_RDWR
        fd = os.open(export.path, mode | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            size = os.fstat(fd).st_size
            last_space_check = 0.0
            space_ok = True
            while True:
                magic, flags, cmd, handle, offset, length = _REQUEST.unpack(_recv_exact(conn, _REQUEST.size))
                if magic != REQUEST_MAGIC:
                    return
                if cmd == CMD_DISC:
                    return
                if cmd == CMD_WRITE:
                    if length > MAX_REQUEST:
                        return  # cannot skip that much data safely; drop the client
                    payload = _recv_exact(conn, length)

                error = 0
                data = b""
                in_bounds = offset + length <= size
                if cmd == CMD_READ:
                    if length > MAX_REQUEST or not in_bounds:
                        error = EINVAL
                    else:
                        data = os.pread(fd, length, offset)
                        if len(data) < length:
                            data += bytes(length - len(data))
                elif cmd in (CMD_WRITE, CMD_TRIM):
                    if export.read_only:
                        error = EPERM
                    elif not in_bounds:
                        error = ENOSPC if cmd == CMD_WRITE else EINVAL
                    else:
                        now = time.monotonic()
                        if cmd == CMD_WRITE and now - last_space_check > 5:
                            space_ok, last_space_check = export.space_ok(), now
                        if cmd == CMD_WRITE and not space_ok:
                            error = ENOSPC
                        elif cmd == CMD_WRITE:
                            error = self._write(fd, offset, payload, bool(flags & CMD_FLAG_FUA))
                        else:
                            _punch_hole(fd, offset, length)
                elif cmd == CMD_FLUSH:
                    try:
                        os.fsync(fd)
                    except OSError:
                        error = EIO
                else:
                    error = EINVAL

                conn.sendall(_REPLY.pack(SIMPLE_REPLY_MAGIC, error, handle) + (data if not error else b""))
        finally:
            os.close(fd)

    @staticmethod
    def _write(fd: int, offset: int, payload: bytes, fua: bool) -> int:
        try:
            view = memoryview(payload)
            while view:
                written = os.pwrite(fd, view, offset)
                view = view[written:]
                offset += written
            if fua:
                os.fdatasync(fd)
            return 0
        except OSError as exc:
            return ENOSPC if exc.errno == ENOSPC else EIO
