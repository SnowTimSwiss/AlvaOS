#!/usr/bin/env python3
"""
AlvaOS Alerts Manager
System alert collection, Telegram notifications, and alert state management.
"""

import hashlib
import json
import os
import secrets
import threading
import uuid

import psutil
import requests

from common import _utc_now, _now_iso, _parse_iso, _safe_int, _read_cpu_temperature_c, ensure_directories

# ── State file ────────────────────────────────────────────────────────────────
ALERTS_STATE_FILE = '/var/lib/alvaos/alerts.json'
NOTIFICATIONS_STATE_FILE = '/var/lib/alvaos/notifications.json'
NOTIFICATIONS_MAX_ENTRIES = 200
# Old notifications expire so the bell shows what matters now, not a backlog.
# Read or dismissed entries go after a week, everything else after a month.
NOTIFICATIONS_READ_TTL_DAYS = 7
NOTIFICATIONS_MAX_AGE_DAYS = 30
_notifications_lock = threading.Lock()

# ── Thresholds ────────────────────────────────────────────────────────────────
ALERT_THRESHOLDS = {
    'cpu_usage_warning': 85.0,
    'cpu_usage_critical': 95.0,
    'cpu_temp_warning': 75.0,
    'cpu_temp_critical': 85.0,
    'memory_warning': 85.0,
    'memory_critical': 93.0,
    'disk_warning': 90.0,
    'disk_critical': 95.0,
    'pool_warning': 85.0,
    'pool_critical': 95.0,
}

ALERT_SEVERITY_PRIORITY = {
    'critical': 0,
    'warning': 1,
    'info': 2,
}

DEFAULT_ALERTS_STATE = {
    'telegram': {
        'enabled': False,
        'bot_token': '',
        'paired_chat_id': '',
        'paired_chat_label': '',
        'paired_at': '',
        'last_update_id': 0,
    },
    'pairing': {
        'code': '',
        'started_at': '',
        'expires_at': '',
    },
    'delivery': {
        'last_critical_fingerprint': '',
        'last_critical_sent_at': '',
    }
}


# ── State helpers ─────────────────────────────────────────────────────────────

def _build_default_alerts_state():
    return json.loads(json.dumps(DEFAULT_ALERTS_STATE))


def _normalize_alerts_state(payload):
    defaults = _build_default_alerts_state()
    if not isinstance(payload, dict):
        return defaults

    merged = defaults
    telegram = payload.get('telegram')
    if isinstance(telegram, dict):
        merged['telegram'].update(telegram)
    pairing = payload.get('pairing')
    if isinstance(pairing, dict):
        merged['pairing'].update(pairing)
    delivery = payload.get('delivery')
    if isinstance(delivery, dict):
        merged['delivery'].update(delivery)

    merged['telegram']['enabled'] = bool(merged['telegram'].get('enabled', False))
    merged['telegram']['bot_token'] = str(merged['telegram'].get('bot_token', '')).strip()
    merged['telegram']['paired_chat_id'] = str(merged['telegram'].get('paired_chat_id', '')).strip()
    merged['telegram']['paired_chat_label'] = str(merged['telegram'].get('paired_chat_label', '')).strip()
    merged['telegram']['paired_at'] = str(merged['telegram'].get('paired_at', '')).strip()
    merged['telegram']['last_update_id'] = _safe_int(merged['telegram'].get('last_update_id', 0), 0)

    merged['pairing']['code'] = str(merged['pairing'].get('code', '')).strip().upper()
    merged['pairing']['started_at'] = str(merged['pairing'].get('started_at', '')).strip()
    merged['pairing']['expires_at'] = str(merged['pairing'].get('expires_at', '')).strip()

    merged['delivery']['last_critical_fingerprint'] = str(merged['delivery'].get('last_critical_fingerprint', '')).strip()
    merged['delivery']['last_critical_sent_at'] = str(merged['delivery'].get('last_critical_sent_at', '')).strip()

    return merged


def load_alerts_state():
    try:
        if os.path.exists(ALERTS_STATE_FILE):
            with open(ALERTS_STATE_FILE, 'r') as f:
                loaded = json.load(f)
                normalized = _normalize_alerts_state(loaded)
                if normalized != loaded:
                    save_alerts_state(normalized)
                return normalized
    except Exception as e:
        print(f"Error loading alerts state: {e}")
    return _build_default_alerts_state()


