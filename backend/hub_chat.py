#!/usr/bin/env python3
"""Chat in the AlvaOS Hub: talk to an AI model, like ChatGPT.

Only a chat: no tools, nothing on the NAS is read or changed (that is the
assistant in the admin pages, ai_assistant.py, a different thing). It uses
the AI services set up in Settings › AI (address, key, models); keys never
leave the NAS. On the Hub page the admin chooses which models Chat offers
(all, only some, all but some; whole services or single models), and may
narrow that further for single people.

Each person's chats are files in their own place (hub_data.py), by default
".alvaos/chat/" in their personal folder: chats.json (the list) and
chat-<id>.json per chat. Chats not used for 30 days (the admin can change
it) go, unless pinned or kept. A private chat is never written anywhere:
the page sends what was said so far with each message.

Answers stream to the page as lines of JSON: {"chat": {...}} first, then
{"r": "..."} (thinking) and {"t": "..."} (the answer) as they come, and
{"done": true} or {"error": "..."} at the end.
"""

import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from flask import Blueprint, Response, jsonify, request

import ai_assistant
import hub_apps
import hub_data
import web_search

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


# Models that refused to think although the service said they could: no
# "Think" for them any more (until the Hub restarts).
_cannot_think: set = set()


def catalog(ai: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every model of every AI service AlvaOS can talk to."""
    ai = ai or ai_assistant.load_settings()
    return [{'id': f"{c['id']}/{m}", 'model': m, 'service': c['name'], 'connection': c['id'],
             'thinking': 'none' if f"{c['id']}/{m}" in _cannot_think else ai_assistant.thinking_of(c, m)}
            for c in ai['connections'] if ai_assistant.usable(c) for m in c['models']]


def _apply(items: List[Dict[str, Any]], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    refs = set(rule.get('items') or [])

    def listed(item: Dict[str, Any]) -> bool:
        return item['connection'] in refs or item['id'] in refs
    if rule.get('mode') == 'only':
        return [i for i in items if listed(i)]
    if rule.get('mode') == 'except':
        return [i for i in items if not listed(i)]
    return items


def offered(user: str, ai: Optional[Dict[str, Any]] = None,
            hub: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], str]:
    """The models this person may choose, and why there are none ('' when there are)."""
    ai = ai or ai_assistant.load_settings()
    entry = (hub or hub_apps.load())['apps']['chat']
    every = catalog(ai)
    if not every:
        if not any(ai_assistant.usable(c) for c in ai['connections']):
            return [], 'No AI service is set up yet. Ask the person who looks after the NAS (Settings › AI).'
        return [], 'The AI service has no models yet. Ask the person who looks after the NAS (Settings › AI).'
    items = _apply(every, entry['access'])
    if user in entry['people_access']:
        items = _apply(items, entry['people_access'][user])
    return items, '' if items else 'No model is open to you. Ask the person who looks after the NAS.'


def may_search(user: str, ai: Optional[Dict[str, Any]] = None, hub: Optional[Dict[str, Any]] = None) -> bool:
    """Web search is on (Settings › AI) and open to this person (Hub page)."""
    ai = ai or ai_assistant.load_settings()
    if not ai.get('search', {}).get('enabled'):
        return False
    people = (hub or hub_apps.load())['apps']['chat'].get('search_people')
    return people is None or user in people


def search_query(service: Dict[str, Any], model: str, history: List[Dict[str, Any]], text: str,
                 post: Optional[Callable[..., Any]] = None) -> str:
    """What to search for. A follow-up ("and tomorrow?") needs the chat
    before it: then the model writes the query; else the question is it."""
    if not any(m.get('role') == 'assistant' for m in history):
        return text
    if post is None:
        import requests
        post = requests.post
    said = '\n'.join(f"{m['role']}: {str(m.get('content') or '')[:600]}" for m in history[-6:]
                     if m.get('role') in ('user', 'assistant'))
    headers = {'Content-Type': 'application/json'}
    if service.get('api_key'):
        headers['Authorization'] = f"Bearer {service['api_key']}"
    body = {'model': model, 'stream': False, 'temperature': 0, 'messages': [
        {'role': 'system', 'content': 'Write one short web search query (at most 12 words) that finds what the '
                                      'last user message asks, using the chat for context. Only the query.'},
        {'role': 'user', 'content': f'{said}\nuser: {text}'}]}
    if service['provider'].startswith('ollama'):
        body['reasoning_effort'] = 'none'
    try:
        res = post(service['base_url'].rstrip('/') + '/chat/completions', headers=headers, json=body, timeout=(10, 30))
        content = str((((res.json().get('choices') or [{}])[0] or {}).get('message') or {}).get('content') or '')
    except Exception:  # noqa: BLE001 - then the question itself
        return text
    lines = re.sub(r'<think>.*?</think>', '', content, flags=re.S).strip().strip('"').splitlines()
    return (lines[0].strip() if lines and lines[0].strip() else text)[:web_search.MAX_QUERY]


def summary() -> Dict[str, Any]:
    """For the admin's Hub page: the services and their models, without keys."""
    from urllib.parse import urlparse
    ai = ai_assistant.load_settings()
    services = [{'id': c['id'], 'name': c['name'], 'host': urlparse(c['base_url']).netloc,
                 'ready': ai_assistant.usable(c), 'models': c['models']} for c in ai['connections']]
    return {'ready': bool(catalog(ai)), 'services': services, 'search': bool(ai.get('search', {}).get('enabled'))}


def title_for(message: str) -> str:
    words = re.sub(r'\s+', ' ', message).strip()
    return words if len(words) <= 60 else words[:57].rsplit(' ', 1)[0] + '…'


def _own(session: Dict[str, Any]) -> Optional[hub_data.Place]:
    return hub_data.own_place('chat', session)


NO_PLACE = ('You have no personal folder yet, so your chats have nowhere to go. '
            'Ask the person who looks after the NAS to make you one.')


def _row(chat: Dict[str, Any]) -> Dict[str, Any]:
    return {k: chat.get(k) for k in ('id', 'title', 'created', 'updated', 'model', 'pinned', 'kept')}


def _expire(place: hub_data.Place, days: int) -> None:
    """Chats not used for `days` go, unless pinned or kept."""
    if days <= 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec='seconds')
    with hub_data.lock(place, INDEX):
        index, _ = hub_data.read(place, INDEX)
        rows = [c for c in (index or {}).get('chats') or [] if isinstance(c, dict)]
        old = [c for c in rows if not c.get('pinned') and not c.get('kept')
               and str(c.get('updated') or c.get('created') or '') < cutoff]
        if not old or index is None:
            return
        if hub_data.write(place, INDEX, {'chats': [c for c in rows if c not in old]}):
            return
        for c in old:
            if ID_RE.match(str(c.get('id'))):
                hub_data.delete(place, _chat_file(str(c['id'])))


