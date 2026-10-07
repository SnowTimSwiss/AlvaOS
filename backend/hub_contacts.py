#!/usr/bin/env python3
"""Contacts in the AlvaOS Hub: a person's address book.

One file per person, contacts.json (hub_data.py), in their personal folder
(".alvaos/contacts/"), written as the person:

    {"contacts": [{"id", "first", "last", "org", "title", "nickname",
                   "phones": [{"type", "value"}], "emails": [{"type", "value"}],
                   "addresses": [{"type", "value"}], "birthday", "url", "notes",
                   "favourite", "updated", "uid", "href"}]}

`uid` and `href` are how a phone knows a contact (CardDAV, hub_carddav.py);
the page never shows them. vCard files can be imported and exported
(hub_vcard.py).
"""

import re
import secrets
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, Response, jsonify, request

import hub_apps
import hub_data
import hub_vcard

bp = Blueprint('hub_contacts', __name__)

FILE = 'contacts.json'
MAX_CONTACTS = 5000
MAX_IMPORT = 2 * 1024 * 1024
ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,40}$')
LIMITS = {'first': 80, 'last': 80, 'org': 120, 'title': 120, 'nickname': 80, 'url': 300, 'notes': 4000}


def _text(value: Any, limit: int) -> str:
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', str(value or '')).strip()[:limit]


def _entries(raw: Any, kinds: Tuple[str, ...], limit: int, one_line: bool) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for e in (raw if isinstance(raw, list) else [])[:20]:
        if not isinstance(e, dict):
            continue
        value = _text(e.get('value'), limit)
        if one_line:
            value = re.sub(r'\s+', ' ', value)
        if value:
            kind = str(e.get('type') or 'other')
            out.append({'type': kind if kind in kinds else 'other', 'value': value})
    return out


def check_contact(raw: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    item: Dict[str, Any] = {k: _text(raw.get(k), n) for k, n in LIMITS.items()}
    for key in ('first', 'last', 'org', 'title', 'nickname', 'url'):
        item[key] = re.sub(r'\s+', ' ', item[key])
    item['phones'] = _entries(raw.get('phones'), hub_vcard.PHONE_TYPES, 40, True)
    item['emails'] = _entries(raw.get('emails'), hub_vcard.EMAIL_TYPES, 160, True)
    item['addresses'] = _entries(raw.get('addresses'), hub_vcard.ADDRESS_TYPES, 300, False)
    birthday = str(raw.get('birthday') or '').strip()
    if birthday and not hub_vcard.BIRTHDAY_RE.match(birthday):
        return None, 'The birthday is not a date.'
    item['birthday'] = birthday
    item['favourite'] = bool(raw.get('favourite'))
    if not (item['first'] or item['last'] or item['org'] or item['phones'] or item['emails']):
        return None, 'Give the contact a name, a phone number or an email address.'
    item['id'] = str(raw.get('id') or '') if ID_RE.match(str(raw.get('id') or '')) else secrets.token_urlsafe(9)
    return item, ''


def clean(data: Dict[str, Any]) -> Dict[str, Any]:
    contacts = data.get('contacts')
    return {'contacts': [c for c in contacts if isinstance(c, dict) and c.get('id')]
            if isinstance(contacts, list) else []}


def _place(session: Dict[str, Any]) -> Optional[hub_data.Place]:
    return hub_data.own_place('contacts', session, hub_apps.load())


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def upsert(items: List[Dict[str, Any]], item: Dict[str, Any]) -> str:
    item['updated'] = _stamp()
    for i, old in enumerate(items):
        if old.get('id') == item['id']:
            for key in ('uid', 'href'):                     # how a phone knows it
                if old.get(key) and not item.get(key):
                    item[key] = old[key]
            items[i] = item
            return ''
    if len(items) >= MAX_CONTACTS:
        return 'That is a lot of contacts. Delete some you no longer need.'
    items.append(item)
    return ''


@bp.get('/api/contacts')
def everything():
    session, refused = hub_data.need_session('contacts')
    if refused:
        return refused
    place = _place(session)
    if not place:
        return jsonify({'contacts': [], 'has_own': False, 'writable': False})
    data, error = hub_data.read(place, FILE)
    if data is None:
        return jsonify({'error': error}), 409
    contacts = [{k: v for k, v in c.items() if k not in ('uid', 'href')} for c in clean(data)['contacts']]
    return jsonify({'contacts': contacts, 'has_own': True, 'writable': place.writable})


def change(session: Dict[str, Any], work: Callable[[Dict[str, Any]], Tuple[Any, str]]):
    place = _place(session)
    if not place:
        return jsonify({'error': 'You have no place for contacts yet. Ask whoever runs this NAS for a '
                                 'personal folder.'}), 409
    if not place.writable:
        return jsonify({'error': 'You can only look at your contacts, not change them.'}), 403
    with hub_data.lock(place, FILE):
        data, error = hub_data.read(place, FILE)
        if data is None:
            return jsonify({'error': error}), 409
        data = clean(data)
        result, problem = work(data)
        if problem:
            return jsonify({'error': problem}), 400
        error = hub_data.write(place, FILE, data)
        if error:
            return jsonify({'error': error}), 409
    return jsonify({'success': True, 'result': result})


@bp.post('/api/contacts/item')
def save_item():
    """Make or change one contact: {item}."""
    session, refused = hub_data.need_session('contacts')
    if refused:
        return refused
    raw = (request.get_json(silent=True) or {}).get('item')
    if not isinstance(raw, dict):
        return jsonify({'error': 'Nothing to save.'}), 400

    def work(data: Dict[str, Any]) -> Tuple[Any, str]:
        item, problem = check_contact(raw)
        if item is None:
            return None, problem
        problem = upsert(data['contacts'], item)
        return ({k: v for k, v in item.items() if k not in ('uid', 'href')}, problem)

    return change(session, work)


@bp.post('/api/contacts/delete')
def delete_item():
    session, refused = hub_data.need_session('contacts')
    if refused:
        return refused
    ids = (request.get_json(silent=True) or {}).get('ids')
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'Nothing to delete.'}), 400
    gone = {str(i) for i in ids}

    def work(data: Dict[str, Any]) -> Tuple[Any, str]:
        before = len(data['contacts'])
        data['contacts'] = [c for c in data['contacts'] if c['id'] not in gone]
        return {'deleted': before - len(data['contacts'])}, ''

    return change(session, work)