def save_alerts_state(state):
    try:
        ensure_directories()
        normalized = _normalize_alerts_state(state)
        with open(ALERTS_STATE_FILE, 'w') as f:
            json.dump(normalized, f, indent=2)
        return normalized
    except Exception as e:
        print(f"Error saving alerts state: {e}")
        return _normalize_alerts_state(state)


def _clear_pairing_state(state):
    state['pairing'] = {
        'code': '',
        'started_at': '',
        'expires_at': '',
    }


def _clear_telegram_chat_binding(state):
    state['telegram']['paired_chat_id'] = ''
    state['telegram']['paired_chat_label'] = ''
    state['telegram']['paired_at'] = ''


def _is_pairing_active(state):
    pairing = state.get('pairing', {})
    code = str(pairing.get('code', '')).strip()
    expires_at = _parse_iso(pairing.get('expires_at'))
    if not code or not expires_at:
        return False
    return expires_at > _utc_now()


def _alert_settings_public_payload(state):
    telegram = state.get('telegram', {})
    pairing = state.get('pairing', {})
    pairing_active = _is_pairing_active(state)
    pairing_code = str(pairing.get('code', '')).strip() if pairing_active else ''
    paired_chat = str(telegram.get('paired_chat_label') or telegram.get('paired_chat_id') or '').strip()

    return {
        'telegram': {
            'enabled': bool(telegram.get('enabled')),
            'bot_token_configured': bool(str(telegram.get('bot_token', '')).strip()),
            'paired': bool(str(telegram.get('paired_chat_id', '')).strip()),
            'paired_chat': paired_chat,
            'paired_at': str(telegram.get('paired_at') or '').strip() or None,
        },
        'pairing': {
            'active': pairing_active,
            'code': pairing_code,
            'command': f"/pair {pairing_code}" if pairing_code else '',
            'expires_at': str(pairing.get('expires_at') or '').strip() or None,
        }
    }


# ── Telegram ──────────────────────────────────────────────────────────────────

def _telegram_api_call(bot_token, method, payload=None, params=None, timeout_sec=10):
    token = str(bot_token or '').strip()
    if not token:
        return False, 'Telegram bot token is not configured', None

    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if payload is not None:
            response = requests.post(url, json=payload, timeout=timeout_sec)
        else:
            response = requests.get(url, params=params, timeout=timeout_sec)
    except Exception as e:
        return False, f'Telegram request failed: {e}', None

    try:
        data = response.json()
    except Exception:
        data = {}

    if response.status_code != 200 or not isinstance(data, dict) or not data.get('ok'):
        description = ''
        if isinstance(data, dict):
            description = str(data.get('description') or '').strip()
        detail = description or f'HTTP {response.status_code}'
        return False, f'Telegram API error: {detail}', data

    return True, '', data.get('result')


def _generate_pairing_code(length=6):
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


# ── Alert collection ──────────────────────────────────────────────────────────

def _build_alert_item(alert_id, severity, title, message, route='', action_label='Open'):
    return {
        'id': alert_id,
        'severity': severity,
        'title': title,
        'message': message,
        'route': route,
        'action_label': action_label if route else '',
        'created_at': _now_iso(),
    }


