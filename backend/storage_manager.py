#!/usr/bin/env python3
"""
AlvaOS Storage Manager
Disk detection, SMART data, Btrfs pool management, path security, and caching.
"""

import json
from typing import AbstractSet, Any, Dict, List, Optional, Set
import os
import platform
import re
import subprocess
import threading

from common import CMD, run_sudo_command, ensure_directories, parse_size_to_bytes

# ── State file ────────────────────────────────────────────────────────────────
POOLS_STATE_FILE = '/var/lib/alvaos/pools.json'

# ── Storage cache ─────────────────────────────────────────────────────────────
STORAGE_CACHE: Dict[str, Dict[str, Any]] = {
    'disks': {'data': None, 'expires': 0},
    'pools': {'data': None, 'expires': 0}
}
CACHE_TTL = 5
_storage_cache_lock = threading.Lock()


def invalidate_storage_cache(*sections):
    """Invalidate selected storage cache sections."""
    with _storage_cache_lock:
        target_sections = sections or ('disks', 'pools')
        for section in target_sections:
            if section in STORAGE_CACHE:
                STORAGE_CACHE[section]['expires'] = 0


# ── Path security ─────────────────────────────────────────────────────────────

def is_secure_system_device(device_name):
    """
    Check if a device (e.g. 'sda', 'nvme0n1') holds the root filesystem.
    This uses /proc/mounts to find the root device and checks if the target
    device is the same or a parent of the root device.
    """
    if platform.system() != 'Linux':
        return False  # no block devices to protect outside Linux

    try:
        root_device = None
        with open('/proc/mounts', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == '/':
                    root_device = parts[0]  # e.g. /dev/sda2 or /dev/nvme0n1p3
                    break

        if not root_device:
            return False

        # Resolve symlinks (e.g. /dev/root -> /dev/sda1)
        if os.path.exists(root_device):
            root_device = os.path.realpath(root_device)

        base_root = os.path.basename(root_device)

        # Exact match
        if base_root == device_name:
            return True

        # Partition match (startswith check is usually enough for sda1 vs sda, but be careful with sdaa vs sda)
        if base_root.startswith(device_name):
            suffix = base_root[len(device_name):]
            if suffix and (suffix[0].isdigit() or suffix.startswith('p')):
                return True

        return False

    except Exception as e:
        print(f"Error checking system device: {e}")
        return False


def _is_safe_path(path):
    """Validate that a path does not contain malicious patterns like .. or resolve outside allowed roots."""
    if not path:
        return False
    # Normalize and check for path traversal attempts
    normalized = os.path.normpath(os.path.abspath(str(path)))
    # Reject paths that contain .. after normalization (traversal attempts)
    if '..' in os.path.normpath(str(path)).split(os.sep):
        return False
    # Ensure path is under allowed roots
    allowed_roots = ['/mnt/', '/var/lib/alvaos/', '/tmp/', '/home/']
    for root in allowed_roots:
        if normalized.startswith(root) or normalized == root.rstrip('/'):
            return True
    # Special case: allow root for probing
    if normalized == '/':
        return True
    return False


def _existing_probe_path(path):
    target = os.path.normpath(str(path or '').strip())
    if not target:
        return '/'
    # Security: reject path traversal attempts
    if not _is_safe_path(target):
        return '/'
    probe = os.path.realpath(target)
    # Security: verify realpath is still under allowed roots
    allowed_roots = ['/mnt/', '/var/lib/alvaos/', '/tmp/', '/home/', '/']
    probe_allowed = False
    for root in allowed_roots:
        if probe.startswith(root) or probe == root.rstrip('/'):
            probe_allowed = True
            break
    if not probe_allowed:
        return '/'
    while probe != '/' and not os.path.exists(probe):
        next_probe = os.path.dirname(probe)
        if next_probe == probe:
            break
        probe = next_probe
    return probe if probe else '/'


def _base_disk_name_from_partition(path_or_name):
    """Strip a partition suffix from a device path/name, e.g. '/dev/nvme0n1p2' -> 'nvme0n1',
    '/dev/sda2' -> 'sda'."""
    name = os.path.basename(str(path_or_name or '').strip())
    if not name:
        return ''
    match = re.match(r'^(nvme\d+n\d+)p\d+$', name)
    if match:
        return match.group(1)
    match = re.match(r'^(mmcblk\d+)p\d+$', name)
    if match:
        return match.group(1)
    match = re.match(r'^([a-zA-Z]+)\d+$', name)
    if match:
        return match.group(1)
    return name


def get_system_disk_names():
    """Return the set of base disk names (e.g. {'sda', 'sdb'}) backing the system btrfs
    pool. Covers multi-device system installs (RAID1/mirror) where /proc/mounts only
    exposes the single device the kernel mounted root from, so checks based purely on
    the active root device miss the other mirror leg."""
    names: Set[str] = set()
    try:
        pools, root_btrfs_uuid = detect_btrfs_pools()
        if not root_btrfs_uuid:
            return names
        for pool in pools:
            if not pool.get('is_system_pool'):
                continue
            for device_path in pool.get('devices', []):
                base = _base_disk_name_from_partition(device_path)
                if base:
                    names.add(base)
    except Exception as e:
        print(f"Error resolving system disk names: {e}")
    return names


def is_path_on_system_disk(path):
    """Return True when a path resolves to the same filesystem device as root (/)."""
    if platform.system() != 'Linux':
        return False
    candidate = str(path or '').strip()
    if not candidate:
        return False
    try:
        probe = _existing_probe_path(candidate)
        return os.stat(probe).st_dev == os.stat('/').st_dev
    except Exception:
        return False


# ── SMART helpers ─────────────────────────────────────────────────────────────

def _json_or_none(raw_text):
    try:
        text = str(raw_text or "").strip()
        if not text:
            return None
        return json.loads(text)
    except Exception:
        return None


def _smartctl_messages(payload):
    if not isinstance(payload, dict):
        return []
    messages = []
    smartctl_meta = payload.get('smartctl', {})
    if isinstance(smartctl_meta, dict):
        for item in smartctl_meta.get('messages', []) if isinstance(smartctl_meta.get('messages'), list) else []:
            if not isinstance(item, dict):
                continue
            message = str(item.get('string') or '').strip()
            if message:
                messages.append(message)
    return messages


def _smartctl_has_payload(payload):
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get('smart_status'), dict):
        return True
    if payload.get('ata_smart_attributes'):
        return True
    if payload.get('nvme_smart_health_information_log'):
        return True
    if payload.get('scsi_grown_defect_list'):
        return True
    if payload.get('scsi_error_counter_log'):
        return True
    if payload.get('power_on_time'):
        return True
    return False


