// The app's own pages: welcome, connect this computer, choose what to use, the sync folders,
// status and settings. Everything is done by the app's commands (src/main.rs, src/syncing.rs);
// the page only shows and asks.
(function () {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const tauri = window.__TAURI_INTERNALS__;
    // Without the app (a browser, for a look at the pages): answers of a pretend app
    // (`?preview=welcome|connect|choose|sync|done|home`).
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    let state = null;
    let way = 'link';
    let busy = false;
    let setup = false;          // in the steps of setting up: the status does not move the page
    const invoke = tauri ? (cmd, args) => tauri.invoke(cmd, args || {}) : preview();

    // ── Icons (drawn here: no files, no fonts) ─────────────────────────────
    const ICONS = {
        globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/>',
        sync: '<path d="M20 12a8 8 0 0 1-13.7 5.6M4 12a8 8 0 0 1 13.7-5.6"/><path d="M18 3v4h-4M6 21v-4h4"/>',
        lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
        monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
        server: '<rect x="4" y="3" width="16" height="8" rx="2"/><rect x="4" y="13" width="16" height="8" rx="2"/><path d="M8 7h.01M8 17h.01"/>',
        paste: '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V3h6v1M9 10h6M9 14h4"/>',
        window: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/>',
        drive: '<path d="M4 15l2.5-9h11L20 15"/><rect x="3" y="15" width="18" height="5" rx="1.5"/><path d="M16 17.5h.01"/>',
        power: '<path d="M12 3v8M6.3 7a8 8 0 1 0 11.4 0"/>',
        refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
        plus: '<path d="M12 5v14M5 12h14"/>',
        folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
        share: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 14h6"/>',
        link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
        logout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10"/>',
        chev: '<path d="M9 6l6 6-6 6"/>',
        check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
        x: '<path d="M6 6l12 12M18 6L6 18"/>',
        home: '<path d="M4 11l8-7 8 7v9H4z"/>',
        cloud: '<path d="M7 18a4 4 0 0 1-.5-7.97A6 6 0 0 1 18 9.2 4.4 4.4 0 0 1 17.5 18z"/>',
        dots: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
        gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    };
    const icon = (name, cls = '') => `<span class="ic ${cls}"><svg viewBox="0 0 24 24">${ICONS[name] || ''}</svg></span>`;
    function paintIcons(root = document) {
        root.querySelectorAll('[data-icon]').forEach((el) => {
            if (!el.firstChild) el.innerHTML = `<svg viewBox="0 0 24 24">${ICONS[el.dataset.icon] || ''}</svg>`;
        });
    }
    // A path shown from its end (cut on the left) keeps its slashes where they belong.
    const ltr = (s) => `\u200e${s}\u200e`;
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function toast(text) {
        const el = $('toast');
        el.textContent = text;
        el.hidden = true;
        void el.offsetWidth;    // the animation again
        el.hidden = false;
        clearTimeout(toast.timer);
        toast.timer = setTimeout(() => { el.hidden = true; }, 3400);
    }
    function current() {
        const v = document.querySelector('.view:not([hidden])');
        return v ? v.id.replace('view-', '') : '';
    }
    function show(view) {
        if (current() === view) return;
        document.querySelectorAll('.view').forEach((v) => { v.hidden = v.id !== `view-${view}`; });
        window.scrollTo(0, 0);
        if (view === 'connect') requestAnimationFrame(glide);
    }
    async function run(button, work, errorEl) {
        if (busy) return;
        busy = true;
        if (button) { button.disabled = true; button.classList.add('working'); }
        if (errorEl) errorEl.textContent = '';
        try {
            await work();
        } catch (err) {
            const message = String(err && err.message ? err.message : err);
            if (errorEl) errorEl.textContent = message; else toast(message);
        } finally {
            busy = false;
            if (button) { button.disabled = false; button.classList.remove('working'); }
        }
    }

    // ── 1. Connect ─────────────────────────────────────────────────────────
    function glide() {
        const on = document.querySelector('[data-way][aria-selected="true"]');
        const bar = $('tab-glide');
        if (!on || !on.offsetWidth) return;
        bar.style.width = `${on.offsetWidth}px`;
        bar.style.transform = `translateX(${on.offsetLeft - 3}px)`;
    }
    function pickWay(next) {
        way = next;
        document.querySelectorAll('[data-way]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.way === way)));
        document.querySelectorAll('[data-form]').forEach((f) => { f.hidden = f.dataset.form !== way; });
        $('connect-error').textContent = '';
        glide();
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
        const bridge = $('bridge');
        bridge.className = 'bridge busy';
        setup = true;
        await run(button, async () => {
            try {
                state = await invoke('connect', { request });
            } catch (err) {
                bridge.className = 'bridge fail';
                if (String(err) === 'needs_code') {
                    $('pw-code-field').hidden = false;
                    $('pw-code').focus();
                    throw new Error('Enter the code of your two-step sign-in.');
                }
                throw err;
            }
            bridge.className = 'bridge done';
            await wait(750);
            choose();
        }, $('connect-error'));
    }

    // ── 2. Choose ──────────────────────────────────────────────────────────
    function choose() {
        setup = true;
        $('choose-title').textContent = `Connected to ${state.nas || 'your NAS'}${state.user ? ` as ${state.user}` : ''}`;
        document.querySelectorAll('.hub-url').forEach((el) => { el.textContent = state.hub_url; });
        $('pick-window').checked = state.use_window;
        $('pick-autostart').checked = state.autostart;
        $('pick-sync').checked = true;
        const drive = $('pick-drive');
        drive.disabled = !state.drive_supported;
        drive.checked = state.drive_supported && state.use_drive;
        $('pick-drive-row').classList.toggle('off', !state.drive_supported);
        if (!state.drive_supported) $('pick-drive-text').textContent = 'Not possible on this system. A sync folder does the job.';
        else if (state.platform === 'windows') $('pick-drive-text').textContent = 'A network drive in Explorer (Z:), without copying. Windows opens files up to 50 MB this way: bigger ones are for a sync folder.';
        show('choose');
    }

    // ── 3. The folder picker (setup and "Add a folder") ────────────────────
    function picker(el) {
        const me = { shares: null, share: null, path: [], local: null };
        const sep = () => (String((state && state.sync && state.sync.root) || '').includes('\\') ? '\\' : '/');
        function place() {
            if (me.local) return me.local;
            const root = (state && state.sync && state.sync.root) || '~/AlvaOS';
            return me.share ? [root, me.share.name, ...me.path].join(sep()) : root;
        }
        async function paint() {
            const crumbs = [`<button type="button" data-at="-1">${icon('home')}</button>`];
            if (me.share) crumbs.push(`<span class="sep">›</span><button type="button" data-at="0">${esc(me.share.name)}</button>`);
            me.path.forEach((p, i) => crumbs.push(`<span class="sep">›</span><button type="button" data-at="${i + 1}">${esc(p)}</button>`));
            el.innerHTML = `<div class="crumbs">${crumbs.join('')}</div>
                <div class="folders">${'<div class="skeleton"></div>'.repeat(3)}</div>
                <div class="place">${icon('monitor')}<code title="${esc(place())}">${esc(ltr(place()))}</code>
                    <button type="button" class="btn small ghost" data-change>Change…</button></div>`;
            el.querySelectorAll('[data-at]').forEach((b) => b.addEventListener('click', () => {
                const at = Number(b.dataset.at);
                if (at < 0) { me.share = null; me.path = []; } else me.path = me.path.slice(0, at);
                me.local = null;
                paint();
            }));
            el.querySelector('[data-change]').addEventListener('click', async () => {
                try {
                    const chosen = await invoke('pick_local');
                    if (chosen) { me.local = chosen; el.querySelector('.place code').textContent = ltr(chosen); }
                } catch (err) { toast(String(err)); }
            });
            const list = el.querySelector('.folders');
            try {
                let rows;
                if (!me.share) {
                    if (!me.shares) me.shares = await invoke('remote_shares');
                    rows = (me.shares || []).map((s, i) => `<button type="button" data-share="${i}" style="--i:${i}">${icon('share')}<span>${esc(s.name)}</span>
                        ${s.access === 'read' ? '<span class="badge">read only</span>' : ''}${icon('chev', 'chev')}</button>`);
                    if (!rows.length) rows = ['<p class="empty">No shared folders for you on the NAS yet.</p>'];
                } else {
                    const names = await invoke('remote_folders', { share: me.share.name, path: me.path.join('/') });
                    rows = names.map((n, i) => `<button type="button" data-folder="${esc(n)}" style="--i:${i}">${icon('folder')}<span>${esc(n)}</span>${icon('chev', 'chev')}</button>`);
                    if (!rows.length) rows = [`<p class="empty">No folders in here. "Keep it here" keeps ${esc(me.path.length ? me.path[me.path.length - 1] : me.share.name)} with all its files.</p>`];
                }
                list.innerHTML = rows.join('');
                list.querySelectorAll('[data-share]').forEach((b) => b.addEventListener('click', () => { me.share = me.shares[Number(b.dataset.share)]; me.path = []; me.local = null; paint(); }));
                list.querySelectorAll('[data-folder]').forEach((b) => b.addEventListener('click', () => { me.path.push(b.dataset.folder); me.local = null; paint(); }));
            } catch (err) {
                list.innerHTML = `<p class="empty">${esc(err && err.message ? err.message : err)}</p>`;
            }
            if (me.onchange) me.onchange();
        }
        me.reset = () => { me.shares = null; me.share = null; me.path = []; me.local = null; return paint(); };
        me.chosen = () => me.share && { share: me.share.name, folder: me.path.join('/'), read_only: me.share.access === 'read', local: me.local,
            on_demand: wantsOnDemand(el.dataset.picker === 'sheet' ? 'sheet-ondemand' : 'setup-ondemand') };
        return me;
    }
    const pickers = {};
    document.querySelectorAll('[data-picker]').forEach((el) => { pickers[el.dataset.picker] = picker(el); });
    pickers.setup.onchange = () => { $('sync-done').disabled = !pickers.setup.chosen(); };
    pickers.sheet.onchange = () => { $('sheet-done').disabled = !pickers.sheet.chosen(); };
    const wantsOnDemand = (box) => !!(state.sync && state.sync.on_demand_supported) && (!!state.sync.on_demand_forced || $(box).checked);
    // Where there is no other kind (Windows) the switch is not shown, only what it means.
    function onDemandRows() {
        const sync = state.sync || {};
        document.querySelectorAll('.ondemand-row').forEach((row) => {
            row.hidden = !sync.on_demand_supported;
            const sw = row.querySelector('.switch');
            if (sw) sw.hidden = !!sync.on_demand_forced;
            const title = row.querySelector('strong');
            const text = row.querySelector('small');
            if (sync.on_demand_forced) {
                title.textContent = 'Files download when you open them';
                text.textContent = 'Every file of the share shows up in the folder at once (with a cloud icon); one is fetched when you open it. Right-click in Explorer: "Always keep on this device" or "Free up space". The folder must be empty.';
            }
        });
    }

    async function addSync(chosen) {
        state.sync = await invoke('add_sync', chosen);
    }
    function sync() {
        $('setup-ondemand').checked = false;
        onDemandRows();
        $('sync-root').textContent = state.sync ? state.sync.root : '~/AlvaOS';
        show('sync');
        $('sync-done').disabled = true;
        pickers.setup.reset();
    }

    // ── 4. All set ─────────────────────────────────────────────────────────
    function done() {
        const lines = [[state.link ? 'link' : 'home', state.link ? `Connected to ${state.nas || 'your NAS'} through AlvaOS Link, from anywhere` : `Connected to ${state.nas || 'your NAS'} at home`]];
        if (state.use_window) lines.push(['window', 'The Hub apps one click from the tray']);
        (state.sync ? state.sync.folders : []).forEach((f) => lines.push(['sync', `${f.label} kept in ${f.local}`]));
        if (state.use_drive) lines.push(['drive', 'Files as a drive in the file manager']);
        if (state.autostart) lines.push(['power', 'Starts with the computer']);
        $('done-list').innerHTML = lines.map(([ic, text]) => `<li>${icon('check')}<span>${esc(text)}</span></li>`).join('');
        $('done-go').textContent = state.use_window ? 'Open AlvaOS' : 'Open in the browser';
        show('done');
    }

    // ── 5. Home ────────────────────────────────────────────────────────────
    const STATES = { waiting: 'Waiting', syncing: 'Syncing', ok: 'Up to date', problem: 'Problem', ask: 'Needs you', paused: 'Paused' };
    const seen = new Set();
    let syncKey = '';
    function paintSync() {
        const sync = state.sync || { folders: [] };
        $('sync-line').textContent = sync.line || '';
        $('sync-pause').checked = !!sync.paused;
        const key = JSON.stringify(sync.folders);
        if (key === syncKey) return;
        syncKey = key;
        const asks = sync.folders.filter((f) => f.status.state === 'ask').map((f) => `<div class="ask-box">
            <strong>${esc(f.label)}:</strong> ${f.status.ask} files were deleted, here or on the NAS. Delete them on the other side too? (They go into the trash.)
            <div class="row"><button type="button" class="btn small danger" data-answer="${esc(f.id)}" data-delete="1">Delete them too</button>
            <button type="button" class="btn small" data-answer="${esc(f.id)}" data-delete="">Bring them back</button></div></div>`);
        const rows = sync.folders.map((f) => {
            const s = f.status;
            const p = s.progress;
            const detail = s.state === 'problem' ? (s.message || s.problems[0] || '')
                : s.state === 'syncing' && p && p.total ? `${p.done + 1} of ${p.total} · ${p.current}`
                : f.on_demand ? `On demand · ${s.stats || 'starting'} · ${f.local}`
                : `${f.local}${f.ago ? ` · ${f.ago}` : ''}${f.read_only ? ' · only comes down' : ''}`;
            const bar = s.state === 'syncing' && p && p.total ? `<div class="meter"><i style="width:${Math.round(100 * p.done / p.total)}%"></i></div>` : '';
            const copies = f.conflicts && f.conflicts.length ? `<span class="state problem" title="Both versions were kept">${f.conflicts.length} ${f.conflicts.length === 1 ? 'copy' : 'copies'}</span>` : '';
            const fresh = !seen.has(f.id);
            seen.add(f.id);
            return `<div class="item sync${fresh ? ' new' : ''}">${icon('sync', 'tile')}
                <div><strong>${esc(f.label)}</strong><small title="${esc(detail)}">${esc(detail)}</small>${bar}</div>
                <div class="item-tail">${copies}<span class="state ${esc(s.state)}">${STATES[s.state] || esc(s.state)}</span>
                <button type="button" class="btn small ghost" data-options="${esc(f.id)}" title="Options and what happened">${icon('dots')}</button></div></div>`;
        });
        const list = $('sync-list');
        list.innerHTML = rows.length ? asks.join('') + rows.join('')
            : `<button type="button" class="empty-sync" data-add>${icon('plus', 'tile')}<span><strong>Add a sync folder</strong><small>Keep a folder of the NAS on this computer, also offline.</small></span></button>`;
        list.querySelectorAll('[data-options]').forEach((b) => b.addEventListener('click', () => openFolder(b.dataset.options)));
        list.querySelectorAll('[data-add]').forEach((b) => b.addEventListener('click', () => $('sync-add').click()));
        list.querySelectorAll('[data-answer]').forEach((b) => b.addEventListener('click', () => run(b, async () => {
            state.sync = await invoke('sync_answer', { id: b.dataset.answer, delete: !!b.dataset.delete });
            toast(b.dataset.delete ? 'Deleting them on the other side too.' : 'They come back on the next round.');
            paintSync();
        }, $('home-error'))));
    }
    // The options of one sync folder: what is left out, what is kept twice, what happened lately.
    let optionsOf = null;
    function ago(at) {
        const s = Math.max(0, Math.round(Date.now() / 1000 - at));
        return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} days ago`;
    }
    function openFolder(id) {
        const f = ((state.sync || {}).folders || []).find((x) => x.id === id);
        if (!f) return;
        optionsOf = id;
        $('folder-title').textContent = f.label;
        $('folder-path').textContent = f.local;
        $('folder-exclude').value = (f.exclude || []).join('\n');
        const od = document.querySelector('#sheet-folder .od-only');
        od.hidden = !f.on_demand || !!(state.sync && state.sync.on_demand_forced);
        $('folder-keep').value = (f.keep || []).join('\n');
        $('folder-stats').textContent = f.status.stats || '';
        $('folder-error').textContent = '';
        const note = $('folder-conflicts');
        note.hidden = !(f.conflicts && f.conflicts.length);
        note.textContent = note.hidden ? '' : `Changed on both sides, so both were kept: ${f.conflicts.join(', ')}. Look at them in the folder and delete the one you do not need.`;
        const recent = f.status.recent || [];
        $('folder-recent').innerHTML = recent.length ? recent.slice(0, 15).map((a) => `<li><span>${esc(a.text)}</span><small>${esc(ago(a.at))}</small></li>`).join('')
            : '<li class="none">Nothing yet.</li>';
        $('sheet-folder').hidden = false;
    }
    function paint() {
        if (!state.set_up) {
            const note = $('connect-note');
            note.hidden = !state.status.message;
            note.textContent = state.status.message || '';
            if (state.status.message) { show('connect'); return; }
            if (!['connect', 'welcome'].includes(current())) show('welcome');
            return;
        }
        if (setup) return;      // still in the steps
        if (current() !== 'settings') show('home');
        const s = state.status;
        $('home-nas').textContent = state.nas || 'Your NAS';
        $('home-user').textContent = `${state.user} on ${state.computer}`;
        $('home-line').textContent = { ok: s.route === 'relay' ? 'Connected · through a relay' : s.route === 'home' ? 'Connected · at home' : `Connected · direct · ${s.ms} ms`, connecting: 'Connecting…', offline: 'Cannot be reached', off: 'Switched off' }[s.state] || s.state;
        $('hero').className = `hero ${s.state === 'ok' ? 'ok' : s.state === 'offline' ? 'bad' : s.state === 'connecting' ? 'busy' : 'off'}`;
        const note = $('home-note');
        note.hidden = !(s.state === 'offline' && s.message);
        note.textContent = s.state === 'offline' ? `${s.message} It tries again by itself.` : '';
        document.querySelectorAll('.hub-url').forEach((el) => { el.textContent = state.hub_url; });
        const online = s.state === 'ok';
        $('home-open').disabled = !online;
        $('home-browser').disabled = !online;
        $('home-drive').checked = s.drive;
        $('home-drive').disabled = !state.drive_supported || !online;
        $('drive-text').textContent = !state.drive_supported ? 'Not possible on this system. Use a sync folder.'
            : state.platform === 'windows' ? 'A network drive in Explorer, at home and away. Files up to 50 MB; bigger ones: a sync folder.'
            : 'Your shared folders in the file manager, at home and away.';
        $('home-connected').checked = state.connected;
        $('home-window').checked = state.use_window;
        $('home-autostart').checked = state.autostart;
        $('sync-add').disabled = !online;
        paintSync();
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
    async function openHub() {
        await invoke(state.use_window ? 'open_hub' : 'open_browser');
    }

    function wire() {
        paintIcons();
        $('welcome-go').addEventListener('click', () => show('connect'));
        document.querySelectorAll('[data-way]').forEach((b) => b.addEventListener('click', () => pickWay(b.dataset.way)));
        window.addEventListener('resize', glide);
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
        $('choose-done').addEventListener('click', (e) => run(e.currentTarget, async () => {
            state = await invoke('choose', { choice: { use_window: $('pick-window').checked, use_drive: $('pick-drive').checked, autostart: $('pick-autostart').checked } });
            if ($('pick-sync').checked) sync(); else done();
        }, $('choose-error')));
        $('sync-skip').addEventListener('click', done);
        $('sync-done').addEventListener('click', (e) => run(e.currentTarget, async () => {
            await addSync(pickers.setup.chosen());
            done();
        }, $('sync-error')));
        $('done-go').addEventListener('click', (e) => run(e.currentTarget, async () => {
            setup = false;
            paint();
            await openHub();
        }, $('home-error')));
        $('done-home').addEventListener('click', () => { setup = false; paint(); });

        $('home-open').addEventListener('click', (e) => run(e.currentTarget, openHub, $('home-error')));
        $('home-browser').addEventListener('click', (e) => run(e.currentTarget, () => invoke('open_browser'), $('home-error')));
        document.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', async () => {
            try { await navigator.clipboard.writeText(state.hub_url); toast('Address copied. It works in any browser on this computer.'); } catch (_e) { toast(state.hub_url); }
        }));
        $('sync-now').addEventListener('click', (e) => {
            const b = e.currentTarget;
            b.classList.add('working');
            invoke('sync_now').then((s) => { state.sync = s; paintSync(); }).catch((err) => toast(String(err)));
            setTimeout(() => { b.classList.remove('working'); refresh(); }, 1600);
        });
        $('home-settings').addEventListener('click', () => show('settings'));
        $('settings-back').addEventListener('click', () => show('home'));
        $('sync-pause').addEventListener('change', (e) => run(null, async () => {
            state.sync = await invoke('pause_sync', { paused: e.target.checked });
            toast(e.target.checked ? 'Sync paused. Nothing goes up or down until you switch it back.' : 'Syncing again.');
            paintSync();
        }, $('settings-error')));
        $('sync-add').addEventListener('click', () => {
            $('sheet-error').textContent = '';
            $('sheet-ondemand').checked = false;
            onDemandRows();
            $('sheet').hidden = false;
            $('sheet-done').disabled = true;
            pickers.sheet.reset();
        });
        const closeFolder = () => { $('sheet-folder').hidden = true; optionsOf = null; };
        $('folder-close').addEventListener('click', closeFolder);
        $('folder-free').addEventListener('click', (e) => run(e.currentTarget, async () => {
            const freed = await invoke('free_space', { id: optionsOf });
            toast(`Freed ${freed}. Files are fetched again when you open them.`);
        }, $('folder-error')));
        $('folder-open').addEventListener('click', () => invoke('open_sync', { id: optionsOf }).catch((e) => toast(String(e))));
        $('folder-remove').addEventListener('click', (e) => {
            if (!window.confirm('Stop syncing this folder? The files on this computer and on the NAS stay as they are.')) return;
            const id = optionsOf;
            run(e.currentTarget, async () => { state.sync = await invoke('remove_sync', { id }); closeFolder(); syncKey = ''; paintSync(); }, $('folder-error'));
        });
        $('sheet-folder').addEventListener('click', (e) => { if (e.target === $('sheet-folder')) closeFolder(); });
        $('folder-save').addEventListener('click', (e) => run(e.currentTarget, async () => {
            state.sync = await invoke('set_exclude', { id: optionsOf, text: $('folder-exclude').value });
            const f = ((state.sync || {}).folders || []).find((x) => x.id === optionsOf);
            if (f && f.on_demand) state.sync = await invoke('set_keep', { id: optionsOf, text: $('folder-keep').value });
            syncKey = '';
            closeFolder();
            paintSync();
            toast('Saved. The next round leaves these out.');
        }, $('folder-error')));
        const closeSheet = () => { $('sheet').hidden = true; };
        $('sheet-cancel').addEventListener('click', closeSheet);
        $('sheet').addEventListener('click', (e) => { if (e.target === $('sheet')) closeSheet(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeSheet(); closeFolder(); } });
        $('sheet-done').addEventListener('click', (e) => run(e.currentTarget, async () => {
            await addSync(pickers.sheet.chosen());
            closeSheet();
            paintSync();
            toast('Added. The first round starts in a moment.');
        }, $('sheet-error')));

        $('home-drive').addEventListener('change', (e) => run(null, async () => {
            const on = e.target.checked;
            try {
                const place = await invoke('drive', { on });
                toast(on ? `Files is a drive now${place ? `: ${place}` : ''}.` : 'The drive is removed.');
            } catch (err) { e.target.checked = !on; throw err; }
            await setChoice({ use_drive: on });
        }, $('settings-error')));
        $('home-connected').addEventListener('change', (e) => run(null, async () => {
            state = await invoke('set_connected', { on: e.target.checked });
            paint();
        }, $('settings-error')));
        $('home-window').addEventListener('change', (e) => run(null, () => setChoice({ use_window: e.target.checked }), $('settings-error')));
        $('home-autostart').addEventListener('change', (e) => run(null, () => setChoice({ autostart: e.target.checked }), $('settings-error')));
        $('home-signout').addEventListener('click', (e) => {
            if (!window.confirm('Sign out this computer? It is removed on the NAS too; connect it again with a new link from the Hub. Sync folders stay on this computer.')) return;
            run(e.currentTarget, async () => {
                state = await invoke('sign_out');
                pickWay('link');
                $('bridge').className = 'bridge';
                seen.clear();
                syncKey = '';
                show('welcome');
                paint();
            }, $('settings-error'));
        });
    }

    function preview() {
        const kind = new URLSearchParams(location.search).get('preview') || 'welcome';
        const folders = [
            { id: 'a1', label: 'Family › Photos', share: 'Family', folder: 'Photos', local: '/home/anna/AlvaOS/Family/Photos', read_only: false, ago: '2 min ago', exclude: ['*.mov', 'Videos/'], conflicts: ['letter (conflict laptop 2026-10-28 12-00).odt'], status: { state: 'ok', last: 1, message: '', ask: 0, problems: [], changed: 0, recent: [{ at: Date.now() / 1000 - 90, text: 'Sent Docs/letter.odt' }, { at: Date.now() / 1000 - 400, text: 'Received Photos/2024/beach.jpg' }] } },
            { id: 'b2', label: 'anna', share: 'anna', folder: '', local: '/home/anna/AlvaOS/anna', read_only: false, ago: null, status: { state: 'syncing', last: null, message: '', ask: 0, problems: [], changed: 0, progress: { done: 3, total: 12, current: 'IMG_2041.jpg (to the NAS)' } } },
            { id: 'd4', label: 'Videos', share: 'Videos', folder: '', local: '/home/anna/AlvaOS/Videos', read_only: false, on_demand: true, keep: ['Holiday/'], exclude: [], conflicts: [], ago: null, status: { state: 'ok', last: 1, message: '', ask: 0, problems: [], changed: 0, stats: '1,204 files · 3.2 GB here', recent: [] } },
            { id: 'c3', label: 'Music', share: 'Music', folder: '', local: '/home/anna/AlvaOS/Music', read_only: true, ago: '1 h ago', status: { state: 'ask', last: 1, message: '', ask: 34, problems: [], changed: 0 } },
        ];
        const fake = {
            set_up: !['welcome', 'connect'].includes(kind), nas: 'cygnus', user: 'anna', link: true, use_window: true, use_drive: true, connected: true, autostart: true,
            hub_url: 'http://127.0.0.1:18090', dav_url: 'http://127.0.0.1:18091', platform: 'linux', drive_supported: true, version: '0.6.0', computer: 'anna-laptop',
            status: { state: 'ok', route: 'direct', ms: 14, message: '', drive: true }, line: 'Connected to cygnus · direct · 14 ms',
            sync: { folders: kind === 'home' ? folders : [], paused: false, root: '/home/anna/AlvaOS', line: kind === 'home' ? 'Syncing anna…' : '', on_demand_supported: true },
        };
        if (kind === 'connect') setTimeout(() => show('connect'), 0);
        if (['choose', 'sync', 'done'].includes(kind)) {
            setup = true;
            setTimeout(() => {
                state = fake;
                if (kind === 'choose') choose();
                if (kind === 'sync') sync();
                if (kind === 'done') { state.sync.folders = [folders[0]]; done(); }
            }, 0);
        }
        return async (cmd, args) => {
            await wait(cmd === 'connect' ? 1400 : 250);
            if (cmd === 'parse_link') { if (!String(args.text).startsWith('alvaos://')) throw new Error('not a link'); return { nas: 'cygnus', user: 'anna', away: true }; }
            if (cmd === 'connect') { fake.set_up = true; return fake; }
            if (cmd === 'choose') return { ...fake, set_up: true, ...args.choice };
            if (cmd === 'sign_out') return { ...fake, set_up: false };
            if (cmd === 'remote_shares') return [{ name: 'anna', access: 'write' }, { name: 'Family', access: 'write' }, { name: 'Music', access: 'read' }];
            if (cmd === 'remote_folders') return args.path ? ['2023', '2024', '2025'] : ['Documents', 'Photos', 'Projects', 'Videos'];
            if (cmd === 'add_sync') return { ...fake.sync, folders: [{ ...folders[0], label: [args.share, args.folder].filter(Boolean).join(' › '), local: `/home/anna/AlvaOS/${args.share}${args.folder ? `/${args.folder}` : ''}` }] };
            if (['sync_now', 'pause_sync', 'remove_sync', 'sync_answer', 'set_exclude', 'set_keep'].includes(cmd)) return fake.sync;
            if (cmd === 'pick_local') return null;
            if (cmd === 'free_space') return '2.1 GB';
            return fake;
        };
    }

    wire();
    refresh();
    // While the window is open: the status as it is.
    setInterval(() => { if (!document.hidden && !busy && !setup && state && state.set_up) refresh(); }, 2500);
})();
