#!/usr/bin/env python3
"""AlvaOS Link: reach this NAS from anywhere, without a router setting and without an account.

The NAS has a key pair. Its public key (64 hex digits) is its address: a phone or a buddy NAS
that knows it can connect, wherever both are. iroh (QUIC) tries a direct connection through the
routers first (hole punching) and falls back to a relay server that only passes on encrypted
packets; it cannot read them. Who is on the other end is proven by their key, so a list of
allowed keys is all the access control that is needed here.

This daemon runs on the NAS (systemd `alvaos-link`). Nothing else on the NAS needs to know
about iroh: other processes talk to it over a small local control API (link_client.py) and over
ordinary TCP on the loopback network.

    Incoming (a peer connects to this NAS), one QUIC stream per request, its first line names a service:

        hub    a phone         -> the Hub (127.0.0.1:8090)
        api    a buddy         -> this NAS's backend (127.0.0.1:8080), for the buddy API
        nbd    a buddy         -> the backup vault server (127.0.0.1:10809)
        pair   anybody         -> one narrow request: pair a phone with a code from the Hub, or
                                  accept a buddy's pairing token. Nothing else is open to strangers.

    The local side sees who it is talking to by the source address: every allowed peer gets its
    own address on the loopback network (127.95.x.y, see `alias_for`), and the daemon connects to the
    service from that address. The Hub then counts failed sign-ins per phone, and the backup
    code knows which buddy is asking, as it did with the old tunnel addresses.

    Outgoing (this NAS to a buddy): for every buddy the daemon listens on the buddy's address
    (127.95.x.y) at the ports of `LOCAL_PORTS` and carries each connection to that buddy's
    service. The backup code connects there as if the buddy were next door.

State is in /var/lib/alvaos/link: the secret key, the allowed peers (peers.json) and the
settings (config.json).
"""

import asyncio
import json
import os
import re
import secrets
import sys
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

for _vendor in ('/opt/alvaos/vendor',):
    if os.path.isdir(_vendor) and _vendor not in sys.path:
        sys.path.insert(0, _vendor)

ALPN = b'alvaos/link/1'
STATE_DIR = os.environ.get('ALVAOS_LINK_DIR', '/var/lib/alvaos/link')
CONTROL_ADDRESS = ('127.0.0.1', 8095)

# What an incoming service connects to on this NAS.
SERVICES = {'hub': ('127.0.0.1', 8090), 'api': ('127.0.0.1', 8080), 'nbd': ('127.0.0.1', 10809)}
# Which services each kind of peer may use.
ALLOWED = {'phone': {'hub'}, 'buddy': {'api', 'nbd'}}
# Where the backup code connects to reach a buddy's service (at the buddy's own address).
LOCAL_PORTS = {'nbd': 10809, 'api': 18080}

PAIR_ALIAS = '127.95.0.1'      # source address of pairing requests (strangers)
ID_RE = re.compile(r'^[0-9a-f]{64}$')
HEADER_LIMIT = 512
REQUEST_LIMIT = 16 * 1024
CHUNK = 64 * 1024
MAX_STREAMS_PER_PEER = 64
PAIR_PER_MINUTE = 6            # per stranger
PAIR_PER_MINUTE_ALL = 30


def alias_for(index: int) -> str:
    """The loopback address of the n-th peer (1, 2, 3, …): 127.95.1.1, 127.95.1.2, …"""
    index = max(1, int(index)) - 1
    return f'127.95.{1 + index // 254}.{1 + index % 254}'


def _now() -> float:
    return time.time()


# ── State ──────────────────────────────────────────────────────────────────────

