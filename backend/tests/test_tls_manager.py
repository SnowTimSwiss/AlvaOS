"""The NAS's own HTTPS certificates (backend/tls_manager.py)."""

import os
import ssl
import stat

import pytest

pytest.importorskip("cryptography")
import tls_manager as tls  # noqa: E402


def test_authority_and_server_certificate_are_made_once(tmp_path):
    d = str(tmp_path / "tls")
    p = tls.ensure_certificates(d, names=["nas", "nas.local"], ips=["127.0.0.1", "192.168.1.20"])
    assert stat.S_IMODE(os.stat(p["ca.key"]).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(p["server.key"]).st_mode) == 0o600
    first = open(p["server.crt"]).read()
    tls.ensure_certificates(d, names=["nas", "nas.local"], ips=["127.0.0.1", "192.168.1.20"])
    assert open(p["server.crt"]).read() == first                 # nothing changed: kept
    about = tls.info(d)
    assert about["ready"] and about["names"] == ["nas", "nas.local"] and "192.168.1.20" in about["addresses"]
    assert len(about["fingerprint"].split(":")) == 32 and about["ports"]["web"] == 8443


def test_a_new_address_renews_the_server_certificate_but_not_the_authority(tmp_path):
    d = str(tmp_path / "tls")
    p = tls.ensure_certificates(d, names=["nas"], ips=["127.0.0.1", "192.168.1.20"])
    ca = open(p["ca.crt"]).read()
    server = open(p["server.crt"]).read()
    tls.ensure_certificates(d, names=["nas"], ips=["127.0.0.1", "10.0.0.7"])
    assert open(p["ca.crt"]).read() == ca and open(p["server.crt"]).read() != server
    assert "10.0.0.7" in tls.info(d)["addresses"]


def test_the_server_certificate_is_trusted_through_the_authority(tmp_path):
    d = str(tmp_path / "tls")
    p = tls.ensure_certificates(d, names=["nas", "localhost"], ips=["127.0.0.1"])
    server_ctx = tls.ssl_context(d)
    client_ctx = ssl.create_default_context(cafile=p["ca.crt"])
    import socket
    import threading
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve():
        conn, _ = listener.accept()
        with server_ctx.wrap_socket(conn, server_side=True) as s:
            s.sendall(b"hello")

    threading.Thread(target=serve, daemon=True).start()
    with socket.create_connection(("127.0.0.1", port)) as raw:
        with client_ctx.wrap_socket(raw, server_hostname="localhost") as s:
            assert s.recv(5) == b"hello"
    listener.close()


def test_info_before_anything_exists(tmp_path):
    assert tls.info(str(tmp_path / "none"))["ready"] is False
