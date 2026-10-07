#!/usr/bin/env python3
"""Remote access: reach this NAS from anywhere, without a router setting.

Two ways, each on its own (Settings › Remote access):

* **Tailscale** – your own phones and computers and the NAS join one private
  network, end to end encrypted, also behind CGNAT. The NAS shows a link once,
  you sign in with your Tailscale account (free for home use), then install
  the Tailscale app on each device with the same account. Everything of the
  NAS is reachable there: the admin pages, the Hub, the shared folders.
* **Cloudflare Tunnel** – the Hub (Files, Photos, Calendar, share links) at an
  address of your own domain, like https://cloud.example.com, in any browser.
  You need a Cloudflare account with your domain and an API token; AlvaOS
  makes the tunnel and the DNS record itself. Only the Hub (port 8090) goes
  through it, never the admin pages. Cloudflare ends the encryption in its
  network, and its terms do not allow large video streams.

Both run as the official containers (`tailscale/tailscale`,
`cloudflare/cloudflared`) on the host network, started through the privilege
helper's checked docker-compose like the apps, so nothing is added to the
system's package sources. Turning one off stops its container; Tailscale
keeps its sign-in for next time.

Until 2026-10 remote access was an own WireGuard tunnel with a forwarded
router port (remote0). `retire_wireguard()` takes it down once on update.
Buddy Backup keeps its own tunnel (buddy0).
"""

import base64
import json
import os
import re
import secrets
import socket
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

SETTINGS_FILE = '/var/lib/alvaos/remote_access.json'
OLD_WG_CONFIG = '/var/lib/alvaos/wireguard/remote0.conf'
DOCKER = '/usr/bin/docker'
COMPOSE = '/usr/bin/docker-compose'
TAILSCALE = {'project': 'alvaos-tailscale', 'container': 'alvaos-tailscale', 'image': 'tailscale/tailscale:stable'}
CLOUDFLARED = {'project': 'alvaos-cloudflared', 'container': 'alvaos-cloudflared',
               'image': 'cloudflare/cloudflared:latest'}
HUB_SERVICE = 'http://localhost:8090'    # the only thing a Cloudflare tunnel reaches
CF_API = 'https://api.cloudflare.com/client/v4'
LABEL_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')
ZONE_ID_RE = re.compile(r'^[0-9a-f]{32}$')
API_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{30,100}$')
TUNNEL_TOKEN_RE = re.compile(r'^[A-Za-z0-9+/=_-]{60,600}$')
HOST_RE = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_tunnel_token(token: str) -> Optional[Dict[str, str]]:
    """A Cloudflare tunnel token is base64 JSON {"a": account, "t": tunnel, "s": secret}."""
    if not TUNNEL_TOKEN_RE.match(token or ''):
        return None
    try:
        data = json.loads(base64.b64decode(token + '=' * (-len(token) % 4)))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data.get(k) for k in ('a', 't', 's')):
        return None
    return {'account': data['a'], 'tunnel': data['t']}


def tailscale_compose(hostname: str) -> Dict[str, Any]:
    name = re.sub(r'[^a-z0-9-]', '-', hostname.lower())[:63].strip('-') or 'alvaos'
    return {'services': {'tailscale': {
        'image': TAILSCALE['image'], 'container_name': TAILSCALE['container'], 'network_mode': 'host',
        'restart': 'unless-stopped',
        'environment': {'TS_STATE_DIR': '/var/lib/tailscale', 'TS_USERSPACE': 'false', 'TS_HOSTNAME': name,
                        'TS_AUTH_ONCE': 'true', 'TS_EXTRA_ARGS': '--accept-dns=false'},
        'volumes': ['alvaos-tailscale:/var/lib/tailscale'],
        'devices': ['/dev/net/tun:/dev/net/tun'], 'cap_add': ['NET_ADMIN', 'NET_RAW']}},
        'volumes': {'alvaos-tailscale': {}}}


def cloudflared_compose(token: str) -> Dict[str, Any]:
    return {'services': {'cloudflared': {
        'image': CLOUDFLARED['image'], 'container_name': CLOUDFLARED['container'], 'network_mode': 'host',
        'restart': 'unless-stopped', 'command': ['tunnel', '--no-autoupdate', 'run'],
        'environment': {'TUNNEL_TOKEN': token}}}}


class CloudflareError(Exception):
    """A sentence for the person."""


