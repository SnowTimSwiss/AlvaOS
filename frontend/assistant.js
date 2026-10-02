// The AlvaOS assistant: a chat panel in the top bar, once it is turned on in
// Settings › Assistant. It can look at this NAS but not change anything.
(function () {
    const HISTORY_KEY = 'alvaos_ai_history';
    const SUGGESTIONS = ['How is my NAS doing?', 'Are my backups working?', 'How full are my disks?'];
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let history = [];
    let panel = null;
    let busy = false;

    function loadHistory() {
        try { history = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]') || []; } catch (_e) { history = []; }
        if (!Array.isArray(history)) history = [];
    }

    function saveHistory() {
        try { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-30))); } catch (_e) { /* storage off */ }
    }

    // Replies are plain text with a little Markdown. Everything is escaped
    // first; only bold, code and lists are turned back into markup.
    function format(text) {
        const inline = (line) => esc(line)
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
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

    function looked(names) {
        const unique = [...new Set(names || [])].map((n) => n.replace(/_/g, ' '));
        return unique.length ? `<div class="ai-looked">Looked at: ${esc(unique.join(', '))}</div>` : '';
    }

    function render() {
        const list = panel.querySelector('.ai-messages');
        if (!history.length) {
            list.innerHTML = `
                <div class="ai-welcome">
                    <p>Ask about this NAS in your own words. The assistant looks at the real state of your disks,
                    backups, apps and updates. It cannot change anything.</p>
                    <div class="ai-suggestions">${SUGGESTIONS.map((s) => `<button type="button" class="ai-chip">${esc(s)}</button>`).join('')}</div>
                </div>`;
            list.querySelectorAll('.ai-chip').forEach((b) => b.addEventListener('click', () => ask(b.textContent)));
            return;
        }
        list.innerHTML = history.map((m) => (m.role === 'user'
            ? `<div class="ai-msg ai-user">${esc(m.content)}</div>`
            : `<div class="ai-msg ai-bot${m.error ? ' ai-error' : ''}">${format(m.content)}${looked(m.looked_at)}</div>`)).join('')
            + (busy ? '<div class="ai-msg ai-bot ai-thinking" role="status">Looking<span>.</span><span>.</span><span>.</span></div>' : '');
        list.scrollTop = list.scrollHeight;
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
                body: JSON.stringify({ messages: history.filter((m) => !m.error).map(({ role, content }) => ({ role, content })) }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || 'The assistant could not answer.');
            history.push({ role: 'assistant', content: data.reply, looked_at: data.looked_at });
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
            if (!res.ok || !(await res.json()).enabled) return;
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