def _collect_system_alerts():
    # Import here to avoid circular dependency at module load time
    from storage_manager import load_pools_state, detect_btrfs_pools

    alerts = []

    try:
        cpu_usage = float(psutil.cpu_percent(interval=0.15))
        if cpu_usage >= ALERT_THRESHOLDS['cpu_usage_critical']:
            alerts.append(_build_alert_item(
                alert_id='cpu-usage-critical',
                severity='critical',
                title='CPU usage is critical',
                message=f'CPU load is at {cpu_usage:.1f}%.',
                route='index.html',
                action_label='Open Dashboard'
            ))
        elif cpu_usage >= ALERT_THRESHOLDS['cpu_usage_warning']:
            alerts.append(_build_alert_item(
                alert_id='cpu-usage-warning',
                severity='warning',
                title='CPU usage is high',
                message=f'CPU load is at {cpu_usage:.1f}%.',
                route='index.html',
                action_label='Open Dashboard'
            ))
    except Exception:
        pass

    cpu_temp_c = _read_cpu_temperature_c()
    if isinstance(cpu_temp_c, (int, float)):
        if cpu_temp_c >= ALERT_THRESHOLDS['cpu_temp_critical']:
            alerts.append(_build_alert_item(
                alert_id='cpu-temp-critical',
                severity='critical',
                title='CPU temperature is critical',
                message=f'CPU temperature reached {cpu_temp_c:.1f} degC.',
                route='index.html',
                action_label='Open Dashboard'
            ))
        elif cpu_temp_c >= ALERT_THRESHOLDS['cpu_temp_warning']:
            alerts.append(_build_alert_item(
                alert_id='cpu-temp-warning',
                severity='warning',
                title='CPU temperature is elevated',
                message=f'CPU temperature is {cpu_temp_c:.1f} degC.',
                route='index.html',
                action_label='Open Dashboard'
            ))

    try:
        memory_percent = float(psutil.virtual_memory().percent)
        if memory_percent >= ALERT_THRESHOLDS['memory_critical']:
            alerts.append(_build_alert_item(
                alert_id='memory-critical',
                severity='critical',
                title='Memory pressure is critical',
                message=f'RAM usage is at {memory_percent:.1f}%.',
                route='index.html',
                action_label='Open Dashboard'
            ))
        elif memory_percent >= ALERT_THRESHOLDS['memory_warning']:
            alerts.append(_build_alert_item(
                alert_id='memory-warning',
                severity='warning',
                title='Memory usage is high',
                message=f'RAM usage is at {memory_percent:.1f}%.',
                route='index.html',
                action_label='Open Dashboard'
            ))
    except Exception:
        pass

    try:
        root_disk_percent = float(psutil.disk_usage('/').percent)
        if root_disk_percent >= ALERT_THRESHOLDS['disk_critical']:
            alerts.append(_build_alert_item(
                alert_id='root-disk-critical',
                severity='critical',
                title='Root filesystem is almost full',
                message=f'Root filesystem usage is at {root_disk_percent:.1f}%.',
                route='storage.html',
                action_label='Open Storage'
            ))
        elif root_disk_percent >= ALERT_THRESHOLDS['disk_warning']:
            alerts.append(_build_alert_item(
                alert_id='root-disk-warning',
                severity='warning',
                title='Root filesystem usage is high',
                message=f'Root filesystem usage is at {root_disk_percent:.1f}%.',
                route='storage.html',
                action_label='Open Storage'
            ))
    except Exception:
        pass

    try:
        live_pools, _ = detect_btrfs_pools()
        pools_state_for_names = load_pools_state()
        for live_pool in live_pools:
            if live_pool.get('status') != 'degraded':
                continue
            pool_id = str(live_pool.get('id', ''))
            state_entry = pools_state_for_names.get(pool_id) if isinstance(pools_state_for_names, dict) else None
            pool_name = str((state_entry or {}).get('name') or live_pool.get('name') or pool_id)
            alerts.append(_build_alert_item(
                alert_id=f'pool-{pool_id}-degraded',
                severity='critical',
                title='Storage pool is degraded',
                message=f'Pool "{pool_name}" is missing one or more disks. Replace the failed disk immediately.',
                route='storage.html',
                action_label='Open Storage'
            ))
            push_notification(
                severity='critical',
                title='Storage pool is degraded',
                message=f'Pool "{pool_name}" is missing one or more disks. Replace the failed disk immediately.',
                source='storage',
                dismissible=True,
                link='storage.html',
                fingerprint=f'pool-degraded-{pool_id}',
            )
    except Exception:
        pass

    try:
        pools_state = load_pools_state()
        if isinstance(pools_state, dict):
            for pool_id, pool_data in pools_state.items():
                mount_point = str((pool_data or {}).get('mount_point') or '').strip()
                if not mount_point:
                    continue
                pool_name = str((pool_data or {}).get('name') or pool_id)
                if not os.path.ismount(mount_point):
                    alerts.append(_build_alert_item(
                        alert_id=f'pool-{pool_id}-unmounted',
                        severity='critical',
                        title='Pool is not mounted',
                        message=f'Pool "{pool_name}" is configured but not mounted.',
                        route='storage.html',
                        action_label='Open Storage'
                    ))
                    continue
                try:
                    pool_percent = float(psutil.disk_usage(mount_point).percent)
                    if pool_percent >= ALERT_THRESHOLDS['pool_critical']:
                        alerts.append(_build_alert_item(
                            alert_id=f'pool-{pool_id}-critical',
                            severity='critical',
                            title='Pool capacity is critical',
                            message=f'Pool "{pool_name}" is at {pool_percent:.1f}% usage.',
                            route='storage.html',
                            action_label='Open Storage'
                        ))
                    elif pool_percent >= ALERT_THRESHOLDS['pool_warning']:
                        alerts.append(_build_alert_item(
                            alert_id=f'pool-{pool_id}-warning',
                            severity='warning',
                            title='Pool capacity is high',
                            message=f'Pool "{pool_name}" is at {pool_percent:.1f}% usage.',
                            route='storage.html',
                            action_label='Open Storage'
                        ))
                except Exception:
                    pass
    except Exception:
        pass

    # Results of the last data check per pool (recorded by health_checks; no
    # btrfs call here, so the alert poll stays cheap).
    try:
        import health_checks
        pools_state = load_pools_state() if isinstance(load_pools_state(), dict) else {}
        for pool_id, result in health_checks.last_results().items():
            if pool_id not in pools_state or not isinstance(result, dict):
                continue
            pool_name = str((pools_state.get(pool_id) or {}).get('name') or pool_id)
            uncorrectable = result.get('uncorrectable') or 0
            errors = result.get('errors') or 0
            if uncorrectable:
                alerts.append(_build_alert_item(
                    alert_id=f'pool-{pool_id}-scrub-damaged',
                    severity='critical',
                    title='The data check found damaged files',
                    message=(f'{uncorrectable} block(s) in "{pool_name}" could not be repaired. '
                             'Restore the affected files from a backup and look at the disks.'),
                    route=f'storage.html#pool={pool_id}',
                    action_label='Open pool'
                ))
            elif errors:
                alerts.append(_build_alert_item(
                    alert_id=f'pool-{pool_id}-scrub-repaired',
                    severity='warning',
                    title='A disk returned bad data',
                    message=(f'The data check repaired {errors} problem(s) in "{pool_name}" from the '
                             'other copy. This often means a disk is wearing out.'),
                    route=f'storage.html#pool={pool_id}',
                    action_label='Open pool'
                ))
    except Exception:
        pass

    alerts.sort(key=lambda item: (
        ALERT_SEVERITY_PRIORITY.get(str(item.get('severity', 'info')).lower(), 9),
        str(item.get('title', ''))
    ))
    return alerts