def _compact_smart_error(error_text):
    text = str(error_text or '').strip()
    if not text:
        return ''
    if ': {' in text:
        text = text.split(': {', 1)[0].strip()
    if len(text) > 320:
        text = text[:320].rstrip() + '...'
    return text


def _collect_smart_report(disk_name, detailed=False, timeout=6):
    disk = str(disk_name or '').strip()
    if not disk:
        return None, '', 'Invalid disk name'
    disk_path = f'/dev/{disk}'

    base_args = ['-a'] if detailed else ['-H', '-A']
    attempts = [list(base_args)]
    if not disk.startswith('nvme'):
        attempts.append(list(base_args) + ['-d', 'sat'])
        attempts.append(list(base_args) + ['-d', 'scsi'])

    saw_unknown_bridge = False
    last_error = ''

    for args in attempts:
        cmd = [CMD['SMARTCTL']] + args + ['-j', disk_path]
        result, err = run_sudo_command(cmd, timeout=timeout)
        payload = _json_or_none((result.stdout if result else '') or '')
        messages = _smartctl_messages(payload)

        msg_text = ' '.join(messages).lower()
        err_text = str(err or '').lower()
        if 'unknown usb bridge' in msg_text or 'unknown usb bridge' in err_text:
            saw_unknown_bridge = True
            if payload and _smartctl_has_payload(payload):
                return payload, '', ''
            continue

        if payload and (_smartctl_has_payload(payload) or payload.get('smart_support', {}).get('available') is False):
            return payload, '', ''

        if err:
            last_error = _compact_smart_error(err)
        elif result and result.stderr:
            last_error = _compact_smart_error(result.stderr)

    if saw_unknown_bridge:
        return None, (
            'SMART monitoring unavailable: unsupported USB-SATA bridge. '
            'Try direct SATA/NVMe connection or a supported USB bridge.'
        ), ''

    return None, '', (last_error or 'Device did not return SMART data')


# ── Pool state ────────────────────────────────────────────────────────────────

