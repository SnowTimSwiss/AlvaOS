#!/usr/bin/env python3
"""AlvaOS Hub: which Hub apps are on, and who sees them.

The Hub is one address for the whole household (files_server.py, port 8090,
HTTPS 9443) with our own apps side by side. The admin turns each app on or
off in the admin pages (Hub) and chooses who sees it: everyone, or only some
people. The Hub server and WebDAV ask this module before they answer.

Settings live in /var/lib/alvaos/hub.json; both the admin backend and the
Hub server (same service account) read it. Without the file every app is on
for everyone, which is how Files behaved before the Hub.
"""

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

SETTINGS_FILE = '/var/lib/alvaos/hub.json'
NAME = 'AlvaOS Hub'

# The Hub apps there are. `needs`: apps it is part of (Photos shows the
# pictures in the shared folders, so it needs Files). Order = order in the bar.
APPS: List[Dict[str, Any]] = [
    {'id': 'files', 'name': 'Files', 'icon': 'folder',
     'description': 'The shared folders in the browser: open, upload, share links, trash and previous versions.',
     'needs': []},
    {'id': 'photos', 'name': 'Photos', 'icon': 'image',
     'description': 'Every picture and video of a shared folder, newest first, by month.',
     'needs': ['files']},
]
APP_IDS = [a['id'] for a in APPS]
USER_RE = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """{'apps': {id: {'enabled': bool, 'people': None or [names]}}}; None = everyone."""
    try:
        with open(path or SETTINGS_FILE) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    found = raw.get('apps') if isinstance(raw, dict) else None
    raw_apps: Dict[str, Any] = found if isinstance(found, dict) else {}
    apps = {}
    for app_id in APP_IDS:
        item = raw_apps.get(app_id)
        entry: Dict[str, Any] = item if isinstance(item, dict) else {}
        people = entry.get('people')
        apps[app_id] = {
            'enabled': bool(entry.get('enabled', True)),
            'people': sorted({str(p) for p in people if USER_RE.match(str(p))}) if isinstance(people, list) else None,
        }
    return {'apps': apps}


def save(payload: Dict[str, Any], known_people: Iterable[str], path: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """Change one or more apps: {'apps': {id: {'enabled': bool, 'people': None or [names]}}}."""
    path = path or SETTINGS_FILE
    settings = load(path)
    known = set(known_people)
    changes = payload.get('apps') if isinstance(payload, dict) else None
    if not isinstance(changes, dict) or not changes:
        return None, 'Nothing to change.'
    for app_id, change in changes.items():
        if app_id not in APP_IDS or not isinstance(change, dict):
            return None, f'There is no Hub app "{app_id}".'
        entry = settings['apps'][app_id]
        if 'enabled' in change:
            entry['enabled'] = bool(change['enabled'])
        if 'people' in change:
            people = change['people']
            if people is None:
                entry['people'] = None
            elif isinstance(people, list):
                unknown = sorted({str(p) for p in people} - known)
                if unknown:
                    return None, f'There is no person called {", ".join(unknown)}.'
                if not people:
                    return None, 'Choose at least one person, or everyone.'
                entry['people'] = sorted({str(p) for p in people})
            else:
                return None, 'Choose everyone or a list of people.'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp'
    with open(tmp, 'w') as f:
        json.dump(settings, f, indent=1)
    os.replace(tmp, path)
    return settings, ''


def allowed(app_id: str, user: str, role: str = 'user', settings: Optional[Dict[str, Any]] = None) -> bool:
    """May this person use this app? The admin account may use every app that is on."""
    settings = settings or load()
    entry = settings['apps'].get(app_id)
    if not entry or not entry['enabled']:
        return False
    app = next(a for a in APPS if a['id'] == app_id)
    if not all(allowed(need, user, role, settings) for need in app['needs']):
        return False
    return role == 'admin' or entry['people'] is None or user in entry['people']


def visible(user: str, role: str = 'user', settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The apps this person sees in the Hub, in bar order."""
    settings = settings or load()
    return [{'id': a['id'], 'name': a['name'], 'icon': a['icon']}
            for a in APPS if allowed(a['id'], user, role, settings)]


def overview(settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every app with its settings, for the admin pages."""
    settings = settings or load()
    return [{**{k: a[k] for k in ('id', 'name', 'icon', 'description', 'needs')}, **settings['apps'][a['id']]}
            for a in APPS]
