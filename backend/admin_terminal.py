#!/usr/bin/env python3
"""The admin terminal in the browser (Settings › Terminal), see
docs/ADMIN-TERMINAL.md.

A real shell, as the AlvaOS service account `alvaos` (the account the web
pages run as), never as root: what needs root goes through the privilege
helper as everywhere else. The admin page asks the backend for a one-time
ticket (the admin is signed in there), the browser opens a WebSocket here
with it, and this starts one shell in a terminal (PTY) for that connection.

Limits: a ticket works once and for one minute; at most MAX_SESSIONS shells
at once; a shell ends after IDLE_SECONDS without typing, when the browser
goes away, or when the admin's sign-in ends (signed out, password changed,
expired). Input frames are small, output is sent in bounded pieces, and
nothing typed or shown is written to a log.

Plain HTTP on 8086 and HTTPS on 9446 (the NAS's own certificate), like the
screens of virtual machines (vm_console.py).
"""

import asyncio
import base64
import fcntl
import hashlib
import json
import os
import pwd
import secrets
import signal
import ssl
import struct
import subprocess
import termios
import threading
import time
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

PORT = 8086
TLS_PORT = 9446
TICKET_SECONDS = 60
HEAD_LIMIT = 16 * 1024
MAX_SESSIONS = 3
IDLE_SECONDS = 30 * 60
CHECK_SECONDS = 20          # how often a shell asks whether its admin is still signed in
MAX_FRAME = 64 * 1024       # what the browser may send in one message
READ_CHUNK = 32 * 1024      # what the shell's output is sent in
SHELL = '/bin/bash'
GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

_tickets: Dict[str, Tuple[str, float]] = {}
_lock = threading.Lock()
_open = 0
# Answers whether the sign-in a ticket was made for is still valid
# (alvaos-backend.py hands over auth_manager's check).
def _never(token: str) -> bool:
    return False


_still_signed_in: Callable[[str], bool] = _never


def setup(still_signed_in: Callable[[str], bool]) -> None:
    global _still_signed_in
    _still_signed_in = still_signed_in


def issue(session_token: str) -> str:
    """A one-time ticket for one shell, tied to the admin's sign-in."""
    ticket = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        for key in [k for k, v in _tickets.items() if v[1] < now]:
            del _tickets[key]
        _tickets[ticket] = (session_token, now + TICKET_SECONDS)
    return ticket


def redeem(ticket: str) -> Optional[str]:
    with _lock:
        found = _tickets.pop(ticket, None)
    if not found or found[1] < time.monotonic():
        return None
    return found[0]


def _refuse(status: str) -> bytes:
    return f'HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode()


def check_request(head: bytes) -> Tuple[Optional[Tuple[str, str]], str]:
    """((sign-in token, websocket key), '') for a good request, else (None, status line)."""
    try:
        lines = head.decode('latin-1').split('\r\n')
        method, target, _version = lines[0].split(' ', 2)
        headers = {}
        for line in lines[1:]:
            if ':' in line:
                key, _, value = line.partition(':')
                headers[key.strip().lower()] = value.strip()
    except ValueError:
        return None, '400 Bad Request'
    key = headers.get('sec-websocket-key', '')
    if method != 'GET' or 'websocket' not in headers.get('upgrade', '').lower() or not key \
            or headers.get('sec-websocket-version') != '13':
        return None, '400 Bad Request'
    # Browsers always send the page's origin: only a page of this NAS may open it.
    origin = urlsplit(headers.get('origin', '')).hostname
    host = urlsplit('//' + headers.get('host', '')).hostname
    if not origin or origin != host:
        return None, '403 Forbidden'
    token = redeem((parse_qs(urlsplit(target).query).get('ticket') or [''])[0])
    if not token or not _still_signed_in(token):
        return None, '403 Forbidden'
    return (token, key), ''


def accept_header(key: str) -> bytes:
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    return (f'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
            f'Sec-WebSocket-Accept: {accept}\r\n\r\n').encode()


# ── WebSocket frames (RFC 6455), only what a browser terminal needs ──────────

def frame(opcode: int, payload: bytes) -> bytes:
    n = len(payload)
    if n < 126:
        head = struct.pack('!BB', 0x80 | opcode, n)
    elif n < 65536:
        head = struct.pack('!BBH', 0x80 | opcode, 126, n)
    else:
        head = struct.pack('!BBQ', 0x80 | opcode, 127, n)
    return head + payload


class Closed(Exception):
    pass


async def read_message(reader: asyncio.StreamReader) -> Tuple[int, bytes]:
    """(opcode, payload) of one whole message; pings are (9, ...)."""
    opcode, parts, size = None, [], 0
    while True:
        b1, b2 = await reader.readexactly(2)
        fin, op, masked, n = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
        if not masked:
            raise Closed('unmasked frame')        # browsers always mask
        if n == 126:
            n = struct.unpack('!H', await reader.readexactly(2))[0]
        elif n == 127:
            n = struct.unpack('!Q', await reader.readexactly(8))[0]
        size += n
        if size > MAX_FRAME:
            raise Closed('message too large')
        mask = await reader.readexactly(4)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(await reader.readexactly(n)))
        if op >= 8:                               # control frames stand alone
            return op, data
        if opcode is None:
            opcode = op
        parts.append(data)
        if fin:
            return opcode, b''.join(parts)


# ── The shell ───────────────────────────────────────────────────────────────

