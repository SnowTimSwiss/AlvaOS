#!/usr/bin/env python3
"""The Hub calendar over CalDAV, so phones and computers sync on their own:
iPhone and Mac (Settings › Calendar › Accounts › CalDAV), Android with
DAVx5, Thunderbird, Outlook with a CalDAV add-in.

It runs inside the Hub server (port 8090, HTTPS 9443) and works on the same
calendar.json files as the Calendar page (hub_calendar.py, hub_data.py), as
the signed-in person through the helper. People sign in with the same name
and password as for the shares (HTTP Basic, files_dav.signed_in); the admin
account is not offered, like for WebDAV.

    /.well-known/caldav          -> /dav/
    /dav/<person>/               the person and their calendars
    /dav/<person>/own.main/      one calendar of a place (events)
    /dav/<person>/own~tasks/     the tasks of a place (VTODO, Reminders on an iPhone)
    /dav/<person>/.../<name>.ics one event or task

Places are the person's own calendar file and every family calendar
(shared folder) they may open; read-only ones say so to the phone.

What the page cannot show is simplified when a phone saves it: one repeat
rule (daily, weekdays, weekly, monthly, yearly, with an end day; "every
2 weeks" becomes weekly), no exceptions to a repeat, no reminders. Times are
local times of the NAS ("floating"), like on the page. Nothing here needs a
library: iCalendar is a simple line format.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit
from xml.sax.saxutils import escape

from flask import Blueprint, Response, redirect, request

import hub_calendar as cal
import hub_data

bp = Blueprint('hub_caldav', __name__)

DAV, CALDAV, CS, APPLE = 'DAV:', 'urn:ietf:params:xml:ns:caldav', 'http://calendarserver.org/ns/', \
    'http://apple.com/ns/ical/'
PREFIX = {DAV: 'D', CALDAV: 'C', CS: 'CS', APPLE: 'A'}
METHODS = ['OPTIONS', 'GET', 'HEAD', 'PUT', 'DELETE', 'PROPFIND', 'PROPPATCH', 'REPORT', 'MKCOL', 'MKCALENDAR',
           'MOVE', 'COPY']
MAX_BODY = 512 * 1024
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._@+~=-]{0,199}$')
TASKS = '~tasks'


# ── iCalendar: reading ──────────────────────────────────────────────────────

def _unfold(text: str) -> List[str]:
    lines: List[str] = []
    for raw in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if raw[:1] in (' ', '\t') and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split_line(line: str) -> Tuple[str, Dict[str, str], str]:
    """('DTSTART', {'TZID': 'Europe/Zurich'}, '20261004T093000')."""
    head, value, quoted = '', '', False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ':' and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return '', {}, ''
    parts = head.split(';')
    params = {}
    for part in parts[1:]:
        key, _, val = part.partition('=')
        params[key.upper()] = val.strip('"')
    return parts[0].upper(), params, value


def _unescape(value: str) -> str:
    return re.sub(r'\\([\\;,nN])', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), value)


def components(text: str) -> List[Tuple[str, Dict[str, Tuple[Dict[str, str], str]]]]:
    """The VEVENTs and VTODOs of a calendar: [(kind, {NAME: (params, value)})].
    Nested parts (alarms, time zones) are skipped."""
    found, stack = [], []
    current: Dict[str, Tuple[Dict[str, str], str]] = {}
    for line in _unfold(text):
        name, params, value = _split_line(line)
        if name == 'BEGIN':
            stack.append(value.upper())
            if value.upper() in ('VEVENT', 'VTODO') and len(stack) == 2:
                current = {}
            continue
        if name == 'END':
            kind = stack.pop() if stack else ''
            if kind in ('VEVENT', 'VTODO') and len(stack) == 1:
                found.append((kind, current))
            continue
        if len(stack) == 2 and stack[-1] in ('VEVENT', 'VTODO') and name not in current:
            current[name] = (params, value)
    return found


def _local(params: Dict[str, str], value: str) -> Tuple[Optional[date], Optional[datetime]]:
    """(a whole day, or a local time of the NAS)."""
    value = value.strip()
    try:
        if params.get('VALUE', '').upper() == 'DATE' or re.fullmatch(r'\d{8}', value):
            return datetime.strptime(value[:8], '%Y%m%d').date(), None
        moment = datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
    except ValueError:
        return None, None
    if value.endswith('Z'):
        moment = moment.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    elif params.get('TZID'):
        try:
            from zoneinfo import ZoneInfo
            moment = moment.replace(tzinfo=ZoneInfo(params['TZID'])).astimezone().replace(tzinfo=None)
        except Exception:  # noqa: BLE001 - an unknown zone: keep the time as written
            pass
    return None, moment


def _duration(value: str) -> Optional[timedelta]:
    m = re.fullmatch(r'([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', value.strip())
    if not m:
        return None
    sign = -1 if m.group(1) == '-' else 1
    w, d, h, mi, s = (int(g or 0) for g in m.groups()[1:])
    return sign * timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def _add_months(day: date, months: int) -> date:
    year, month = divmod(day.month - 1 + months, 12)
    year += day.year
    month += 1
    for d in range(day.day, 27, -1):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    return date(year, month, min(day.day, 28))


def _repeat(rule: str, first: date) -> Tuple[str, str]:
    """('weekly', '2026-12-31') from an RRULE; what the page cannot show is simplified."""
    parts = dict(p.partition('=')[::2] for p in rule.upper().split(';') if '=' in p)
    freq = parts.get('FREQ', '')
    days = set(parts.get('BYDAY', '').split(',')) - {''}
    repeat = {'DAILY': 'daily', 'WEEKLY': 'weekly', 'MONTHLY': 'monthly', 'YEARLY': 'yearly'}.get(freq, '')
    if freq == 'WEEKLY' and days == {'MO', 'TU', 'WE', 'TH', 'FR'}:
        repeat = 'weekdays'
    if not repeat:
        return '', ''
    until = ''
    if parts.get('UNTIL'):
        day, moment = _local({}, parts['UNTIL'])
        until = (day or (moment.date() if moment else None) or first).isoformat()
    elif parts.get('COUNT', '').isdigit() and int(parts['COUNT']) > 0:
        n = int(parts['COUNT']) - 1
        if repeat == 'daily':
            last = first + timedelta(days=n)
        elif repeat == 'weekly':
            last = first + timedelta(weeks=n)
        elif repeat == 'weekdays':
            last = first
            while n:
                last += timedelta(days=1)
                n -= last.weekday() < 5
        elif repeat == 'monthly':
            last = _add_months(first, n)
        else:
            last = _add_months(first, 12 * n)
        until = last.isoformat()
    return repeat, until


def _text_of(props: Dict[str, Tuple[Dict[str, str], str]], name: str) -> str:
    return _unescape(props[name][1]) if name in props else ''


def event_from(props: Dict[str, Tuple[Dict[str, str], str]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """An event of the page from a VEVENT (not yet checked)."""
    if 'DTSTART' not in props:
        return None, 'The event has no start.'
    day, moment = _local(*props['DTSTART'])
    end_day, end_moment = _local(*props['DTEND']) if 'DTEND' in props else (None, None)
    length = _duration(props['DURATION'][1]) if 'DURATION' in props else None
    item: Dict[str, Any] = {'title': _text_of(props, 'SUMMARY'), 'location': _text_of(props, 'LOCATION'),
                            'notes': _text_of(props, 'DESCRIPTION')}
    if day:
        last = end_day - timedelta(days=1) if end_day else (day + length - timedelta(days=1) if length else day)
        item.update(all_day=True, start=day.isoformat(), end=max(last, day).isoformat())
        first = day
    elif moment:
        end = end_moment or (moment + length if length else moment + timedelta(hours=1))
        if end <= moment:
            end = moment + timedelta(minutes=15)
        item.update(all_day=False, start=moment.strftime('%Y-%m-%dT%H:%M'), end=end.strftime('%Y-%m-%dT%H:%M'))
        first = moment.date()
    else:
        return None, 'The start of the event could not be read.'
    if 'RRULE' in props:
        item['repeat'], item['until'] = _repeat(props['RRULE'][1], first)
    return item, ''


def task_from(props: Dict[str, Tuple[Dict[str, str], str]]) -> Dict[str, Any]:
    item: Dict[str, Any] = {'title': _text_of(props, 'SUMMARY') or 'Task', 'notes': _text_of(props, 'DESCRIPTION'),
                            'done': props.get('STATUS', ({}, ''))[1].upper() == 'COMPLETED' or 'COMPLETED' in props}
    if 'DUE' in props:
        day, moment = _local(*props['DUE'])
        if day:
            item['date'] = day.isoformat()
        elif moment:
            item['date'], item['time'] = moment.date().isoformat(), moment.strftime('%H:%M')
    return item


# ── iCalendar: writing ──────────────────────────────────────────────────────

def _escape(value: str) -> str:
    return value.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def _fold(line: str) -> str:
    out: List[str] = []
    data = line.encode('utf-8')
    while len(data) > 75:
        cut = 75 if not out else 74
        while cut and (data[cut] & 0xC0) == 0x80:   # never split a character
            cut -= 1
        out.append(data[:cut].decode('utf-8'))
        data = data[cut:]
    out.append(data.decode('utf-8'))
    return '\r\n '.join(out)


def _stamp(item: Dict[str, Any]) -> str:
    try:
        moment = datetime.fromisoformat(str(item.get('updated') or '')).astimezone(timezone.utc)
    except ValueError:
        moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return moment.strftime('%Y%m%dT%H%M%SZ')


def _uid(item: Dict[str, Any]) -> str:
    return str(item.get('uid') or f"{item['id']}@alvaos")


def ics_of(item: Dict[str, Any], kind: str) -> str:
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//AlvaOS//Hub Calendar//EN', 'CALSCALE:GREGORIAN']
    if kind == 'event':
        lines += ['BEGIN:VEVENT', f'UID:{_uid(item)}', f'DTSTAMP:{_stamp(item)}']
        if item.get('all_day'):
            start = date.fromisoformat(item['start'])
            end = date.fromisoformat(item['end']) + timedelta(days=1)
            lines += [f'DTSTART;VALUE=DATE:{start:%Y%m%d}', f'DTEND;VALUE=DATE:{end:%Y%m%d}']
        else:
            fmt = '%Y%m%dT%H%M00'
            lines += [f"DTSTART:{datetime.strptime(item['start'], '%Y-%m-%dT%H:%M').strftime(fmt)}",
                      f"DTEND:{datetime.strptime(item['end'], '%Y-%m-%dT%H:%M').strftime(fmt)}"]
        lines.append(f"SUMMARY:{_escape(str(item.get('title') or ''))}")
        for name, key in (('LOCATION', 'location'), ('DESCRIPTION', 'notes')):
            if item.get(key):
                lines.append(f'{name}:{_escape(str(item[key]))}')
        repeat = item.get('repeat')
        if repeat:
            rule = {'daily': 'FREQ=DAILY', 'weekdays': 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR', 'weekly': 'FREQ=WEEKLY',
                    'monthly': 'FREQ=MONTHLY', 'yearly': 'FREQ=YEARLY'}[repeat]
            if item.get('until'):
                last = date.fromisoformat(item['until'])
                rule += f';UNTIL={last:%Y%m%d}' if item.get('all_day') else f';UNTIL={last:%Y%m%d}T235959'
            lines.append(f'RRULE:{rule}')
        lines.append('END:VEVENT')
    else:
        lines += ['BEGIN:VTODO', f'UID:{_uid(item)}', f'DTSTAMP:{_stamp(item)}',
                  f"SUMMARY:{_escape(str(item.get('title') or ''))}"]
        if item.get('notes'):
            lines.append(f"DESCRIPTION:{_escape(str(item['notes']))}")
        if item.get('date'):
            day = date.fromisoformat(item['date'])
            if item.get('time'):
                lines.append(f"DUE:{day:%Y%m%d}T{item['time'].replace(':', '')}00")
            else:
                lines.append(f'DUE;VALUE=DATE:{day:%Y%m%d}')
        lines.append('STATUS:COMPLETED' if item.get('done') else 'STATUS:NEEDS-ACTION')
        lines.append('END:VTODO')
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(line) for line in lines) + '\r\n'


def etag(item: Dict[str, Any]) -> str:
    return '"' + hashlib.sha1(json.dumps(item, sort_keys=True).encode()).hexdigest() + '"'


# ── Collections: one per calendar of a place, one for the place's tasks ────

def _slug(place: hub_data.Place) -> str:
    return 'own' if place.own else f'shared-{place.share}'


def collections(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every calendar and task list this person syncs, with what is in it."""
    places, _ = cal._places(session)
    out = []
    for place in places:
        data, _ = hub_data.read(place, cal.FILE)
        if data is None:
            continue
        data = cal.clean(data, place)
        for c in data['calendars']:
            name = c['name'] if place.own else f"{place.name} · {c['name']}"
            out.append({'name': f"{_slug(place)}.{c['id']}", 'kind': 'event', 'place': place, 'calendar': c['id'],
                        'title': name, 'color': c['color'],
                        'items': [e for e in data['events'] if e.get('calendar') == c['id'] and e.get('id')]})
        out.append({'name': _slug(place) + TASKS, 'kind': 'task', 'place': place, 'calendar': '',
                    'title': 'Tasks' if place.own else f'{place.name} · Tasks', 'color': cal.COLORS[0],
                    'items': [t for t in data['tasks'] if t.get('id')]})
    return out


