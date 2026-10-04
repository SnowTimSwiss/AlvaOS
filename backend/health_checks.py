#!/usr/bin/env python3
"""
AlvaOS health checks: regular data checks (btrfs scrub) for every pool.

A data check reads every block and compares it with its checksum. On a pool
with protection a damaged copy is repaired from the good one. Run regularly,
it finds a disk that is going bad while there is still a good copy.

btrfs remembers when the last check started and what it found
(`btrfs scrub status`), so this module keeps almost no state of its own: the
setting (how often) and the last result it saw per pool, so the dashboard and
the alerts can show it without running btrfs on every request.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

STATE_FILE = '/var/lib/alvaos/health_checks.json'
FREQUENCIES = {'off': None, 'weekly': 7, 'monthly': 30}
DEFAULT_SETTINGS = {'scrub': 'monthly', 'start_hour': 3, 'smart': 'standard'}
WINDOW_HOURS = 3            # start between start_hour and start_hour + 3, at night
LOOP_SECONDS = 600

_lock = threading.Lock()


# ── State ─────────────────────────────────────────────────────────────────────

def _load() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STATE_FILE)


def get_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    stored = _load().get('settings')
    if isinstance(stored, dict):
        if stored.get('scrub') in FREQUENCIES:
            settings['scrub'] = stored['scrub']
        hour = stored.get('start_hour')
        if isinstance(hour, int) and 0 <= hour <= 23:
            settings['start_hour'] = hour
        if stored.get('smart') in SMART_MODES:
            settings['smart'] = stored['smart']
    return settings


def save_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and store. Raises ValueError with a sentence for the UI."""
    settings = get_settings()
    if 'scrub' in payload:
        if payload['scrub'] not in FREQUENCIES:
            raise ValueError('Choose weekly, monthly or off.')
        settings['scrub'] = payload['scrub']
    if 'start_hour' in payload:
        hour = payload['start_hour']
        if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
            raise ValueError('The start hour is a number from 0 to 23.')
        settings['start_hour'] = hour
    if 'smart' in payload:
        if payload['smart'] not in SMART_MODES:
            raise ValueError('Choose standard, short or off.')
        settings['smart'] = payload['smart']
    with _lock:
        data = _load()
        stored = data.get('settings')
        data['settings'] = {**(stored if isinstance(stored, dict) else {}), **settings}
        _save(data)
    return get_settings()


def last_results() -> Dict[str, Dict[str, Any]]:
    """The last data check seen per pool id."""
    seen = _load().get('pools')
    return seen if isinstance(seen, dict) else {}


def _we_started(pool_id: str) -> Optional[datetime]:
    value = (_load().get('started_by_scheduler') or {}).get(pool_id)
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _remember_start(pool_id: str, when: datetime) -> None:
    with _lock:
        data = _load()
        started = data.get('started_by_scheduler')
        started = started if isinstance(started, dict) else {}
        started[pool_id] = when.isoformat()
        data['started_by_scheduler'] = started
        _save(data)


def _record(pool_id: str, result: Dict[str, Any]) -> None:
    with _lock:
        data = _load()
        stored = data.get('pools')
        pools: Dict[str, Any] = stored if isinstance(stored, dict) else {}
        if pools.get(pool_id) == result:
            return  # unchanged: do not wake the system disk every pass
        pools[pool_id] = result
        data['pools'] = pools
        _save(data)


# ── Decisions (pure, tested) ──────────────────────────────────────────────────

