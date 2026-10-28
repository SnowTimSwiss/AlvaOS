#!/usr/bin/env python3
"""The Hub's Contacts over CardDAV, so phones and computers sync their address
book on their own: iPhone and Mac (Settings › Contacts › Accounts › CardDAV),
Android with DAVx5, Thunderbird.

It lives beside CalDAV (hub_caldav.py), in the same place of the Hub server
and with the same sign-in (name and password of the person, HTTP Basic):

    /.well-known/carddav        -> /dav/
    /dav/<person>/contacts/     the person's address book
    /dav/<person>/contacts/<name>.vcf   one contact

The contacts are the ones of the Contacts page (hub_contacts.py, one
contacts.json in the person's folder); vCards are made and read in
hub_vcard.py, which also says what a phone's extras (photos, groups) become.
"""

import hashlib
import json
import re
from typing import Any
from urllib.parse import quote, unquote, urlsplit
from xml.sax.saxutils import escape

from flask import Response, request

import hub_caldav as dav
import hub_contacts as contacts
import hub_data
import hub_vcard

CARDDAV = 'urn:ietf:params:xml:ns:carddav'
NAME = 'contacts'
NAME_RE = dav.NAME_RE


def uid_of(item: dict[str, Any]) -> str:
    return str(item.get('uid') or item['id'])


def href_of(item: dict[str, Any]) -> str:
    return str(item.get('href') or f"{item['id']}.vcf")


def etag(item: dict[str, Any]) -> str:
    return '"' + hashlib.sha1(json.dumps(item, sort_keys=True).encode()).hexdigest() + '"'


def vcard(item: dict[str, Any]) -> str:
    return hub_vcard.vcard_of(item, uid_of(item))


def ctag(items: list[dict[str, Any]]) -> str:
    return hashlib.sha1(''.join(etag(i) for i in items).encode()).hexdigest()


def load(session: dict[str, Any]) -> tuple[hub_data.Place | None, list[dict[str, Any]]]:
    """The person's place for contacts and the contacts in it."""
    place = contacts._place(session)
    if not place:
        return None, []
    data, _ = hub_data.read(place, contacts.FILE)
    return place, contacts.clean(data or {})['contacts']


def collection_href(user: str) -> str:
    return f'{dav._home(user)}{NAME}/'


def item_href(user: str, item: dict[str, Any]) -> str:
    return f'{collection_href(user)}{quote(href_of(item))}'


