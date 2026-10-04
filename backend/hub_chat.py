#!/usr/bin/env python3
"""Chat in the AlvaOS Hub: talk to an AI model, like ChatGPT.

Only a chat: no tools, nothing on the NAS is read or changed (that is the
assistant in the admin pages, ai_assistant.py, a different thing). It uses
the AI service set up in Settings › Assistant (address, key); the key never
leaves the NAS. The admin chooses on the Hub page which models people may
pick; without a choice it is the model set there.

Each person's chats are files in their own place (hub_data.py), by default
".alvaos/chat/" in their personal folder: chats.json (the list) and
chat-<id>.json per chat.

Answers stream to the page as lines of JSON: {"chat": {...}} first, then
{"r": "..."} (thinking) and {"t": "..."} (the answer) as they come, and
{"done": true} or {"error": "..."} at the end.
"""

import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from flask import Blueprint, Response, jsonify, request

import ai_assistant
import hub_apps
import hub_data

bp = Blueprint('hub_chat', __name__)

INDEX = 'chats.json'
ID_RE = re.compile(r'^[A-Za-z0-9_-]{6,40}$')
EFFORTS = ('low', 'medium', 'high')
MAX_CHATS = 2000
MAX_MESSAGE = 32000
MAX_CONTEXT_MESSAGES = 40
SYSTEM_PROMPT = ('You are a helpful assistant. Answer in the language of the question. Use Markdown where it '
                 'helps (lists, code blocks, tables). Today is {today}.')


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _chat_file(chat_id: str) -> str:
    return f'chat-{chat_id}.json'


def connection() -> Tuple[Dict[str, Any], str]:
    """The AI service from Settings › Assistant, and why it cannot be used ('' when it can)."""
    settings = ai_assistant.load_settings()
    if not os.path.exists(ai_assistant.SETTINGS_FILE) or not ai_assistant.check_url(settings['base_url']):
        return settings, 'No AI service is set up yet. Ask the person who looks after the NAS (Settings › Assistant).'
    if ai_assistant.PROVIDERS[settings['provider']]['needs_key'] and not settings['api_key']:
        return settings, 'The AI service needs a key. Ask the person who looks after the NAS (Settings › Assistant).'
    return settings, ''


