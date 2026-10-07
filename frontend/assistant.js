// The AlvaOS assistant: a chat panel in the top bar, once it is turned on in
// Settings › Assistant. It looks at this NAS; at the "ask" level it also
// suggests changes, which run only when the person presses "Do it".
(function () {
    const HISTORY_KEY = 'alvaos_ai_history';
    const SUGGESTIONS = ['How is my NAS doing?', 'Are my backups working?', 'How full are my disks?', 'Why did something fail?'];
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let history = [];
    let panel = null;
    let busy = false;
    let level = 'read';

    function loadHistory() {
        try { history = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]') || []; } catch (_e) { history = []; }
        if (!Array.isArray(history)) history = [];
    }

    function saveHistory() {
        try { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-30))); } catch (_e) { /* storage off */ }
    }

    // Pages the assistant may link to (ai_assistant.PAGES); other links stay text.
    const PAGES = ['index.html', 'storage.html', 'files.html', 'apps.html', 'backup.html', 'updates.html', 'system.html', 'vms.html'];
    const pageLink = (_m, label, href) => {
        const [file, anchor = ''] = href.split('#');
        if (!PAGES.includes(file) || !/^[a-z=-]{0,20}$/.test(anchor)) return label;
        return `<a href="${file}${anchor ? `#${anchor}` : ''}" class="ai-page-link">${label}</a>`;
    };

    // Replies are plain text with a little Markdown. Everything is escaped
    // first; only bold, code, lists and links to AlvaOS pages are turned back
    // into markup.
    function format(text) {
        const inline = (line) => esc(line)
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\[([^\]]{1,60})\]\(([a-z]+\.html(?:#[a-z=-]*)?)\)/g, pageLink);
        const out = [];
        let list = null;
        for (const raw of String(text || '').split('\n')) {
            const item = raw.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
            if (item) {
                if (!list) { list = []; out.push(list); }
                list.push(`<li>${inline(item[1])}</li>`);
                continue;
            }
            list = null;
            const heading = raw.match(/^#{1,4}\s+(.*)$/);
            if (heading) out.push(`<p><strong>${inline(heading[1])}</strong></p>`);
            else if (raw.trim()) out.push(`<p>${inline(raw)}</p>`);
        }
        return out.map((part) => (Array.isArray(part) ? `<ul>${part.join('')}</ul>` : part)).join('');
    }

    function looked(names, ran) {
        const unique = [...new Set(names || [])].map((n) => n.replace(/_/g, ' '));
        const commands = [...new Set(ran || [])];
        return (unique.length ? `<div class="ai-looked">Looked at: ${esc(unique.join(', '))}</div>` : '')
            + (commands.length ? `<div class="ai-looked ai-ran">Ran: ${commands.map((c) => `<code>${esc(c)}</code>`).join(' ')}</div>` : '');
    }

    function render() {
        const list = panel.querySelector('.ai-messages');
        if (!history.length) {
            list.innerHTML = `
                <div class="ai-welcome">
                    <p>Ask about this NAS in your own words. The assistant looks at the real state of your disks,
                    backups, apps and updates. It changes nothing on its own${level === 'ask' ? '; when it suggests something, you decide' : ''}.</p>
                    <div class="ai-suggestions">${SUGGESTIONS.map((s) => `<button type="button" class="ai-chip">${esc(s)}</button>`).join('')}</div>
                </div>`;
            list.querySelectorAll('.ai-chip').forEach((b) => b.addEventListener('click', () => ask(b.textContent)));
            return;
        }
        list.innerHTML = history.filter((m) => !m.note).map((m) => (m.role === 'user'
            ? `<div class="ai-msg ai-user">${esc(m.content)}</div>`
            : `<div class="ai-msg ai-bot${m.error ? ' ai-error' : ''}">${format(m.content)}${looked(m.looked_at, m.ran)}${proposalsHtml(m)}</div>`)).join('')
            + (busy ? '<div class="ai-msg ai-bot ai-thinking" role="status">Looking<span>.</span><span>.</span><span>.</span></div>' : '');
        list.querySelectorAll('[data-decide]').forEach((b) => b.addEventListener('click', () => decide(b.dataset.id, b.dataset.decide)));
        list.scrollTop = list.scrollHeight;
    }

    // A change the assistant suggests. It runs only when the person presses
    // "Do it"; the NAS runs exactly what is described, with their session.
    function proposalsHtml(m) {
        return (m.proposals || []).map((p) => `
            <div class="ai-proposal${p.state ? ` ${p.state}` : ''}">
                <strong>${esc(p.title)}</strong>
                <span>${esc(p.detail)}</span>
                ${p.state ? `<em>${esc(p.result || '')}</em>` : `<div class="ai-proposal-actions">
                    <button type="button" class="btn-primary" data-decide="run" data-id="${esc(p.id)}">Do it</button>
                    <button type="button" class="btn-secondary" data-decide="skip" data-id="${esc(p.id)}">No</button></div>`}
            </div>`).join('');
    }

    async function decide(id, decision) {
        const item = history.flatMap((m) => m.proposals || []).find((p) => p.id === id);
        if (!item || item.state) return;
        item.state = 'busy';
        item.result = decision === 'run' ? 'Working…' : '';
        render();
        try {
            const res = await fetch(`${API_BASE}/ai/actions/${encodeURIComponent(id)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || 'That did not work.');
            item.state = data.done ? 'done' : 'skipped';
            item.result = data.done ? data.message : 'Not done.';
        } catch (e) {
            item.state = 'failed';
            item.result = e.message;
        }
        // The assistant knows what happened in the next answer.
        history.push({ role: 'user', content: `[${item.state === 'done' ? 'Done' : item.state === 'skipped' ? 'I said no to' : 'It failed'}: ${item.title}. ${item.result}]`, note: true });
        saveHistory();
        render();
    }

    async function ask(text) {
        const question = String(text || '').trim();
        if (!question || busy) return;
        history.push({ role: 'user', content: question });
        busy = true;
        render();
        const input = panel.querySelector('textarea');
        input.value = '';
        try {
            const res = await fetch(`${API_BASE}/ai/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: history.filter((m) => !m.error).map(({ role, content }) => ({ role, content })),
                    page: window.location.pathname.split('/').pop() || 'index.html',
                }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || 'The assistant could not answer.');
            history.push({ role: 'assistant', content: data.reply, looked_at: data.looked_at, ran: data.ran || [], proposals: data.proposals || [] });
        } catch (e) {
            history.push({ role: 'assistant', content: e.message, error: true });
        } finally {
            busy = false;
            saveHistory();
            render();
            input.focus();
        }
    }

    function open() {
        panel.hidden = false;
        document.body.classList.add('ai-open');
        document.getElementById('ai-btn')?.setAttribute('aria-expanded', 'true');
        render();
        panel.querySelector('textarea').focus();
    }

    function close() {
        panel.hidden = true;
        document.body.classList.remove('ai-open');
        const button = document.getElementById('ai-btn');
        button?.setAttribute('aria-expanded', 'false');
        button?.focus();
    }

    function build(actions) {
        const button = document.createElement('button');
        button.type = 'button';
        button.id = 'ai-btn';
        button.className = 'notif-bell-btn';
        button.setAttribute('aria-label', 'Assistant');
        button.setAttribute('aria-controls', 'ai-panel');
        button.setAttribute('aria-expanded', 'false');
        button.title = 'Ask the assistant';
        button.innerHTML = window.alvaIcon ? window.alvaIcon('bot', 'notif-bell-icon', 'aria-hidden="true"') : 'AI';
        actions.prepend(button);

        panel = document.createElement('aside');
        panel.id = 'ai-panel';
        panel.className = 'ai-panel';
        panel.hidden = true;
        panel.setAttribute('aria-label', 'Assistant');
        panel.innerHTML = `
            <div class="ai-head">
                <strong>Assistant</strong>
                <span class="ai-head-actions">
                    <button type="button" class="ai-link" id="ai-clear">New chat</button>
                    <button type="button" class="modal-close-x" id="ai-close" aria-label="Close">&times;</button>
                </span>
            </div>
            <div class="ai-messages" aria-live="polite"></div>
            <form class="ai-form">
                <textarea rows="1" placeholder="Ask about your NAS..." aria-label="Your question" maxlength="4000"></textarea>
                <button type="submit" class="btn-primary">Ask</button>
            </form>`;
        document.body.appendChild(panel);

        button.addEventListener('click', () => (panel.hidden ? open() : close()));
        panel.querySelector('#ai-close').addEventListener('click', close);
        panel.querySelector('#ai-clear').addEventListener('click', () => {
            history = [];
            saveHistory();
            render();
            panel.querySelector('textarea').focus();
        });
        const input = panel.querySelector('textarea');
        panel.querySelector('form').addEventListener('submit', (event) => {
            event.preventDefault();
            ask(input.value);
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                ask(input.value);
            } else if (event.key === 'Escape') {
                close();
            }
        });
    }

    async function start() {
        const path = window.location.pathname;
        if (path.includes('login.html') || path.includes('setup.html')) return;
        if (!localStorage.getItem('alvaos_token')) return;
        const actions = document.querySelector('.topbar .topbar-actions');
        if (!actions || document.getElementById('ai-btn')) return;
        try {
            const res = await fetch(`${API_BASE}/ai/settings`);
            const data = res.ok ? await res.json() : {};
            if (!data.enabled) return;
            level = data.level || 'read';
        } catch (_e) {
            return;
        }
        loadHistory();
        build(actions);
    }

    // Settings › Assistant turns it on or off without a reload.
    window.alvaosAssistantChanged = (enabled) => {
        const button = document.getElementById('ai-btn');
        if (enabled && !button) start();
        if (!enabled && button) {
            button.remove();
            panel?.remove();
            panel = null;
            document.body.classList.remove('ai-open');
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