def href_of(item: Dict[str, Any]) -> str:
    return str(item.get('href') or f"{item['id']}.ics")


def ctag(coll: Dict[str, Any]) -> str:
    return hashlib.sha1(''.join(etag(i) for i in coll['items']).encode()).hexdigest()


# ── WebDAV answers ──────────────────────────────────────────────────────────

def _tag(ns: str, name: str) -> str:
    return f'{PREFIX[ns]}:{name}'


def _multistatus(responses: List[str]) -> Response:
    body = ('<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"'
            ' xmlns:CS="http://calendarserver.org/ns/" xmlns:A="http://apple.com/ns/ical/">'
            + ''.join(responses) + '</D:multistatus>')
    return Response(body, 207, {'Content-Type': 'application/xml; charset=utf-8'})


def _response(href: str, props: Dict[Tuple[str, str], str], wanted: Optional[List[Tuple[str, str]]]) -> str:
    keys = list(props) if wanted is None else wanted
    found = [k for k in keys if k in props]
    missing = [k for k in keys if k not in props and k[0] in PREFIX]
    out = f'<D:response><D:href>{escape(href)}</D:href>'
    if found or not missing:
        inner = ''.join(f'<{_tag(*k)}>{props[k]}</{_tag(*k)}>' if props[k] else f'<{_tag(*k)}/>' for k in found)
        out += f'<D:propstat><D:prop>{inner}</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>'
    if missing:
        inner = ''.join(f'<{_tag(*k)}/>' for k in missing)
        out += f'<D:propstat><D:prop>{inner}</D:prop><D:status>HTTP/1.1 404 Not Found</D:status></D:propstat>'
    return out + '</D:response>'


