#!/usr/bin/env python3
"""File changes in the shared folders, run as root by alvaos-priv.

Every operation gets a folder and a single name, never a path with "/" in
the name. The folder is opened without following a symlink and checked on
the open descriptor to be inside the data root; everything after that is
done relative to that descriptor (dir_fd), so swapping a folder for a
symlink meanwhile cannot reach anything else. Nothing is overwritten,
except the data files of Hub apps (see write_app_data).

Deleting moves the item into "<share>/.alvaos-trash/<stamp>/", next to a
small ".origin" file that says where it came from. Items older than
TRASH_DAYS are removed for good.
"""

import json
import os
import re
import secrets
import shutil
import stat
import time
from datetime import datetime, timedelta
from typing import Any, BinaryIO

DATA_ROOT = '/mnt/alvaos'
TRASH_DIR = '.alvaos-trash'
ORIGIN_FILE = '.origin'
TRASH_DAYS = 30
STAMP_FORMAT = '%Y%m%d-%H%M%S'


class FileOpError(Exception):
    """A sentence for the person: why it did not work."""


def check_name(name: str) -> str:
    if (not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or '\x00' in name
            or len(name.encode('utf-8', 'surrogateescape')) > 255):
        raise FileOpError('That name is not allowed.')
    if name == TRASH_DIR:
        raise FileOpError('That name is used for the trash.')
    return name


def _real(fd: int) -> str:
    return os.readlink(f'/proc/self/fd/{fd}')


def open_dir(path: str, root: str = DATA_ROOT) -> int:
    """A directory below root, opened without following a final symlink and
    checked on the descriptor."""
    if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
        raise FileOpError('Invalid folder.')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except PermissionError:
        raise FileOpError('You do not have access to this folder.') from None
    except OSError:
        raise FileOpError('This folder does not exist (any more).') from None
    if not _real(fd).startswith(root.rstrip('/') + '/'):
        os.close(fd)
        raise FileOpError('This folder is outside the storage pools.')
    return fd


def _open_subdir(name: str, dir_fd: int) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)


def _exists(name: str, dir_fd: int) -> bool:
    try:
        os.lstat(name, dir_fd=dir_fd)
        return True
    except FileNotFoundError:
        return False


def _like_parent(fd: int, parent_fd: int, is_dir: bool) -> None:
    """Group and permissions as the folder it is in, so the share's users can
    use it like files they made themselves. Running as a person (not root),
    the group may not be theirs to give; the folder's setgid bit sets it then."""
    st = os.fstat(parent_fd)
    try:
        os.fchown(fd, -1, st.st_gid)
    except PermissionError:
        pass
    mode = st.st_mode & 0o7777 if is_dir else (st.st_mode & 0o666) | 0o600
    try:
        os.fchmod(fd, mode)
    except PermissionError:
        pass


def list_dir(dir_path: str, root: str = DATA_ROOT) -> list[dict[str, Any]]:
    """What is in a folder: name, type, size, modified. Run as the person, the
    kernel decides what they may see."""
    dfd = open_dir(dir_path, root)
    try:
        entries = []
        for name in os.listdir(dfd):
            if name == TRASH_DIR:
                continue
            try:
                st = os.lstat(name, dir_fd=dfd)
            except OSError:
                continue
            kind = 'folder' if stat.S_ISDIR(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode) else \
                'link' if stat.S_ISLNK(st.st_mode) else 'other'
            entries.append({'name': name, 'type': kind,
                            'size_bytes': st.st_size if kind == 'file' else 0,
                            'modified_at': datetime.fromtimestamp(st.st_mtime).astimezone().isoformat()})
        entries.sort(key=lambda e: (e['type'] != 'folder', str(e['name']).lower()))
        return entries
    except PermissionError:
        raise FileOpError('You do not have access to this folder.') from None
    finally:
        os.close(dfd)


SEARCH_LIMIT = 200
SEARCH_SECONDS = 15
SEARCH_DEPTH = 40


def search(dir_path: str, query: str, root: str = DATA_ROOT, limit: int = SEARCH_LIMIT,
           seconds: float = SEARCH_SECONDS) -> dict[str, Any]:
    """Files and folders below a folder whose name holds every word of the
    query (any case). Never follows symlinks; folders the person cannot open
    are skipped. Stops at `limit` results, SEARCH_DEPTH levels or after
    `seconds`. Only one descriptor per level is open at a time."""
    words = [w for w in str(query or '').lower().split() if w][:8]
    if not words or len(query) > 200:
        raise FileOpError('Type what to look for.')
    deadline = time.monotonic() + seconds
    found: list[dict[str, Any]] = []
    state = {'complete': True}

    def walk(dfd: int, rel: str, depth: int) -> None:
        try:
            names = sorted(os.listdir(dfd))
        except OSError:
            return
        for name in names:
            if len(found) >= limit or time.monotonic() > deadline:
                state['complete'] = False
                return
            if name == TRASH_DIR or name.endswith(PART_SUFFIX):
                continue
            try:
                st = os.lstat(name, dir_fd=dfd)
            except OSError:
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
            lower = name.lower()
            if all(w in lower for w in words) and (is_dir or stat.S_ISREG(st.st_mode)):
                found.append({'name': name, 'folder': rel, 'type': 'folder' if is_dir else 'file',
                              'size_bytes': 0 if is_dir else st.st_size,
                              'modified_at': datetime.fromtimestamp(st.st_mtime).astimezone().isoformat()})
            if is_dir and depth < SEARCH_DEPTH:
                try:
                    sub = _open_subdir(name, dfd)
                except OSError:
                    continue
                try:
                    walk(sub, f'{rel}/{name}' if rel else name, depth + 1)
                finally:
                    os.close(sub)

    dfd = open_dir(dir_path, root)
    try:
        walk(dfd, '', 0)
    finally:
        os.close(dfd)
    found.sort(key=lambda e: (e['type'] != 'folder', str(e['name']).lower(), str(e['folder'])))
    return {'results': found, 'complete': state['complete']}


