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


def test_https_only_setting_is_read_when_it_changes(tmp_path):
    path = str(tmp_path / "https.json")
    assert tls.https_only(path) is False
    tls.set_https_only(True, path)
    assert tls.https_only(path) is True
    tls.set_https_only(False, path)
    os.utime(path, (1, 1))   # a new mtime, whatever the clock
    assert tls.https_only(path) is False


def test_the_authority_can_only_sign_for_home_names_and_private_addresses(tmp_path):
    d = str(tmp_path / "tls")
    p = tls.ensure_certificates(d, names=["nas", "nas.local", "localhost", "evil.example.com"],
                                ips=["127.0.0.1", "192.168.1.20", "8.8.8.8"])
    about = tls.info(d)
    assert "evil.example.com" not in about["names"] and "8.8.8.8" not in about["addresses"]
    assert {"nas", "nas.local", "localhost"} <= set(about["names"]) and "192.168.1.20" in about["addresses"]
    # Nothing to renew afterwards although the public name and address are "missing".
    first = open(p["server.crt"]).read()
    tls.ensure_certificates(d, names=["nas", "nas.local", "localhost", "evil.example.com"],
                            ips=["127.0.0.1", "192.168.1.20", "8.8.8.8"])
    assert open(p["server.crt"]).read() == first
    # And a certificate for a foreign name, signed with the stolen key, fails in a real TLS client.
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    ca_key = serialization.load_pem_private_key(open(p["ca.key"], "rb").read(), password=None)
    ca = x509.load_pem_x509_certificate(open(p["ca.crt"], "rb").read())
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    forged = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bank.example")]))
              .issuer_name(ca.subject).public_key(key.public_key()).serial_number(1)
              .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName("bank.example")]), critical=False)
              .sign(ca_key, hashes.SHA256()))
    forged_dir = tmp_path / "forged"
    forged_dir.mkdir()
    (forged_dir / "server.crt").write_bytes(forged.public_bytes(serialization.Encoding.PEM))
    (forged_dir / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                              serialization.NoEncryption()))
    server_ctx = tls.ssl_context(str(forged_dir))
    client_ctx = ssl.create_default_context(cafile=p["ca.crt"])
    import socket
    import threading
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True):
                pass
        except (ssl.SSLError, OSError):
            pass

    threading.Thread(target=serve, daemon=True).start()
    with socket.create_connection(listener.getsockname()) as raw:
        with pytest.raises(ssl.SSLCertVerificationError):
            client_ctx.wrap_socket(raw, server_hostname="bank.example")
    listener.close()


def _served_addresses(context, ca_file):
    """The addresses in the certificate a new connection gets from `context`."""
    import socket
    import threading
    a, b = socket.socketpair()
    server = threading.Thread(target=lambda: context.wrap_socket(a, server_side=True).close(), daemon=True)
    server.start()
    client_ctx = ssl.create_default_context(cafile=ca_file)
    client_ctx.check_hostname = False
    with client_ctx.wrap_socket(b) as tls_sock:
        cert = tls_sock.getpeercert()
    server.join(5)
    return {value for kind, value in cert.get("subjectAltName", ()) if kind == "IP Address"}


def test_a_running_server_gets_the_renewed_certificate(tmp_path, monkeypatch):
    d = str(tmp_path / "tls")
    monkeypatch.setattr(tls, "local_names", lambda: (["nas"], ["127.0.0.1", "192.168.1.20"]))
    tls.ensure_certificates(d)
    context = tls.ssl_context(d)
    seen = tls.refresh(context, d)
    assert "100.96.96.1" not in _served_addresses(context, tls.paths(d)["ca.crt"])
    # Remote access turns on: a new address appears while the server runs.
    monkeypatch.setattr(tls, "local_names", lambda: (["nas"], ["127.0.0.1", "192.168.1.20", "100.96.96.1"]))
    os.utime(tls.paths(d)["server.crt"], (1, 1))      # the file time must differ even within one second
    tls.refresh(context, d, seen)
    assert "100.96.96.1" in _served_addresses(context, tls.paths(d)["ca.crt"])
