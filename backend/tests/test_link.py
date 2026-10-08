"""AlvaOS Link (link_daemon.py): phones and buddies reach the NAS through iroh.

Two or three nodes in one process, no relay (loopback only): the way through the routers
is not what is tested here but who may use which service, what the local side sees, and
that pairing is the only thing a stranger can do."""

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

iroh = pytest.importorskip("iroh")

import link_daemon as ld  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeHub(ThreadingHTTPServer):
    """Stands in for the Hub: pairs the code "ABCD1234" and says who is asking."""

    def __init__(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._answer(200, {"you": self.client_address[0]})

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.seen.append((self.path, self.client_address[0], self.headers.get("X-Buddy-Secret")))
                if self.path == "/api/devices/pair":
                    ok = data.get("code") == "ABCD1234"
                    self._answer(200 if ok else 401, {"success": True, "user": "anna", "token": "t", "device": "dev1"}
                                 if ok else {"error": "This code is not valid"})
                else:
                    self._answer(200, {"success": True, "accepted": data.get("token")})
        super().__init__(("127.0.0.1", 0), Handler)
        self.seen = []
        threading.Thread(target=self.serve_forever, daemon=True).start()


class Echo:
    """A TCP service that answers "<source address> says: <what it was sent>"."""

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        data = await reader.read(100)
        peer = writer.get_extra_info("peername")[0]
        writer.write(f"{peer} says: ".encode() + data)
        await writer.drain()
        writer.close()


async def node(tmp_path, name, services, local_ports=None):
    link = ld.Link(ld.State(str(tmp_path / name)), services=services, local_ports=local_ports, direct_only=True)
    await link.start()
    return link


async def stranger(target, key=None):
    """Somebody with an iroh key who is not on the NAS's list."""
    options = iroh.EndpointOptions(preset=iroh.preset_n0_disable_relay(), bind_addr="127.0.0.1:0",
                                   secret_key=key or iroh.SecretKey.generate().to_bytes())
    endpoint = await iroh.Endpoint.bind(options)
    conn = await endpoint.connect(iroh.EndpointAddr(iroh.EndpointId.from_bytes(bytes.fromhex(target.node_id)), None,
                                                    target.address_hints()), ld.ALPN)
    return endpoint, conn


async def ask(conn, first_line, rest=b""):
    bi = await conn.open_bi()
    await bi.send().write_all(first_line.encode() + b"\n" + rest)
    await bi.send().finish()
    try:
        return await asyncio.wait_for(bi.recv().read_to_end(100000), 10)
    except Exception as exc:  # noqa: BLE001 - a refused stream ends with an error
        return exc


def test_alias_addresses_are_unique_and_loopback():
    seen = {ld.alias_for(i) for i in range(1, 600)}
    assert len(seen) == 599 and all(a.startswith("127.95.") for a in seen)
    assert ld.alias_for(1) == "127.95.1.1" and ld.alias_for(255) == "127.95.2.1"
    assert ld.PAIR_ALIAS not in seen


def test_a_stranger_can_only_pair_and_a_paired_phone_gets_the_hub(tmp_path):
    hub = FakeHub()

    async def scenario():
        nas = await node(tmp_path, "nas", {"hub": ("127.0.0.1", hub.server_address[1]),
                                           "api": ("127.0.0.1", hub.server_address[1]), "nbd": ("127.0.0.1", 1)})
        try:
            _, conn = await stranger(nas)
            # Nothing but pairing is open to somebody unknown.
            assert isinstance(await ask(conn, "hub", b"GET / HTTP/1.0\r\n\r\n"), Exception)
            assert isinstance(await ask(conn, "nbd"), Exception)
            assert isinstance(await ask(conn, "ping"), Exception)
            # A wrong code pairs nothing.
            wrong = json.loads(await ask(conn, "pair", json.dumps({"op": "device", "code": "NOPE0000"}).encode() + b"\n"))
            assert "not valid" in wrong["error"] and not nas.state.peers
            # The right one does, and the answer is the Hub's own (the token is for the app).
            right = json.loads(await ask(conn, "pair", json.dumps({"op": "device", "code": "ABCD1234",
                                                                  "device": {"name": "Pixel 8"}}).encode() + b"\n"))
            assert right["token"] == "t" and right["device"] == "dev1"
            (peer_id, peer), = nas.state.peers.items()
            assert peer["kind"] == "phone" and peer["name"] == "Pixel 8" and peer["device"] == "dev1"
            assert hub.seen[-1][1] == ld.PAIR_ALIAS            # the Hub saw the pairing address
            # Now the phone gets the Hub, seen from its own address; but not the backup services.
            answer = json.loads((await ask(conn, "hub", b"GET /x HTTP/1.0\r\n\r\n")).split(b"\r\n\r\n")[1])
            assert answer["you"] == peer["alias"]
            assert isinstance(await ask(conn, "nbd"), Exception) and isinstance(await ask(conn, "api"), Exception)
            assert await ask(conn, "ping") == b"pong\n"
            # Removing the device in the Hub takes the key off the list.
            assert nas.status()["peers"][0]["device"] == "dev1"
            nas.state.peers.pop(peer_id)
            assert isinstance(await ask(conn, "hub", b"GET / HTTP/1.0\r\n\r\n"), Exception)
        finally:
            await nas.stop()
    asyncio.run(scenario())


def test_pairing_is_slowed_down_for_strangers(tmp_path):
    hub = FakeHub()

    async def scenario():
        nas = await node(tmp_path, "nas", {"hub": ("127.0.0.1", hub.server_address[1]),
                                           "api": ("127.0.0.1", 1), "nbd": ("127.0.0.1", 1)})
        try:
            _, conn = await stranger(nas)
            tries = [json.loads(await ask(conn, "pair", json.dumps({"op": "device", "code": f"WRONG{i:03}"}).encode() + b"\n"))
                     for i in range(ld.PAIR_PER_MINUTE + 2)]
            assert "Too many" in tries[-1]["error"] and "not valid" in tries[0]["error"]
            junk = json.loads(await ask(conn, "pair", b"{not json}\n"))
            assert "Too many" in junk["error"]       # still within the same minute
        finally:
            await nas.stop()
    asyncio.run(scenario())


def test_buddies_are_neighbours_on_the_loopback_network(tmp_path):
    hub = FakeHub()

    async def scenario():
        # Two NAS; each has the other's nbd and api as a stand-in on its own side.
        echo_b = Echo()
        await echo_b.start()
        ports_a = {"nbd": free_port(), "api": free_port()}
        ports_b = {"nbd": free_port(), "api": free_port()}
        a = await node(tmp_path, "a", {"hub": ("127.0.0.1", 1), "api": ("127.0.0.1", 1), "nbd": ("127.0.0.1", 1)}, ports_a)
        b = await node(tmp_path, "b", {"hub": ("127.0.0.1", 1), "api": ("127.0.0.1", hub.server_address[1]),
                                       "nbd": ("127.0.0.1", echo_b.port)}, ports_b)
        try:
            a.hints[b.node_id] = b.address_hints()
            b.hints[a.node_id] = a.address_hints()
            a.state.set_buddies([{"id": b.node_id, "name": "Bella"}])
            b.state.set_buddies([{"id": a.node_id, "name": "Anton"}])
            await a.refresh_forwarders()
            await b.refresh_forwarders()
            alias_of_b = a.state.peers[b.node_id]["alias"]
            alias_of_a = b.state.peers[a.node_id]["alias"]

            # A connects to "the buddy" next door; B's service sees A's own address there.
            reader, writer = await asyncio.open_connection(alias_of_b, ports_a["nbd"])
            writer.write(b"hello")
            await writer.drain()
            reply = await asyncio.wait_for(reader.read(200), 10)
            assert reply == f"{alias_of_a} says: hello".encode()
            writer.close()

            # The api goes to B's backend (the fake Hub stands in), also from A's address.
            reader, writer = await asyncio.open_connection(alias_of_b, ports_a["api"])
            writer.write(b"GET / HTTP/1.0\r\n\r\n")
            await writer.drain()
            body = (await asyncio.wait_for(reader.read(1000), 10)).split(b"\r\n\r\n")[1]
            assert json.loads(body)["you"] == alias_of_a
            writer.close()

            # Taking the buddy off the list closes the way in both directions.
            b.state.set_buddies([])
            _, conn = await stranger(b, key=None)
            assert isinstance(await ask(conn, "nbd"), Exception)
            a.state.set_buddies([])
            await a.refresh_forwarders()
            with pytest.raises(OSError):
                await asyncio.open_connection(alias_of_b, ports_a["nbd"])
        finally:
            await a.stop()
            await b.stop()
    asyncio.run(scenario())


def test_the_buddy_pairing_request_reaches_the_backend_with_the_secret(tmp_path):
    hub = FakeHub()

    async def scenario():
        nas = await node(tmp_path, "nas", {"hub": ("127.0.0.1", 1), "nbd": ("127.0.0.1", 1),
                                           "api": ("127.0.0.1", hub.server_address[1])})
        try:
            _, conn = await stranger(nas)
            answer = json.loads(await ask(conn, "pair", json.dumps({"op": "buddy", "secret": "s3cret", "token": "TOK"}).encode() + b"\n"))
            assert answer["accepted"] == "TOK"
            assert hub.seen[-1] == ("/api/v1/backup/pairing/accept", ld.PAIR_ALIAS, "s3cret")
            assert not nas.state.peers                         # the backup code decides who becomes a buddy
        finally:
            await nas.stop()
    asyncio.run(scenario())


def test_the_state_files_keep_the_key_and_the_peers(tmp_path):
    state = ld.State(str(tmp_path / "s"))
    key = state.secret_key()
    assert len(key) == 32 and state.secret_key() == key
    state.add_peer("a" * 64, "phone", "Pixel", device="d1")
    state.set_buddies([{"id": "b" * 64, "name": "Bella"}])
    again = ld.State(str(tmp_path / "s"))
    assert {p["kind"] for p in again.peers.values()} == {"phone", "buddy"}
    assert again.peers["a" * 64]["alias"] != again.peers["b" * 64]["alias"]
    again.set_buddies([])
    assert list(again.peers) == ["a" * 64]
    token = state.control_token()
    assert len(token) >= 32 and ld.State(str(tmp_path / "s")).control_token() == token


def test_the_control_api_needs_its_token_and_manages_the_peers(tmp_path, monkeypatch):
    import link_client

    async def scenario():
        nas = await node(tmp_path, "nas", {"hub": ("127.0.0.1", 1), "api": ("127.0.0.1", 1), "nbd": ("127.0.0.1", 1)},
                         {"nbd": free_port(), "api": free_port()})
        port = free_port()
        token = nas.state.control_token()
        server = ld.control_server(nas, asyncio.get_running_loop(), token, ("127.0.0.1", port))
        monkeypatch.setattr(link_client, "BASE", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(link_client, "TOKEN_FILE", str(tmp_path / "nas" / "control_token"))
        loop = asyncio.get_running_loop()

        def call(fn, *args):
            return loop.run_in_executor(None, fn, *args)
        try:
            found = await call(link_client.status)
            assert found["running"] and found["node_id"] == nas.node_id and found["peers"] == []
            assert await call(link_client.node_id) == nas.node_id
            both = await call(link_client.set_buddies, [{"id": "c" * 64, "name": "Carl"}])
            assert both["peers"][0]["alias"] == "127.95.1.1" and both["peers"][0]["kind"] == "buddy"
            assert (await call(link_client.set_buddies, [{"id": "nonsense", "name": "x"}]))["error"]
            nas.state.add_peer("d" * 64, "phone", "Pixel", device="dev9")
            assert await call(link_client.remove_device, "dev9")
            assert [p["kind"] for p in nas.status()["peers"]] == ["buddy"]
            off = await call(link_client.set_enabled, False)
            assert off["running"] is False and off["enabled"] is False
            on = await call(link_client.set_enabled, True)
            assert on["running"] is True
            monkeypatch.setattr(link_client, "_token", lambda: "wrong")
            assert (await call(link_client.status))["error"] == "Not allowed."
        finally:
            server.shutdown()
            await nas.stop()
    asyncio.run(scenario())


def test_two_nas_pair_and_ping_each_other_and_a_new_install_takes_over_the_key(tmp_path, monkeypatch):
    import link_client
    hub = FakeHub()

    async def scenario():
        a = await node(tmp_path, "a", {"hub": ("127.0.0.1", 1), "nbd": ("127.0.0.1", 1), "api": ("127.0.0.1", hub.server_address[1])},
                       {"nbd": free_port(), "api": free_port()})
        b = await node(tmp_path, "b", {"hub": ("127.0.0.1", 1), "nbd": ("127.0.0.1", 1), "api": ("127.0.0.1", 1)},
                       {"nbd": free_port(), "api": free_port()})
        try:
            b.hints[a.node_id] = a.address_hints()
            # B pairs with A through Link (a stranger to A), the request ends in A's backend.
            answer = await b.remote_pair(a.node_id, {"op": "buddy", "secret": "sss", "token": "T"})
            assert answer["accepted"] == "T" and hub.seen[-1][2] == "sss"
            # Both let each other in (the backup code does this), then a ping goes through.
            a.state.set_buddies([{"id": b.node_id, "name": "B"}])
            b.state.set_buddies([{"id": a.node_id, "name": "A"}])
            a.hints[b.node_id] = b.address_hints()
            ok = await b.ping(a.node_id)
            assert ok["ok"] and ok["ms"] >= 0
            assert (await b.ping("e" * 64))["ok"] is False                  # not on the list
            # The key can be exported and a new install takes it over: same address again.
            port = free_port()
            token = a.state.control_token()
            server = ld.control_server(a, asyncio.get_running_loop(), token, ("127.0.0.1", port))
            monkeypatch.setattr(link_client, "BASE", f"http://127.0.0.1:{port}")
            monkeypatch.setattr(link_client, "TOKEN_FILE", str(tmp_path / "a" / "control_token"))
            loop = asyncio.get_running_loop()
            try:
                secret = await loop.run_in_executor(None, link_client.secret_key)
                assert len(secret) == 64
                before = a.node_id
                new_key = "ab" * 32
                assert await loop.run_in_executor(None, link_client.import_secret_key, new_key)
                assert a.node_id != before and a.state.secret_key().hex() == new_key
                assert await loop.run_in_executor(None, link_client.import_secret_key, secret)
                assert a.node_id == before
            finally:
                server.shutdown()
        finally:
            await a.stop()
            await b.stop()
    asyncio.run(scenario())
