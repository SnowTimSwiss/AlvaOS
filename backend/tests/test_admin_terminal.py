"""The admin terminal in the browser (backend/admin_terminal.py)."""

import asyncio
import base64
import json
import os
import struct

import pytest

import admin_terminal as at


def masked(opcode, payload):
    mask = os.urandom(4)
    n = len(payload)
    head = struct.pack("!BB", 0x80 | opcode, 0x80 | n) if n < 126 else struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


async def read_frame(reader):
    b1, b2 = await reader.readexactly(2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    return b1 & 0x0F, await reader.readexactly(n)


async def connect(port, ticket, origin="http://nas.local:8080"):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write((f"GET /?ticket={ticket} HTTP/1.1\r\nHost: nas.local:{port}\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                  f"Origin: {origin}\r\n\r\n").encode())
    await writer.drain()
    status = (await reader.readuntil(b"\r\n\r\n")).split(b"\r\n")[0]
    return reader, writer, status, key


async def collect(reader, until, limit=5.0):
    out, closed = b"", None
    async def run():
        nonlocal out, closed
        while until not in out and closed is None:
            op, data = await read_frame(reader)
            if op == 2:
                out += data
            elif op == 1:
                closed = json.loads(data)["closed"]
            elif op == 8:
                break
    await asyncio.wait_for(run(), limit)
    return out, closed


@pytest.fixture
def server(monkeypatch):
    signed_in = {"good"}
    at.setup(lambda token: token in signed_in)
    monkeypatch.setattr(at, "CHECK_SECONDS", 0.1)

    async def start():
        srv = await asyncio.start_server(lambda r, w: at.handle(r, w, argv=["/bin/sh"]), "127.0.0.1", 0)
        return srv, srv.sockets[0].getsockname()[1]
    return start, signed_in


def test_a_shell_runs_after_the_ticket_and_ends_with_the_connection(server):
    start, _ = server

    async def run():
        srv, port = await start()
        reader, writer, status, key = await connect(port, at.issue("good"))
        assert status == b"HTTP/1.1 101 Switching Protocols"
        writer.write(masked(1, json.dumps({"resize": {"cols": 120, "rows": 40}}).encode()))
        writer.write(masked(2, b"stty size; echo hello-$((40+2))\n"))
        out, _ = await collect(reader, b"hello-42")
        assert b"40 120" in out and b"hello-42" in out
        writer.write(masked(9, b"ping"))
        writer.write(masked(2, b"exit\n"))
        _, closed = await collect(reader, b"never")
        assert closed == "The shell ended."
        writer.close()
        srv.close()
    asyncio.run(run())


def test_no_shell_without_a_good_ticket_or_from_another_website(server):
    start, signed_in = server

    async def run():
        srv, port = await start()
        assert (await connect(port, "made-up"))[2].startswith(b"HTTP/1.1 403")
        ticket = at.issue("good")
        assert (await connect(port, ticket, origin="https://evil.example"))[2].startswith(b"HTTP/1.1 403")
        assert (await connect(port, ticket))[2].endswith(b"101 Switching Protocols")
        assert (await connect(port, ticket))[2].startswith(b"HTTP/1.1 403")        # used once already
        signed_in.discard("good")
        assert (await connect(port, at.issue("good")))[2].startswith(b"HTTP/1.1 403")   # signed out since
        srv.close()
    asyncio.run(run())


def test_signing_out_closes_the_terminal(server):
    start, signed_in = server

    async def run():
        srv, port = await start()
        reader, writer, status, _ = await connect(port, at.issue("good"))
        assert status.endswith(b"101 Switching Protocols")
        signed_in.discard("good")
        _, closed = await collect(reader, b"never")
        assert "signed out" in closed
        srv.close()
    asyncio.run(run())


def test_only_a_few_at_once_and_no_big_messages(server, monkeypatch):
    start, _ = server
    monkeypatch.setattr(at, "MAX_SESSIONS", 1)

    async def run():
        srv, port = await start()
        reader, writer, status, _ = await connect(port, at.issue("good"))
        assert status.endswith(b"101 Switching Protocols")
        assert (await connect(port, at.issue("good")))[2].startswith(b"HTTP/1.1 503")
        big = b"y" * (at.MAX_FRAME + 1)
        mask = os.urandom(4)
        writer.write(struct.pack("!BBQ", 0x82, 0x80 | 127, len(big)) + mask)
        await writer.drain()
        _, closed = await collect(reader, b"never")
        assert closed is None                                  # the connection just ends
        srv.close()
    asyncio.run(run())


def test_the_shell_gets_a_plain_environment(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "x")
    env = at._clean_env()
    assert "SECRET_TOKEN" not in env and env["TERM"] == "xterm-256color"
