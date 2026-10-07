#!/usr/bin/env python3
"""Makes the app's icons (res/drawable/ic_*.xml) from the same line icons the Hub uses,
so the native screens and the Hub inside the app draw the same pictures.

    python3 android/tools/icons.py            # writes the vector drawables
    python3 android/tools/icons.py --preview  # also writes a preview.html to look at them

The Hub's own icons are in frontend/files-app/app.js (the table `P`); a few more
(lucide.dev) are here. Only <path>, <rect>, <circle> and <line> are used.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, '..', 'app', 'src', 'main', 'res', 'drawable')
HUB_JS = os.path.join(HERE, '..', '..', 'frontend', 'files-app', 'app.js')

EXTRA = {
    'cloud-upload': '<path d="M12 13v8"/><path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/><path d="m8 17 4-4 4 4"/>',
    'settings': '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    'qr-code': '<rect width="5" height="5" x="3" y="3" rx="1"/><rect width="5" height="5" x="16" y="3" rx="1"/><rect width="5" height="5" x="3" y="16" rx="1"/><path d="M21 16h-3a2 2 0 0 0-2 2v3"/><path d="M21 21v.01"/><path d="M12 7v3a2 2 0 0 1-2 2H7"/><path d="M3 12h.01"/><path d="M12 3h.01"/><path d="M12 16v.01"/><path d="M16 12h1"/><path d="M21 12v.01"/><path d="M12 21v-1"/>',
    'wifi-off': '<path d="M12 20h.01"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/><path d="M5 12.859a10 10 0 0 1 5.17-2.69"/><path d="M19 12.859a10 10 0 0 0-2.007-1.523"/><path d="M2 8.82a15 15 0 0 1 4.177-2.643"/><path d="M22 8.82a15 15 0 0 0-11.288-3.764"/><path d="m2 2 20 20"/>',
    'more-vertical': '<circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/>',
    'arrow-left': '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    'hard-drive': '<path d="M22 12H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><path d="M6 16h.01"/><path d="M10 16h.01"/>',
    'shield-check': '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    'zap': '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    'sparkles': '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>',
}

# name in the app -> name in the table
ICONS = {
    'ic_tab_hub': 'grid', 'ic_tab_files': 'folder', 'ic_tab_photos': 'image', 'ic_tab_calendar': 'calendar',
    'ic_tab_chat': 'message-circle', 'ic_tab_backup': 'cloud-upload', 'ic_tab_settings': 'settings',
    'ic_qr': 'qr-code', 'ic_check': 'check', 'ic_wifi_off': 'wifi-off', 'ic_phone': 'phone', 'ic_trash': 'trash',
    'ic_back': 'arrow-left', 'ic_more': 'more-vertical', 'ic_link': 'link', 'ic_monitor': 'monitor',
    'ic_chevron': 'right', 'ic_download': 'download', 'ic_refresh': 'refresh', 'ic_nas': 'hard-drive',
    'ic_lock': 'shield-check', 'ic_logout': 'lock', 'ic_x': 'x', 'ic_plus': 'plus', 'ic_new': 'sparkles', 'ic_charge': 'zap',
}


def hub_icons():
    src = open(HUB_JS).read()
    table = src[src.index('const P = {'):src.index('const icon = ')]
    out = {}
    for m in re.finditer(r"^\s*'?([a-z-]+)'?: '(.*)',?$", table, re.M):
        out[m.group(1)] = m.group(2)
    return out


def attrs(tag):
    return dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', tag))


def num(x):
    s = ('%.3f' % x).rstrip('0').rstrip('.')
    return s or '0'


def rect(a):
    x, y, w, h = (float(a.get(k, 0)) for k in ('x', 'y', 'width', 'height'))
    rx = float(a.get('rx', a.get('ry', 0)))
    if not rx:
        return f'M{num(x)},{num(y)}h{num(w)}v{num(h)}h{num(-w)}z'
    return (f'M{num(x + rx)},{num(y)}H{num(x + w - rx)}A{num(rx)},{num(rx)} 0 0 1 {num(x + w)},{num(y + rx)}'
            f'V{num(y + h - rx)}A{num(rx)},{num(rx)} 0 0 1 {num(x + w - rx)},{num(y + h)}H{num(x + rx)}'
            f'A{num(rx)},{num(rx)} 0 0 1 {num(x)},{num(y + h - rx)}V{num(y + rx)}A{num(rx)},{num(rx)} 0 0 1 {num(x + rx)},{num(y)}z')


def circle(a):
    cx, cy, r = (float(a[k]) for k in ('cx', 'cy', 'r'))
    return f'M{num(cx - r)},{num(cy)}A{num(r)},{num(r)} 0 1 1 {num(cx + r)},{num(cy)}A{num(r)},{num(r)} 0 1 1 {num(cx - r)},{num(cy)}z'


NUMBER = r'-?(?:\d+\.?\d*|\.\d+)'


def absolute_start(d):
    """A path alone starts at 0,0 even with a lowercase m; joined to others it would not, so the
    first move becomes absolute (M). Pairs that follow it stay relative lines (l)."""
    m = re.match(r'm\s*(' + NUMBER + r')[\s,]*(' + NUMBER + r')(.*)$', d, re.S)
    if not m:
        return d
    rest = m.group(3)
    implicit = re.match(r'\s*(?:-|\.|\d)', rest) is not None
    return f'M{m.group(1)} {m.group(2)}' + ('l' if implicit else '') + rest


def to_path(svg):
    parts = []
    for m in re.finditer(r'<(path|rect|circle)\b([^>]*)/>', svg):
        a = attrs(m.group(2))
        if a.get('fill') == 'currentColor':
            continue
        d = a['d'] if m.group(1) == 'path' else rect(a) if m.group(1) == 'rect' else circle(a)
        parts.append(absolute_start(d))
    return ' '.join(parts)


def drawable(path):
    # One path per shape would be cleaner, but one path with every shape draws the same.
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!-- Made by android/tools/icons.py: the Hub's line icons. -->
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"
    android:tint="?attr/colorControlNormal">
    <path android:fillColor="#00000000" android:strokeColor="#FF000000" android:strokeWidth="1.8"
        android:strokeLineCap="round" android:strokeLineJoin="round"
        android:pathData="{path}" />
</vector>
'''


def main():
    table = {**hub_icons(), **EXTRA}
    missing = [v for v in ICONS.values() if v not in table]
    if missing:
        sys.exit(f'not in the table: {missing}')
    html = ['<body style="font:14px sans-serif;background:#fff;color:#111"><div style="display:flex;flex-wrap:wrap;gap:18px;padding:16px">']
    for name, source in ICONS.items():
        path = to_path(table[source])
        open(os.path.join(RES, name + '.xml'), 'w').write(drawable(path))
        html.append(f'<div style="width:90px;text-align:center"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#111" '
                    f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="{path}"/></svg><br>{name[3:]}</div>')
    print(f'{len(ICONS)} icons written to {os.path.relpath(RES)}')
    if '--preview' in sys.argv:
        open(os.path.join(HERE, 'preview.html'), 'w').write(''.join(html) + '</div></body>')


if __name__ == '__main__':
    main()