class Cloudflare:
    """The few calls of Cloudflare's API that make a tunnel for the Hub."""

    def __init__(self, token: str, request: Optional[Callable[..., Any]] = None):
        self.token = token
        if request is None:
            import requests
            request = requests.request
        self.request = request

    def call(self, method: str, path: str, **kw: Any) -> Any:
        try:
            res = self.request(method, CF_API + path, headers={'Authorization': f'Bearer {self.token}'},
                               timeout=20, **kw)
            data = res.json()
        except Exception as e:  # noqa: BLE001 - offline, DNS, TLS, not JSON
            raise CloudflareError(f'Cloudflare could not be reached ({type(e).__name__}).') from e
        if not data.get('success'):
            errors = data.get('errors') or [{}]
            code, message = errors[0].get('code'), str(errors[0].get('message') or 'unknown')
            if res.status_code in (401, 403) or code in (9109, 10000):
                raise CloudflareError('Cloudflare does not accept the API token for this. It needs the '
                                      'permissions "Cloudflare Tunnel: Edit" and "DNS: Edit".')
            raise CloudflareError(f'Cloudflare said: {message[:200]}')
        return data.get('result')

    def zones(self) -> List[Dict[str, str]]:
        found = self.call('GET', '/zones', params={'per_page': 50, 'status': 'active'}) or []
        return [{'id': z['id'], 'name': z['name'], 'account': (z.get('account') or {}).get('id', '')}
                for z in found if isinstance(z, dict) and ZONE_ID_RE.match(str(z.get('id', '')))]

    def make(self, zone: Dict[str, str], hostname: str, label: str) -> Dict[str, str]:
        """Tunnel, its route to the Hub and the DNS name. Returns the ids and the tunnel token."""
        account = zone['account']
        existing = self.call('GET', f'/zones/{zone["id"]}/dns_records', params={'name': hostname}) or []
        if existing:
            raise CloudflareError(f'{hostname} is used already in your Cloudflare DNS. Choose another name.')
        tunnel = self.call('POST', f'/accounts/{account}/cfd_tunnel',
                           json={'name': f'alvaos-{label}-{secrets.token_hex(3)}', 'config_src': 'cloudflare'})
        tunnel_id = str(tunnel['id'])
        try:
            self.call('PUT', f'/accounts/{account}/cfd_tunnel/{tunnel_id}/configurations',
                      json={'config': {'ingress': [{'hostname': hostname, 'service': HUB_SERVICE},
                                                   {'service': 'http_status:404'}]}})
            record = self.call('POST', f'/zones/{zone["id"]}/dns_records',
                               json={'type': 'CNAME', 'name': hostname, 'content': f'{tunnel_id}.cfargotunnel.com',
                                     'proxied': True, 'comment': 'AlvaOS Hub (Cloudflare Tunnel)'})
            token = self.call('GET', f'/accounts/{account}/cfd_tunnel/{tunnel_id}/token')
        except CloudflareError:
            self.remove(account, tunnel_id, zone['id'], '')
            raise
        return {'tunnel_id': tunnel_id, 'dns_record_id': str(record['id']), 'tunnel_token': str(token)}

    def remove(self, account: str, tunnel_id: str, zone_id: str, record_id: str) -> None:
        """Best effort: what is gone already does not matter."""
        for method, path in (('DELETE', f'/zones/{zone_id}/dns_records/{record_id}') if record_id else ('', ''),
                             ('DELETE', f'/accounts/{account}/cfd_tunnel/{tunnel_id}/connections'),
                             ('DELETE', f'/accounts/{account}/cfd_tunnel/{tunnel_id}')):
            if method:
                try:
                    self.call(method, path)
                except CloudflareError:
                    pass