TREE_LIMIT = 200_000
TREE_SECONDS = 25


def tree(dir_path: str, root: str = DATA_ROOT, limit: int = TREE_LIMIT,
         seconds: float = TREE_SECONDS) -> dict[str, Any]:
    """Every file and folder below a folder, in one answer, for the sync
    folder of the computer app: relative path, kind, size, modification time
    (whole seconds). Never follows symlinks; skips the trash, unfinished
    uploads and what the person cannot open. `complete` is false when it
    stopped early (`limit`, SEARCH_DEPTH levels, `seconds`): then the
    computer must not take anything missing for deleted."""
    deadline = time.monotonic() + seconds
    found: list[dict[str, Any]] = []
    state = {'complete': True}

    def walk(dfd: int, rel: str, depth: int) -> None:
        try:
            names = sorted(os.listdir(dfd))
        except OSError:
            state['complete'] = False
            return
        for name in names:
            if len(found) >= limit or time.monotonic() > deadline:
                state['complete'] = False
                return
            if name == TRASH_DIR or name.endswith(PART_SUFFIX):
                continue
            try:
                st = os.lstat(name, dir_fd=dfd)
            except OSError:
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
            if not is_dir and not stat.S_ISREG(st.st_mode):
                continue
            path = f'{rel}/{name}' if rel else name
            found.append({'path': path, 'type': 'folder' if is_dir else 'file',
                          'size': 0 if is_dir else st.st_size, 'mtime': int(st.st_mtime)})
            if is_dir:
                if depth >= SEARCH_DEPTH:
                    state['complete'] = False
                    continue
                try:
                    sub = _open_subdir(name, dfd)
                except OSError:
                    state['complete'] = False
                    continue
                try:
                    walk(sub, path, depth + 1)
                finally:
                    os.close(sub)

    dfd = open_dir(dir_path, root)
    try:
        walk(dfd, '', 0)
    finally:
        os.close(dfd)
    return {'entries': found, 'complete': state['complete']}


MEDIA_TYPES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif', '.avif', '.bmp',
               '.mp4', '.mov', '.m4v', '.webm', '.mkv', '.3gp')
MEDIA_LIMIT = 5000


def media(dir_path: str, root: str = DATA_ROOT, limit: int = MEDIA_LIMIT,
          seconds: float = SEARCH_SECONDS) -> dict[str, Any]:
    """Photos and videos below a folder, newest first, for the Photos view.
    Like search: no symlinks, skips what the person cannot open, hidden
    folders, the trash and unfinished uploads; stops at `limit` or `seconds`."""
    deadline = time.monotonic() + seconds
    found: list[dict[str, Any]] = []
    state = {'complete': True}

    def walk(dfd: int, rel: str, depth: int) -> None:
        try:
            names = os.listdir(dfd)
        except OSError:
            return
        for name in names:
            if time.monotonic() > deadline:
                state['complete'] = False
                return
            if name.startswith('.') or name.endswith(PART_SUFFIX):
                continue
            try:
                st = os.lstat(name, dir_fd=dfd)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and name.lower().endswith(MEDIA_TYPES):
                found.append({'name': name, 'folder': rel, 'type': 'file', 'size_bytes': st.st_size,
                              'modified_at': datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(),
                              '_t': st.st_mtime})
            elif stat.S_ISDIR(st.st_mode) and depth < SEARCH_DEPTH:
                try:
                    sub = _open_subdir(name, dfd)
                except OSError:
                    continue
                try:
                    walk(sub, f'{rel}/{name}' if rel else name, depth + 1)
                finally:
                    os.close(sub)

    dfd = open_dir(dir_path, root)
    try:
        walk(dfd, '', 0)
    finally:
        os.close(dfd)
    found.sort(key=lambda e: e['_t'], reverse=True)
    if len(found) > limit:
        found, state['complete'] = found[:limit], False
    for item in found:
        del item['_t']
    return {'results': found, 'complete': state['complete']}


# ── Upload, new folder, rename ───────────────────────────────────────────────

def write_file(dir_path: str, name: str, src: BinaryIO, root: str = DATA_ROOT) -> int:
    check_name(name)
    dfd = open_dir(dir_path, root)
    try:
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                         dir_fd=dfd)
        except FileExistsError:
            raise FileOpError(f'"{name}" is already there.') from None
        written = 0
        try:
            with os.fdopen(fd, 'wb', closefd=False) as out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
            _like_parent(fd, dfd, is_dir=False)
        except BaseException:
            os.unlink(name, dir_fd=dfd)   # no half-written file left behind
            raise
        finally:
            os.close(fd)
        return written
    finally:
        os.close(dfd)


OFFICE_SAVE_LIMIT = 2 * 1024 ** 3


def replace_file(dir_path: str, name: str, src: BinaryIO, root: str = DATA_ROOT) -> int:
    """A saved office document replaces the file it was opened from, as a whole: written next to it, then
    renamed over it, so a failed save leaves the old file. The file must be there already (a save never makes
    a new one, nor follows a link), and keeps its owner, group and mode. Only for the editor in the Hub."""
    check_name(name)
    dfd = open_dir(dir_path, root)
    tmp = f'.{name[:120]}.alvaos-save'
    try:
        try:
            st = os.lstat(name, dir_fd=dfd)
        except FileNotFoundError:
            raise FileOpError(f'"{name}" is not there any more.') from None
        if not stat.S_ISREG(st.st_mode):
            raise FileOpError('Only a file can be saved over.')
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        except FileExistsError:
            raise FileOpError('Another save of this file is running.') from None
        written = 0
        try:
            with os.fdopen(fd, 'wb', closefd=False) as out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > OFFICE_SAVE_LIMIT:
                        raise FileOpError('That document is too big to save.')
                    out.write(chunk)
                out.flush()
                os.fsync(fd)
            if not written:
                raise FileOpError('Nothing to save.')
            try:
                os.fchown(fd, st.st_uid, st.st_gid)
            except PermissionError:
                pass
            try:
                os.fchmod(fd, stat.S_IMODE(st.st_mode))
            except PermissionError:
                pass
            os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
        finally:
            os.close(fd)
        return written
    finally:
        os.close(dfd)


