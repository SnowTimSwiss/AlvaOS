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


def set_enabled(enabled: bool) -> Optional[Dict[str, Any]]:
    return _call('POST', '/config', {'enabled': bool(enabled)}, timeout=40)