class State:
    """The allowed peers and the settings, in two small files."""

    def __init__(self, directory: str):
        self.dir = directory
        self.lock = threading.RLock()
        os.makedirs(directory, mode=0o750, exist_ok=True)
        self.peers: Dict[str, Dict[str, Any]] = self._read('peers.json', {})
        self.config: Dict[str, Any] = {'enabled': True, **self._read('config.json', {})}

    def _read(self, name: str, default):
        try:
            with open(os.path.join(self.dir, name)) as f:
                data = json.load(f)
            return data if isinstance(data, type(default)) else default
        except (OSError, ValueError):
            return default

    def _write(self, name: str, data) -> None:
        path = os.path.join(self.dir, name)
        tmp = path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)

    def save(self) -> None:
        with self.lock:
            self._write('peers.json', self.peers)
            self._write('config.json', self.config)

    def secret_key(self) -> bytes:
        path = os.path.join(self.dir, 'key')
        try:
            with open(path, 'rb') as f:
                key = f.read()
            if len(key) == 32:
                return key
        except OSError:
            pass
        key = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(key)
        return key

    def write_secret_key(self, key: bytes) -> None:
        path = os.path.join(self.dir, 'key')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(key)

    def control_token(self) -> str:
        """The password of the local control API: a file only the NAS's own services can read."""
        path = os.path.join(self.dir, 'control_token')
        try:
            with open(path) as f:
                token = f.read().strip()
            if len(token) >= 32:
                return token
        except OSError:
            pass
        token = secrets.token_hex(24)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, 'w') as f:
            f.write(token + '\n')
        try:
            import grp
            os.chown(path, 0, grp.getgrnam('alvaos').gr_gid)
        except (KeyError, OSError, ImportError):
            pass
        return token

    def next_alias(self) -> str:
        used = {p.get('alias') for p in self.peers.values()}
        for i in range(1, 64000):
            alias = alias_for(i)
            if alias not in used:
                return alias
        raise RuntimeError('No more peer addresses')

    def add_peer(self, peer_id: str, kind: str, name: str, **extra: Any) -> Dict[str, Any]:
        with self.lock:
            peer = self.peers.get(peer_id) or {'alias': self.next_alias(), 'added': _now()}
            peer.update(kind=kind, name=name[:60], **extra)
            self.peers[peer_id] = peer
            self.save()
            return dict(peer)

    def remove_peer(self, peer_id: str) -> bool:
        with self.lock:
            gone = self.peers.pop(peer_id, None) is not None
            if gone:
                self.save()
            return gone

    def set_buddies(self, buddies: List[Dict[str, str]]) -> None:
        """Make the allowed buddies exactly this list: {id, name}."""
        with self.lock:
            wanted = {b['id']: b for b in buddies}
            for peer_id in [i for i, p in self.peers.items() if p.get('kind') == 'buddy' and i not in wanted]:
                del self.peers[peer_id]
            for peer_id, b in wanted.items():
                self.add_peer(peer_id, 'buddy', b.get('name') or peer_id[:8])
            self.save()


# ── The link ─────────────────────────────────────────────────────────────────────

