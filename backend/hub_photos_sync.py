#!/usr/bin/env python3
"""Photos: the phone backup, kept in sync both ways (docs/PHOTOS.md).

The AlvaOS app on a phone (android/) backs up the albums the person chose
(on Android: the folders of the gallery, like Camera, Screenshots, WhatsApp
Images). Each album goes to `Photos/<phone>/<album>/` in the person's own
Photos place, so it shows up in Photos as an album and is a normal folder in
Files, SMB and backups.

Per phone the NAS keeps one small list of what came from where
(`Photos/.alvaos/phones/<id>.json`, written as the person through the helper
like Calendar's data): the phone's id for a picture → its path on the NAS, size
and date. With it, each sync is one question and one answer (`plan`):

* new on the phone → upload it (into its album's folder), unless the same file
  is there already (a reinstalled app uploads nothing twice);
* deleted on the NAS (in Photos, Files or over SMB: it is in the share's
  trash) → delete it on the phone too;
* moved or renamed on the NAS (gone, but not in the trash) → left alone:
  reorganising on the NAS never deletes anything on the phone, and the
  picture is not uploaded again;
* deleted on the phone → moved to the trash on the NAS (back for 30 days),
  unless the person chose "Free up space" for it, which keeps it on the NAS.

Uploads use the resumable upload of Files (`/api/upload/*`) with the file's
own date; afterwards the phone reports them (`done`).
"""

import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from flask import Blueprint, jsonify, request

import files_manager
import hub_apps
import hub_data

bp = Blueprint('hub_photos_sync', __name__)

PHONES_DIR = '.alvaos/phones'
PHONE_ID_RE = re.compile(r'^[0-9a-f]{12}$')
LOCAL_ID_RE = re.compile(r'^[A-Za-z0-9._:-]{1,80}$')
MAX_PHONES = 20
MAX_BATCH = 5000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def folder_name(name: Any, fallback: str = 'Phone') -> str:
    """A folder name from a phone's or album's name: no slashes, no dots in front."""
    clean = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', ' ', str(name or ''))
    clean = re.sub(r'\.{2,}', '.', re.sub(r'\s+', ' ', clean)).strip().lstrip('. ').rstrip('. ')
    return clean[:60] or fallback


def _place(session: Dict[str, Any]) -> Tuple[Optional[hub_data.Place], Any]:
    place = hub_data.own_place('photos', session, hub_apps.load())
    if not place:
        return None, (jsonify({'error': 'You have no place for photos yet. Ask whoever runs this NAS for a '
                                        'personal folder.'}), 409)
    if not place.writable:
        return None, (jsonify({'error': 'You can only look at your photos, not add to them.'}), 403)
    return place, None


def _rel(place: hub_data.Place, *parts: str) -> str:
    return '/'.join(p for p in (place.folder, *parts) if p)


def _manifest_name(phone_id: str) -> str:
    return f'{PHONES_DIR}/{phone_id}.json'


