#!/usr/bin/env python3
"""When a photo was taken (Photos in the Hub).

A copied or downloaded photo gets a new file date; the camera's own date is in
the photo (EXIF "DateTimeOriginal"). Photos sorts by that date when it knows it.

Lightweight on purpose:
- Only the start of the file is read (the EXIF block sits at the front), as the
  signed-in person through the helper, and parsed here in the unprivileged Hub
  process, never in the helper.
- What was found is kept in one small file in the Hub cache, keyed by the
  file's path, size and date, so each photo is read once. A changed photo is
  read again.
- Unknown dates are looked up in the background at low priority, a few
  hundred per visit; the view uses the file date until then.
"""

import hashlib
import json
import os
import re
import threading
from io import BytesIO
from typing import Callable, Dict, Iterable, List, Optional, Tuple

HEAD_BYTES = 128 * 1024
EXIF_TYPES = ('.jpg', '.jpeg', '.tif', '.tiff', '.webp')
BATCH = 300
MAX_ENTRIES = 200_000
DATE_RE = re.compile(r'^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})')

_lock = threading.Lock()
_busy: set = set()


def key(path: str, size, modified) -> str:
    return hashlib.sha256(f'{path}|{size}|{modified}'.encode()).hexdigest()[:32]


def taken_at(head: bytes) -> str:
    """'2024-07-14T18:03:22' from the start of a photo, or '' when it has none."""
    try:
        from PIL import Image
        with Image.open(BytesIO(head)) as img:
            exif = img.getexif()
            raw = exif.get_ifd(0x8769).get(36867) or exif.get_ifd(0x8769).get(36868) or exif.get(306)
    except Exception:  # noqa: BLE001 - broken, cut short or not a photo: no date
        return ''
    match = DATE_RE.match(str(raw or '').strip())
    if not match:
        return ''
    y, mo, d, h, mi, s = (int(x) for x in match.groups())
    if not (1900 < y < 2200 and 1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 61):
        return ''
    return f'{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}'


class DateCache:
    """path|size|date -> taken date ('' = the photo has none), in one JSON file."""

    def __init__(self, folder_for: Callable[[], str]):
        self.folder_for = folder_for
        self._data: Optional[Dict[str, str]] = None
        self._file = ''

    def _load(self) -> Dict[str, str]:
        file = os.path.join(self.folder_for(), 'photo-dates.json')
        if self._data is None or file != self._file:
            self._file = file
            try:
                with open(file) as f:
                    data = json.load(f)
                self._data = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def get(self, k: str) -> Optional[str]:
        with _lock:
            return self._load().get(k)

    def put(self, found: Dict[str, str]) -> None:
        if not found:
            return
        with _lock:
            data = self._load()
            data.update(found)
            if len(data) > MAX_ENTRIES:   # forget the oldest lookups
                for old in list(data)[:len(data) - MAX_ENTRIES]:
                    del data[old]
            try:
                os.makedirs(os.path.dirname(self._file), exist_ok=True)
                with open(self._file + '.tmp', 'w') as f:
                    json.dump(data, f)
                os.replace(self._file + '.tmp', self._file)
            except OSError:
                pass


def fill(items: List[Dict], base: str, cache: DateCache) -> List[Tuple[str, str]]:
    """Add 'taken_at' to the items whose date is known; return those still to look up.
    `base` is the folder on disk the items' 'folder' is relative to."""
    todo: List[Tuple[str, str]] = []
    for item in items:
        if not str(item.get('name', '')).lower().endswith(EXIF_TYPES):
            continue
        path = os.path.join(base, item.get('folder') or '', item['name'])
        k = key(path, item.get('size_bytes'), item.get('modified_at'))
        known = cache.get(k)
        if known:
            item['taken_at'] = known
        elif known is None:
            todo.append((k, path))
    return todo


def look_up(todo: Iterable[Tuple[str, str]], read_head: Callable[[str], Optional[bytes]], cache: DateCache,
            submit: Callable[[Callable], object]) -> None:
    """Read the dates of up to BATCH photos in the background."""
    batch: List[Tuple[str, str]] = []
    with _lock:
        for k, path in todo:
            if k not in _busy and len(batch) < BATCH:
                _busy.add(k)
                batch.append((k, path))
    if not batch:
        return

    def work():
        found = {}
        try:
            for k, path in batch:
                head = read_head(path)
                found[k] = taken_at(head) if head else ''
                if len(found) >= 50:
                    cache.put(found)
                    found = {}
            cache.put(found)
        finally:
            with _lock:
                _busy.difference_update(k for k, _ in batch)

    submit(work)
