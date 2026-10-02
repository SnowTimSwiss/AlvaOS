#!/usr/bin/env python3
"""Disks, pools and subvolumes."""

# ── Standard library ──────────────────────────────────────────────────────────
import os
import platform
import re
import subprocess
import time
from datetime import datetime

# ── Third-party ───────────────────────────────────────────────────────────────
import psutil
from flask import Blueprint, jsonify, request

# ── AlvaOS managers ───────────────────────────────────────────────────────────
import health_checks
from common import (
    CMD, run_sudo_command, build_privileged_cmd, format_bytes_gib,
)
from auth_manager import (
    _get_current_session,
    require_auth, require_csrf_token,
)
from shares_manager import load_shares_state
from storage_manager import (
    is_path_on_system_disk,
    STORAGE_CACHE, CACHE_TTL, _storage_cache_lock,
    invalidate_storage_cache,
    load_pools_state, save_pools_state,
    detect_btrfs_pools, sanitize_pool_name,
    _collect_smart_report, disk_inventory, find_disk, check_disks_for_pool,
    parse_scrub_status, parse_replace_status, parse_balance_status, parse_device_stats,
)


bp = Blueprint('storage', __name__)

# Names for new pools (the create dialog checks the same rule).
POOL_NAME_RE = re.compile(r'^[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?$')

POOL_PROFILES = ('single', 'raid0', 'raid1', 'raid1c3', 'raid1c4', 'raid5', 'raid6', 'raid10')


def mkfs_profile_args(raid_level):
    """mkfs.btrfs -d/-m arguments for a pool profile.

    Btrfs parity (raid5/raid6) is still not recommended for metadata: a crash
    during a write can damage it (the "write hole"). As the Btrfs
    documentation advises, data uses parity but metadata is mirrored with at
    least as many copies as the parity can lose disks."""
    if raid_level == 'single':
        return []
    metadata = {'raid5': 'raid1', 'raid6': 'raid1c3'}.get(raid_level, raid_level)
    return ['-d', raid_level, '-m', metadata]

NOT_ON_NAS = 'Storage management is only available on the AlvaOS NAS itself (Linux).'

@bp.route('/api/v1/storage/disks', methods=['GET'])
@require_auth
def get_disks():
    """All disks, each with its role (usage) and a quick SMART verdict."""
    if platform.system() != 'Linux':
        # Disk management only exists on the NAS itself; never invent hardware.
        return jsonify({'disks': [], 'error': NOT_ON_NAS}), 200
    try:
        disks = disk_inventory()
    except Exception as e:
        print(f"Error getting disk info: {e}")
        return jsonify({'error': str(e)}), 500
    for disk in disks:
        disk.update(_smart_summary(disk['name']))
    return jsonify({'disks': disks})


def _smart_summary(name):
    """healthy / failed / unknown, plus temperature and power-on hours when reported."""
    summary = {'smart_status': 'unknown', 'temp': None, 'power_on_hours': None}
    try:
        data, _, _ = _collect_smart_report(name, detailed=False, timeout=5)
    except Exception:
        return summary
    if not isinstance(data, dict) or data.get('smart_support', {}).get('available', True) is False:
        return summary
    if data.get('smart_status', {}).get('passed'):
        summary['smart_status'] = 'healthy'
    elif isinstance(data.get('smart_status'), dict):
        summary['smart_status'] = 'failed'
    for attr in data.get('ata_smart_attributes', {}).get('table', []):
        if attr.get('id') in (194, 190):
            summary['temp'] = attr.get('raw', {}).get('value')
        elif attr.get('id') == 9:
            summary['power_on_hours'] = attr.get('raw', {}).get('value')
    nvme = data.get('nvme_smart_health_information_log', {})
    if summary['temp'] is None:
        summary['temp'] = data.get('temperature', {}).get('current') or nvme.get('temperature')
    if summary['power_on_hours'] is None:
        summary['power_on_hours'] = data.get('power_on_time', {}).get('hours') or nvme.get('power_on_hours')
    return summary

@bp.route('/api/v1/storage/disks/<disk_name>/smart', methods=['GET'])
@require_auth
def get_disk_smart(disk_name):
    """Get detailed SMART health attributes for a specific disk"""
    if not disk_name.isalnum() and not all(c in '._-' for c in disk_name if not c.isalnum()):
        return jsonify({'error': 'Invalid disk name'}), 400
        
    try:
        if platform.system() == 'Linux':
            data, unsupported_reason, read_error = _collect_smart_report(
                disk_name=disk_name,
                detailed=True,
                timeout=8
            )
            if isinstance(data, dict):
                if not data.get('smart_support', {}).get('available', True):
                    return jsonify({'error': 'SMART not supported on this device (common for USB sticks/bridges)'}), 200
                return jsonify(data)

            if unsupported_reason:
                return jsonify({'error': unsupported_reason}), 200

            if read_error:
                return jsonify({'error': f'SMART read failed: {read_error}'}), 500

            return jsonify({'error': 'Device did not return any SMART data'}), 404
        else:
            return jsonify({'error': NOT_ON_NAS}), 501
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/v1/storage/disks/<disk_name>/wipe', methods=['POST'])
@require_auth(require_admin=True)
@require_csrf_token
def wipe_disk(disk_name):
    """Wipe disk signatures and partition table to make it available for pools"""
    if not disk_name.isalnum() and not all(c in '._-' for c in disk_name if not c.isalnum()):
        return jsonify({'error': 'Invalid disk name'}), 400
    
    if platform.system() != 'Linux':
        return jsonify({'error': NOT_ON_NAS}), 501

    try:
        disk = find_disk(disk_inventory(), disk_name)
    except Exception as e:
        return jsonify({'error': f'Could not read the disks: {e}'}), 500
    if disk is None:
        return jsonify({'error': f'Disk /dev/{disk_name} was not found.'}), 404
    usage = disk['usage']
    if not usage['can_erase']:
        # Only disks with leftover data are erased. A disk in a pool, running the
        # system, or mounted elsewhere is refused instead of unmounted behind the
        # user's back; an empty disk has nothing to erase.
        if usage['role'] == 'empty':
            return jsonify({'error': f'/dev/{disk_name} is already empty.'}), 409
        return jsonify({'error': f'/dev/{disk_name} was not erased. {usage["summary"]}', 'role': usage['role']}), 409

    disk_path = disk['path']
    for part in sorted((p['name'] for p in disk['partitions'] if p.get('name')), key=len, reverse=True):
        run_sudo_command([CMD['WIPEFS'], '-a', '-f', f'/dev/{part}'], timeout=30)
    res, err = run_sudo_command([CMD['WIPEFS'], '-a', '-f', disk_path], timeout=45)
    if err:
        # Retry once after partprobe in case the kernel still holds stale partition refs.
        run_sudo_command([CMD['PARTPROBE'], disk_path], timeout=20)
        res, err = run_sudo_command([CMD['WIPEFS'], '-a', '-f', disk_path], timeout=45)
    if err:
        return jsonify({'error': f'Erasing {disk_path} failed: {err}'}), 500
    run_sudo_command([CMD['PARTPROBE'], disk_path], timeout=20)
    invalidate_storage_cache('disks', 'pools')
    return jsonify({'success': True, 'message': f'{disk_path} was erased and is ready for a pool.'})