class Link:
    def __init__(self, state: State, services: Optional[Dict[str, Tuple[str, int]]] = None,
                 local_ports: Optional[Dict[str, int]] = None, direct_only: bool = False,
                 hints: Optional[Dict[str, List[str]]] = None):
        self.state = state
        self.services = services or dict(SERVICES)
        self.local_ports = local_ports or dict(LOCAL_PORTS)
        self.direct_only = direct_only          # tests: no relay, loopback only
        self.hints: Dict[str, List[str]] = hints or {}
        self.endpoint: Any = None
        self.iroh: Any = None
        self.tasks: List[asyncio.Task] = []
        self.forwarders: Dict[str, List[Any]] = {}
        self.connections: Dict[str, Any] = {}
        self.conn_lock = asyncio.Lock()
        self.streams: Dict[str, int] = {}
        self.pair_log: Dict[str, List[float]] = {}
        self.seen: Dict[str, float] = {}
        self.started = False

    # -- Start and stop ----------------------------------------------------------

    @property
    def node_id(self) -> str:
        return self.endpoint.id().to_bytes().hex() if self.endpoint else ''

    async def start(self) -> None:
        import iroh
        self.iroh = iroh
        preset = iroh.preset_n0_disable_relay() if self.direct_only else iroh.preset_n0()
        options = iroh.EndpointOptions(preset=preset, secret_key=self.state.secret_key(), alpns=[ALPN],
                                       bind_addr='127.0.0.1:0' if self.direct_only else None)
        self.endpoint = await iroh.Endpoint.bind(options)
        self.tasks.append(asyncio.create_task(self._accept_loop()))
        self.started = True
        await self.refresh_forwarders()

    async def stop(self) -> None:
        self.started = False
        for task in self.tasks:
            task.cancel()
        for servers in self.forwarders.values():
            for server in servers:
                server.close()
        self.forwarders.clear()
        for conn in list(self.connections.values()):
            try:
                conn.close(0, b'bye')
            except Exception:  # noqa: BLE001
                pass
        self.connections.clear()
        if self.endpoint is not None:
            try:
                await self.endpoint.close()
            except Exception:  # noqa: BLE001
                pass
            self.endpoint = None

    def address_hints(self) -> List[str]:
        return list(self.endpoint.bound_sockets()) if self.endpoint else []

    # -- Incoming ----------------------------------------------------------------

    async def _accept_loop(self) -> None:
        while True:
            try:
                incoming = await self.endpoint.accept_next()
            except Exception:  # noqa: BLE001 - closed
                return
            if incoming is None:
                return
            asyncio.create_task(self._connection(incoming))

    async def _connection(self, incoming: Any) -> None:
        try:
            accepting = await incoming.accept()
            conn = await accepting.connect()
        except Exception:  # noqa: BLE001 - a failed handshake is not our problem
            return
        remote = conn.remote_id().to_bytes().hex()
        self.seen[remote] = _now()
        while True:
            try:
                bi = await conn.accept_bi()
            except Exception:  # noqa: BLE001 - the peer went away
                return
            asyncio.create_task(self._stream(remote, bi))

    async def _read_line(self, recv: Any, limit: int) -> Tuple[Optional[str], bytes]:
        """One line from a stream and whatever came with it after the newline."""
        buf = b''
        while b'\n' not in buf:
            if len(buf) > limit:
                return None, b''
            chunk = await asyncio.wait_for(recv.read(4096), 10)
            if not chunk:
                return None, b''
            buf += chunk
        line, _, rest = buf.partition(b'\n')
        return line.decode('utf-8', 'replace').strip(), rest

    async def _stream(self, remote: str, bi: Any) -> None:
        recv, send = bi.recv(), bi.send()
        self.streams[remote] = self.streams.get(remote, 0) + 1
        try:
            if self.streams[remote] > MAX_STREAMS_PER_PEER:
                await send.reset(2)
                return
            line, rest = await self._read_line(recv, HEADER_LIMIT)
            if not line:
                await send.reset(1)
                return
            service = line.split()[0]
            peer = self.state.peers.get(remote)
            if service == 'pair':
                await self._pair(remote, recv, send, rest)
            elif service == 'ping' and peer:
                await send.write_all(b'pong\n')
                await send.finish()
            elif peer and service in ALLOWED.get(str(peer.get('kind')), ()) and service in self.services:
                host, port = self.services[service]
                reader, writer = await asyncio.open_connection(host, port, local_addr=(peer['alias'], 0))
                if rest:
                    writer.write(rest)
                await self._pipe(reader, writer, recv, send)
            else:
                await send.reset(1)             # not for you
        except Exception:  # noqa: BLE001 - one stream must never take the daemon down
            try:
                await send.reset(3)
            except Exception:  # noqa: BLE001
                pass
        finally:
            self.streams[remote] = max(0, self.streams.get(remote, 1) - 1)

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, recv: Any, send: Any) -> None:
        """Both ways at once until both sides are done."""
        async def up() -> None:
            try:
                while True:
                    data = await reader.read(CHUNK)
                    if not data:
                        break
                    await send.write_all(data)
                await send.finish()
            except Exception:  # noqa: BLE001
                try:
                    await send.reset(4)
                except Exception:  # noqa: BLE001
                    pass

        async def down() -> None:
            try:
                while True:
                    data = await recv.read(CHUNK)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
                if writer.can_write_eof():
                    writer.write_eof()
            except Exception:  # noqa: BLE001
                writer.close()

        try:
            await asyncio.gather(up(), down())
        finally:
            writer.close()

    # -- Pairing: the only thing a stranger may do ---------------------------------

    def _pair_allowed(self, remote: str) -> bool:
        now = _now()
        mine = [t for t in self.pair_log.get(remote, []) if now - t < 60]
        everyone = [t for log in self.pair_log.values() for t in log if now - t < 60]
        if len(mine) >= PAIR_PER_MINUTE or len(everyone) >= PAIR_PER_MINUTE_ALL:
            return False
        self.pair_log[remote] = mine + [now]
        return True

    async def _pair(self, remote: str, recv: Any, send: Any, rest: bytes) -> None:
        async def answer(payload: Dict[str, Any]) -> None:
            await send.write_all(json.dumps(payload).encode() + b'\n')
            await send.finish()

        if not self._pair_allowed(remote):
            await answer({'error': 'Too many attempts. Wait a minute.'})
            return
        buf = rest
        while b'\n' not in buf and len(buf) < REQUEST_LIMIT:
            chunk = await asyncio.wait_for(recv.read(4096), 10)
            if not chunk:
                break
            buf += chunk
        try:
            request = json.loads(buf.split(b'\n')[0].decode('utf-8'))
        except ValueError:
            await answer({'error': 'That request was not understood.'})
            return
        op = request.get('op') if isinstance(request, dict) else None
        loop = asyncio.get_running_loop()
        if op == 'device':
            host, port = self.services['hub']
            status, body = await loop.run_in_executor(None, _http_post, host, port, '/api/devices/pair', PAIR_ALIAS,
                                                      {'code': str(request.get('code') or '')[:32],
                                                       'device': request.get('device') if isinstance(request.get('device'), dict) else {}}, {})
            if status == 200 and body.get('success'):
                device = str(body.get('device') or '')
                self.state.add_peer(remote, 'phone', str((request.get('device') or {}).get('name') or 'Phone'),
                                    device=device, user=str(body.get('user') or ''))
            await answer(body if isinstance(body, dict) else {'error': 'The NAS did not answer.'})
        elif op == 'buddy':
            host, port = self.services['api']
            status, body = await loop.run_in_executor(
                None, _http_post, host, port, '/api/v1/backup/pairing/accept', PAIR_ALIAS,
                {'token': str(request.get('token') or '')[:8192]},
                {'X-Buddy-Secret': str(request.get('secret') or '')[:256]})
            await answer(body if isinstance(body, dict) else {'error': 'The NAS did not answer.'})
        else:
            await answer({'error': 'That request is not known.'})

    # -- Outgoing: buddies as neighbours on the loopback network -------------------

    async def refresh_forwarders(self) -> None:
        """One listener per buddy and service, on the buddy's own address."""
        buddies = {i: p for i, p in self.state.peers.items() if p.get('kind') == 'buddy'}
        for peer_id in [i for i in self.forwarders if i not in buddies]:
            for server in self.forwarders.pop(peer_id):
                server.close()
        for peer_id, peer in buddies.items():
            if peer_id in self.forwarders:
                continue
            servers = []
            for service in ('nbd', 'api'):
                handler = self._forwarder(peer_id, service)
                try:
                    servers.append(await asyncio.start_server(handler, peer['alias'], self.local_ports[service]))
                except OSError as exc:
                    print(f'Link: cannot listen on {peer["alias"]}:{self.local_ports[service]}: {exc}')
            self.forwarders[peer_id] = servers

    def _forwarder(self, peer_id: str, service: str):
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                bi = await self._open_stream(peer_id, service)
            except Exception as exc:  # noqa: BLE001
                print(f'Link: {service} to {peer_id[:8]}: {exc}')
                writer.close()
                return
            await self._pipe(reader, writer, bi.recv(), bi.send())
        return handle

    async def _connection_to(self, peer_id: str, fresh: bool = False) -> Any:
        async with self.conn_lock:
            conn = None if fresh else self.connections.get(peer_id)
            if conn is not None:
                return conn
            addr = self.iroh.EndpointAddr(self.iroh.EndpointId.from_bytes(bytes.fromhex(peer_id)), None,
                                          self.hints.get(peer_id, []))
            conn = await asyncio.wait_for(self.endpoint.connect(addr, ALPN), 30)
            self.connections[peer_id] = conn
            return conn

    async def _open_stream(self, peer_id: str, service: str) -> Any:
        for attempt in (0, 1):
            conn = await self._connection_to(peer_id, fresh=attempt == 1)
            try:
                bi = await conn.open_bi()
                await bi.send().write_all(service.encode() + b'\n')
                return bi
            except Exception:  # noqa: BLE001 - a stale connection: connect again
                self.connections.pop(peer_id, None)
                if attempt:
                    raise
        raise RuntimeError('unreachable')

    async def remote_pair(self, peer_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        """Pair with another NAS: the same narrow request a stranger may make (see _pair)."""
        try:
            conn = await self._connection_to(peer_id)
            bi = await conn.open_bi()
            await bi.send().write_all(b'pair\n' + json.dumps(request).encode() + b'\n')
            await bi.send().finish()
            raw = await asyncio.wait_for(bi.recv().read_to_end(REQUEST_LIMIT), 40)
            parsed = json.loads(raw.decode('utf-8').split('\n')[0])
            return parsed if isinstance(parsed, dict) else {'error': 'The other NAS answered something odd.'}
        except Exception as exc:  # noqa: BLE001
            self.connections.pop(peer_id, None)
            return {'error': f'The other NAS could not be reached: {exc}'}

    async def ping(self, peer_id: str) -> Dict[str, Any]:
        """Whether an allowed buddy answers, and how fast."""
        started = time.monotonic()
        try:
            bi = await self._open_stream(peer_id, 'ping')
            await bi.send().finish()
            answer = await asyncio.wait_for(bi.recv().read_to_end(16), 20)
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'error': str(exc)}
        ok = answer.strip() == b'pong'
        return {'ok': ok, 'ms': round((time.monotonic() - started) * 1000), 'error': '' if ok else 'The other NAS did not answer.'}

    # -- For the control API ---------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        addr = self.endpoint.addr() if self.endpoint else None
        peers = []
        for peer_id, p in self.state.peers.items():
            peers.append({'id': peer_id, 'kind': p.get('kind'), 'name': p.get('name'), 'alias': p.get('alias'),
                          'device': p.get('device', ''), 'user': p.get('user', ''),
                          'connected': peer_id in self.connections, 'last_seen': self.seen.get(peer_id)})
        return {'enabled': bool(self.state.config.get('enabled', True)), 'running': self.started,
                'node_id': self.node_id, 'relay': (addr.relay_url() if addr else None) or '',
                'peers': peers}