@bp.get('/api/chat')
def overview():
    """The models to choose from, and this person's chats, newest first."""
    session, refused = hub_data.need_session('chat')
    if refused:
        return refused
    hub = hub_apps.load()
    items, problem = offered(session['user'], hub=hub)
    days = hub['apps']['chat']['keep_days']
    place = _own(session)
    chats: List[Dict[str, Any]] = []
    if place:
        _expire(place, days)
        index, error = hub_data.read(place, INDEX)
        if index is None:
            problem = problem or error
        else:
            chats = [c for c in index.get('chats') or [] if isinstance(c, dict) and ID_RE.match(str(c.get('id')))]
    else:
        problem = problem or NO_PLACE
    chats.sort(key=lambda c: str(c.get('updated') or ''), reverse=True)
    return jsonify({'models': [{k: i[k] for k in ('id', 'model', 'service', 'thinking')} for i in items],
                    'chats': chats, 'problem': problem, 'keep_days': days,
                    'search': may_search(session['user'], hub=hub)})


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
        rows.append(_row(chat))
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


def _flag(chat_id: str, key: str):
    session, refused = hub_data.need_session('chat')
    if refused:
        return refused
    on = bool((request.get_json(silent=True) or {}).get(key))
    place = _own(session)
    if not place or not ID_RE.match(chat_id):
        return jsonify({'error': 'This chat is not there.'}), 404
    with hub_data.lock(place, _chat_file(chat_id)):
        chat, error = hub_data.read(place, _chat_file(chat_id))
        if not chat:
            return jsonify({'error': error or 'This chat is not there (any more).'}), 404
        chat[key] = on
        error = _save(place, chat)
    return (jsonify({'error': error}), 409) if error else jsonify({'success': True, key: on})


