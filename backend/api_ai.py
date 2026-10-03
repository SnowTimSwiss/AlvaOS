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
        settings, problem = ai_assistant.save_settings(settings, request.get_json(silent=True) or {})
        if problem:
            return jsonify({'error': problem}), 400
    return jsonify({'success': True, **ai_assistant.public_settings(settings)})


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
        return jsonify({'error': 'The assistant is off. Turn it on in Settings › Assistant.'}), 409
    data = request.get_json(silent=True) or {}
    token = request.headers.get('Authorization', '')
    try:
        answer = ai_assistant.chat(settings, data.get('messages'), _reader(token))
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


@bp.route('/api/v1/ai/test', methods=['POST'])
@require_auth(require_admin=True)
def ai_test():
    settings = ai_assistant.load_settings()
    payload = request.get_json(silent=True) or {}
    trial = dict(settings)
    for key in ('provider', 'base_url', 'model'):
        if payload.get(key):
            trial[key] = str(payload[key]).strip()
    if payload.get('api_key'):
        trial['api_key'] = str(payload['api_key']).strip()
    if not ai_assistant.check_url(trial['base_url']):
        return jsonify({'error': 'Enter the address of the service.'}), 400
    try:
        message = ai_assistant.call_model(trial, [{'role': 'user', 'content': 'Say hello in five words.'}],
                                          use_tools=False)
    except Exception as e:  # noqa: BLE001
        return jsonify({'error': f'No answer: {e}'}), 502
    return jsonify({'success': True, 'reply': str(message.get('content') or '').strip()[:300]})