def load_pools_state():
    """Load pools state from file"""
    try:
        if os.path.exists(POOLS_STATE_FILE):
            with open(POOLS_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_pools_state(state):
    """Save pools state to file"""
    try:
        ensure_directories()
        with open(POOLS_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving pools state: {e}")


def sanitize_pool_name(name: str, fallback: str = "") -> str:
    base = re.sub(r'[^a-zA-Z0-9._-]+', '-', (name or '').strip()).strip('-')
    if base:
        return base
    if fallback:
        return f"pool-{fallback[:8]}"
    return "pool-imported"


def detect_btrfs_pools():
    pools: List[Dict[str, Any]] = []
    root_btrfs_uuid = None
    if platform.system() != 'Linux':
        return pools, root_btrfs_uuid

    try:
        root_res, root_err = run_sudo_command([CMD['BTRFS'], 'filesystem', 'show', '/'])
        if root_res and root_res.returncode == 0 and not root_err:
            root_match = re.search(r"uuid:\s+([A-Fa-f0-9-]+)", root_res.stdout, re.IGNORECASE)
            if root_match:
                root_btrfs_uuid = root_match.group(1).lower()
    except Exception as e:
        print(f"Warning: Could not detect root Btrfs UUID: {e}")

    result, err = run_sudo_command([CMD['BTRFS'], 'filesystem', 'show'])
    if not result or result.returncode != 0:
        return pools, root_btrfs_uuid

    output = result.stdout or ""
    fs_blocks = re.split(r'Label:', output)
    for block in fs_blocks:
        if not block.strip():
            continue

        uuid_match = re.search(r"uuid:\s+([A-Fa-f0-9-]+)", block, re.IGNORECASE)
        if not uuid_match:
            continue
        uuid_val = uuid_match.group(1)

        label_match = re.match(r"\s*('(.*?)'|\S+)", block)
        label = 'none'
        if label_match:
            label = (label_match.group(2) or label_match.group(1)).strip("'")
            if label == 'none':
                label = 'Unlabeled'

        pool: Dict[str, Any] = {
            'id': uuid_val,
            'name': label,
            'uuid': uuid_val,
            'devices': [],
            'device_sizes_bytes': [],
            'total_size': 'Unknown',
            'used_size': 'Unknown',
            'raid_level': 'Single',
            'status': 'healthy',
            'is_system_pool': bool(root_btrfs_uuid and uuid_val.lower() == root_btrfs_uuid)
        }

        members = parse_show_members(block)
        pool['members'] = members
        pool['devices'] = [m['path'] for m in members if not m['missing']]
        total_match = re.search(r"Total devices\s+(\d+)", block)
        present = len(pool['devices'])
        pool['missing_count'] = max(0, int(total_match.group(1)) - present) if total_match else 0

        size_matches = re.findall(r"devid\s+\d+\s+size\s+(\d+\.?\d*[TiGkMBP]i?B)", block)
        for sm in size_matches:
            b = parse_size_to_bytes(sm)
            if b:
                pool['device_sizes_bytes'].append(b)

        if 'missing' in block.lower():
            pool['status'] = 'degraded'

        pools.append(pool)

    return pools, root_btrfs_uuid


# ── btrfs status output ───────────────────────────────────────────────────────
# Parsers for what `btrfs ... status` prints, so the UI can show long-running
# pool work (replace, scrub, balance) with progress, even after a page reload.

_MEMBER_RE = re.compile(
    r"devid\s+(\d+)\s+size\s+(\S+)\s+used\s+(\S+)\s+path\s+(.+?)(\s+MISSING)?\s*$", re.MULTILINE)


def parse_show_members(block: str) -> List[Dict[str, Any]]:
    """Members of one filesystem in `btrfs filesystem show` output.

    A disk that is gone shows as "path <missing disk> MISSING" while the pool
    is mounted; when it is not mounted, the line is absent altogether."""
    members = []
    for devid, size, used, path, missing in _MEMBER_RE.findall(block or ''):
        gone = bool(missing) or path.startswith('<')
        members.append({
            'devid': int(devid),
            'size': size,
            'size_bytes': parse_size_to_bytes(size) or 0,
            'used': used,
            'path': '' if gone else path.strip(),
            'missing': gone,
        })
    return members


def _percent(text: str):
    match = re.search(r"(\d+(?:\.\d+)?)%", text or '')
    return float(match.group(1)) if match else None


def parse_scrub_status(text: str) -> Dict[str, Any]:
    """`btrfs scrub status POOL` → state, progress and what was found."""
    text = text or ''
    fields = {}
    for line in text.splitlines():
        key, sep, value = line.partition(':')
        if sep:
            fields[key.strip().lower()] = value.strip()
    if 'no stats available' in text or 'status' not in fields:
        return {'state': 'never'}
    state = fields['status'].split()[0].lower() if fields['status'] else 'unknown'
    summary = fields.get('error summary', '')
    errors: Optional[int] = 0
    if summary and 'no errors' not in summary:
        errors = sum(int(n) for n in re.findall(r"=(\d+)", summary)) or None
    uncorrectable = re.search(r"Uncorrectable:\s*(\d+)", text)
    return {
        'state': state,
        'started': fields.get('scrub started') or fields.get('scrub resumed') or '',
        'duration': fields.get('duration', ''),
        'time_left': fields.get('time left', ''),
        'percent': _percent(fields.get('bytes scrubbed', '')) if state == 'running' else None,
        'errors': errors,
        'uncorrectable': int(uncorrectable.group(1)) if uncorrectable else (0 if errors == 0 else None),
        'summary': summary,
    }


def parse_replace_status(text: str) -> Dict[str, Any]:
    """`btrfs replace status -1 POOL` → never / running / finished / canceled."""
    text = (text or '').strip()
    lower = text.lower()
    errors = re.search(r"(\d+) write errs?, (\d+) uncorr\. read errs?", text)
    result: Dict[str, Any] = {'errors': int(errors.group(1)) + int(errors.group(2)) if errors else 0}
    if not text or 'never started' in lower:
        result['state'] = 'never'
    elif 'cancel' in lower:
        result['state'] = 'canceled'
    elif 'suspended' in lower:
        result.update(state='paused', percent=_percent(text))
    elif 'finished' in lower:
        result['state'] = 'finished'
    elif '% done' in lower:
        result.update(state='running', percent=_percent(text))
    else:
        result['state'] = 'unknown'
    result['text'] = text
    return result


def parse_balance_status(text: str) -> Dict[str, Any]:
    """`btrfs balance status POOL` → none / running / paused, with progress."""
    text = text or ''
    lower = text.lower()
    if 'no balance found' in lower:
        return {'state': 'none'}
    state = 'paused' if 'paused' in lower else 'running' if 'running' in lower else 'unknown'
    left = re.search(r"(\d+(?:\.\d+)?)% left", text)
    return {'state': state, 'percent': round(100 - float(left.group(1)), 1) if left else None}


def parse_device_stats(text: str) -> Dict[str, Dict[str, int]]:
    """`btrfs device stats POOL` → {device: {counter: value}}; any non-zero
    counter means the disk returned bad data or failed an I/O at some point."""
    stats: Dict[str, Dict[str, int]] = {}
    for dev, counter, value in re.findall(r"^\[(.+?)\]\.(\w+)\s+(\d+)\s*$", text or '', re.MULTILINE):
        stats.setdefault(dev, {})[counter] = int(value)
    return stats


# ── Disk inventory ────────────────────────────────────────────────────────────
#
# Every disk gets one role, so the UI can say in plain words what is on it and
# only offer what is safe. The same roles decide on the server whether a disk
# may be erased or put into a pool; the privilege helper checks again that the
# disk is not in use (priv_policy.System.busy_devices).

LSBLK_COLUMNS = 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,LABEL,MODEL,SERIAL,TRAN,RM'

# Kernel devices that look like disks but are not hardware a user can pool:
# loop images, optical drives, compressed RAM swap, RAM disks, floppies, and the
# NBD devices Buddy Backup attaches vaults with.
VIRTUAL_DISK_PREFIXES = ('loop', 'sr', 'zram', 'ram', 'fd', 'nbd')

# Mount points that mean "this disk runs the operating system".
SYSTEM_MOUNTPOINTS = {'/', '/boot', '/boot/efi', '/usr', '/var'}

DISK_ROLES = ('system', 'pool', 'in_use', 'other_pool', 'has_data', 'empty')


def read_block_devices() -> List[Dict[str, Any]]:
    """Top-level block devices from lsblk, with their partitions as children."""
    result = subprocess.run(
        ['env', 'LC_ALL=C', CMD['LSBLK'], '-J', '-b', '-o', LSBLK_COLUMNS],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or 'lsblk failed').strip())
    return json.loads(result.stdout or '{}').get('blockdevices') or []


def human_size(value) -> str:
    """Bytes as lsblk prints them: 3.6T, 465.8G, 512M."""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return str(value or 'Unknown')
    for unit in ('B', 'K', 'M', 'G', 'T', 'P'):
        if size < 1024 or unit == 'P':
            text = f'{size:.1f}'.rstrip('0').rstrip('.')
            return f'{text}{unit}'
        size /= 1024
    return 'Unknown'


def _size_bytes(value) -> int:
    """lsblk -b gives bytes; older callers (and tests) may pass '4T'."""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or '').strip()
    if text.isdigit():
        return int(text)
    return parse_size_to_bytes(f'{text}iB' if text[-1:] in 'KMGTP' and text else text) or 0