@bp.post('/api/chat/<chat_id>/pin')
def pin(chat_id: str):
    """Pinned: on top of the list, and never deleted by itself."""
    return _flag(chat_id, 'pinned')


@bp.post('/api/chat/<chat_id>/keep')
def keep(chat_id: str):
    """Kept: stays where it is in the list, and is never deleted by itself."""
    return _flag(chat_id, 'kept')


@bp.post('/api/chat/burn')
def burn():
    """The flame: every chat goes at once, except pinned and kept ones unless `everything`."""
    session, refused = hub_data.need_session('chat')
    if refused:
        return refused
    everything = bool((request.get_json(silent=True) or {}).get('everything'))
    place = _own(session)
    if not place:
        return jsonify({'error': NO_PLACE}), 409
    with hub_data.lock(place, INDEX):
        index, error = hub_data.read(place, INDEX)
        if index is None:
            return jsonify({'error': error}), 409
        rows = [c for c in index.get('chats') or [] if isinstance(c, dict)]
        stay = [] if everything else [c for c in rows if c.get('pinned') or c.get('kept')]
        gone = [c for c in rows if c not in stay]
        error = hub_data.write(place, INDEX, {'chats': stay})
        if not error:
            for c in gone:
                if ID_RE.match(str(c.get('id'))):
                    hub_data.delete(place, _chat_file(str(c['id'])))
    return (jsonify({'error': error}), 409) if error else jsonify({'success': True, 'deleted': len(gone),
                                                                    'stayed': len(stay)})


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
                  effort: str, post: Optional[Callable[..., Any]] = None,
                  found: str = '') -> Iterator[Tuple[str, str]]:
    """('r' | 't' | 'note' | 'error', text) as the model answers. `found`:
    web search results, given just before the last question."""
    if post is None:
        import requests
        post = requests.post
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT.format(today=datetime.now().strftime('%A, %d %B %Y'))}]
    messages += [{'role': m['role'], 'content': str(m.get('content') or '')}
                 for m in history[-MAX_CONTEXT_MESSAGES:] if m.get('role') in ('user', 'assistant')]
    if found:
        messages.insert(len(messages) - 1, {'role': 'system', 'content': found})
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


def _private_history(raw: Any) -> List[Dict[str, Any]]:
    """What was said so far in a private chat, as the page sends it."""
    out = []
    for m in (raw if isinstance(raw, list) else [])[-MAX_CONTEXT_MESSAGES:]:
        if isinstance(m, dict) and m.get('role') in ('user', 'assistant'):
            out.append({'role': m['role'], 'content': str(m.get('content') or '')[:MAX_MESSAGE * 2]})
    return out