def _http_post(host: str, port: int, path: str, source: str, body: Dict[str, Any],
               headers: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """One local POST from a given source address; (status, JSON answer)."""
    try:
        conn = HTTPConnection(host, port, timeout=15, source_address=(source, 0))
        conn.request('POST', path, json.dumps(body), {'Content-Type': 'application/json', **headers})
        res = conn.getresponse()
        data = res.read(256 * 1024)
        conn.close()
        try:
            parsed = json.loads(data.decode('utf-8'))
        except ValueError:
            parsed = {}
        return res.status, parsed if isinstance(parsed, dict) else {}
    except OSError as exc:
        return 0, {'error': f'The NAS did not answer: {exc}'}


# ── Local control API (for the Hub, the backup code and the admin pages) ────────

def control_server(link: Link, loop: asyncio.AbstractEventLoop, token: str,
                   address: Tuple[str, int] = CONTROL_ADDRESS) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> Any:
            length = min(int(self.headers.get('Content-Length') or 0), 64 * 1024)
            try:
                return json.loads(self.rfile.read(length) or b'{}')
            except ValueError:
                return {}

        def _run(self, coro: Any) -> Any:
            return asyncio.run_coroutine_threadsafe(coro, loop).result(30)

        def _ok(self) -> bool:
            if not secrets.compare_digest(self.headers.get('X-Link-Token', ''), token):
                self._send(403, {'error': 'Not allowed.'})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._ok():
                return
            if self.path == '/status':
                self._send(200, link.status())
            elif self.path == '/secret':
                self._send(200, {'secret': link.state.secret_key().hex()})
            else:
                self._send(404, {'error': 'Not found.'})

        def do_POST(self) -> None:  # noqa: N802
            if not self._ok():
                return
            body = self._body()
            if self.path == '/buddies':
                items = body.get('buddies') if isinstance(body, dict) else None
                clean = [{'id': str(b.get('id') or '').lower(), 'name': str(b.get('name') or '')}
                         for b in (items if isinstance(items, list) else []) if isinstance(b, dict)]
                if any(not ID_RE.match(b['id']) for b in clean):
                    self._send(400, {'error': 'A buddy address is not valid.'})
                    return
                link.state.set_buddies(clean)
                self._run(link.refresh_forwarders())
                self._send(200, link.status())
            elif self.path == '/pair':
                peer_id = str(body.get('id') or '').lower()
                request = body.get('request')
                if not ID_RE.match(peer_id) or not isinstance(request, dict) or not link.started:
                    self._send(400, {'error': 'Link is not running, or that address is not valid.'})
                    return
                self._send(200, self._run(link.remote_pair(peer_id, request)))
            elif self.path == '/ping':
                peer_id = str(body.get('id') or '').lower()
                if peer_id not in link.state.peers or not link.started:
                    self._send(200, {'ok': False, 'error': 'That buddy is not known to Link.'})
                    return
                self._send(200, self._run(link.ping(peer_id)))
            elif self.path == '/secret':
                # The recovery kit of Buddy Backup carries this key: a new install is the same node again.
                secret = str(body.get('secret') or '').lower() if isinstance(body, dict) else ''
                if not re.fullmatch(r'[0-9a-f]{64}', secret):
                    self._send(400, {'error': 'That key is not valid.'})
                    return
                link.state.write_secret_key(bytes.fromhex(secret))
                if link.started:
                    self._run(link.stop())
                    self._run(link.start())
                self._send(200, link.status())
            elif self.path == '/remove-device':
                device = str(body.get('device') or '') if isinstance(body, dict) else ''
                gone = [i for i, p in list(link.state.peers.items()) if p.get('kind') == 'phone' and p.get('device') == device and device]
                for peer_id in gone:
                    link.state.remove_peer(peer_id)
                self._send(200, {'removed': len(gone)})
            elif self.path == '/config':
                if isinstance(body, dict) and 'enabled' in body:
                    link.state.config['enabled'] = bool(body['enabled'])
                    link.state.save()
                    self._run(link.start() if body['enabled'] and not link.started else link.stop()
                              if not body['enabled'] and link.started else _nothing())
                self._send(200, link.status())
            else:
                self._send(404, {'error': 'Not found.'})

    server = ThreadingHTTPServer(address, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def _nothing() -> None:
    return None


async def run() -> None:
    state = State(STATE_DIR)
    # Tests start the daemon with other services and no relay (ALVAOS_LINK_TEST, a JSON object).
    test = json.loads(os.environ.get('ALVAOS_LINK_TEST') or 'null')
    if isinstance(test, dict):
        services = {k: ('127.0.0.1', int(v)) for k, v in (test.get('services') or {}).items()}
        link = Link(state, services={**SERVICES, **services}, direct_only=True)
    else:
        link = Link(state)
    token = state.control_token()
    if state.config.get('enabled', True):
        await link.start()
    control_server(link, asyncio.get_running_loop(), token,
                   ('127.0.0.1', int(test['control_port'])) if isinstance(test, dict) and test.get('control_port') else CONTROL_ADDRESS)
    if isinstance(test, dict):
        print(json.dumps({'node_id': link.node_id, 'addresses': link.address_hints()}), flush=True)
    print(f'AlvaOS Link: {link.node_id or "off"}', flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == '__main__':
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
