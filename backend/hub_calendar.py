#!/usr/bin/env python3
"""Calendar in the AlvaOS Hub: calendars, events and tasks, like Google Calendar.

Each place keeps one file, calendar.json (hub_data.py): the person's own
place (in their personal folder, ".alvaos/calendar/") and every shared
folder the admin marked as a family calendar. A place holds calendars (name
and colour), events and tasks:

    {"calendars": [{"id": "main", "name": "Anna", "color": "#039be5"}],
     "events": [{"id", "calendar", "title", "start", "end", "all_day", "color",
                 "location", "notes", "repeat", "until"}],
     "tasks": [{"id", "title", "notes", "date", "time", "done"}]}

Times are local times of the NAS ("2026-10-04T09:30"), whole days are dates
("2026-10-04", the end day included). Repeating events are stored once and
repeated by the page for the days it shows.
"""

import re
import secrets
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

import hub_apps
import hub_data

bp = Blueprint('hub_calendar', __name__)

FILE = 'calendar.json'
# Google Calendar's colours: Peacock first, the default.
COLORS = ['#039be5', '#7986cb', '#33b679', '#8e24aa', '#e67c73', '#f6bf26', '#f4511e', '#3f51b5',
          '#616161', '#0b8043', '#d50000']
REPEATS = ('', 'daily', 'weekdays', 'weekly', 'monthly', 'yearly')
ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,40}$')
MAX_EVENTS = 20000
MAX_TASKS = 5000
MAX_CALENDARS = 30


def _new_id() -> str:
    return secrets.token_urlsafe(9)


def _text(value: Any, limit: int) -> str:
    return str(value or '').strip()[:limit]


def _day(value: Any) -> Optional[str]:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


def _moment(value: Any) -> Optional[str]:
    try:
        return datetime.strptime(str(value), '%Y-%m-%dT%H:%M').strftime('%Y-%m-%dT%H:%M')
    except ValueError:
        return None


def _color(value: Any, empty_ok: bool = True) -> Optional[str]:
    value = str(value or '').lower()
    if value in COLORS or (empty_ok and not value):
        return value
    return None


def defaults(place: hub_data.Place) -> Dict[str, Any]:
    return {'calendars': [{'id': 'main', 'name': place.name, 'color': COLORS[0] if place.own else COLORS[2]}],
            'events': [], 'tasks': []}


def clean(data: Dict[str, Any], place: hub_data.Place) -> Dict[str, Any]:
    """What a calendar file holds, with anything odd left out."""
    out = defaults(place)
    cals = [c for c in data.get('calendars') or [] if isinstance(c, dict) and ID_RE.match(str(c.get('id')))]
    if cals:
        out['calendars'] = [{'id': str(c['id']), 'name': _text(c.get('name'), 80) or 'Calendar',
                             'color': _color(c.get('color'), False) or COLORS[0]} for c in cals[:MAX_CALENDARS]]
    out['events'] = [e for e in (data.get('events') or []) if isinstance(e, dict)][:MAX_EVENTS]
    out['tasks'] = [t for t in (data.get('tasks') or []) if isinstance(t, dict)][:MAX_TASKS]
    return out


