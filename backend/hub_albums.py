#!/usr/bin/env python3
"""Photos: albums and favourites of a person (docs/PHOTOS.md).

An album is a list of pictures, nothing more: no copies, so an album costs no
space. It lives in one small file per album in the person's own Photos place
(`Photos/.alvaos/albums/<id>.json`), written as the person through the helper,
like the phone lists and Calendar's data. Favourites are one more such list
(`Photos/.alvaos/favourites.json`).

A picture is named by its place on the NAS: "<shared folder>/<path in it>", so it
can be one of the person's own pictures or one in a photo library. A picture that was
moved or deleted simply no longer shows in the album (the page matches the lists
against the pictures it has found); nothing is cleaned up behind the person's back.

The pages ask for everything at once (`GET /api/photos/library`: all the lists, small
enough), and change one list per call.
"""

import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

import files_manager
import hub_apps
import hub_data

bp = Blueprint('hub_albums', __name__)

ALBUMS_DIR = '.alvaos/albums'
FAVOURITES = '.alvaos/favourites.json'
ALBUM_ID_RE = re.compile(r'^[0-9a-f]{12}$')
MAX_ALBUMS = 200
MAX_ITEMS = 20000
MAX_BATCH = 5000
MAX_REF = 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def album_name(name: Any) -> str:
    clean = re.sub(r'[\x00-\x1f\x7f]', ' ', str(name or ''))
    return re.sub(r'\s+', ' ', clean).strip()[:60]


def refs(raw: Any) -> Tuple[List[str], str]:
    """The pictures of a request: "<share>/<path>" strings, checked."""
    if not isinstance(raw, list) or len(raw) > MAX_BATCH:
        return [], f'Send up to {MAX_BATCH} pictures at a time.'
    out: List[str] = []
    for item in raw:
        text = str(item or '')
        share, _, path = text.partition('/')
        parts = path.split('/')
        if (not share or not path or len(text) > MAX_REF or '\x00' in text or '\n' in text
                or any(p in ('', '.', '..') for p in parts)):
            return [], 'A picture is not described properly.'
        if text not in out:
            out.append(text)
    return out, ''


def _place(session: Dict[str, Any], need_write: bool) -> Tuple[Optional[hub_data.Place], Any]:
    place = hub_data.own_place('photos', session, hub_apps.load())
    if not place:
        return None, (jsonify({'error': 'You have no place for photos yet. Ask whoever runs this NAS for a '
                                        'personal folder.'}), 409)
    if need_write and not place.writable:
        return None, (jsonify({'error': 'You can only look at your photos, not change them.'}), 403)
    return place, None


def _album_file(album_id: str) -> str:
    return f'{ALBUMS_DIR}/{album_id}.json'


def _list_names(place: hub_data.Place) -> List[str]:
    folder = '/'.join(p for p in (place.folder, ALBUMS_DIR) if p)
    entries, _ = files_manager.list_entries('/'.join(p for p in (place.root, folder) if p), user=place.user)
    return sorted(str(e.get('name')) for e in entries or [] if str(e.get('name', '')).endswith('.json')
                  and ALBUM_ID_RE.match(str(e.get('name'))[:-5]))


def _items(data: Dict[str, Any]) -> List[str]:
    items = data.get('items')
    return [str(i) for i in items] if isinstance(items, list) else []


@bp.get('/api/photos/library')
def library():
    """All the person's lists: {albums: [{id, name, items}], favourites: [items]}."""
    session, refused = hub_data.need_session('photos')
    if refused:
        return refused
    place, bad = _place(session, False)
    if bad or place is None:
        return bad
    albums = []
    for name in _list_names(place):
        data, _ = hub_data.read(place, _album_file(name[:-5]))
        if data and data.get('id'):
            albums.append({'id': data['id'], 'name': data.get('name', ''), 'items': _items(data),
                           'created_at': data.get('created_at', '')})
    albums.sort(key=lambda a: (a['name'].lower(), a['id']))
    fav, _ = hub_data.read(place, FAVOURITES)
    return jsonify({'albums': albums, 'favourites': _items(fav or {}), 'writable': place.writable})


