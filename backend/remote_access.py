#!/usr/bin/env python3
"""Remote access: reach this NAS from anywhere over WireGuard.

Settings › Remote access turns it on. Each phone or computer is added by
name; it gets a WireGuard configuration as a QR code (phones) or a file
(computers), shown once. The device's private key is made here and never
stored; the NAS keeps only its public key and a pre-shared key.

The tunnel reaches the NAS itself (100.96.96.1), nothing else on the home
network. The router has to forward one UDP port (51821 by default) to the
NAS; without that, nothing from outside can knock.

Buddy Backup has its own tunnel (buddy0, 100.95.95.x, port 51820); this one
is remote0. The privilege helper runs wg-quick only for these two files and
refuses PostUp and other shell hooks in them.
"""

import base64
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

WG_DIR = '/var/lib/alvaos/wireguard'
CONFIG_PATH = os.path.join(WG_DIR, 'remote0.conf')
SETTINGS_FILE = '/var/lib/alvaos/remote_access.json'
INTERFACE = 'remote0'
NET_PREFIX = '100.96.96'
NAS_ADDRESS = f'{NET_PREFIX}.1'
DEFAULT_PORT = 51821
MAX_DEVICES = 50
NAME_RE = re.compile(r'^[\w .\'()-]{1,40}$', re.UNICODE)
HOST_RE = re.compile(r'^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')
KEY_RE = re.compile(r'^[A-Za-z0-9+/]{43}=$')
WEB_PORT = 8080
UPNP_NAME = 'AlvaOS remote access'
UPNP_LEASE_FALLBACK = 7 * 24 * 3600   # for routers that refuse permanent mappings
CGNAT = ipaddress.ip_network('100.64.0.0/10')
DUCKDNS_URL = 'https://www.duckdns.org/update'
DUCKDNS_DOMAIN_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')
DUCKDNS_TOKEN_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
CHECK_SECONDS = 600            # how often the dynamic address is updated
UPNP_EVERY = 24 * 3600         # and the router asked again

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_keypair() -> Tuple[str, str]:
    """(private, public) as WireGuard writes them: 32 bytes, base64."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    key = X25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def public_key_of(private_b64: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    key = X25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                          serialization.PublicFormat.Raw)).decode()


def check_endpoint(host: str) -> bool:
    """A public name (like home.example.net) or an address, nothing else."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(HOST_RE.match(host or '')) and '.' in host


def qr_svg(text: str) -> str:
    """The configuration as a QR code (SVG, as a data: URL), or '' without the library."""
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ''
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    return 'data:image/svg+xml;base64,' + base64.b64encode(image.to_string()).decode()


