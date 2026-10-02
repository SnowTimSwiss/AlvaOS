#!/usr/bin/env python3
"""Let hard disks sleep when nobody uses them.

One setting for all spinning disks that are not the system disk: never, or
after 10, 20, 30 or 60 minutes. It is applied with `hdparm -S` when saved and
at every start (disks forget it when they lose power). SSDs are left alone.
"""

import json
import os
from typing import Any, Callable, Dict, List

from common import CMD, ensure_directories

SETTINGS_FILE = '/var/lib/alvaos/disk_power.json'
HDPARM = CMD.get('HDPARM', '/usr/sbin/hdparm')

# Minutes -> hdparm -S value: 1..240 count 5 seconds, 241..251 count 30 minutes.
SPINDOWN_VALUES = {0: 0, 10: 120, 20: 240, 30: 241, 60: 242}


def load_settings(path: str = SETTINGS_FILE) -> Dict[str, Any]:
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    minutes = raw.get('spindown_minutes', 0) if isinstance(raw, dict) else 0
    return {'spindown_minutes': minutes if minutes in SPINDOWN_VALUES else 0}


def save_settings(minutes: int, path: str = SETTINGS_FILE) -> Dict[str, Any]:
    if minutes not in SPINDOWN_VALUES:
        raise ValueError('Choose never, 10, 20, 30 or 60 minutes.')
    ensure_directories()
    settings = {'spindown_minutes': minutes}
    tmp = f'{path}.tmp'
    with open(tmp, 'w') as f:
        json.dump(settings, f)
    os.replace(tmp, path)
    return settings


def sleepable_disks(disks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Spinning data disks: not the system disk, not USB sticks."""
    return [d for d in disks if d.get('rotational') and not d.get('is_system_disk')
            and not d.get('is_removable') and str(d.get('path', '')).startswith('/dev/')]


def apply(minutes: int, disks: List[Dict[str, Any]], run: Callable) -> List[Dict[str, Any]]:
    """Set the sleep timer on every sleepable disk. Returns per disk whether it worked."""
    value = str(SPINDOWN_VALUES[minutes])
    results = []
    for disk in sleepable_disks(disks):
        res, err = run([HDPARM, '-S', value, disk['path']], timeout=20)
        ok = not err and res is not None and res.returncode == 0
        results.append({'path': disk['path'], 'ok': ok, 'error': '' if ok else (err or 'hdparm failed')})
    return results


def apply_saved(disks_reader: Callable[[], List[Dict[str, Any]]], run: Callable) -> None:
    """At startup: disks lose the timer when they lose power."""
    minutes = load_settings()['spindown_minutes']
    if minutes:
        try:
            apply(minutes, disks_reader(), run)
        except Exception as exc:  # noqa: BLE001 - startup goes on without it
            print(f'Could not set the disk sleep timer: {exc}')