def _clean_env() -> Dict[str, str]:
    """The shell gets a plain environment, not the web server's."""
    me = pwd.getpwuid(os.getuid())
    return {'TERM': 'xterm-256color', 'LANG': 'C.UTF-8', 'HOME': me.pw_dir, 'USER': me.pw_name,
            'LOGNAME': me.pw_name, 'SHELL': SHELL,
            'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'}


def start_shell(cols: int = 100, rows: int = 30, argv=None) -> Tuple[subprocess.Popen, int]:
    master, slave = os.openpty()
    resize(master, cols, rows)
    env = _clean_env()
    cwd = env['HOME'] if os.path.isdir(env['HOME']) else '/'
    proc = subprocess.Popen(argv or [SHELL, '--login'], stdin=slave, stdout=slave, stderr=slave, env=env, cwd=cwd,
                            start_new_session=True, close_fds=True)
    os.close(slave)
    os.set_blocking(master, False)
    return proc, master


def resize(fd: int, cols: int, rows: int) -> None:
    cols, rows = max(10, min(500, int(cols))), max(4, min(300, int(rows)))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))


def end_shell(proc: subprocess.Popen) -> None:
    """The whole process group: what the shell started ends with it."""
    for sig in (signal.SIGHUP, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            proc.wait(timeout=2)
            break
        except subprocess.TimeoutExpired:
            continue


async def session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, token: str, argv=None) -> None:
    loop = asyncio.get_running_loop()
    proc, master = start_shell(argv=argv)
    output: asyncio.Queue = asyncio.Queue(maxsize=64)
    last_input = [time.monotonic()]

    def readable() -> None:
        try:
            data = os.read(master, READ_CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b''
        if not data:
            loop.remove_reader(master)
        try:
            output.put_nowait(data)
        except asyncio.QueueFull:
            loop.remove_reader(master)            # the browser is slow: wait for it
            loop.create_task(_put_later(data))

    async def _put_later(data: bytes) -> None:
        await output.put(data)
        if data:
            loop.add_reader(master, readable)

    async def send_output() -> str:
        while True:
            data = await output.get()
            if not data:
                return 'The shell ended.'
            writer.write(frame(2, data))
            await writer.drain()

    async def receive_input() -> str:
        while True:
            op, data = await read_message(reader)
            if op == 8:
                return ''
            if op == 9:
                writer.write(frame(10, data))
                continue
            if op == 2:
                last_input[0] = time.monotonic()
                os.write(master, data)
            elif op == 1:
                try:
                    msg = json.loads(data.decode('utf-8'))
                    size = msg.get('resize') if isinstance(msg, dict) else None
                    if isinstance(size, dict):
                        resize(master, size.get('cols', 100), size.get('rows', 30))
                except (ValueError, TypeError, OSError):
                    pass

    async def watch() -> str:
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            if not _still_signed_in(token):
                return 'You were signed out, so the terminal closed.'
            if time.monotonic() - last_input[0] > IDLE_SECONDS:
                return f'Closed after {IDLE_SECONDS // 60} minutes without typing.'

    loop.add_reader(master, readable)
    tasks = [asyncio.ensure_future(t()) for t in (send_output, receive_input, watch)]
    reason = ''
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                reason = task.result() or reason
            except (Closed, asyncio.IncompleteReadError, ConnectionError, OSError):
                pass
    finally:
        for task in tasks:
            task.cancel()
        try:
            loop.remove_reader(master)
        except (ValueError, OSError):
            pass
        await loop.run_in_executor(None, end_shell, proc)
        os.close(master)
        try:
            if reason:
                writer.write(frame(1, json.dumps({'closed': reason}).encode()))
            writer.write(frame(8, struct.pack('!H', 1000)))
            await writer.drain()
            writer.close()
        except (ConnectionError, OSError):
            pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, argv=None) -> None:
    global _open
    try:
        head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    if len(head) > HEAD_LIMIT:
        writer.write(_refuse('431 Request Header Fields Too Large'))
        writer.close()
        return
    found, status = check_request(head)
    if not found:
        writer.write(_refuse(status))
        writer.close()
        return
    if _open >= MAX_SESSIONS:
        writer.write(_refuse('503 Service Unavailable'))
        writer.close()
        return
    _open += 1
    try:
        writer.write(accept_header(found[1]))
        await writer.drain()
        await session(reader, writer, found[0], argv)
    finally:
        _open -= 1


async def serve(port: int = PORT, tls_port: Optional[int] = TLS_PORT, host: str = '0.0.0.0',
                context: Optional[ssl.SSLContext] = None) -> None:
    servers = [await asyncio.start_server(handle, host, port, limit=HEAD_LIMIT)]
    if tls_port and context:
        servers.append(await asyncio.start_server(handle, host, tls_port, ssl=context, limit=HEAD_LIMIT))
    await asyncio.gather(*(s.serve_forever() for s in servers))


def serve_in_background() -> Optional[threading.Thread]:
    """Start the terminal door next to the web page (alvaos-backend.py)."""
    context = None
    try:
        import tls_manager
        tls_manager.ensure_certificates()
        context = tls_manager.ssl_context()
    except Exception as e:  # noqa: BLE001 - HTTPS is extra; plain HTTP keeps working without it
        print(f'HTTPS for the admin terminal not started: {e}')

    def run() -> None:
        try:
            asyncio.run(serve(context=context))
        except OSError as e:
            print(f'The admin terminal could not start: {e}')

    thread = threading.Thread(target=run, name='admin-terminal', daemon=True)
    thread.start()
    return thread