# Data of Hub apps (Calendar, Chat): small JSON files below a hidden folder
# of a share, like ".alvaos/calendar/calendar.json". Unlike everything else
# here they are replaced as a whole (written next to it, then renamed over
# it), because the app keeps one file per thing and changes it.
APP_DATA_LIMIT = 16 * 1024 * 1024
APP_DATA_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')


def _app_data_parts(rel: str) -> list[str]:
    parts = rel.split('/') if isinstance(rel, str) else []
    if not 1 <= len(parts) <= 4 or not all(p == '.alvaos' or APP_DATA_NAME.match(p) for p in parts) \
            or parts[-1] == '.alvaos':
        raise FileOpError('That name is not allowed.')
    return parts


def _app_data_dir(dir_path: str, parts: list[str], root: str, create: bool) -> int | None:
    """The folder of an app data file, made on the way when `create`; None
    when it is not there and need not be."""
    fd = open_dir(dir_path, root)
    for name in parts[:-1]:
        try:
            if create and not _exists(name, fd):
                os.mkdir(name, 0o700, dir_fd=fd)
                sub = _open_subdir(name, fd)
                _like_parent(sub, fd, is_dir=True)
            else:
                sub = _open_subdir(name, fd)
        except FileNotFoundError:
            os.close(fd)
            return None
        except OSError:
            os.close(fd)
            raise FileOpError('This folder could not be opened.') from None
        os.close(fd)
        fd = sub
    return fd


def read_app_data(dir_path: str, rel: str, root: str = DATA_ROOT) -> dict[str, Any]:
    """{'found': False} or {'found': True, 'text': ...}."""
    parts = _app_data_parts(rel)
    dfd = _app_data_dir(dir_path, parts, root, create=False)
    if dfd is None:
        return {'found': False}
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        except FileNotFoundError:
            return {'found': False}
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise FileOpError('That is not a file.')
            data = f.read(APP_DATA_LIMIT + 1)
        if len(data) > APP_DATA_LIMIT:
            raise FileOpError('The file is too large.')
        return {'found': True, 'text': data.decode('utf-8', 'replace')}
    finally:
        os.close(dfd)


def write_app_data(dir_path: str, rel: str, src: BinaryIO, root: str = DATA_ROOT) -> int:
    """Replace (or make) an app data file with what comes on `src`."""
    parts = _app_data_parts(rel)
    data = src.read(APP_DATA_LIMIT + 1)
    if len(data) > APP_DATA_LIMIT:
        raise FileOpError('That is too large to keep.')
    dfd = _app_data_dir(dir_path, parts, root, create=True)
    assert dfd is not None
    tmp = f'.{parts[-1]}.{secrets.token_hex(4)}.tmp'
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as out:
                out.write(data)
                out.flush()
                os.fsync(fd)
            _like_parent(fd, dfd, is_dir=False)
        finally:
            os.close(fd)
        os.replace(tmp, parts[-1], src_dir_fd=dfd, dst_dir_fd=dfd)
        return len(data)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise
    finally:
        os.close(dfd)


def delete_app_data(dir_path: str, rel: str, root: str = DATA_ROOT) -> None:
    """Remove an app data file for good (a deleted chat); gone already is fine."""
    parts = _app_data_parts(rel)
    dfd = _app_data_dir(dir_path, parts, root, create=False)
    if dfd is None:
        return
    try:
        if stat.S_ISREG(os.lstat(parts[-1], dir_fd=dfd).st_mode):
            os.unlink(parts[-1], dir_fd=dfd)
    except FileNotFoundError:
        pass
    finally:
        os.close(dfd)


# Uploads in pieces: written into a hidden part file next to the final name,
# renamed at the end. A dropped connection continues where it stopped.
PART_SUFFIX = '.alvaos-upload'


def _part_name(name: str) -> str:
    check_name(name)
    part = f'.{name}{PART_SUFFIX}'
    if len(part.encode('utf-8', 'surrogateescape')) > 255:
        raise FileOpError('That name is too long.')
    return part


def part_size(dir_path: str, name: str, root: str = DATA_ROOT) -> int:
    part = _part_name(name)
    dfd = open_dir(dir_path, root)
    try:
        try:
            st = os.lstat(part, dir_fd=dfd)
        except FileNotFoundError:
            return 0
        if not stat.S_ISREG(st.st_mode):
            raise FileOpError('An unfinished upload of that name is in the way.')
        return st.st_size
    finally:
        os.close(dfd)


def part_write(dir_path: str, name: str, offset: int, src: BinaryIO, root: str = DATA_ROOT) -> int:
    """Add one piece at `offset`, which must be where the part file ends.
    Returns the new size."""
    part = _part_name(name)
    dfd = open_dir(dir_path, root)
    try:
        if _exists(name, dfd):
            raise FileOpError(f'"{name}" is already there.')
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(part, flags, 0o600, dir_fd=dfd)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise FileOpError('An unfinished upload of that name is in the way.')
            if st.st_size != offset:
                raise FileOpError(f'The upload continues at {st.st_size}, not at {offset}.')
            os.lseek(fd, offset, os.SEEK_SET)
            with os.fdopen(fd, 'wb', closefd=False) as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            return os.fstat(fd).st_size
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


EARLIEST_MTIME = 315532800   # 1980-01-01: older dates from a browser are not real


