#!/usr/bin/env python3
"""System information, alerts, notifications, time, power, network, SSH, logs, watchdog."""

# ── Standard library ──────────────────────────────────────────────────────────
import os
from typing import Any, Dict
import platform
import re
import socket
import subprocess
from datetime import datetime, timedelta

# ── Third-party ───────────────────────────────────────────────────────────────
import psutil
from flask import Blueprint, jsonify, request

# ── AlvaOS managers ───────────────────────────────────────────────────────────
import health_checks
from common import (
    CMD, PRIV_HELPER, run_sudo_command, build_privileged_cmd, is_root_user,
    _utc_now, _now_iso, _parse_iso,
    _safe_int,
)
from auth_manager import (
    require_auth,
    require_csrf_token,
)
from storage_manager import (
    load_pools_state,
)
from alerts_manager import (
    load_alerts_state,
    save_alerts_state, _collect_system_alerts,
    _build_alert_summary,
    _is_pairing_active,
    _clear_pairing_state, _clear_telegram_chat_binding, _alert_settings_public_payload,
    _telegram_api_call,
    _generate_pairing_code, mark_notification_read,
    mark_notification_dismissed, mark_all_notifications_read, get_notifications_feed,
)

from app_services import (
    VERSION, watchdog_manager, power_ups_manager,
)

bp = Blueprint('system', __name__)

def _io_counters():
    """Bytes moved over the network and to/from disks since boot."""
    info: dict = {'net_bytes_sent': None, 'net_bytes_recv': None,
            'disk_read_bytes': None, 'disk_write_bytes': None}
    try:
        net = psutil.net_io_counters(pernic=True) or {}
        sent = recv = 0
        for name, counters in net.items():
            # Loopback and container bridges are internal traffic, not the LAN.
            if name == 'lo' or name.startswith(('docker', 'veth', 'br-')):
                continue
            sent += counters.bytes_sent
            recv += counters.bytes_recv
        info['net_bytes_sent'] = sent
        info['net_bytes_recv'] = recv
    except Exception:
        pass
    try:
        disk = psutil.disk_io_counters()
        if disk is not None:
            info['disk_read_bytes'] = disk.read_bytes
            info['disk_write_bytes'] = disk.write_bytes
    except Exception:
        pass
    return info