def parse_btrfs_time(text: str) -> Optional[datetime]:
    """'Wed Oct  1 03:00:01 2026' as btrfs prints it (local time)."""
    cleaned = ' '.join(str(text or '').split())
    for fmt in ('%a %b %d %H:%M:%S %Y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def in_window(now: datetime, start_hour: int) -> bool:
    """True during the quiet hours when checks may start."""
    return (now.hour - start_hour) % 24 < WINDOW_HOURS


def is_due(scrub: Dict[str, Any], now: datetime, frequency: str,
           we_started: Optional[datetime] = None) -> bool:
    """Whether a pool needs a data check now, given `btrfs scrub status`.

    `we_started` is when this scheduler last started one. It counts too, so a
    btrfs-progs version that prints the time differently does not make every
    night look due."""
    days = FREQUENCIES.get(frequency)
    if not days:
        return False
    state = scrub.get('state')
    if state == 'running':
        return False
    started = parse_btrfs_time(scrub.get('started', ''))
    candidates = [t for t in (started, we_started) if t is not None]
    if not candidates:
        return True
    started = max(candidates)
    # A little slack so "monthly" does not drift a day later every month.
    return now - started >= timedelta(days=days) - timedelta(hours=WINDOW_HOURS)


def summarize(scrub: Dict[str, Any]) -> Dict[str, Any]:
    """What the dashboard and the alerts need from one scrub status."""
    started = parse_btrfs_time(scrub.get('started', ''))
    return {
        'state': scrub.get('state', 'unknown'),
        'started_at': started.isoformat() if started else None,
        'errors': scrub.get('errors'),
        'uncorrectable': scrub.get('uncorrectable'),
        'duration': scrub.get('duration', ''),
        'percent': scrub.get('percent'),
    }


# ── Scheduler ─────────────────────────────────────────────────────────────────

class HealthScheduler:
    """Starts due data checks at night, one pool at a time.

    The storage functions are passed in, so this module does not depend on the
    API layer and the decisions can be tested without btrfs:
      pools()            -> {pool_id: {name, mount_point}} of mounted, managed pools
      activity(mount)    -> parsed scrub/replace/balance status
      busy(activity)     -> '' or why the pool is busy
      start_scrub(mount) -> '' or an error
    """

    def __init__(self, pools: Callable[[], Dict[str, Dict[str, Any]]],
                 activity: Callable[[str], Dict[str, Any]],
                 busy: Callable[[Dict[str, Any]], str],
                 start_scrub: Callable[[str], str],
                 log: Callable[[str], None] = print):
        self.pools = pools
        self.activity = activity
        self.busy = busy
        self.start_scrub = start_scrub
        self.log = log

    def run_once(self, now: Optional[datetime] = None) -> Dict[str, str]:
        """One pass: record results, start at most one due check. Returns
        what happened per pool (for tests and the log)."""
        now = now or datetime.now()
        settings = get_settings()
        outcome: Dict[str, str] = {}
        started_one = False
        for pool_id, pool in sorted(self.pools().items()):
            mount = str(pool.get('mount_point') or '')
            if not mount or mount == '/':
                continue
            try:
                act = self.activity(mount)
            except Exception as e:  # btrfs missing or pool gone: try next time
                outcome[pool_id] = f'unreadable: {e}'
                continue
            scrub = act.get('scrub') or {}
            _record(pool_id, summarize(scrub))
            if started_one or not in_window(now, settings['start_hour']):
                outcome[pool_id] = 'waiting'
                continue
            if not is_due(scrub, now, settings['scrub'], _we_started(pool_id)):
                outcome[pool_id] = 'not due'
                continue
            reason = self.busy(act)
            if reason:
                outcome[pool_id] = f'busy: {reason}'
                continue
            error = self.start_scrub(mount)
            if error:
                outcome[pool_id] = f'failed: {error}'
                self.log(f"[health] data check for {pool.get('name') or pool_id} did not start: {error}")
                continue
            _remember_start(pool_id, now)
            started_one = True  # one at a time: checks are heavy on the disks
            outcome[pool_id] = 'started'
            self.log(f"[health] started the scheduled data check for {pool.get('name') or pool_id}")
        return outcome

    def serve_forever(self, smart: Optional['SmartScheduler'] = None) -> None:
        time.sleep(60)  # let pools mount after boot
        while True:
            try:
                self.run_once()
            except Exception as e:
                self.log(f"[health] scheduler error: {e}")
            if smart is not None:
                try:
                    smart.run_once()
                except Exception as e:
                    self.log(f"[health] self-test scheduler error: {e}")
            time.sleep(LOOP_SECONDS)


# ── Disk self-tests (SMART) ───────────────────────────────────────────────────
# A short test (about two minutes) checks the disk's electronics and a sample
# of its surface; a long test reads the whole surface (hours). Both run inside
# the disk, keep it usable, and are started at night like the data checks.

SMART_MODES = {'standard', 'short', 'off'}     # standard = short weekly + long monthly
SMART_SHORT_DAYS = 7
SMART_LONG_DAYS = 30


def smart_mode() -> str:
    return str(get_settings()['smart'])


def save_smart_mode(mode: str) -> str:
    if mode not in SMART_MODES:
        raise ValueError('Choose standard, short or off.')
    with _lock:
        data = _load()
        stored = data.get('settings')
        settings: Dict[str, Any] = stored if isinstance(stored, dict) else {}
        settings['smart'] = mode
        data['settings'] = settings
        _save(data)
    return mode


def smart_records() -> Dict[str, Dict[str, Any]]:
    """What the last self-tests and SMART readings said, per disk."""
    records = _load().get('smart')
    return records if isinstance(records, dict) else {}


def _update_smart(key: str, changes: Dict[str, Any]) -> None:
    with _lock:
        data = _load()
        stored = data.get('smart')
        records: Dict[str, Any] = stored if isinstance(stored, dict) else {}
        record = dict(records.get(key) or {})
        merged = {**record, **changes}
        if merged == record:
            return
        records[key] = merged
        data['smart'] = records
        _save(data)


def _raw(table: Any, attr_id: int) -> Optional[int]:
    for attr in table or []:
        if isinstance(attr, dict) and attr.get('id') == attr_id:
            value = (attr.get('raw') or {}).get('value')
            return int(value) if isinstance(value, (int, float)) else None
    return None


def parse_smart(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The parts of `smartctl -H -A -l selftest -j` that matter, for ATA and
    NVMe disks. None when the disk was asleep or did not answer."""
    if not isinstance(payload, dict):
        return None
    if (payload.get('power_mode') or '').upper() == 'STANDBY' or not (
            'smart_status' in payload or 'ata_smart_attributes' in payload
            or 'nvme_smart_health_information_log' in payload):
        return None
    status = payload.get('smart_status')
    health = 'unknown'
    if isinstance(status, dict) and 'passed' in status:
        health = 'passed' if status['passed'] else 'failed'

    table = (payload.get('ata_smart_attributes') or {}).get('table')
    nvme = payload.get('nvme_smart_health_information_log') or {}
    result: Dict[str, Any] = {
        'health': health,
        'reallocated': _raw(table, 5),
        'pending': _raw(table, 197),
        'uncorrectable': _raw(table, 198),
        'media_errors': nvme.get('media_errors') if nvme else None,
        'last_test': None,
        'test_running': False,
    }

    ata_log = ((payload.get('ata_smart_self_test_log') or {}).get('standard') or {}).get('table')
    nvme_log = (payload.get('nvme_self_test_log') or {}).get('table')
    if isinstance(ata_log, list) and ata_log:
        entry = ata_log[0]
        status = entry.get('status') or {}
        result['last_test'] = {
            'kind': ((entry.get('type') or {}).get('string') or '').lower(),
            'result': status.get('string') or '',
            'passed': status.get('passed'),
        }
    elif isinstance(nvme_log, list) and nvme_log:
        entry = nvme_log[0]
        code = (entry.get('self_test_result') or {}).get('value')
        result['last_test'] = {
            'kind': ((entry.get('self_test_code') or {}).get('string') or '').lower(),
            'result': (entry.get('self_test_result') or {}).get('string') or '',
            'passed': code == 0 if isinstance(code, int) else None,
        }

    running_ata = ((payload.get('ata_smart_data') or {}).get('self_test') or {}).get('status') or {}
    if isinstance(running_ata.get('remaining_percent'), int):
        result['test_running'] = True
    current_nvme = (payload.get('nvme_self_test_log') or {}).get('current_self_test_operation') or {}
    if current_nvme.get('value'):
        result['test_running'] = True
    return result


def smart_test_due(last_iso: Optional[str], now: datetime, days: int) -> bool:
    if not last_iso:
        return True
    try:
        last = datetime.fromisoformat(last_iso)
    except (TypeError, ValueError):
        return True
    return now - last >= timedelta(days=days) - timedelta(hours=WINDOW_HOURS)


def smart_problems(record: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """'critical' or 'warning' with a sentence for one disk, or None."""
    reading = record.get('reading') or {}
    test = reading.get('last_test') or {}
    if reading.get('health') == 'failed':
        return {'severity': 'critical', 'text': 'The disk reports that it is failing. Replace it soon.'}
    if test.get('passed') is False:
        return {'severity': 'critical', 'text': f"Its last self-test failed ({test.get('result') or 'read error'}). Replace it soon."}
    pending = reading.get('pending') or 0
    uncorrectable = reading.get('uncorrectable') or 0
    media = reading.get('media_errors') or 0
    if pending or uncorrectable or media:
        count = pending + uncorrectable + media
        return {'severity': 'warning', 'text': f'{count} part(s) of the disk could not be read. Keep a backup current and watch whether this grows.'}
    reallocated = reading.get('reallocated')
    baseline = record.get('reallocated_baseline')
    if isinstance(reallocated, int) and isinstance(baseline, int) and reallocated > baseline:
        return {'severity': 'warning', 'text': f'The disk replaced {reallocated - baseline} more worn-out part(s) since AlvaOS started watching it. A growing number is an early sign it is wearing out.'}
    return None


class SmartScheduler:
    """Starts SMART self-tests at night and records what the disks report.

      disks()                -> [{key, name, path, model, pool_id}] for pool disks
      start_test(path, kind) -> '' or an error
      read(path)             -> smartctl JSON payload (with -n standby), or None
    """

    def __init__(self, disks: Callable[[], Any], start_test: Callable[[str, str], str],
                 read: Callable[[str], Optional[Dict[str, Any]]], log: Callable[[str], None] = print):
        self.disks = disks
        self.start_test = start_test
        self.read = read
        self.log = log

    def run_once(self, now: Optional[datetime] = None) -> Dict[str, str]:
        now = now or datetime.now()
        mode = smart_mode()
        outcome: Dict[str, str] = {}
        if mode == 'off' or not in_window(now, get_settings()['start_hour']):
            return outcome
        records = smart_records()
        scrubbing = {pid for pid, r in last_results().items() if (r or {}).get('state') == 'running'}
        today = now.date().isoformat()
        scrub_started_today = {pid for pid, when in (_load().get('started_by_scheduler') or {}).items()
                               if str(when).startswith(today)}
        long_started = False
        for disk in self.disks():
            key = disk['key']
            record = records.get(key) or {}
            label = disk.get('model') or disk.get('name') or key
            _update_smart(key, {'name': disk.get('name'), 'model': disk.get('model'), 'pool_id': disk.get('pool_id')})

            # Read the disk once per night (it is awake for the tests anyway).
            if not str(record.get('read_at') or '').startswith(today):
                reading = parse_smart(self.read(disk['path']))
                if reading is not None:
                    changes: Dict[str, Any] = {'reading': reading, 'read_at': now.isoformat()}
                    if record.get('reallocated_baseline') is None and isinstance(reading.get('reallocated'), int):
                        changes['reallocated_baseline'] = reading['reallocated']
                    _update_smart(key, changes)
                    record = {**record, **changes}
            if (record.get('reading') or {}).get('test_running'):
                outcome[key] = 'test running'
                continue

            pool_busy = disk.get('pool_id') in scrubbing or disk.get('pool_id') in scrub_started_today
            if (mode == 'standard' and not long_started and not pool_busy
                    and smart_test_due(record.get('last_long'), now, SMART_LONG_DAYS)):
                kind = 'long'
            elif smart_test_due(record.get('last_short'), now, SMART_SHORT_DAYS):
                kind = 'short'
            else:
                outcome[key] = 'not due'
                continue
            error = self.start_test(disk['path'], kind)
            if error:
                outcome[key] = f'failed: {error}'
                self.log(f'[health] {kind} self-test on {label} did not start: {error}')
                continue
            stamp = {'last_long': now.isoformat(), 'last_short': now.isoformat()} if kind == 'long' else {'last_short': now.isoformat()}
            _update_smart(key, stamp)
            long_started = long_started or kind == 'long'
            outcome[key] = f'{kind} started'
            self.log(f'[health] started a {kind} self-test on {label}')
        return outcome