def _walk(node: Dict[str, Any]):
    yield node
    for child in node.get('children') or []:
        yield from _walk(child)


def _pool_member_names(pool: Dict[str, Any]) -> Set[str]:
    return {_base_disk_name_from_partition(d) for d in pool.get('devices') or [] if d}


def describe_disks(blockdevices: List[Dict[str, Any]], pools: List[Dict[str, Any]],
                   system_disk_names: AbstractSet[str] = frozenset()) -> List[Dict[str, Any]]:
    """Turn lsblk output into the disk list the Storage page shows.

    pools are the known Btrfs pools (detect_btrfs_pools() entries, with
    is_managed / is_system_pool / mount_point filled in). Each disk gets a
    'usage' entry: its role, one plain sentence, and what may be done with it.
    """
    disks = []
    mounted_disks: Set[str] = set()
    for device in blockdevices:
        if device.get('type') == 'disk' and any((n.get('mountpoint') or '') for n in _walk(device)):
            mounted_disks.add(device.get('name') or '')

    for device in blockdevices:
        name = str(device.get('name') or '')
        if device.get('type') != 'disk' or not name or name.startswith(VIRTUAL_DISK_PREFIXES):
            continue
        nodes = list(_walk(device))
        children = device.get('children') or []
        mountpoints = [str(n['mountpoint']) for n in nodes if n.get('mountpoint')]
        filesystems = sorted({str(n['fstype']) for n in nodes if n.get('fstype')})

        pool = next((p for p in pools if name in _pool_member_names(p)), None)
        usage: Dict[str, Any] = {'mountpoints': mountpoints, 'filesystems': filesystems}
        if (name in system_disk_names or any(m in SYSTEM_MOUNTPOINTS for m in mountpoints)
                or (pool and pool.get('is_system_pool'))):
            usage.update(role='system', summary='Runs AlvaOS. It cannot be erased or added to a pool.')
        elif pool and pool.get('is_managed'):
            usage.update(role='pool', pool_id=pool.get('id'), pool_name=pool.get('name'),
                         summary=f'Part of the pool "{pool.get("name")}".')
        elif mountpoints:
            where = 'as swap' if mountpoints[0] == '[SWAP]' else f'at {mountpoints[0]}'
            usage.update(role='in_use', summary=f'In use outside AlvaOS ({where}). Unmount it before using it here.')
        elif pool:
            members_mounted = _pool_member_names(pool) & mounted_disks
            if members_mounted:
                usage.update(role='in_use', pool_id=pool.get('id'), pool_name=pool.get('name'),
                             summary=f'Part of the Btrfs pool "{pool.get("name")}", which is mounted outside AlvaOS.')
            else:
                usage.update(role='other_pool', pool_id=pool.get('id'), pool_name=pool.get('name'),
                             summary=f'Holds the Btrfs pool "{pool.get("name")}". Import it to keep the data, '
                                     'or erase the disk to reuse it.')
        elif filesystems or children:
            what = ', '.join(filesystems) if filesystems else f'{len(children)} partition(s)'
            usage.update(role='has_data', summary=f'Has old data ({what}). Erase it to use the disk for a pool.')
        else:
            usage.update(role='empty', summary='Empty and ready for a pool.')
        usage['can_erase'] = usage['role'] in ('has_data', 'other_pool')
        usage['can_add_to_pool'] = usage['role'] == 'empty'

        model = (device.get('model') or '').strip()
        disks.append({
            'name': name,
            'path': f'/dev/{name}',
            'size': human_size(device.get('size')) if device.get('size') is not None else 'Unknown',
            'size_bytes': _size_bytes(device.get('size')),
            'model': model or 'Unknown',
            'serial': (device.get('serial') or '').strip() or 'N/A',
            'fstype': device.get('fstype') or 'none',
            'mountpoint': device.get('mountpoint'),
            'is_system_disk': usage['role'] == 'system',
            'is_removable': bool(device.get('rm')) or device.get('tran') == 'usb',
            'transport': device.get('tran') or 'unknown',
            'partitions': [
                {'name': c.get('name'), 'size': human_size(c.get('size')) if c.get('size') is not None else 'Unknown',
                 'fstype': c.get('fstype') or 'none', 'mountpoint': c.get('mountpoint')}
                for c in children
            ],
            'usage': usage,
        })
    return disks


