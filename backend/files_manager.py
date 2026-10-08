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
    'audio/flac', 'audio/mp4', 'video/quicktime', 'video/x-m4v', 'video/x-matroska', 'video/ogg',
    'video/3gpp', 'video/3gpp2', 'audio/aac', 'audio/x-m4a',
}
# Python's list does not know every type a phone makes.
EXTRA_TYPES = {'.mov': 'video/quicktime', '.m4v': 'video/x-m4v', '.mkv': 'video/x-matroska', '.ogv': 'video/ogg',
               '.3gp': 'video/3gpp', '.3g2': 'video/3gpp2', '.m4a': 'audio/mp4', '.aac': 'audio/aac',
               '.opus': 'audio/ogg', '.webm': 'video/webm'}
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
    guessed = EXTRA_TYPES.get(ext) or mimetypes.guess_type(name)[0] or 'application/octet-stream'
    if inline and guessed in INLINE_TYPES:
        return guessed, True
    return 'application/octet-stream', False


def parse_range(header: Optional[str], size: int) -> Tuple[Optional[Tuple[int, int]], bool]:
    """One byte range from a Range header: ((start, length), ok). No header:
    (None, True). A range that cannot be served: (None, False)."""
    if not header:
        return None, True
    import re
    m = re.fullmatch(r'\s*bytes=(\d*)-(\d*)\s*', header)
    if not m or (not m.group(1) and not m.group(2)) or size <= 0:
        return None, False
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:
        start = max(0, size - int(m.group(2)))
        end = size - 1
    if start >= size or end < start:
        return None, False
    return (start, end - start + 1), True


def file_size(path: str, user: Optional[str] = None) -> Optional[int]:
    result, _ = run_helper(['file-size', path], timeout=30, user=user)
    size = (result or {}).get('size')
    return size if isinstance(size, int) and size >= 0 else None


def open_stream(path: str, chunk: int = 256 * 1024, part: Optional[Tuple[int, int]] = None,
                user: Optional[str] = None) -> Tuple[Optional[Iterator[bytes]], str]:
    """Stream a file (or one part) through `alvaos-priv read-file`. The first
    chunk is read before answering, so a refused or missing file becomes an
    error, not an empty download."""
    cmd = _helper_cmd(['read-file', path] + ([str(part[0]), str(part[1])] if part else []), user)
    return _stream(cmd, chunk)


def open_zip(path: str, user: Optional[str] = None) -> Tuple[Optional[Iterator[bytes]], str]:
    """A folder as a ZIP stream through `alvaos-priv files-zip`."""
    return _stream(_helper_cmd(['files-zip', path], user), 256 * 1024)


def _stream(cmd: List[str], chunk: int) -> Tuple[Optional[Iterator[bytes]], str]:
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


# ── Changes (alvaos-priv files-* operations) ─────────────────────────────────

def _helper_cmd(args: List[str], user: Optional[str] = None) -> List[str]:
    """The helper command line; with `user`, the helper drops root to that
    person first so Linux checks their rights."""
    cmd = [PRIV_HELPER] + (['--as', user] if user else []) + args
    return cmd if is_root_user() else ['sudo', '-n'] + cmd


def _result(returncode: int, out: bytes, err: bytes) -> Tuple[Optional[Dict[str, Any]], str]:
    if returncode != 0:
        message = err.decode('utf-8', 'replace').strip().replace('alvaos-priv: denied: ', '')
        return None, message.replace('alvaos-priv: ', '') or 'That did not work.'
    lines = out.decode('utf-8', 'replace').strip().splitlines()
    try:
        import json
        return (json.loads(lines[-1]) if lines else {}), ''
    except ValueError:
        return {}, ''


def run_helper(args: List[str], timeout: int = 600,
               user: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        res = subprocess.run(_helper_cmd(args, user), capture_output=True, timeout=timeout, env={'LC_ALL': 'C'})
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    return _result(res.returncode, res.stdout, res.stderr)


def upload(dir_path: str, name: str, stream: Any, chunk: int = 1024 * 1024,
           user: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """Pipe an upload into `files-write`; the helper never overwrites."""
    return pipe_helper(['files-write', dir_path, name], stream, chunk, user)


def pipe_helper(args: List[str], stream: Any, chunk: int = 1024 * 1024,
                user: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """Run a helper operation with the request body on its stdin."""
    try:
        proc = subprocess.Popen(_helper_cmd(args, user), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={'LC_ALL': 'C'})
    except OSError as exc:
        return None, str(exc)
    assert proc.stdin is not None
    try:
        while True:
            data = stream.read(chunk)
            if not data:
                break
            proc.stdin.write(data)
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass   # the helper refused or stopped; its answer says why
    except BaseException:
        proc.kill()   # the browser went away: the helper removes the half file
        raise
    try:
        out, err = proc.communicate(timeout=120)   # also closes stdin: the end of the upload
    except ValueError:
        # stdin was already broken (the helper refused early): read its answer.
        out = proc.stdout.read() if proc.stdout else b''
        err = proc.stderr.read() if proc.stderr else b''
        proc.wait(timeout=120)
    return _result(proc.returncode, out, err)


def share_root(shares_state: Dict[str, Any], share_name: str) -> Optional[str]:
    share = find_share(shares_state, share_name)
    return os.path.normpath(share['path']) if share else None


def purge_all_trash(shares_state: Dict[str, Any], days: int = 30) -> None:
    """Daily: trash items older than `days` are removed for good."""
    for share in (shares_state or {}).values():
        if isinstance(share, dict) and str(share.get('path', '')).startswith('/'):
            run_helper(['files-trash-purge', os.path.normpath(share['path']), str(days)])


def list_entries(path: str, user: Optional[str] = None) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """A folder through `files-list` (as the person, when given)."""
    result, error = run_helper(['files-list', path], timeout=60, user=user)
    if result is None:
        return None, error or 'This folder could not be read.'
    entries = result.get('entries')
    return (entries if isinstance(entries, list) else []), ''