def _load(place: hub_data.Place, phone_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not PHONE_ID_RE.match(phone_id):
        return None, 'That phone is not known here.'
    data, error = hub_data.read(place, _manifest_name(phone_id))
    if data is None:
        return None, error
    if not data.get('id'):
        return None, 'That phone is not known here.'
    data.setdefault('items', {})
    return data, ''


def _list(place: hub_data.Place, rel_dir: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """name -> entry of one folder below the share, or None when it is not there."""
    entries, _ = files_manager.list_entries(os.path.join(place.root, rel_dir) if rel_dir else place.root,
                                            user=place.user)
    if entries is None:
        return None
    return {str(e.get('name')): e for e in entries if isinstance(e, dict)}


def _trashed(place: hub_data.Place) -> List[str]:
    """What is in the share's trash, as paths below the share."""
    result, _ = files_manager.run_helper(['files-trash-list', place.root], timeout=60, user=place.user)
    out = []
    for item in (result or {}).get('items') or []:
        folder = str(item.get('folder') or '').strip('/')
        out.append('/'.join(p for p in (folder, str(item.get('name') or '')) if p))
    return out


def _in_trash(path: str, trashed: List[str]) -> bool:
    return any(path == t or path.startswith(t + '/') for t in trashed)


def _mkdir(place: hub_data.Place, rel_dir: str) -> None:
    """Each level of a folder, as the person; there already is fine."""
    parent = ''
    for part in rel_dir.split('/'):
        files_manager.run_helper(['files-mkdir', os.path.join(place.root, parent) if parent else place.root, part],
                                 user=place.user)
        parent = f'{parent}/{part}' if parent else part


def _flag(entry: List[Any]) -> str:
    return str(entry[4]) if len(entry) > 4 else ''


def _free_name(name: str, taken: Dict[str, Any], local_id: str) -> str:
    if name not in taken:
        return name
    stem, ext = os.path.splitext(name)
    return f'{stem} ({re.sub(r"[^A-Za-z0-9]", "", local_id)[-8:] or "2"}){ext}'


# ── The API for the app ─────────────────────────────────────────────────────

def phone_list(place: hub_data.Place) -> List[Dict[str, Any]]:
    """The phones backing up into this place, with what they backed up."""
    folder = _list(place, _rel(place, PHONES_DIR)) or {}
    out = []
    for name in sorted(folder):
        if name.endswith('.json') and PHONE_ID_RE.match(name[:-5]):
            data, _ = _load(place, name[:-5])
            if data:
                out.append({'id': data['id'], 'name': data.get('name'), 'folder': data.get('folder'),
                            'albums': sorted({str(v[0]).split('/')[0] for v in data['items'].values()}),
                            'count': len(data['items']), 'last_sync': data.get('last_sync', ''),
                            'device': data.get('device', '')})
    return out


def phones_of(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The same for the Devices list in the Hub; empty without Photos."""
    if not hub_apps.allowed('photos', session['user'], session['role']):
        return []
    place = hub_data.own_place('photos', session)
    return phone_list(place) if place else []


@bp.get('/api/photos/phones')
def phones():
    session, refused = hub_data.need_session('photos')
    if refused:
        return refused
    place, bad = _place(session)
    if bad or place is None:
        return bad
    return jsonify({'phones': phone_list(place), 'share': place.share, 'folder': place.folder})


@bp.post('/api/photos/phones')
def add_phone():
    """A phone starts backing up: {name}. Its pictures go to Photos/<name>/."""
    session, refused = hub_data.need_session('photos')
    if refused:
        return refused
    place, bad = _place(session)
    if bad or place is None:
        return bad
    name = folder_name((request.get_json(silent=True) or {}).get('name'))
    folder = _list(place, _rel(place, PHONES_DIR)) or {}
    known = [n[:-5] for n in folder if n.endswith('.json') and PHONE_ID_RE.match(n[:-5])]
    if len(known) >= MAX_PHONES:
        return jsonify({'error': 'That is a lot of phones. Remove one you no longer use.'}), 409
    taken = {(_load(place, k)[0] or {}).get('folder') for k in known}
    unique, n = name, 2
    while unique in taken:
        unique, n = f'{name} {n}', n + 1
    phone: Dict[str, Any] = {'id': secrets.token_hex(6), 'name': name, 'folder': unique, 'created_at': _now(), 'last_sync': '',
             'items': {}}
    if isinstance(session.get('device'), dict):
        phone['device'] = session['device'].get('id', '')      # the app that added it (Hub › Devices)
    error = hub_data.write(place, _manifest_name(phone['id']), phone)
    if error:
        return jsonify({'error': error}), 409
    _mkdir(place, _rel(place, unique))
    return jsonify({'success': True, 'phone': {k: phone[k] for k in ('id', 'name', 'folder')},
                    'share': place.share}), 201


@bp.delete('/api/photos/phones/<phone_id>')
def forget_phone(phone_id):
    """The phone no longer syncs; its pictures stay on the NAS."""
    session, refused = hub_data.need_session('photos')
    if refused:
        return refused
    place, bad = _place(session)
    if bad or place is None:
        return bad
    if not PHONE_ID_RE.match(phone_id):
        return jsonify({'error': 'That phone is not known here.'}), 404
    error = hub_data.delete(place, _manifest_name(phone_id))
    return (jsonify({'error': error}), 409) if error else jsonify({'success': True})


def _change(phone_id: str, work):
    session, refused = hub_data.need_session('photos')
    if refused:
        return refused
    place, bad = _place(session)
    if bad or place is None:
        return bad
    with hub_data.lock(place, _manifest_name(phone_id)):
        phone, error = _load(place, phone_id)
        if phone is None:
            return jsonify({'error': error}), 404
        answer, problem = work(place, phone)
        if problem:
            return jsonify({'error': problem}), 400
        error = hub_data.write(place, _manifest_name(phone_id), phone)
        if error:
            return jsonify({'error': error}), 409
    return jsonify({'success': True, **answer})


def _checked_items(raw: Any) -> Tuple[List[Dict[str, Any]], str]:
    if not isinstance(raw, list) or len(raw) > MAX_BATCH:
        return [], f'Send up to {MAX_BATCH} pictures at a time.'
    out = []
    for item in raw:
        if not isinstance(item, dict) or not LOCAL_ID_RE.match(str(item.get('id') or '')):
            return [], 'A picture is not described properly.'
        try:
            size, modified = int(str(item.get('size'))), int(item.get('modified') or 0)
        except (TypeError, ValueError):
            return [], 'A picture is not described properly.'
        name = folder_name(item.get('name'), '')
        if not name or size < 0:
            return [], 'A picture is not described properly.'
        out.append({'id': str(item['id']), 'album': folder_name(item.get('album'), 'Camera'), 'name': name,
                    'size': size, 'modified': modified})
    return out, ''


def _ids(raw: Any) -> List[str]:
    return [str(i) for i in raw if LOCAL_ID_RE.match(str(i))][:MAX_BATCH] if isinstance(raw, list) else []


@bp.post('/api/photos/phones/<phone_id>/plan')
def plan(phone_id):
    """What to do now: {items: what the phone has in the chosen albums,
    deleted: ids gone from the phone, keep: ids freed up on purpose}.
    Answers {upload: [{id, share, path, name}], delete_on_phone: [ids]}."""
    body = request.get_json(silent=True) or {}
    items, problem = _checked_items(body.get('items') or [])
    deleted, keep = set(_ids(body.get('deleted'))), set(_ids(body.get('keep')))

    def work(place: hub_data.Place, phone: Dict[str, Any]):
        if problem:
            return None, problem
        known: Dict[str, List[Any]] = phone['items']
        base = _rel(place, phone['folder'])
        trashed: Optional[List[str]] = None
        listings: Dict[str, Optional[Dict[str, Dict[str, Any]]]] = {}

        def listing(album: str) -> Optional[Dict[str, Dict[str, Any]]]:
            if album not in listings:
                listings[album] = _list(place, f'{base}/{album}')
            return listings[album]

        moved_to_trash = 0
        for local_id in deleted:
            entry = known.get(local_id)
            if not entry:
                continue
            if local_id in keep or _flag(entry) in ('kept', 'moved'):
                del known[local_id]            # freed up on the phone, or moved on the NAS: stays there
                continue
            album, name = entry[0].split('/', 1)
            if name in (listing(album) or {}):
                result, _ = files_manager.run_helper(['files-trash', place.root, os.path.join(place.root, base, album),
                                                      name], user=place.user)
                moved_to_trash += result is not None
                listings.pop(album, None)
            del known[local_id]

        delete_on_phone, forgotten = [], 0
        for local_id, entry in list(known.items()):
            if _flag(entry) == 'moved':
                continue
            album, name = entry[0].split('/', 1)
            if name in (listing(album) or {}):
                continue
            if trashed is None:
                trashed = _trashed(place)
            if _in_trash(f'{base}/{album}/{name}', trashed):
                delete_on_phone.append(local_id)      # deleted on the NAS: the phone follows
            else:
                # Moved or renamed on the NAS: nothing happens on the phone, and it
                # is not uploaded again either.
                known[local_id] = entry[:4] + ['moved']
                forgotten += 1

        upload = []
        made: Set[str] = set()
        for item in items:
            if item['id'] in known or item['id'] in deleted:
                continue
            there = listing(item['album'])
            same = (there or {}).get(item['name'])
            if same and int(same.get('size_bytes') or -1) == item['size']:
                known[item['id']] = [f'{item["album"]}/{item["name"]}', item['size'], item['modified'], _now()]
                continue
            if there is None and item['album'] not in made:
                _mkdir(place, f'{base}/{item["album"]}')
                made.add(item['album'])
            name = _free_name(item['name'], there or {}, item['id'])
            upload.append({'id': item['id'], 'share': place.share, 'path': f'{base}/{item["album"]}', 'name': name})
        for local_id in keep & set(known):
            if _flag(known[local_id]) != 'moved':
                known[local_id] = known[local_id][:4] + ['kept']
        phone['last_sync'] = _now()
        return {'upload': upload, 'delete_on_phone': delete_on_phone, 'trashed': moved_to_trash,
                'forgotten': forgotten}, ''

    return _change(phone_id, work)


@bp.post('/api/photos/phones/<phone_id>/done')
def done(phone_id):
    """Uploads that finished: {items: [{id, path, name, size, modified}]}."""
    raw = (request.get_json(silent=True) or {}).get('items')

    def work(place: hub_data.Place, phone: Dict[str, Any]):
        if not isinstance(raw, list) or len(raw) > MAX_BATCH:
            return None, 'Nothing to note.'
        base = _rel(place, phone['folder'])
        for item in raw:
            if not isinstance(item, dict) or not LOCAL_ID_RE.match(str(item.get('id') or '')):
                continue
            path, name = str(item.get('path') or ''), folder_name(item.get('name'), '')
            if not path.startswith(base + '/') or '/' in path[len(base) + 1:] or not name:
                continue                                   # only into this phone's own album folders
            try:
                size, modified = int(str(item.get('size'))), int(item.get('modified') or 0)
            except (TypeError, ValueError):
                continue
            phone['items'][str(item['id'])] = [f'{path[len(base) + 1:]}/{name}', size, modified, _now()]
        return {'count': len(phone['items'])}, ''

    return _change(phone_id, work)


@bp.post('/api/photos/phones/<phone_id>/deleted')
def deleted_on_phone(phone_id):
    """The phone deleted what `plan` asked it to: {ids}."""
    ids = set(_ids((request.get_json(silent=True) or {}).get('ids')))

    def work(place: hub_data.Place, phone: Dict[str, Any]):
        for local_id in ids:
            phone['items'].pop(local_id, None)
        return {'count': len(phone['items'])}, ''

    return _change(phone_id, work)
