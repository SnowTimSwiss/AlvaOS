#!/usr/bin/env python3
"""Telling people about problems when nobody has the web page open.

Every few minutes the alerts are collected in the background. A problem that
is still there on the next check (so a short spike does not wake anyone) is
sent once to every channel that is set up: Telegram and email. When it is
gone and comes back, it is sent again. Once a week an optional report says
that all is well, or what needs attention.
"""

import json
import os
import re
import smtplib
import ssl
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional, Tuple

from common import _now_iso, ensure_directories

DELIVERY_FILE = '/var/lib/alvaos/alert_delivery.json'
CHECK_INTERVAL_SECONDS = 300
# Load and memory go up and down all day; they stay on the dashboard only.
NOT_SENT_PREFIXES = ('cpu-usage', 'memory-')
REPORT_WEEKDAY = 6          # Sunday
REPORT_HOUR = 10            # local time

EMAIL_PRESETS: Dict[str, Dict[str, Any]] = {
    'gmail': {'host': 'smtp.gmail.com', 'port': 587, 'security': 'starttls'},
    'outlook': {'host': 'smtp-mail.outlook.com', 'port': 587, 'security': 'starttls'},
    'icloud': {'host': 'smtp.mail.me.com', 'port': 587, 'security': 'starttls'},
    'gmx': {'host': 'mail.gmx.net', 'port': 587, 'security': 'starttls'},
    'other': {},
}
SECURITY_MODES = ('starttls', 'ssl', 'none')
EMAIL_RE = re.compile(r'^[^@\s<>,;"]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$')
HOST_RE = re.compile(r'^[A-Za-z0-9.-]{1,253}$')

DEFAULT_STATE: Dict[str, Any] = {
    'email': {
        'enabled': False, 'provider': 'other', 'host': '', 'port': 587, 'security': 'starttls',
        'username': '', 'password': '', 'sender': '', 'recipient': '',
        'last_sent_at': '', 'last_error': '',
    },
    'report': {'weekly': True, 'last_sent_at': ''},
    'seen': [],      # problem ids seen at the last check
    'sent': [],      # problem ids already sent and still present
}

_lock = threading.Lock()


# ── State ────────────────────────────────────────────────────────────────────

def _normalize(raw: Any) -> Dict[str, Any]:
    state = json.loads(json.dumps(DEFAULT_STATE))
    if not isinstance(raw, dict):
        return state
    for key in ('email', 'report'):
        if isinstance(raw.get(key), dict):
            state[key].update({k: v for k, v in raw[key].items() if k in state[key]})
    for key in ('seen', 'sent'):
        if isinstance(raw.get(key), list):
            state[key] = [str(v) for v in raw[key]][:200]
    email = state['email']
    email['enabled'] = bool(email['enabled'])
    try:
        email['port'] = max(1, min(65535, int(email['port'])))
    except (TypeError, ValueError):
        email['port'] = 587
    if email['security'] not in SECURITY_MODES:
        email['security'] = 'starttls'
    if email['provider'] not in EMAIL_PRESETS:
        email['provider'] = 'other'
    state['report']['weekly'] = bool(state['report']['weekly'])
    return state


def load_state(path: str = DELIVERY_FILE) -> Dict[str, Any]:
    try:
        with open(path) as f:
            return _normalize(json.load(f))
    except (OSError, ValueError):
        return _normalize(None)


def save_state(state: Dict[str, Any], path: str = DELIVERY_FILE) -> Dict[str, Any]:
    state = _normalize(state)
    ensure_directories()
    tmp = f'{path}.tmp'
    # The email password is in here: readable by the backend only.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)
    return state


def public_settings(state: Dict[str, Any]) -> Dict[str, Any]:
    email = dict(state['email'])
    password = email.pop('password', '')
    email['password_set'] = bool(password)
    email['configured'] = bool(email['host'] and email['recipient'])
    return {'email': email, 'report': dict(state['report']), 'presets': EMAIL_PRESETS}