@bp.post('/api/photos/albums')
def create_album():
    """{name, items?}: a new album, perhaps with its first pictures."""
    session, refused = hub_data.need_session('photos')
    if refused:
        return refused
    place, bad = _place(session, True)
    if bad or place is None:
        return bad
    data = request.get_json(silent=True) or {}
    name = album_name(data.get('name'))
    if not name:
        return jsonify({'error': 'Give the album a name.'}), 400
    items, problem = refs(data.get('items') or [])
    if problem:
        return jsonify({'error': problem}), 400
    names = _list_names(place)
    if len(names) >= MAX_ALBUMS:
        return jsonify({'error': 'That is a lot of albums. Delete one you no longer use.'}), 409
    album = {'id': secrets.token_hex(6), 'name': name, 'created_at': _now(), 'items': items}
    error = hub_data.write(place, _album_file(album['id']), album)
    if error:
        return jsonify({'error': error}), 409
    return jsonify({'success': True, 'album': album}), 201


def _change(album_id: str, work):
    """Read one album, let `work(album)` change it (an error text or ''), write it back."""
    session, refused = hub_data.need_session('photos')
    if refused:
        return refused
    place, bad = _place(session, True)
    if bad or place is None:
        return bad
    if not ALBUM_ID_RE.match(album_id):
        return jsonify({'error': 'That album is not known here.'}), 404
    with hub_data.lock(place, _album_file(album_id)):
        album, error = hub_data.read(place, _album_file(album_id))
        if album is None:
            return jsonify({'error': error}), 409
        if not album.get('id'):
            return jsonify({'error': 'That album is not known here.'}), 404
        problem = work(album)
        if problem:
            return jsonify({'error': problem}), 400
        error = hub_data.write(place, _album_file(album_id), album)
        if error:
            return jsonify({'error': error}), 409
    return jsonify({'success': True, 'album': album})


def _apply(data: Dict[str, Any], album: Dict[str, Any]) -> str:
    """Rename and add/remove pictures, as the request says."""
    if 'name' in data:
        name = album_name(data.get('name'))
        if not name:
            return 'Give the album a name.'
        album['name'] = name
    add, problem = refs(data.get('add') or [])
    if problem:
        return problem
    remove, problem = refs(data.get('remove') or [])
    if problem:
        return problem
    items = _items(album)
    gone = set(remove)
    items = [i for i in items if i not in gone]
    have = set(items)
    items += [i for i in add if i not in have]
    if len(items) > MAX_ITEMS:
        return f'An album holds up to {MAX_ITEMS} pictures.'
    album['items'] = items
    return ''


@bp.patch('/api/photos/albums/<album_id>')
def change_album(album_id):
    """{name?, add?: [pictures], remove?: [pictures]}."""
    data = request.get_json(silent=True) or {}
    return _change(album_id, lambda album: _apply(data, album))


@bp.delete('/api/photos/albums/<album_id>')
def delete_album(album_id):
    """The album goes; its pictures stay where they are."""
    session, refused = hub_data.need_session('photos')
    if refused:
        return refused
    place, bad = _place(session, True)
    if bad or place is None:
        return bad
    if not ALBUM_ID_RE.match(album_id):
        return jsonify({'error': 'That album is not known here.'}), 404
    error = hub_data.delete(place, _album_file(album_id))
    return (jsonify({'error': error}), 409) if error else jsonify({'success': True})


@bp.patch('/api/photos/favourites')
def change_favourites():
    """{add?: [pictures], remove?: [pictures]}: hearts."""
    session, refused = hub_data.need_session('photos')
    if refused:
        return refused
    place, bad = _place(session, True)
    if bad or place is None:
        return bad
    data = request.get_json(silent=True) or {}
    with hub_data.lock(place, FAVOURITES):
        current, error = hub_data.read(place, FAVOURITES)
        if current is None:
            return jsonify({'error': error}), 409
        current.setdefault('items', [])
        problem = _apply({'add': data.get('add'), 'remove': data.get('remove')}, current)
        if problem:
            return jsonify({'error': problem}), 400
        error = hub_data.write(place, FAVOURITES, current)
        if error:
            return jsonify({'error': error}), 409
    return jsonify({'success': True, 'favourites': _items(current)})
