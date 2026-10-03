#!/usr/bin/env python3
"""The AlvaOS assistant: questions about this NAS in plain words.

It talks to any OpenAI-compatible chat API (Ollama on the network or in the
cloud, OpenAI, OpenRouter, LM Studio, ...). To answer, it may read the same
API endpoints the web page uses, as the signed-in admin, through a fixed list
of read-only endpoints without secrets. It cannot change anything yet.
"""

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from common import ensure_directories

SETTINGS_FILE = '/var/lib/alvaos/ai.json'
MAX_ROUNDS = 6
MAX_TOOL_CHARS = 8000
MAX_HISTORY = 30

PROVIDERS: Dict[str, Dict[str, Any]] = {
    'ollama-local': {'label': 'Ollama on your network', 'base_url': 'http://localhost:11434/v1', 'needs_key': False,
                     'model': 'llama3.1'},
    'ollama-cloud': {'label': 'Ollama Cloud', 'base_url': 'https://ollama.com/v1', 'needs_key': True,
                     'model': 'gpt-oss:20b'},
    'openai': {'label': 'OpenAI', 'base_url': 'https://api.openai.com/v1', 'needs_key': True, 'model': 'gpt-4o-mini'},
    'other': {'label': 'Another OpenAI-compatible service', 'base_url': '', 'needs_key': False, 'model': ''},
}

# What the assistant may read: (name, endpoint, what it is). Only GET, only
# what the page shows, nothing secret (no tokens, keys, recovery kits, logs).
TOOLS: List[Tuple[str, str, str]] = [
    ('system_overview', '/api/v1/system/info', 'CPU, memory, uptime, network, AlvaOS version and pool usage'),
    ('problems', '/api/v1/alerts', 'Current alerts: what needs attention right now'),
    ('notifications', '/api/v1/notifications', 'Recent notifications (the bell)'),
    ('storage_pools', '/api/v1/storage/pools', 'Storage pools: size, use, RAID level, disks, health'),
    ('disks', '/api/v1/storage/disks', 'All disks with model, size, SMART health and role'),
    ('data_checks', '/api/v1/storage/health-checks', 'Schedule and results of data checks (scrub) and disk tests'),
    ('disk_sleep', '/api/v1/storage/disk-power', 'Whether hard disks sleep when idle'),
    ('shared_folders', '/api/v1/storage/shares', 'Shared folders, who can open them and how'),
    ('people', '/api/v1/users', 'People (share accounts) on this NAS'),
    ('backup_status', '/api/v1/backup/status', 'When backups last ran and if they worked'),
    ('backup_settings', '/api/v1/backup/settings', 'What is backed up, how often, how long restore points are kept'),
    ('restore_points', '/api/v1/backup/snapshots', 'Restore points (snapshots) of folders'),
    ('apps', '/api/v1/apps/installed', 'Installed apps and their versions'),
    ('app_containers', '/api/v1/containers', 'Running and stopped app containers'),
    ('updates', '/api/v1/updates/status', 'Whether an AlvaOS update is available or running'),
    ('update_history', '/api/v1/updates/history', 'Past updates'),
    ('network', '/api/v1/system/network', 'Network addresses and interfaces'),
    ('time', '/api/v1/system/time', 'Date, time and time zone'),
    ('services', '/api/v1/watchdog/status', 'Whether file sharing and apps are running'),
    ('files_app', '/api/v1/files-app', 'Whether AlvaOS Files is on'),
]
TOOL_PATHS = {name: path for name, path, _ in TOOLS}

LEVELS = ('read', 'ask')   # only look / propose changes the person confirms

# What the assistant may propose at the "ask" level. It never runs one
# itself: a proposal is shown in the chat with exactly what will happen, and
# only the person's click runs it, through the normal endpoint with their
# session and CSRF token (api_ai.py). Each builder checks its arguments
# against the real state and returns (title, detail, method, path, body).
ACTION_SPECS: Dict[str, Dict[str, Any]] = {
    'backup_now': {
        'description': 'Make restore points of the shared folders now (a backup on this NAS).',
        'parameters': {},
    },
    'start_data_check': {
        'description': 'Start a data check (scrub) of a storage pool: every block is read and bad copies are '
                       'repaired from the good one. Takes hours; the NAS is slower meanwhile.',
        'parameters': {'pool_id': {'type': 'string', 'description': 'The id of the pool from storage_pools.'}},
    },
    'restart_app': {
        'description': 'Restart one app (container) that hangs or misbehaves.',
        'parameters': {'container': {'type': 'string', 'description': 'Name or id of the container from app_containers.'}},
    },
    'check_services': {
        'description': 'Check file sharing and apps now and restart what has stopped.',
        'parameters': {},
    },
    'set_disk_sleep': {
        'description': 'Let hard disks sleep after some minutes without use (0 = never).',
        'parameters': {'minutes': {'type': 'integer', 'enum': [0, 10, 20, 30, 60]}},
    },
}