def models(service: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> List[str]:
    chosen = (settings or hub_apps.load())['apps']['chat'].get('models') or []
    return list(chosen) or ([service['model']] if service.get('model') else [])


def title_for(message: str) -> str:
    words = re.sub(r'\s+', ' ', message).strip()
    return words if len(words) <= 60 else words[:57].rsplit(' ', 1)[0] + '…'


def _own(session: Dict[str, Any]) -> Optional[hub_data.Place]:
    return hub_data.own_place('chat', session)


NO_PLACE = ('You have no personal folder yet, so your chats have nowhere to go. '
            'Ask the person who looks after the NAS to make you one.')


@bp.get('/api/chat')
def overview():
    """The models to choose from, and this person's chats, newest first."""
    session, refused = hub_data.need_session('chat')
    if refused:
        return refused
    service, problem = connection()
    place = _own(session)
    chats: List[Dict[str, Any]] = []
    if place:
        index, error = hub_data.read(place, INDEX)
        if index is None:
            problem = problem or error
        else:
            chats = [c for c in index.get('chats') or [] if isinstance(c, dict) and ID_RE.match(str(c.get('id')))]
    else:
        problem = problem or NO_PLACE
    chats.sort(key=lambda c: str(c.get('updated') or ''), reverse=True)
    return jsonify({'models': models(service), 'chats': chats, 'problem': problem})


@bp.get('/api/chat/<chat_id>')
def one(chat_id: str):
    session, refused = hub_data.need_session('chat')
    if refused:
        return refused
    place = _own(session)
    if not place or not ID_RE.match(chat_id):
        return jsonify({'error': 'This chat is not there.'}), 404
    chat, error = hub_data.read(place, _chat_file(chat_id))
    if chat is None:
        return jsonify({'error': error}), 409
    if not chat:
        return jsonify({'error': 'This chat is not there (any more).'}), 404
    return jsonify(chat)


def _save(place: hub_data.Place, chat: Dict[str, Any]) -> str:
    """The chat's file, and its line in the list."""
    error = hub_data.write(place, _chat_file(chat['id']), chat)
    if error:
        return error
    with hub_data.lock(place, INDEX):
        index, error = hub_data.read(place, INDEX)
        if index is None:
            return error
        rows = [c for c in index.get('chats') or [] if isinstance(c, dict) and c.get('id') != chat['id']]
        rows.append({k: chat.get(k) for k in ('id', 'title', 'created', 'updated', 'model')})
        rows.sort(key=lambda c: str(c.get('updated') or ''), reverse=True)
        return hub_data.write(place, INDEX, {'chats': rows[:MAX_CHATS]})


@bp.post('/api/chat/<chat_id>/rename')
def rename(chat_id: str):
    session, refused = hub_data.need_session('chat')
    if refused:
        return refused
    title = str((request.get_json(silent=True) or {}).get('title') or '').strip()[:120]
    place = _own(session)
    if not place or not ID_RE.match(chat_id):
        return jsonify({'error': 'This chat is not there.'}), 404
    if not title:
        return jsonify({'error': 'Give the chat a name.'}), 400
    with hub_data.lock(place, _chat_file(chat_id)):
        chat, error = hub_data.read(place, _chat_file(chat_id))
        if not chat:
            return jsonify({'error': error or 'This chat is not there (any more).'}), 404
        chat['title'] = title
        error = _save(place, chat)
    return (jsonify({'error': error}), 409) if error else jsonify({'success': True, 'title': title})


@bp.post('/api/chat/<chat_id>/delete')
def delete(chat_id: str):
    session, refused = hub_data.need_session('chat')
    if refused:
        return refused
    place = _own(session)
    if not place or not ID_RE.match(chat_id):
        return jsonify({'error': 'This chat is not there.'}), 404
    with hub_data.lock(place, INDEX):
        index, error = hub_data.read(place, INDEX)
        if index is None:
            return jsonify({'error': error}), 409
        rows = [c for c in index.get('chats') or [] if isinstance(c, dict) and c.get('id') != chat_id]
        error = hub_data.write(place, INDEX, {'chats': rows}) or hub_data.delete(place, _chat_file(chat_id))
    return (jsonify({'error': error}), 409) if error else jsonify({'success': True})


# ── Talking to the model ─────────────────────────────────────────────────────

class ThinkSplitter:
    """Some models write their thinking into the answer as <think>…</think>;
    this sends that part to the thinking instead."""

    OPEN, CLOSE = '<think>', '</think>'

    def __init__(self) -> None:
        self.state = 'start'   # start → (think →) answer
        self.buffer = ''

    def feed(self, text: str) -> List[Tuple[str, str]]:
        self.buffer += text
        out: List[Tuple[str, str]] = []
        if self.state == 'start':
            head = self.buffer.lstrip()
            if head.startswith(self.OPEN):
                self.state, self.buffer = 'think', head[len(self.OPEN):]
            elif self.OPEN.startswith(head):
                return out   # could still become <think>
            else:
                self.state = 'answer'
        if self.state == 'think':
            end = self.buffer.find(self.CLOSE)
            if end < 0:
                keep = len(self.CLOSE) - 1   # the end tag may come in pieces
                if len(self.buffer) > keep:
                    out.append(('r', self.buffer[:-keep]))
                    self.buffer = self.buffer[-keep:]
                return out
            if end:
                out.append(('r', self.buffer[:end]))
            self.state, self.buffer = 'answer', self.buffer[end + len(self.CLOSE):].lstrip('\n')
        if self.state == 'answer' and self.buffer:
            out.append(('t', self.buffer))
            self.buffer = ''
        return out

    def flush(self) -> List[Tuple[str, str]]:
        rest, self.buffer = self.buffer, ''
        return [('r' if self.state == 'think' else 't', rest)] if rest else []


def _error_text(res: Any, service: Dict[str, Any], model: str) -> str:
    detail = ''
    try:
        body = res.json()
        detail = str((body.get('error') or {}).get('message') if isinstance(body.get('error'), dict)
                     else body.get('error') or '')
    except Exception:  # noqa: BLE001 - any body
        detail = str(getattr(res, 'text', ''))[:200]
    if res.status_code == 401:
        return 'The AI service does not accept its key. Ask the person who looks after the NAS.'
    if res.status_code == 404 and 'model' in detail.lower():
        return f'The AI service does not know the model "{model}".'
    return f'The AI service answered {res.status_code}{": " + detail if detail else ""}'


def _open(service: Dict[str, Any], model: str, messages: List[Dict[str, str]], reasoning: bool, effort: str,
          post: Callable[..., Any]) -> Tuple[Any, str, bool]:
    """The streaming answer, or why not. Tries again without the thinking
    setting when the service or model does not know it."""
    headers = {'Content-Type': 'application/json'}
    if service.get('api_key'):
        headers['Authorization'] = f"Bearer {service['api_key']}"
    body: Dict[str, Any] = {'model': model, 'messages': messages, 'stream': True}
    if reasoning:
        body['reasoning_effort'] = effort
    elif service['provider'].startswith('ollama'):
        body['reasoning_effort'] = 'none'   # Ollama: do not think first
    url = service['base_url'].rstrip('/') + '/chat/completions'
    for attempt in (1, 2):
        try:
            res = post(url, headers=headers, json=body, stream=True, timeout=(15, 300))
        except Exception as e:  # noqa: BLE001 - requests raises many kinds
            if 'Timeout' in type(e).__name__:
                return None, 'The AI service took too long to answer.', False
            return None, f'The NAS cannot reach the AI service at {service["base_url"]}.', False
        if res.status_code < 400:
            return res, '', attempt == 2 and reasoning
        text = _error_text(res, service, model)
        if attempt == 1 and 'reasoning_effort' in body and res.status_code in (400, 422) \
                and re.search(r'reason|think|effort|unknown|unrecognized|extra', text, re.I):
            del body['reasoning_effort']
            continue
        return None, text, False
    return None, 'The AI service did not answer.', False


def stream_answer(service: Dict[str, Any], model: str, history: List[Dict[str, Any]], reasoning: bool,
                  effort: str, post: Optional[Callable[..., Any]] = None) -> Iterator[Tuple[str, str]]:
    """('r' | 't' | 'note' | 'error', text) as the model answers."""
    if post is None:
        import requests
        post = requests.post
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT.format(today=datetime.now().strftime('%A, %d %B %Y'))}]
    messages += [{'role': m['role'], 'content': str(m.get('content') or '')}
                 for m in history[-MAX_CONTEXT_MESSAGES:] if m.get('role') in ('user', 'assistant')]
    res, error, no_thinking = _open(service, model, messages, reasoning, effort, post)
    if res is None:
        yield 'error', error
        return
    if no_thinking:
        yield 'note', 'This model answers without thinking first.'
    splitter = ThinkSplitter()
    try:
        for line in res.iter_lines(decode_unicode=True):
            if not line or not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if chunk.get('error'):
                err = chunk['error']
                yield 'error', str(err.get('message') if isinstance(err, dict) else err)
                return
            delta = ((chunk.get('choices') or [{}])[0] or {}).get('delta') or {}
            thinking = delta.get('reasoning_content') or delta.get('reasoning')
            if thinking:
                yield 'r', str(thinking)
            if delta.get('content'):
                yield from splitter.feed(str(delta['content']))
        yield from splitter.flush()
    finally:
        res.close()