def check_event(item: Dict[str, Any], calendars: List[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    all_day = bool(item.get('all_day'))
    parse: Callable[[Any], Optional[str]] = _day if all_day else _moment
    start, end = parse(item.get('start')), parse(item.get('end') or item.get('start'))
    if not start or not end:
        return None, 'Choose when it starts and ends.'
    if end < start or (not all_day and end == start):
        return None, 'It has to end after it starts.'
    calendar = str(item.get('calendar') or calendars[0])
    if calendar not in calendars:
        return None, 'Choose one of the calendars.'
    color = _color(item.get('color'))
    if color is None:
        return None, 'Choose one of the colours.'
    repeat = str(item.get('repeat') or '')
    if repeat not in REPEATS:
        return None, 'Choose how it repeats.'
    until = _day(item.get('until')) if item.get('until') and repeat else None
    if item.get('until') and repeat and not until:
        return None, 'Choose the last day it repeats.'
    return {'id': str(item['id']) if ID_RE.match(str(item.get('id') or '')) else _new_id(),
            'calendar': calendar, 'title': _text(item.get('title'), 300), 'start': start, 'end': end,
            'all_day': all_day, 'color': color, 'location': _text(item.get('location'), 300),
            'notes': _text(item.get('notes'), 8000), 'repeat': repeat, 'until': until or ''}, ''


def check_task(item: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    title = _text(item.get('title'), 300)
    if not title:
        return None, 'Give the task a title.'
    day = _day(item.get('date')) if item.get('date') else ''
    if day is None:
        return None, 'Choose a day, or none.'
    time = str(item.get('time') or '')
    if time and (not day or not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', time)):
        return None, 'Choose a time on that day, or none.'
    return {'id': str(item['id']) if ID_RE.match(str(item.get('id') or '')) else _new_id(),
            'title': title, 'notes': _text(item.get('notes'), 8000), 'date': day, 'time': time,
            'done': bool(item.get('done'))}, ''


def check_calendar(item: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    name = _text(item.get('name'), 80)
    if not name:
        return None, 'Give the calendar a name.'
    color = _color(item.get('color'), False)
    if not color:
        return None, 'Choose one of the colours.'
    return {'id': str(item['id']) if ID_RE.match(str(item.get('id') or '')) else _new_id(),
            'name': name, 'color': color}, ''


def _places(session: Dict[str, Any]) -> Tuple[List[hub_data.Place], bool]:
    settings = hub_apps.load()
    own = hub_data.own_place('calendar', session, settings)
    return ([own] if own else []) + hub_data.shared_places('calendar', session, settings), own is not None


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@bp.get('/api/calendar')
def everything():
    """Every place with its calendars, events and tasks."""
    session, refused = hub_data.need_session('calendar')
    if refused:
        return refused
    places, has_own = _places(session)
    out, problems = [], []
    for place in places:
        data, error = hub_data.read(place, FILE)
        if data is None:
            problems.append(f'{place.name}: {error}')
            continue
        out.append({'id': place.id, 'name': place.name, 'own': place.own, 'writable': place.writable,
                    **clean(data, place)})
    return jsonify({'places': out, 'has_own': has_own, 'problems': problems, 'colors': COLORS,
                    'today': date.today().isoformat()})


def _change(session: Dict[str, Any], place_id: str, work: Callable[[Dict[str, Any]], Tuple[Any, str]]):
    places, _ = _places(session)
    place = next((p for p in places if p.id == place_id), None)
    if not place:
        return jsonify({'error': 'This calendar is not yours to change.'}), 404
    if not place.writable:
        return jsonify({'error': f'You can only look at "{place.name}", not change it.'}), 403
    with hub_data.lock(place, FILE):
        data, error = hub_data.read(place, FILE)
        if data is None:
            return jsonify({'error': error}), 409
        data = clean(data, place)
        result, problem = work(data)
        if problem:
            return jsonify({'error': problem}), 400
        error = hub_data.write(place, FILE, data)
        if error:
            return jsonify({'error': error}), 409
    return jsonify({'success': True, 'item': result, 'calendars': data['calendars']})


def _upsert(items: List[Dict[str, Any]], item: Dict[str, Any], limit: int) -> str:
    item['updated'] = _stamp()
    for i, old in enumerate(items):
        if old.get('id') == item['id']:
            items[i] = item
            return ''
    if len(items) >= limit:
        return 'There are too many already.'
    items.append(item)
    return ''


@bp.post('/api/calendar/item')
def save_item():
    """Make or change one event, task or calendar: {place, kind, item}."""
    session, refused = hub_data.need_session('calendar')
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    kind, raw = body.get('kind'), body.get('item')
    if kind not in ('event', 'task', 'calendar') or not isinstance(raw, dict):
        return jsonify({'error': 'Nothing to save.'}), 400

    def work(data: Dict[str, Any]) -> Tuple[Any, str]:
        if kind == 'event':
            item, problem = check_event(raw, [c['id'] for c in data['calendars']])
            return (item, problem or _upsert(data['events'], item, MAX_EVENTS)) if item else (None, problem)
        if kind == 'task':
            item, problem = check_task(raw)
            return (item, problem or _upsert(data['tasks'], item, MAX_TASKS)) if item else (None, problem)
        item, problem = check_calendar(raw)
        return (item, problem or _upsert(data['calendars'], item, MAX_CALENDARS)) if item else (None, problem)

    return _change(session, str(body.get('place') or ''), work)


@bp.post('/api/calendar/delete')
def delete_item():
    """Delete one event, task or calendar (with its events): {place, kind, id}."""
    session, refused = hub_data.need_session('calendar')
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    kind, item_id = body.get('kind'), str(body.get('id') or '')
    if kind not in ('event', 'task', 'calendar'):
        return jsonify({'error': 'Nothing to delete.'}), 400

    def work(data: Dict[str, Any]) -> Tuple[Any, str]:
        if kind == 'calendar':
            if len(data['calendars']) < 2:
                return None, 'Keep at least one calendar here.'
            data['calendars'] = [c for c in data['calendars'] if c['id'] != item_id]
            data['events'] = [e for e in data['events'] if e.get('calendar') != item_id]
        else:
            key = 'events' if kind == 'event' else 'tasks'
            data[key] = [e for e in data[key] if e.get('id') != item_id]
        return {'id': item_id}, ''

    return _change(session, str(body.get('place') or ''), work)
