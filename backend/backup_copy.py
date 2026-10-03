#!/usr/bin/env python3
"""A second copy of the restore points on a backup disk (usually USB).

The backup disk is an ordinary AlvaOS pool, made in Storage like any other
(formatting only after the usual confirmation). Chosen here, it gets a copy
of the newest restore point of every shared folder: Btrfs send piped into
receive, incremental against the last copy both sides still have, never
through a file on the system disk. The transfer runs at low priority
(`background` in the helper policy).

When the disk is connected, it is mounted and copied to on its own; "Safely
remove" unmounts it. The copies are registered as restore points of the
class "copy", so "Get files" on the Backup page and "Previous versions" in
Files can use them while the disk is there.
"""

import json
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from common import CMD

SETTINGS_FILE = '/var/lib/alvaos/backup_copy.json'
COPY_DIR = '.alvaos-copies'
KEEP_COPIES = 30
CHECK_SECONDS = 600
STALE_DAYS = 7
BTRFS = CMD['BTRFS']
SNAPSHOT_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$')

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_settings(path: Optional[str] = None) -> Dict[str, Any]:
    try:
        with open(path or SETTINGS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return {'enabled': bool(data.get('enabled')), 'pool_id': str(data.get('pool_id') or ''),
            'enabled_at': data.get('enabled_at'),
            'last_copy_at': data.get('last_copy_at'), 'last_try_at': data.get('last_try_at'),
            'last_error': str(data.get('last_error') or '')}


def save_settings(settings: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    path = path or SETTINGS_FILE
    tmp = f'{path}.tmp'
    with open(tmp, 'w') as f:
        json.dump(settings, f, indent=1)
    os.replace(tmp, path)
    return settings


def slug(source_path: str) -> str:
    """A folder name for one source: /mnt/alvaos/main/Family -> main__Family."""
    parts = [p for p in source_path.split('/') if p][2:] or ['root']
    return re.sub(r'[^A-Za-z0-9._-]', '_', '__'.join(parts))[:200]


class BackupCopier:
    def __init__(self, backup_manager, run_command: Callable, load_pools_state: Callable,
                 popen: Callable = subprocess.Popen, build_cmd: Optional[Callable] = None,
                 settings_path: Optional[str] = None, device_present: Optional[Callable[[str], bool]] = None,
                 is_mounted: Optional[Callable[[str], bool]] = None):
        self.backup = backup_manager
        self.run = run_command
        self.pools = load_pools_state
        self.popen = popen
        if build_cmd is None:
            from common import build_privileged_cmd
            build_cmd = build_privileged_cmd
        self.build_cmd = build_cmd
        self.settings_path = settings_path
        self.device_present = device_present or (lambda uuid: os.path.exists(f'/dev/disk/by-uuid/{uuid}'))
        self.is_mounted = is_mounted or os.path.ismount
        # After "Safely remove" the disk stays unmounted until it was
        # unplugged (or "Copy now" is pressed), even if it is still there.
        self.ejected = False

    # ── Settings and state ───────────────────────────────────────────────

    def settings(self) -> Dict[str, Any]:
        return load_settings(self.settings_path)

    def _save(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        return save_settings(settings, self.settings_path)

    def _source_pools(self) -> set:
        """Pools that hold the folders being backed up (not a backup disk)."""
        ids = set()
        for entry in self.backup.list_snapshots(snapshot_class='data'):
            source = str(entry.get('source_path') or '')
            for pool_id, pool in (self.pools() or {}).items():
                mount = str((pool or {}).get('mount_point') or '')
                if mount and (source == mount or source.startswith(mount.rstrip('/') + '/')):
                    ids.add(pool_id)
        return ids

    def choices(self) -> List[Dict[str, Any]]:
        """Pools that can be the backup disk: not the system, not one being backed up."""
        busy = self._source_pools()
        out = []
        for pool_id, pool in (self.pools() or {}).items():
            if not isinstance(pool, dict) or not pool.get('mount_point') or pool.get('mount_point') == '/':
                continue
            out.append({'id': pool_id, 'name': pool.get('name') or pool_id, 'usable': pool_id not in busy,
                        'connected': self.device_present(pool_id)})
        return sorted(out, key=lambda p: str(p['name']).lower())

    def configure(self, payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
        settings = self.settings()
        if 'enabled' in payload and not payload.get('enabled'):
            settings['enabled'] = False
            return self._save(settings), ''
        pool_id = str(payload.get('pool_id') or settings['pool_id'] or '')
        choice = next((c for c in self.choices() if c['id'] == pool_id), None)
        if not choice:
            return None, 'Choose the backup disk.'
        if not choice['usable']:
            return None, 'This pool holds folders that are backed up; choose another disk.'
        if not settings['enabled'] or settings['pool_id'] != pool_id:
            settings['enabled_at'] = _now().isoformat()
        settings.update({'enabled': True, 'pool_id': pool_id})
        return self._save(settings), ''

    def status(self) -> Dict[str, Any]:
        settings = self.settings()
        pool = (self.pools() or {}).get(settings['pool_id']) or {}
        mount = str(pool.get('mount_point') or '')
        connected = bool(settings['pool_id']) and self.device_present(settings['pool_id'])
        stale = False
        since = settings['last_copy_at'] or settings['enabled_at']
        if settings['enabled'] and since:
            try:
                stale = (_now() - datetime.fromisoformat(since)).days >= STALE_DAYS
            except ValueError:
                stale = False
        return {**settings, 'pool_name': pool.get('name') or settings['pool_id'], 'connected': connected,
                'mounted': bool(mount) and connected and self.is_mounted(mount), 'stale': stale,
                'running': _lock.locked(), 'ejected': self.ejected and connected,
                'choices': self.choices()}

    # ── Mounting ─────────────────────────────────────────────────────────

    def _mount(self) -> Tuple[Optional[str], str]:
        settings = self.settings()
        pool = (self.pools() or {}).get(settings['pool_id']) or {}
        mount = str(pool.get('mount_point') or '')
        if not mount:
            return None, 'The backup disk is not known any more. Choose it again.'
        if not self.device_present(settings['pool_id']):
            return None, 'The backup disk is not connected.'
        if not self.is_mounted(mount):
            self.run([CMD['MKDIR'], '-p', mount], timeout=30)
            res, err = self.run([CMD['MOUNT'], '-U', settings['pool_id'], mount], timeout=60)
            if err or not res or res.returncode != 0:
                return None, f'The backup disk could not be mounted: {err or "mount failed"}'
        return mount, ''

    def eject(self) -> Tuple[bool, str]:
        """Unmount the backup disk so it can be unplugged."""
        settings = self.settings()
        pool = (self.pools() or {}).get(settings['pool_id']) or {}
        mount = str(pool.get('mount_point') or '')
        if not mount or not self.is_mounted(mount):
            return True, 'The backup disk can be unplugged.'
        if not _lock.acquire(blocking=False):   # never in the middle of a copy
            return False, 'A copy is running. Wait until it is done, then remove the disk.'
        try:   # umount writes everything out first
            res, err = self.run([CMD['UMOUNT'], mount], timeout=120)
            if err or not res or res.returncode != 0:
                return False, 'The backup disk is still in use. Try again in a moment.'
            self.ejected = True
        finally:
            _lock.release()
        return True, 'The backup disk can be unplugged.'

    # ── Copying ──────────────────────────────────────────────────────────

    def _pipe(self, send_args: List[str], target_dir: str) -> str:
        """btrfs send ... | btrfs receive target_dir, both through the helper."""
        sender = self.popen(self.build_cmd([BTRFS, 'send'] + send_args), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env={'LC_ALL': 'C'})
        receiver = self.popen(self.build_cmd([BTRFS, 'receive', target_dir]), stdin=sender.stdout,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env={'LC_ALL': 'C'})
        if sender.stdout:
            sender.stdout.close()   # the receiver owns it now; a dying receiver stops the sender
        _, recv_err = receiver.communicate(timeout=24 * 3600)
        _, send_err = sender.communicate(timeout=60)
        if sender.returncode != 0:
            return (send_err or b'').decode(errors='replace').strip() or 'Sending failed.'
        if receiver.returncode != 0:
            return (recv_err or b'').decode(errors='replace').strip() or 'Receiving failed.'
        return ''

    def _copies(self, target_dir: str) -> List[str]:
        try:
            return sorted(n for n in os.listdir(target_dir) if SNAPSHOT_NAME_RE.match(n))
        except OSError:
            return []

    def copy_now(self) -> Dict[str, Any]:
        """Copy the newest restore point of every folder that is not on the
        backup disk yet. Returns {'copied': [...], 'error': ''}."""
        if not _lock.acquire(blocking=False):
            return {'copied': [], 'error': 'A copy is running already.'}
        try:
            self.ejected = False
            settings = self.settings()
            settings['last_try_at'] = _now().isoformat()
            mount, error = self._mount()
            if not mount:
                settings['last_error'] = error
                self._save(settings)
                return {'copied': [], 'error': error}
            copied, errors = [], []
            by_source: Dict[str, List[Dict[str, Any]]] = {}
            for entry in self.backup.list_snapshots(snapshot_class='data'):   # newest first
                by_source.setdefault(str(entry.get('source_path') or ''), []).append(entry)
            for source, entries in by_source.items():
                newest = entries[0]
                name = os.path.basename(str(newest.get('snapshot_path') or ''))
                if not source or not SNAPSHOT_NAME_RE.match(name):
                    continue
                target_dir = os.path.join(mount, COPY_DIR, slug(source))
                self.run([CMD['MKDIR'], '-p', target_dir], timeout=30)
                have = set(self._copies(target_dir))
                if name not in have:
                    parent = next((e for e in entries[1:]
                                   if os.path.basename(str(e.get('snapshot_path'))) in have), None)
                    args = (['-p', str(parent['snapshot_path'])] if parent else []) + [str(newest['snapshot_path'])]
                    problem = self._pipe(args, target_dir)
                    if problem:
                        self.run([BTRFS, 'subvolume', 'delete', os.path.join(target_dir, name)], timeout=300)
                        errors.append(f'{source}: {problem}')
                        continue
                    copied.append(source)
                    self._register(newest, os.path.join(target_dir, name))
                self._thin_out(source, target_dir)
            settings['last_error'] = '; '.join(errors)[:500]
            if not errors:
                settings['last_copy_at'] = _now().isoformat()
            self._save(settings)
            return {'copied': copied, 'error': settings['last_error']}
        finally:
            _lock.release()

    def _register(self, original: Dict[str, Any], copy_path: str) -> None:
        self.backup._append_snapshot({
            'id': f"copy-{original.get('id') or os.path.basename(copy_path)}",
            'source_path': original.get('source_path'), 'snapshot_name': os.path.basename(copy_path),
            'snapshot_path': copy_path, 'created_at': original.get('created_at'),
            'trigger': 'backup disk', 'snapshot_class': 'copy', 'on_backup_disk': True,
        })

    def _thin_out(self, source: str, target_dir: str) -> None:
        """Keep the newest KEEP_COPIES copies of a folder on the backup disk."""
        names = self._copies(target_dir)
        drop = names[:-KEEP_COPIES] if len(names) > KEEP_COPIES else []
        for name in drop:
            path = os.path.join(target_dir, name)
            self.run([BTRFS, 'subvolume', 'delete', path], timeout=300)
            entries = self.backup._load_json(self.backup.snapshots_file, [])
            self.backup._save_snapshots([e for e in entries if isinstance(e, dict)
                                         and os.path.normpath(str(e.get('snapshot_path') or '')) != path])

    def needs_copy(self) -> bool:
        settings = self.settings()
        if not settings['enabled'] or not self.device_present(settings['pool_id']):
            self.ejected = False   # unplugged: the next time it is back, copy again
            return False
        if self.ejected:
            return False
        # Folders backed up in the same run share snapshot names; compare per folder.
        copied = {(str(e.get('source_path') or ''), os.path.basename(str(e.get('snapshot_path') or '')))
                  for e in self.backup.list_snapshots(snapshot_class='copy')}
        newest: Dict[str, str] = {}
        for entry in self.backup.list_snapshots(snapshot_class='data'):
            newest.setdefault(str(entry.get('source_path') or ''),
                              os.path.basename(str(entry.get('snapshot_path') or '')))
        return any((source, name) not in copied for source, name in newest.items())

    def serve_forever(self, stop: Optional[threading.Event] = None) -> None:
        """Copy whenever the disk is there and something new is waiting."""
        stop = stop or threading.Event()
        while not stop.wait(CHECK_SECONDS):
            try:
                if self.needs_copy():
                    self.copy_now()
            except Exception as e:  # noqa: BLE001 - try again at the next check
                print(f'Backup disk copy failed: {e}')