def part_finish(dir_path: str, name: str, size: int, root: str = DATA_ROOT, mtime: int | None = None) -> None:
    """The last piece arrived: give the file its name, never over another.
    With `mtime` (seconds), the file keeps the date it had on the device it
    came from, so a photo lands in the right month in the Photos view."""
    part = _part_name(name)
    dfd = open_dir(dir_path, root)
    try:
        try:
            fd = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        except FileNotFoundError:
            raise FileOpError('This upload is not there any more.') from None
        try:
            if os.fstat(fd).st_size != size:
                raise FileOpError('The upload is not complete yet.')
            _like_parent(fd, dfd, is_dir=False)
            if mtime is not None and EARLIEST_MTIME <= mtime <= time.time() + 86400:
                os.utime(fd, (mtime, mtime))
        finally:
            os.close(fd)
        if _exists(name, dfd):
            raise FileOpError(f'"{name}" is already there.')
        os.rename(part, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        os.close(dfd)


def part_abort(dir_path: str, name: str, root: str = DATA_ROOT) -> None:
    part = _part_name(name)
    dfd = open_dir(dir_path, root)
    try:
        try:
            if stat.S_ISREG(os.lstat(part, dir_fd=dfd).st_mode):
                os.unlink(part, dir_fd=dfd)
        except FileNotFoundError:
            pass
    finally:
        os.close(dfd)


def make_dir(dir_path: str, name: str, root: str = DATA_ROOT) -> None:
    check_name(name)
    dfd = open_dir(dir_path, root)
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=dfd)
        except FileExistsError:
            raise FileOpError(f'"{name}" is already there.') from None
        nfd = _open_subdir(name, dfd)
        try:
            _like_parent(nfd, dfd, is_dir=True)
        finally:
            os.close(nfd)
    finally:
        os.close(dfd)


def rename(dir_path: str, old: str, new: str, root: str = DATA_ROOT) -> None:
    check_name(old)
    check_name(new)
    dfd = open_dir(dir_path, root)
    try:
        if not _exists(old, dfd):
            raise FileOpError(f'"{old}" is not there any more.')
        if _exists(new, dfd):
            raise FileOpError(f'"{new}" is already there.')
        os.rename(old, new, src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        os.close(dfd)


def move(src_dir: str, name: str, dst_dir: str, root: str = DATA_ROOT) -> None:
    """Move one item into another folder, never over something there."""
    check_name(name)
    sfd = open_dir(src_dir, root)
    try:
        dfd = open_dir(dst_dir, root)
        try:
            if not _exists(name, sfd):
                raise FileOpError(f'"{name}" is not there any more.')
            src_real, dst_real = _real(sfd), _real(dfd)
            if src_real == dst_real:
                raise FileOpError(f'"{name}" is already in that folder.')
            if _inside(dst_real, os.path.join(src_real, name)):
                raise FileOpError('A folder cannot be moved into itself.')
            if _exists(name, dfd):
                raise FileOpError(f'There is already a "{name}" in that folder.')
            try:
                os.rename(name, name, src_dir_fd=sfd, dst_dir_fd=dfd)
            except OSError as exc:
                if exc.errno == 18:   # EXDEV
                    raise FileOpError('That folder is a separate Btrfs folder (subvolume); copy the item '
                                      'there from your computer instead.') from None
                raise
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def _free_name(name: str, dir_fd: int, is_dir: bool) -> str:
    if not _exists(name, dir_fd):
        return name
    stem, ext = os.path.splitext(name)
    if not stem or is_dir:
        stem, ext = name, ''
    candidate = f'{stem} (copy){ext}'
    n = 2
    while _exists(candidate, dir_fd):
        candidate = f'{stem} (copy {n}){ext}'
        n += 1
    return candidate


def _copy_file_at(src_dir_fd: int, name: str, dst_dir_fd: int, new_name: str) -> None:
    sfd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=src_dir_fd)
    try:
        st = os.fstat(sfd)
        if not stat.S_ISREG(st.st_mode):
            return
        dfd = os.open(new_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                      dir_fd=dst_dir_fd)
        try:
            with os.fdopen(sfd, 'rb', closefd=False) as fin, os.fdopen(dfd, 'wb', closefd=False) as fout:
                shutil.copyfileobj(fin, fout, 1024 * 1024)
            _like_parent(dfd, dst_dir_fd, is_dir=False)
            os.utime(dfd, ns=(st.st_atime_ns, st.st_mtime_ns))
        except BaseException:
            os.unlink(new_name, dir_fd=dst_dir_fd)
            raise
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def versions(name: str, dirs: list[str], root: str = DATA_ROOT) -> list[dict[str, Any] | None]:
    """For each folder (a restore point's copy of one folder), whether it
    holds a regular file `name`: its size and modification time, or None."""
    check_name(name)
    out: list[dict[str, Any] | None] = []
    for dir_path in dirs:
        try:
            dfd = open_dir(dir_path, root)
        except (FileOpError, OSError):
            out.append(None)
            continue
        try:
            st = os.lstat(name, dir_fd=dfd)
            out.append({'size_bytes': st.st_size,
                        'modified_at': datetime.fromtimestamp(st.st_mtime).astimezone().isoformat()}
                       if stat.S_ISREG(st.st_mode) else None)
        except OSError:
            out.append(None)
        finally:
            os.close(dfd)
    return out


