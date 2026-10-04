#!/usr/bin/env python3
"""AlvaOS Hub: which Hub apps are on, and who sees them.

The Hub is one address for the whole household (files_server.py, port 8090,
HTTPS 9443) with our own apps side by side. The admin turns each app on or
off in the admin pages (Hub) and chooses who sees it: everyone, or only some
people. The Hub server and WebDAV ask this module before they answer.

Where the apps keep data is part of the settings too (see docs/HUB.md):
personal data in each person's personal folder or, per app, on a pool of its
own (one share per person); which shared folders an app reads (Photos: photo
libraries); and the pool for caches such as thumbnails.

Settings live in /var/lib/alvaos/hub.json; both the admin backend and the
Hub server (same service account) read it. Without the file every app is on
for everyone, personal data goes to the personal folder and caches stay on
the system disk, which is how Files behaved before the Hub.
"""

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

SETTINGS_FILE = '/var/lib/alvaos/hub.json'
NAME = 'AlvaOS Hub'

# The Hub apps there are. `needs`: apps it is part of (Photos shows the
# pictures in the shared folders, so it needs Files). `personal`: the folder
# it keeps each person's own data in (None: it keeps none); `libraries`: it
# also reads shared folders the admin marks. Order = order in the bar.
APPS: List[Dict[str, Any]] = [
    {'id': 'files', 'name': 'Files', 'icon': 'folder',
     'description': 'The shared folders in the browser: open, upload, share links, trash and previous versions.',
     'needs': [], 'personal': None, 'libraries': False},
    {'id': 'photos', 'name': 'Photos', 'icon': 'image',
     'description': 'Everyone\'s own photos and the family\'s photo libraries, newest first, by month.',
     'needs': ['files'], 'personal': 'Photos', 'libraries': True},
]
CACHE_FOLDER = '.alvaos-hub'   # at the top of the cache pool, not shared
SHARE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,63}$')
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
    apps: Dict[str, Dict[str, Any]] = {}
    for app in APPS:
        item = raw_apps.get(app['id'])
        entry: Dict[str, Any] = item if isinstance(item, dict) else {}
        people = entry.get('people')
        apps[app['id']] = {
            'enabled': bool(entry.get('enabled', True)),
            'people': sorted({str(p) for p in people if USER_RE.match(str(p))}) if isinstance(people, list) else None,
        }
        if app['personal']:
            apps[app['id']]['location'] = _clean_location(entry.get('location'))
        if app['libraries']:
            libs = entry.get('libraries')
            apps[app['id']]['libraries'] = sorted({str(n) for n in libs if SHARE_NAME_RE.match(str(n))}) \
                if isinstance(libs, list) else []
    raw_storage = raw.get('storage') if isinstance(raw, dict) else None
    storage: Dict[str, Any] = raw_storage if isinstance(raw_storage, dict) else {}
    return {'apps': apps, 'storage': {'cache_pool': str(storage.get('cache_pool') or '')}}


def _clean_location(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict) and raw.get('mode') == 'pool' and raw.get('pool_id'):
        limit = raw.get('limit_gb')
        return {'mode': 'pool', 'pool_id': str(raw['pool_id']),
                'limit_gb': limit if isinstance(limit, (int, float)) and limit > 0 else None}
    return {'mode': 'personal'}


def save(payload: Dict[str, Any], known_people: Iterable[str], path: Optional[str] = None,
         pools: Optional[Dict[str, Any]] = None, shares: Optional[Iterable[str]] = None
         ) -> Tuple[Optional[Dict[str, Any]], str]:
    """Change apps and where they keep things:
    {'apps': {id: {'enabled', 'people', 'location', 'libraries'}}, 'storage': {'cache_pool'}}.
    `pools` (id -> pool) and `shares` (names) are what may be chosen."""
    path = path or SETTINGS_FILE
    settings = load(path)
    known = set(known_people)
    pools = pools or {}
    share_names = set(shares or [])
    if not isinstance(payload, dict) or not (payload.get('apps') or payload.get('storage')):
        return None, 'Nothing to change.'
    changes = payload.get('apps') or {}
    if not isinstance(changes, dict):
        return None, 'Nothing to change.'
    for app_id, change in changes.items():
        if app_id not in APP_IDS or not isinstance(change, dict):
            return None, f'There is no Hub app "{app_id}".'
        app = next(a for a in APPS if a['id'] == app_id)
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
        if 'location' in change:
            if not app['personal']:
                return None, f'{app["name"]} keeps no data of its own.'
            location = change['location'] if isinstance(change['location'], dict) else {}
            if location.get('mode') == 'pool':
                if str(location.get('pool_id') or '') not in pools:
                    return None, 'Choose a storage pool.'
                limit = location.get('limit_gb')
                if limit not in (None, '', 0):
                    try:
                        limit = float(limit)
                    except (TypeError, ValueError):
                        return None, 'The limit is a number of GB.'
                    if limit <= 0:
                        return None, 'The limit is a number of GB.'
                entry['location'] = _clean_location({'mode': 'pool', 'pool_id': location['pool_id'],
                                                     'limit_gb': limit or None})
            elif location.get('mode') == 'personal':
                entry['location'] = {'mode': 'personal'}
            else:
                return None, 'Choose the personal folder or a storage pool.'
        if 'libraries' in change:
            if not app['libraries']:
                return None, f'{app["name"]} reads no shared folders.'
            libs = change['libraries']
            if not isinstance(libs, list) or not set(map(str, libs)) <= share_names:
                return None, 'Choose shared folders that exist.'
            entry['libraries'] = sorted({str(n) for n in libs})
    storage = payload.get('storage')
    if isinstance(storage, dict) and 'cache_pool' in storage:
        pool_id = str(storage.get('cache_pool') or '')
        if pool_id and pool_id not in pools:
            return None, 'Choose a storage pool for the cache.'
        settings['storage']['cache_pool'] = pool_id
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
    return [{**{k: a[k] for k in ('id', 'name', 'icon', 'description', 'needs', 'personal', 'libraries')},
             **settings['apps'][a['id']]} for a in APPS]


# ── Where things are ─────────────────────────────────────────────────────────

def app_share_name(app_id: str, user: str) -> str:
    """The share an app keeps one person's data in when it has a pool of its own."""
    return f'{user}-{app_id}'


def own_folder(app_id: str, user: str, shares_state: Dict[str, Any],
               settings: Optional[Dict[str, Any]] = None) -> Optional[Tuple[str, str]]:
    """(share name, folder in it) where an app keeps this person's own data,
    or None when there is no such place yet (no personal folder)."""
    settings = settings or load()
    app = next((a for a in APPS if a['id'] == app_id), None)
    if not app or not app['personal']:
        return None
    shares = [s for s in shares_state.values() if isinstance(s, dict)]
    if settings['apps'][app_id]['location']['mode'] == 'pool':
        name = app_share_name(app_id, user)
        found = next((s for s in shares if s.get('name') == name and s.get('hub_for') == user), None)
        return (name, '') if found else None
    personal = next((s for s in shares if s.get('personal_for') == user), None)
    return (str(personal['name']), app['personal']) if personal else None


def cache_dir(pools_state: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The folder for caches on the chosen pool, or None for the system disk."""
    settings = settings or load()
    pool = pools_state.get(settings['storage']['cache_pool']) if settings['storage']['cache_pool'] else None
    mount = str((pool or {}).get('mount_point') or '') if isinstance(pool, dict) else ''
    if not mount or mount == '/' or not os.path.ismount(mount):
        return None
    return os.path.join(mount, CACHE_FOLDER)
