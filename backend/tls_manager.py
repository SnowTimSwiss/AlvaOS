#!/usr/bin/env python3
"""HTTPS on the home network: a certificate authority of this NAS alone, and a
server certificate it signs for the NAS's names and addresses.

People trust the authority once on each device (Settings › Security explains
how); after that every page of the NAS opens without a warning, also when the
server certificate is renewed because the NAS got a new address or name.

The plain HTTP ports keep working. HTTPS runs next to them:
  web interface 8443, Files 9443, Files over WebDAV 9444.
Waitress (the HTTP server) cannot do TLS, so these ports use Werkzeug's
threaded server with a per-connection timeout.
"""

import datetime
import ipaddress
import os
import socket
import ssl
import threading
from typing import Any, Dict, List, Optional, Tuple

TLS_DIR = '/var/lib/alvaos/tls'
CA_DAYS = 3650
SERVER_DAYS = 825
RENEW_DAYS = 30
CONNECTION_TIMEOUT = 120
PORTS = {'web': 8443, 'files': 9443, 'dav': 9444}

_lock = threading.Lock()

# What the authority may sign for at all (X.509 name constraints, enforced by
# browsers): this NAS's own name, home-network domains and private addresses.
# A stolen authority key can then not be used to impersonate other websites.
PERMITTED_DOMAINS = ('local', 'lan', 'home', 'home.arpa', 'internal', 'localhost')
PERMITTED_NETWORKS = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '100.64.0.0/10',
                      '169.254.0.0/16')


def paths(tls_dir: Optional[str] = None) -> Dict[str, str]:
    base = tls_dir or TLS_DIR
    return {name: os.path.join(base, name) for name in ('ca.crt', 'ca.key', 'server.crt', 'server.key')}


def local_names() -> Tuple[List[str], List[str]]:
    """(DNS names, IP addresses) the NAS is reached by on the network."""
    host = socket.gethostname().split('.')[0] or 'alvaos'
    names = sorted({host, f'{host}.local', 'localhost'})
    ips = {'127.0.0.1'}
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith('169.254.'):
                    ips.add(addr.address)
    except Exception:  # noqa: BLE001 - psutil missing or no interfaces: localhost only
        pass
    return names, sorted(ips)


def _write_private(path: str, data: bytes) -> None:
    tmp = f'{path}.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _write_public(path: str, data: bytes) -> None:
    tmp = f'{path}.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _make_ca(p: Dict[str, str], host: str):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'AlvaOS {host} local authority'),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'AlvaOS')])
    now = _now()
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.NameConstraints(
                permitted_subtrees=[x509.DNSName(host)] + [x509.DNSName(d) for d in PERMITTED_DOMAINS]
                + [x509.IPAddress(ipaddress.ip_network(n)) for n in PERMITTED_NETWORKS],
                excluded_subtrees=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write_private(p['ca.key'], key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
    _write_public(p['ca.crt'], cert.public_bytes(serialization.Encoding.PEM))


def _load_cert(path: str):
    from cryptography import x509
    try:
        with open(path, 'rb') as f:
            return x509.load_pem_x509_certificate(f.read())
    except (OSError, ValueError):
        return None


def _cert_names(cert) -> Tuple[List[str], List[str]]:
    from cryptography import x509
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return [], []
    return (sorted(san.get_values_for_type(x509.DNSName)),
            sorted(str(ip) for ip in san.get_values_for_type(x509.IPAddress)))


def _not_after(cert) -> datetime.datetime:
    value = getattr(cert, 'not_valid_after_utc', None)
    return value if value is not None else cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)


def permitted(ca, names: List[str], ips: List[str]) -> Tuple[List[str], List[str]]:
    """The names and addresses the authority may sign for (all of them for an
    authority made before name constraints)."""
    from cryptography import x509
    try:
        constraints = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return names, ips
    trees = constraints.permitted_subtrees or []
    domains = [str(t.value).lower() for t in trees if isinstance(t, x509.DNSName)]
    networks = [t.value for t in trees if isinstance(t, x509.IPAddress)
                and isinstance(t.value, (ipaddress.IPv4Network, ipaddress.IPv6Network))]
    ok_names = [n for n in names if any(n.lower() == d or n.lower().endswith('.' + d) for d in domains)]
    ok_ips = [ip for ip in ips if any(ipaddress.ip_address(ip) in net for net in networks)]
    return ok_names, ok_ips


def _make_server_cert(p: Dict[str, str], names: List[str], ips: List[str]) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    with open(p['ca.key'], 'rb') as f:
        ca_key = serialization.load_pem_private_key(f.read(), password=None)
    if not isinstance(ca_key, ec.EllipticCurvePrivateKey):
        raise ValueError('The authority key is not the kind AlvaOS makes.')
    ca = _load_cert(p['ca.crt'])
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now()
    names, ips = permitted(ca, names, ips)
    san = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0] if names else 'alvaos')]))
            .issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=SERVER_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_private(p['server.key'], key.private_bytes(serialization.Encoding.PEM,
                                                      serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()))
    _write_public(p['server.crt'], cert.public_bytes(serialization.Encoding.PEM))


