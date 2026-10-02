#!/usr/bin/env python3
"""File changes in the shared folders, run as root by alvaos-priv.

Every operation gets a folder and a single name, never a path with "/" in
the name. The folder is opened without following a symlink and checked on
the open descriptor to be inside the data root; everything after that is
done relative to that descriptor (dir_fd), so swapping a folder for a
symlink meanwhile cannot reach anything else. Nothing is overwritten.

Deleting moves the item into "<share>/.alvaos-trash/<stamp>/", next to a
small ".origin" file that says where it came from. Items older than
TRASH_DAYS are removed for good.
"""

import json
import os
import secrets
import shutil
import stat
from datetime import datetime, timedelta
from typing import Any, BinaryIO, Dict, List, Optional

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


def list_dir(dir_path: str, root: str = DATA_ROOT) -> List[Dict[str, Any]]:
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


# ── Trash ────────────────────────────────────────────────────────────────────

def _inside(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip('/') + '/')


def _open_trash(share_fd: int, create: bool) -> Optional[int]:
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
          now: Optional[datetime] = None) -> str:
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


def _read_origin(ifd: int) -> Dict[str, str]:
    try:
        fd = os.open(ORIGIN_FILE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=ifd)
        with os.fdopen(fd) as f:
            data = json.load(f)
        return {'folder': str(data.get('folder') or ''), 'name': str(data.get('name') or '')}
    except (OSError, ValueError, AttributeError):
        return {'folder': '', 'name': ''}


def _stamp_time(stamp: str) -> Optional[datetime]:
    try:
        return datetime.strptime(stamp[:15], STAMP_FORMAT)
    except ValueError:
        return None


def list_trash(share_root: str, root: str = DATA_ROOT) -> List[Dict[str, Any]]:
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
        finally:
            os.close(tfd)
        return items
    finally:
        os.close(sfd)


def _check_stamp(stamp: str) -> str:
    if not isinstance(stamp, str) or '/' in stamp or stamp in ('.', '..') or _stamp_time(stamp) is None:
        raise FileOpError('This item is not in the trash.')
    return stamp


def restore(share_root: str, stamp: str, root: str = DATA_ROOT) -> Dict[str, str]:
    """Put an item back where it was. If the name is taken there, it comes back
    as "name (restored)"; if its folder is gone, it comes back to the share."""
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


def purge(share_root: str, days: int, root: str = DATA_ROOT, now: Optional[datetime] = None) -> int:
    """Remove trash items older than `days` (0: everything). Returns how many."""
    sfd = open_dir(share_root, root)
    try:
        tfd = _open_trash(sfd, create=False)
        if tfd is None:
            return 0
        removed = 0
        limit = (now or datetime.now()) - timedelta(days=max(0, int(days)))
        try:
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