def _wanted(root: Optional[ET.Element]) -> Optional[List[Tuple[str, str]]]:
    """The properties asked for, or None for all of them."""
    if root is None:
        return None
    prop = root.find(f'{{{DAV}}}prop')
    if prop is None:
        return None
    out = []
    for child in prop:
        m = re.match(r'\{(.*)\}(.*)', child.tag)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _body_xml() -> Tuple[Optional[ET.Element], Optional[Response]]:
    data = request.get_data(cache=False) if (request.content_length or 0) <= MAX_BODY else None
    if data is None:
        return None, Response('Too large\n', 413)
    if not data.strip():
        return None, None
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        return None, Response('Not allowed\n', 400)
    try:
        return ET.fromstring(data), None
    except ET.ParseError:
        return None, Response('Bad XML\n', 400)


def _home(user: str) -> str:
    return f'/dav/{quote(user)}/'


def _principal_props(user: str) -> Dict[Tuple[str, str], str]:
    home = escape(_home(user))
    return {
        (DAV, 'resourcetype'): '<D:collection/><D:principal/>',
        (DAV, 'displayname'): escape(user),
        (DAV, 'current-user-principal'): f'<D:href>{home}</D:href>',
        (DAV, 'principal-URL'): f'<D:href>{home}</D:href>',
        (DAV, 'owner'): f'<D:href>{home}</D:href>',
        (CALDAV, 'calendar-home-set'): f'<D:href>{home}</D:href>',
        (CALDAV, 'calendar-user-address-set'): f'<D:href>{home}</D:href>',
        (DAV, 'current-user-privilege-set'): '<D:privilege><D:read/></D:privilege>',
        (DAV, 'supported-report-set'): '',
    }


