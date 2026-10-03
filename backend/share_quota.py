#!/usr/bin/env python3
"""Space limits for shared folders (personal folders included).

A limit is a Btrfs quota group limit on the share's own subvolume, so it
counts everything in that folder. Quotas are turned on per pool the first
time a limit is set there; with many restore points Btrfs quotas can make a
pool a little slower, which is why they stay off until someone wants one.
"""

import os
import re
from typing import Callable, Dict, Optional, Tuple

GB = 1024 ** 3
MAX_LIMIT_BYTES = 10 ** 16      # 10 PB: anything above is a typo


def is_subvolume(path: str) -> bool:
    """Btrfs subvolume roots always have inode 256."""
    try:
        return os.stat(path).st_ino == 256
    except OSError:
        return False


def parse_qgroup_show(text: str) -> Optional[Dict[str, Optional[int]]]:
    """`btrfs qgroup show -reF --raw PATH`: the first 0/<id> line, i.e. the
    subvolume's own group. Returns used bytes and the limit (None = none)."""
    for line in (text or '').splitlines():
        fields = line.split()
        if len(fields) >= 4 and re.match(r'^0/[0-9]+$', fields[0]) and fields[1].isdigit():
            limit = fields[3]
            return {'used_bytes': int(fields[1]), 'limit_bytes': int(limit) if limit.isdigit() else None}
    return None


def limit_from_gb(value) -> Tuple[Optional[int], str]:
    """None/''/0 = no limit."""
    if value in (None, '', 0, '0'):
        return None, ''
    try:
        gb = float(value)
    except (TypeError, ValueError):
        return None, 'Enter the limit in GB, like 100.'
    if gb < 1 or gb * GB > MAX_LIMIT_BYTES:
        return None, 'The limit must be at least 1 GB.'
    return int(gb * GB), ''


def apply_limit(path: str, mount_point: str, limit_bytes: Optional[int],
                run: Callable, btrfs: str = 'btrfs') -> str:
    """Set (or clear) the limit. Returns '' or what went wrong."""
    if not is_subvolume(path):
        return ('This folder is not a separate Btrfs folder (subvolume), so it cannot have its own limit. '
                'Folders AlvaOS creates for a share always are.')
    if limit_bytes is not None:
        res, err = run([btrfs, 'quota', 'enable', mount_point], timeout=600)
        if err or res is None or res.returncode != 0:
            return f'Could not turn on space limits for this pool: {err or (res.stderr if res else "")}'.strip()
    res, err = run([btrfs, 'qgroup', 'limit', str(limit_bytes) if limit_bytes else 'none', path], timeout=60)
    if err or res is None or res.returncode != 0:
        if limit_bytes is None and 'quota' in str(err or (res.stderr if res else '')).lower():
            return ''   # quotas were never on: there is no limit to clear
        return f'Could not set the limit: {err or (res.stderr if res else "")}'.strip()
    return ''


def read_usage(path: str, run: Callable, btrfs: str = 'btrfs') -> Optional[Dict[str, Optional[int]]]:
    res, err = run([btrfs, 'qgroup', 'show', '-reF', '--raw', path], timeout=30)
    if err or res is None or res.returncode != 0:
        return None
    return parse_qgroup_show(res.stdout)


NEARLY_FULL = 0.90
FULL = 0.99


def limit_alerts(shares, usage: Callable[[str], Optional[Dict[str, Optional[int]]]]):
    """(alert id, severity, title, message, route) for shares close to their
    limit. `usage(path)` reads what a share holds now."""
    out = []
    for share in shares:
        if not isinstance(share, dict) or not share.get('quota_bytes'):
            continue
        now = usage(str(share.get('path') or ''))
        if not now:
            continue
        limit = now.get('limit_bytes') or share['quota_bytes']
        used = now.get('used_bytes') or 0
        share_part = used / limit if limit else 0
        if share_part < NEARLY_FULL:
            continue
        who = f'The personal folder of {share["personal_for"]}' if share.get('personal_for') else f'"{share.get("name")}"'
        gb = limit / GB
        if share_part >= FULL:
            out.append((f'share-{share.get("id")}-full', 'critical', f'{who} is full',
                        f'It holds its limit of {gb:g} GB; saving new files there fails. '
                        'Delete files or raise the limit.', 'storage.html#shares'))
        else:
            out.append((f'share-{share.get("id")}-nearly-full', 'warning', f'{who} is nearly full',
                        f'{round(share_part * 100)} % of its {gb:g} GB limit is used.', 'storage.html#shares'))
    return out