def known_pools() -> List[Dict[str, Any]]:
    """Detected Btrfs pools, marked managed when AlvaOS has them in pools.json,
    plus managed pools whose disks are currently missing."""
    detected, _ = detect_btrfs_pools()
    state = load_pools_state()
    by_lower = {str(k).lower(): v for k, v in state.items()}
    seen = set()
    for pool in detected:
        entry = by_lower.get(str(pool.get('id', '')).lower()) or {}
        seen.add(str(pool.get('id', '')).lower())
        pool['is_managed'] = bool(entry.get('mount_point'))
        if entry.get('mount_point'):
            pool['mount_point'] = entry['mount_point']
            pool['name'] = entry.get('name') or pool.get('name')
    for key, entry in state.items():
        if str(key).lower() not in seen and isinstance(entry, dict):
            detected.append({'id': key, 'name': entry.get('name') or key, 'devices': entry.get('devices') or [],
                             'is_managed': bool(entry.get('mount_point')), 'is_system_pool': False,
                             'mount_point': entry.get('mount_point')})
    return detected


def disk_inventory() -> List[Dict[str, Any]]:
    """The live disk list with roles (no SMART data; that is slow and added by the API)."""
    pools = known_pools()
    system_names = {
        name for pool in pools if pool.get('is_system_pool') for name in _pool_member_names(pool)
    }
    devices = read_block_devices()
    system_names |= {str(d.get('name')) for d in devices if is_secure_system_device(str(d.get('name') or ''))}
    return describe_disks(devices, pools, system_names)