@bp.route('/api/v1/storage/pools', methods=['GET', 'POST', 'DELETE'])
@require_auth(require_admin=True)
def manage_pools():
    """Manage Btrfs pools"""

    if request.method == 'GET':
        # Check cache
        current_time = time.time()
        with _storage_cache_lock:
            if STORAGE_CACHE['pools']['expires'] > current_time:
                return jsonify({'pools': STORAGE_CACHE['pools']['data']})

        pools = []
        
        try:
            if platform.system() == 'Linux':
                pools, _ = detect_btrfs_pools()

                # Load pools state to get mount points
                pools_state = load_pools_state()
                state_key_by_lower = {str(key).lower(): key for key in pools_state.keys()}
                detected_ids = set()

                # Get usage information for each pool
                for pool in pools:
                    pool_id = str(pool.get('id', ''))
                    detected_ids.add(pool_id.lower())
                    state_key = pool_id if pool_id in pools_state else state_key_by_lower.get(pool_id.lower())
                    pool_state = pools_state.get(state_key, {}) if state_key else {}
                    mount_point = pool_state.get('mount_point', '')
                    pool['is_managed'] = bool(pool_state and mount_point)
                    if mount_point:
                        pool['mount_point'] = mount_point
                        if mount_point == '/':
                            pool['is_system_pool'] = True

                    if pool['devices']:
                        try:
                            # Prefer the mount point when known: it is guaranteed to reflect the
                            # live, mounted filesystem, whereas a raw device path can point at a
                            # btrfs member that btrfs-progs refuses to introspect directly (e.g.
                            # right after a device add/remove, or for the non-primary leg of a
                            # mirror), which previously caused multi-device system pools to be
                            # misreported as raid_level "single".
                            usage_target = mount_point if mount_point else pool['devices'][0]
                            usage_res, _ = run_sudo_command([CMD['BTRFS'], 'filesystem', 'usage', usage_target], timeout=5)
                            if usage_res and usage_res.returncode == 0:
                                u_out = usage_res.stdout
                                if 'RAID1C3' in u_out:
                                    pool['raid_level'] = 'RAID1C3'
                                elif 'RAID1C4' in u_out:
                                    pool['raid_level'] = 'RAID1C4'
                                elif 'RAID10' in u_out:
                                    pool['raid_level'] = 'RAID10'
                                elif 'RAID1' in u_out:
                                    pool['raid_level'] = 'RAID1'
                                elif 'RAID5' in u_out:
                                    pool['raid_level'] = 'RAID5'
                                elif 'RAID6' in u_out:
                                    pool['raid_level'] = 'RAID6'
                                elif 'RAID0' in u_out:
                                    pool['raid_level'] = 'RAID0'

                                # Prefer explicit sizes when present
                                used_match = re.search(r"Used:\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)
                                dev_match = re.search(r"Device size:\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)
                                fs_match = re.search(r"Filesystem size:\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)
                                free_match = re.search(r"Free \(estimated\):\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)

                                if used_match:
                                    pool['used_size'] = used_match.group(1)
                                if dev_match:
                                    pool['total_size'] = dev_match.group(1)
                                elif fs_match:
                                    pool['total_size'] = fs_match.group(1)
                                if free_match:
                                    pool['free_size'] = free_match.group(1)
                        except Exception as e:
                            print(f"Error getting pool usage: {e}")

                        # Adjust usable size for mirror-like RAID
                        try:
                            if pool['raid_level'] == 'RAID1' and pool['device_sizes_bytes']:
                                total_bytes = sum(pool['device_sizes_bytes'])
                                max_bytes = max(pool['device_sizes_bytes'])
                                usable_bytes = max(0, total_bytes - max_bytes)
                                if usable_bytes > 0:
                                    pool['total_size'] = format_bytes_gib(usable_bytes)
                            elif pool['raid_level'] == 'RAID1C3' and pool['device_sizes_bytes']:
                                total_bytes = sum(pool['device_sizes_bytes'])
                                usable_bytes = total_bytes // 3
                                if usable_bytes > 0:
                                    pool['total_size'] = format_bytes_gib(usable_bytes)
                            elif pool['raid_level'] == 'RAID1C4' and pool['device_sizes_bytes']:
                                total_bytes = sum(pool['device_sizes_bytes'])
                                usable_bytes = total_bytes // 4
                                if usable_bytes > 0:
                                    pool['total_size'] = format_bytes_gib(usable_bytes)
                            elif pool['raid_level'] == 'RAID5' and pool['device_sizes_bytes']:
                                total_bytes = sum(pool['device_sizes_bytes'])
                                max_bytes = max(pool['device_sizes_bytes'])
                                usable_bytes = max(0, total_bytes - max_bytes)
                                if usable_bytes > 0:
                                    pool['total_size'] = format_bytes_gib(usable_bytes)
                            elif pool['raid_level'] == 'RAID6' and pool['device_sizes_bytes']:
                                total_bytes = sum(pool['device_sizes_bytes'])
                                sorted_devs = sorted(pool['device_sizes_bytes'], reverse=True)
                                if len(sorted_devs) >= 2:
                                    usable_bytes = max(0, total_bytes - sum(sorted_devs[:2]))
                                else:
                                    usable_bytes = total_bytes // 2
                                if usable_bytes > 0:
                                    pool['total_size'] = format_bytes_gib(usable_bytes)
                            elif pool['raid_level'] == 'RAID10' and pool['device_sizes_bytes']:
                                total_bytes = sum(pool['device_sizes_bytes'])
                                usable_bytes = total_bytes // 2
                                if usable_bytes > 0:
                                    pool['total_size'] = format_bytes_gib(usable_bytes)
                        except Exception as e:
                            print(f"Error adjusting usable size: {e}")

                        if (pool['used_size'] == 'Unknown' or 'Estimated' not in pool['total_size']) and mount_point:
                            try:
                                df_res = subprocess.run([CMD['DF'], '-h', mount_point], capture_output=True, text=True, timeout=2)
                                if df_res.returncode == 0:
                                    p_lines = df_res.stdout.strip().split('\n')
                                    if len(p_lines) >= 2:
                                        p_parts = p_lines[1].split()
                                        if len(p_parts) >= 4:
                                            pool['total_size'] = p_parts[1]
                                            pool['used_size'] = p_parts[2]
                            except Exception:
                                pass

                # Fallback: include managed pools that were not returned by btrfs detection.
                for state_key, pool_state in pools_state.items():
                    state_id = str(state_key)
                    if state_id.lower() in detected_ids:
                        continue

                    mount_point = (pool_state.get('mount_point') or '').strip()
                    raid_level = str(pool_state.get('raid_level', 'single')).strip().lower()
                    pool_entry = {
                        'id': state_id,
                        'name': pool_state.get('name') or state_id,
                        'uuid': state_id,
                        'devices': pool_state.get('devices', []) if isinstance(pool_state.get('devices', []), list) else [],
                        'device_sizes_bytes': [],
                        'total_size': 'Unknown',
                        'used_size': 'Unknown',
                        'raid_level': 'Single' if raid_level == 'single' else raid_level.upper(),
                        'status': 'healthy',
                        'is_system_pool': mount_point == '/',
                        'is_managed': bool(mount_point)
                    }
                    if mount_point:
                        pool_entry['mount_point'] = mount_point
                        try:
                            df_res = subprocess.run([CMD['DF'], '-h', mount_point], capture_output=True, text=True, timeout=2)
                            if df_res.returncode == 0:
                                p_lines = df_res.stdout.strip().split('\n')
                                if len(p_lines) >= 2:
                                    p_parts = p_lines[1].split()
                                    if len(p_parts) >= 4:
                                        pool_entry['total_size'] = p_parts[1]
                                        pool_entry['used_size'] = p_parts[2]
                        except Exception:
                            pass
                    pools.append(pool_entry)
            else:
                # Not Linux: report honestly that storage cannot be inspected
                # here. Returning invented pools made dev runs look like real
                # hardware and turned every test into a guess.
                pools = []
        except Exception as e:
            print(f"Error in manage_pools GET: {e}")

        for pool in pools:
            pool.update(_usage_bytes(pool.get('mount_point')))

        with _storage_cache_lock:
            STORAGE_CACHE['pools'] = {'data': pools, 'expires': current_time + CACHE_TTL}
        return jsonify({'pools': pools})

    # CSRF required for state-changing operations
    session = _get_current_session()
    csrf_token = request.headers.get('X-CSRF-Token', '').strip()
    if not csrf_token or csrf_token != (session or {}).get('csrf_token', ''):
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    elif request.method == 'POST':
        # Create new pool
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        pool_name = data.get('name', '').strip()
        devices = data.get('devices', [])
        raid_level = data.get('raid_level', 'single')
        
        # Validation
        if not pool_name:
            return jsonify({'error': 'Pool name is required'}), 400
        
        if not devices or len(devices) == 0:
            return jsonify({'error': 'At least one device is required'}), 400
        
        if raid_level not in POOL_PROFILES:
            return jsonify({'error': 'Unknown protection level'}), 400
        if raid_level in ('raid0', 'raid10') and len(devices) < 2:
            return jsonify({'error': f'{raid_level.upper()} requires at least 2 devices'}), 400

        # Validate RAID level requirements
        if raid_level == 'raid1' and len(devices) < 2:
            return jsonify({'error': 'RAID1 requires at least 2 devices'}), 400
        
        if raid_level == 'raid5' and len(devices) < 3:
            return jsonify({'error': 'RAID5 requires at least 3 devices'}), 400

        if raid_level == 'raid1c3' and len(devices) < 3:
            return jsonify({'error': 'RAID1c3 requires at least 3 devices'}), 400

        if raid_level == 'raid6' and len(devices) < 4:
            return jsonify({'error': 'RAID6 requires at least 4 devices'}), 400

        if raid_level == 'raid1c4' and len(devices) < 4:
            return jsonify({'error': 'RAID1c4 requires at least 4 devices'}), 400

        if raid_level == 'raid10' and len(devices) < 4:
            return jsonify({'error': 'RAID10 requires at least 4 devices'}), 400
        
        if not POOL_NAME_RE.match(pool_name):
            return jsonify({'error': 'Pool names use lowercase letters, numbers, "-" and "_" (up to 63 characters).'}), 400
        if any(isinstance(p, dict) and p.get('mount_point') == f'/mnt/alvaos/{pool_name}'
               for p in load_pools_state().values()):
            return jsonify({'error': f'A pool named "{pool_name}" already exists.'}), 409

        try:
            if platform.system() == 'Linux':
                # Only empty disks: mkfs erases whatever is on them.
                problem = check_disks_for_pool(devices)
                if problem:
                    return jsonify({'error': problem}), 409

                # Build mkfs.btrfs command
                cmd = [CMD['MKFS_BTRFS'], '-f', '-L', pool_name]
                
                # Add RAID level (parity profiles keep metadata mirrored)
                cmd.extend(mkfs_profile_args(raid_level))
                
                # Add devices
                cmd.extend(devices)
                
                # Execute pool creation
                res, err = run_sudo_command(cmd, timeout=60)
                
                if err:
                    return jsonify({'error': f'Failed to create pool: {err}'}), 500
                
                # Create mount point
                mount_point = f'/mnt/alvaos/{pool_name}'
                # Use sudo to create directory as we might not have permission in /mnt/alvaos
                res, err = run_sudo_command([CMD['MKDIR'], '-p', mount_point])
                if err:
                     return jsonify({'error': f'Failed to create mount point: {err}'}), 500
                
                # Mount the pool
                res, err = run_sudo_command([CMD['MOUNT'], devices[0], mount_point])
                if err:
                    return jsonify({'error': f'Pool created but failed to mount: {err}'}), 500
                
                # Save pool state
                pools_state = load_pools_state()
                
                # Get real BTRFS UUID to use as ID (matches get_pools logic)
                import uuid
                pool_id = str(uuid.uuid4()) # Fallback
                try:
                    # blkid returns just the UUID value
                    blkid_res, _ = run_sudo_command([CMD['BLKID'], '-s', 'UUID', '-o', 'value', devices[0]])
                    if blkid_res and blkid_res.returncode == 0:
                         real_uuid = blkid_res.stdout.strip()
                         if real_uuid:
                             pool_id = real_uuid
                except Exception:
                    pass

                pools_state[pool_id] = {
                    'name': pool_name,
                    'devices': devices,
                    'raid_level': raid_level,
                    'mount_point': mount_point,
                    'created_at': datetime.now().isoformat()
                }
                save_pools_state(pools_state)
                invalidate_storage_cache('pools', 'disks')
                
                return jsonify({
                    'success': True,
                    'message': f'Pool "{pool_name}" created successfully',
                    'pool_id': pool_id,
                    'mount_point': mount_point
                })
            else:
                # Never pretend a pool was created. A fake success here is worse
                # than an error: the UI would show a pool that does not exist.
                return jsonify({
                    'error': 'Storage pools can only be created on the NAS itself '
                             '(Linux with btrfs). This system is not supported.',
                    'unsupported_platform': True,
                }), 400
        
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Pool creation timed out'}), 500
        except Exception as e:
            return jsonify({'error': f'Pool creation failed: {str(e)}'}), 500
    
    elif request.method == 'DELETE':
        return _remove_pool(request.get_json(silent=True) or {})


def _usage_bytes(mount_point):
    """Used / free / total bytes of a mounted pool, as df sees it (the same
    numbers the capacity alerts use)."""
    if not mount_point or not os.path.ismount(mount_point):
        return {}
    try:
        usage = psutil.disk_usage(mount_point)
    except OSError:
        return {}
    return {'used_bytes': usage.used, 'free_bytes': usage.free, 'total_bytes': usage.total,
            'used_percent': round(usage.percent, 1)}


def _path_within(path, root):
    path, root = os.path.normpath(path or '/'), os.path.normpath(root)
    return path == root or path.startswith(root.rstrip('/') + '/')


def _is_mounted(path):
    return subprocess.run([CMD['MOUNTPOINT'], '-q', path], check=False).returncode == 0


def _remove_pool(data):
    """Take a pool out of AlvaOS. Its data stays on the disks (the pool can be
    imported again) unless erase is true, which wipes every member disk."""
    pool_id = str(data.get('pool_id') or '')
    erase = data.get('erase') is True
    if not pool_id:
        return jsonify({'error': 'Pool ID is required'}), 400

    pools_state = load_pools_state()
    pool_info = pools_state.get(pool_id)
    if not isinstance(pool_info, dict):
        return jsonify({'error': 'Pool not found'}), 404
    name = pool_info.get('name') or pool_id
    mount_point = str(pool_info.get('mount_point') or '')
    if mount_point == '/':
        return jsonify({'error': 'The system pool cannot be removed.'}), 403

    if mount_point:
        users = sorted(
            str(share.get('name') or share_id) for share_id, share in load_shares_state().items()
            if isinstance(share, dict) and _path_within(str(share.get('path') or ''), mount_point)
        )
        if users:
            return jsonify({'error': f'Pool "{name}" is still shared as {", ".join(users)}. '
                                     'Delete those shares first.', 'shares': users}), 409

    devices = list(pool_info.get('devices') or [])
    if platform.system() == 'Linux':
        live = next((p for p in detect_btrfs_pools()[0] if str(p.get('id', '')).lower() == pool_id.lower()), None)
        if live and live.get('devices'):
            devices = list(live['devices'])
        if mount_point and _is_mounted(mount_point):
            run_sudo_command([CMD['UMOUNT'], mount_point], timeout=30)
            if _is_mounted(mount_point):
                return jsonify({'error': f'Pool "{name}" is busy, so it was not removed. '
                                         'Stop the apps and backups that use it, then try again.'}), 409
        if mount_point:
            run_sudo_command([CMD['RMDIR'], mount_point])

    del pools_state[pool_id]
    save_pools_state(pools_state)
    invalidate_storage_cache('pools', 'disks')

    if not erase:
        return jsonify({'success': True, 'erased': False,
                        'message': f'Pool "{name}" was removed. Its data is still on the disks; '
                                   'import it again from the Pools tab to get it back.'})

    failed = []
    if platform.system() == 'Linux':
        for device in devices:
            _, err = run_sudo_command([CMD['WIPEFS'], '-a', '-f', device], timeout=45)
            if err:
                failed.append(device)
    invalidate_storage_cache('pools', 'disks')
    if failed:
        return jsonify({'success': True, 'erased': False, 'failed': failed,
                        'message': f'Pool "{name}" was removed, but {", ".join(failed)} could not be erased. '
                                   'Erase them on the Disks tab.'})
    return jsonify({'success': True, 'erased': True,
                    'message': f'Pool "{name}" was removed and its disks were erased.'})

@bp.route('/api/v1/storage/pools/import', methods=['POST'])
@require_auth(require_admin=True)
def import_pool():
    """Import an existing detected Btrfs pool into managed state."""
    data = request.get_json() or {}
    pool_id = (data.get('pool_id') or '').strip()
    requested_name = (data.get('pool_name') or '').strip()
    requested_mount = (data.get('mount_point') or '').strip()

    if not pool_id:
        return jsonify({'error': 'pool_id is required'}), 400

    if platform.system() != 'Linux':
        return jsonify({'error': NOT_ON_NAS}), 501

    try:
        detected_pools, root_uuid = detect_btrfs_pools()
        detected = None
        for pool in detected_pools:
            if (pool.get('id') or '').lower() == pool_id.lower():
                detected = pool
                break

        if not detected:
            return jsonify({'error': f'Pool not detected: {pool_id}'}), 404

        if root_uuid and pool_id.lower() == root_uuid:
            return jsonify({'error': 'System root pool cannot be imported via this endpoint'}), 400

        pools_state = load_pools_state()
        existing = pools_state.get(pool_id, {})
        pool_name = requested_name or existing.get('name') or detected.get('name') or f'pool-{pool_id[:8]}'
        safe_name = sanitize_pool_name(pool_name, pool_id)

        mount_point = requested_mount or existing.get('mount_point') or f'/mnt/alvaos/{safe_name}'
        used_mounts = {
            (item.get('mount_point') or '').strip()
            for item in pools_state.values()
            if isinstance(item, dict) and (item.get('mount_point') or '').strip()
        }
        if not requested_mount and mount_point in used_mounts and pool_id not in pools_state:
            base_mount = mount_point
            suffix = 2
            while mount_point in used_mounts:
                mount_point = f"{base_mount}-{suffix}"
                suffix += 1

        mk_res, mk_err = run_sudo_command([CMD['MKDIR'], '-p', mount_point])
        if mk_err or not mk_res or mk_res.returncode != 0:
            return jsonify({'error': f'Failed to create mount point: {mk_err or "unknown error"}'}), 500

        is_mounted = subprocess.run([CMD['MOUNTPOINT'], '-q', mount_point], check=False).returncode == 0
        if not is_mounted:
            mount_res, mount_err = run_sudo_command([CMD['MOUNT'], '-U', pool_id, mount_point], timeout=30)
            if mount_err or not mount_res or mount_res.returncode != 0:
                devices = detected.get('devices') or []
                fallback_device = devices[0] if devices else None
                if not fallback_device:
                    return jsonify({'error': f'Failed to mount pool and no fallback device available: {mount_err or "unknown error"}'}), 500
                mount_res, mount_err = run_sudo_command([CMD['MOUNT'], fallback_device, mount_point], timeout=30)
                if mount_err or not mount_res or mount_res.returncode != 0:
                    return jsonify({'error': f'Failed to mount imported pool: {mount_err or "unknown error"}'}), 500

        pools_state[pool_id] = {
            'name': pool_name,
            'devices': detected.get('devices', []),
            'raid_level': str(detected.get('raid_level', 'single')).lower(),
            'mount_point': mount_point,
            'created_at': existing.get('created_at') or datetime.now().isoformat(),
            'imported_at': datetime.now().isoformat(),
        }
        save_pools_state(pools_state)
        invalidate_storage_cache('pools', 'disks')

        return jsonify({
            'success': True,
            'message': f'Pool "{pool_name}" imported successfully',
            'pool_id': pool_id,
            'mount_point': mount_point
        })
    except Exception as e:
        return jsonify({'error': f'Failed to import pool: {str(e)}'}), 500

@bp.route('/api/v1/storage/pools/<pool_id>/subvolumes', methods=['GET', 'POST', 'DELETE'])
@require_auth(require_admin=True)
def manage_subvolumes(pool_id):
    """Manage subvolumes in a pool"""
    
    pools_state = load_pools_state()
    
    if pool_id not in pools_state:
        return jsonify({'error': 'Pool not found'}), 404
    
    pool_info = pools_state[pool_id]
    mount_point = pool_info.get('mount_point')
    
    if request.method == 'GET':
        # List subvolumes
        subvolumes = []
        
        try:
            if platform.system() == 'Linux' and mount_point:
                # Use run_sudo_command instead of direct subprocess.run with 'sudo -n' string
                res, err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'list', mount_point], timeout=5)
                
                if res and res.returncode == 0:
                    for line in res.stdout.split('\n'):
                        if line.strip():
                            # Parse: ID 256 gen 7 top level 5 path subvol1
                            parts = line.split()
                            if 'path' in parts:
                                path_idx = parts.index('path')
                                if path_idx + 1 < len(parts):
                                    subvol_name = parts[path_idx + 1]
                                    subvolumes.append({
                                        'name': subvol_name,
                                        'path': f'{mount_point}/{subvol_name}'
                                    })
            else:
                return jsonify({'subvolumes': [], 'error': NOT_ON_NAS})
        
        except Exception as e:
            print(f"Error listing subvolumes: {e}")
        
        return jsonify({'subvolumes': subvolumes})
    
    elif request.method == 'POST':
        # Create subvolume
        data = request.get_json()
        subvol_name = data.get('name', '').strip()
        
        if not subvol_name:
            return jsonify({'error': 'Subvolume name is required'}), 400
        
        try:
            if platform.system() == 'Linux' and mount_point:
                subvol_path = f'{mount_point}/{subvol_name}'
                
                res, err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'create', subvol_path])
                
                if err:
                    return jsonify({'error': f'Failed to create subvolume: {err}'}), 500
                
                return jsonify({
                    'success': True,
                    'message': f'Subvolume "{subvol_name}" created',
                    'path': subvol_path
                })
            else:
                return jsonify({'error': NOT_ON_NAS}), 501
        
        except Exception as e:
            return jsonify({'error': f'Failed to create subvolume: {str(e)}'}), 500
    
    elif request.method == 'DELETE':
        # Delete subvolume
        data = request.get_json()
        subvol_name = data.get('name', '').strip()
        
        if not subvol_name:
            return jsonify({'error': 'Subvolume name is required'}), 400
        
        try:
            if platform.system() == 'Linux' and mount_point:
                subvol_path = f'{mount_point}/{subvol_name}'
                
                res, err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'delete', subvol_path])
                
                if err:
                    err_text = str(err)
                    if 'not deleting default subvolume id' in err_text.lower():
                        def _parse_subvol_id(text):
                            m = re.search(r'Subvolume ID:\s*(\d+)', str(text or ''))
                            if not m:
                                return None
                            try:
                                return int(m.group(1))
                            except Exception:
                                return None

                        def _parse_default_id(text):
                            m = re.search(r'ID\s+(\d+)', str(text or ''))
                            if not m:
                                return None
                            try:
                                return int(m.group(1))
                            except Exception:
                                return None

                        active_id = None
                        target_id = None
                        default_id = None

                        active_res, active_err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'show', mount_point], timeout=15)
                        if not active_err and active_res and active_res.returncode == 0:
                            active_id = _parse_subvol_id(active_res.stdout)

                        target_res, target_err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'show', subvol_path], timeout=15)
                        if not target_err and target_res and target_res.returncode == 0:
                            target_id = _parse_subvol_id(target_res.stdout)

                        default_res, default_err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'get-default', mount_point], timeout=15)
                        if not default_err and default_res and default_res.returncode == 0:
                            default_id = _parse_default_id(default_res.stdout)

                        if (
                            active_id is not None
                            and target_id is not None
                            and default_id is not None
                            and default_id == target_id
                            and active_id != target_id
                        ):
                            set_res, set_err = run_sudo_command(
                                [CMD['BTRFS'], 'subvolume', 'set-default', str(active_id), mount_point],
                                timeout=20
                            )
                            if not set_err and set_res and set_res.returncode == 0:
                                res, err = run_sudo_command([CMD['BTRFS'], 'subvolume', 'delete', subvol_path])

                if err and 'not deleting default subvolume id' in str(err).lower():
                    return jsonify({
                        'error': (
                            'Failed to delete subvolume: this snapshot is currently configured as default. '
                            'Restore/switch to another snapshot first, then delete it.'
                        )
                    }), 409

                if err:
                    return jsonify({'error': f'Failed to delete subvolume: {err}'}), 500
                
                return jsonify({
                    'success': True,
                    'message': f'Subvolume "{subvol_name}" deleted'
                })
            else:
                return jsonify({'error': NOT_ON_NAS}), 501
        
        except Exception as e:
            return jsonify({'error': f'Failed to delete subvolume: {str(e)}'}), 500

