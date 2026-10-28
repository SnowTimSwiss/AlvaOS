"""Apps of the App Store inside the Hub's window.

An app from the App Store has a port of its own and, most of the time, tells
browsers not to show it inside another page (X-Frame-Options, CSP
frame-ancestors). For the apps the admin lets open in the Hub's window, the
Hub runs a small proxy on a second port (the app's port + EMBED_OFFSET) that
passes everything on to the app and only takes those two instructions out of
the answers. Nothing else is changed: the app still asks for its own sign-in,
and WebSockets (live pages, media players) are passed through.

The proxy always goes to one fixed address, 127.0.0.1 and the app's port: it
is no open proxy. It listens where the app itself does (all addresses), so it
opens the app to nobody who could not reach it before. AlvaOS Link and the
computer app carry the same ports (docs/LINK.md), so the window works away
from home and in the apps too.

The messages are read and written with h11 (framing, keep-alive, chunks); after
a WebSocket upgrade the two sides are tied together as raw bytes.
"""

import asyncio
import json
import os
import re
import threading
from typing import Any

import h11

EMBED_OFFSET = 20000
EMBED_PORTS_FILE = '/var/lib/alvaos/embed_ports.json'   # for AlvaOS Link: the ports it may carry
MAX_HEAD = 64 * 1024
IDLE_SECONDS = 300
CONNECT_SECONDS = 10
CHUNK = 64 * 1024


def embed_port(app_port: int) -> int | None:
    """The proxy's port for an app on `app_port`, or None when it would be above 65535."""
    port = app_port + EMBED_OFFSET
    return port if 1 <= app_port <= 65535 and port <= 65535 else None


def _clean_csp(value: str) -> str:
    """The policy without its frame-ancestors part (the rest stays)."""
    parts = [p.strip() for p in value.split(';')]
    return '; '.join(p for p in parts if p and not re.match(r'(?i)^frame-ancestors\b', p))


def clean_response_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """The answer's headers without what keeps the app out of a frame."""
    out = []
    for name, value in headers:
        lower = name.lower()
        if lower == b'x-frame-options':
            continue
        if lower in (b'content-security-policy', b'content-security-policy-report-only'):
            cleaned = _clean_csp(value.decode('latin-1'))
            if cleaned:
                out.append((name, cleaned.encode('latin-1')))
            continue
        out.append((name, value))
    return out


async def _read(reader: asyncio.StreamReader) -> bytes:
    return await asyncio.wait_for(reader.read(CHUNK), IDLE_SECONDS)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await asyncio.wait_for(reader.read(CHUNK), IDLE_SECONDS)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, target_port: int) -> None:
    """One browser connection: any number of requests, one upstream connection."""
    try:
        up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', target_port), CONNECT_SECONDS)
    except (asyncio.TimeoutError, OSError):
        client_writer.close()
        return
    try:
        await _relay(client_reader, client_writer, up_reader, up_writer)
    finally:
        for writer in (client_writer, up_writer):
            try:
                writer.close()
            except OSError:
                pass