def _find(items: Any, *keys: str) -> List[Dict[str, Any]]:
    if isinstance(items, dict):
        for key in keys:
            if isinstance(items.get(key), list):
                return [i for i in items[key] if isinstance(i, dict)]
        return []
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def build_action(name: str, args: Dict[str, Any], read: Callable[[str], Tuple[int, Any]]) -> Dict[str, Any]:
    """A proposal the person can confirm, or ValueError with why not."""
    if name == 'backup_now':
        return {'title': 'Make a backup now',
                'detail': 'Restore points of your shared folders are made now. This takes a moment and changes '
                          'no files.', 'method': 'POST', 'path': '/api/v1/backup/run', 'body': {'backup_type': 'pool'}}
    if name == 'start_data_check':
        wanted = str(args.get('pool_id') or '').strip()
        _, data = read('/api/v1/storage/pools')
        pool = next((p for p in _find(data, 'pools') if wanted and wanted in (str(p.get('id')), str(p.get('name')))),
                    None)
        if not pool or pool.get('is_system_pool'):
            raise ValueError(f'There is no storage pool "{wanted}".')
        return {'title': f'Start a data check of "{pool.get("name") or pool.get("id")}"',
                'detail': 'Every block is read and bad copies are repaired from the good one. It takes a few '
                          'hours, the NAS is a little slower meanwhile; you can stop it on the Storage page.',
                'method': 'POST', 'path': f'/api/v1/storage/pools/{pool.get("id")}/scrub', 'body': {}}
    if name == 'restart_app':
        wanted = str(args.get('container') or '').strip()
        _, data = read('/api/v1/containers')
        found = None
        for c in _find(data, 'containers'):
            ident = str(c.get('ID') or c.get('id') or '')
            label = str(c.get('Names') or c.get('name') or '')
            if wanted and (wanted == label or (len(wanted) >= 6 and ident.startswith(wanted))):
                found = (ident, label)
                break
        if not found or not re_container.match(found[0] or found[1]):
            raise ValueError(f'There is no app container "{wanted}".')
        ident, label = found[0] or found[1], found[1]
        return {'title': f'Restart the app "{label or ident}"',
                'detail': 'The app stops and starts again; it is away for a moment. Its data stays.',
                'method': 'POST', 'path': f'/api/v1/containers/{ident}/restart', 'body': {}}
    if name == 'check_services':
        return {'title': 'Check file sharing and apps now',
                'detail': 'AlvaOS checks its services and restarts what has stopped.',
                'method': 'POST', 'path': '/api/v1/watchdog/check', 'body': {}}
    if name == 'set_disk_sleep':
        try:
            minutes = int(str(args.get('minutes')))
        except (TypeError, ValueError):
            minutes = -1
        if minutes not in (0, 10, 20, 30, 60):
            raise ValueError('Disks can sleep after 10, 20, 30 or 60 minutes, or never (0).')
        return {'title': 'Never let hard disks sleep' if minutes == 0 else
                f'Let hard disks sleep after {minutes} minutes without use',
                'detail': 'Applies to the spinning data disks; SSDs and the system disk are left alone.',
                'method': 'POST', 'path': '/api/v1/storage/disk-power', 'body': {'spindown_minutes': minutes}}
    raise ValueError(f'There is no action called {name}.')


re_container = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')

ASK_PROMPT = (
    ' You may also propose changes with the action tools. A proposal does not run: the person sees it in the '
    'chat and has to confirm it. Propose only what they asked for or what clearly fixes a problem you found, one at a '
    'time, and say in a sentence what it will do. Never say it is done.'
)