@bp.route('/api/v1/storage/pools/<pool_id>/expand', methods=['POST'])
@require_auth(require_admin=True)
def expand_pool(pool_id):
    """Add new devices to an existing pool"""
    data = request.get_json()
    devices = data.get('devices', [])
    target_raid_level = str(data.get('raid_level') or '').strip().lower()

    if not devices:
        return jsonify({'error': 'No devices provided'}), 400

    if platform.system() == 'Linux':
        # Only empty disks: adding a disk to a pool erases it.
        problem = check_disks_for_pool(devices)
        if problem:
            return jsonify({'error': problem}), 409

    pools_state = load_pools_state()
    pool_info = pools_state.get(pool_id)

    # The system/root pool is created by the installer, not through this app's "create pool"
    # flow, so it normally has no entry in pools.json. To allow replacing a failed mirror leg
    # on a redundant (raid1) system install, fall back to the live btrfs detection when the
    # pool_id matches the currently-mounted root pool.
    if pool_info is None:
        live_pools, root_btrfs_uuid = detect_btrfs_pools()
        if root_btrfs_uuid and pool_id.lower() == root_btrfs_uuid.lower():
            live_pool = next((p for p in live_pools if p.get('is_system_pool')), None)
            if live_pool:
                if str(live_pool.get('raid_level', 'single')).strip().lower() == 'single':
                    return jsonify({'error': 'Operation denied: the system pool is not redundant (raid1), so it cannot be expanded. Reinstall to set up a mirrored system disk.'}), 403
                pool_info = {
                    'name': live_pool.get('name') or 'system',
                    'devices': list(live_pool.get('devices', [])),
                    'raid_level': str(live_pool.get('raid_level', 'single')).strip().lower(),
                    'mount_point': '/',
                }

    if pool_info is None:
        return jsonify({'error': 'Pool not found'}), 404

    mount_point = pool_info.get('mount_point')

    try:
        if platform.system() == 'Linux':
            if not mount_point:
                return jsonify({'error': 'Pool not mounted'}), 400

            # Drop bookkeeping for any device that already went missing (e.g. a previously
            # failed/removed disk in a degraded pool) so the pool can leave "degraded" state
            # once the replacement below is balanced in.
            run_sudo_command([CMD['BTRFS'], 'device', 'remove', 'missing', mount_point], timeout=30)

            # Add devices to pool
            # cmd: sudo btrfs device add /dev/sdX /mnt/alvaos/poolname
            cmd = [CMD['BTRFS'], 'device', 'add'] + devices + [mount_point]
            res, err = run_sudo_command(cmd, timeout=60)

            if err:
                return jsonify({'error': f'Failed to add devices: {err}'}), 500

            # Decide the data/metadata profile to balance into. An explicit raid_level wins;
            # otherwise default newly-redundant pools (now >=2 devices) to raid1 so a plain
            # "add a disk" expand on a single-profile pool actually becomes redundant instead
            # of silently staying single with two devices.
            balance_level = target_raid_level
            if not balance_level:
                current_devices = len(pool_info.get('devices', [])) + len(devices)
                current_raid = str(pool_info.get('raid_level', 'single')).strip().lower()
                if current_devices >= 2 and current_raid == 'single':
                    balance_level = 'raid1'

            if balance_level:
                balance_cmd = [
                    CMD['BTRFS'], 'balance', 'start',
                    f'-dconvert={balance_level}', f'-mconvert={balance_level}',
                    mount_point
                ]
                pool_info['raid_level'] = balance_level
            else:
                balance_cmd = [CMD['BTRFS'], 'balance', 'start', mount_point]

            # Start the balance in the background to redistribute/convert data without
            # blocking the request.
            _start_background(balance_cmd)

            # Update state
            pool_info['devices'].extend(devices)
            pools_state[pool_id] = pool_info
            save_pools_state(pools_state)
            invalidate_storage_cache('pools', 'disks')

            return jsonify({
                'success': True,
                'message': f'Added {len(devices)} device(s) to pool "{pool_info["name"]}"'
            })
        else:
            return jsonify({'error': NOT_ON_NAS}), 501
    except Exception as e:
        return jsonify({'error': f'Failed to expand pool: {str(e)}'}), 500