def ensure_certificates(tls_dir: Optional[str] = None, names: Optional[List[str]] = None,
                        ips: Optional[List[str]] = None) -> Dict[str, str]:
    """Make the authority once, and a server certificate whenever it is
    missing, ends within RENEW_DAYS, or does not cover the current names and
    addresses. Returns the file paths."""
    p = paths(tls_dir)
    if names is None or ips is None:
        found_names, found_ips = local_names()
        names = names if names is not None else found_names
        ips = ips if ips is not None else found_ips
    os.makedirs(os.path.dirname(p['ca.crt']), mode=0o700, exist_ok=True)
    import fcntl
    with _lock, open(os.path.join(os.path.dirname(p['ca.crt']), '.lock'), 'w') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)   # the backend and Files start at the same time
        if _load_cert(p['ca.crt']) is None or not os.path.exists(p['ca.key']):
            _make_ca(p, names[0] if names else 'alvaos')
            if os.path.exists(p['server.crt']):
                os.remove(p['server.crt'])
        server = _load_cert(p['server.crt'])
        renew = server is None or not os.path.exists(p['server.key'])
        if not renew:
            have_names, have_ips = _cert_names(server)
            want_names, want_ips = permitted(_load_cert(p['ca.crt']), names, ips)
            renew = (_not_after(server) - _now() < datetime.timedelta(days=RENEW_DAYS)
                     or not set(want_names) <= set(have_names) or not set(want_ips) <= set(have_ips))
        if renew:
            _make_server_cert(p, names, ips)
    return p


def info(tls_dir: Optional[str] = None) -> Dict[str, Any]:
    """What the settings page shows: fingerprint of the authority, names and
    addresses covered, and when the server certificate ends."""
    from cryptography.hazmat.primitives import hashes
    p = paths(tls_dir)
    ca = _load_cert(p['ca.crt'])
    server = _load_cert(p['server.crt'])
    if ca is None or server is None:
        return {'ready': False, 'ports': PORTS}
    fingerprint = ca.fingerprint(hashes.SHA256()).hex().upper()
    names, ips = _cert_names(server)
    return {'ready': True, 'ports': PORTS, 'names': names, 'addresses': ips,
            'authority': ca.subject.rfc4514_string(),
            'fingerprint': ':'.join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2)),
            'server_valid_until': _not_after(server).isoformat(),
            'authority_valid_until': _not_after(ca).isoformat()}


def ssl_context(tls_dir: Optional[str] = None) -> ssl.SSLContext:
    p = paths(tls_dir)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(p['server.crt'], p['server.key'])
    return context


def serve_in_background(app, port: int, name: str, tls_dir: Optional[str] = None) -> Optional[threading.Thread]:
    """HTTPS for a WSGI app on `port`, next to its plain HTTP port."""
    try:
        from werkzeug.serving import WSGIRequestHandler, make_server
        ensure_certificates(tls_dir)

        class Handler(WSGIRequestHandler):
            timeout = CONNECTION_TIMEOUT   # a connection that sends nothing is closed

            def log_request(self, code: Any = '-', size: Any = '-') -> None:
                pass   # the HTTP side logs already

        server = make_server('0.0.0.0', port, app, threaded=True, request_handler=Handler,
                             ssl_context=ssl_context(tls_dir))
    except Exception as e:  # noqa: BLE001 - HTTPS is extra; HTTP keeps working without it
        print(f'HTTPS for {name} not started on port {port}: {e}')
        return None
    thread = threading.Thread(target=server.serve_forever, name=f'https-{name}', daemon=True)
    thread.start()
    print(f'{name} also on https port {port}')
    return thread


# ── "HTTPS only" ─────────────────────────────────────────────────────────────
# When on, the plain HTTP ports send people to HTTPS. Off by default: a device
# that has not trusted the authority yet would only see a warning page.

SETTINGS_FILE = '/var/lib/alvaos/https.json'
_settings_cache: Dict[str, Any] = {'mtime': None, 'only': False}


def https_only(path: Optional[str] = None) -> bool:
    path = path or SETTINGS_FILE
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return False
    if _settings_cache['mtime'] != (path, mtime):
        try:
            import json
            with open(path) as f:
                _settings_cache['only'] = bool(json.load(f).get('only'))
        except (OSError, ValueError, AttributeError):
            _settings_cache['only'] = False
        _settings_cache['mtime'] = (path, mtime)
    return bool(_settings_cache['only'])


def set_https_only(only: bool, path: Optional[str] = None) -> None:
    import json
    path = path or SETTINGS_FILE
    tmp = f'{path}.tmp'
    with open(tmp, 'w') as f:
        json.dump({'only': bool(only)}, f)
    os.replace(tmp, path)


def redirect_to_https(request, port: int, keep: Tuple[str, ...] = ()):
    """For a plain-HTTP request while "HTTPS only" is on: where to send it,
    or None to let it through (HTTPS already, or one of `keep`)."""
    if request.is_secure or not https_only() or request.path in keep:
        return None
    host = (request.host or '').rsplit(':', 1)[0] if not (request.host or '').endswith(']') else request.host
    query = ('?' + request.query_string.decode('latin-1')) if request.query_string else ''
    return f'https://{host}:{port}{request.path}{query}'
