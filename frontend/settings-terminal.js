// Settings › Terminal: a shell on the NAS in the browser, as the AlvaOS
// service account, never as root (backend/admin_terminal.py,
// docs/ADMIN-TERMINAL.md). xterm.js is loaded only when it is opened.
(function () {
    const $ = (id) => document.getElementById(id);
    let term = null;
    let fit = null;
    let socket = null;

    function load(tag, attrs) {
        return new Promise((resolve, reject) => {
            const el = document.createElement(tag);
            Object.assign(el, attrs);
            el.onload = resolve;
            el.onerror = () => reject(new Error('The terminal could not be loaded.'));
            document.head.appendChild(el);
        });
    }

    async function ready() {
        if (window.Terminal && window.FitAddon) return;
        await load('link', { rel: 'stylesheet', href: 'vendor/xterm/xterm.css' });
        await load('script', { src: 'vendor/xterm/xterm.js' });
        await load('script', { src: 'vendor/xterm/addon-fit.js' });
    }

    function status(text, tone) {
        const el = $('term-status');
        el.textContent = text;
        el.className = `term-status ${tone || ''}`;
    }

    function sendSize() {
        if (!term || !socket || socket.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify({ resize: { cols: term.cols, rows: term.rows } }));
    }

    async function open() {
        const btn = $('term-open');
        btn.disabled = true;
        try {
            await ready();
            const res = await fetch(`${API_BASE}/system/terminal`, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || 'The terminal could not be opened.');
            $('term-box').hidden = false;
            $('term-start').hidden = true;
            if (!term) {
                const dark = matchMedia('(prefers-color-scheme: dark)').matches || document.documentElement.dataset.theme === 'dark';
                term = new window.Terminal({
                    cursorBlink: true, fontSize: 14, scrollback: 5000,
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                    theme: dark ? { background: '#0b1220' } : { background: '#111827' },
                });
                fit = new window.FitAddon.FitAddon();
                term.loadAddon(fit);
                term.open($('term-screen'));
                term.onData((d) => { if (socket && socket.readyState === WebSocket.OPEN) socket.send(new TextEncoder().encode(d)); });
                term.onResize(sendSize);
                window.addEventListener('resize', () => { if (!$('term-box').hidden) fit.fit(); });
            }
            term.reset();
            fit.fit();
            const secure = location.protocol === 'https:';
            socket = new WebSocket(`${secure ? 'wss' : 'ws'}://${location.hostname}:${secure ? data.tls_port : data.port}/?ticket=${encodeURIComponent(data.ticket)}`);
            socket.binaryType = 'arraybuffer';
            socket.onopen = () => { status(`Connected as ${data.user || 'alvaos'}`, 'ok'); sendSize(); term.focus(); };
            socket.onmessage = (e) => {
                if (typeof e.data === 'string') {
                    try { const msg = JSON.parse(e.data); if (msg.closed) status(msg.closed, ''); } catch (_e) { /* not for us */ }
                    return;
                }
                term.write(new Uint8Array(e.data));
            };
            socket.onclose = () => {
                if (!$('term-status').textContent.startsWith('Connected')) return;
                status('The terminal closed.', '');
                $('term-again').hidden = false;
            };
            socket.onerror = () => status(secure ? 'The terminal could not be reached. The certificate of this NAS has to be trusted for port 9446 too.' : 'The terminal could not be reached.', 'bad');
            $('term-again').hidden = true;
        } catch (err) {
            status(err.message, 'bad');
        } finally {
            btn.disabled = false;
        }
    }

    function close() {
        if (socket) socket.close();
        socket = null;
        $('term-box').hidden = true;
        $('term-start').hidden = false;
        status('', '');
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (!$('term-open')) return;
        $('term-open').addEventListener('click', open);
        $('term-again').addEventListener('click', open);
        $('term-close').addEventListener('click', close);
        $('term-clear').addEventListener('click', () => term && term.clear());
        $('term-copy').addEventListener('click', async () => {
            const text = term ? term.getSelection() : '';
            if (!text) { status('Select some text first, then Copy.', ''); return; }
            try { await navigator.clipboard.writeText(text); status('Copied.', 'ok'); } catch (_e) { status('The browser did not allow copying.', 'bad'); }
        });
    });
})();