@bp.post('/api/contacts/import')
def import_cards():
    """{text}: the contents of a .vcf file (many cards allowed). A card that is
    in the book already (same UID, or same name and number) is left alone."""
    session, refused = hub_data.need_session('contacts')
    if refused:
        return refused
    text = str((request.get_json(silent=True) or {}).get('text') or '')
    if not text.strip() or len(text) > MAX_IMPORT:
        return jsonify({'error': 'Choose a .vcf file of up to 2 MB.'}), 400
    cards = hub_vcard.cards(text)
    if not cards:
        return jsonify({'error': 'There are no contacts in this file.'}), 400
    counts = {'added': 0, 'skipped': 0}

    def work(data: Dict[str, Any]) -> Tuple[Any, str]:
        have_uid = {c.get('uid') for c in data['contacts'] if c.get('uid')}
        have_key = {_key(c) for c in data['contacts']}
        for card in cards:
            uid = card.pop('uid', '')
            item, _ = check_contact(card)
            if item is None or (uid and uid in have_uid) or _key(item) in have_key:
                counts['skipped'] += 1
                continue
            if uid:
                item['uid'] = uid[:255]
            problem = upsert(data['contacts'], item)
            if problem:
                return None, problem
            have_key.add(_key(item))
            counts['added'] += 1
        return dict(counts), ''

    return change(session, work)


def _key(c: Dict[str, Any]) -> str:
    phone = re.sub(r'\D', '', (c.get('phones') or [{}])[0].get('value', ''))
    email = ((c.get('emails') or [{}])[0].get('value', '')).lower()
    return f"{(c.get('first') or '').lower()}|{(c.get('last') or '').lower()}|{phone}|{email}"


@bp.get('/api/contacts/export')
def export_cards():
    session, refused = hub_data.need_session('contacts')
    if refused:
        return refused
    place = _place(session)
    data, error = hub_data.read(place, FILE) if place else ({}, '')
    if data is None:
        return jsonify({'error': error}), 409
    body = ''.join(hub_vcard.vcard_of(c, c.get('uid') or c['id']) for c in clean(data)['contacts'])
    return Response(body.encode('utf-8'), 200, {'Content-Type': 'text/vcard; charset=utf-8',
                                                'Content-Disposition': 'attachment; filename="contacts.vcf"'})