def _collection_props(user: str, coll: Dict[str, Any]) -> Dict[Tuple[str, str], str]:
    writable = coll['place'].writable
    privileges = '<D:privilege><D:read/></D:privilege>' + (
        '<D:privilege><D:write/></D:privilege><D:privilege><D:write-content/></D:privilege>'
        '<D:privilege><D:bind/></D:privilege><D:privilege><D:unbind/></D:privilege>' if writable else '')
    comp = 'VEVENT' if coll['kind'] == 'event' else 'VTODO'
    return {
        (DAV, 'resourcetype'): '<D:collection/><C:calendar/>',
        (DAV, 'displayname'): escape(coll['title']),
        (DAV, 'owner'): f'<D:href>{escape(_home(user))}</D:href>',
        (DAV, 'current-user-principal'): f'<D:href>{escape(_home(user))}</D:href>',
        (DAV, 'current-user-privilege-set'): privileges,
        (DAV, 'supported-report-set'): ''.join(
            f'<D:supported-report><D:report><C:{r}/></D:report></D:supported-report>'
            for r in ('calendar-multiget', 'calendar-query')),
        (CALDAV, 'supported-calendar-component-set'): f'<C:comp name="{comp}"/>',
        (CS, 'getctag'): ctag(coll),
        (APPLE, 'calendar-color'): coll['color'] + 'FF',
        (DAV, 'getcontenttype'): 'text/calendar; charset=utf-8',
    }


