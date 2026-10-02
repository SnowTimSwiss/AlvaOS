#!/usr/bin/env python3
"""AlvaOS Files, first step: browse and download the shared folders.

The backend user cannot read the share folders (they belong to the share
groups), so listing goes through the helper's `find` rule and reading through
its `read-file` operation, which checks the opened file itself is on a pool.
Only the shares' own folders can be reached: every request names a share and
a path inside it, never a path on the NAS.
"""

import mimetypes
import os
import subprocess
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from backup_manager import CMD as BACKUP_CMD, FIND_LIST_FORMAT, clean_relative_path, parse_find_listing
from common import PRIV_HELPER, is_root_user

# Shown in the browser as they are; everything else is downloaded.
INLINE_TYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif', 'image/bmp',
    'application/pdf', 'video/mp4', 'video/webm', 'audio/mpeg', 'audio/ogg', 'audio/wav',
    'audio/flac', 'audio/mp4',
}
# Text is shown as plain text, never as HTML or script.
TEXT_EXTENSIONS = {'.txt', '.md', '.log', '.csv', '.json', '.xml', '.yml', '.yaml', '.ini', '.conf', '.cfg'}


def find_share(shares_state: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for share in (shares_state or {}).values():
        if isinstance(share, dict) and share.get('name') == name and str(share.get('path', '')).startswith('/'):
            return share
    return None


def resolve(shares_state: Dict[str, Any], share_name: str, rel_path: str) -> Tuple[Optional[str], str, str]:
    """(absolute path, clean relative path, error)."""
    share = find_share(shares_state, share_name)
    if not share:
        return None, '', 'This shared folder does not exist.'
    rel = clean_relative_path(rel_path)
    if rel is None:
        return None, '', 'Invalid path.'
    base = os.path.normpath(share['path'])
    return (os.path.join(base, rel) if rel else base), rel, ''


def list_folder(path: str, run: Callable) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    res, err = run([BACKUP_CMD['FIND'], path, '-mindepth', '1', '-maxdepth', '1', '-printf', FIND_LIST_FORMAT],
                   timeout=60)
    if err or res is None or res.returncode != 0:
        return None, 'This folder could not be read.'
    return parse_find_listing(res.stdout), ''


def content_type(name: str, inline: bool) -> Tuple[str, bool]:
    """(Content-Type, shown inline). HTML, SVG and scripts are never inline."""
    ext = os.path.splitext(name)[1].lower()
    if inline and ext in TEXT_EXTENSIONS:
        return 'text/plain; charset=utf-8', True
    guessed = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    if inline and guessed in INLINE_TYPES:
        return guessed, True
    return 'application/octet-stream', False


def open_stream(path: str, chunk: int = 256 * 1024) -> Tuple[Optional[Iterator[bytes]], str]:
    """Stream a file through `alvaos-priv read-file`. The first chunk is read
    before answering, so a refused or missing file becomes an error, not an
    empty download."""
    cmd = [PRIV_HELPER, 'read-file', path]
    if not is_root_user():
        cmd = ['sudo', '-n'] + cmd
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={'LC_ALL': 'C'})
    except OSError as exc:
        return None, str(exc)
    assert proc.stdout is not None
    first = proc.stdout.read(chunk)
    if not first:
        proc.wait(timeout=30)
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else b'').decode('utf-8', 'replace').strip()
            return None, err.replace('alvaos-priv: ', '') or 'The file could not be read.'

    def chunks() -> Iterator[bytes]:
        try:
            if first:
                yield first
            while True:
                data = proc.stdout.read(chunk)  # type: ignore[union-attr]
                if not data:
                    break
                yield data
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    return chunks(), ''
