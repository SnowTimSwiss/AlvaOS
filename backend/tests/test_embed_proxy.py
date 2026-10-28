"""Apps in the Hub's window: the proxy that takes the frame bans out of the answers (embed_proxy.py)."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import embed_proxy as ep


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class App(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    seen = []

    def log_message(self, *args):
        pass

    def _send(self, body, extra=()):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        App.seen.append(('GET', self.path, self.headers.get('Host')))
        if self.path == '/framed':
            self._send(b'hello', [('X-Frame-Options', 'DENY'),
                                  ('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; img-src data:"),
                                  ('Set-Cookie', 'a=b')])
        elif self.path == '/onlyframe':
            self._send(b'x', [('Content-Security-Policy', "frame-ancestors 'self'")])
        elif self.path == '/chunked':
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            for piece in (b'abc', b'defg'):
                self.wfile.write(f'{len(piece):x}\r\n'.encode() + piece + b'\r\n')
            self.wfile.write(b'0\r\n\r\n')
        else:
            self._send(b'plain')

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        App.seen.append(('POST', self.path, body))
        self._send(b'got ' + body)


@pytest.fixture
def stack(tmp_path):
    App.seen = []
    app_server = ThreadingHTTPServer(('127.0.0.1', 0), App)
    app_port = app_server.server_address[1]
    threading.Thread(target=app_server.serve_forever, daemon=True).start()
    proxies = ep.EmbedProxies(str(tmp_path / 'ports.json'), '127.0.0.1')
    listen = free_port()
    proxies.sync({listen: app_port})
    yield proxies, listen, app_port, tmp_path
    proxies.stop()
    app_server.shutdown()


def raw(port, request: bytes, until=b'', read=65536, timeout=3):
    with socket.create_connection(('127.0.0.1', port), timeout=timeout) as s:
        s.sendall(request)
        data = b''
        try:
            while True:
                chunk = s.recv(read)
                if not chunk:
                    break
                data += chunk
                if until and until in data:
                    break
        except TimeoutError:
            pass
        return data


def test_the_frame_bans_come_out_and_everything_else_stays(stack):
    _, listen, _, _ = stack
    got = raw(listen, b'GET /framed HTTP/1.1\r\nHost: nas:28096\r\nConnection: close\r\n\r\n')
    head, _, body = got.partition(b'\r\n\r\n')
    lower = head.lower()
    assert b'200' in head.split(b'\r\n')[0] and body == b'hello'
    assert b'x-frame-options' not in lower and b'frame-ancestors' not in lower
    assert b"default-src 'self'; img-src data:" in head          # the rest of the policy stays
    assert b'set-cookie: a=b' in lower
    assert ('GET', '/framed', 'nas:28096') in App.seen              # the browser's Host goes through


def test_a_policy_that_only_bans_frames_is_dropped_whole(stack):
    _, listen, _, _ = stack
    head = raw(listen, b'GET /onlyframe HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n').partition(b'\r\n\r\n')[0]
    assert b'content-security-policy' not in head.lower()


def test_keep_alive_post_bodies_and_chunks_work(stack):
    _, listen, _, _ = stack
    two = (b'GET /a HTTP/1.1\r\nHost: h\r\n\r\n'
           b'POST /p HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n\r\nhello'
           b'GET /chunked HTTP/1.1\r\nHost: h\r\nConnection: close\r\n\r\n')
    got = raw(listen, two)
    assert got.count(b'HTTP/1.1 200') == 3
    assert b'plain' in got and b'got hello' in got
    assert b'3\r\nabc\r\n4\r\ndefg\r\n0\r\n\r\n' in got
    assert ('POST', '/p', b'hello') in App.seen


def test_a_websocket_upgrade_becomes_a_tunnel(tmp_path):
    # A tiny "app" that answers an upgrade with 101 and then echoes bytes.
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    app_port = srv.getsockname()[1]

    def echo():
        conn, _ = srv.accept()
        data = b''
        while b'\r\n\r\n' not in data:
            data += conn.recv(4096)
        conn.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                     b'X-Frame-Options: DENY\r\n\r\n')
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            conn.sendall(chunk.upper())
        conn.close()

    threading.Thread(target=echo, daemon=True).start()
    proxies = ep.EmbedProxies(str(tmp_path / 'p.json'), '127.0.0.1')
    listen = free_port()
    proxies.sync({listen: app_port})
    try:
        with socket.create_connection(('127.0.0.1', listen), timeout=3) as s:
            s.sendall(b'GET /ws HTTP/1.1\r\nHost: h\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n')
            head = b''
            while b'\r\n\r\n' not in head:
                head += s.recv(4096)
            assert head.startswith(b'HTTP/1.1 101') and b'x-frame-options' not in head.lower()
            s.sendall(b'ping')
            assert s.recv(4096) == b'PING'
    finally:
        proxies.stop()
        srv.close()


def test_the_listeners_follow_the_wanted_ports_and_link_gets_the_list(stack):
    proxies, listen, app_port, tmp_path = stack
    assert json.loads((tmp_path / 'ports.json').read_text()) == {'ports': [listen]}
    other = free_port()
    proxies.sync({other: app_port})                                  # the first app is no longer shown
    assert json.loads((tmp_path / 'ports.json').read_text()) == {'ports': [other]}
    with pytest.raises(OSError):
        socket.create_connection(('127.0.0.1', listen), timeout=1)
    assert raw(other, b'GET /x HTTP/1.1\r\nHost: h\r\nConnection: close\r\n\r\n').endswith(b'plain')


def test_a_port_in_use_is_skipped_and_noted_not_fatal(tmp_path):
    taken = socket.socket()
    taken.bind(('127.0.0.1', 0))
    taken.listen(1)
    busy = taken.getsockname()[1]
    proxies = ep.EmbedProxies(str(tmp_path / 'p.json'), '127.0.0.1')
    try:
        proxies.sync({busy: 9})
        assert busy in proxies.failed and not proxies.servers
    finally:
        proxies.stop()
        taken.close()


def test_the_ports_and_the_policy_helper():
    assert ep.embed_port(8096) == 28096 and ep.embed_port(3001) == 23001
    assert ep.embed_port(50000) is None and ep.embed_port(0) is None
    assert ep._clean_csp("frame-ancestors 'none'") == ''
    assert ep._clean_csp("default-src 'self'; FRAME-ANCESTORS x y; script-src a") == "default-src 'self'; script-src a"