async def _relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter,
                 up_reader: asyncio.StreamReader, up_writer: asyncio.StreamWriter) -> None:
    try:
        browser = h11.Connection(h11.SERVER, max_incomplete_event_size=MAX_HEAD)
        app = h11.Connection(h11.CLIENT, max_incomplete_event_size=MAX_HEAD)
        while True:
            # The request: head, then body, from the browser to the app.
            request: h11.Request | None = None
            while True:
                event = browser.next_event()
                if event is h11.NEED_DATA:
                    data = await _read(client_reader)
                    browser.receive_data(data)
                    continue
                if isinstance(event, h11.ConnectionClosed):
                    return
                if isinstance(event, h11.Request):
                    request = event
                    up_writer.write(app.send(h11.Request(method=event.method, target=event.target, headers=event.headers)))
                elif isinstance(event, h11.Data):
                    up_writer.write(app.send(h11.Data(data=event.data)))
                elif isinstance(event, h11.EndOfMessage):
                    up_writer.write(app.send(h11.EndOfMessage(headers=event.headers)))
                    await up_writer.drain()
                    break
                elif browser.their_state is h11.MIGHT_SWITCH_PROTOCOL or event is h11.PAUSED:
                    break
            if request is None:
                return
            # The answer: head (cleaned), then body, from the app to the browser.
            switched = False
            while True:
                event = app.next_event()
                if event is h11.NEED_DATA:
                    data = await _read(up_reader)
                    app.receive_data(data)
                    continue
                if isinstance(event, (h11.Response, h11.InformationalResponse)):
                    headers = clean_response_headers(list(event.headers))
                    cls = h11.InformationalResponse if isinstance(event, h11.InformationalResponse) else h11.Response
                    client_writer.write(browser.send(cls(status_code=event.status_code, headers=headers, reason=event.reason)))
                    if event.status_code == 101:
                        switched = True
                        break
                elif isinstance(event, h11.Data):
                    client_writer.write(browser.send(h11.Data(data=event.data)))
                    await client_writer.drain()
                elif isinstance(event, h11.EndOfMessage):
                    client_writer.write(browser.send(h11.EndOfMessage(headers=event.headers)))
                    await client_writer.drain()
                    break
                elif isinstance(event, h11.ConnectionClosed):
                    return
            if switched:
                # A WebSocket (or similar): from here on bytes in both directions.
                leftover_up = bytes(app.trailing_data[0])
                leftover_browser = bytes(browser.trailing_data[0])
                if leftover_up:
                    client_writer.write(leftover_up)
                if leftover_browser:
                    up_writer.write(leftover_browser)
                await asyncio.gather(_pipe(client_reader, up_writer), _pipe(up_reader, client_writer))
                return
            if browser.our_state is h11.MUST_CLOSE or app.our_state is h11.MUST_CLOSE:
                return
            browser.start_next_cycle()
            app.start_next_cycle()
    except (h11.ProtocolError, asyncio.TimeoutError, ConnectionError, OSError):
        # A message that is not HTTP, a silent connection or a closed one: end it.
        return


class EmbedProxies:
    """The listeners: one per app shown in the Hub's window, kept in step with the settings."""

    def __init__(self, ports_file: str = EMBED_PORTS_FILE, bind: str = '0.0.0.0') -> None:
        self.ports_file = ports_file
        self.bind = bind
        self.servers: dict[int, tuple[int, Any]] = {}    # listening port -> (app port, server)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.failed: dict[int, str] = {}

    def start(self) -> None:
        if self.thread:
            return
        ready = threading.Event()

        def run() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, name='embed-proxy', daemon=True)
        self.thread.start()
        ready.wait(5)

    def sync(self, wanted: dict[int, int]) -> None:
        """Listen on exactly these ports: {listening port: the app's port}."""
        self.start()
        assert self.loop is not None
        asyncio.run_coroutine_threadsafe(self._sync(dict(wanted)), self.loop).result(15)
        self._write_ports()

    async def _sync(self, wanted: dict[int, int]) -> None:
        for port, (target, server) in list(self.servers.items()):
            if wanted.get(port) != target:
                server.close()
                await server.wait_closed()
                del self.servers[port]
        for port, target in wanted.items():
            if port in self.servers:
                continue

            async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: int = target) -> None:
                await handle(reader, writer, target)

            try:
                server = await asyncio.start_server(serve, self.bind, port)
            except OSError as exc:
                self.failed[port] = str(exc)
                continue
            self.failed.pop(port, None)
            self.servers[port] = (target, server)

    def _write_ports(self) -> None:
        """What AlvaOS Link may carry for apps (link_daemon.py reads this)."""
        try:
            os.makedirs(os.path.dirname(self.ports_file), exist_ok=True)
            tmp = f'{self.ports_file}.tmp'
            with open(tmp, 'w') as f:
                json.dump({'ports': sorted(self.servers)}, f)
            os.replace(tmp, self.ports_file)
        except OSError:
            pass

    def stop(self) -> None:
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._sync({}), self.loop).result(15)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread = None
            self.loop = None