class RemoteAccess:
    def __init__(self, run_command: Callable, settings_path: Optional[str] = None,
                 compose_up: Optional[Callable[[Dict[str, Any], str], Tuple[bool, Optional[str]]]] = None,
                 compose_down: Optional[Callable[[Dict[str, Any], str], Tuple[bool, Optional[str]]]] = None,
                 cloudflare: Optional[Callable[[str], Cloudflare]] = None,
                 hostname: Optional[Callable[[], str]] = None,
                 exec_in: Optional[Callable[..., Tuple[Optional[Dict[str, Any]], Optional[str]]]] = None):
        self.run = run_command
        self.settings_path = settings_path or SETTINGS_FILE
        self.compose_up = compose_up or _compose_up
        self.compose_down = compose_down or _compose_down
        self.cloudflare = cloudflare or Cloudflare
        self.hostname = hostname or (lambda: socket.gethostname().split('.')[0])
        self.exec_in = exec_in or _exec_in

    # ── State ────────────────────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        try:
            with open(self.settings_path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        ts: Dict[str, Any] = data['tailscale'] if isinstance(data.get('tailscale'), dict) else {}
        cf: Dict[str, Any] = data['cloudflare'] if isinstance(data.get('cloudflare'), dict) else {}
        return {
            'tailscale': {'enabled': bool(ts.get('enabled'))},
            'cloudflare': {k: str(cf.get(k) or '') for k in ('hostname', 'zone_id', 'zone_name', 'account_id',
                                                               'tunnel_id', 'dns_record_id', 'api_token',
                                                               'tunnel_token', 'connected_at', 'mode')}
            | {'enabled': bool(cf.get('enabled'))},
            'wireguard_retired': bool(data.get('wireguard_retired')),
            # Kept only to take the old tunnel down once (retire_wireguard).
            'old': {k: data.get(k) for k in ('enabled', 'upnp', 'port') if k in data},
        }

    def _save(self, state: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        tmp = f'{self.settings_path}.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # holds the Cloudflare tokens
        out = {'tailscale': state['tailscale'], 'cloudflare': state['cloudflare'],
               'wireguard_retired': state['wireguard_retired']}
        with os.fdopen(fd, 'w') as f:
            json.dump(out, f, indent=1)
        os.replace(tmp, self.settings_path)

    def _exec(self, container: str, command: str, timeout: int = 20) -> Tuple[int, str]:
        """A command inside the container (docker_manager's checked exec)."""
        result, err = self.exec_in(container, command, timeout=timeout)
        if err or result is None:
            return 1, err or ''
        return int(result.get('exit_code', 1)), str(result.get('output') or '')

    def _running(self, container: str) -> bool:
        res, err = self.run([DOCKER, 'inspect', '--format', '{{.State.Running}}', container], timeout=15)
        return not err and res is not None and res.returncode == 0 and (res.stdout or '').strip() == 'true'

    # ── What the page shows ───────────────────────────────────────────────

    def tailscale_status(self) -> Dict[str, Any]:
        state = self.load()['tailscale']
        out: Dict[str, Any] = {'enabled': state['enabled'], 'running': False, 'state': 'off', 'login_url': '',
                               'name': '', 'addresses': [], 'tailnet': '', 'devices': []}
        if not state['enabled']:
            return out
        out['running'] = self._running(TAILSCALE['container'])
        if not out['running']:
            out['state'] = 'starting'
            return out
        code, text = self._exec(TAILSCALE['container'], 'tailscale status --json')
        try:
            data = json.loads(text[text.index('{'):]) if code == 0 or '{' in text else {}
        except ValueError:
            data = {}
        backend = str(data.get('BackendState') or '')
        out['state'] = {'Running': 'on', 'NeedsLogin': 'login', 'NeedsMachineAuth': 'approve',
                        'Starting': 'starting', 'Stopped': 'stopped'}.get(backend, 'starting')
        url = str(data.get('AuthURL') or '')
        out['login_url'] = url if url.startswith('https://login.tailscale.com/') else ''
        me: Dict[str, Any] = data['Self'] if isinstance(data.get('Self'), dict) else {}
        out['name'] = str(me.get('DNSName') or '').rstrip('.')
        out['addresses'] = [a for a in me.get('TailscaleIPs') or [] if isinstance(a, str)][:2]
        tailnet: Dict[str, Any] = data['CurrentTailnet'] if isinstance(data.get('CurrentTailnet'), dict) else {}
        out['tailnet'] = str(tailnet.get('Name') or '')
        peers: Dict[str, Any] = data['Peer'] if isinstance(data.get('Peer'), dict) else {}
        out['devices'] = sorted(({'name': str(p.get('HostName') or ''), 'os': str(p.get('OS') or ''),
                                  'online': bool(p.get('Online')), 'last_seen': str(p.get('LastSeen') or '')}
                                 for p in peers.values() if isinstance(p, dict)), key=lambda p: p['name'].lower())
        return out

    def cloudflare_status(self) -> Dict[str, Any]:
        cf = self.load()['cloudflare']
        return {'enabled': cf['enabled'], 'hostname': cf['hostname'], 'mode': cf['mode'],
                'url': f'https://{cf["hostname"]}' if cf['hostname'] else '', 'connected_at': cf['connected_at'],
                'running': cf['enabled'] and self._running(CLOUDFLARED['container']),
                'has_api_token': bool(cf['api_token'])}

    def status(self) -> Dict[str, Any]:
        return {'tailscale': self.tailscale_status(), 'cloudflare': self.cloudflare_status()}

    def public_url(self) -> str:
        """The Hub's address on the internet, for share links (Cloudflare)."""
        cf = self.load()['cloudflare']
        return f'https://{cf["hostname"]}' if cf['enabled'] and cf['hostname'] else ''

    def problems(self) -> List[Dict[str, str]]:
        """What keeps remote access from working, for the alerts."""
        state = self.load()
        out = []
        if state['tailscale']['enabled'] and not self._running(TAILSCALE['container']):
            out.append({'alert_id': 'remote-access-tailscale', 'severity': 'warning',
                        'title': 'Tailscale is not running',
                        'message': 'Your devices cannot reach this NAS from outside. Turn Tailscale off and on '
                                   'again in Settings › Remote access; Docker has to be running.'})
        if state['cloudflare']['enabled'] and not self._running(CLOUDFLARED['container']):
            out.append({'alert_id': 'remote-access-cloudflare', 'severity': 'warning',
                        'title': 'The Cloudflare Tunnel is not running',
                        'message': f'{state["cloudflare"]["hostname"]} does not reach the Hub. Turn it off and '
                                   'on again in Settings › Remote access.'})
        return out

    # ── Tailscale ─────────────────────────────────────────────────────────

    def set_tailscale(self, on: bool) -> Tuple[bool, str]:
        with _lock:
            state = self.load()
            if on:
                ok, error = self.compose_up(tailscale_compose(self.hostname()), TAILSCALE['project'])
                if not ok:
                    return False, _docker_problem(error, 'Tailscale')
            else:
                self.compose_down(tailscale_compose(self.hostname()), TAILSCALE['project'])
            state['tailscale']['enabled'] = on
            self._save(state)
        return True, ('Tailscale is starting. Sign in with the link that appears.' if on else 'Tailscale is off.')

    def tailscale_logout(self) -> Tuple[bool, str]:
        """Sign the NAS out of the Tailscale account (to use another one)."""
        if not self.load()['tailscale']['enabled']:
            return False, 'Tailscale is off.'
        code, text = self._exec(TAILSCALE['container'], 'tailscale logout', timeout=30)
        if code != 0:
            return False, f'Tailscale did not sign out: {text.strip()[-200:]}'
        return True, 'Signed out. A new sign-in link appears in a moment.'

    # ── Cloudflare Tunnel ─────────────────────────────────────────────────

    def cloudflare_zones(self, api_token: str) -> Tuple[List[Dict[str, str]], str]:
        token = api_token.strip() or self.load()['cloudflare']['api_token']
        if not API_TOKEN_RE.match(token):
            return [], 'Paste the API token from Cloudflare (My Profile › API Tokens).'
        try:
            return [{'id': z['id'], 'name': z['name']} for z in self.cloudflare(token).zones()], ''
        except CloudflareError as e:
            return [], str(e)

    def cloudflare_connect(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        """Two ways: an API token, a domain and a name (AlvaOS makes the tunnel
        and the DNS name), or a tunnel token made by hand in Cloudflare."""
        with _lock:
            state = self.load()
            cf = state['cloudflare']
            if cf['enabled']:
                return False, 'A tunnel is set up already. Remove it first.'
            tunnel_token = str(payload.get('tunnel_token') or '').strip()
            if tunnel_token:
                parsed = check_tunnel_token(tunnel_token)
                if not parsed:
                    return False, 'That is not a tunnel token. Copy the token from the Docker command of the tunnel.'
                hostname = str(payload.get('hostname') or '').strip().lower()
                if hostname and not HOST_RE.match(hostname):
                    return False, 'Enter the public hostname you set for the tunnel, like cloud.example.com.'
                cf.update(mode='token', tunnel_token=tunnel_token, hostname=hostname, api_token='', zone_id='',
                          zone_name='', account_id='', tunnel_id=parsed['tunnel'],
                          dns_record_id='')
            else:
                api_token = str(payload.get('api_token') or '').strip() or cf['api_token']
                label = str(payload.get('name') or '').strip().lower()
                if not API_TOKEN_RE.match(api_token):
                    return False, 'Paste the API token from Cloudflare (My Profile › API Tokens).'
                if not LABEL_RE.match(label):
                    return False, 'Choose a name of letters, numbers and dashes, like "cloud".'
                api = self.cloudflare(api_token)
                try:
                    zone = next((z for z in api.zones() if z['id'] == str(payload.get('zone_id') or '')), None)
                    if not zone or not zone['account']:
                        return False, 'Choose one of your domains.'
                    hostname = f'{label}.{zone["name"]}'
                    made = api.make(zone, hostname, label)
                except CloudflareError as e:
                    return False, str(e)
                cf.update(mode='api', api_token=api_token, zone_id=zone['id'], zone_name=zone['name'],
                          account_id=zone['account'], hostname=hostname, **made)
            ok, error = self.compose_up(cloudflared_compose(cf['tunnel_token']), CLOUDFLARED['project'])
            if not ok:
                if cf['mode'] == 'api':
                    self.cloudflare(cf['api_token']).remove(cf['account_id'], cf['tunnel_id'], cf['zone_id'],
                                                            cf['dns_record_id'])
                return False, _docker_problem(error, 'The Cloudflare Tunnel')
            cf.update(enabled=True, connected_at=_now())
            self._save(state)
        where = f'https://{cf["hostname"]}' if cf['hostname'] else 'the hostname you set in Cloudflare'
        return True, f'The Hub is at {where} in a minute or two.'

    def cloudflare_disconnect(self) -> Tuple[bool, str]:
        with _lock:
            state = self.load()
            cf = state['cloudflare']
            if not cf['enabled'] and not cf['tunnel_token']:
                return False, 'There is no tunnel.'
            self.compose_down(cloudflared_compose(cf['tunnel_token'] or 'x'), CLOUDFLARED['project'])
            if cf['mode'] == 'api' and cf['api_token']:
                self.cloudflare(cf['api_token']).remove(cf['account_id'], cf['tunnel_id'], cf['zone_id'],
                                                        cf['dns_record_id'])
            state['cloudflare'] = {k: '' for k in cf} | {'enabled': False}
            self._save(state)
        return True, ('The tunnel and its DNS name are removed.' if cf['mode'] == 'api'
                      else 'The tunnel is stopped. Delete it in Cloudflare too if you no longer need it.')

    # ── At startup ────────────────────────────────────────────────────────

    def retire_wireguard(self, run_local: Optional[Callable[[List[str]], str]] = None) -> bool:
        """Once, after the update: the old WireGuard remote access goes away
        (the tunnel down, the router port closed). True when it was on."""
        state = self.load()
        if state['wireguard_retired']:
            return False
        was_on = bool(state['old'].get('enabled'))
        if os.path.exists('/sys/class/net/remote0'):
            wg_quick = '/usr/bin/wg-quick'
            self.run([wg_quick, 'down', OLD_WG_CONFIG], timeout=30)
        if state['old'].get('upnp') and state['old'].get('port'):
            import shutil
            upnpc = shutil.which('upnpc')
            if upnpc:
                (run_local or _run_local)([upnpc, '-d', str(state['old']['port']), 'UDP'])
        state['wireguard_retired'] = True
        self._save(state)
        return was_on


def public_url(settings_path: str = SETTINGS_FILE) -> str:
    """The Hub's internet address (Cloudflare Tunnel), for the Hub's share links."""
    try:
        with open(settings_path) as f:
            cf = json.load(f).get('cloudflare') or {}
    except (OSError, ValueError, AttributeError):
        return ''
    host = str(cf.get('hostname') or '') if isinstance(cf, dict) and cf.get('enabled') else ''
    return f'https://{host}' if HOST_RE.match(host) else ''


def _docker_problem(error: Optional[str], what: str) -> str:
    text = (error or '').strip()
    if 'Cannot connect to the Docker daemon' in text or 'docker.sock' in text:
        return f'{what} runs as a container, and Docker is not running. Check Apps.'
    if 'pull' in text.lower() or 'manifest' in text.lower() or 'dial tcp' in text.lower():
        return f'{what} could not be downloaded. Is the NAS online?'
    last = [ln for ln in text.splitlines() if ln.strip()][-1:] or ['']
    return f'{what} did not start: {last[0][:200]}'


def _exec_in(container: str, command: str, timeout: int = 20) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    import docker_manager
    return docker_manager.DockerManager().exec_in_container(container, command, timeout=timeout)


def _compose_up(compose: Dict[str, Any], project: str) -> Tuple[bool, Optional[str]]:
    import docker_manager
    return docker_manager.DockerManager().create_container_from_compose(compose, project, '', project_name=project)


def _compose_down(compose: Dict[str, Any], project: str) -> Tuple[bool, Optional[str]]:
    import subprocess

    import yaml

    import docker_manager
    from common import build_privileged_cmd
    path = docker_manager.write_compose_file(yaml.dump(compose), project)
    try:
        cmd = build_privileged_cmd([COMPOSE, '-f', path, '-p', project, 'down'], env=docker_manager.COMPOSE_ENV)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=docker_manager.COMPOSE_ENV)
        return res.returncode == 0, res.stderr
    finally:
        os.unlink(path)


def _run_local(argv: List[str]) -> str:
    import subprocess
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=30, env={'LC_ALL': 'C'})
        return (res.stdout or '') + (res.stderr or '')
    except (OSError, subprocess.TimeoutExpired):
        return ''