SYSTEM_PROMPT = (
    'You are the assistant built into AlvaOS, a home NAS. Answer in the language the person writes in, '
    'short and in plain words, for someone who is not a technician. Use the tools to look at the real state '
    'of this NAS before you answer questions about it; do not guess numbers. When something should be changed '
    'by hand, say where in AlvaOS to do it (pages: Dashboard, Storage, Files, Apps, Backup, Updates, Settings). '
    'Never ask for passwords.'
)
READ_PROMPT = ' You can only look, not change anything.'


# ── Settings ─────────────────────────────────────────────────────────────────

def load_settings(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or SETTINGS_FILE
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    provider = str(raw.get('provider') or '')
    provider = provider if provider in PROVIDERS else 'ollama-local'
    return {
        'enabled': bool(raw.get('enabled')),
        'provider': provider,
        'base_url': str(raw.get('base_url') or PROVIDERS[provider]['base_url']),
        'model': str(raw.get('model') or PROVIDERS[provider]['model']),
        'api_key': str(raw.get('api_key') or ''),
        'level': raw.get('level') if raw.get('level') in LEVELS else 'read',
    }


def public_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in settings.items() if k != 'api_key'}
    out['key_set'] = bool(settings.get('api_key'))
    out['providers'] = {k: {kk: vv for kk, vv in v.items()} for k, v in PROVIDERS.items()}
    return out


def check_url(url: str) -> bool:
    return bool(re.match(r'^https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~/-]*)?$', url or ''))