def _build_alert_summary(alerts):
    summary = {'critical': 0, 'warning': 0, 'info': 0, 'total': 0}
    for item in alerts:
        sev = str(item.get('severity', 'info')).lower()
        if sev not in summary:
            continue
        summary[sev] += 1
        summary['total'] += 1
    return summary


def _critical_fingerprint(alerts):
    critical = [
        f"{item.get('id', '')}:{item.get('message', '')}"
        for item in alerts
        if str(item.get('severity', '')).lower() == 'critical'
    ]
    if not critical:
        return ''
    digest_input = '|'.join(sorted(critical))
    return hashlib.sha256(digest_input.encode('utf-8')).hexdigest()


def _maybe_send_telegram_critical_alerts(alerts, state):
    telegram = state.get('telegram', {})
    delivery = state.get('delivery', {})
    token = str(telegram.get('bot_token', '')).strip()
    chat_id = str(telegram.get('paired_chat_id', '')).strip()
    enabled = bool(telegram.get('enabled', False))
    if not enabled or not token or not chat_id:
        return

    fingerprint = _critical_fingerprint(alerts)
    if not fingerprint:
        if delivery.get('last_critical_fingerprint'):
            state['delivery']['last_critical_fingerprint'] = ''
            save_alerts_state(state)
        return

    if fingerprint == str(delivery.get('last_critical_fingerprint', '')).strip():
        return

    critical_alerts = [item for item in alerts if str(item.get('severity', '')).lower() == 'critical']
    if not critical_alerts:
        return

    lines = ['AlvaOS critical alerts detected:']
    for item in critical_alerts[:8]:
        lines.append(f"- {item.get('title')}: {item.get('message')}")
    if len(critical_alerts) > 8:
        lines.append(f"- ... and {len(critical_alerts) - 8} more")
    lines.append('')
    lines.append(f"Generated at {_utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}")

    ok, error, _result = _telegram_api_call(
        token,
        'sendMessage',
        payload={
            'chat_id': chat_id,
            'text': '\n'.join(lines),
            'disable_web_page_preview': True,
        },
        timeout_sec=12
    )
    if not ok:
        print(f"Telegram critical alert delivery failed: {error}")
        return

    state['delivery']['last_critical_fingerprint'] = fingerprint
    state['delivery']['last_critical_sent_at'] = _now_iso()
    save_alerts_state(state)