# ── Long-running pool work: replace, scrub, balance ─────────────────────────

def _start_background(cmd):
    """Start a long btrfs job (it runs for minutes to hours) without waiting for
    it. Returns an error when it fails right away, e.g. a target disk too small."""
    proc = subprocess.Popen(build_privileged_cmd(cmd), env={'LC_ALL': 'C'},
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        return ''
    if proc.returncode == 0:
        return ''
    detail = (proc.stderr.read() if proc.stderr else '').strip()
    return detail.splitlines()[-1] if detail else f'exit code {proc.returncode}'


def _mounted_managed_pool(pool_id):
    """(pool state entry, None) for a managed pool that is mounted, else (None, error response)."""
    pool_info = load_pools_state().get(pool_id)
    if not isinstance(pool_info, dict):
        return None, (jsonify({'error': 'Pool not found'}), 404)
    mount_point = str(pool_info.get('mount_point') or '')
    if mount_point == '/':
        return None, (jsonify({'error': 'This is not available for the system pool.'}), 400)
    if platform.system() != 'Linux':
        return None, (jsonify({'error': NOT_ON_NAS}), 501)
    if not mount_point or not _is_mounted(mount_point):
        return None, (jsonify({'error': f'Pool "{pool_info.get("name") or pool_id}" is not mounted.'}), 409)
    return pool_info, None


def _btrfs_output(args, timeout=10):
    res, err = run_sudo_command([CMD['BTRFS']] + args, timeout=timeout)
    return (res.stdout if res else '') or '', err


def _pool_activity(mount_point):
    scrub_out, _ = _btrfs_output(['scrub', 'status', mount_point])
    replace_out, _ = _btrfs_output(['replace', 'status', '-1', mount_point])
    balance_out, _ = _btrfs_output(['balance', 'status', mount_point])
    stats_out, _ = _btrfs_output(['device', 'stats', mount_point])
    return {
        'scrub': parse_scrub_status(scrub_out),
        'replace': parse_replace_status(replace_out),
        'balance': parse_balance_status(balance_out),
        'device_stats': parse_device_stats(stats_out),
    }


@bp.route('/api/v1/storage/pools/<pool_id>/activity', methods=['GET'])
@require_auth(require_admin=True)
def pool_activity(pool_id):
    """What the pool is doing (replace, data check, balance) and error counters per disk."""
    pool_info, error = _mounted_managed_pool(pool_id)
    if error:
        return error
    return jsonify(_pool_activity(pool_info['mount_point']))


def _busy_with(activity):
    if activity['replace'].get('state') == 'running':
        return 'A disk is being replaced'
    if activity['balance'].get('state') in ('running', 'paused'):
        return 'Data is being spread over the disks'
    if activity['scrub'].get('state') == 'running':
        return 'A data check is running'
    return ''


@bp.route('/api/v1/storage/pools/<pool_id>/scrub', methods=['POST'])
@require_auth(require_admin=True)
def scrub_pool(pool_id):
    """Start (or cancel) a data check: btrfs reads every block and repairs bad
    copies from the good one when the pool is redundant."""
    pool_info, error = _mounted_managed_pool(pool_id)
    if error:
        return error
    mount_point = pool_info['mount_point']
    if (request.get_json(silent=True) or {}).get('action') == 'cancel':
        _, err = _btrfs_output(['scrub', 'cancel', mount_point])
        if err:
            return jsonify({'error': 'No data check is running.'}), 409
        return jsonify({'success': True, 'message': 'The data check was stopped.'})
    busy = _busy_with(_pool_activity(mount_point))
    if busy:
        return jsonify({'error': f'{busy}. Try again when it is done.'}), 409
    failed = _start_background([CMD['BTRFS'], 'scrub', 'start', '-B', mount_point])
    if failed:
        return jsonify({'error': f'The data check did not start: {failed}'}), 500
    return jsonify({'success': True, 'message': 'Data check started. The pool stays usable meanwhile.'})


def _mounted_managed_pools():
    """{pool_id: state entry} for managed pools that are mounted (not the system)."""
    result = {}
    for pool_id, info in load_pools_state().items():
        mount_point = str((info or {}).get('mount_point') or '') if isinstance(info, dict) else ''
        if mount_point and mount_point != '/' and _is_mounted(mount_point):
            result[str(pool_id)] = info
    return result


def make_health_scheduler():
    """The nightly data-check scheduler, wired to the real btrfs commands."""
    return health_checks.HealthScheduler(
        pools=_mounted_managed_pools,
        activity=_pool_activity,
        busy=_busy_with,
        start_scrub=lambda mount_point: _start_background([CMD['BTRFS'], 'scrub', 'start', '-B', mount_point]),
    )


@bp.route('/api/v1/storage/health-checks', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def storage_health_checks():
    """How often pools get a data check, and the last result per pool."""
    if request.method == 'POST':
        try:
            health_checks.save_settings(request.get_json(silent=True) or {})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({
        'settings': health_checks.get_settings(),
        'pools': health_checks.last_results(),
    })


@bp.route('/api/v1/storage/pools/<pool_id>/replace', methods=['POST'])
@require_auth(require_admin=True)
def replace_pool_disk(pool_id):
    """Copy one member (failing or missing) onto an empty disk with btrfs replace.
    The pool stays online; for a missing disk the data is rebuilt from the mirror."""
    pool_info, error = _mounted_managed_pool(pool_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    source = data.get('source')
    target = str(data.get('target') or '')
    mount_point = pool_info['mount_point']

    live = next((p for p in detect_btrfs_pools()[0] if str(p.get('id', '')).lower() == pool_id.lower()), None)
    members = (live or {}).get('members') or []
    member = next((m for m in members if str(m['devid']) == str(source) or (m['path'] and m['path'] == source)), None)
    if member is None:
        return jsonify({'error': 'Choose a disk of this pool to replace.'}), 400
    problem = check_disks_for_pool([target])
    if problem:
        return jsonify({'error': problem}), 409
    new_disk = find_disk(disk_inventory(), target) or {}
    if member['size_bytes'] and new_disk.get('size_bytes', 0) < member['size_bytes']:
        return jsonify({'error': f'{target} is smaller than the disk it replaces ({member["size"]}). '
                                 'Use a disk of the same size or larger.'}), 409
    busy = _busy_with(_pool_activity(mount_point))
    if busy:
        return jsonify({'error': f'{busy}. Try again when it is done.'}), 409

    failed = _start_background([CMD['BTRFS'], 'replace', 'start', '-B', str(member['devid']), target, mount_point])
    if failed:
        return jsonify({'error': f'The replacement did not start: {failed}'}), 500

    devices = [d for d in pool_info.get('devices') or [] if d != member['path']]
    pool_info['devices'] = devices + [target]
    state = load_pools_state()
    state[pool_id] = pool_info
    save_pools_state(state)
    invalidate_storage_cache('pools', 'disks')
    old = member['path'] or f'the missing disk (#{member["devid"]})'
    return jsonify({'success': True,
                    'message': f'Replacing {old} with {target}. The pool stays usable; this can take hours.'})


# ============================================================================
@bp.route('/api/v1/storage/available-paths', methods=['GET'])
@require_auth
def get_available_paths():
    """Get list of all potential share paths (pools and subvolumes)"""
    paths = []


    # Add Pools
    pools = load_pools_state()
    for pid, pool in pools.items():
        mount_point = os.path.normpath(str(pool.get('mount_point') or '').strip())
        if not mount_point:
            continue
        if platform.system() == 'Linux' and is_path_on_system_disk(mount_point):
            continue

        paths.append({'name': f"Pool: {pool.get('name') or pid}", 'path': mount_point})

        # Dynamic subvolume lookup.
        if platform.system() == 'Linux':
            try:
                result, err = run_sudo_command(
                    [CMD['BTRFS'], 'subvolume', 'list', mount_point], timeout=3
                )
                if result and result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if not line.strip():
                            continue
                        # ID 256 gen 7 top level 5 path subvol1
                        parts = line.split()
                        try:
                            path_idx = parts.index('path')
                        except ValueError:
                            continue

                        if path_idx + 1 < len(parts):
                            subvol_name = parts[path_idx + 1]
                            subvol_path = os.path.normpath(os.path.join(mount_point, subvol_name))
                            if is_path_on_system_disk(subvol_path):
                                continue
                            paths.append({
                                'name': f"  -> Subvolume: {subvol_name}",
                                'path': subvol_path
                            })
            except Exception as e:
                print(f"Error listing subvolumes for path: {e}")
        # On non-Linux systems there are no btrfs subvolumes to list; the pool
        # mount point on its own is the honest answer.

    return jsonify({'paths': paths})
