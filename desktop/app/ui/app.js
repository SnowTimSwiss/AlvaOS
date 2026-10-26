// The app's own pages: connect this computer, choose what to use, status and settings.
// Everything is done by the app's commands (src/main.rs); the page only shows and asks.
(function () {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const tauri = window.__TAURI_INTERNALS__;
    // Without the app (a browser, for a look at the pages): answers of a pretend app (`?preview=connect|choose|home`).
    const invoke = tauri ? (cmd, args) => tauri.invoke(cmd, args || {}) : preview();
    let state = null;
    let way = 'link';
    let busy = false;

    function toast(text) {
        const el = $('toast');
        el.textContent = text;
        el.hidden = false;
        clearTimeout(toast.timer);
        toast.timer = setTimeout(() => { el.hidden = true; }, 3200);
    }
    function show(view) {
        document.querySelectorAll('.view').forEach((v) => { v.hidden = v.id !== `view-${view}`; });
    }
    async function run(button, work, errorEl) {
        if (busy) return;
        busy = true;
        const before = button ? button.textContent : '';
        if (button) { button.disabled = true; button.textContent = `${before.replace(/…$/, '')}…`; }
        if (errorEl) errorEl.textContent = '';
        try {
            await work();
        } catch (err) {
            const message = String(err && err.message ? err.message : err);
            if (errorEl) errorEl.textContent = message; else toast(message);
        } finally {
            busy = false;
            if (button) { button.disabled = false; button.textContent = before; }
        }
    }

    // ── 1. Connect ─────────────────────────────────────────────────────────
    function pickWay(next) {
        way = next;
        document.querySelectorAll('[data-way]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.way === way)));
        document.querySelectorAll('[data-form]').forEach((f) => { f.hidden = f.dataset.form !== way; });
        $('connect-error').textContent = '';
    }
    async function checkLink() {
        const text = $('link-text').value.trim();
        const found = $('link-found');
        if (!text) { found.hidden = true; return; }
        try {
            const link = await invoke('parse_link', { text });
            found.textContent = `✓ A link for ${link.user || 'you'}${link.nas ? ` on ${link.nas}` : ''}${link.away ? ', works from anywhere' : ', at home only'}.`;
            found.hidden = false;
        } catch (_e) { found.hidden = true; }
    }
    async function connect(request, button) {
        await run(button, async () => {
            try {
                state = await invoke('connect', { request });
            } catch (err) {
                if (String(err) === 'needs_code') {
                    $('pw-code-field').hidden = false;
                    $('pw-code').focus();
                    throw new Error('Enter the code of your two-step sign-in.');
                }
                throw err;
            }
            choose();
        }, $('connect-error'));
    }

    // ── 2. Choose ──────────────────────────────────────────────────────────
    function choose() {
        $('choose-title').textContent = `Connected to ${state.nas || 'your NAS'}${state.user ? ` as ${state.user}` : ''}`;
        document.querySelectorAll('.hub-url').forEach((el) => { el.textContent = state.hub_url; });
        $('pick-window').checked = state.use_window;
        $('pick-autostart').checked = state.autostart;
        const drive = $('pick-drive');
        drive.disabled = !state.drive_supported;
        drive.checked = state.drive_supported && state.use_drive;
        $('pick-drive-row').classList.toggle('off', !state.drive_supported);
        if (!state.drive_supported) $('pick-drive-text').textContent = 'On Windows this comes with the sync folder (AlvaOS 0.6). Until then: Files in the window.';
        show('choose');
    }

    // ── 3. Home ────────────────────────────────────────────────────────────
    function paint() {
        if (!state.set_up) {
            show('connect');
            const note = $('connect-note');
            note.hidden = !state.status.message;
            note.textContent = state.status.message || '';
            return;
        }
        if (!$('view-choose').hidden) return;     // still choosing
        show('home');
        const s = state.status;
        $('home-nas').textContent = state.nas || 'Your NAS';
        $('home-user').textContent = `${state.user} · ${state.computer}`;
        $('home-line').textContent = state.line;
        const pill = $('home-pill');
        const label = { ok: s.route === 'relay' ? 'Connected · relay' : s.route === 'home' ? 'Connected · at home' : `Connected · ${s.ms} ms`, connecting: 'Connecting…', offline: 'Not reachable', off: 'Switched off' }[s.state] || s.state;
        pill.textContent = label;
        pill.className = `pill ${s.state === 'ok' ? 'ok' : s.state === 'offline' ? 'bad' : s.state === 'connecting' ? 'busy' : ''}`;
        const note = $('home-note');
        note.hidden = !(s.state === 'offline' && s.message);
        note.textContent = s.state === 'offline' ? `${s.message} It tries again by itself.` : '';
        document.querySelectorAll('.hub-url').forEach((el) => { el.textContent = state.hub_url; });
        const online = s.state === 'ok';
        $('home-open').disabled = !online;
        $('home-browser').disabled = !online;
        $('drive-row').hidden = false;
        $('home-drive').checked = s.drive;
        $('home-drive').disabled = !state.drive_supported || !online;
        $('drive-text').textContent = state.drive_supported
            ? 'Your shared folders in the file manager, at home and away.'
            : 'On Windows this comes with the sync folder (AlvaOS 0.6).';
        $('home-connected').checked = state.connected;
        $('home-window').checked = state.use_window;
        $('home-autostart').checked = state.autostart;
        $('home-foot').textContent = `AlvaOS ${state.version} for ${{ windows: 'Windows', linux: 'Linux', macos: 'macOS' }[state.platform] || state.platform}${state.link ? ' · AlvaOS Link' : ' · at home only (AlvaOS Link is off on the NAS)'}`;
    }
    async function refresh() {
        try {
            state = await invoke('app_state');
            paint();
        } catch (err) { toast(String(err)); }
    }
    async function setChoice(changes) {
        state = await invoke('choose', { choice: { use_window: state.use_window, use_drive: state.use_drive, autostart: state.autostart, ...changes } });
        paint();
    }

    function wire() {
        document.querySelectorAll('[data-way]').forEach((b) => b.addEventListener('click', () => pickWay(b.dataset.way)));
        $('link-text').addEventListener('input', checkLink);
        $('link-paste').addEventListener('click', async () => {
            try { $('link-text').value = await navigator.clipboard.readText(); checkLink(); } catch (_e) { $('link-text').focus(); toast('Paste with Ctrl+V (⌘V).'); }
        });
        $('form-link').addEventListener('submit', (e) => {
            e.preventDefault();
            connect({ how: 'link', text: $('link-text').value }, e.submitter);
        });
        $('form-code').addEventListener('submit', (e) => {
            e.preventDefault();
            connect({ how: 'code', code: $('code-code').value, address: $('code-address').value }, e.submitter);
        });
        $('form-password').addEventListener('submit', (e) => {
            e.preventDefault();
            connect({ how: 'password', address: $('pw-address').value, user: $('pw-user').value, password: $('pw-password').value, code: $('pw-code').value }, e.submitter);
        });
        $('choose-done').addEventListener('click', (e) => run(e.target, async () => {
            state = await invoke('choose', { choice: { use_window: $('pick-window').checked, use_drive: $('pick-drive').checked, autostart: $('pick-autostart').checked } });
            show('home');
            paint();
            if (state.use_window) await invoke('open_hub').catch(() => {});
        }, $('choose-error')));
        $('home-open').addEventListener('click', (e) => run(e.target, () => invoke(state.use_window ? 'open_hub' : 'open_browser'), $('home-error')));
        $('home-browser').addEventListener('click', (e) => run(e.target, () => invoke('open_browser'), $('home-error')));
        document.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', async () => {
            try { await navigator.clipboard.writeText(state.hub_url); toast('Address copied. It works in any browser on this computer.'); } catch (_e) { toast(state.hub_url); }
        }));
        $('home-drive').addEventListener('change', (e) => run(null, async () => {
            const on = e.target.checked;
            try {
                const place = await invoke('drive', { on });
                toast(on ? `Files is a drive now${place ? `: ${place}` : ''}.` : 'The drive is removed.');
            } catch (err) { e.target.checked = !on; throw err; }
            await setChoice({ use_drive: on });
        }, $('home-error')));
        $('home-connected').addEventListener('change', (e) => run(null, async () => {
            state = await invoke('set_connected', { on: e.target.checked });
            paint();
        }, $('home-error')));
        $('home-window').addEventListener('change', (e) => run(null, () => setChoice({ use_window: e.target.checked }), $('home-error')));
        $('home-autostart').addEventListener('change', (e) => run(null, () => setChoice({ autostart: e.target.checked }), $('home-error')));
        $('home-signout').addEventListener('click', (e) => {
            if (!window.confirm('Sign out this computer? It is removed on the NAS too; connect it again with a new link from the Hub.')) return;
            run(e.target, async () => {
                state = await invoke('sign_out');
                pickWay('link');
                paint();
            }, $('home-error'));
        });
    }

    function preview() {
        const kind = new URLSearchParams(location.search).get('preview') || 'connect';
        const fake = {
            set_up: kind !== 'connect', nas: 'cygnus', user: 'anna', link: true, use_window: true, use_drive: true, connected: true, autostart: true,
            hub_url: 'http://127.0.0.1:18090', dav_url: 'http://127.0.0.1:18091', platform: 'linux', drive_supported: true, version: '0.5.0', computer: 'anna-laptop',
            status: { state: 'ok', route: 'direct', ms: 14, message: '', drive: true }, line: 'Connected to cygnus · direct · 14 ms',
        };
        if (kind === 'choose') setTimeout(() => { state = fake; choose(); }, 0);
        return async (cmd, args) => {
            await new Promise((r) => setTimeout(r, 150));
            if (cmd === 'parse_link') { if (!String(args.text).startsWith('alvaos://')) throw new Error('not a link'); return { nas: 'cygnus', user: 'anna', away: true }; }
            if (cmd === 'connect') return { ...fake, set_up: true };
            if (cmd === 'choose') return { ...fake, ...args.choice };
            if (cmd === 'sign_out') return { ...fake, set_up: false };
            return fake;
        };
    }

    wire();
    refresh();
    // While the window is open: the status as it is.
    setInterval(() => { if (!document.hidden && !busy && state && state.set_up && $('view-choose').hidden) refresh(); }, 2500);
})();