def _item_props(coll: Dict[str, Any], item: Dict[str, Any], with_data: bool) -> Dict[Tuple[str, str], str]:
    props = {(DAV, 'resourcetype'): '', (DAV, 'getetag'): escape(etag(item)),
             (DAV, 'getcontenttype'): 'text/calendar; charset=utf-8; component=' +
             ('VEVENT' if coll['kind'] == 'event' else 'VTODO')}
    if with_data:
        props[(CALDAV, 'calendar-data')] = escape(ics_of(item, coll['kind']))
    return props


def _find(session: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    return next((c for c in collections(session) if c['name'] == name), None)


def _item_href(user: str, coll: Dict[str, Any], item: Dict[str, Any]) -> str:
    return f"{_home(user)}{coll['name']}/{quote(href_of(item))}"


def propfind(session: Dict[str, Any], parts: List[str]) -> Response:
    root, bad = _body_xml()
    if bad:
        return bad
    wanted = _wanted(root)
    depth = request.headers.get('Depth', '1')
    user = session['user']
    if not parts:   # /dav/: only says who is signed in
        props = _principal_props(user)
        return _multistatus([_response('/dav/', {k: props[k] for k in ((DAV, 'current-user-principal'),
                                                                        (DAV, 'resourcetype'))}
                                       | {(DAV, 'resourcetype'): '<D:collection/>'}, wanted)])
    if len(parts) == 1:
        out = [_response(_home(user), _principal_props(user), wanted)]
        if depth != '0':
            out += [_response(f"{_home(user)}{c['name']}/", _collection_props(user, c), wanted)
                    for c in collections(session)]
        return _multistatus(out)
    coll = _find(session, parts[1])
    if not coll:
        return Response('Not found\n', 404)
    if len(parts) == 2:
        out = [_response(f"{_home(user)}{coll['name']}/", _collection_props(user, coll), wanted)]
        if depth != '0':
            with_data = wanted is not None and (CALDAV, 'calendar-data') in wanted
            out += [_response(_item_href(user, coll, i), _item_props(coll, i, with_data), wanted)
                    for i in coll['items']]
        return _multistatus(out)
    item = next((i for i in coll['items'] if href_of(i) == parts[2]), None)
    if not item:
        return Response('Not found\n', 404)
    return _multistatus([_response(_item_href(user, coll, item),
                                   _item_props(coll, item, wanted is not None and (CALDAV, 'calendar-data') in wanted),
                                   wanted)])


def report(session: Dict[str, Any], parts: List[str]) -> Response:
    root, bad = _body_xml()
    if bad:
        return bad
    coll = _find(session, parts[1]) if len(parts) >= 2 else None
    if root is None or not coll:
        return Response('Not found\n', 404)
    wanted = _wanted(root)
    user = session['user']
    if root.tag == f'{{{CALDAV}}}calendar-multiget':
        out = []
        for h in root.findall(f'{{{DAV}}}href'):
            name = unquote(urlsplit((h.text or '').strip()).path).rstrip('/').rsplit('/', 1)[-1]
            item = next((i for i in coll['items'] if href_of(i) == name), None)
            if item:
                out.append(_response(_item_href(user, coll, item), _item_props(coll, item, True), wanted))
            else:
                out.append(f'<D:response><D:href>{escape((h.text or "").strip())}</D:href>'
                           '<D:status>HTTP/1.1 404 Not Found</D:status></D:response>')
        return _multistatus(out)
    if root.tag == f'{{{CALDAV}}}calendar-query':
        # Every item of the calendar: a home calendar is small, and filters by
        # time would have to know how each repeat unfolds.
        return _multistatus([_response(_item_href(user, coll, i), _item_props(coll, i, True), wanted)
                             for i in coll['items']])
    return Response('<?xml version="1.0" encoding="utf-8"?><D:error xmlns:D="DAV:"><D:supported-report/></D:error>',
                    403, {'Content-Type': 'application/xml; charset=utf-8'})


def get(session: Dict[str, Any], parts: List[str], head: bool) -> Response:
    coll = _find(session, parts[1]) if len(parts) == 3 else None
    item = next((i for i in coll['items'] if href_of(i) == parts[2]), None) if coll else None
    if not item or not coll:
        return Response('Not found\n', 404)
    body = ics_of(item, coll['kind'])
    return Response(b'' if head else body.encode('utf-8'), 200,
                    {'Content-Type': 'text/calendar; charset=utf-8', 'ETag': etag(item)})


def _precondition(existing: Optional[Dict[str, Any]]) -> Optional[Response]:
    match, none_match = request.headers.get('If-Match'), request.headers.get('If-None-Match')
    if none_match == '*' and existing:
        return Response('It is there already.\n', 412)
    if match and (not existing or (match != '*' and etag(existing) not in [m.strip() for m in match.split(',')])):
        return Response('It was changed in the meantime.\n', 412)
    return None


def _write(coll: Dict[str, Any], change) -> Tuple[Any, Optional[Response]]:
    """Read the place's file, change it, write it back; one change at a time."""
    place = coll['place']
    if not place.writable:
        return None, Response(f'You can only look at "{place.name}", not change it.\n', 403)
    with hub_data.lock(place, cal.FILE):
        data, error = hub_data.read(place, cal.FILE)
        if data is None:
            return None, Response(f'{error}\n', 409)
        data = cal.clean(data, place)
        result, refused = change(data)
        if refused:
            return None, refused
        error = hub_data.write(place, cal.FILE, data)
        if error:
            return None, Response(f'{error}\n', 409)
    return result, None


def put(session: Dict[str, Any], parts: List[str]) -> Response:
    if len(parts) != 3 or not NAME_RE.match(parts[2]):
        return Response('Not allowed here\n', 403)
    coll = _find(session, parts[1])
    if not coll:
        return Response('Not found\n', 404)
    if (request.content_length or 0) > MAX_BODY:
        return Response('Too large\n', 413)
    text = request.get_data(cache=False).decode('utf-8', 'replace')
    found = [(k, p) for k, p in components(text) if 'RECURRENCE-ID' not in p]
    want = 'VEVENT' if coll['kind'] == 'event' else 'VTODO'
    props = next((p for k, p in found if k == want), None)
    if props is None:
        return Response(f'Only a {want} belongs in this calendar.\n', 415)
    uid = props.get('UID', ({}, ''))[1].strip()[:255]

    def change(data: Dict[str, Any]):
        key = 'events' if coll['kind'] == 'event' else 'tasks'
        old = next((i for i in data[key] if href_of(i) == parts[2]
                    and (coll['kind'] == 'task' or i.get('calendar') == coll['calendar'])), None)
        refused = _precondition(old)
        if refused:
            return None, refused
        if coll['kind'] == 'event':
            raw, problem = event_from(props)
            if raw is None:
                return None, Response(problem + '\n', 400)
            raw.update(calendar=coll['calendar'], color=(old or {}).get('color', ''),
                       id=(old or {}).get('id', ''))
            item, problem = cal.check_event(raw, [c['id'] for c in data['calendars']])
        else:
            raw = task_from(props)
            raw['id'] = (old or {}).get('id', '')
            item, problem = cal.check_task(raw)
        if item is None:
            return None, Response(problem + '\n', 400)
        item['href'] = parts[2]
        if uid:
            item['uid'] = uid
        limit = cal.MAX_EVENTS if key == 'events' else cal.MAX_TASKS
        problem = cal._upsert(data[key], item, limit)
        if problem:
            return None, Response(problem + '\n', 507)
        return (item, old is None), None

    result, refused = _write(coll, change)
    if refused:
        return refused
    item, created = result
    return Response(b'', 201 if created else 204, {'ETag': etag(item)})


def delete(session: Dict[str, Any], parts: List[str]) -> Response:
    if len(parts) != 3:
        return Response('Calendars are made and deleted on the Calendar page.\n', 403)
    coll = _find(session, parts[1])
    if not coll:
        return Response('Not found\n', 404)

    def change(data: Dict[str, Any]):
        key = 'events' if coll['kind'] == 'event' else 'tasks'
        old = next((i for i in data[key] if href_of(i) == parts[2]
                    and (coll['kind'] == 'task' or i.get('calendar') == coll['calendar'])), None)
        if not old:
            return None, Response('Not found\n', 404)
        refused = _precondition(old)
        if refused:
            return None, refused
        data[key] = [i for i in data[key] if i is not old]
        return True, None

    _, refused = _write(coll, change)
    return refused or Response(b'', 204)


def proppatch(session: Dict[str, Any], parts: List[str]) -> Response:
    """Phones set a colour, a name and an order. The name and colour are
    kept (the colour as the nearest one of the page); the rest is accepted
    and forgotten, so the phone does not complain."""
    root, bad = _body_xml()
    if bad:
        return bad
    asked: List[Tuple[str, str]] = []
    values: Dict[Tuple[str, str], str] = {}
    if root is not None:
        for prop in root.iter(f'{{{DAV}}}prop'):
            for child in prop:
                m = re.match(r'\{(.*)\}(.*)', child.tag)
                if m and m.group(1) in PREFIX:
                    asked.append((m.group(1), m.group(2)))
                    values[(m.group(1), m.group(2))] = (child.text or '').strip()
    coll = _find(session, parts[1]) if len(parts) == 2 else None
    if coll and coll['kind'] == 'event' and coll['place'].writable:
        name = values.get((DAV, 'displayname'))
        color = values.get((APPLE, 'calendar-color'))

        def change(data: Dict[str, Any]):
            for c in data['calendars']:
                if c['id'] == coll['calendar']:
                    if name and coll['place'].own:
                        c['name'] = name[:80]
                    if color and re.fullmatch(r'#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?', color):
                        c['color'] = _nearest(color[:7])
            return True, None

        if name or color:
            _write(coll, change)
    href = request.path
    return _multistatus([_response(href, {k: '' for k in asked}, asked)])


def _nearest(color: str) -> str:
    def rgb(c: str) -> Tuple[int, int, int]:
        return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
    want = rgb(color)
    return min(cal.COLORS, key=lambda c: sum((a - b) ** 2 for a, b in zip(rgb(c), want, strict=True)))


# ── Routes ──────────────────────────────────────────────────────────────────

@bp.route('/.well-known/caldav', methods=METHODS)
def well_known():
    return redirect('/dav/', code=301)


@bp.route('/', methods=['PROPFIND'])
def root():
    """Some apps ask the address itself who is signed in, before /.well-known."""
    return dav('')


@bp.route('/dav', defaults={'rest': ''}, methods=METHODS, strict_slashes=False)
@bp.route('/dav/<path:rest>', methods=METHODS)
def dav(rest: str):
    if request.method == 'OPTIONS':
        return Response(b'', 200, {'DAV': '1, 2, 3, calendar-access', 'Allow': ', '.join(METHODS)})
    import files_dav
    session, refused = files_dav.signed_in('calendar')
    if refused or session is None:
        return refused
    parts = [unquote(p) for p in rest.split('/') if p]
    if parts and parts[0] != session['user']:
        return Response('Not found\n', 404)
    method = request.method
    if method == 'PROPFIND':
        return propfind(session, parts)
    if method == 'REPORT':
        return report(session, parts)
    if method in ('GET', 'HEAD'):
        if len(parts) < 3:
            return Response(f'AlvaOS Calendar for {session["user"]}. Add this address to the calendar app '
                            'of your phone or computer.\n', 200, {'Content-Type': 'text/plain; charset=utf-8'})
        return get(session, parts, method == 'HEAD')
    if method == 'PUT':
        return put(session, parts)
    if method == 'DELETE':
        return delete(session, parts)
    if method == 'PROPPATCH':
        return proppatch(session, parts)
    return Response('Calendars are made, moved and deleted on the Calendar page.\n', 403)