def apply_email_settings(state: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Merge what the page sent into the email settings. A password is only
    replaced when a new one is typed. Returns (settings, problem)."""
    email = dict(state['email'])
    provider = str(payload.get('provider') or email['provider'])
    if provider not in EMAIL_PRESETS:
        return email, 'Unknown email provider.'
    email['provider'] = provider
    for key in ('host', 'username', 'sender', 'recipient', 'security'):
        if key in payload:
            email[key] = str(payload.get(key) or '').strip()
    if 'port' in payload:
        email['port'] = payload.get('port')
    if 'enabled' in payload:
        email['enabled'] = bool(payload.get('enabled'))
    if payload.get('password'):
        email['password'] = str(payload['password'])
    email.update(EMAIL_PRESETS[provider])
    if not email['username'] and provider != 'other':
        email['username'] = email['recipient']
    email = _normalize({'email': email})['email']
    if email['enabled'] or payload.get('test'):
        if not EMAIL_RE.match(email['recipient']):
            return email, 'Enter the email address that should get the messages.'
        if email['sender'] and not EMAIL_RE.match(email['sender']):
            return email, 'The sender address is not a valid email address.'
        if not HOST_RE.match(email['host']):
            return email, 'Enter the mail server (for example smtp.example.com).'
    return email, ''


# ── Sending ──────────────────────────────────────────────────────────────────

def send_email(email: Dict[str, Any], subject: str, body: str,
               smtp_module: Any = smtplib) -> Tuple[bool, str]:
    msg = EmailMessage()
    msg['Subject'] = re.sub(r'[\r\n]+', ' ', subject)[:200]
    msg['From'] = email.get('sender') or email.get('username') or email['recipient']
    msg['To'] = email['recipient']
    msg.set_content(body)
    try:
        context = ssl.create_default_context()
        if email['security'] == 'ssl':
            server = smtp_module.SMTP_SSL(email['host'], email['port'], timeout=20, context=context)
        else:
            server = smtp_module.SMTP(email['host'], email['port'], timeout=20)
        with server:
            if email['security'] == 'starttls':
                server.starttls(context=context)
            if email.get('username') and email.get('password'):
                server.login(email['username'], email['password'])
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return False, 'The mail server did not accept the name or password. Many providers need an app password.'
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        return False, f'The email could not be sent: {exc}'
    return True, ''


def _telegram_target() -> Optional[Tuple[str, str]]:
    from alerts_manager import load_alerts_state
    telegram = load_alerts_state().get('telegram', {})
    token = str(telegram.get('bot_token') or '').strip()
    chat = str(telegram.get('paired_chat_id') or '').strip()
    if telegram.get('enabled') and token and chat:
        return token, chat
    return None


def send_telegram(text: str) -> Tuple[bool, str]:
    target = _telegram_target()
    if not target:
        return False, 'Telegram is not set up.'
    from alerts_manager import _telegram_api_call
    ok, error, _ = _telegram_api_call(target[0], 'sendMessage', timeout_sec=12, payload={
        'chat_id': target[1], 'text': text, 'disable_web_page_preview': True})
    return bool(ok), str(error or '')


def deliver(state: Dict[str, Any], subject: str, body: str,
            email_sender: Callable = send_email, telegram_sender: Callable = send_telegram) -> List[str]:
    """Send to every channel that is set up. Returns the channels that worked."""
    reached = []
    email = state['email']
    if email['enabled'] and email['host'] and email['recipient']:
        ok, error = email_sender(email, subject, body)
        email['last_error'] = error
        if ok:
            email['last_sent_at'] = _now_iso()
            reached.append('email')
    if _telegram_target_safe():
        ok, _ = telegram_sender(f'{subject}\n\n{body}')
        if ok:
            reached.append('telegram')
    return reached


def _telegram_target_safe() -> bool:
    try:
        return _telegram_target() is not None
    except Exception:  # noqa: BLE001 - a broken alerts file must not stop email
        return False


# ── What to say ──────────────────────────────────────────────────────────────

def _nas_name() -> str:
    try:
        with open('/etc/hostname') as f:
            return (f.read().strip() or 'AlvaOS')[:63]
    except OSError:
        return 'AlvaOS'


def problems_to_send(alerts: List[Dict[str, Any]], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Problems seen at this check and the one before, not sent yet. Updates
    state['seen'] and state['sent']; gone problems can be sent again later."""
    current = [a for a in alerts
               if str(a.get('severity')) in ('critical', 'warning')
               and not str(a.get('id', '')).startswith(NOT_SENT_PREFIXES)]
    ids = [str(a.get('id')) for a in current]
    lasting = [a for a in current if str(a.get('id')) in state['seen']]
    new = [a for a in lasting if str(a.get('id')) not in state['sent']]
    state['seen'] = ids
    state['sent'] = [i for i in state['sent'] if i in ids]
    return new


def problem_message(problems: List[Dict[str, Any]], name: str) -> Tuple[str, str]:
    critical = any(p.get('severity') == 'critical' for p in problems)
    first = problems[0].get('title', 'A problem')
    subject = f'{name}: {first}' + (f' and {len(problems) - 1} more' if len(problems) > 1 else '')
    lines = [f'{"Something on" if critical else "Something to look at on"} {name}:', '']
    for p in problems[:10]:
        lines.append(f'- {p.get("title")}: {p.get("message")}')
    lines += ['', 'Open AlvaOS to see what to do.']
    return subject, '\n'.join(lines)


def report_due(state: Dict[str, Any], now: datetime) -> bool:
    if not state['report']['weekly'] or now.weekday() != REPORT_WEEKDAY or now.hour < REPORT_HOUR:
        return False
    try:
        last = datetime.fromisoformat(state['report']['last_sent_at'])
    except (TypeError, ValueError):
        return True
    if last.tzinfo is not None:
        last = last.astimezone().replace(tzinfo=None)
    return now - last > timedelta(days=6)


def report_message(alerts: List[Dict[str, Any]], pools: List[Dict[str, Any]], name: str) -> Tuple[str, str]:
    problems = [a for a in alerts if str(a.get('severity')) in ('critical', 'warning')]
    lines = []
    if problems:
        subject = f'{name}: weekly report, {len(problems)} thing{"s" if len(problems) > 1 else ""} to look at'
        lines.append('Needs attention:')
        lines += [f'- {p.get("title")}: {p.get("message")}' for p in problems[:10]]
    else:
        subject = f'{name}: weekly report, all is well'
        lines.append('All is well. Disks, data checks and backups had no problems.')
    if pools:
        lines += ['', 'Storage:']
        lines += [f'- {p["name"]}: {p["used_percent"]}% used' for p in pools]
    lines += ['', 'You get this every Sunday. Turn it off in AlvaOS under Settings › Notifications.']
    return subject, '\n'.join(lines)


def _pool_usage() -> List[Dict[str, Any]]:
    import shutil
    from storage_manager import load_pools_state
    pools = []
    for pool in (load_pools_state() or {}).values():
        mount = (pool or {}).get('mount_point')
        if not mount or mount == '/' or not os.path.ismount(mount):
            continue
        usage = shutil.disk_usage(mount)
        pools.append({'name': pool.get('name') or mount,
                      'used_percent': round(usage.used * 100 / usage.total) if usage.total else 0})
    return pools


# ── The loop ─────────────────────────────────────────────────────────────────

def run_once(collect: Optional[Callable[[], List[Dict[str, Any]]]] = None,
             now: Optional[datetime] = None, path: str = DELIVERY_FILE,
             send: Callable = deliver, pools: Callable = _pool_usage) -> Dict[str, Any]:
    if collect is None:
        from alerts_manager import _collect_system_alerts
        collect = _collect_system_alerts
    with _lock:
        alerts = collect()
        state = load_state(path)
        name = _nas_name()
        new = problems_to_send(alerts, state)
        if new:
            subject, body = problem_message(new, name)
            if send(state, subject, body):
                state['sent'] += [str(p.get('id')) for p in new]
        if report_due(state, now or datetime.now()):
            subject, body = report_message(alerts, pools(), name)
            if send(state, subject, body):
                state['report']['last_sent_at'] = _now_iso()
        return save_state(state, path)


def serve_forever(interval: int = CHECK_INTERVAL_SECONDS) -> None:
    time.sleep(60)   # let pools mount and services start first
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001 - keep checking
            print(f'Alert delivery failed: {exc}')
        time.sleep(interval)
