#!/usr/bin/env python3
"""vCards for the Hub's Contacts: reading what phones and other address books
send, and writing a contact the way they like to get it (vCard 3.0, which
iPhone, Android with DAVx5, Thunderbird and Outlook all read).

A contact of the Hub (hub_contacts.py) is a small dictionary:

    {"id", "first", "last", "org", "title", "nickname", "phones": [{"type", "value"}],
     "emails": [{"type", "value"}], "addresses": [{"type", "value"}], "birthday", "url", "notes"}

with types "mobile", "home", "work" or "other". What the Hub does not keep is
dropped when a phone saves a contact: photos, groups, social profiles, other
custom fields. (The contact stays; those extras are not kept.)

Nothing here needs a library: vCard is a simple line format.
"""

import re
from typing import Any, Dict, List, Tuple

PHONE_TYPES = ('mobile', 'home', 'work', 'other')
EMAIL_TYPES = ('home', 'work', 'other')
ADDRESS_TYPES = ('home', 'work', 'other')
BIRTHDAY_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}|--\d{2}-\d{2})$')   # the year may be unknown: --03-09


def unfold(text: str) -> List[str]:
    lines: List[str] = []
    for raw in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if raw[:1] in (' ', '\t') and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def split_line(line: str) -> Tuple[str, str, Dict[str, List[str]], str]:
    """('item1', 'TEL', {'TYPE': ['CELL', 'VOICE']}, '+41 79 123 45 67'); group may be ''."""
    head, value, quoted = '', '', False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ':' and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return '', '', {}, ''
    parts = head.split(';')
    group, _, name = parts[0].rpartition('.')
    params: Dict[str, List[str]] = {}
    for part in parts[1:]:
        key, eq, val = part.partition('=')
        if not eq:                       # vCard 2.1: ";CELL" stands for TYPE=CELL
            key, val = 'TYPE', key
        params.setdefault(key.upper(), []).extend(v.strip('"').upper() if key.upper() == 'TYPE' else v.strip('"')
                                                  for v in val.split(','))
    return group.lower(), name.upper(), params, value


def unescape(value: str) -> str:
    return re.sub(r'\\([\\;,nN])', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), value)


def _split_semicolons(value: str) -> List[str]:
    """Fields of a structured value; "\\;" stays inside a field."""
    out, cur, i = [], '', 0
    while i < len(value):
        ch = value[i]
        if ch == '\\' and i + 1 < len(value):
            cur += value[i:i + 2]
            i += 2
            continue
        if ch == ';':
            out.append(cur)
            cur = ''
        else:
            cur += ch
        i += 1
    out.append(cur)
    return [unescape(p).strip() for p in out]


def _decode(params: Dict[str, List[str]], value: str) -> str:
    """vCard 2.1 from old phones: QUOTED-PRINTABLE values."""
    if 'QUOTED-PRINTABLE' in [e.upper() for e in params.get('ENCODING', [])]:
        import quopri
        charset = (params.get('CHARSET') or ['utf-8'])[0]
        try:
            return quopri.decodestring(value.encode('latin-1', 'replace')).decode(charset, 'replace')
        except (LookupError, ValueError):
            return value
    return value


def cards(text: str) -> List[Dict[str, Any]]:
    """Every VCARD in the text, as the Hub keeps contacts (not yet checked)."""
    out: List[Dict[str, Any]] = []
    current: List[Tuple[str, str, Dict[str, List[str]], str]] = []
    inside = False
    for line in unfold(text):
        group, name, params, value = split_line(line)
        if name == 'BEGIN' and value.upper() == 'VCARD':
            inside, current = True, []
            continue
        if name == 'END' and value.upper() == 'VCARD':
            if inside:
                out.append(_card(current))
            inside = False
            continue
        if inside and name:
            current.append((group, name, params, _decode(params, value)))
    return out


def _types(params: Dict[str, List[str]], labels: List[str]) -> List[str]:
    return [t.upper() for t in params.get('TYPE', [])] + [lab.upper() for lab in labels]


