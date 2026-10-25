// Chat in the AlvaOS Hub: talk to an AI model, like ChatGPT. Chats on the
// left (pinned on top; the flame deletes them all), the model top left
// (grouped by AI service), thinking on or off and how hard in the box, but
// only where the model can. A private chat is kept nowhere. Only a chat: it
// cannot see or change anything on the NAS (backend/hub_chat.py). The
// answer streams in as it is written.
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
        flame: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
        bookmark: '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/>',
        globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
        ghost: '<path d="M9 10h.01M15 10h.01"/><path d="M12 2a8 8 0 0 0-8 8v12l3-3 2.5 2.5L12 19l2.5 2.5L17 19l3 3V10a8 8 0 0 0-8-8z"/>',
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
    let keepDays = 30;
    let privateMode = false;
    let canSearch = false;    // web search is on and open to this person
    let searchOn = store.get('search', false);
    let cites = null;         // the sources of the answer being drawn: [1] becomes a link  // the open chat is private: kept nowhere, gone when left
    let chat = null;          // the open chat: {id, title, messages}
    let streaming = null;     // {controller, answer}
    let stickToBottom = true;
    const $c = (sel) => root.querySelector(sel);

    // ── Markdown, safely: everything is escaped first ──────────────────────
    // A small block parser (fenced code, headings, quotes, tables, nested and
    // loose lists, task lists, maths blocks) and inline marks. Nothing the
    // model writes becomes HTML of its own; pictures stay links.
    const LINK = (url, label) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    function inline(text) {
        const keep = [];
        const hold = (html) => { keep.push(html); return `\u0001${keep.length - 1}\u0001`; };
        let s = String(text)
            .replace(/\\([\\`*_{}[\]()#+\-.!|~>$])/g, (_, c) => hold(esc(c)))
            .replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, (_, _t, code) => hold(`<code>${esc(code.trim() || code)}</code>`));
        if (cites) {
            s = s.replace(/\[(\d{1,2})\](?!\()/g, (all, n) => {
                const src = cites[Number(n) - 1];
                return src ? hold(`<a class="ch-cite" href="${esc(src.url)}" target="_blank" rel="noopener noreferrer" title="${esc(src.title)}">${n}</a>`) : all;
            });
        }
        s = esc(s)
            .replace(/!\[([^\]\n]*)\]\((https?:\/\/[^\s)]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (_, alt, url) => hold(LINK(url, alt || url)))
            .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (_, label, url) => hold(LINK(url, label)))
            .replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, (_, url) => hold(LINK(url, url)))
            .replace(/(^|[\s(])(https?:\/\/[^\s<)\u0001]+[^\s<).,;:!?'"\u0001])/g, (_, pre, url) => pre + hold(LINK(url, url)))
            .replace(/\*\*\*(?=\S)([^\n]*?\S)\*\*\*/g, '<strong><em>$1</em></strong>')
            .replace(/\*\*(?=\S)([^\n]*?\S)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^\w])__(?=\S)([^\n]*?\S)__(?!\w)/g, '$1<strong>$2</strong>')
            .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/g, '$1<em>$2</em>')
            .replace(/(^|[^_\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?![_\w])/g, '$1<em>$2</em>')
            .replace(/~~(?=\S)([^~\n]*?\S)~~/g, '<del>$1</del>')
            .replace(/==(?=\S)([^=\n]*?\S)==/g, '<mark>$1</mark>');
        for (let n = 0; n < 3 && s.includes('\u0001'); n += 1) s = s.replace(/\u0001(\d+)\u0001/g, (_, i) => keep[Number(i)]);
        return s;
    }
    const RE = {
        fence: /^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)/,
        heading: /^ {0,3}(#{1,6})(?:\s+(.*?))?\s*#*\s*$/,
        hr: /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/,
        quote: /^ {0,3}>/,
        item: /^(\s*)([-*+•]|\d{1,9}[.)])(\s+|$)(.*)$/,
        sep: /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/,
        math: /^\s*(\$\$|\\\[)/,
    };
    const indentOf = (l) => l.replace(/\t/g, '    ').match(/^ */)[0].length;
    const strip = (l, n) => { let t = l.replace(/\t/g, '    '); let k = 0; while (k < n && t[k] === ' ') k += 1; return t.slice(k); };
    function cells(line) {
        const out = [];
        let cur = '';
        let tick = false;
        const t = line.trim().replace(/^\|/, '').replace(/(?<!\\)\|$/, '');
        for (let k = 0; k < t.length; k += 1) {
            const c = t[k];
            if (c === '`') tick = !tick;
            if (c === '\\' && t[k + 1] === '|') { cur += '|'; k += 1; continue; }
            if (c === '|' && !tick) { out.push(cur.trim()); cur = ''; continue; }
            cur += c;
        }
        out.push(cur.trim());
        return out;
    }
    function startsBlock(line, next) {
        return RE.fence.test(line) || RE.heading.test(line) || RE.hr.test(line) || RE.quote.test(line) || RE.math.test(line)
            || (/^\s*([-*+•]|1[.)])\s+\S/.test(line)) || (line.includes('|') && next !== undefined && RE.sep.test(next) && next.includes('-'));
    }
    function codeBlock(lang, body) {
        return `<div class="ch-code"><div class="ch-code-head"><span>${esc(lang || 'code')}</span><button type="button" data-copy-code>Copy</button></div><pre><code>${esc(body)}</code></pre></div>`;
    }
    function list(lines, i) {
        const first = RE.item.exec(lines[i]);
        const base = indentOf(first[1]);
        const ordered = /\d/.test(first[2]);
        const items = [];
        let loose = false;
        while (i < lines.length) {
            const m = RE.item.exec(lines[i]);
            if (!m || indentOf(m[1]) > base + 3 || indentOf(m[1]) < base || /\d/.test(m[2]) !== ordered) break;
            const inner = indentOf(m[1]) + m[2].length + Math.min(Math.max(m[3].length, 1), 4);
            const body = [m[4]];
            let gap = false;
            i += 1;
            while (i < lines.length) {
                const l = lines[i];
                if (!l.trim()) {
                    let j = i + 1;
                    while (j < lines.length && !lines[j].trim()) j += 1;
                    if (j < lines.length && indentOf(lines[j]) >= Math.min(inner, base + 2) && !(RE.item.test(lines[j]) && indentOf(RE.item.exec(lines[j])[1]) <= base + 1)) {
                        body.push('');
                        gap = true;
                        i += 1;
                        continue;
                    }
                    break;
                }
                const sub = RE.item.exec(l);
                if (sub && indentOf(sub[1]) <= base + 1) break;
                if (indentOf(l) >= Math.min(inner, base + 2)) { body.push(strip(l, Math.min(indentOf(l), inner))); i += 1; continue; }
                if (!gap && !startsBlock(l, lines[i + 1])) { body.push(l.trim()); i += 1; continue; }   // a lazy line of the text
                break;
            }
            if (gap && body.some((b, k) => !b && k < body.length - 1)) loose = true;
            const task = /^\[( |x|X)\]\s+/.exec(body[0]);
            if (task) body[0] = body[0].slice(task[0].length);
            items.push({ html: blocks(body, !gap), task: task ? task[1].trim() !== '' : null, num: ordered ? Number(/\d+/.exec(m[2])[0]) : 0 });
            // Blank lines between items: the list goes on.
            let j = i;
            while (j < lines.length && !lines[j].trim()) j += 1;
            const next = j < lines.length ? RE.item.exec(lines[j]) : null;
            if (j > i && next && indentOf(next[1]) >= base && indentOf(next[1]) <= base + 3 && /\d/.test(next[2]) === ordered) { loose = true; i = j; }
        }
        const tag = ordered ? 'ol' : 'ul';
        const start = ordered && items[0].num !== 1 ? ` start="${items[0].num}"` : '';
        const html = `<${tag}${start}${loose ? ' class="loose"' : ''}>${items.map((it) => (it.task === null
            ? `<li>${it.html}</li>`
            : `<li class="ch-task"><span class="ch-tick${it.task ? ' done' : ''}" aria-label="${it.task ? 'done' : 'open'}"></span><div>${it.html}</div></li>`)).join('')}</${tag}>`;
        return [html, i];
    }
    function blocks(lines, tight) {
        const out = [];
        let para = [];
        const flush = () => {
            if (!para.length) return;
            const html = para.map((l) => inline(l.trim())).join('<br>');
            out.push(tight && !out.length ? html : `<p>${html}</p>`);
            para = [];
        };
        for (let i = 0; i < lines.length; i += 1) {
            const line = lines[i];
            if (!line.trim()) { flush(); tight = false; continue; }
            const fence = RE.fence.exec(line);
            if (fence) {
                flush();
                const pad = indentOf(line);
                const body = [];
                i += 1;
                while (i < lines.length && !new RegExp(`^ {0,3}${fence[1][0]}{${fence[1].length},}\\s*$`).test(lines[i])) { body.push(strip(lines[i], pad)); i += 1; }
                out.push(codeBlock(fence[2], body.join('\n')));
                continue;
            }
            if (RE.math.test(line)) {
                flush();
                const end = /\$\$/.test(line.trim().slice(0, 2)) ? '$$' : '\\]';
                let text = line.trim().slice(2);
                while (!text.includes(end) && i + 1 < lines.length) { i += 1; text += `\n${lines[i]}`; }
                out.push(`<div class="ch-math">${esc(text.replace(end, '').trim())}</div>`);
                continue;
            }
            const h = RE.heading.exec(line);
            if (h && (h[2] || '').trim()) { flush(); const n = Math.min(5, h[1].length + 1); out.push(`<h${n}>${inline(h[2])}</h${n}>`); continue; }
            if (RE.hr.test(line) && !(para.length && /^\s*-+\s*$/.test(line))) { flush(); out.push('<hr>'); continue; }
            if (para.length && /^\s*(=+|-+)\s*$/.test(line)) {   // a title underlined
                const n = line.trim()[0] === '=' ? 2 : 3;
                const text = para.pop();
                flush();
                out.push(`<h${n}>${inline(text.trim())}</h${n}>`);
                continue;
            }
            if (RE.quote.test(line)) {
                flush();
                const quote = [];
                while (i < lines.length && lines[i].trim() && (RE.quote.test(lines[i]) || quote.length)) {
                    if (!RE.quote.test(lines[i]) && startsBlock(lines[i], lines[i + 1])) break;
                    quote.push(lines[i].replace(/^ {0,3}>\s?/, ''));
                    i += 1;
                }
                i -= 1;
                out.push(`<blockquote>${blocks(quote, false)}</blockquote>`);
                continue;
            }
            if (line.includes('|') && i + 1 < lines.length && RE.sep.test(lines[i + 1]) && lines[i + 1].includes('-')) {
                flush();
                const head = cells(line);
                const align = cells(lines[i + 1]).map((c) => (/^:-+:$/.test(c) ? 'center' : /-:$/.test(c) ? 'right' : ''));
                const td = (tag, c, k) => `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ''}>${inline(c)}</${tag}>`;
                i += 2;
                const rows = [];
                while (i < lines.length && lines[i].trim() && lines[i].includes('|')) { rows.push(cells(lines[i])); i += 1; }
                i -= 1;
                out.push(`<div class="ch-table"><table><thead><tr>${head.map((c, k) => td('th', c, k)).join('')}</tr></thead><tbody>${
                    rows.map((r) => `<tr>${head.map((_, k) => td('td', r[k] || '', k)).join('')}</tr>`).join('')}</tbody></table></div>`);
                continue;
            }
            if (RE.item.test(line) && RE.item.exec(line)[4].trim() !== '' && (!para.length || /^\s*([-*+•]|1[.)])\s/.test(line))) {
                flush();
                const [html, next] = list(lines, i);
                out.push(html);
                i = next - 1;
                continue;
            }
            para.push(line);
        }
        flush();
        return out.join('');
    }
    function markdown(src) {
        return blocks(String(src || '').replace(/\r\n?/g, '\n').split('\n'), false);
    }

    // ── Layout ─────────────────────────────────────────────────────────────
    function build() {
        root.innerHTML = `
            <aside class="ch-side" id="ch-side">
                <div class="ch-side-top">
                    <button type="button" class="ch-new" id="ch-new">${icon('new-chat')}<span>New chat</span></button>
                    <button type="button" class="ch-burn-btn" id="ch-burn" aria-label="Delete all chats" title="Delete all chats">${icon('flame')}</button>
                </div>
                <nav class="ch-list" id="ch-list" aria-label="Chats"></nav>
                <div class="ch-keep-note" id="ch-keep-note"></div>
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
                    <span class="ch-grow"></span>
                    <button type="button" class="ch-private" id="ch-private" aria-pressed="false" title="Private chat: kept nowhere, gone when you leave it">${icon('ghost')}<span>Private</span></button>
                    <button type="button" class="icon-btn ch-new-small" id="ch-new-small" aria-label="New chat" title="New chat">${icon('new-chat')}</button>
                </header>
                <div class="ch-scroll" id="ch-scroll"><div class="ch-thread" id="ch-thread"></div></div>
                <div class="ch-bottom">
                    <form class="ch-box" id="ch-box">
                        <textarea id="ch-input" rows="1" placeholder="Message" aria-label="Message" maxlength="32000"></textarea>
                        <div class="ch-tools">
                            <button type="button" class="ch-pill" id="ch-search" aria-pressed="false" title="Search the web first (DuckDuckGo) and answer with sources" hidden>${icon('globe')}<span>Search</span></button>
                            <button type="button" class="ch-pill" id="ch-think" aria-pressed="false" title="Think first: slower, better for hard questions" hidden>${icon('bulb')}<span>Think</span></button>
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
        $c('#ch-burn').addEventListener('click', askBurn);
        $c('#ch-private').addEventListener('click', () => {
            const on = !privateMode;
            newChat();
            privateMode = on;
            renderThread();
            renderTools();
            $c('#ch-input').focus();
        });
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
            renderTools();
            closeModels();
            renderBar();
        });
        $c('#ch-search').addEventListener('click', () => { searchOn = !searchOn; store.set('search', searchOn); renderTools(); $c('#ch-input').focus(); });
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
    // {id: 'service/model', model, service, thinking: 'none' | 'on' | 'effort'}
    const current = () => models.find((m) => m.id === model);
    const manyServices = () => new Set(models.map((m) => m.service)).size > 1;
    function renderBar() {
        if (!current()) model = (models.find((m) => m.model === model) || models[0] || {}).id || '';
        const m = current();
        $c('#ch-model').innerHTML = `<span>${esc(m ? m.model : 'Chat')}</span>${m && manyServices() ? `<small>${esc(m.service)}</small>` : ''}${models.length > 1 ? icon('chev-down') : ''}`;
        $c('#ch-model').disabled = models.length < 2;
        $c('#ch-model').title = models.length > 1 ? 'Choose the model' : '';
    }
    function toggleModels() {
        const menu = $c('#ch-model-menu');
        if (!menu.hidden) { closeModels(); return; }
        let last = '';
        menu.innerHTML = models.map((m) => {
            const head = manyServices() && m.service !== last ? `<div class="ch-menu-head">${esc(m.service)}</div>` : '';
            last = m.service;
            return `${head}<button type="button" role="option" data-model="${esc(m.id)}" aria-selected="${m.id === model}"><span>${esc(m.model)}${m.thinking !== 'none' ? '<i>can think</i>' : ''}</span>${m.id === model ? icon('check') : ''}</button>`;
        }).join('');
        menu.hidden = false;
        $c('#ch-model').setAttribute('aria-expanded', 'true');
    }
    function closeModels() {
        if (!root) return;
        $c('#ch-model-menu').hidden = true;
        $c('#ch-model').setAttribute('aria-expanded', 'false');
    }

    function renderTools() {
        // Think only where the model can think first, how hard only where it can be told.
        const can = (current() || {}).thinking || 'none';
        $c('#ch-think').hidden = can === 'none';
        $c('#ch-think').setAttribute('aria-pressed', reasoning && can !== 'none');
        $c('#ch-effort').hidden = !reasoning || can !== 'effort';
        $c('#ch-private').setAttribute('aria-pressed', privateMode);
        $c('#ch-search').hidden = !canSearch;
        $c('#ch-search').setAttribute('aria-pressed', canSearch && searchOn);
        root.classList.toggle('is-private', privateMode);
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
        const pinned = chats.filter((c) => c.pinned);
        const rest = chats.filter((c) => !c.pinned);
        const row = (c) => `<div class="ch-row${chat && chat.id === c.id ? ' active' : ''}" data-chat="${esc(c.id)}">
                <button type="button" class="ch-row-open" data-open>${esc(c.title || 'New chat')}</button>
                ${c.kept && !c.pinned ? `<span class="ch-row-mark" title="Kept: not deleted by itself">${icon('bookmark')}</span>` : ''}
                <button type="button" class="ch-row-more ch-row-burn" data-row-burn aria-label="Delete ${esc(c.title)}" title="Delete">${icon('flame')}</button>
                <button type="button" class="ch-row-more" data-row-more aria-label="More for ${esc(c.title)}">${icon('more')}</button>
            </div>`;
        const head = pinned.length ? `<div class="ch-group">Pinned</div>${pinned.map(row).join('')}` : '';
        $c('#ch-list').innerHTML = head + rest.map((c) => {
            const g = groupOf(c.updated || c.created);
            const h = g !== last ? `<div class="ch-group">${esc(g)}</div>` : '';
            last = g;
            return h + row(c);
        }).join('') || '<div class="ch-list-empty">Your chats show up here.</div>';
        $c('#ch-keep-note').textContent = keepDays
            ? `Chats unused for ${keepDays} days are deleted. Pin or keep the ones you need.`
            : '';
        $c('#ch-burn').disabled = !chats.some((c) => !c.pinned && !c.kept) && !chats.length;
    }
    async function onListClick(e) {
        const row = e.target.closest('[data-chat]');
        if (!row) return;
        const id = row.dataset.chat;
        if (e.target.closest('[data-open]')) { openChat(id); closeSide(); return; }
        if (e.target.closest('[data-row-burn]')) { askRemove(id); return; }
        if (e.target.closest('[data-row-more]')) {
            root.querySelectorAll('.ch-row-menu').forEach((m) => m.remove());
            const menu = document.createElement('div');
            menu.className = 'ch-row-menu';
            const c = chats.find((x) => x.id === id) || {};
            menu.innerHTML = `<button type="button" data-act="pin">${icon('pin')}${c.pinned ? 'Unpin' : 'Pin'}</button>
                ${c.pinned ? '' : `<button type="button" data-act="keep">${icon('bookmark')}${c.kept ? 'Do not keep' : 'Keep'}</button>`}
                <button type="button" data-act="rename">${icon('pen')}Rename</button><button type="button" data-act="delete" class="danger">${icon('trash')}Delete</button>`;
            row.appendChild(menu);
            menu.addEventListener('click', async (ev) => {
                const act = ev.target.closest('[data-act]');
                if (!act) return;
                menu.remove();
                if (act.dataset.act === 'rename') rename(row, id);
                else if (act.dataset.act === 'pin') flag(id, 'pin', 'pinned');
                else if (act.dataset.act === 'keep') flag(id, 'keep', 'kept');
                else askRemove(id);
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
    async function flag(id, what, key) {
        const c = chats.find((x) => x.id === id);
        const on = !c[key];
        try {
            await api(`chat/${encodeURIComponent(id)}/${what}`, { method: 'POST', json: { [key]: on } });
            c[key] = on;
            renderList();
            if (key === 'pinned') toast(on ? `“${c.title}” is pinned and stays.` : `“${c.title}” is no longer pinned.`);
            else toast(on ? `“${c.title}” is kept: it is not deleted by itself.` : `“${c.title}” goes after ${keepDays} days without use.`);
        } catch (err) { toast(err.message, 'error'); }
    }

    // ── The flame: one chat, or every chat at once (pinned and kept ones stay, unless asked) ──
    function confirmBox({ title, text, all, go }) {
        root.querySelectorAll('.ch-dialog').forEach((d) => d.remove());
        const box = document.createElement('div');
        box.className = 'ch-dialog';
        box.innerHTML = `<div class="ch-dialog-card" role="dialog" aria-modal="true" aria-labelledby="ch-dialog-title">
            <div class="ch-dialog-flame">${icon('flame')}</div>
            <h3 id="ch-dialog-title">${esc(title)}</h3>
            <p>${esc(text)}</p>
            ${all ? `<label class="ch-dialog-all"><input type="checkbox" id="ch-burn-all"> ${esc(all)}</label>` : ''}
            <div class="ch-dialog-btns"><button type="button" class="ch-dialog-cancel">Cancel</button><button type="button" class="ch-dialog-go">${icon('flame')}Delete</button></div>
        </div>`;
        root.appendChild(box);
        const close = () => box.remove();
        box.addEventListener('click', (e) => { if (e.target === box || e.target.closest('.ch-dialog-cancel')) close(); });
        box.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
        box.querySelector('.ch-dialog-go').addEventListener('click', () => {
            const ticked = !!(box.querySelector('#ch-burn-all') || {}).checked;
            close();
            go(ticked);
        });
        box.querySelector('.ch-dialog-go').focus();
    }
    function askBurn() {
        const stay = chats.filter((c) => c.pinned || c.kept).length;
        const go = chats.length - stay;
        confirmBox({
            title: 'Delete all chats?',
            text: `${go ? `${go} chat${go === 1 ? '' : 's'} will be deleted for good.` : 'There is nothing to delete except pinned and kept chats.'}${stay ? ` ${stay} pinned or kept chat${stay === 1 ? '' : 's'} stay${stay === 1 ? 's' : ''}.` : ''}`,
            all: stay ? 'Also delete pinned and kept chats' : '',
            go: burn,
        });
    }
    function askRemove(id) {
        const c = chats.find((x) => x.id === id) || {};
        confirmBox({
            title: 'Delete this chat?',
            text: `“${c.title || 'New chat'}” will be deleted for good.${c.pinned || c.kept ? ` It is ${c.pinned ? 'pinned' : 'kept'}.` : ''}`,
            go: () => remove(id),
        });
    }
    async function burn(everything) {
        if (streaming) stop();
        try {
            const got = await api('chat/burn', { method: 'POST', json: { everything } });
            chats = everything ? [] : chats.filter((c) => c.pinned || c.kept);
            if (chat && !chats.some((c) => c.id === chat.id)) chat = null;
            privateMode = false;
            renderList();
            renderThread();
            toast(got.deleted ? `${got.deleted} chat${got.deleted === 1 ? '' : 's'} deleted.` : 'Nothing to delete.');
        } catch (err) {
            toast(err.message, 'error');
        }
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
        privateMode = false;
        closeSide();
        renderThread();
        renderList();
        $c('#ch-input').focus();
    }
    async function openChat(id) {
        if (streaming) stop();
        try {
            chat = await api(`chat/${encodeURIComponent(id)}`);
            privateMode = false;
            const used = models.find((m) => m.id === chat.model) || models.find((m) => m.model === chat.model);
            if (used) { model = used.id; renderBar(); }
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
        cites = m.sources && m.sources.length ? m.sources : null;
        const body = m.content ? markdown(m.content) : '';
        cites = null;
        const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (_e) { return u; } };
        const searching = live && m.searching && !m.sources && !m.content
            ? `<div class="ch-think live"><span class="ch-think-label">Searching the web for “${esc(m.searching)}”…</span></div>` : '';
        const sources = m.sources && m.sources.length
            ? `<div class="ch-sources">${icon('globe')}<span>Sources</span>${m.sources.map((src, k) => `<a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer" title="${esc(src.title)}"><b>${k + 1}</b>${esc(host(src.url))}</a>`).join('')}</div>` : '';
        return `<div class="ch-msg ai" data-i="${i}">
            <div class="ch-avatar">${icon('sparkle')}</div>
            <div class="ch-ai">
                ${searching}${thinkingHtml(m, live && !searching)}
                <div class="ch-md">${body}${live && m.content ? '<span class="ch-caret"></span>' : ''}</div>
                ${sources}
                ${m.error ? `<div class="ch-error">${icon('alert')}<span>${esc(m.error)}</span></div>` : ''}
                ${live || !m.content ? '' : `<div class="ch-actions"><button type="button" class="ch-act" data-copy="${i}" title="Copy">${icon('copy')}</button>${m.model ? `<span class="ch-meta">${esc(m.model)}${m.service && manyServices() ? ` · ${esc(m.service)}` : ''}</span>` : ''}</div>`}
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
            thread.innerHTML = privateMode
                ? `<div class="ch-hello"><div class="ch-hello-ic private">${icon('ghost')}</div><h2>Private chat</h2><p>Kept nowhere: not in your chats, not on the NAS. It is gone when you start a new chat or leave. The AI service still sees what you write.</p></div>`
                : '<div class="ch-hello"><h2>What can I help with?</h2></div>';
        } else {
            thread.innerHTML = (privateMode ? `<div class="ch-private-note">${icon('ghost')}Private chat: kept nowhere, gone when you leave it.</div>` : '')
                + messages.map((m, i) => messageHtml(m, i, streaming && i === messages.length - 1 && m.role === 'assistant')).join('');
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
        const history = privateMode ? chat.messages.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content })) : null;
        chat.messages.push({ role: 'user', content: text });
        const picked = current() || {};
        const answer = { role: 'assistant', content: '', reasoning: '', model: picked.model, service: picked.service };
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
                body: JSON.stringify(privateMode
                    ? { private: true, history, message: text, model, reasoning, effort, search: canSearch && searchOn }
                    : { chat: chat.id || null, message: text, model, reasoning, effort, search: canSearch && searchOn }),
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
                    if (msg.chat && msg.chat.private) {
                        chat.title = msg.chat.title;
                    } else if (msg.chat) {
                        const isNew = !chat.id;
                        chat.id = msg.chat.id;
                        chat.title = msg.chat.title;
                        const row = chats.find((c) => c.id === chat.id);
                        const now = new Date().toISOString();
                        if (row) { row.updated = now; chats = [row, ...chats.filter((c) => c !== row)]; } else chats.unshift({ id: chat.id, title: chat.title, updated: now, model });
                        if (isNew || row) renderList();
                    } else if (msg.searching) { answer.searching = msg.searching; schedulePaint(); }
                    else if (msg.sources) { answer.sources = msg.sources; schedulePaint(); }
                    else if (msg.r) { answer.reasoning += msg.r; schedulePaint(); }
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
            keepDays = got.keep_days === undefined ? 30 : got.keep_days;
            canSearch = !!got.search;
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
        privateMode = false;
        if (built) { renderList(); renderThread(); }
    });

    window.HubApps = window.HubApps || {};
    window.HubApps.chat = {
        markdown,      // for the tests
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