def restore_version(src_dir: str, name: str, dst_dir: str, new_name: str, root: str = DATA_ROOT) -> str:
    """Copy one file from a restore point next to the current one, under a
    new name. Never over anything."""
    check_name(name)
    check_name(new_name)
    sfd = open_dir(src_dir, root)
    try:
        dfd = open_dir(dst_dir, root)
        try:
            try:
                st = os.lstat(name, dir_fd=sfd)
            except FileNotFoundError:
                raise FileOpError(f'"{name}" is not in this restore point.') from None
            if not stat.S_ISREG(st.st_mode):
                raise FileOpError('Only files can be restored here.')
            try:
                _copy_file_at(sfd, name, dfd, new_name)
            except FileExistsError:
                raise FileOpError(f'"{new_name}" is already there.') from None
            return new_name
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def _copy_tree_at(src_fd: int, dst_fd: int) -> None:
    """Copy what is inside src_fd into dst_fd: files and folders, never
    symlinks, nothing the person cannot read."""
    for name in os.listdir(src_fd):
        if name == TRASH_DIR or name.endswith(PART_SUFFIX):
            continue
        try:
            st = os.lstat(name, dir_fd=src_fd)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            try:
                _copy_file_at(src_fd, name, dst_fd, name)
            except PermissionError:
                continue
        elif stat.S_ISDIR(st.st_mode):
            try:
                child = _open_subdir(name, src_fd)
            except OSError:
                continue
            try:
                os.mkdir(name, 0o700, dir_fd=dst_fd)
                new = _open_subdir(name, dst_fd)
                try:
                    _like_parent(new, dst_fd, is_dir=True)
                    _copy_tree_at(child, new)
                finally:
                    os.close(new)
            finally:
                os.close(child)


def copy(src_dir: str, name: str, dst_dir: str, root: str = DATA_ROOT) -> str:
    """Copy one file or folder into a folder. Returns the name of the copy
    ("name (copy)" when the name is taken there)."""
    check_name(name)
    sfd = open_dir(src_dir, root)
    try:
        dfd = open_dir(dst_dir, root)
        try:
            try:
                st = os.lstat(name, dir_fd=sfd)
            except FileNotFoundError:
                raise FileOpError(f'"{name}" is not there any more.') from None
            if stat.S_ISDIR(st.st_mode) and _inside(_real(dfd), os.path.join(_real(sfd), name)):
                raise FileOpError('A folder cannot be copied into itself.')
            new_name = _free_name(name, dfd, stat.S_ISDIR(st.st_mode))
            if stat.S_ISREG(st.st_mode):
                _copy_file_at(sfd, name, dfd, new_name)
            elif stat.S_ISDIR(st.st_mode):
                src = _open_subdir(name, sfd)
                try:
                    os.mkdir(new_name, 0o700, dir_fd=dfd)
                    new = _open_subdir(new_name, dfd)
                    try:
                        _like_parent(new, dfd, is_dir=True)
                        _copy_tree_at(src, new)
                    finally:
                        os.close(new)
                finally:
                    os.close(src)
            else:
                raise FileOpError('Only files and folders can be copied.')
            return new_name
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


ZIP_NAMES_MAX = 200


def zip_folder(dir_path: str, out: BinaryIO, root: str = DATA_ROOT, names: list[str] | None = None) -> int:
    """Write a folder as a ZIP stream (no temporary file), or only the listed
    files and folders in it. Walks with descriptors and never follows
    symlinks; files that cannot be read (no access) are left out. Returns the
    number of files."""
    import zipfile
    if names is not None:
        if not names or len(names) > ZIP_NAMES_MAX:
            raise FileOpError('Choose what to put in the ZIP.')
        for name in names:
            check_name(name)
    dfd = open_dir(dir_path, root)
    count = 0

    def add_file(zf: Any, name: str, tfd: int, arcname: str) -> int:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=tfd)
        except OSError:
            return 0
        with os.fdopen(fd, 'rb') as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                return 0
            info = zipfile.ZipInfo(arcname, datetime.fromtimestamp(st.st_mtime).timetuple()[:6])
            info.compress_type = zipfile.ZIP_STORED
            info.file_size = st.st_size
            with zf.open(info, 'w', force_zip64=st.st_size > 2 ** 31) as dst:
                shutil.copyfileobj(f, dst, 1024 * 1024)
        return 1

    def add_tree(zf: Any, tree_fd: int, prefix: str) -> int:
        added = 0
        for top, dirs, files, tfd in os.fwalk('.', dir_fd=tree_fd, follow_symlinks=False):
            dirs[:] = sorted(d for d in dirs if d != TRASH_DIR and not d.startswith('.'))
            rel_top = os.path.normpath(top)
            for name in sorted(files):
                if name.startswith('.'):
                    continue
                arcname = name if rel_top == '.' else os.path.join(rel_top, name)
                added += add_file(zf, name, tfd, os.path.join(prefix, arcname) if prefix else arcname)
        return added

    try:
        with zipfile.ZipFile(_Unseekable(out), "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:  # type: ignore[call-overload]
            if names is None:
                count = add_tree(zf, dfd, '')
            else:
                for name in names:
                    try:
                        st = os.lstat(name, dir_fd=dfd)
                    except OSError:
                        continue
                    if stat.S_ISREG(st.st_mode):
                        count += add_file(zf, name, dfd, name)
                    elif stat.S_ISDIR(st.st_mode):
                        try:
                            sub = _open_subdir(name, dfd)
                        except OSError:
                            continue
                        try:
                            count += add_tree(zf, sub, name)
                        finally:
                            os.close(sub)
        return count
    finally:
        os.close(dfd)


UNZIP_ENTRIES_MAX = 50000
UNZIP_BYTES_MAX = 50 * 1024 ** 3


def unzip(dir_path: str, name: str, root: str = DATA_ROOT) -> dict[str, Any]:
    """Unpack a ZIP file of a folder into a new folder next to it, named like
    the ZIP. Nothing is overwritten; paths that climb out ("../", absolute),
    links and passworded files are refused or skipped, and the size is capped
    while writing (the sizes in a ZIP's header are not trusted)."""
    import zipfile
    check_name(name)
    dfd = open_dir(dir_path, root)
    made = ''
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        except OSError:
            raise FileOpError('That ZIP file could not be opened.') from None
        with os.fdopen(fd, 'rb') as raw:
            if not stat.S_ISREG(os.fstat(raw.fileno()).st_mode):
                raise FileOpError('That is not a file.')
            try:
                zf = zipfile.ZipFile(raw)
            except zipfile.BadZipFile:
                raise FileOpError('This is not a ZIP file, or it is damaged.') from None
            infos = zf.infolist()
            if len(infos) > UNZIP_ENTRIES_MAX:
                raise FileOpError('This ZIP has too many files to unpack here.')
            if any(i.flag_bits & 0x1 for i in infos):
                raise FileOpError('This ZIP is protected with a password; it cannot be unpacked here.')
            stem = name[:-4] if name.lower().endswith('.zip') and len(name) > 4 else name + ' (unpacked)'
            folder = stem
            n = 2
            while _exists(folder, dfd):
                folder = f'{stem} {n}'
                n += 1
            os.mkdir(folder, 0o770, dir_fd=dfd)
            made = folder
            top = _open_subdir(folder, dfd)
            try:
                _like_parent(top, dfd, True)
                total = 0
                files = 0
                for info in infos:
                    parts = [p for p in info.filename.replace('\\', '/').split('/') if p not in ('', '.')]
                    if not parts or info.filename.startswith('/') or '..' in parts or any('\x00' in p for p in parts):
                        if parts and (info.filename.startswith('/') or '..' in parts):
                            raise FileOpError('This ZIP holds paths that point outside its folder; it was not unpacked.')
                        continue
                    if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                        continue
                    for part in parts:
                        check_name(part)
                    cur = os.dup(top)
                    try:
                        for part in parts[:-1] if not info.is_dir() else parts:
                            try:
                                os.mkdir(part, 0o770, dir_fd=cur)
                                made_fd = _open_subdir(part, cur)
                                _like_parent(made_fd, cur, True)
                            except FileExistsError:
                                made_fd = _open_subdir(part, cur)
                            os.close(cur)
                            cur = made_fd
                        if info.is_dir():
                            continue
                        leaf = parts[-1]
                        try:
                            out_fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o660, dir_fd=cur)
                        except FileExistsError:
                            continue
                        with os.fdopen(out_fd, 'wb') as dst, zf.open(info) as src:
                            _like_parent(dst.fileno(), cur, False)
                            while True:
                                chunk = src.read(1024 * 1024)
                                if not chunk:
                                    break
                                total += len(chunk)
                                if total > UNZIP_BYTES_MAX:
                                    raise FileOpError('This ZIP unpacks to more than 50 GB; it was not unpacked.')
                                dst.write(chunk)
                            try:
                                moment = time.mktime(datetime(*info.date_time).timetuple())
                                os.utime(dst.fileno(), (moment, moment))
                            except (ValueError, OverflowError, OSError):
                                pass
                        files += 1
                    finally:
                        os.close(cur)
            finally:
                os.close(top)
        return {'folder': folder, 'files': files, 'bytes': total}
    except BaseException:
        if made:
            shutil.rmtree(made, ignore_errors=True, dir_fd=dfd)
        raise
    finally:
        os.close(dfd)


