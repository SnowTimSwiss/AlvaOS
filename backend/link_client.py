#!/usr/bin/env python3
"""How the rest of the NAS talks to the Link daemon (link_daemon.py): the Hub, the backup code and
the admin pages. Every call answers None when the daemon is not running, so nothing here may
depend on it: Link is an extra way in, never the only one."""

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

BASE = 'http://127.0.0.1:8095'
TOKEN_FILE = os.path.join(os.environ.get('ALVAOS_LINK_DIR', '/var/lib/alvaos/link'), 'control_token')


def _token() -> str:
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        return ''


def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 10) -> Optional[Dict[str, Any]]:
    token = _token()
    if not token:
        return None
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={'X-Link-Token': token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as res:
            parsed = json.loads(res.read().decode('utf-8'))
            return parsed if isinstance(parsed, dict) else None
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode('utf-8'))
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, OSError):
            return None
    except (OSError, ValueError):
        return None


def status() -> Optional[Dict[str, Any]]:
    """{enabled, running, node_id, relay, peers: [...]} or None."""
    return _call('GET', '/status')


def node_id() -> str:
    """This NAS's Link address (64 hex digits), or '' when Link is off or not running."""
    found = status() or {}
    return str(found.get('node_id') or '') if found.get('running') else ''


def set_buddies(buddies: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """The buddies allowed in: [{id, name}]. Each gets an address on the loopback network
    (peer['alias'] in the answer) at which the backup code reaches it."""
    return _call('POST', '/buddies', {'buddies': buddies})


def remove_device(device_id: str) -> bool:
    """A phone was removed in the Hub: its Link key goes off the list."""
    return _call('POST', '/remove-device', {'device': device_id}) is not None


def add_phone(key: str, device_id: str, name: str, user: str) -> bool:
    """A phone that paired at home tells its Link key: it may now come in from away."""
    found = _call('POST', '/phones', {'key': key, 'device': device_id, 'name': name, 'user': user})
    return bool(found and found.get('ok'))


def set_enabled(enabled: bool) -> Optional[Dict[str, Any]]:
    return _call('POST', '/config', {'enabled': bool(enabled)}, timeout=40)


def pair(peer_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """Send the other NAS a pairing request through Link: {'op': 'buddy', 'secret', 'token'}.
    Answers its JSON, or {'error': ...}."""
    found = _call('POST', '/pair', {'id': peer_id, 'request': request}, timeout=60)
    return found if found is not None else {'error': 'AlvaOS Link is not running on this NAS.'}


def ping(peer_id: str) -> Dict[str, Any]:
    """{'ok': bool, 'ms': int, 'error': str}: does this buddy answer through Link?"""
    found = _call('POST', '/ping', {'id': peer_id}, timeout=40)
    return found if found is not None else {'ok': False, 'error': 'AlvaOS Link is not running on this NAS.'}


def secret_key() -> str:
    """This NAS's Link key (64 hex digits), for the Buddy Backup recovery kit; '' when Link does not answer."""
    return str((_call('GET', '/secret') or {}).get('secret') or '')


def import_secret_key(secret: str) -> bool:
    """A new install takes over the old Link key (from the recovery kit)."""
    found = _call('POST', '/secret', {'secret': secret}, timeout=60)
    return bool(found and not found.get('error'))