@bp.post('/api/chat/send')
def send():
    """A message into a chat (a new one without `chat`), the answer streamed.
    With `private` nothing is kept: the page sends `history` itself."""
    session, refused = hub_data.need_session('chat')
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    text = str(body.get('message') or '').strip()
    if not text:
        return jsonify({'error': 'Type a message.'}), 400
    if len(text) > MAX_MESSAGE:
        return jsonify({'error': 'That message is too long.'}), 400
    ai = ai_assistant.load_settings()
    items, problem = offered(session['user'], ai=ai)
    if problem:
        return jsonify({'error': problem}), 409
    wanted = str(body.get('model') or items[0]['id'])
    item = next((i for i in items if i['id'] == wanted), None)
    if item is None:
        return jsonify({'error': 'Choose one of the models.'}), 400
    conn = next(c for c in ai['connections'] if c['id'] == item['connection'])
    service = {k: conn[k] for k in ('provider', 'base_url', 'api_key')}
    model = item['model']
    # Thinking only where the model can; how hard only where it can be told.
    reasoning = bool(body.get('reasoning')) and item['thinking'] != 'none'
    effort = str(body.get('effort') or 'medium')
    effort = effort if effort in EFFORTS and item['thinking'] == 'effort' else 'medium'
    private = bool(body.get('private'))
    search = bool(body.get('search')) and may_search(session['user'], ai=ai)

    place = None if private else _own(session)
    if not private and not place:
        return jsonify({'error': NO_PLACE}), 409
    chat_id = '' if private else str(body.get('chat') or '')
    if private:
        chat: Dict[str, Any] = {'id': '', 'title': title_for(text), 'messages': _private_history(body.get('history'))}
    elif chat_id:
        if not ID_RE.match(chat_id) or place is None:
            return jsonify({'error': 'This chat is not there.'}), 404
        found, error = hub_data.read(place, _chat_file(chat_id))
        if found is None:
            return jsonify({'error': error}), 409
        if not found:
            return jsonify({'error': 'This chat is not there (any more).'}), 404
        chat = found
    else:
        chat_id = secrets.token_urlsafe(9)
        chat = {'id': chat_id, 'title': title_for(text), 'created': _stamp(), 'messages': []}
    chat['messages'] = [m for m in chat.get('messages') or [] if isinstance(m, dict)]
    chat['messages'].append({'role': 'user', 'content': text, 'at': _stamp()})
    chat['model'], chat['updated'] = wanted, _stamp()
    if place:
        error = _save(place, chat)
        if error:
            return jsonify({'error': error}), 409

    def lines() -> Iterator[str]:
        answer: Dict[str, Any] = {'role': 'assistant', 'content': '', 'reasoning': '', 'model': model,
                                  'service': item['service']}
        started = time.monotonic()
        thought_until: Optional[float] = None
        yield json.dumps({'chat': {'id': chat_id, 'title': chat['title'], 'private': private}}) + '\n'
        failed = ''
        found = ''
        if search:
            query = search_query(service, model, chat['messages'][:-1], text)
            yield json.dumps({'searching': query}) + '\n'
            results, why = web_search.gather(query)
            if results:
                answer['sources'] = [{'title': r['title'], 'url': r['url']} for r in results]
                answer['searched'] = query
                found = web_search.context(results)
                yield json.dumps({'sources': answer['sources']}) + '\n'
            else:
                yield json.dumps({'note': f'No web search this time: {why}'}) + '\n'
        try:
            for kind, piece in stream_answer(service, model, list(chat['messages']), reasoning, effort, found=found):
                if kind == 'error':
                    failed = piece
                    break
                if kind == 'note':
                    _cannot_think.add(item['id'])
                if kind == 'r':
                    answer['reasoning'] += piece
                elif kind == 't':
                    if answer['reasoning'] and thought_until is None:
                        thought_until = time.monotonic()
                    answer['content'] += piece
                yield json.dumps({kind: piece}) + '\n'
        finally:
            # Also when the person pressed stop: keep what came so far (not in a private chat).
            if answer['reasoning']:
                answer['thought_seconds'] = round((thought_until or time.monotonic()) - started)
            if failed:
                answer['error'] = failed
            answer['at'] = _stamp()
            if place and (answer['content'] or answer['reasoning'] or failed):
                chat['messages'].append(answer)
                chat['updated'] = _stamp()
                _save(place, chat)
        yield json.dumps({'error': failed} if failed else {'done': True,
                                                           'thought_seconds': answer.get('thought_seconds')}) + '\n'

    return Response(lines(), mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