SIZE_SECONDS = 25


def folder_size(dir_path: str, name: str, root: str = DATA_ROOT, seconds: float = SIZE_SECONDS) -> dict[str, Any]:
    """How much a file or folder holds: bytes, files and folders. Looks at
    everything the person may open (the trash left out) and stops after
    `seconds`, saying that the answer is not complete then."""
    check_name(name)
    dfd = open_dir(dir_path, root)
    try:
        try:
            st = os.lstat(name, dir_fd=dfd)
        except OSError:
            raise FileOpError('That does not exist (any more).') from None
        if stat.S_ISREG(st.st_mode):
            return {'size': st.st_size, 'files': 1, 'folders': 0, 'complete': True}
        if not stat.S_ISDIR(st.st_mode):
            raise FileOpError('Only files and folders have a size.')
        try:
            sub = _open_subdir(name, dfd)
        except PermissionError:
            raise FileOpError('You do not have access to this folder.') from None
    finally:
        os.close(dfd)
    deadline = time.monotonic() + seconds
    size = files = folders = 0
    complete = True
    try:
        for _top, dirs, names, tfd in os.fwalk('.', dir_fd=sub, follow_symlinks=False):
            dirs[:] = [d for d in dirs if d != TRASH_DIR]
            folders += len(dirs)
            for item in names:
                try:
                    item_st = os.lstat(item, dir_fd=tfd)
                except OSError:
                    continue
                if stat.S_ISREG(item_st.st_mode):
                    size += item_st.st_size
                    files += 1
            if time.monotonic() > deadline:
                complete = False
                break
    finally:
        os.close(sub)
    return {'size': size, 'files': files, 'folders': folders, 'complete': complete}


class _Unseekable:
    """zipfile writes data descriptors when the stream cannot seek (stdout)."""

    def __init__(self, raw: BinaryIO):
        self.raw = raw
        self.pos = 0

    def write(self, data: bytes) -> int:
        self.raw.write(data)
        self.pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        self.raw.flush()

    def seekable(self) -> bool:
        return False


# ── Trash ────────────────────────────────────────────────────────────────────

def _inside(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip('/') + '/')


