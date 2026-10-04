// Chat in the AlvaOS Hub: talk to an AI model, like ChatGPT. Chats on the
// left, the model top left, thinking on or off and how hard in the box.
// Only a chat: it cannot see or change anything on the NAS
// (backend/hub_chat.py). The answer streams in as it is written.
(function () {
    'use strict';
    const H = window.Hub;
    if (!H) return;
    const { esc, icon, api, toast } = H;
    H.addIcons({
        'new-chat': '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z"/>',
        'chev-down': '<path d="m6 9 6 6 6-6"/>',
        'arrow-up': '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
        stop: '<rect width="12" height="12" x="6" y="6" rx="2" fill="currentColor"/>',
        bulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6M10 22h4"/>',
        check: '<path d="M20 6 9 17l-5-5"/>',
        sparkle: '<path d="M9.94 14.06A2 2 0 0 0 8.5 12.62l-6.13-1.58a.5.5 0 0 1 0-.96L8.5 8.5A2 2 0 0 0 9.94 7.06l1.58-6.13a.5.5 0 0 1 .96 0l1.58 6.13a2 2 0 0 0 1.44 1.44l6.13 1.58a.5.5 0 0 1 0 .96L15.5 12.62a2 2 0 0 0-1.44 1.44l-1.58 6.13a.5.5 0 0 1-.96 0z"/>',
        alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    });

    const store = {
        get(k, d) { try { const v = localStorage.getItem(`alvaos_chat_${k}`); return v === null ? d : JSON.parse(v); } catch (_e) { return d; } },
        set(k, v) { try { localStorage.setItem(`alvaos_chat_${k}`, JSON.stringify(v)); } catch (_e) { /* off */ } },
    };
    const EFFORTS = [['low', 'Low', 'Quick'], ['medium', 'Medium', 'Balanced'], ['high', 'High', 'Thorough']];

    let root = null;
    let built = false;
    let models = [];
    let model = store.get('model', '');
    let reasoning = store.get('reasoning', false);
    let effort = store.get('effort', 'medium');
    let chats = [];
    let problem = '';
    let chat = null;          // the open chat: {id, title, messages}
    let streaming = null;     // {controller, answer}
    let stickToBottom = true;
    const $c = (sel) => root.querySelector(sel);

    // ── Markdown, safely: everything is escaped first ──────────────────────
    function inline(text) {
        const keep = [];
        let s = esc(text).replace(/`([^`\n]+)`/g, (_, code) => { keep.push(`<code>${code}</code>`); return `\u0001${keep.length - 1}\u0001`; });
        s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`)
            .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?'"])/g, (_, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`)
            .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>')
            .replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, '$1<em>$2</em>')
            .replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
        return s.replace(/\u0001(\d+)\u0001/g, (_, i) => keep[Number(i)]);
    }
    function markdown(src) {
        const code = [];
        const text = String(src || '').replace(/```([\w+#.-]*)[^\n]*\n?([\s\S]*?)(?:```|$)/g, (_, lang, body) => {
            code.push(`<div class="ch-code"><div class="ch-code-head"><span>${esc(lang || 'code')}</span><button type="button" data-copy-code>Copy</button></div><pre><code>${esc(body.replace(/\n$/, ''))}</code></pre></div>`);
            return `\n\u0000${code.length - 1}\u0000\n`;
        });
        const lines = text.split('\n');
        const out = [];
        let para = [];
        const flush = () => { if (para.length) { out.push(`<p>${para.map(inline).join('<br>')}</p>`); para = []; } };
        for (let i = 0; i < lines.length; i += 1) {
            const line = lines[i];
            const block = /^\u0000(\d+)\u0000$/.exec(line.trim());
            if (block) { flush(); out.push(code[Number(block[1])]); continue; }
            if (!line.trim()) { flush(); continue; }
            const h = /^(#{1,6})\s+(.*)$/.exec(line);
            if (h) { flush(); const n = Math.min(4, h[1].length + 1); out.push(`<h${n}>${inline(h[2])}</h${n}>`); continue; }
            if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flush(); out.push('<hr>'); continue; }
            if (/^\s*>/.test(line)) {
                flush();
                const quote = [];
                while (i < lines.length && /^\s*>/.test(lines[i])) { quote.push(lines[i].replace(/^\s*>\s?/, '')); i += 1; }
                i -= 1;
                out.push(`<blockquote>${markdown(quote.join('\n'))}</blockquote>`);
                continue;
            }
            if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
                flush();
                const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => inline(c.trim()));
                const head = cells(line);
                i += 2;
                const rows = [];
                while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(cells(lines[i])); i += 1; }
                i -= 1;
                out.push(`<div class="ch-table"><table><thead><tr>${head.map((c) => `<th>${c}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
                continue;
            }
            const li = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line);
            if (li) {
                flush();
                const ordered = /\d/.test(li[2]);
                const items = [];
                while (i < lines.length) {
                    const m = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i]);
                    if (m && /\d/.test(m[2]) === ordered) {
                        const task = /^\[( |x)\]\s+(.*)$/i.exec(m[3]);
                        items.push({ indent: m[1].length, html: task ? `<span class="ch-tick">${task[1].trim() ? '☑' : '☐'}</span> ${inline(task[2])}` : inline(m[3]) });
                        i += 1;
                    } else if (lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && items.length) {
                        items[items.length - 1].html += `<br>${inline(lines[i].trim())}`;
                        i += 1;
                    } else break;
                }
                i -= 1;
                const start = ordered ? Number(/\d+/.exec(li[2])[0]) : 1;
                const tag = ordered ? 'ol' : 'ul';
                out.push(`<${tag}${ordered && start !== 1 ? ` start="${start}"` : ''}>${items.map((it) => `<li${it.indent >= 2 ? ' class="sub"' : ''}>${it.html}</li>`).join('')}</${tag}>`);
                continue;
            }
            para.push(line);
        }
        flush();
        return out.join('');
    }

    // ── Layout ─────────────────────────────────────────────────────────────
    function build() {
        root.innerHTML = `
            <aside class="ch-side" id="ch-side">
                <div class="ch-side-top">
                    <button type="button" class="ch-new" id="ch-new">${icon('new-chat')}<span>New chat</span></button>
                </div>
                <nav class="ch-list" id="ch-list" aria-label="Chats"></nav>
                ${H.foot()}
            </aside>
            <div class="ch-scrim" id="ch-scrim" hidden></div>
            <section class="ch-main">
                <header class="ch-bar">
                    <button type="button" class="icon-btn ch-menu" id="ch-menu" aria-label="Chats">${icon('menu')}</button>
                    <div class="ch-model-wrap">
                        <button type="button" class="ch-model" id="ch-model" aria-haspopup="listbox" aria-expanded="false"></button>
                        <div class="ch-menu-pop" id="ch-model-menu" role="listbox" hidden></div>
                    </div>
                    <button type="button" class="icon-btn ch-new-small" id="ch-new-small" aria-label="New chat" title="New chat">${icon('new-chat')}</button>
                </header>
                <div class="ch-scroll" id="ch-scroll"><div class="ch-thread" id="ch-thread"></div></div>
                <div class="ch-bottom">
                    <form class="ch-box" id="ch-box">
                        <textarea id="ch-input" rows="1" placeholder="Message" aria-label="Message" maxlength="32000"></textarea>
                        <div class="ch-tools">
                            <button type="button" class="ch-pill" id="ch-think" aria-pressed="false" title="Think first: slower, better for hard questions">${icon('bulb')}<span>Think</span></button>
                            <div class="ch-effort" id="ch-effort" role="group" aria-label="How hard it thinks" hidden>
                                ${EFFORTS.map(([v, label, hint]) => `<button type="button" data-effort="${v}" title="${hint}">${label}</button>`).join('')}
                            </div>
                            <span class="ch-grow"></span>
                            <button type="submit" class="ch-send" id="ch-send" aria-label="Send" title="Send (Enter)">${icon('arrow-up')}</button>
                        </div>
                    </form>
                    <div class="ch-disclaimer">AI can make mistakes. Check what matters. It cannot see or change anything on the NAS.</div>
                </div>
            </section>`;
        $c('#ch-new').addEventListener('click', newChat);
        $c('#ch-new-small').addEventListener('click', newChat);
        $c('#ch-menu').addEventListener('click', () => { $c('#ch-side').classList.add('open'); $c('#ch-scrim').hidden = false; });
        $c('#ch-scrim').addEventListener('click', closeSide);
        $c('#ch-list').addEventListener('click', onListClick);
        $c('#ch-model').addEventListener('click', toggleModels);
        $c('#ch-model-menu').addEventListener('click', (e) => {
            const b = e.target.closest('[data-model]');
            if (!b) return;
            model = b.dataset.model;
            store.set('model', model);
            closeModels();
            renderBar();
        });
        $c('#ch-think').addEventListener('click', () => { reasoning = !reasoning; store.set('reasoning', reasoning); renderTools(); $c('#ch-input').focus(); });
        $c('#ch-effort').addEventListener('click', (e) => {
            const b = e.target.closest('[data-effort]');
            if (b) { effort = b.dataset.effort; store.set('effort', effort); renderTools(); }
        });
        const input = $c('#ch-input');
        input.addEventListener('input', () => { grow(); renderTools(); });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
        });
        $c('#ch-box').addEventListener('submit', (e) => { e.preventDefault(); if (streaming) stop(); else send(); });
        $c('#ch-scroll').addEventListener('scroll', () => {
            const s = $c('#ch-scroll');
            stickToBottom = s.scrollHeight - s.scrollTop - s.clientHeight < 60;
        }, { passive: true });
        $c('#ch-thread').addEventListener('click', onThreadClick);
        document.addEventListener('pointerdown', (e) => {
            if (!e.target.closest('.ch-model-wrap')) closeModels();
            if (!e.target.closest('.ch-row-menu, [data-row-more]')) root.querySelectorAll('.ch-row-menu').forEach((m) => m.remove());
        });
        built = true;
    }
    function closeSide() { $c('#ch-side').classList.remove('open'); $c('#ch-scrim').hidden = true; }
    function grow() {
        const input = $c('#ch-input');
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 240)}px`;
    }

    // ── Models ─────────────────────────────────────────────────────────────
    function renderBar() {
        if (!models.includes(model)) model = models[0] || '';
        $c('#ch-model').innerHTML = `<span>${esc(model || 'Chat')}</span>${models.length > 1 ? icon('chev-down') : ''}`;
        $c('#ch-model').disabled = models.length < 2;
        $c('#ch-model').title = models.length > 1 ? 'Choose the model' : '';
    }
    function toggleModels() {
        const menu = $c('#ch-model-menu');
        if (!menu.hidden) { closeModels(); return; }
        menu.innerHTML = models.map((m) => `<button type="button" role="option" data-model="${esc(m)}" aria-selected="${m === model}"><span>${esc(m)}</span>${m === model ? icon('check') : ''}</button>`).join('');
        menu.hidden = false;
        $c('#ch-model').setAttribute('aria-expanded', 'true');
    }
    function closeModels() {
        if (!root) return;
        $c('#ch-model-menu').hidden = true;
        $c('#ch-model').setAttribute('aria-expanded', 'false');
    }

    function renderTools() {
        $c('#ch-think').setAttribute('aria-pressed', reasoning);
        $c('#ch-effort').hidden = !reasoning;
        root.querySelectorAll('[data-effort]').forEach((b) => b.setAttribute('aria-pressed', b.dataset.effort === effort));
        const empty = !$c('#ch-input').value.trim();
        const send = $c('#ch-send');
        send.innerHTML = icon(streaming ? 'stop' : 'arrow-up');
        send.setAttribute('aria-label', streaming ? 'Stop' : 'Send');
        send.title = streaming ? 'Stop' : 'Send (Enter)';
        send.disabled = !streaming && (empty || !!problem);
        $c('#ch-input').disabled = !!problem;
    }

    // ── The chats on the left ──────────────────────────────────────────────
    function groupOf(iso) {
        const d = new Date(iso);
        const today = new Date();
        const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
        const days = Math.floor((start - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
        if (days <= 0) return 'Today';
        if (days === 1) return 'Yesterday';
        if (days < 7) return 'Previous 7 days';
        if (days < 30) return 'Previous 30 days';
        return d.getFullYear() === today.getFullYear() ? d.toLocaleDateString('en-GB', { month: 'long' }) : String(d.getFullYear());
    }
    function renderList() {
        let last = '';
        $c('#ch-list').innerHTML = chats.map((c) => {
            const g = groupOf(c.updated || c.created);
            const head = g !== last ? `<div class="ch-group">${esc(g)}</div>` : '';
            last = g;
            return `${head}<div class="ch-row${chat && chat.id === c.id ? ' active' : ''}" data-chat="${esc(c.id)}">
                <button type="button" class="ch-row-open" data-open>${esc(c.title || 'New chat')}</button>
                <button type="button" class="ch-row-more" data-row-more aria-label="More for ${esc(c.title)}">${icon('more')}</button>
            </div>`;
        }).join('') || '<div class="ch-list-empty">Your chats show up here.</div>';
    }
    async function onListClick(e) {
        const row = e.target.closest('[data-chat]');
        if (!row) return;
        const id = row.dataset.chat;
        if (e.target.closest('[data-open]')) { openChat(id); closeSide(); return; }
        if (e.target.closest('[data-row-more]')) {
            root.querySelectorAll('.ch-row-menu').forEach((m) => m.remove());
            const menu = document.createElement('div');
            menu.className = 'ch-row-menu';
            menu.innerHTML = `<button type="button" data-act="rename">${icon('pen')}Rename</button><button type="button" data-act="delete" class="danger">${icon('trash')}Delete</button>`;
            row.appendChild(menu);
            menu.addEventListener('click', async (ev) => {
                const act = ev.target.closest('[data-act]');
                if (!act) return;
                menu.remove();
                if (act.dataset.act === 'rename') rename(row, id);
                else remove(id);
            });
        }
    }
    function rename(row, id) {
        const c = chats.find((x) => x.id === id);
        const open = row.querySelector('[data-open]');
        const input = document.createElement('input');
        input.className = 'ch-row-input';
        input.value = c.title || '';
        input.maxLength = 120;
        open.replaceWith(input);
        input.focus();
        input.select();
        let done = false;
        const finish = async (keep) => {
            if (done) return;
            done = true;
            const title = input.value.trim();
            if (keep && title && title !== c.title) {
                try {
                    await api(`chat/${encodeURIComponent(id)}/rename`, { method: 'POST', json: { title } });
                    c.title = title;
                    if (chat && chat.id === id) chat.title = title;
                } catch (err) { toast(err.message, 'error'); }
            }
            renderList();
        };
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') finish(true); if (e.key === 'Escape') finish(false); });
        input.addEventListener('blur', () => finish(true));
    }
    async function remove(id) {
        const c = chats.find((x) => x.id === id);
        try {
            await api(`chat/${encodeURIComponent(id)}/delete`, { method: 'POST' });
            chats = chats.filter((x) => x.id !== id);
            if (chat && chat.id === id) newChat();
            renderList();
            toast(`“${c ? c.title : 'Chat'}” deleted.`);
        } catch (err) { toast(err.message, 'error'); }
    }

    // ── One chat ───────────────────────────────────────────────────────────
    function newChat() {
        if (streaming) stop();
        chat = null;
        closeSide();
        renderThread();
        renderList();
        $c('#ch-input').focus();
    }
    async function openChat(id) {
        if (streaming) stop();
        try {
            chat = await api(`chat/${encodeURIComponent(id)}`);
            if (chat.model && models.includes(chat.model)) { model = chat.model; renderBar(); }
        } catch (err) {
            toast(err.message, 'error');
            return;
        }
        stickToBottom = true;
        renderThread();
        renderList();
        scrollDown(true);
    }

    function thinkingHtml(m, live) {
        if (!m.reasoning) return live ? '<div class="ch-think live"><span class="ch-think-label">Thinking…</span></div>' : '';
        const label = live && !m.content ? 'Thinking…' : `Thought for ${m.thought_seconds > 1 ? `${m.thought_seconds} seconds` : 'a moment'}`;
        return `<details class="ch-think${live && !m.content ? ' live' : ''}"${live && !m.content ? ' open' : ''}>
            <summary><span class="ch-think-label">${label}</span>${icon('chev-down')}</summary>
            <div class="ch-think-text">${esc(m.reasoning)}</div></details>`;
    }
    function messageHtml(m, i, live) {
        if (m.role === 'user') return `<div class="ch-msg user"><div class="ch-bubble">${esc(m.content)}</div></div>`;
        const body = m.content ? markdown(m.content) : '';
        return `<div class="ch-msg ai" data-i="${i}">
            <div class="ch-avatar">${icon('sparkle')}</div>
            <div class="ch-ai">
                ${thinkingHtml(m, live)}
                <div class="ch-md">${body}${live && m.content ? '<span class="ch-caret"></span>' : ''}</div>
                ${m.error ? `<div class="ch-error">${icon('alert')}<span>${esc(m.error)}</span></div>` : ''}
                ${live || !m.content ? '' : `<div class="ch-actions"><button type="button" class="ch-act" data-copy="${i}" title="Copy">${icon('copy')}</button>${m.model ? `<span class="ch-meta">${esc(m.model)}</span>` : ''}</div>`}
            </div>
        </div>`;
    }
    function renderThread() {
        const thread = $c('#ch-thread');
        const messages = chat ? chat.messages || [] : [];
        root.classList.toggle('is-empty', !messages.length && !problem);
        if (problem) {
            thread.innerHTML = `<div class="ch-hello"><div class="ch-hello-ic">${icon('alert')}</div><h2>Chat is not ready yet</h2><p>${esc(problem)}</p></div>`;
        } else if (!messages.length) {
            thread.innerHTML = '<div class="ch-hello"><h2>What can I help with?</h2></div>';
        } else {
            thread.innerHTML = messages.map((m, i) => messageHtml(m, i, streaming && i === messages.length - 1 && m.role === 'assistant')).join('');
        }
        renderTools();
    }
    function paintLive() {
        // Only the last message changes while it streams.
        const messages = chat.messages;
        const i = messages.length - 1;
        const el = $c(`.ch-msg.ai[data-i="${i}"]`);
        const html = messageHtml(messages[i], i, !!streaming);
        if (el) el.outerHTML = html; else $c('#ch-thread').insertAdjacentHTML('beforeend', html);
        scrollDown();
    }
    let paintQueued = false;
    function schedulePaint() {
        if (paintQueued) return;
        paintQueued = true;
        requestAnimationFrame(() => { paintQueued = false; if (chat) paintLive(); });
    }
    function scrollDown(force) {
        const s = $c('#ch-scroll');
        if (force || stickToBottom) s.scrollTop = s.scrollHeight;
    }

    async function onThreadClick(e) {
        const copyBtn = e.target.closest('[data-copy]');
        const codeBtn = e.target.closest('[data-copy-code]');
        let text = '';
        if (copyBtn) text = (chat.messages[Number(copyBtn.dataset.copy)] || {}).content || '';
        else if (codeBtn) text = codeBtn.closest('.ch-code').querySelector('code').textContent;
        else return;
        try {
            await navigator.clipboard.writeText(text);
        } catch (_e) {
            const area = document.createElement('textarea');
            area.value = text;
            document.body.appendChild(area);
            area.select();
            document.execCommand('copy');
            area.remove();
        }
        const btn = copyBtn || codeBtn;
        const before = btn.innerHTML;
        btn.innerHTML = codeBtn ? 'Copied' : icon('check');
        setTimeout(() => { btn.innerHTML = before; }, 1200);
    }

    // ── Sending and the streamed answer ────────────────────────────────────
    async function send() {
        const input = $c('#ch-input');
        const text = input.value.trim();
        if (!text || streaming || problem) return;
        input.value = '';
        grow();
        if (!chat) chat = { id: '', title: '', messages: [] };
        chat.messages.push({ role: 'user', content: text });
        const answer = { role: 'assistant', content: '', reasoning: '', model };
        chat.messages.push(answer);
        const controller = new AbortController();
        streaming = { controller, answer };
        stickToBottom = true;
        renderThread();
        scrollDown(true);
        const started = Date.now();
        let firstText = 0;
        try {
            const res = await fetch('/api/chat/send', {
                method: 'POST', credentials: 'same-origin', signal: controller.signal,
                headers: { 'Content-Type': 'application/json', 'X-AlvaOS-Files': '1' },
                body: JSON.stringify({ chat: chat.id || null, message: text, model, reasoning, effort }),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                if (res.status === 401 && data.signed_out) { H.signOut(); return; }
                throw new Error(data.error || 'That did not work.');
            }
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            for (;;) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                let nl;
                while ((nl = buffer.indexOf('\n')) >= 0) {
                    const line = buffer.slice(0, nl).trim();
                    buffer = buffer.slice(nl + 1);
                    if (!line) continue;
                    let msg;
                    try { msg = JSON.parse(line); } catch (_e) { continue; }
                    if (msg.chat) {
                        const isNew = !chat.id;
                        chat.id = msg.chat.id;
                        chat.title = msg.chat.title;
                        const row = chats.find((c) => c.id === chat.id);
                        const now = new Date().toISOString();
                        if (row) { row.updated = now; chats = [row, ...chats.filter((c) => c !== row)]; } else chats.unshift({ id: chat.id, title: chat.title, updated: now, model });
                        if (isNew || row) renderList();
                    } else if (msg.r) { answer.reasoning += msg.r; schedulePaint(); }
                    else if (msg.t) {
                        if (!firstText) { firstText = Date.now(); if (answer.reasoning) answer.thought_seconds = Math.round((firstText - started) / 1000); }
                        answer.content += msg.t;
                        schedulePaint();
                    } else if (msg.note) toast(msg.note);
                    else if (msg.error) answer.error = msg.error;
                    else if (msg.done && msg.thought_seconds !== undefined && msg.thought_seconds !== null) answer.thought_seconds = msg.thought_seconds;
                }
            }
        } catch (err) {
            if (err.name !== 'AbortError') answer.error = err.message;
        } finally {
            if (answer.reasoning && answer.thought_seconds === undefined) answer.thought_seconds = Math.round(((firstText || Date.now()) - started) / 1000);
            if (!answer.content && !answer.reasoning && !answer.error) chat.messages.pop();   // stopped before anything came
            streaming = null;
            renderThread();
            scrollDown();
        }
    }
    function stop() {
        if (streaming) streaming.controller.abort();
    }

    // ── Start ──────────────────────────────────────────────────────────────
    async function refresh() {
        try {
            const got = await api('chat');
            models = got.models || [];
            chats = got.chats || [];
            problem = got.problem || '';
        } catch (err) {
            problem = err.message;
        }
        renderBar();
        renderList();
        renderThread();
    }

    window.addEventListener('hub-signout', () => {
        stop();
        chat = null;
        chats = [];
        models = [];
        if (built) { renderList(); renderThread(); }
    });

    window.HubApps = window.HubApps || {};
    window.HubApps.chat = {
        show(el) {
            root = el;
            if (!built) build();
            renderTools();
            renderList();
            renderThread();
            refresh();
            if (!window.matchMedia('(max-width: 760px)').matches) setTimeout(() => $c('#ch-input').focus(), 0);
        },
    };
})();