@bp.route('/api/v1/system/info', methods=['GET'])
@require_auth
def get_system_info():
    """Get comprehensive system information"""
    
    # CPU Information
    cpu_freq = psutil.cpu_freq()
    cpu_model = platform.processor() or "Unknown"
    cpu_temp = None
    try:
        if platform.system() == 'Linux' and os.path.exists('/proc/cpuinfo'):
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.lower().startswith('model name'):
                        cpu_model = line.split(':', 1)[1].strip()
                        break
    except Exception:
        pass
    try:
        temps = psutil.sensors_temperatures() if hasattr(psutil, 'sensors_temperatures') else {}
        # Prefer common sensor keys when available
        for key in ('coretemp', 'k10temp', 'cpu-thermal', 'soc_thermal'):
            if key in temps and temps[key]:
                cpu_temp = temps[key][0].current
                break
        if cpu_temp is None:
            # Fallback to first available temperature
            for entries in temps.values():
                if entries:
                    cpu_temp = entries[0].current
                    break
    except Exception:
        pass
    cpu_info = {
        'cores': psutil.cpu_count(logical=False),
        'threads': psutil.cpu_count(logical=True),
        'usage_percent': psutil.cpu_percent(interval=1),
        'frequency_mhz': round(cpu_freq.current, 2) if cpu_freq else 0,
        'model': cpu_model,
        'temperature_c': round(cpu_temp, 1) if isinstance(cpu_temp, (int, float)) else None,
    }
    
    # Memory Information
    mem = psutil.virtual_memory()
    memory_info = {
        'total_gb': round(mem.total / (1024**3), 2),
        'used_gb': round(mem.used / (1024**3), 2),
        'available_gb': round(mem.available / (1024**3), 2),
        'percent': mem.percent,
    }
    
    # Disk Information
    disk = psutil.disk_usage('/')
    disk_info = {
        'total_gb': round(disk.total / (1024**3), 2),
        'used_gb': round(disk.used / (1024**3), 2),
        'free_gb': round(disk.free / (1024**3), 2),
        'percent': disk.percent,
    }

    # Pool-based storage information (for dashboard breakdown)
    pool_storage_info = []
    try:
        pools_state = load_pools_state()
        checks = health_checks.last_results()
        for pool_id, pool_data in pools_state.items():
            mount_point = pool_data.get('mount_point')
            if not mount_point:
                continue

            pool_entry = {
                'id': pool_id,
                'name': pool_data.get('name', pool_id),
                'mount_point': mount_point,
                'mounted': bool(os.path.ismount(mount_point)),
            }
            check = checks.get(str(pool_id))
            if isinstance(check, dict):
                pool_entry['last_check'] = check

            if pool_entry['mounted']:
                try:
                    pool_usage = psutil.disk_usage(mount_point)
                    pool_entry.update({
                        'total_gb': round(pool_usage.total / (1024**3), 2),
                        'used_gb': round(pool_usage.used / (1024**3), 2),
                        'free_gb': round(pool_usage.free / (1024**3), 2),
                        'percent': pool_usage.percent,
                    })
                except Exception as pool_usage_error:
                    pool_entry['error'] = str(pool_usage_error)
            else:
                pool_entry['error'] = 'Pool is not mounted'

            pool_storage_info.append(pool_entry)
    except Exception:
        pass
    
    # Network Information
    hostname = 'unknown'
    try:
        if platform.system() == 'Linux':
            res = subprocess.run([CMD['HOSTNAMECTL'], 'hostname'], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                hostname = res.stdout.strip()
            else:
                hostname = socket.gethostname() or 'unknown'
        else:
            hostname = socket.gethostname() or 'unknown'
    except Exception:
        hostname = 'unknown'
        
    ip_address = '127.0.0.1'
    
    try:
        # Better IP detection: find first non-loopback IPv4
        addrs = psutil.net_if_addrs()
        for iface, iface_addrs in addrs.items():
            if iface.startswith('lo'):
                continue
            for addr in iface_addrs:
                if addr.family == socket.AF_INET:
                    ip_address = addr.address
                    break
            if ip_address != '127.0.0.1':
                break
    except Exception:
        pass
    
    network_info = {
        'hostname': hostname,
        'ip_address': ip_address,
    }

    # Cumulative counters; the dashboard turns two samples into a rate, so the
    # backend never has to sleep or keep state between requests.
    io_info = _io_counters()
    
    # System Information
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime_seconds = (datetime.now() - boot_time).total_seconds()
    
    system_info = {
        'os': platform.system(),
        'os_version': platform.release(),
        'architecture': platform.machine(),
        'python_version': platform.python_version(),
        'uptime_hours': round(uptime_seconds / 3600, 1),
        'boot_time': boot_time.strftime('%Y-%m-%d %H:%M:%S'),
        'effective_user': os.getenv('USER') or os.getenv('USERNAME') or 'unknown',
        'is_root': is_root_user(),
    }
    
    return jsonify({
        'version': VERSION,
        'timestamp': datetime.now().isoformat(),
        'cpu': cpu_info,
        'memory': memory_info,
        'disk': disk_info,
        'storage_pools': pool_storage_info,
        'network': network_info,
        'io': io_info,
        'system': system_info,
    })

@bp.route('/api/v1/alerts', methods=['GET'])
@require_auth
def get_alerts():
    alerts = _collect_system_alerts()
    summary = _build_alert_summary(alerts)
    # Telegram and email are sent by alert_delivery in the background, so they
    # also arrive when nobody has this page open.
    return jsonify({
        'alerts': alerts,
        'summary': summary,
        'generated_at': _now_iso(),
    })

@bp.route('/api/v1/notifications', methods=['GET'])
@require_auth
def api_get_notifications():
    return jsonify(get_notifications_feed())

@bp.route('/api/v1/notifications/<notification_id>/read', methods=['POST'])
@require_auth
def api_mark_notification_read(notification_id):
    found = mark_notification_read(notification_id)
    if not found:
        return jsonify({'error': 'Notification not found'}), 404
    return jsonify({'success': True})

@bp.route('/api/v1/notifications/<notification_id>/dismiss', methods=['POST'])
@require_auth
def api_dismiss_notification(notification_id):
    found = mark_notification_dismissed(notification_id)
    if not found:
        return jsonify({'error': 'Notification not found'}), 404
    return jsonify({'success': True})

@bp.route('/api/v1/notifications/read-all', methods=['POST'])
@require_auth
def api_mark_all_notifications_read():
    count = mark_all_notifications_read()
    return jsonify({'success': True, 'count': count})

@bp.route('/api/v1/alerts/settings', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def alerts_settings():
    state = load_alerts_state()

    if request.method == 'GET':
        return jsonify({
            'success': True,
            'settings': _alert_settings_public_payload(state)
        })

    payload = request.get_json(silent=True) or {}
    telegram_payload = payload.get('telegram')
    if telegram_payload is None:
        telegram_payload = payload
    if not isinstance(telegram_payload, dict):
        return jsonify({'error': 'Invalid payload'}), 400

    telegram = state.get('telegram', {})
    if 'enabled' in telegram_payload:
        telegram['enabled'] = bool(telegram_payload.get('enabled'))

    if 'bot_token' in telegram_payload:
        bot_token = str(telegram_payload.get('bot_token') or '').strip()
        previous_token = str(telegram.get('bot_token') or '').strip()
        telegram['bot_token'] = bot_token

        if not bot_token:
            _clear_telegram_chat_binding(state)
            _clear_pairing_state(state)
            state['delivery']['last_critical_fingerprint'] = ''
        elif previous_token and previous_token != bot_token:
            _clear_telegram_chat_binding(state)
            _clear_pairing_state(state)
            telegram['last_update_id'] = 0
            state['delivery']['last_critical_fingerprint'] = ''

    if bool(telegram_payload.get('clear_pairing')):
        _clear_telegram_chat_binding(state)
        _clear_pairing_state(state)
        state['delivery']['last_critical_fingerprint'] = ''

    state['telegram'] = telegram
    saved = save_alerts_state(state)

    return jsonify({
        'success': True,
        'settings': _alert_settings_public_payload(saved)
    })

@bp.route('/api/v1/alerts/email', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def alerts_email():
    """Email for problems and the weekly report. The password is never sent back."""
    import alert_delivery
    state = alert_delivery.load_state()
    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        if isinstance(payload.get('report'), dict) and 'weekly' in payload['report']:
            state['report']['weekly'] = bool(payload['report']['weekly'])
        if isinstance(payload.get('email'), dict):
            email, problem = alert_delivery.apply_email_settings(state, payload['email'])
            if problem:
                return jsonify({'error': problem}), 400
            state['email'] = email
        state = alert_delivery.save_state(state)
    return jsonify({'success': True, **alert_delivery.public_settings(state)})


@bp.route('/api/v1/alerts/email/test', methods=['POST'])
@require_auth(require_admin=True)
def alerts_email_test():
    """Send a test email with what is typed in the dialog (saved password if none typed)."""
    import alert_delivery
    state = alert_delivery.load_state()
    payload = request.get_json(silent=True) or {}
    email, problem = alert_delivery.apply_email_settings(state, {**(payload.get('email') or {}), 'test': True})
    if problem:
        return jsonify({'error': problem}), 400
    ok, error = alert_delivery.send_email(
        email, 'AlvaOS test email',
        'This is a test from your NAS. If you can read this, AlvaOS can tell you about problems by email.')
    if not ok:
        return jsonify({'error': error}), 502
    return jsonify({'success': True, 'message': f'Sent to {email["recipient"]}. Check your inbox (and the spam folder).'})


@bp.route('/api/v1/alerts/telegram/pairing/start', methods=['POST'])
@require_auth
def start_telegram_pairing():
    state = load_alerts_state()
    token = str(state.get('telegram', {}).get('bot_token') or '').strip()
    if not token:
        return jsonify({'error': 'Configure and save a Telegram bot token first'}), 400

    expires_at = _utc_now() + timedelta(minutes=10)
    code = _generate_pairing_code(6)
    state['pairing'] = {
        'code': code,
        'started_at': _now_iso(),
        'expires_at': expires_at.isoformat(),
    }
    save_alerts_state(state)

    return jsonify({
        'success': True,
        'pairing': _alert_settings_public_payload(state).get('pairing', {}),
        'message': f'Send "/pair {code}" to your bot, then click "Check Pairing".'
    })

@bp.route('/api/v1/alerts/telegram/pairing/check', methods=['POST'])
@require_auth
def check_telegram_pairing():
    state = load_alerts_state()
    telegram = state.get('telegram', {})
    token = str(telegram.get('bot_token') or '').strip()
    if not token:
        return jsonify({'error': 'Telegram bot token is not configured'}), 400

    payload = request.get_json(silent=True) or {}
    expected_code = str(payload.get('code') or state.get('pairing', {}).get('code') or '').strip().upper()
    if not expected_code:
        return jsonify({'error': 'No active pairing code. Generate one first.'}), 400
    if not _is_pairing_active(state):
        _clear_pairing_state(state)
        save_alerts_state(state)
        return jsonify({'error': 'Pairing code expired. Generate a new one.'}), 400

    offset = _safe_int(telegram.get('last_update_id', 0), 0) + 1
    ok, error, updates = _telegram_api_call(
        token,
        'getUpdates',
        payload=None,
        params={
            'offset': offset,
            'limit': 100,
            'timeout': 0
        },
        timeout_sec=12
    )
    if not ok:
        return jsonify({'error': error}), 400

    if not isinstance(updates, list):
        updates = []

    max_update_id = _safe_int(telegram.get('last_update_id', 0), 0)
    started_at = _parse_iso(state.get('pairing', {}).get('started_at'))
    started_ts = int(started_at.timestamp()) if started_at else 0
    matched_chat = None

    for update in updates:
        update_id = _safe_int(update.get('update_id', 0), 0)
        if update_id > max_update_id:
            max_update_id = update_id

        message = update.get('message') or update.get('edited_message') or {}
        text = str(message.get('text') or '').strip()
        if not text:
            continue

        cmd = re.match(r'^/?pair(?:@\w+)?\s+([A-Za-z0-9]+)\s*$', text, flags=re.IGNORECASE)
        if not cmd:
            continue

        sent_code = str(cmd.group(1) or '').strip().upper()
        if sent_code != expected_code:
            continue

        msg_ts = _safe_int(message.get('date', 0), 0)
        if started_ts and msg_ts and msg_ts < (started_ts - 60):
            continue

        chat = message.get('chat') or {}
        chat_id = chat.get('id')
        if chat_id is None:
            continue

        chat_label = (
            str(chat.get('title') or '').strip()
            or str(chat.get('username') or '').strip()
            or (
                f"{str(chat.get('first_name') or '').strip()} {str(chat.get('last_name') or '').strip()}"
            ).strip()
            or str(chat_id)
        )

        matched_chat = {
            'id': str(chat_id),
            'label': chat_label,
        }
        break

    telegram['last_update_id'] = max_update_id
    state['telegram'] = telegram

    if matched_chat:
        state['telegram']['paired_chat_id'] = matched_chat['id']
        state['telegram']['paired_chat_label'] = matched_chat['label']
        state['telegram']['paired_at'] = _now_iso()
        _clear_pairing_state(state)
        save_alerts_state(state)
        return jsonify({
            'success': True,
            'paired': True,
            'settings': _alert_settings_public_payload(state),
            'message': f'Paired successfully with "{matched_chat["label"]}".'
        })

    save_alerts_state(state)
    return jsonify({
        'success': True,
        'paired': False,
        'settings': _alert_settings_public_payload(state),
        'message': 'No matching pairing message found yet. Send the pairing command in Telegram and retry.'
    })

@bp.route('/api/v1/alerts/telegram/test', methods=['POST'])
@require_auth
def test_telegram_alert_delivery():
    state = load_alerts_state()
    telegram = state.get('telegram', {})
    token = str(telegram.get('bot_token') or '').strip()
    chat_id = str(telegram.get('paired_chat_id') or '').strip()
    if not token:
        return jsonify({'error': 'Telegram bot token is not configured'}), 400
    if not chat_id:
        return jsonify({'error': 'Telegram bot is not paired'}), 400

    ok, error, _ = _telegram_api_call(
        token,
        'sendMessage',
        payload={
            'chat_id': chat_id,
            'text': f'AlvaOS test alert\nTime: {_utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")}\nSeverity: critical',
            'disable_web_page_preview': True,
        },
        timeout_sec=12
    )
    if not ok:
        return jsonify({'error': error}), 400

    return jsonify({'success': True, 'message': 'Test alert sent to Telegram'})

@bp.route('/api/v1/alerts/telegram/unpair', methods=['POST'])
@require_auth
def unpair_telegram_alert_delivery():
    state = load_alerts_state()
    _clear_telegram_chat_binding(state)
    _clear_pairing_state(state)
    state['delivery']['last_critical_fingerprint'] = ''
    save_alerts_state(state)
    return jsonify({
        'success': True,
        'settings': _alert_settings_public_payload(state),
        'message': 'Telegram pairing removed'
    })

@bp.route('/api/v1/system/time', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def system_time():
    """Get or Set system time settings"""
    if request.method == 'GET':
        timezone = 'UTC'
        ntp_enabled = True
        
        if platform.system() == 'Linux':
            try:
                # Get current timezone
                tz_result = subprocess.run([CMD['TIMEDATECTL'], 'show', '--property=Timezone', '--value'], 
                                         capture_output=True, text=True)
                if tz_result.returncode == 0:
                    timezone = tz_result.stdout.strip()
                
                # Get NTP status
                ntp_result = subprocess.run([CMD['TIMEDATECTL'], 'show', '--property=NTP', '--value'], 
                                          capture_output=True, text=True)
                if ntp_result.returncode == 0:
                    ntp_enabled = ntp_result.stdout.strip() == 'yes'
            except Exception:
                pass
                
        return jsonify({
            'timezone': timezone,
            'ntp_enabled': ntp_enabled,
            'current_time': datetime.now().isoformat()
        })
    
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if platform.system() == 'Linux':
            try:
                warnings = []
                if 'timezone' in data:
                    res, err = run_sudo_command([CMD['TIMEDATECTL'], 'set-timezone', data['timezone']])
                    if err:
                        raise Exception(err)
                if 'ntp' in data:
                    ntp_val = 'true' if data['ntp'] else 'false'
                    res, err = run_sudo_command([CMD['TIMEDATECTL'], 'set-ntp', ntp_val])
                    if err:
                        warnings.append(err)

                if warnings:
                    return jsonify({
                        'success': True,
                        'message': 'Time settings updated with warnings',
                        'warnings': warnings
                    })

                return jsonify({'success': True, 'message': 'Time settings updated'})
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        else:
            return jsonify({'error': 'Time settings can only be changed on the NAS itself (Linux).'}), 501

@bp.route('/api/v1/system/power', methods=['POST'])
@require_auth(require_admin=True)
@require_csrf_token
def system_power():
    """Handle Shutdown/Reboot"""
    data = request.get_json(silent=True) or {}
    action = str(data.get('action') or '').strip().lower()

    if action not in ['reboot', 'shutdown']:
        return jsonify({'error': 'Invalid action'}), 400

    if platform.system() == 'Linux':
        try:
            cmd_path = CMD['REBOOT'] if action == 'reboot' else CMD['POWEROFF']
            _res, err = run_sudo_command([cmd_path], timeout=15)
            if err:
                return jsonify({'error': f'Failed to {action}: {err}'}), 500
            return jsonify({'success': True, 'message': f'System {action} initiated'})
        except Exception as e:
            return jsonify({'error': f'Failed to {action}: {str(e)}'}), 500
    else:
        return jsonify({'success': False, 'message': f'System {action} not supported on {platform.system()}'}), 400

@bp.route('/api/v1/system/permissions/check', methods=['GET'])
@require_auth(require_admin=True)
def system_permissions_check():
    """Run a focused diagnostics check for backend privileged command execution."""
    report: Dict[str, Any] = {
        'success': True,
        'platform': platform.system(),
        'sudoers': {},
        'sudo_rules': {},
        'privileged_commands': [],
        'errors': [],
    }

    sudoers_path = '/etc/sudoers.d/alvaos'
    sudoers_info: Dict[str, Any] = {
        'path': sudoers_path,
        'exists': False,
        'owner_uid': None,
        'mode_octal': None,
        'valid_owner': False,
        'valid_mode': False,
    }
    try:
        if os.path.exists(sudoers_path):
            st = os.stat(sudoers_path)
            mode = st.st_mode & 0o777
            sudoers_info.update({
                'exists': True,
                'owner_uid': int(st.st_uid),
                'mode_octal': oct(mode),
                'valid_owner': int(st.st_uid) == 0,
                'valid_mode': mode == 0o440,
            })
    except Exception as e:
        report['errors'].append(f'Failed reading sudoers metadata: {e}')
    report['sudoers'] = sudoers_info

    if platform.system() != 'Linux':
        report['success'] = False
        report['errors'].append('Permission diagnostics are only supported on Linux.')
        return jsonify(report), 400

    sudo_list: Dict[str, Any] = {'ok': False, 'required_rules': {}, 'error': ''}
    # The backend's only sudo rule is the privilege helper; everything else is
    # decided by priv_policy.py inside the helper.
    required_rules = [PRIV_HELPER]
    try:
        sudo_probe = subprocess.run(
            ['sudo', '-n', '-l'],
            capture_output=True,
            text=True,
            timeout=8,
            env={'LC_ALL': 'C'}
        )
        sudo_output = f"{sudo_probe.stdout or ''}\n{sudo_probe.stderr or ''}".strip()
        if sudo_probe.returncode == 0:
            sudo_list['ok'] = True
            for rule in required_rules:
                sudo_list['required_rules'][rule] = (rule in sudo_output)
        else:
            sudo_list['error'] = sudo_output or f"sudo -n -l failed (exit {sudo_probe.returncode})"
            for rule in required_rules:
                sudo_list['required_rules'][rule] = False
    except Exception as e:
        sudo_list['error'] = str(e)
        for rule in required_rules:
            sudo_list['required_rules'][rule] = False
    report['sudo_rules'] = sudo_list

    command_checks = [
        {'name': 'timedatectl', 'cmd': [CMD['TIMEDATECTL'], 'show', '--property=Timezone', '--value']},
        {'name': 'lsblk', 'cmd': [CMD['LSBLK'], '-dn', '-o', 'NAME']},
        {'name': 'df', 'cmd': [CMD['DF'], '-h', '/']},
        {'name': 'mountpoint', 'cmd': [CMD['MOUNTPOINT'], '/']},
    ]

    for item in command_checks:
        res, err = run_sudo_command(item['cmd'], timeout=10)
        check = {
            'name': item['name'],
            'command': ' '.join(item['cmd']),
            'ok': (err is None and res is not None and res.returncode == 0),
            'error': err,
        }
        report['privileged_commands'].append(check)

    if not sudoers_info.get('exists'):
        report['errors'].append('Missing /etc/sudoers.d/alvaos')
    if sudoers_info.get('exists') and not sudoers_info.get('valid_owner'):
        report['errors'].append('Invalid sudoers owner (expected root:root)')
    if sudoers_info.get('exists') and not sudoers_info.get('valid_mode'):
        report['errors'].append('Invalid sudoers mode (expected 440)')
    if not sudo_list.get('ok'):
        report['errors'].append('sudo -n -l failed')
    for rule, ok in (sudo_list.get('required_rules') or {}).items():
        if not ok:
            report['errors'].append(f'Missing sudo rule: {rule}')
    for check in report['privileged_commands']:
        if not check.get('ok'):
            report['errors'].append(f"Command check failed: {check.get('name')}")

    report['success'] = len(report['errors']) == 0
    status_code = 200 if report['success'] else 500
    return jsonify(report), status_code

@bp.route('/api/v1/system/power/ups', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def system_power_ups():
    """Get or update battery UPS behavior (charge limit + low-battery shutdown)."""
    if request.method == 'GET':
        status = power_ups_manager.run_monitor_check()
        return jsonify({
            'success': True,
            'settings': power_ups_manager.get_settings(),
            'status': status,
        })

    data = request.get_json(silent=True) or {}
    payload: Dict[str, Any] = {}

    if 'enabled' in data:
        payload['enabled'] = bool(data.get('enabled'))

    if 'charge_limit_percent' in data:
        try:
            payload['charge_limit_percent'] = int(data['charge_limit_percent'])
        except Exception:
            return jsonify({'error': 'charge_limit_percent must be an integer'}), 400

    if 'shutdown_percent' in data:
        try:
            payload['shutdown_percent'] = int(data['shutdown_percent'])
        except Exception:
            return jsonify({'error': 'shutdown_percent must be an integer'}), 400

    if 'monitor_interval_seconds' in data:
        try:
            payload['monitor_interval_seconds'] = int(data['monitor_interval_seconds'])
        except Exception:
            return jsonify({'error': 'monitor_interval_seconds must be an integer'}), 400

    preview = power_ups_manager.get_settings().copy()
    preview.update(payload)
    if int(preview.get('shutdown_percent', 20)) >= int(preview.get('charge_limit_percent', 80)):
        return jsonify({'error': 'shutdown_percent must be lower than charge_limit_percent'}), 400

    saved = power_ups_manager.save_settings(payload)
    status = power_ups_manager.run_monitor_check()
    return jsonify({
        'success': True,
        'settings': saved,
        'status': status,
    })

@bp.route('/api/v1/system/network', methods=['GET'])
@require_auth
def get_network_details():
    """Get detailed network configuration"""
    hostname = "unknown"
    try:
        if platform.system() == 'Linux':
            res = subprocess.run([CMD['HOSTNAMECTL'], 'hostname'], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                hostname = res.stdout.strip()
            else:
                hostname = socket.gethostname() or "unknown"
        else:
            hostname = socket.gethostname() or "unknown"
    except Exception:
        pass
        
    ip_address = "127.0.0.1"
    interface = "lo"
    subnet_mask = "255.255.255.0"
    gateway = "N/A"
    dns_servers = []
    
    # Try to get real network information
    try:
        # Get all network interfaces
        import psutil
        net_if_addrs = psutil.net_if_addrs()
        
        # Find the first non-loopback interface with an IPv4 address
        for iface_name, iface_addresses in net_if_addrs.items():
            if iface_name.startswith(('lo', 'docker', 'veth', 'br-')):
                continue
            
            for addr in iface_addresses:
                if addr.family == socket.AF_INET:  # IPv4
                    ip_address = addr.address
                    interface = iface_name
                    if addr.netmask:
                        subnet_mask = addr.netmask
                    break
            
            if ip_address != "127.0.0.1":
                break
        
        # Try to get gateway on Linux
        if platform.system() == 'Linux':
            try:
                result = subprocess.run([CMD['IP'], 'route', 'show', 'default'], 
                                      capture_output=True, text=True, timeout=2)
                if result.returncode == 0 and result.stdout:
                    parts = result.stdout.split()
                    if len(parts) >= 3 and parts[0] == 'default':
                        gateway = parts[2]
            except Exception:
                pass
            
            # Try to get DNS servers
            try:
                if os.path.exists('/etc/resolv.conf'):
                    with open('/etc/resolv.conf', 'r') as f:
                        for line in f:
                            if line.strip().startswith('nameserver'):
                                dns = line.split()[1]
                                if dns not in dns_servers:
                                    dns_servers.append(dns)
            except Exception:
                pass
        
        # Fallback DNS if none found
        if not dns_servers:
            dns_servers = ['1.1.1.1', '8.8.8.8']
            
    except Exception as e:
        print(f"Error getting network details: {e}")
        # Use fallback values

    return jsonify({
        'interface': interface,
        'hostname': hostname,
        'ip_address': ip_address,
        'subnet_mask': subnet_mask,
        'gateway': gateway,
        'dns': dns_servers
    })

SSH_CONFIG_FILE = '/etc/ssh/sshd_config.d/00-alvaos-security.conf'


def _read_ssh_access_state():
    """Return the currently configured SSH access state."""
    state = {'enabled': False, 'permit_root_login': False, 'available': True}
    if platform.system() != 'Linux':
        state['available'] = False
        return state
    try:
        res, err = run_sudo_command([CMD['CAT'], SSH_CONFIG_FILE])
        if err or not res or res.returncode != 0:
            return state
        for raw in (res.stdout or '').splitlines():
            line = raw.strip().lower()
            if line.startswith('permitrootlogin'):
                state['permit_root_login'] = line.split()[-1] == 'yes'
        # Root login is the only SSH account AlvaOS manages, so it is the switch.
        state['enabled'] = state['permit_root_login']
    except Exception as e:
        print(f"Error reading SSH config: {e}")
    return state


@bp.route('/api/v1/system/ssh', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def system_ssh_access():
    """Read or change SSH remote access. Disabled by default; opt-in only."""
    if request.method == 'GET':
        return jsonify(_read_ssh_access_state())

    data = request.get_json() or {}
    if 'enabled' not in data:
        return jsonify({'error': 'Field "enabled" is required'}), 400
    enabled = bool(data['enabled'])

    if platform.system() != 'Linux':
        return jsonify({'error': 'SSH access can only be changed on the NAS itself'}), 400

    if enabled:
        config = (
            '# AlvaOS Security Configuration\n'
            '# Managed by AlvaOS - System > Remote Access\n'
            'PermitRootLogin yes\n'
            'PasswordAuthentication yes\n'
            'PermitEmptyPasswords no\n'
        )
    else:
        config = (
            '# AlvaOS Security Configuration\n'
            '# Managed by AlvaOS - System > Remote Access\n'
            'PermitRootLogin no\n'
            'PasswordAuthentication yes\n'
            'PermitEmptyPasswords no\n'
        )

    try:
        process = subprocess.Popen(
            build_privileged_cmd([CMD['TEE'], SSH_CONFIG_FILE]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={'LC_ALL': 'C'}
        )
        _, stderr = process.communicate(input=config, timeout=10)
        if process.returncode != 0:
            return jsonify({'error': 'Could not update the SSH configuration.',
                            'detail': (stderr or '').strip()}), 500
    except Exception as e:
        return jsonify({'error': f'Could not update the SSH configuration: {e}'}), 500

    res, err = run_sudo_command([CMD['SYSTEMCTL'], 'restart', 'ssh'])
    if err:
        return jsonify({'error': 'SSH configuration saved, but the SSH service '
                                 'could not be restarted.', 'detail': err}), 500

    return jsonify({'success': True, **_read_ssh_access_state()})


@bp.route('/api/v1/system/hostname', methods=['PUT'])
@require_auth(require_admin=True)
def set_hostname():
    """Set system hostname"""
    data = request.get_json()
    if not data or 'hostname' not in data:
        return jsonify({'error': 'Hostname required'}), 400
    
    new_hostname = data['hostname']
    
    # Validation: allow letters, digits, hyphens, and dots (for FQDNs like nas.home.local)
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-.]*[a-zA-Z0-9])?$', new_hostname) or '..' in new_hostname or len(new_hostname) > 253:
        return jsonify({'error': 'Invalid hostname format'}), 400

    if platform.system() == 'Linux':
        # 0. Capture old hostname BEFORE changing it
        old_hostname = socket.gethostname()
        
        # 1. Update hostname via hostnamectl
        res, err = run_sudo_command([CMD['HOSTNAMECTL'], 'set-hostname', new_hostname])
        if err:
             return jsonify({'error': f'Failed to set hostname: {err}'}), 500
             
        # 2. Update /etc/hosts to prevent "unable to resolve host" errors
        try:
            hosts_file = '/etc/hosts'
            
            # Read current hosts file
            res, err = run_sudo_command([CMD['CAT'], hosts_file])
            if res and res.returncode == 0:
                content = res.stdout
                
                # More robust replacement
                lines = content.splitlines()
                new_lines = []
                found_local_ip = False
                
                for line in lines:
                    if line.strip().startswith('127.0.1.1'):
                        new_lines.append(f'127.0.1.1\t{new_hostname}')
                        found_local_ip = True
                    else:
                        new_lines.append(line.replace(old_hostname, new_hostname))
                
                if not found_local_ip:
                    new_lines.append(f'127.0.1.1\t{new_hostname}')
                
                new_content = "\n".join(new_lines) + "\n"
                
                # Write back with tee
                process = subprocess.Popen(
                    build_privileged_cmd([CMD['TEE'], hosts_file]), 
                    stdin=subprocess.PIPE, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    text=True,
                    env={'LC_ALL': 'C'}
                )
                process.communicate(input=new_content)
        except Exception as e:
            print(f"Warning: Failed to update /etc/hosts: {e}")
    else:
        print(f"SIMULATION: Setting hostname to {new_hostname}")

    return jsonify({'success': True, 'hostname': new_hostname})

@bp.route('/api/v1/system/logs', methods=['GET'])
@require_auth
def get_system_logs():
    """Get system logs"""
    logs = []
    
    try:
        # Strategy 1: Read syslog file (traditional Linux)
        log_file = '/var/log/syslog'
        if os.path.exists(log_file):
            try:
                cmd = [CMD['TAIL'], '-n', '50', log_file]
                res, err = run_sudo_command(cmd)
                if res and res.returncode == 0:
                    logs = res.stdout.splitlines()
                    return jsonify({'logs': logs})
            except Exception as e:
                print(f"Reading syslog failed: {e}")
                
        # Strategy 2: Use journalctl (systemd systems)
        try:
            cmd = [CMD['JOURNALCTL'], '-n', '50', '--no-pager', '--output=short']
            res, err = run_sudo_command(cmd)
            if res and res.returncode == 0:
                logs = res.stdout.splitlines()
                return jsonify({'logs': logs})
        except Exception:
            pass
            
        logs = [f"[{datetime.now().isoformat()}] Could not read system logs (no syslog or journal available)"]

    except Exception as e:
        logs = [f"Error fetching logs: {str(e)}"]

    return jsonify({'logs': logs})

# ============================================================================
# UPDATE MANAGEMENT ENDPOINTS (v0.3.0)
# ============================================================================


# ── Watchdog Routes ──────────────────────────────────────────────────────────

@bp.route('/api/v1/watchdog/status', methods=['GET'])
@require_auth
def get_watchdog_status():
    """Return live service status and recent recovery log."""
    try:
        status = watchdog_manager.get_status()
        return jsonify({'success': True, **status})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/v1/watchdog/check', methods=['POST'])
@require_auth(require_admin=True)
def run_watchdog_check():
    """Trigger a manual health check with auto-restart for failing services."""
    try:
        result = watchdog_manager.run_check()
        return jsonify({'success': True, **result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