def _open_trash(share_fd: int, create: bool) -> int | None:
    if create:
        try:
            os.mkdir(TRASH_DIR, 0o700, dir_fd=share_fd)
            tfd = _open_subdir(TRASH_DIR, share_fd)
            _like_parent(tfd, share_fd, is_dir=True)
            return tfd
        except FileExistsError:
            pass
    try:
        return _open_subdir(TRASH_DIR, share_fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise FileOpError('The trash folder of this share is not a folder.') from None


def trash(share_root: str, dir_path: str, name: str, root: str = DATA_ROOT,
          now: datetime | None = None) -> str:
    """Move one item into the share's trash. Returns its trash id."""
    check_name(name)
    sfd = open_dir(share_root, root)
    try:
        dfd = open_dir(dir_path, root)
        try:
            share_real, dir_real = _real(sfd), _real(dfd)
            if not _inside(dir_real, share_real):
                raise FileOpError('This folder is not in that share.')
            if _inside(dir_real, os.path.join(share_real, TRASH_DIR)):
                raise FileOpError('This is already in the trash.')
            if not _exists(name, dfd):
                raise FileOpError(f'"{name}" is not there any more.')
            tfd = _open_trash(sfd, create=True)
            assert tfd is not None
            try:
                stamp = f'{(now or datetime.now()).strftime(STAMP_FORMAT)}-{secrets.token_hex(3)}'
                os.mkdir(stamp, 0o700, dir_fd=tfd)
                ifd = _open_subdir(stamp, tfd)
                try:
                    _like_parent(ifd, tfd, is_dir=True)
                    origin = os.path.relpath(dir_real, share_real)
                    ofd = os.open(ORIGIN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                                  dir_fd=ifd)
                    with os.fdopen(ofd, 'w') as f:
                        json.dump({'folder': '' if origin == '.' else origin, 'name': name}, f)
                    try:
                        os.rename(name, name, src_dir_fd=dfd, dst_dir_fd=ifd)
                    except OSError as exc:
                        os.unlink(ORIGIN_FILE, dir_fd=ifd)
                        os.rmdir(stamp, dir_fd=tfd)
                        if exc.errno == 18:   # EXDEV: another subvolume
                            raise FileOpError('This item is in its own Btrfs folder (subvolume) and cannot '
                                              'go to the trash. Remove that folder in Storage.') from None
                        raise
                finally:
                    os.close(ifd)
            finally:
                os.close(tfd)
            return stamp
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def _read_origin(ifd: int) -> dict[str, str]:
    try:
        fd = os.open(ORIGIN_FILE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=ifd)
        with os.fdopen(fd) as f:
            data = json.load(f)
        return {'folder': str(data.get('folder') or ''), 'name': str(data.get('name') or '')}
    except (OSError, ValueError, AttributeError):
        return {'folder': '', 'name': ''}


def _stamp_time(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp[:15], STAMP_FORMAT)
    except ValueError:
        return None


def list_trash(share_root: str, root: str = DATA_ROOT) -> list[dict[str, Any]]:
    sfd = open_dir(share_root, root)
    try:
        tfd = _open_trash(sfd, create=False)
        if tfd is None:
            return []
        items = []
        try:
            for stamp in sorted(os.listdir(tfd), reverse=True):
                when = _stamp_time(stamp)
                if when is None:
                    continue
                try:
                    ifd = _open_subdir(stamp, tfd)
                except OSError:
                    continue
                try:
                    origin = _read_origin(ifd)
                    names = [n for n in os.listdir(ifd) if n != ORIGIN_FILE]
                    if not names:
                        continue
                    st = os.lstat(names[0], dir_fd=ifd)
                    items.append({
                        'id': stamp, 'name': names[0], 'folder': origin['folder'],
                        'type': 'folder' if stat.S_ISDIR(st.st_mode) else 'file',
                        'size_bytes': st.st_size if stat.S_ISREG(st.st_mode) else 0,
                        'deleted_at': when.isoformat(),
                    })
                finally:
                    os.close(ifd)
            items += _smb_trash_items(tfd)
        finally:
            os.close(tfd)
        items.sort(key=lambda i: str(i['deleted_at']), reverse=True)
        return items
    finally:
        os.close(sfd)


# Files deleted over the network (Samba vfs_recycle, see shares_manager.py)
# land in <trash>/smb/<their folder>/<name>; "Copy #2 of name" when the same
# name was deleted before. Their id is "smb:<path>"; the time of deleting is
# the file's time (recycle:touch_mtime).
SMB_TRASH = 'smb'
SMB_TRASH_LIMIT = 2000
_SMB_COPY = re.compile(r'^Copy #\d+ of (.+)$')


def _smb_trash_items(tfd: int) -> list[dict[str, Any]]:
    try:
        rfd = _open_subdir(SMB_TRASH, tfd)
    except OSError:
        return []
    items: list[dict[str, Any]] = []

    def walk(dfd: int, rel: str, depth: int) -> None:
        try:
            names = os.listdir(dfd)
        except OSError:
            return
        for name in names:
            if len(items) >= SMB_TRASH_LIMIT:
                return
            try:
                st = os.lstat(name, dir_fd=dfd)
            except OSError:
                continue
            path = f'{rel}/{name}' if rel else name
            if stat.S_ISREG(st.st_mode):
                original = _SMB_COPY.match(name)
                items.append({'id': f'smb:{path}', 'name': original.group(1) if original else name, 'folder': rel,
                              'type': 'file', 'size_bytes': st.st_size, 'from_network': True,
                              'deleted_at': datetime.fromtimestamp(st.st_mtime).isoformat()})
            elif stat.S_ISDIR(st.st_mode) and depth < SEARCH_DEPTH:
                try:
                    sub = _open_subdir(name, dfd)
                except OSError:
                    continue
                try:
                    walk(sub, path, depth + 1)
                finally:
                    os.close(sub)

    try:
        walk(rfd, '', 0)
    finally:
        os.close(rfd)
    return items


def _smb_parts(item_id: str) -> list[str]:
    parts = item_id[len('smb:'):].split('/')
    for part in parts:
        check_name(part)
    return parts


def _restore_smb(sfd: int, tfd: int, item_id: str, root: str) -> dict[str, str]:
    parts = _smb_parts(item_id)
    fds = []
    try:
        dfd = _open_subdir(SMB_TRASH, tfd)
        fds.append(dfd)
        for part in parts[:-1]:
            dfd = _open_subdir(part, dfd)
            fds.append(dfd)
        stored = parts[-1]
        try:
            if not stat.S_ISREG(os.lstat(stored, dir_fd=dfd).st_mode):
                raise FileOpError('This item is not in the trash any more.')
        except FileNotFoundError:
            raise FileOpError('This item is not in the trash any more.') from None
        original = _SMB_COPY.match(stored)
        name = original.group(1) if original else stored
        folder = '/'.join(parts[:-1])
        share_real = _real(sfd)
        try:
            target_fd = open_dir(os.path.join(share_real, folder), root) if folder else os.dup(sfd)
            if not _inside(_real(target_fd), share_real):
                os.close(target_fd)
                raise FileOpError('Invalid folder.')
        except FileOpError:
            folder = ''
            target_fd = os.dup(sfd)
        try:
            target = name
            if _exists(target, target_fd):
                stem, ext = os.path.splitext(name)
                if not stem:
                    stem, ext = name, ''
                target = f'{stem} (restored){ext}'
                n = 2
                while _exists(target, target_fd):
                    target = f'{stem} (restored {n}){ext}'
                    n += 1
            os.rename(stored, target, src_dir_fd=dfd, dst_dir_fd=target_fd)
        finally:
            os.close(target_fd)
        return {'folder': folder, 'name': target}
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _purge_smb(tfd: int, limit: datetime, everything: bool) -> int:
    """Remove network-deleted files older than `limit`, then empty folders."""
    try:
        rfd = _open_subdir(SMB_TRASH, tfd)
    except OSError:
        return 0
    removed = 0

    def walk(dfd: int, depth: int) -> None:
        nonlocal removed
        for name in os.listdir(dfd):
            try:
                st = os.lstat(name, dir_fd=dfd)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode) and depth < SEARCH_DEPTH:
                try:
                    sub = _open_subdir(name, dfd)
                except OSError:
                    continue
                try:
                    walk(sub, depth + 1)
                    empty = not os.listdir(sub)
                finally:
                    os.close(sub)
                if empty:
                    try:
                        os.rmdir(name, dir_fd=dfd)
                    except OSError:
                        pass
            elif everything or datetime.fromtimestamp(st.st_mtime) < limit:
                try:
                    os.unlink(name, dir_fd=dfd)
                    removed += 1
                except OSError:
                    pass

    try:
        walk(rfd, 0)
    finally:
        os.close(rfd)
    return removed


