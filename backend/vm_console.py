#!/usr/bin/env python3
"""The screen of a virtual machine in the browser (Virtual machines page).

QEMU offers each machine's screen as VNC over WebSocket on the loopback
address of the NAS only (backend/vm_ops.py). The browser (noVNC) cannot reach
that, and QEMU has no sign-in. This is the door in between: the admin page asks
the backend for a one-time ticket (the admin is signed in there), the browser
opens a WebSocket here with that ticket, and after the check this only copies
bytes between the two. A ticket works once and for one minute, for one
machine. Nothing is stored and nothing of the stream is looked at.

Plain HTTP on 8085 and HTTPS on 9445 (the NAS's own certificate), like the
other pages: an HTTPS page can only open a secure WebSocket.
"""

import asyncio
import secrets
import ssl
import threading
import time
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

PORT = 8085
TLS_PORT = 9445
TICKET_SECONDS = 60
HEAD_LIMIT = 16 * 1024
MAX_CONNECTIONS = 16
VNC_WEBSOCKET_BASE = 5700   # vm_ops.VNC_WEBSOCKET_BASE

_tickets: Dict[str, Tuple[str, int, float]] = {}
_lock = threading.Lock()
_open = 0


def issue(vm_id: str, slot: int) -> str:
    """A one-time ticket for the screen of one machine."""
    ticket = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        for key in [k for k, v in _tickets.items() if v[2] < now]:
            del _tickets[key]
        _tickets[ticket] = (vm_id, slot, now + TICKET_SECONDS)
    return ticket


def redeem(ticket: str) -> Optional[Tuple[str, int]]:
    """(machine, screen number) once, or None."""
    with _lock:
        found = _tickets.pop(ticket, None)
    if not found or found[2] < time.monotonic():
        return None
    return found[0], found[1]


def _refuse(status: str) -> bytes:
    return f'HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode()


def check_request(head: bytes) -> Tuple[Optional[int], str]:
    """(screen number, '') for a good request, else (None, status line)."""
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
    if method != 'GET' or 'websocket' not in headers.get('upgrade', '').lower():
        return None, '400 Bad Request'
    # Browsers always send the page's origin: only a page of this NAS may open it.
    origin = urlsplit(headers.get('origin', '')).hostname
    host = urlsplit('//' + headers.get('host', '')).hostname
    if not origin or origin != host:
        return None, '403 Forbidden'
    ticket = (parse_qs(urlsplit(target).query).get('ticket') or [''])[0]
    found = redeem(ticket)
    if not found:
        return None, '403 Forbidden'
    return found[1], ''


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 target: Callable[[int], Tuple[str, int]] = lambda slot: ('127.0.0.1', VNC_WEBSOCKET_BASE + slot)
                 ) -> None:
    global _open
    if _open >= MAX_CONNECTIONS:
        writer.write(_refuse('503 Service Unavailable'))
        writer.close()
        return
    _open += 1
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        if len(head) > HEAD_LIMIT:
            writer.write(_refuse('431 Request Header Fields Too Large'))
            writer.close()
            return
        slot, status = check_request(head)
        if slot is None:
            writer.write(_refuse(status))
            writer.close()
            return
        try:
            host, port = target(slot)
            up_reader, up_writer = await asyncio.open_connection(host, port)
        except OSError:
            writer.write(_refuse('502 Bad Gateway'))   # the machine is not running
            writer.close()
            return
        up_writer.write(head)
        await up_writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
    finally:
        _open -= 1


async def serve(port: int = PORT, tls_port: Optional[int] = TLS_PORT, host: str = '0.0.0.0',
                context: Optional[ssl.SSLContext] = None, target=None) -> None:
    def client(r, w):
        return handle(r, w, target) if target else handle(r, w)
    servers = [await asyncio.start_server(client, host, port, limit=HEAD_LIMIT)]
    if tls_port and context:
        servers.append(await asyncio.start_server(client, host, tls_port, ssl=context, limit=HEAD_LIMIT))
    await asyncio.gather(*(s.serve_forever() for s in servers))


def serve_in_background() -> Optional[threading.Thread]:
    """Start the door next to the web page (alvaos-backend.py)."""
    context = None
    try:
        import tls_manager
        tls_manager.ensure_certificates()
        context = tls_manager.ssl_context()
    except Exception as e:  # noqa: BLE001 - HTTPS is extra; plain HTTP keeps working without it
        print(f'HTTPS for the virtual machine screens not started: {e}')

    def run() -> None:
        try:
            asyncio.run(serve(context=context))
        except OSError as e:
            print(f'The virtual machine screens could not start: {e}')

    thread = threading.Thread(target=run, name='vm-console', daemon=True)
    thread.start()
    return thread
