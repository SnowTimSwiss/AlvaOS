"""What each person chose for their own Hub (the side bar), kept on the NAS so it follows them to the phone.

{ "<person>": { "bar": ["files", "calendar", "store:jellyfin"] } }

`bar` is the apps in the side bar, in this order; what is not in it is under "More". Without
a choice (None) the side bar has the Hub's own apps and the App Store apps are under "More".
Ids are the Hub's apps ("files", "photos", "calendar", "contacts", "chat") and "store:<app id>".
"""

import json
import os
import re
import threading
from typing import Any

PREFS_FILE = '/var/lib/alvaos/hub_prefs.json'
ITEM_RE = re.compile(r'^(?:[a-z][a-z0-9_-]{0,31}|store:[a-z0-9][a-z0-9_-]{0,63})$')
MAX_ITEMS = 64
_lock = threading.Lock()


def _read(path: str) -> dict[str, Any]:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get(user: str, path: str | None = None) -> dict[str, Any]:
    entry = _read(path or PREFS_FILE).get(user)
    bar = entry.get('bar') if isinstance(entry, dict) else None
    if isinstance(bar, list):
        return {'bar': [str(i) for i in bar if ITEM_RE.match(str(i))][:MAX_ITEMS]}
    return {'bar': None}


def save(user: str, bar: list[str] | None, available: list[str], path: str | None = None) -> dict[str, Any]:
    """Keep this person's side bar: `bar` is the list in order (only what is `available` to them), or
    None to go back to the default. Raises ValueError with a sentence."""
    path = path or PREFS_FILE
    if bar is not None:
        if not isinstance(bar, list) or len(bar) > MAX_ITEMS or any(not isinstance(i, str) for i in bar):
            raise ValueError('Choose the apps for the side bar.')
        bar = list(dict.fromkeys(bar))
        unknown = [i for i in bar if i not in available]
        if unknown:
            raise ValueError('That app is not there (any more).')
        if not bar:
            raise ValueError('Keep at least one app in the side bar.')
    with _lock:
        data = _read(path)
        if bar is None:
            data.pop(user, None)
        else:
            data[user] = {'bar': bar}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f'{path}.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    return get(user, path)