def find_disk(disks: List[Dict[str, Any]], device: str):
    """The inventory entry for a name or /dev path, or None."""
    name = os.path.basename(str(device or '').strip())
    return next((d for d in disks if d['name'] == name), None)


def check_disks_for_pool(devices) -> str:
    """'' when every device is an empty, whole disk; otherwise why not."""
    if not isinstance(devices, list) or not devices:
        return 'At least one disk is required'
    if len(set(devices)) != len(devices):
        return 'A disk was selected twice'
    disks = disk_inventory()
    for dev in devices:
        disk = find_disk(disks, dev) if isinstance(dev, str) and dev.startswith('/dev/') else None
        if disk is None or disk['path'] != dev:
            return f'{dev} is not a disk AlvaOS can use'
        usage = disk['usage']
        if not usage['can_add_to_pool']:
            return f'{dev} cannot be used: {usage["summary"]}'
    return ''


# ── Startup ───────────────────────────────────────────────────────────────────

def mount_existing_pools():
    """Mount all known pools on startup"""
    if platform.system() != 'Linux':
        return

    print("Checking and mounting storage pools...")
    pools = load_pools_state()

    for pool_id, pool_info in pools.items():
        name = pool_info.get('name')
        mount_point = pool_info.get('mount_point')
        devices = pool_info.get('devices', [])

        if not name or not mount_point:
            continue

        try:
            # 1. Ensure mount point exists
            if not os.path.exists(mount_point):
                print(f"Creating mount point for {name}: {mount_point}")
                run_sudo_command([CMD['MKDIR'], '-p', mount_point])

            # 2. Check if already mounted
            is_mounted = subprocess.run([CMD['MOUNTPOINT'], '-q', mount_point], check=False).returncode == 0

            if not is_mounted:
                print(f"Mounting pool {name}...")
                mounted = False

                # STRATEGY 1: Mount by UUID (Robust against device changes)
                if pool_id and len(pool_id) > 20:
                    res, err = run_sudo_command([CMD['MOUNT'], '-U', pool_id, mount_point])
                    if res and res.returncode == 0:
                        print(f"Successfully mounted {name} using UUID: {pool_id}")
                        mounted = True

                # STRATEGY 2: Fallback to device path
                if not mounted and devices:
                    print(f"UUID mount not possible/failed for {name}, trying device path: {devices[0]}")
                    res, err = run_sudo_command([CMD['MOUNT'], devices[0], mount_point])
                    if err:
                        print(f"Error mounting {name}: {err}")
                    else:
                        print(f"Successfully mounted {name} using device path")
            else:
                print(f"Pool {name} is already mounted.")

        except Exception as e:
            print(f"Failed to process pool {name}: {e}")
