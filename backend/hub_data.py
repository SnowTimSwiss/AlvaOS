#!/usr/bin/env python3
"""Where Hub apps keep their data, read and written as the signed-in person.

Calendar and Chat keep small JSON files in a hidden folder of the person's
personal folder (".alvaos/calendar", ".alvaos/chat"), or at the top of their
own share when the admin gave the app a pool of its own; family calendars
in a hidden folder of a shared folder. See docs/HUB.md.

Everything goes through the privileged helper as the person
(`alvaos-priv --as USER files-data-*`), so Linux checks their rights exactly
as over the network: what they cannot open over SMB, they cannot read here.

files_server.py hands over its session checks with setup(), so the app
modules (hub_calendar.py, hub_chat.py) need not import it.
"""

import io
import json
import threading
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

import files_manager
import hub_apps

_hooks: Dict[str, Callable[..., Any]] = {}
_locks: Dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def setup(**hooks: Callable[..., Any]) -> None:
    """need_session(app_id), shares_for(session), as_user(session), read_json(path)."""
    _hooks.update(hooks)


def need_session(app_id: str) -> Any:
    return _hooks['need_session'](app_id)


class Place(NamedTuple):
    id: str          # 'own' or 'shared:<share>'
    name: str        # what the person sees
    share: str
    root: str        # the share's folder on the pool
    folder: str      # where in the share the app keeps its files
    writable: bool
    own: bool
    user: Optional[str]   # who the helper acts as (None: root, for the admin)


def own_place(app_id: str, session: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> Optional[Place]:
    """Where this app keeps this person's own data, or None (no personal folder yet)."""
    settings = settings or hub_apps.load()
    shares_state = _hooks['read_json'](_hooks['shares_file']())
    found = hub_apps.own_folder(app_id, session['user'], shares_state, settings)
    mine = _hooks['shares_for'](session)
    if not found or found[0] not in mine:
        return None
    share = mine[found[0]]
    return Place('own', session['user'], share['name'], share['path'], found[1],
                 share['access'] == 'write', True, _hooks['as_user'](session))


def shared_places(app_id: str, session: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> List[Place]:
    """The shared folders the admin marked for this app that this person may open."""
    settings = settings or hub_apps.load()
    app = next(a for a in hub_apps.APPS if a['id'] == app_id)
    mine = _hooks['shares_for'](session)
    places = []
    for name in settings['apps'][app_id].get('libraries', []):
        share = mine.get(name)
        if share:
            places.append(Place(f'shared:{name}', name, name, share['path'], str(app['personal'] or ''),
                                share['access'] == 'write', False, _hooks['as_user'](session)))
    return places


def _rel(place: Place, name: str) -> str:
    return '/'.join(p for p in (place.folder, name) if p)


def lock(place: Place, name: str) -> threading.Lock:
    """One change at a time per file (read, change, write back)."""
    key = f'{place.root}/{_rel(place, name)}'
    with _locks_lock:
        return _locks.setdefault(key, threading.Lock())


def read(place: Place, name: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """The file's JSON, {} when it is not there yet, or (None, why not)."""
    result, error = files_manager.run_helper(['files-data-read', place.root, _rel(place, name)], timeout=60,
                                             user=place.user)
    if result is None:
        return None, error or 'This could not be read.'
    if not result.get('found'):
        return {}, ''
    try:
        data = json.loads(str(result.get('text') or ''))
    except ValueError:
        return None, f'{_rel(place, name)} in "{place.share}" is damaged.'
    return (data if isinstance(data, dict) else {}), ''


def write(place: Place, name: str, data: Dict[str, Any]) -> str:
    """Replace the file with `data`; '' or why not."""
    if not place.writable:
        return f'You can only look at "{place.share}", not change it.'
    body = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    result, error = files_manager.pipe_helper(['files-data-write', place.root, _rel(place, name)],
                                              io.BytesIO(body), user=place.user)
    return '' if result is not None else (error or 'This could not be saved.')


def delete(place: Place, name: str) -> str:
    result, error = files_manager.run_helper(['files-data-delete', place.root, _rel(place, name)], timeout=60,
                                             user=place.user)
    return '' if result is not None else (error or 'This could not be deleted.')