# ── Notification feed ──────────────────────────────────────────────────────────

def _load_notifications_raw():
    try:
        if os.path.exists(NOTIFICATIONS_STATE_FILE):
            with open(NOTIFICATIONS_STATE_FILE, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception as e:
        print(f"Error loading notifications: {e}")
    return []


def _save_notifications_raw(notifications):
    try:
        ensure_directories()
        with open(NOTIFICATIONS_STATE_FILE, 'w') as f:
            json.dump(notifications, f, indent=2)
    except Exception as e:
        print(f"Error saving notifications: {e}")


def _prune_expired(notifications, now=None):
    """Drop entries that are too old to matter. Entries without a readable
    timestamp are kept, so a malformed file never silently loses alerts."""
    now = now or _utc_now()
    kept = []
    for entry in notifications:
        if not isinstance(entry, dict):
            continue
        ts = _parse_iso(entry.get('ts'))
        if ts is None:
            kept.append(entry)
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=now.tzinfo)
        age_days = (now - ts).total_seconds() / 86400
        if age_days > NOTIFICATIONS_MAX_AGE_DAYS:
            continue
        if (entry.get('read') or entry.get('dismissed')) and age_days > NOTIFICATIONS_READ_TTL_DAYS:
            continue
        kept.append(entry)
    return kept


def load_notifications():
    with _notifications_lock:
        notifications = _load_notifications_raw()
        pruned = _prune_expired(notifications)
        if len(pruned) != len(notifications):
            _save_notifications_raw(pruned)
        return pruned


def push_notification(severity, title, message, source='system', dismissible=True, link=None, fingerprint=None):
    """Create a persisted notification feed entry. Returns the created entry.

    `fingerprint`, if given, dedupes against the most recent unread entry with the
    same fingerprint so repeated polling (e.g. degraded pool checks) does not spam
    the feed every refresh cycle.
    """
    severity = str(severity or 'info').lower()
    if severity not in ALERT_SEVERITY_PRIORITY:
        severity = 'info'

    with _notifications_lock:
        notifications = _prune_expired(_load_notifications_raw())

        if fingerprint:
            for existing in notifications:
                if existing.get('fingerprint') == fingerprint and not existing.get('dismissed'):
                    return existing

        entry = {
            'id': uuid.uuid4().hex,
            'ts': _now_iso(),
            'severity': severity,
            'title': str(title or ''),
            'message': str(message or ''),
            'source': str(source or 'system'),
            'read': False,
            'dismissed': False,
            'dismissible': bool(dismissible),
            'link': link,
            'fingerprint': fingerprint,
        }
        notifications.insert(0, entry)
        notifications = notifications[:NOTIFICATIONS_MAX_ENTRIES]
        _save_notifications_raw(notifications)
        return entry


def mark_notification_read(notification_id):
    with _notifications_lock:
        notifications = _load_notifications_raw()
        found = False
        for entry in notifications:
            if entry.get('id') == notification_id:
                entry['read'] = True
                found = True
                break
        if found:
            _save_notifications_raw(notifications)
        return found


def mark_notification_dismissed(notification_id):
    with _notifications_lock:
        notifications = _load_notifications_raw()
        found = False
        for entry in notifications:
            if entry.get('id') == notification_id:
                entry['read'] = True
                entry['dismissed'] = True
                found = True
                break
        if found:
            _save_notifications_raw(notifications)
        return found


def mark_all_notifications_read():
    with _notifications_lock:
        notifications = _load_notifications_raw()
        for entry in notifications:
            entry['read'] = True
        _save_notifications_raw(notifications)
        return len(notifications)


def get_notifications_feed(limit=200):
    notifications = load_notifications()
    visible = [n for n in notifications if not n.get('dismissed')]
    unread_count = sum(1 for n in visible if not n.get('read'))
    return {
        'notifications': visible[:limit],
        'unread_count': unread_count,
    }
