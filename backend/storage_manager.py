#!/usr/bin/env python3
"""
AlvaOS Storage Manager
Disk detection, SMART data, Btrfs pool management, path security, and caching.
"""

import json
import os
import platform
import re
import subprocess
import threading
import time
from datetime import datetime

from common import CMD, run_sudo_command, build_privileged_cmd, ensure_directories, parse_size_to_bytes, format_bytes_gib

# ── State file ────────────────────────────────────────────────────────────────
POOLS_STATE_FILE = '/var/lib/alvaos/pools.json'

# ── Storage cache ─────────────────────────────────────────────────────────────
STORAGE_CACHE = {
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
        return False  # Mock environment safety

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
    pools = []
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

        pool = {
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

        dev_lines = re.findall(r"path\s+(\S+)", block)
        pool['devices'] = [d.strip() for d in dev_lines]

        size_matches = re.findall(r"devid\s+\d+\s+size\s+(\d+\.?\d*[TiGkMBP]i?B)", block)
        for sm in size_matches:
            b = parse_size_to_bytes(sm)
            if b:
                pool['device_sizes_bytes'].append(b)

        if 'missing' in block.lower():
            pool['status'] = 'degraded'

        pools.append(pool)

    return pools, root_btrfs_uuid


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