def _check_stamp(stamp: str) -> str:
    if not isinstance(stamp, str) or '/' in stamp or stamp in ('.', '..') or _stamp_time(stamp) is None:
        raise FileOpError('This item is not in the trash.')
    return stamp


def restore(share_root: str, stamp: str, root: str = DATA_ROOT) -> dict[str, str]:
    """Put an item back where it was. If the name is taken there, it comes back
    as "name (restored)"; if its folder is gone, it comes back to the share."""
    if isinstance(stamp, str) and stamp.startswith('smb:'):
        _smb_parts(stamp)
        sfd = open_dir(share_root, root)
        try:
            tfd = _open_trash(sfd, create=False)
            if tfd is None:
                raise FileOpError('The trash is empty.')
            try:
                return _restore_smb(sfd, tfd, stamp, root)
            finally:
                os.close(tfd)
        finally:
            os.close(sfd)
    _check_stamp(stamp)
    sfd = open_dir(share_root, root)
    try:
        tfd = _open_trash(sfd, create=False)
        if tfd is None:
            raise FileOpError('The trash is empty.')
        try:
            try:
                ifd = _open_subdir(stamp, tfd)
            except OSError:
                raise FileOpError('This item is not in the trash any more.') from None
            try:
                origin = _read_origin(ifd)
                names = [n for n in os.listdir(ifd) if n != ORIGIN_FILE]
                if not names:
                    raise FileOpError('This item is not in the trash any more.')
                name = names[0]
                share_real = _real(sfd)
                folder = origin['folder']
                try:
                    dfd = open_dir(os.path.join(share_real, folder), root) if folder else os.dup(sfd)
                    if not _inside(_real(dfd), share_real):
                        os.close(dfd)
                        raise FileOpError('Invalid folder.')
                except FileOpError:
                    folder = ''
                    dfd = os.dup(sfd)
                try:
                    target = name
                    if _exists(target, dfd):
                        stem, ext = os.path.splitext(name)
                        if not stem or stat.S_ISDIR(os.lstat(name, dir_fd=ifd).st_mode):
                            stem, ext = name, ''
                        target = f'{stem} (restored){ext}'
                        n = 2
                        while _exists(target, dfd):
                            target = f'{stem} (restored {n}){ext}'
                            n += 1
                    os.rename(name, target, src_dir_fd=ifd, dst_dir_fd=dfd)
                finally:
                    os.close(dfd)
                os.unlink(ORIGIN_FILE, dir_fd=ifd)
            finally:
                os.close(ifd)
            os.rmdir(stamp, dir_fd=tfd)
            return {'folder': folder, 'name': target}
        finally:
            os.close(tfd)
    finally:
        os.close(sfd)


def purge(share_root: str, days: int, root: str = DATA_ROOT, now: datetime | None = None) -> int:
    """Remove trash items older than `days` (0: everything). Returns how many."""
    sfd = open_dir(share_root, root)
    try:
        tfd = _open_trash(sfd, create=False)
        if tfd is None:
            return 0
        removed = 0
        limit = (now or datetime.now()) - timedelta(days=max(0, int(days)))
        try:
            removed += _purge_smb(tfd, limit, not days)
            for stamp in os.listdir(tfd):
                when = _stamp_time(stamp)
                if when is None or (days and when > limit):
                    continue
                try:
                    os.lstat(stamp, dir_fd=tfd)
                    shutil.rmtree(stamp, dir_fd=tfd)   # does not follow symlinks
                    removed += 1
                except OSError:
                    continue
        finally:
            os.close(tfd)
        return removed
    finally:
        os.close(sfd)