class RemoteAccess:
    def __init__(self, run_command: Callable, settings_path: Optional[str] = None,
                 config_path: Optional[str] = None, interface_up: Optional[Callable[[], bool]] = None,
                 lan_addresses: Optional[Callable[[], List[str]]] = None,
                 run_local: Optional[Callable[[List[str]], str]] = None,
                 http_get: Optional[Callable[..., Any]] = None):
        self.run = run_command
        self.run_local = run_local or _run_local   # upnpc needs no privileges
        self.http_get = http_get
        self.settings_path = settings_path or SETTINGS_FILE
        self.config_path = config_path or CONFIG_PATH
        self.interface_up = interface_up or (lambda: os.path.exists(f'/sys/class/net/{INTERFACE}'))
        self.lan_addresses = lan_addresses or _lan_addresses

    # ── State ────────────────────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        try:
            with open(self.settings_path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        devices = [d for d in data.get('devices') or [] if isinstance(d, dict) and KEY_RE.match(str(d.get('public_key')))]
        try:
            port = int(data.get('port') or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        raw_duck = data.get('duckdns')
        duck: Dict[str, Any] = raw_duck if isinstance(raw_duck, dict) else {}
        return {'enabled': bool(data.get('enabled')), 'endpoint': str(data.get('endpoint') or ''),
                'upnp': bool(data.get('upnp')),
                'duckdns': {k: str(duck.get(k) or '') for k in ('domain', 'token', 'last_update', 'last_error')},
                'port': port if 1024 <= port <= 65535 else DEFAULT_PORT,
                'private_key': str(data.get('private_key') or ''), 'devices': devices}

    def _save(self, state: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        tmp = f'{self.settings_path}.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # holds the NAS's private key
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f, indent=1)
        os.replace(tmp, self.settings_path)

    def _server_key(self, state: Dict[str, Any]) -> str:
        if not KEY_RE.match(state.get('private_key') or ''):
            state['private_key'], _ = new_keypair()
        return state['private_key']

    def status(self) -> Dict[str, Any]:
        state = self.load()
        seen = self._handshakes() if state['enabled'] else {}
        devices = [{'id': d.get('id'), 'name': d.get('name'), 'address': d.get('address'),
                    'created_at': d.get('created_at'), 'last_seen': seen.get(d['public_key'])}
                   for d in state['devices']]
        return {'enabled': state['enabled'], 'running': state['enabled'] and self.interface_up(),
                'endpoint': state['endpoint'], 'port': state['port'], 'nas_address': NAS_ADDRESS,
                'open_url': f'http://{NAS_ADDRESS}:{WEB_PORT}', 'lan_addresses': self.lan_addresses(),
                'duckdns': {'domain': state['duckdns']['domain'], 'last_update': state['duckdns']['last_update'],
                            'last_error': state['duckdns']['last_error']},   # never the token
                'devices': devices, 'wireguard_installed': bool(_wg_quick()), 'upnp': state['upnp'],
                'upnp_installed': bool(_upnpc())}

    # ── Settings ─────────────────────────────────────────────────────────

    def configure(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        with _lock:
            state = self.load()
            if 'endpoint' in payload:
                endpoint = str(payload.get('endpoint') or '').strip().lower()
                if endpoint and not check_endpoint(endpoint):
                    return False, 'Enter the public address of your home: a name like home.example.net or an address.'
                state['endpoint'] = endpoint
            if 'port' in payload:
                try:
                    port = int(str(payload.get('port')))
                except (TypeError, ValueError):
                    port = 0
                if not 1024 <= port <= 65535 or port == 51820:
                    return False, 'Choose a port between 1024 and 65535 (51820 is used by Buddy Backup).'
                state['port'] = port
            duck_changed = False
            if 'duckdns' in payload:
                duck = payload.get('duckdns') or {}
                if not duck or not str(duck.get('domain') or '').strip():
                    state['duckdns'] = {'domain': '', 'token': '', 'last_update': '', 'last_error': ''}
                else:
                    domain = str(duck.get('domain') or '').strip().lower().removesuffix('.duckdns.org')
                    token = str(duck.get('token') or '').strip().lower() or state['duckdns']['token']
                    if not DUCKDNS_DOMAIN_RE.match(domain):
                        return False, 'Enter the DuckDNS name, like "myhome" for myhome.duckdns.org.'
                    if not DUCKDNS_TOKEN_RE.match(token):
                        return False, 'Enter the token from duckdns.org (it looks like a1b2c3d4-…).'
                    state['duckdns'] = {'domain': domain, 'token': token, 'last_update': '', 'last_error': ''}
                    state['endpoint'] = f'{domain}.duckdns.org'
                    duck_changed = True
            if 'enabled' in payload:
                state['enabled'] = bool(payload.get('enabled'))
            if state['enabled'] and not _wg_quick():
                return False, 'WireGuard is not installed on this NAS (package wireguard-tools).'
            old_port = self.load()['port']
            port_changed = state['port'] != old_port
            self._server_key(state)
            self._save(state)
            ok, message = self._apply(state)
        if duck_changed:
            self.update_duckdns()
        if ok and port_changed and state['upnp'] and _upnpc():
            self.run_local([_upnpc() or 'upnpc', '-d', str(old_port), 'UDP'])   # no forgotten open port
            if state['enabled']:
                self.open_router_port()
        return ok, message

    def _apply(self, state: Dict[str, Any]) -> Tuple[bool, str]:
        """Write the config and bring the tunnel up (or down when off)."""
        wg_quick = _wg_quick()
        if not wg_quick:
            return (not state['enabled']), 'WireGuard is not installed on this NAS.'
        if self.interface_up():
            self.run([wg_quick, 'down', self.config_path], timeout=30)
        if not state['enabled']:
            return True, 'Remote access is off.'
        self._write_config(state)
        res, err = self.run([wg_quick, 'up', self.config_path], timeout=60)
        if err or not res or res.returncode != 0:
            detail = (err or (res.stderr if res else '') or '').strip().splitlines()[-1:] or ['']
            return False, f'The tunnel could not start: {detail[0]}'
        return True, 'Remote access is on.'

    def render_config(self, state: Dict[str, Any]) -> str:
        lines = ['# AlvaOS remote access. Written by AlvaOS; changes here are replaced.',
                 '[Interface]', f'PrivateKey = {self._server_key(state)}', f'Address = {NAS_ADDRESS}/24',
                 f'ListenPort = {state["port"]}']
        for device in state['devices']:
            lines += ['', '[Peer]', f'PublicKey = {device["public_key"]}']
            if KEY_RE.match(str(device.get('preshared_key') or '')):
                lines.append(f'PresharedKey = {device["preshared_key"]}')
            lines.append(f'AllowedIPs = {device["address"]}/32')
        return '\n'.join(lines) + '\n'

    def _write_config(self, state: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.config_path), mode=0o700, exist_ok=True)
        tmp = f'{self.config_path}.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(self.render_config(state))
        os.replace(tmp, self.config_path)

    def start(self, stop: Optional[threading.Event] = None) -> None:
        """At startup: wg-quick state does not survive a reboot. Runs on in
        its own thread to keep the router's port mapping (UPnP) alive."""
        state = self.load()
        if state['enabled'] and not self.interface_up():
            ok, message = self._apply(state)
            if not ok:
                print(f'Remote access: {message}')
        # The dynamic address is kept up to date every few minutes; a router
        # forgets port mappings when it restarts, so it is asked every day.
        stop = stop or threading.Event()
        last_upnp = 0.0
        while True:
            state = self.load()
            if state['duckdns']['domain']:
                self.update_duckdns()
            now = time.monotonic()
            if state['enabled'] and state['upnp'] and (not last_upnp or now - last_upnp >= UPNP_EVERY):
                last_upnp = now
                ok, message, _ = self.open_router_port()
                if not ok:
                    print(f'Remote access, router: {message}')
            if stop.wait(CHECK_SECONDS):
                return

    def update_duckdns(self) -> Tuple[bool, str]:
        """Tell DuckDNS the home's current address (it sees it from the request)."""
        with _lock:
            state = self.load()
            duck = state['duckdns']
            if not duck['domain'] or not duck['token']:
                return False, 'DuckDNS is not set up.'
        get = self.http_get
        if get is None:
            import requests
            get = requests.get
        try:
            res = get(DUCKDNS_URL, params={'domains': duck['domain'], 'token': duck['token'], 'ip': ''}, timeout=15)
            answer = str(getattr(res, 'text', '')).strip()
        except Exception as e:  # noqa: BLE001 - offline, DNS, TLS
            answer, error = '', f'DuckDNS could not be reached ({type(e).__name__}).'
        else:
            error = '' if answer.startswith('OK') else \
                'DuckDNS refused the update: check the name and the token.'
        with _lock:
            state = self.load()
            if state['duckdns']['domain'] != duck['domain']:
                return False, 'Changed meanwhile.'
            state['duckdns']['last_error'] = error
            if not error:
                state['duckdns']['last_update'] = _now()
            self._save(state)
        return not error, error or f'{duck["domain"]}.duckdns.org points to your home.'

    # ── Devices ──────────────────────────────────────────────────────────

    def add_device(self, name: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """A new phone or computer. Returns its configuration, shown once."""
        name = str(name or '').strip()
        if not NAME_RE.match(name):
            return None, 'Give the device a name, like "Anna\'s phone" (up to 40 letters).'
        with _lock:
            state = self.load()
            if not state['enabled']:
                return None, 'Turn on remote access first.'
            if not state['endpoint']:
                return None, 'Enter the public address of your home first.'
            if len(state['devices']) >= MAX_DEVICES:
                return None, f'Remote access is for up to {MAX_DEVICES} devices.'
            if any(str(d.get('name')).lower() == name.lower() for d in state['devices']):
                return None, f'There is already a device called "{name}".'
            used = {str(d.get('address')) for d in state['devices']}
            address = next((f'{NET_PREFIX}.{n}' for n in range(2, 255) if f'{NET_PREFIX}.{n}' not in used), '')
            if not address:
                return None, 'No address left for another device.'
            private, public = new_keypair()
            preshared = base64.b64encode(secrets.token_bytes(32)).decode()
            device = {'id': secrets.token_hex(6), 'name': name, 'public_key': public, 'preshared_key': preshared,
                      'address': address, 'created_at': _now()}
            state['devices'].append(device)
            server_public = public_key_of(self._server_key(state))
            self._save(state)
            ok, message = self._apply(state)
            if not ok:
                state['devices'].remove(device)
                self._save(state)
                self._apply(state)
                return None, message
        host = f'[{state["endpoint"]}]' if ':' in state['endpoint'] else state['endpoint']
        config = '\n'.join([
            '[Interface]', f'PrivateKey = {private}', f'Address = {address}/32', '',
            '[Peer]', f'PublicKey = {server_public}', f'PresharedKey = {preshared}',
            f'Endpoint = {host}:{state["port"]}', f'AllowedIPs = {NAS_ADDRESS}/32', 'PersistentKeepalive = 25', ''])
        return {'id': device['id'], 'name': name, 'address': address, 'config': config,
                'file_name': _file_name(name), 'qr': qr_svg(config),
                'open_url': f'http://{NAS_ADDRESS}:{WEB_PORT}'}, ''

    def remove_device(self, device_id: str) -> Tuple[bool, str]:
        with _lock:
            state = self.load()
            kept = [d for d in state['devices'] if d.get('id') != device_id]
            if len(kept) == len(state['devices']):
                return False, 'This device is not on the list any more.'
            state['devices'] = kept
            self._save(state)
            ok, message = self._apply(state)
            return ok, ('The device can no longer connect.' if ok else message)

    # ── The router ───────────────────────────────────────────────────────

    def router(self) -> Dict[str, Any]:
        """What the router says over UPnP: is it there, the NAS's address on
        the home network and the router's own internet address."""
        upnpc = _upnpc()
        if not upnpc:
            return {'found': False}
        out = self.run_local([upnpc, '-s'])
        lan = re.search(r'Local LAN ip address\s*:\s*([0-9.]+)', out)
        wan = re.search(r'ExternalIPAddress\s*=\s*([0-9a-fA-F.:]+)', out)
        return {'found': 'Found valid IGD' in out or bool(wan), 'lan_ip': lan.group(1) if lan else '',
                'wan_ip': wan.group(1) if wan else ''}

    def open_router_port(self) -> Tuple[bool, str, Dict[str, Any]]:
        """Ask the router to forward the port (UPnP). Returns (ok, message, router)."""
        if not _upnpc():
            return False, 'This NAS cannot talk to routers (package miniupnpc). Forward the port by hand.', {}
        with _lock:
            state = self.load()
            info = self.router()
            if not info.get('found'):
                return False, ('Your router did not answer. Automatic port opening (UPnP) is off or not '
                               'supported there; forward the port by hand, or turn on UPnP in the router.'), info
            lan = info.get('lan_ip') or (self.lan_addresses() or [''])[0]
            port = str(state['port'])
            problem = ''
            for lease in ('0', str(UPNP_LEASE_FALLBACK)):
                out = self.run_local([_upnpc() or 'upnpc', '-e', UPNP_NAME, '-a', lan, port, port, 'UDP', lease])
                if 'is redirected to internal' in out:
                    state['upnp'] = True
                    self._save(state)
                    return True, f'Your router now forwards UDP port {port} to this NAS.', info
                failed = re.search(r'failed with code (\d+) \(([^)]*)\)', out)
                problem = f'{failed.group(2)} ({failed.group(1)})' if failed else (out.strip().splitlines() or [''])[-1]
                if failed and failed.group(1) == '718':
                    break   # the port is taken by another device: a lease does not help
            return False, f'The router refused: {problem}. Forward the port by hand.', info

    # ── Seen ─────────────────────────────────────────────────────────────

    def _handshakes(self) -> Dict[str, str]:
        """public key -> when it last connected (ISO), from `wg show remote0 latest-handshakes`."""
        wg = _wg()
        if not wg or not self.interface_up():
            return {}
        res, err = self.run([wg, 'show', INTERFACE, 'latest-handshakes'], timeout=10)
        if err or not res or res.returncode != 0:
            return {}
        seen = {}
        for line in (res.stdout or '').splitlines():
            parts = line.split()
            if len(parts) == 2 and KEY_RE.match(parts[0]) and parts[1].isdigit() and int(parts[1]) > 0:
                seen[parts[0]] = datetime.fromtimestamp(int(parts[1]), timezone.utc).isoformat()
        return seen


def _file_name(name: str) -> str:
    slug = re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower()[:24] or 'device'
    return f'alvaos-{slug}.conf'


def _detect(names: List[str]) -> Optional[str]:
    for path in names:
        if os.path.exists(path):
            return path
    return None


def _wg_quick() -> Optional[str]:
    return _detect(['/usr/bin/wg-quick', '/usr/sbin/wg-quick']) or shutil.which('wg-quick')


def _upnpc() -> Optional[str]:
    return _detect(['/usr/bin/upnpc']) or shutil.which('upnpc')


def _run_local(argv: List[str]) -> str:
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=20, env={'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return (res.stdout or '') + (res.stderr or '')


def address_warning(address: str) -> str:
    """Why this internet address cannot be reached from outside, or ''."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return ''
    if ip.version == 4 and ip in CGNAT:
        return ('Your internet provider shares one internet address among many customers (CGNAT). Then a '
                'forwarded port cannot be reached from outside. Ask your provider for a public IPv4 address '
                '(often free, sometimes called "dual stack" or "public IP").')
    if ip.is_private:
        return ('Your router is behind another router (for example a provider box in front of your own). '
                'Forward the port on both, or put the first one into bridge mode.')
    return ''


def _wg() -> Optional[str]:
    return _detect(['/usr/bin/wg', '/usr/sbin/wg']) or shutil.which('wg')


def _lan_addresses() -> List[str]:
    """IPv4 addresses of this NAS on the home network (for the router step)."""
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for name, addrs in psutil.net_if_addrs().items():
        if name in ('lo', INTERFACE, 'buddy0') or name.startswith(('docker', 'br-', 'veth')):
            continue
        for addr in addrs:
            try:
                ip = ipaddress.ip_address(addr.address)
            except ValueError:
                continue
            if ip.version == 4 and ip.is_private and not ip.is_loopback:
                out.append(str(ip))
    return sorted(set(out))