def _card(lines: List[Tuple[str, str, Dict[str, List[str]], str]]) -> Dict[str, Any]:
    labels: Dict[str, List[str]] = {}
    for group, name, _p, value in lines:                  # Apple: item1.X-ABLabel:_$!<Mobile>!$_
        if group and name == 'X-ABLABEL':
            labels.setdefault(group, []).append(re.sub(r'[_$!<>]', '', value))
    item: Dict[str, Any] = {'first': '', 'last': '', 'org': '', 'title': '', 'nickname': '', 'phones': [],
                            'emails': [], 'addresses': [], 'birthday': '', 'url': '', 'notes': '', 'uid': ''}
    full = ''
    for group, name, params, value in lines:
        types = _types(params, labels.get(group, []))
        if name == 'UID':
            item['uid'] = value.strip()
        elif name == 'FN':
            full = unescape(value).strip()
        elif name == 'N':
            f = _split_semicolons(value) + [''] * 5
            item['last'], item['first'] = f[0], ' '.join(p for p in (f[1], f[2]) if p)
        elif name == 'NICKNAME':
            item['nickname'] = unescape(value).strip()
        elif name == 'ORG':
            item['org'] = ', '.join(p for p in _split_semicolons(value) if p)
        elif name == 'TITLE':
            item['title'] = unescape(value).strip()
        elif name == 'TEL':
            number = value.strip()
            if number.lower().startswith('tel:'):
                number = number[4:]
            kind = 'mobile' if 'CELL' in types or 'MOBILE' in types else 'work' if 'WORK' in types \
                else 'home' if 'HOME' in types else 'other'
            item['phones'].append({'type': kind, 'value': number})
        elif name == 'EMAIL':
            kind = 'work' if 'WORK' in types else 'home' if 'HOME' in types else 'other'
            item['emails'].append({'type': kind, 'value': unescape(value).strip()})
        elif name == 'ADR':
            f = _split_semicolons(value) + [''] * 7
            street = ' '.join(p for p in (f[1], f[2]) if p)
            city = ' '.join(p for p in (f[5], f[3]) if p)
            lines_ = [p for p in (street, city, f[4], f[6]) if p]
            kind = 'work' if 'WORK' in types else 'home' if 'HOME' in types else 'other'
            if lines_:
                item['addresses'].append({'type': kind, 'value': '\n'.join(lines_)})
        elif name == 'BDAY':
            item['birthday'] = _birthday(value)
        elif name == 'URL':
            item['url'] = unescape(value).strip()
        elif name == 'NOTE':
            item['notes'] = unescape(value).strip()
    if not item['first'] and not item['last'] and full:
        first, _, last = full.rpartition(' ')
        item['first'], item['last'] = (first, last) if first else ('', last)
        if not first:
            item['first'], item['last'] = full, ''
    return item


def _birthday(value: str) -> str:
    v = value.strip().split('T')[0]
    m = re.fullmatch(r'(\d{4})-?(\d{2})-?(\d{2})', v)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    m = re.fullmatch(r'--(\d{2})-?(\d{2})', v)
    return f'--{m.group(1)}-{m.group(2)}' if m else ''


# ── Writing ─────────────────────────────────────────────────────────────────

def escape(value: str) -> str:
    return (value.replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;'))


def fold(line: str) -> str:
    """Lines of at most 75 bytes, continued with a space (never inside a UTF-8 character)."""
    raw = line.encode('utf-8')
    if len(raw) <= 75:
        return line
    parts: List[str] = []
    cur, size = '', 0
    for ch in line:
        n = len(ch.encode('utf-8'))
        if size + n > (75 if not parts else 74):
            parts.append(cur)
            cur, size = '', 0
        cur += ch
        size += n
    parts.append(cur)
    return '\r\n '.join(parts)


def display_name(item: Dict[str, Any]) -> str:
    name = ' '.join(p for p in (item.get('first'), item.get('last')) if p).strip()
    return name or str(item.get('org') or '') or next((e['value'] for e in item.get('emails', [])), '') \
        or next((p['value'] for p in item.get('phones', [])), '') or 'No name'


def vcard_of(item: Dict[str, Any], uid: str) -> str:
    lines = ['BEGIN:VCARD', 'VERSION:3.0', f'UID:{uid}', f'FN:{escape(display_name(item))}',
             f"N:{escape(item.get('last') or '')};{escape(item.get('first') or '')};;;"]
    if item.get('nickname'):
        lines.append(f"NICKNAME:{escape(item['nickname'])}")
    if item.get('org'):
        lines.append(f"ORG:{escape(item['org'])}")
    if item.get('title'):
        lines.append(f"TITLE:{escape(item['title'])}")
    for p in item.get('phones', []):
        t = {'mobile': 'CELL', 'home': 'HOME,VOICE', 'work': 'WORK,VOICE'}.get(p['type'], 'VOICE')
        lines.append(f"TEL;TYPE={t}:{p['value']}")
    for e in item.get('emails', []):
        t = {'home': 'HOME', 'work': 'WORK'}.get(e['type'], 'OTHER')
        lines.append(f"EMAIL;TYPE=INTERNET,{t}:{escape(e['value'])}")
    for a in item.get('addresses', []):
        t = {'home': 'HOME', 'work': 'WORK'}.get(a['type'], 'OTHER')
        rows = a['value'].split('\n') + ['', '', '', '']
        # street; city; region; (zip); country: what a phone shows in its own fields
        lines.append(f"ADR;TYPE={t}:;;{escape(rows[0])};{escape(rows[1])};{escape(rows[2])};;{escape(rows[3])}")
    if item.get('birthday'):
        b = item['birthday']
        lines.append(f"BDAY:{b.replace('-', '')}" if not b.startswith('--') else f"BDAY:--{b[2:].replace('-', '')}")
    if item.get('url'):
        lines.append(f"URL:{escape(item['url'])}")
    if item.get('notes'):
        lines.append(f"NOTE:{escape(item['notes'])}")
    lines.append('END:VCARD')
    return '\r\n'.join(fold(line) for line in lines) + '\r\n'