@bp.post('/api/chat/send')
def send():
    """A message into a chat (a new one without `chat`), the answer streamed."""
    session, refused = hub_data.need_session('chat')
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    text = str(body.get('message') or '').strip()
    if not text:
        return jsonify({'error': 'Type a message.'}), 400
    if len(text) > MAX_MESSAGE:
        return jsonify({'error': 'That message is too long.'}), 400
    service, problem = connection()
    if problem:
        return jsonify({'error': problem}), 409
    offered = models(service)
    model = str(body.get('model') or (offered[0] if offered else ''))
    if model not in offered:
        return jsonify({'error': 'Choose one of the models.'}), 400
    reasoning = bool(body.get('reasoning'))
    effort = str(body.get('effort') or 'medium')
    effort = effort if effort in EFFORTS else 'medium'
    place = _own(session)
    if not place:
        return jsonify({'error': NO_PLACE}), 409

    chat_id = str(body.get('chat') or '')
    if chat_id:
        if not ID_RE.match(chat_id):
            return jsonify({'error': 'This chat is not there.'}), 404
        chat, error = hub_data.read(place, _chat_file(chat_id))
        if chat is None:
            return jsonify({'error': error}), 409
        if not chat:
            return jsonify({'error': 'This chat is not there (any more).'}), 404
    else:
        chat_id = secrets.token_urlsafe(9)
        chat = {'id': chat_id, 'title': title_for(text), 'created': _stamp(), 'messages': []}
    chat['messages'] = [m for m in chat.get('messages') or [] if isinstance(m, dict)]
    chat['messages'].append({'role': 'user', 'content': text, 'at': _stamp()})
    chat['model'], chat['updated'] = model, _stamp()
    error = _save(place, chat)
    if error:
        return jsonify({'error': error}), 409

    def lines() -> Iterator[str]:
        answer: Dict[str, Any] = {'role': 'assistant', 'content': '', 'reasoning': '', 'model': model}
        started = time.monotonic()
        thought_until: Optional[float] = None
        yield json.dumps({'chat': {'id': chat_id, 'title': chat['title']}}) + '\n'
        failed = ''
        try:
            for kind, piece in stream_answer(service, model, list(chat['messages']), reasoning, effort):
                if kind == 'error':
                    failed = piece
                    break
                if kind == 'r':
                    answer['reasoning'] += piece
                elif kind == 't':
                    if answer['reasoning'] and thought_until is None:
                        thought_until = time.monotonic()
                    answer['content'] += piece
                yield json.dumps({kind: piece}) + '\n'
        finally:
            # Also when the person pressed stop: keep what came so far.
            if answer['reasoning']:
                answer['thought_seconds'] = round((thought_until or time.monotonic()) - started)
            if failed:
                answer['error'] = failed
            answer['at'] = _stamp()
            if answer['content'] or answer['reasoning'] or failed:
                chat['messages'].append(answer)
                chat['updated'] = _stamp()
                _save(place, chat)
        yield json.dumps({'error': failed} if failed else {'done': True,
                                                           'thought_seconds': answer.get('thought_seconds')}) + '\n'

    return Response(lines(), mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
