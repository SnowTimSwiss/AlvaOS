"""The AlvaOS assistant API (admins). See ai_assistant.py."""

from flask import Blueprint, current_app, jsonify, request

import ai_assistant
from auth_manager import require_auth

bp = Blueprint('ai', __name__)


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
    return jsonify({'success': True, **answer})


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