def collection_props(user: str, place: hub_data.Place | None, items: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    writable = bool(place and place.writable)
    privileges = '<D:privilege><D:read/></D:privilege>' + (
        '<D:privilege><D:write/></D:privilege><D:privilege><D:write-content/></D:privilege>'
        '<D:privilege><D:bind/></D:privilege><D:privilege><D:unbind/></D:privilege>' if writable else '')
    home = escape(dav._home(user))
    return {
        (dav.DAV, 'resourcetype'): '<D:collection/><CR:addressbook/>',
        (dav.DAV, 'displayname'): 'Contacts',
        (dav.DAV, 'owner'): f'<D:href>{home}</D:href>',
        (dav.DAV, 'current-user-principal'): f'<D:href>{home}</D:href>',
        (dav.DAV, 'current-user-privilege-set'): privileges,
        (dav.DAV, 'supported-report-set'): ''.join(
            f'<D:supported-report><D:report><CR:{r}/></D:report></D:supported-report>'
            for r in ('addressbook-multiget', 'addressbook-query')),
        (CARDDAV, 'supported-address-data'): '<CR:address-data-type content-type="text/vcard" version="3.0"/>',
        (dav.CS, 'getctag'): ctag(items),
        (dav.DAV, 'getcontenttype'): 'text/vcard; charset=utf-8',
    }


def _item_props(item: dict[str, Any], with_data: bool) -> dict[tuple[str, str], str]:
    props = {(dav.DAV, 'resourcetype'): '', (dav.DAV, 'getetag'): escape(etag(item)),
             (dav.DAV, 'getcontenttype'): 'text/vcard; charset=utf-8'}
    if with_data:
        props[(CARDDAV, 'address-data')] = escape(vcard(item))
    return props


def _wants_data(wanted: list[tuple[str, str]] | None) -> bool:
    return wanted is not None and (CARDDAV, 'address-data') in wanted


def propfind(session: dict[str, Any], parts: list[str]) -> Response:
    root, bad = dav._body_xml()
    if bad:
        return bad
    wanted = dav._wanted(root)
    depth = request.headers.get('Depth', '1')
    user = session['user']
    place, items = load(session)
    if len(parts) == 2:
        out = [dav._response(collection_href(user), collection_props(user, place, items), wanted)]
        if depth != '0':
            out += [dav._response(item_href(user, i), _item_props(i, _wants_data(wanted)), wanted) for i in items]
        return dav._multistatus(out)
    item = next((i for i in items if href_of(i) == parts[2]), None)
    if not item:
        return Response('Not found\n', 404)
    return dav._multistatus([dav._response(item_href(user, item), _item_props(item, _wants_data(wanted)), wanted)])


def report(session: dict[str, Any], parts: list[str]) -> Response:
    root, bad = dav._body_xml()
    if bad:
        return bad
    if root is None or len(parts) < 2:
        return Response('Not found\n', 404)
    wanted = dav._wanted(root)
    user = session['user']
    _, items = load(session)
    if root.tag == f'{{{CARDDAV}}}addressbook-multiget':
        out = []
        for h in root.findall(f'{{{dav.DAV}}}href'):
            name = unquote(urlsplit((h.text or '').strip()).path).rstrip('/').rsplit('/', 1)[-1]
            item = next((i for i in items if href_of(i) == name), None)
            if item:
                out.append(dav._response(item_href(user, item), _item_props(item, True), wanted))
            else:
                out.append(f'<D:response><D:href>{escape((h.text or "").strip())}</D:href>'
                           '<D:status>HTTP/1.1 404 Not Found</D:status></D:response>')
        return dav._multistatus(out)
    if root.tag == f'{{{CARDDAV}}}addressbook-query':
        # A personal address book is small: everything, whatever the filter says.
        return dav._multistatus([dav._response(item_href(user, i), _item_props(i, True), wanted) for i in items])
    return Response('<?xml version="1.0" encoding="utf-8"?><D:error xmlns:D="DAV:"><D:supported-report/></D:error>',
                    403, {'Content-Type': 'application/xml; charset=utf-8'})


def get(session: dict[str, Any], parts: list[str], head: bool) -> Response:
    _, items = load(session)
    item = next((i for i in items if href_of(i) == parts[2]), None) if len(parts) == 3 else None
    if not item:
        return Response('Not found\n', 404)
    body = vcard(item)
    return Response(b'' if head else body.encode('utf-8'), 200,
                    {'Content-Type': 'text/vcard; charset=utf-8', 'ETag': etag(item)})


def _precondition(existing: dict[str, Any] | None) -> Response | None:
    match, none_match = request.headers.get('If-Match'), request.headers.get('If-None-Match')
    if none_match == '*' and existing:
        return Response('It is there already.\n', 412)
    if match and (not existing or (match != '*' and etag(existing) not in [m.strip() for m in match.split(',')])):
        return Response('It was changed in the meantime.\n', 412)
    return None


def _write(session: dict[str, Any], change) -> tuple[Any, Response | None]:
    place = contacts._place(session)
    if not place:
        return None, Response('You have no place for contacts yet.\n', 409)
    if not place.writable:
        return None, Response('You can only look at your contacts, not change them.\n', 403)
    with hub_data.lock(place, contacts.FILE):
        data, error = hub_data.read(place, contacts.FILE)
        if data is None:
            return None, Response(f'{error}\n', 409)
        data = contacts.clean(data)
        result, refused = change(data)
        if refused:
            return None, refused
        error = hub_data.write(place, contacts.FILE, data)
        if error:
            return None, Response(f'{error}\n', 409)
    return result, None


def put(session: dict[str, Any], parts: list[str]) -> Response:
    if len(parts) != 3 or not NAME_RE.match(parts[2]):
        return Response('Not allowed here\n', 403)
    if (request.content_length or 0) > dav.MAX_BODY:
        return Response('Too large\n', 413)
    text = request.get_data(cache=False).decode('utf-8', 'replace')
    found = hub_vcard.cards(text)
    if not found:
        return Response('Only a vCard belongs in the address book.\n', 415)
    card = found[0]
    uid = str(card.pop('uid', '') or '').strip()[:255]

    def change(data: dict[str, Any]):
        old = next((c for c in data['contacts'] if href_of(c) == parts[2]), None)
        if old is None and uid:
            old = next((c for c in data['contacts'] if c.get('uid') == uid), None)
        refused = _precondition(old)
        if refused:
            return None, refused
        raw = {**card, 'id': (old or {}).get('id', ''), 'favourite': (old or {}).get('favourite', False)}
        item, problem = contacts.check_contact(raw)
        if item is None:
            return None, Response(problem + '\n', 400)
        item['href'] = parts[2]
        if uid:
            item['uid'] = uid
        problem = contacts.upsert(data['contacts'], item)
        if problem:
            return None, Response(problem + '\n', 507)
        return (item, old is None), None

    result, refused = _write(session, change)
    if refused:
        return refused
    item, created = result
    return Response(b'', 201 if created else 204, {'ETag': etag(item)})


def delete(session: dict[str, Any], parts: list[str]) -> Response:
    if len(parts) != 3:
        return Response('The address book is part of the Hub and stays.\n', 403)

    def change(data: dict[str, Any]):
        old = next((c for c in data['contacts'] if href_of(c) == parts[2]), None)
        if not old:
            return None, Response('Not found\n', 404)
        refused = _precondition(old)
        if refused:
            return None, refused
        data['contacts'] = [c for c in data['contacts'] if c is not old]
        return True, None

    _, refused = _write(session, change)
    return refused or Response(b'', 204)


def proppatch(parts: list[str]) -> Response:
    """Phones set a name or an order on the book; it is accepted and forgotten, so they do not complain."""
    root, bad = dav._body_xml()
    if bad:
        return bad
    asked: list[tuple[str, str]] = []
    if root is not None:
        for prop in root.iter(f'{{{dav.DAV}}}prop'):
            for child in prop:
                m = re.match(r'\{(.*)\}(.*)', child.tag)
                if m and m.group(1) in dav.PREFIX:
                    asked.append((m.group(1), m.group(2)))
    return dav._multistatus([dav._response(request.path, {k: '' for k in asked}, asked)])