def save_settings(current: Dict[str, Any], payload: Dict[str, Any],
                  path: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    path = path or SETTINGS_FILE
    settings = dict(current)
    if 'provider' in payload:
        if payload['provider'] not in PROVIDERS:
            return current, 'Unknown provider.'
        if payload['provider'] != settings['provider']:
            settings['base_url'] = PROVIDERS[payload['provider']]['base_url']
            settings['model'] = PROVIDERS[payload['provider']]['model']
        settings['provider'] = payload['provider']
    for key in ('base_url', 'model'):
        if key in payload:
            settings[key] = str(payload.get(key) or '').strip()
    if payload.get('api_key'):
        settings['api_key'] = str(payload['api_key']).strip()
    if payload.get('clear_key'):
        settings['api_key'] = ''
    if 'enabled' in payload:
        settings['enabled'] = bool(payload['enabled'])
    if 'level' in payload:
        if payload['level'] not in LEVELS:
            return current, 'Choose what the assistant may do.'
        settings['level'] = payload['level']
    if settings['enabled']:
        if not check_url(settings['base_url']):
            return current, 'Enter the address of the service, like http://192.168.1.20:11434/v1.'
        if not re.match(r'^[A-Za-z0-9._:/@+-]{1,120}$', settings['model'] or ''):
            return current, 'Enter the name of the model.'
        if PROVIDERS[settings['provider']]['needs_key'] and not settings['api_key']:
            return current, 'This service needs an API key.'
    ensure_directories()
    tmp = f'{path}.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(settings, f)
    os.replace(tmp, path)
    return settings, ''


# ── Talking to the model ─────────────────────────────────────────────────────

def tool_specs(level: str = 'read') -> List[Dict[str, Any]]:
    specs = [{'type': 'function', 'function': {'name': name, 'description': description,
                                               'parameters': {'type': 'object', 'properties': {}}}}
             for name, _, description in TOOLS]
    if level == 'ask':
        specs += [{'type': 'function', 'function': {
            'name': name, 'description': 'PROPOSE (the person confirms): ' + spec['description'],
            'parameters': {'type': 'object', 'properties': spec['parameters'],
                           'required': list(spec['parameters'])}}} for name, spec in ACTION_SPECS.items()]
    return specs


def call_model(settings: Dict[str, Any], messages: List[Dict[str, Any]], post: Optional[Callable] = None,
               use_tools: bool = True) -> Dict[str, Any]:
    """One chat completion. Returns the assistant message."""
    if post is None:
        import requests
        post = requests.post
    headers = {'Content-Type': 'application/json'}
    if settings.get('api_key'):
        headers['Authorization'] = f"Bearer {settings['api_key']}"
    body: Dict[str, Any] = {'model': settings['model'], 'messages': messages, 'temperature': 0.2}
    if use_tools:
        body['tools'] = tool_specs(settings.get('level', 'read'))
    res = post(settings['base_url'].rstrip('/') + '/chat/completions', headers=headers, json=body, timeout=120)
    if getattr(res, 'status_code', 200) >= 400:
        detail = ''
        try:
            detail = str((res.json().get('error') or {}).get('message') or '')
        except Exception:  # noqa: BLE001 - any body
            detail = str(getattr(res, 'text', ''))[:200]
        raise RuntimeError(f'The AI service answered {res.status_code}{": " + detail if detail else ""}')
    data = res.json()
    message = ((data.get('choices') or [{}])[0] or {}).get('message') or {}
    if not isinstance(message, dict):
        raise RuntimeError('The AI service gave an answer AlvaOS does not understand.')
    return message


SECRET_KEY = re.compile(r'pass|secret|token|api_?key|private|otp|hash|salt|csrf|cookie', re.I)


def redact(data: Any) -> Any:
    """Drops anything that looks secret, in case an endpoint ever grows one."""
    if isinstance(data, dict):
        return {k: ('(hidden)' if SECRET_KEY.search(str(k)) else redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


def run_tool(name: str, read: Callable[[str], Tuple[int, Any]]) -> str:
    path = TOOL_PATHS.get(name)
    if not path:
        return json.dumps({'error': f'There is no tool called {name}.'})
    status, data = read(path)
    data = redact(data)
    text = json.dumps(data, default=str, ensure_ascii=False)
    if status >= 400:
        return json.dumps({'error': f'{path} answered {status}', 'detail': text[:500]})
    return text if len(text) <= MAX_TOOL_CHARS else text[:MAX_TOOL_CHARS] + ' …(cut)'


def clean_history(history: Any) -> List[Dict[str, str]]:
    out = []
    for item in history if isinstance(history, list) else []:
        if isinstance(item, dict) and item.get('role') in ('user', 'assistant') and isinstance(item.get('content'), str):
            out.append({'role': item['role'], 'content': item['content'][:8000]})
    return out[-MAX_HISTORY:]


def chat(settings: Dict[str, Any], history: Any, read: Callable[[str], Tuple[int, Any]],
         post: Optional[Callable] = None) -> Dict[str, Any]:
    """Answer the last question. Returns {'reply', 'looked_at', 'proposals'};
    proposals (at the "ask" level) are changes the person still has to confirm."""
    level = settings.get('level', 'read')
    prompt = SYSTEM_PROMPT + (ASK_PROMPT if level == 'ask' else READ_PROMPT)
    messages: List[Dict[str, Any]] = [{'role': 'system', 'content': prompt}] + clean_history(history)
    if len(messages) < 2 or messages[-1]['role'] != 'user':
        raise ValueError('Ask something first.')
    looked_at: List[str] = []
    proposals: List[Dict[str, Any]] = []

    def answer(message: Dict[str, Any]) -> Dict[str, Any]:
        return {'reply': str(message.get('content') or '').strip() or '(No answer.)', 'looked_at': looked_at,
                'proposals': proposals}

    for _ in range(MAX_ROUNDS):
        message = call_model(settings, messages, post)
        calls = message.get('tool_calls') or []
        if not calls:
            return answer(message)
        messages.append({'role': 'assistant', 'content': message.get('content') or '', 'tool_calls': calls})
        for call in calls[:8]:
            function = (call or {}).get('function') or {}
            name = str(function.get('name') or '')
            call_id = str((call or {}).get('id') or name)
            if name in ACTION_SPECS:
                if level != 'ask':
                    result = {'error': 'You can only look, not change anything.'}
                elif len(proposals) >= 3:
                    result = {'error': 'Enough proposals for now; let the person decide first.'}
                else:
                    try:
                        raw_args = function.get('arguments') or '{}'
                        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                        proposals.append(build_action(name, args if isinstance(args, dict) else {}, read))
                        result = {'proposed': proposals[-1]['title'],
                                  'note': 'Shown to the person; it runs only if they confirm.'}
                    except (ValueError, TypeError) as e:
                        result = {'error': str(e)}
                messages.append({'role': 'tool', 'tool_call_id': call_id, 'content': json.dumps(result)})
                continue
            looked_at.append(name)
            messages.append({'role': 'tool', 'tool_call_id': call_id, 'content': run_tool(name, read)})
    message = call_model(settings, messages + [{'role': 'user', 'content': 'Answer now with what you found.'}],
                         post, use_tools=False)
    return answer(message)
