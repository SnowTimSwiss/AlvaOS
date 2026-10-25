"""The AlvaOS assistant API (admins). See ai_assistant.py."""

import secrets
import threading
import time
from typing import Any, Dict

from flask import Blueprint, current_app, jsonify, request

import ai_assistant
from auth_manager import require_auth

bp = Blueprint('ai', __name__)

PROPOSAL_SECONDS = 600
_lock = threading.Lock()
_proposals: Dict[str, Dict[str, Any]] = {}   # id -> {'action', 'token', 'expires'}: changes waiting for the person's click


@bp.route('/api/v1/ai/settings', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def ai_settings():
    settings = ai_assistant.load_settings()
    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        if isinstance(payload.get('connections'), list):
            _ask_thinking(payload['connections'], settings)
        settings, problem = ai_assistant.save_settings(settings, payload)
        if problem:
            return jsonify({'error': problem}), 400
    return jsonify({'success': True, **ai_assistant.public_settings(settings)})


def _ask_thinking(sent, settings):
    """What each model can (think, how hard) comes from the service, not from
    the page: asked for models that are new, or when the address changed."""
    for item in sent:
        if not isinstance(item, dict):
            continue
        saved = next((c for c in settings['connections'] if c['id'] == item.get('id')), None)
        conn = _draft({'connection': item}, settings)
        models = [str(m) for m in item.get('models') or [] if ai_assistant.MODEL_RE.match(str(m))]
        before = saved.get('thinking', {}) if saved and saved['base_url'] == conn['base_url'] \
            and saved['provider'] == conn['provider'] else {}
        known = {m: before[m] for m in models if m in before}
        new = [m for m in models if m not in known]
        item['thinking'] = {**known, **(ai_assistant.detect_thinking(conn, new) if new else {})}


def _reader(token):
    """Reads an allowed endpoint in-process, as the signed-in admin."""
    client = current_app.test_client()

    def read(path):
        res = client.get(path, headers={'Authorization': token})
        try:
            return res.status_code, res.get_json(silent=True)
        except Exception:  # noqa: BLE001 - a non-JSON answer
            return res.status_code, None
    return read


@bp.route('/api/v1/ai/chat', methods=['POST'])
@require_auth(require_admin=True)
def ai_chat():
    settings = ai_assistant.load_settings()
    if not settings['enabled']:
        return jsonify({'error': 'The assistant is off. Turn it on in Settings › AI.'}), 409
    data = request.get_json(silent=True) or {}
    token = request.headers.get('Authorization', '')
    try:
        answer = ai_assistant.chat(settings, data.get('messages'), _reader(token), page=str(data.get('page') or ''))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:  # noqa: BLE001 - the service is outside AlvaOS
        return jsonify({'error': f'The assistant could not answer: {e}'}), 502
    shown = []
    now = time.time()
    with _lock:
        for key in [k for k, v in _proposals.items() if v['expires'] < now]:
            del _proposals[key]
        for action in answer.pop('proposals', []):
            pid = secrets.token_urlsafe(12)
            _proposals[pid] = {'action': action, 'token': token, 'expires': now + PROPOSAL_SECONDS}
            shown.append({'id': pid, 'title': action['title'], 'detail': action['detail']})
    return jsonify({'success': True, **answer, 'proposals': shown})


@bp.route('/api/v1/ai/actions/<proposal_id>', methods=['POST'])
@require_auth(require_admin=True)
def ai_action(proposal_id):
    """The person's answer to a proposal: run it, or drop it. Runs through the
    normal endpoint with this session and its CSRF token, so it is exactly as
    if the person had pressed the button on that page."""
    token = request.headers.get('Authorization', '')
    with _lock:
        pending = _proposals.pop(proposal_id, None)
    if not pending or pending['expires'] < time.time() or pending['token'] != token:
        return jsonify({'error': 'This proposal is no longer open. Ask again.'}), 404
    if (request.get_json(silent=True) or {}).get('decision') != 'run':
        return jsonify({'success': True, 'done': False, 'message': 'Left as it is.'})
    action = pending['action']
    res = current_app.test_client().open(
        action['path'], method=action['method'], json=action['body'],
        headers={'Authorization': token, 'X-CSRF-Token': request.headers.get('X-CSRF-Token', '')})
    data = res.get_json(silent=True) or {}
    if res.status_code >= 400:
        return jsonify({'error': str(data.get('error') or f'That did not work ({res.status_code}).')}), 409
    return jsonify({'success': True, 'done': True,
                    'message': str(data.get('message') or f'Done: {action["title"]}.')})


def _draft(payload: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """A connection as the form has it now: a key left empty is the saved one."""
    found = payload.get('connection')
    raw: Dict[str, Any] = found if isinstance(found, dict) else {}
    saved = next((c for c in settings['connections'] if c['id'] == raw.get('id')), None)
    provider = str(raw.get('provider') or (saved or {}).get('provider') or 'other')
    provider = provider if provider in ai_assistant.PROVIDERS else 'other'
    return {'id': str(raw.get('id') or ''), 'name': str(raw.get('name') or ''), 'provider': provider,
            'base_url': str(raw.get('base_url') or (saved or {}).get('base_url') or '').strip(),
            'api_key': str(raw.get('api_key') or '').strip() or (saved or {}).get('api_key', '')}


@bp.route('/api/v1/ai/models', methods=['POST'])
@require_auth(require_admin=True)
def ai_models():
    """The models a service offers, to add with one click."""
    settings = ai_assistant.load_settings()
    conn = _draft(request.get_json(silent=True) or {}, settings)
    found, problem = ai_assistant.list_models(conn)
    if problem and not found:
        return jsonify({'error': problem}), 502
    return jsonify({'success': True, 'models': found})


@bp.route('/api/v1/ai/test', methods=['POST'])
@require_auth(require_admin=True)
def ai_test():
    settings = ai_assistant.load_settings()
    payload = request.get_json(silent=True) or {}
    trial = dict(settings)
    if isinstance(payload.get('connection'), dict):
        trial.update({k: v for k, v in _draft(payload, settings).items() if k in ('provider', 'base_url', 'api_key')})
        trial['model'] = str(payload.get('model') or '').strip()
    else:
        for key in ('provider', 'base_url', 'model'):
            if payload.get(key):
                trial[key] = str(payload[key]).strip()
        if payload.get('api_key'):
            trial['api_key'] = str(payload['api_key']).strip()
    if not ai_assistant.check_url(trial['base_url']):
        return jsonify({'error': 'Enter the address of the service.'}), 400
    if not ai_assistant.MODEL_RE.match(trial['model'] or ''):
        return jsonify({'error': 'Add a model first.'}), 400
    try:
        message = ai_assistant.call_model(trial, [{'role': 'user', 'content': 'Say hello in five words.'}],
                                          use_tools=False)
    except Exception as e:  # noqa: BLE001
        return jsonify({'error': f'No answer: {e}'}), 502
    return jsonify({'success': True, 'reply': str(message.get('content') or '').strip()[:300]})


@bp.route('/api/v1/ai/search-test', methods=['POST'])
@require_auth(require_admin=True)
def ai_search_test():
    """Does web search work from this NAS? Asks DuckDuckGo once."""
    import web_search
    found, problem = web_search.search('AlvaOS NAS')
    if not found:
        return jsonify({'error': problem}), 502
    return jsonify({'success': True, 'titles': [r['title'] for r in found[:3]]})
