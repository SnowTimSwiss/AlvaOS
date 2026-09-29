#!/usr/bin/env python3
"""Disks, pools and subvolumes."""

# ── Standard library ──────────────────────────────────────────────────────────
import json
import os
import platform
import re
import subprocess
import time
from datetime import datetime

# ── Third-party ───────────────────────────────────────────────────────────────
from flask import Blueprint, jsonify, request

# ── AlvaOS managers ───────────────────────────────────────────────────────────
from common import (
    CMD, run_sudo_command, build_privileged_cmd, format_bytes_gib,
)
from auth_manager import (
    _get_current_session,
    require_auth, require_csrf_token,
)
from storage_manager import (
    is_secure_system_device, is_path_on_system_disk,
    STORAGE_CACHE, CACHE_TTL, _storage_cache_lock,
    invalidate_storage_cache,
    load_pools_state, save_pools_state,
    detect_btrfs_pools, sanitize_pool_name,
    _collect_smart_report, get_system_disk_names,
)


bp = Blueprint('storage', __name__)

NOT_ON_NAS = 'Storage management is only available on the AlvaOS NAS itself (Linux).'

@bp.route('/api/v1/storage/disks', methods=['GET'])
@require_auth
def get_disks():
    """Get list of all available disks"""
    disks = []
    
    try:
        if platform.system() == 'Linux':
            # Use lsblk to get disk information
            # LC_ALL=C for consistent parsing
            result = subprocess.run(
                ['env', 'LC_ALL=C', CMD['LSBLK'], '-J', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL,TRAN,RM'],
                capture_output=True, text=True, timeout=5
            )
            
            if result.returncode == 0:
                lsblk_data = json.loads(result.stdout)
                system_disk_names = get_system_disk_names()

                # Filter for disk devices (not partitions or loops)
                for device in lsblk_data.get('blockdevices', []):
                    if device.get('type') == 'disk':
                        # Skip loop devices and CD-ROMs
                        if device['name'].startswith('loop') or device['name'].startswith('sr'):
                            continue
                        
                        # Check if disk is system disk (has root partition)
                        is_system_disk = False
                        children = device.get('children', [])
                        for child in children:
                            if child.get('mountpoint') == '/':
                                is_system_disk = True
                                break
                        
                        # Strategy 2: Secure check via /proc/mounts logic
                        if not is_system_disk and platform.system() == 'Linux':
                            if is_secure_system_device(device['name']):
                                is_system_disk = True

                        # Strategy 3: Other legs of a multi-device system pool (RAID/mirror
                        # installs) — /proc/mounts only exposes the device the kernel mounted
                        # root from, not its mirror siblings.
                        if not is_system_disk and device['name'] in system_disk_names:
                            is_system_disk = True
                        
                        # Get SMART data
                        smart_status = 'unknown'
                        temp = None
                        power_on_hours = None
                        
                        try:
                            smart_data, unsupported_reason, _ = _collect_smart_report(
                                device["name"],
                                detailed=False,
                                timeout=5
                            )
                            if isinstance(smart_data, dict):
                                if smart_data.get('smart_support', {}).get('available', True) is False:
                                    smart_status = 'unknown'
                                elif smart_data.get('smart_status', {}).get('passed'):
                                    smart_status = 'healthy'
                                elif isinstance(smart_data.get('smart_status'), dict):
                                    smart_status = 'failed'

                                # Extract temp and hours from attributes (ATA)
                                attributes = smart_data.get('ata_smart_attributes', {}).get('table', [])
                                for attr in attributes:
                                    if attr.get('id') in [194, 190]:
                                        temp = attr.get('raw', {}).get('value')
                                    elif attr.get('id') == 9:
                                        power_on_hours = attr.get('raw', {}).get('value')

                                # NVMe fallback values
                                if temp is None:
                                    temp = smart_data.get('temperature', {}).get('current') or smart_data.get('nvme_smart_health_information_log', {}).get('temperature')
                                if power_on_hours is None:
                                    power_on_hours = smart_data.get('power_on_time', {}).get('hours') or smart_data.get('nvme_smart_health_information_log', {}).get('power_on_hours')
                            elif unsupported_reason:
                                smart_status = 'unknown'
                        except Exception:
                            pass
                        
                        # Determine if removable (USB/SD)
                        is_removable = bool(device.get('rm')) or device.get('tran') == 'usb'

                        disk_info = {
                            'name': device['name'],
                            'path': f'/dev/{device["name"]}',
                            'size': device.get('size', 'Unknown'),
                            'model': device.get('model', 'Unknown').strip() if device.get('model') else 'Unknown',
                            'serial': device.get('serial', 'N/A'),
                            'fstype': device.get('fstype') or 'none', # Fix: detection of empty disks
                            'mountpoint': device.get('mountpoint', None),
                            'is_system_disk': is_system_disk,
                            'smart_status': smart_status,
                            'temp': temp,
                            'power_on_hours': power_on_hours,
                            'is_removable': is_removable,
                            'transport': device.get('tran', 'unknown'),
                            'partitions': []
                        }
                        
                        # Add partition information
                        for child in children:
                            partition = {
                                'name': child['name'],
                                'size': child.get('size', 'Unknown'),
                                'fstype': child.get('fstype') or 'none', # Fix here too
                                'mountpoint': child.get('mountpoint', None)
                            }
                            disk_info['partitions'].append(partition)
                        
                        disks.append(disk_info)
        else:
            # Disk management only exists on the NAS itself; never invent hardware.
            return jsonify({'disks': [], 'error': NOT_ON_NAS}), 200
    
    except Exception as e:
        print(f"Error getting disk info: {e}")
        return jsonify({'error': str(e)}), 500
    
    return jsonify({'disks': disks})

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
    
    # SYSTEM DISK PROTECTION (covers both legs of a RAID/mirror system install)
    if is_secure_system_device(disk_name) or disk_name in get_system_disk_names():
        return jsonify({'error': 'Operation denied: Cannot wipe the system disk.'}), 403

    try:
        if platform.system() == 'Linux':
            disk_path = f'/dev/{disk_name}'

            # 1. Collect children + mountpoints and unmount deepest first.
            device_rows = []
            mountpoints = []
            try:
                lsblk_res, lsblk_err = run_sudo_command(
                    [CMD['LSBLK'], '-nrpo', 'NAME,TYPE,MOUNTPOINT', disk_path],
                    timeout=10
                )
                if not lsblk_err and lsblk_res and lsblk_res.returncode == 0:
                    for raw in (lsblk_res.stdout or '').splitlines():
                        line = raw.strip()
                        if not line:
                            continue
                        parts = line.split(None, 2)
                        name = parts[0].strip() if len(parts) > 0 else ''
                        dev_type = parts[1].strip() if len(parts) > 1 else ''
                        mnt = parts[2].strip() if len(parts) > 2 else ''
                        if not name:
                            continue
                        device_rows.append({'name': name, 'type': dev_type, 'mountpoint': mnt})
                        if mnt and mnt not in ('-', '[SWAP]'):
                            mountpoints.append(mnt)
            except Exception:
                pass

            for mnt in sorted(set(mountpoints), key=len, reverse=True):
                run_sudo_command([CMD['UMOUNT'], '-l', mnt], timeout=20)

            # Also try device-path unmount for remaining holders.
            for row in sorted(device_rows, key=lambda item: len(item.get('name', '')), reverse=True):
                dev_name = row.get('name') or ''
                if not dev_name:
                    continue
                run_sudo_command([CMD['UMOUNT'], '-l', dev_name], timeout=20)

            # 2. Wipe children first, then root disk.
            children = [
                row.get('name')
                for row in sorted(device_rows, key=lambda item: len(item.get('name', '')), reverse=True)
                if row.get('name') and row.get('name') != disk_path
            ]
            for child in children:
                run_sudo_command([CMD['WIPEFS'], '-a', '-f', child], timeout=30)

            res, err = run_sudo_command([CMD['WIPEFS'], '-a', '-f', disk_path], timeout=45)
            if err:
                # Retry once after partprobe in case kernel still holds stale partition refs.
                run_sudo_command([CMD['PARTPROBE'], disk_path], timeout=20)
                res, err = run_sudo_command([CMD['WIPEFS'], '-a', '-f', disk_path], timeout=45)
            if err:
                # Include active mountpoints for actionable troubleshooting.
                busy_mounts = []
                try:
                    mp_res, mp_err = run_sudo_command([CMD['LSBLK'], '-nrpo', 'MOUNTPOINT', disk_path], timeout=10)
                    if not mp_err and mp_res and mp_res.returncode == 0:
                        busy_mounts = [ln.strip() for ln in (mp_res.stdout or '').splitlines() if ln.strip() and ln.strip() != '-']
                except Exception:
                    pass
                extra = f" Active mounts: {', '.join(sorted(set(busy_mounts)))}" if busy_mounts else ""
                return jsonify({'error': f'Wipe failed: {err}{extra}'}), 500

            # 3. Inform kernel of changes
            run_sudo_command([CMD['PARTPROBE'], disk_path], timeout=20)
            invalidate_storage_cache('disks', 'pools')
            
            return jsonify({'success': True, 'message': f'Disk /dev/{disk_name} wiped successfully and is now ready for use.'})
        else:
            return jsonify({'error': NOT_ON_NAS}), 501
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        
        # SYSTEM DISK PROTECTION
        system_disk_names = get_system_disk_names()
        for dev_path in devices:
            # dev_path is like /dev/sda
            dev_name = os.path.basename(dev_path)
            if is_secure_system_device(dev_name) or dev_name in system_disk_names:
                return jsonify({'error': f'Operation denied: Device {dev_name} is the system disk.'}), 403
        
        try:
            if platform.system() == 'Linux':
                # Build mkfs.btrfs command
                cmd = [CMD['MKFS_BTRFS'], '-f', '-L', pool_name]
                
                # Add RAID level
                if raid_level != 'single':
                    cmd.extend(['-d', raid_level, '-m', raid_level])
                
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
        # Delete pool
        data = request.get_json()
        pool_id = data.get('pool_id')
        
        if not pool_id:
            return jsonify({'error': 'Pool ID is required'}), 400
        
        try:
            pools_state = load_pools_state()
            
            if pool_id not in pools_state:
                return jsonify({'error': 'Pool not found'}), 404
            
            pool_info = pools_state[pool_id]
            mount_point = pool_info.get('mount_point')
            
            if platform.system() == 'Linux':
                if mount_point:
                    run_sudo_command([CMD['UMOUNT'], mount_point], timeout=5)
                    
                    try:
                        run_sudo_command([CMD['RMDIR'], mount_point])
                    except Exception:
                        pass
                
                devices = pool_info.get('devices', [])
                for device in devices:
                    try:
                        run_sudo_command([CMD['WIPEFS'], '-a', device])
                    except Exception as e:
                         print(f"Warning: Failed to wipe device {device}: {e}")
            
            del pools_state[pool_id]
            save_pools_state(pools_state)
            invalidate_storage_cache('pools', 'disks')
            
            return jsonify({'success': True, 'message': 'Pool deleted successfully'})
        
        except Exception as e:
            return jsonify({'error': f'Failed to delete pool: {str(e)}'}), 500

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

    # SYSTEM DISK PROTECTION
    system_disk_names = get_system_disk_names()
    for dev_path in devices:
        dev_name = os.path.basename(dev_path)
        if is_secure_system_device(dev_name) or dev_name in system_disk_names:
            return jsonify({'error': f'Operation denied: Device {dev_name} is the system disk.'}), 403

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
            subprocess.Popen(build_privileged_cmd(balance_cmd), env={'LC_ALL': 'C'})

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
