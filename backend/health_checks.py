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
DEFAULT_SETTINGS = {'scrub': 'monthly', 'start_hour': 3}
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
    with _lock:
        data = _load()
        data['settings'] = settings
        _save(data)
    return settings


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

    def serve_forever(self) -> None:
        time.sleep(60)  # let pools mount after boot
        while True:
            try:
                self.run_once()
            except Exception as e:
                self.log(f"[health] scheduler error: {e}")
            time.sleep(LOOP_SECONDS)
