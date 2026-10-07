// AlvaOS Hub, with Files as its first app: the shared folders as an app. Sign in with your share
// password; everything you do runs as you on the NAS, so you see and change
// exactly what you may over the network.
(function () {
    'use strict';

    // ── Icons (Lucide, ISC) ────────────────────────────────────────────────
    const P = {
        folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
        'folder-fill': '<path fill="currentColor" stroke="none" d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
        file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
        doc: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
        pdf: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M9 11h6"/>',
        image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
        video: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="m10 8 6 4-6 4Z"/>',
        audio: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
        archive: '<rect width="20" height="5" x="2" y="3" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
        trash: '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
        menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
        left: '<path d="m15 18-6-6 6-6"/>',
        right: '<path d="m9 18 6-6-6-6"/>',
        search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
        grid: '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
        list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
        apps: '<rect width="7" height="7" x="3" y="3" rx="1.5"/><rect width="7" height="7" x="14" y="3" rx="1.5"/><rect width="7" height="7" x="3" y="14" rx="1.5"/><path d="M17.5 14v7M14 17.5h7"/>',
        sort: '<path d="m21 16-4 4-4-4"/><path d="M17 20V4"/><path d="m3 8 4-4 4 4"/><path d="M7 4v16"/>',
        check: '<path d="M20 6 9 17l-5-5"/>',
        plus: '<path d="M5 12h14M12 5v14"/>',
        upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
        'folder-plus': '<path d="M12 10v6M9 13h6"/><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
        x: '<path d="M18 6 6 18M6 6l12 12"/>',
        download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
        pen: '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/>',
        more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
        open: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
        lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
        undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
        link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
        copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
        clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
        monitor: '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8M12 17v4"/>',
        calendar: '<path d="M8 2v4M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>',
        'message-circle': '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    };
    const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || P.file}</svg>`;
    const paintIcons = (root) => (root || document).querySelectorAll('[data-icon]').forEach((el) => { if (!el.firstChild) el.innerHTML = icon(el.dataset.icon); });

    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    const KINDS = [
        ['image', /\.(jpe?g|png|gif|webp|avif|bmp|heic)$/i], ['video', /\.(mp4|webm|m4v|mov|mkv|avi)$/i],
        ['audio', /\.(mp3|ogg|wav|flac|m4a|aac)$/i], ['pdf', /\.pdf$/i],
        ['doc', /\.(txt|md|log|csv|json|xml|ya?ml|ini|conf|cfg|docx?|odt|xlsx?|ods|pptx?|odp|rtf)$/i],
        ['archive', /\.(zip|tar|gz|tgz|7z|rar|bz2|xz)$/i],
    ];
    const kindOf = (e) => (e.type === 'folder' ? 'folder' : (KINDS.find(([, re]) => re.test(e.name)) || ['file'])[0]);
    const THUMB = /\.(jpe?g|png|gif|webp|bmp)$/i;
    const VIEW = { image: /\.(jpe?g|png|gif|webp|avif|bmp)$/i, video: /\.(mp4|webm|m4v)$/i, audio: /\.(mp3|ogg|wav|flac|m4a)$/i, text: /\.(txt|md|log|csv|json|xml|ya?ml|ini|conf|cfg)$/i };

    function bytes(n) {
        n = Number(n) || 0;
        if (n < 1024) return `${n} B`;
        const u = ['KB', 'MB', 'GB', 'TB']; let v = n / 1024; let i = 0;
        while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
        return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
    }
    function when(iso) {
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return '';
        const today = new Date();
        if (d.toDateString() === today.toDateString()) return `Today ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
        return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric' });
    }

    function whenExact(iso) {
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return '';
        return `${when(iso).replace(/^Today .*/, 'Today')}, ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    }

    // ── Talking to the NAS ─────────────────────────────────────────────────
    async function api(path, options = {}) {
        const opts = { credentials: 'same-origin', ...options, headers: { ...(options.headers || {}) } };
        if (opts.method && opts.method !== 'GET') opts.headers['X-AlvaOS-Files'] = '1';
        if (opts.json !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(opts.json);
            delete opts.json;
        }
        const res = await fetch(`/api/${path}`, opts);
        const data = await res.json().catch(() => ({}));
        if (res.status === 401 && data.signed_out) { showSignin(); throw new Error('Please sign in.'); }
        if (!res.ok) { const err = new Error(data.error || 'That did not work.'); err.data = data; throw err; }
        return data;
    }

    function toast(text, kind) {
        const el = document.createElement('div');
        el.className = `toast ${kind || ''}`;
        el.textContent = text;
        $('toasts').appendChild(el);
        setTimeout(() => el.remove(), kind === 'error' ? 6000 : 3500);
    }

    // ── State ──────────────────────────────────────────────────────────────
    let me = null;
    let share = '';
    let path = '';
    let entries = [];
    let shown = [];
    let selected = new Set();
    let anchor = -1;
    let view = 'grid';
    try { view = localStorage.getItem('alvaos_files_view') || 'grid'; } catch (_e) { /* storage off */ }
    // How a folder is sorted: name, modified, size or type; folders stay first.
    let sortBy = 'name:asc';
    try { sortBy = localStorage.getItem('alvaos_files_sort') || 'name:asc'; } catch (_e) { /* storage off */ }
    let loadId = 0;
    let found = null;       // search results below the folder, or null
    let foundInfo = { q: '', complete: true };
    let searchAll = false;   // false: this folder and below; true: every shared folder
    let searchId = 0;
    // Results come from many folders: they are opened or shown in their
    // folder, changes happen there.
    const access = () => (found ? 'read' : (me?.shares || []).find((s) => s.name === share)?.access || 'read');

    // ── Sign in ────────────────────────────────────────────────────────────
    function showSignin() {
        $('app').hidden = true;
        $('signin').hidden = false;
        $('in-user').focus();
    }

    $('signin-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = $('signin-btn');
        btn.disabled = true;
        $('signin-error').textContent = '';
        try {
            await api('login', { method: 'POST', json: { username: $('in-user').value.trim(), password: $('in-pass').value, code: $('in-code').value.trim() } });
            $('in-pass').value = '';
            $('in-code').value = '';
            await start();
        } catch (err) {
            if (err.data?.needs_code) {
                $('field-code').hidden = false;
                $('signin-sub').textContent = 'One more step: the code from your authenticator app.';
                if (err.data.error) $('signin-error').textContent = err.data.error;
                $('in-code').focus();
            } else {
                $('signin-error').textContent = err.message;
            }
        } finally {
            btn.disabled = false;
        }
    });

    async function signOut() {
        await api('logout', { method: 'POST' }).catch(() => {});
        me = null;
        leaveView();
        window.dispatchEvent(new Event('hub-signout'));   // the other apps forget what they showed
        showSignin();
    }
    $('signout').addEventListener('click', signOut);

    // ── Navigation and history ─────────────────────────────────────────────
    function hashFor(s, p) {
        const q = new URLSearchParams();
        if (s) q.set('share', s);
        if (p) q.set('path', p);
        return `#${q}`;
    }
    function go(s, p, push = true) {
        leaveView();
        current = 'files';
        share = s;
        path = p || '';
        found = null;
        $('search').value = '';
        $('main').querySelector('.bar').classList.remove('find');
        leavePhotos();
        selected = new Set();
        anchor = -1;
        if (push && location.hash !== hashFor(share, path)) history.pushState(null, '', hashFor(share, path));
        closeSide();
        renderSide();
        load();
    }
    window.addEventListener('popstate', () => {
        const q = new URLSearchParams(location.hash.slice(1));
        const s = q.get('share');
        if (VIEWS[q.get('app')] && hubApps.some((a) => a.id === q.get('app'))) openApp(q.get('app'), false);
        else if (s && me?.shares.some((x) => x.name === s)) go(s, q.get('path') || '', false);
    });
    $('back-btn').addEventListener('click', () => history.back());
    $('fwd-btn').addEventListener('click', () => history.forward());
    const up = () => { if (path) go(share, path.split('/').slice(0, -1).join('/')); };

    function renderSide() {
        $('share-list').innerHTML = (me?.shares || []).map((s) => `
            <button type="button" class="side-item${s.name === share ? ' active' : ''}" data-share="${esc(s.name)}">
                <span class="ic">${icon('folder-fill')}</span><span>${esc(s.name)}</span>
                ${s.access === 'read' ? `<span class="ro" title="You can look, not change">${icon('lock').replace('<svg', '<svg width="13" height="13"')}</span>` : ''}
            </button>`).join('');
        const parts = path ? path.split('/') : [];
        $('crumbs').innerHTML = [`<button type="button" class="crumb" data-path="">${esc(share)}</button>`,
            ...parts.map((p, i) => `<span class="sep">›</span><button type="button" class="crumb" data-path="${esc(parts.slice(0, i + 1).join('/'))}">${esc(p)}</button>`)].join('');
        const canWrite = access() === 'write';
        $('new-btn').hidden = !canWrite;
        $('back-btn').disabled = false;
        showSpace();
    }

    // "42 of 100 GB": the space limit of the open folder, when it has one.
    let spaceFor = '';
    let spaceAt = 0;
    async function showSpace() {
        const box = $('space');
        const info = (me?.shares || []).find((s) => s.name === share);
        if (!info || !info.limited) { box.hidden = true; spaceFor = ''; return; }
        if (spaceFor === share && !box.hidden && Date.now() - spaceAt < 60000) return;
        spaceFor = share;
        spaceAt = Date.now();
        try {
            const data = await api(`space?share=${encodeURIComponent(share)}`);
            if (spaceFor !== share || !data.limit_bytes || data.used_bytes === null || data.used_bytes === undefined) {
                box.hidden = true;
                return;
            }
            const part = Math.min(1, data.used_bytes / data.limit_bytes);
            box.className = `space${part >= 0.99 ? ' full' : part >= 0.9 ? ' warn' : ''}`;
            box.innerHTML = `<div><strong>${esc(share)}</strong>: ${esc(bytes(data.used_bytes))} of ${esc(bytes(data.limit_bytes))} used</div>
                <div class="space-bar" role="progressbar" aria-label="Space used in ${esc(share)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(part * 100)}"><span style="width:${(part * 100).toFixed(1)}%"></span></div>`;
            box.hidden = false;
        } catch (_e) {
            box.hidden = true;
        }
    }
    $('share-list').addEventListener('click', (e) => { const b = e.target.closest('[data-share]'); if (b) go(b.dataset.share, ''); });
    $('crumbs').addEventListener('click', (e) => { const b = e.target.closest('[data-path]'); if (b) go(share, b.dataset.path); });

    const openSide = () => { $('side').classList.add('open'); $('scrim').hidden = false; };
    function closeSide() { $('side').classList.remove('open'); $('scrim').hidden = true; }
    $('menu-btn').addEventListener('click', openSide);
    $('scrim').addEventListener('click', closeSide);

    // ── Listing ────────────────────────────────────────────────────────────
    async function load() {
        const id = ++loadId;
        $('items').className = `items ${view}`;
        $('items').innerHTML = '<div class="empty">Loading…</div>';
        $('status').textContent = '';
        try {
            const data = await api(`list?share=${encodeURIComponent(share)}&path=${encodeURIComponent(path)}`);
            if (id !== loadId) return;
            entries = (data.entries || []).filter((e) => !e.name.startsWith('.') && (e.type === 'file' || e.type === 'folder'));
            render();
        } catch (err) {
            if (id !== loadId) return;
            entries = [];
            $('items').innerHTML = `<div class="empty">${icon('lock')}<strong>Cannot open this folder</strong>${esc(err.message)}</div>`;
        }
    }

    const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
    const extOf = (e) => (e.type === 'folder' || !e.name.includes('.') ? '' : e.name.split('.').pop().toLowerCase());
    function compare(a, b) {
        const [key, dir] = sortBy.split(':');
        const sign = dir === 'desc' ? -1 : 1;
        const folders = (a.type === 'folder' ? 0 : 1) - (b.type === 'folder' ? 0 : 1);
        if (folders) return folders;
        let d = 0;
        if (key === 'modified') d = String(a.modified_at || '').localeCompare(String(b.modified_at || ''));
        else if (key === 'size') d = (Number(a.size_bytes) || 0) - (Number(b.size_bytes) || 0);
        else if (key === 'type') d = extOf(a).localeCompare(extOf(b)) || kindOf(a).localeCompare(kindOf(b));
        else d = byName(a, b);
        return sign * d || byName(a, b);
    }
    function setSort(value) {
        sortBy = value;
        try { localStorage.setItem('alvaos_files_sort', value); } catch (_e) { /* off */ }
        render();
    }
    const SORT_HEADS = { name: ['name:asc', 'name:desc'], size: ['size:desc', 'size:asc'], modified: ['modified:desc', 'modified:asc'] };
    function headCell(key, label, extra = '') {
        const [key0, dir] = sortBy.split(':');
        const on = key0 === key;
        const next = on && sortBy === SORT_HEADS[key][0] ? SORT_HEADS[key][1] : SORT_HEADS[key][0];
        return `<button type="button" class="hsort${on ? ' on' : ''}" data-sort="${next}"${extra} aria-label="Sort by ${label.toLowerCase()}">${label}${on ? `<span aria-hidden="true">${dir === 'desc' ? ' ↓' : ' ↑'}</span>` : ''}</button>`;
    }

    function render() {
        const q = found ? '' : $('search').value.trim().toLowerCase();
        shown = found ? found.slice() : entries.filter((e) => !q || e.name.toLowerCase().includes(q));
        if (!found) shown.sort(compare);
        const items = $('items');
        items.className = `items ${view}`;
        $('new-btn').hidden = access() !== 'write';
        $('view-grid').setAttribute('aria-pressed', view === 'grid');
        $('view-list').setAttribute('aria-pressed', view === 'list');
        if (found && !shown.length && foundInfo.photos) {
            const own = (foundInfo.sources || []).find((src) => src.own);
            items.innerHTML = `<div class="empty">${icon('image')}<strong>No photos or videos yet</strong>${own
                ? `Put pictures into "${esc([own.share, own.path].filter(Boolean).join(' › '))}" and they show up here, newest first.`
                : 'You have no folder for photos yet. Ask whoever runs this NAS for a personal folder or a photo library.'}</div>`;
        } else if (found && !shown.length) {
            items.innerHTML = `<div class="empty">${icon('search')}<strong>Nothing found</strong>No name here or in a folder below contains "${esc(foundInfo.q)}".</div>`;
        } else if (found && foundInfo.photos) {
            // The Photos view: a timeline by month, only pictures, no names.
            let month = '';
            items.className = 'items grid photos';
            items.innerHTML = shown.map((e, i) => {
                const d = new Date(shotAt(e));
                const label = Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString([], { month: 'long', year: 'numeric' });
                const head = label !== month ? `<div class="month">${esc(label)}</div>` : '';
                month = label;
                const k = kindOf(e);
                const thumb = THUMB.test(e.name)
                    ? `<img loading="lazy" decoding="async" alt="${esc(e.name)}" src="/api/thumb?share=${encodeURIComponent(sh(e))}&path=${encodeURIComponent(rel(e))}&v=${encodeURIComponent(e.modified_at || '')}" data-fallback="${k}">`
                    : `<span class="pvideo">${icon(k)}</span>`;
                return `${head}<div class="ptile" data-i="${i}" title="${esc(rel(e))}">${thumb}</div>`;
            }).join('');
        } else if (!shown.length) {
            items.innerHTML = q
                ? `<div class="empty">${icon('search')}<strong>Nothing found</strong>No name in this folder contains "${esc(q)}".</div>`
                : `<div class="empty">${icon('folder')}<strong>This folder is empty</strong>${access() === 'write' ? 'Drop files here, or use New › Upload files.' : ''}</div>`;
        } else if (view === 'grid') {
            items.innerHTML = shown.map((e, i) => {
                const k = kindOf(e);
                const thumb = THUMB.test(e.name)
                    ? `<div class="thumb"><img loading="lazy" decoding="async" alt="" src="/api/thumb?share=${encodeURIComponent(sh(e))}&path=${encodeURIComponent(rel(e))}&v=${encodeURIComponent(e.modified_at || '')}" data-fallback="${k}"></div>`
                    : `<div class="thumb icon kind-${k}">${icon(k === 'folder' ? 'folder-fill' : k)}</div>`;
                return `<div class="tile" data-i="${i}" title="${esc(found ? rel(e) : e.name)}" draggable="${access() === 'write'}">${thumb}<div class="name">${esc(e.name)}</div>${found ? `<div class="where">${esc(searchAll ? where(e) : where(e).split(' › ').pop())}</div>` : ''}
                    <button type="button" class="more" data-more="${i}" aria-label="More for ${esc(e.name)}">${icon('more')}</button></div>`;
            }).join('');
        } else {
            const heads = found ? '<span>Name</span><span style="text-align:right">Size</span><span>Modified</span>'
                : `${headCell('name', 'Name')}${headCell('size', 'Size', ' style="justify-content:flex-end"')}${headCell('modified', 'Modified')}`;
            items.innerHTML = `<div class="head"><span></span>${heads}<span></span></div>` + shown.map((e, i) => {
                const k = kindOf(e);
                const lead = THUMB.test(e.name)
                    ? `<img class="rthumb" loading="lazy" alt="" src="/api/thumb?share=${encodeURIComponent(sh(e))}&path=${encodeURIComponent(rel(e))}&v=${encodeURIComponent(e.modified_at || '')}" data-fallback="${k}">`
                    : `<span class="ricon kind-${k}">${icon(k === 'folder' ? 'folder-fill' : k)}</span>`;
                const size = e.type === 'folder' ? '' : bytes(e.size_bytes);
                return `<div class="row" data-i="${i}" draggable="${access() === 'write'}">${lead}<div class="rtext"><div class="rname">${esc(e.name)}</div>${found ? `<div class="rsub rwhere" style="display:block">${esc(where(e))}</div>` : `<div class="rsub">${esc([size, when(e.modified_at)].filter(Boolean).join(' · '))}</div>`}</div>
                    <span class="rsize">${esc(size)}</span><span class="rdate">${esc(when(e.modified_at))}</span>
                    <button type="button" class="more" data-more="${i}" aria-label="More for ${esc(e.name)}">${icon('more')}</button></div>`;
            }).join('');
        }
        items.querySelectorAll('img[data-fallback]').forEach((img) => img.addEventListener('error', () => {
            const k = img.dataset.fallback;
            if (img.parentElement?.classList.contains('ptile')) {
                const box = document.createElement('span');
                box.className = 'pvideo';
                box.innerHTML = icon(k);
                img.replaceWith(box);
                return;
            }
            const box = document.createElement(img.classList.contains('rthumb') ? 'span' : 'div');
            box.className = img.classList.contains('rthumb') ? `ricon kind-${k}` : `thumb icon kind-${k}`;
            box.innerHTML = icon(k);
            (img.classList.contains('rthumb') ? img : img.parentElement).replaceWith(box);
        }, { once: true }));
        if (found) {
            const head = document.createElement('div');
            head.className = 'results-head';
            const here = path ? path.split('/').pop() : share;
            head.innerHTML = foundInfo.photos
                ? `${icon('image').replace('<svg', '<svg width="16" height="16"')}<span>${found.length}${foundInfo.complete ? '' : '+'} photos and videos, newest first</span>
                <button type="button" class="link" id="results-close">Back to the folder</button>`
                : `${icon('search').replace('<svg', '<svg width="16" height="16"')}<span>${found.length}${foundInfo.complete ? '' : '+'} found for "${esc(foundInfo.q)}"${foundInfo.complete ? '' : ' · type more to narrow it down'}</span>
                <span class="scope" role="group" aria-label="Where to search"><button type="button" data-scope="here" aria-pressed="${!searchAll}">In ${esc(here)}</button><button type="button" data-scope="all" aria-pressed="${searchAll}">All shared folders</button></span>
                <button type="button" class="link" id="results-close">Back to the folder</button>`;
            items.prepend(head);
            head.querySelector('#results-close').addEventListener('click', clearSearch);
            head.querySelectorAll('[data-scope]').forEach((b) => b.addEventListener('click', () => {
                searchAll = b.dataset.scope === 'all';
                deepSearch();
            }));
        }
        paintSelection();
    }

    const shotAt = (e) => String(e.taken_at || e.modified_at || '').slice(0, 19);
    const rel = (e) => (e.folder !== undefined ? (e.folder ? `${e.folder}/${e.name}` : e.name) : (path ? `${path}/${e.name}` : e.name));
    const sh = (e) => e.share || share;
    const where = (e) => [sh(e), ...(e.folder ? e.folder.split('/') : [])].join(' › ');

    function paintSelection() {
        $('items').querySelectorAll('[data-i]').forEach((el) => el.classList.toggle('selected', selected.has(shown[Number(el.dataset.i)]?.name)));
        const n = selected.size;
        $('selbar').hidden = n === 0;
        const files = shown.filter((e) => e.type === 'file');
        const folders = shown.length - files.length;
        if (n) {
            const sel = shown.filter((e) => selected.has(e.name));
            const size = sel.filter((e) => e.type === 'file').reduce((s, e) => s + (e.size_bytes || 0), 0);
            $('sel-count').textContent = `${n} selected${size ? ` · ${bytes(size)}` : ''}`;
            const canWrite = access() === 'write';
            $('sel-rename').hidden = !canWrite || n !== 1;
            $('sel-delete').hidden = !canWrite;
            $('sel-download').hidden = false;
        }
        if (found) { $('status').textContent = foundInfo.photos ? 'Photos · click one to look through them' : 'Search results · open one, or "Show in folder" to change it'; return; }
        $('status').textContent = [folders ? `${folders} folder${folders === 1 ? '' : 's'}` : '',
            files.length ? `${files.length} file${files.length === 1 ? '' : 's'} (${bytes(files.reduce((s, e) => s + (e.size_bytes || 0), 0))})` : '',
            access() === 'read' ? 'You can look at this folder, not change it' : ''].filter(Boolean).join(' · ');
    }

    // ── Selecting and opening ──────────────────────────────────────────────
    function select(i, e) {
        const name = shown[i].name;
        if (e && (e.ctrlKey || e.metaKey)) {
            if (selected.has(name)) selected.delete(name); else selected.add(name);
            anchor = i;
        } else if (e && e.shiftKey && anchor >= 0) {
            const [a, b] = [Math.min(anchor, i), Math.max(anchor, i)];
            selected = new Set(shown.slice(a, b + 1).map((x) => x.name));
        } else {
            selected = new Set([name]);
            anchor = i;
        }
        paintSelection();
    }

    const coarse = window.matchMedia('(pointer: coarse)');
    $('items').addEventListener('click', (e) => {
        const more = e.target.closest('[data-more]');
        if (more) {
            e.stopPropagation();
            const i = Number(more.dataset.more);
            if (!selected.has(shown[i].name)) select(i);
            const r = more.getBoundingClientRect();
            showContext(r.left, r.bottom + 4);
            return;
        }
        const item = e.target.closest('[data-i]');
        if (!item) { selected = new Set(); paintSelection(); return; }
        const i = Number(item.dataset.i);
        // Touch: a tap opens, like on a phone. Mouse: click selects, double-click opens.
        // In Photos a click opens the picture, like in any photo app.
        if ((found && foundInfo.photos && !(e.ctrlKey || e.metaKey || e.shiftKey)) || (coarse.matches && !selected.size)) open(shown[i]);
        else select(i, e);
    });
    $('items').addEventListener('dblclick', (e) => {
        const item = e.target.closest('[data-i]');
        if (item) open(shown[Number(item.dataset.i)]);
    });
    $('items').addEventListener('contextmenu', (e) => {
        e.preventDefault();
        const item = e.target.closest('[data-i]');
        if (item) {
            const i = Number(item.dataset.i);
            if (!selected.has(shown[i].name)) select(i);
        } else {
            selected = new Set();
            paintSelection();
        }
        showContext(e.clientX, e.clientY);
    });

    function selectedEntries() { return shown.filter((e) => selected.has(e.name)); }

    async function open(entry) {
        if (!entry) return;
        if (entry.type === 'folder') { go(sh(entry), rel(entry)); return; }
        if (/\.pdf$/i.test(entry.name)) {
            const w = window.open('', '_blank');
            try { const url = await link(entry, true); if (w) w.location.href = url; else location.href = url; } catch (err) { if (w) w.close(); toast(err.message, 'error'); }
            return;
        }
        const kind = Object.keys(VIEW).find((k) => VIEW[k].test(entry.name));
        if (kind && !(kind === 'text' && entry.size_bytes > 2 * 1024 * 1024)) { showViewer(entry, kind); return; }
        download([entry]);
    }

    async function link(entry, inline) {
        return (await api('link', { method: 'POST', json: { share: sh(entry), path: rel(entry), inline: !!inline } })).url;
    }

    function zipDownload(folderPath, inShare = share) {
        const a = document.createElement('a');
        a.href = `/api/zip?${new URLSearchParams({ share: inShare, path: folderPath })}`;
        a.download = '';
        document.body.appendChild(a);
        a.click();
        a.remove();
        toast('The ZIP is made while it downloads; big folders take a while.');
    }

    async function download(list) {
        for (const entry of list.filter((e) => e.type === 'folder')) zipDownload(rel(entry), sh(entry));
        for (const entry of list.filter((e) => e.type === 'file')) {
            try {
                const a = document.createElement('a');
                a.href = await link(entry, false);
                a.download = entry.name;
                document.body.appendChild(a);
                a.click();
                a.remove();
            } catch (err) {
                toast(err.message, 'error');
            }
        }
    }

    // ── Context menu ───────────────────────────────────────────────────────
    function showContext(x, y) {
        const menu = $('ctx');
        const sel = selectedEntries();
        const canWrite = access() === 'write';
        const one = sel.length === 1 ? sel[0] : null;
        const rows = [];
        if (one) rows.push(`<button type="button" data-ctx="open">${icon(one.type === 'folder' ? 'folder' : 'open')}Open</button>`);
        if (one && found) rows.push(`<button type="button" data-ctx="reveal">${icon('folder')}Show in folder</button>`);
        if (sel.some((e) => e.type === 'file')) rows.push(`<button type="button" data-ctx="download">${icon('download')}Download</button>`);
        if (one && one.type === 'folder') rows.push(`<button type="button" data-ctx="zip">${icon('archive')}Download as ZIP</button>`);
        if (!sel.length && !found) rows.push(`<button type="button" data-ctx="zip-here">${icon('archive')}Download this folder as ZIP</button>`);
        if (canWrite && one) rows.push(`<button type="button" data-ctx="rename">${icon('pen')}Rename</button>`);
        if (one) rows.push(`<button type="button" data-ctx="share">${icon('link')}Share link…</button>`);
        if (one && one.type === 'file') rows.push(`<button type="button" data-ctx="versions">${icon('clock')}Previous versions…</button>`);
        if (canWrite && sel.length) rows.push(`<button type="button" data-ctx="move">${icon('folder')}Move to…</button>`, `<button type="button" data-ctx="copy">${icon('copy')}Copy to…</button>`);
        if (canWrite && sel.length) rows.push('<hr>', `<button type="button" class="danger" data-ctx="delete">${icon('trash')}Delete</button>`);
        if (!sel.length && canWrite) rows.push(`<button type="button" data-ctx="upload">${icon('upload')}Upload files</button>`, `<button type="button" data-ctx="folder">${icon('folder-plus')}New folder</button>`);
        if (!rows.length) return;
        menu.innerHTML = rows.join('');
        menu.hidden = false;
        const w = menu.offsetWidth;
        const h = menu.offsetHeight;
        menu.style.left = `${Math.max(8, Math.min(x, innerWidth - w - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(y, innerHeight - h - 8))}px`;
        menu.querySelector('button')?.focus();
    }
    const hideMenus = () => {
        $('ctx').hidden = true;
        $('new-menu').hidden = true;
        $('sort-menu').hidden = true;
        $('sort-btn').setAttribute('aria-expanded', 'false');
        if ($('store-menu')) {
            $('store-menu').hidden = true;
            $('rail-store').setAttribute('aria-expanded', 'false');
        }
    };
    document.addEventListener('click', (e) => { if (!e.target.closest('.menu') && !e.target.closest('#new-btn') && !e.target.closest('#sort-btn')) hideMenus(); });
    $('ctx').addEventListener('click', (e) => {
        const b = e.target.closest('[data-ctx]');
        if (!b) return;
        hideMenus();
        action(b.dataset.ctx);
    });

    function action(name) {
        const sel = selectedEntries();
        if (name === 'open') open(sel[0]);
        if (name === 'reveal' && sel.length === 1) reveal(sel[0]);
        if (name === 'download') download(sel);
        if (name === 'zip' && sel.length === 1) zipDownload(rel(sel[0]), sh(sel[0]));
        if (name === 'zip-here') zipDownload(path);
        if (name === 'rename' && sel.length === 1) rename(sel[0]);
        if (name === 'delete' && sel.length) remove(sel);
        if (name === 'move' && sel.length) moveDialog(sel);
        if (name === 'copy' && sel.length) moveDialog(sel, true);
        if (name === 'share' && sel.length === 1) shareDialog(sel[0]);
        if (name === 'versions' && sel.length === 1) versionsDialog(sel[0]);
        if (name === 'upload') $('file-input').click();
        if (name === 'folder') newFolder();
    }

    $('new-btn').addEventListener('click', () => { $('new-menu').hidden = !$('new-menu').hidden; });
    $('new-menu').addEventListener('click', (e) => {
        const b = e.target.closest('[data-new]');
        if (!b) return;
        hideMenus();
        action(b.dataset.new === 'upload' ? 'upload' : 'folder');
    });
    $('sel-clear').addEventListener('click', () => { selected = new Set(); paintSelection(); });
    $('sel-download').addEventListener('click', () => action('download'));
    $('sel-rename').addEventListener('click', () => action('rename'));
    $('sel-delete').addEventListener('click', () => action('delete'));
    $('sort-btn').addEventListener('click', () => {
        const menu = $('sort-menu');
        const opening = menu.hidden;
        hideMenus();
        if (!opening) return;
        menu.querySelectorAll('[data-sort]').forEach((b) => {
            const on = b.dataset.sort === sortBy;
            b.setAttribute('aria-checked', on);
            b.innerHTML = `<span class="mark">${on ? icon('check') : ''}</span>${b.textContent}`;
        });
        menu.hidden = false;
        $('sort-btn').setAttribute('aria-expanded', 'true');
        (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button')).focus();
    });
    $('sort-menu').addEventListener('click', (e) => {
        const b = e.target.closest('[data-sort]');
        if (!b) return;
        hideMenus();
        setSort(b.dataset.sort);
        $('sort-btn').focus();
    });
    $('items').addEventListener('click', (e) => {
        const b = e.target.closest('.hsort');
        if (b) setSort(b.dataset.sort);
    });
    $('view-grid').addEventListener('click', () => setView('grid'));
    $('view-list').addEventListener('click', () => setView('list'));
    function setView(v) { view = v; try { localStorage.setItem('alvaos_files_view', v); } catch (_e) { /* off */ } render(); }
    // Typing filters this folder at once; a moment later (or on Enter) the
    // folders below are searched too.
    let searchTimer = 0;
    async function deepSearch() {
        clearTimeout(searchTimer);
        const q = $('search').value.trim();
        if (q.length < 2) return;
        const id = ++searchId;
        $('status').textContent = 'Searching…';
        try {
            const data = await api(`search?${new URLSearchParams(searchAll ? { everywhere: '1', q } : { share, path, q })}`);
            if (id !== searchId || $('search').value.trim() !== q) return;
            found = (data.results || []).filter((e) => !e.name.startsWith('.'));
            foundInfo = { q, complete: !!data.complete };
            selected = new Set();
            anchor = -1;
            render();
        } catch (err) {
            if (id === searchId) $('status').textContent = err.message;
        }
    }
    function clearSearch() {
        $('search').value = '';
        found = null;
        leavePhotos();
        searchId++;
        $('main').querySelector('.bar').classList.remove('find');
        selected = new Set();
        render();
    }
    function reveal(entry) {
        const folder = entry.folder || '';
        go(sh(entry), folder);
        const name = entry.name;
        const wait = setInterval(() => {
            const i = shown.findIndex((x) => x.name === name);
            if (i < 0) return;
            clearInterval(wait);
            select(i);
            $('items').querySelector(`[data-i="${i}"]`)?.scrollIntoView({ block: 'nearest' });
        }, 100);
        setTimeout(() => clearInterval(wait), 5000);
    }
    $('search').addEventListener('input', () => {
        if (found) { found = null; selected = new Set(); leavePhotos(); }
        searchId++;
        render();
        clearTimeout(searchTimer);
        if ($('search').value.trim().length >= 2) searchTimer = setTimeout(deepSearch, 500);
    });
    $('search').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); deepSearch(); }
        if (e.key === 'Escape') { e.preventDefault(); clearSearch(); $('search').blur(); }
    });
    $('search-btn').addEventListener('click', () => {
        $('main').querySelector('.bar').classList.add('find');
        $('search').focus();
    });
    $('search').addEventListener('blur', () => {
        if (!$('search').value) $('main').querySelector('.bar').classList.remove('find');
    });

    // ── Dialogs ────────────────────────────────────────────────────────────
    function ask({ title, text, value, okLabel, danger }) {
        return new Promise((resolve) => {
            const wrap = $('dialog');
            const hasInput = value !== undefined;
            wrap.innerHTML = `<form class="dialog" novalidate><h2>${esc(title)}</h2>${text ? `<p>${esc(text)}</p>` : ''}
                ${hasInput ? `<input id="dlg-input" value="${esc(value)}" autocomplete="off" spellcheck="false">` : ''}
                <div class="actions"><button type="button" class="btn" data-cancel>Cancel</button><button type="submit" class="btn primary${danger ? ' danger-fill' : ''}">${esc(okLabel)}</button></div></form>`;
            wrap.hidden = false;
            const input = wrap.querySelector('#dlg-input');
            const done = (v) => { wrap.hidden = true; wrap.innerHTML = ''; resolve(v); };
            wrap.querySelector('[data-cancel]').onclick = () => done(null);
            wrap.onclick = (e) => { if (e.target === wrap) done(null); };
            wrap.querySelector('form').onsubmit = (e) => { e.preventDefault(); done(hasInput ? input.value.trim() : true); };
            if (input) {
                input.focus();
                const dot = value.lastIndexOf('.');
                input.setSelectionRange(0, dot > 0 ? dot : value.length);
            } else {
                wrap.querySelector('[type=submit]').focus();
            }
        });
    }

    async function rename(entry) {
        const name = await ask({ title: 'Rename', value: entry.name, okLabel: 'Rename' });
        if (!name || name === entry.name) return;
        try {
            await api('rename', { method: 'POST', json: { share, path, old: entry.name, new: name } });
            selected = new Set([name]);
            load();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function newFolder() {
        const name = await ask({ title: 'New folder', value: 'New folder', okLabel: 'Create' });
        if (!name) return;
        try {
            await api('mkdir', { method: 'POST', json: { share, path, name } });
            selected = new Set([name]);
            load();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function remove(list) {
        const what = list.length === 1 ? `"${list[0].name}"` : `${list.length} items`;
        if (!await ask({ title: `Delete ${what}?`, text: 'Deleted items go to the trash and can be put back for 30 days.', okLabel: 'Delete', danger: true })) return;
        try {
            const res = await api('delete', { method: 'POST', json: { share, path, names: list.map((e) => e.name) } });
            toast(res.failed?.length ? `Some items were not deleted: ${res.failed[0]}` : `${what} moved to the trash.`, res.failed?.length ? 'error' : '');
            selected = new Set();
            load();
        } catch (err) { toast(err.message, 'error'); }
    }

    // ── Share links ────────────────────────────────────────────────────────
    const fullUrl = (u) => `${location.origin}${u}`;
    async function copy(text) {
        try {
            await navigator.clipboard.writeText(text);   // only on https or localhost
            toast('Link copied.');
            return;
        } catch (_e) { /* plain http on the home network: copy the old way */ }
        const area = document.createElement('textarea');
        area.value = text;
        area.setAttribute('readonly', '');
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (_e) { ok = false; }
        area.remove();
        if (ok) toast('Link copied.'); else window.prompt('Copy the link:', text);
    }

    function shareDialog(entry) {
        const wrap = $('dialog');
        const canDrop = entry.type === 'folder' && (me?.shares || []).find((x) => x.name === sh(entry))?.access === 'write';
        wrap.innerHTML = `<form class="dialog" novalidate><h2>Share "${esc(entry.name)}"</h2>
            ${canDrop ? `<label class="field">People with the link can<select id="ln-mode" class="sel">
                <option value="view">Look at and download what is in it</option>
                <option value="upload">Only upload files into it (drop box)</option></select></label>
                <label class="field" id="ln-max-field" hidden>They may upload<select id="ln-max" class="sel">
                <option value="0">As much as the folder has room for</option><option value="1">Up to 1 GB in all</option>
                <option value="5" selected>Up to 5 GB in all</option><option value="20">Up to 20 GB in all</option><option value="100">Up to 100 GB in all</option></select></label>` : ''}
            <p id="ln-what">Anyone with the link can ${entry.type === 'folder' ? 'look at and download what is in this folder' : 'look at and download this file'}. They cannot change anything.</p>
            <label class="field">Works for<select id="ln-days" class="sel">
                <option value="1">1 day</option><option value="7" selected>7 days</option><option value="30">30 days</option>
                <option value="90">90 days</option><option value="0">Until I remove it</option></select></label>
            <label class="field">Password (optional)<input id="ln-pass" type="text" autocomplete="off" placeholder="Leave empty for no password"></label>
            <div class="notice" id="ln-error"></div>
            <div class="actions"><button type="button" class="btn" data-cancel>Cancel</button><button type="submit" class="btn primary">Create link</button></div></form>`;
        wrap.hidden = false;
        const close = () => { wrap.hidden = true; wrap.innerHTML = ''; };
        wrap.querySelector('[data-cancel]').onclick = close;
        $('ln-mode')?.addEventListener('change', () => {
            $('ln-max-field').hidden = $('ln-mode').value !== 'upload';
            $('ln-what').textContent = $('ln-mode').value === 'upload'
                ? 'Anyone with the link can add files to this folder. They cannot see, open or change what is in it; files with the same name are kept side by side.'
                : 'Anyone with the link can look at and download what is in this folder. They cannot change anything.';
        });
        wrap.querySelector('form').onsubmit = async (e) => {
            e.preventDefault();
            try {
                const link = await api('links', { method: 'POST', json: { share: sh(entry), path: rel(entry), kind: entry.type, mode: $('ln-mode')?.value || 'view', max_gb: Number($('ln-max')?.value || 0), days: Number($('ln-days').value), password: $('ln-pass').value } });
                const url = fullUrl(link.url);
                wrap.innerHTML = `<div class="dialog"><h2>Link ready</h2>
                    <p>${link.expires_at ? `Works until ${esc(when(link.expires_at))}` : 'Works until you remove it'}${link.has_password ? ', with a password' : ''}. You find it again under Shared links.</p>
                    <div class="linkbox"><input readonly value="${esc(url)}" id="ln-url"><button type="button" class="btn primary" id="ln-copy">${icon('copy')}Copy</button></div>
                    <div class="actions"><button type="button" class="btn" data-done>Done</button></div></div>`;
                $('ln-url').select();
                $('ln-copy').onclick = () => copy(url);
                wrap.querySelector('[data-done]').onclick = close;
            } catch (err) {
                $('ln-error').textContent = err.message;
            }
        };
        $('ln-days').focus();
    }

    // Older states of one file, from the restore points (Backup page).
    async function versionsDialog(entry) {
        const wrap = $('dialog');
        const filePath = rel(entry);
        const inShare = sh(entry);
        const canWrite = (me?.shares || []).find((x) => x.name === inShare)?.access === 'write';
        const close = () => { wrap.hidden = true; wrap.innerHTML = ''; };
        wrap.innerHTML = `<div class="dialog wide"><h2>Previous versions of "${esc(entry.name)}"</h2><p>Looking in the restore points…</p></div>`;
        wrap.hidden = false;
        let versions = [];
        let error = '';
        try { versions = (await api(`versions?${new URLSearchParams({ share: inShare, path: filePath })}`)).versions || []; } catch (err) { error = err.message; }
        if (wrap.hidden) return;
        wrap.innerHTML = `<div class="dialog wide"><h2>Previous versions of "${esc(entry.name)}"</h2>
            <p>${versions.length ? `Saved by the restore points.${canWrite ? ' "Restore" puts the old version next to this file, with the date in its name; nothing is overwritten.' : ''}` : ''}</p>
            <div class="trash-list">${error ? esc(error) : versions.length ? versions.map((v) => `<div class="trash-row"><div><strong>${icon('clock')} Changed ${esc(whenExact(v.modified_at))}</strong>
                <small>${esc(bytes(v.size_bytes))} · in the restore point of ${esc(when(v.created_at))}</small></div>
                <div style="display:flex;gap:6px"><button type="button" class="btn" data-open="${esc(v.id)}">${icon('open')}<span class="hide-phone">Open</span></button>${canWrite ? `<button type="button" class="btn" data-restore="${esc(v.id)}">Restore</button>` : ''}</div></div>`).join('')
                : '<div class="empty" style="padding:30px 0">No older version of this file. Restore points keep the state of a shared folder; turn them on under Backup in AlvaOS.</div>'}</div>
            <div class="notice" id="ver-error"></div>
            <div class="actions"><button type="button" class="btn primary" data-close>Done</button></div></div>`;
        wrap.querySelector('[data-close]').onclick = close;
        wrap.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', async () => {
            const w = window.open('', '_blank');
            try {
                const link = await api('versions/link', { method: 'POST', json: { share: inShare, path: filePath, id: b.dataset.open, inline: true } });
                if (w) w.location.href = link.url; else location.href = link.url;
            } catch (err) {
                if (w) w.close();
                $('ver-error').textContent = err.message;
            }
        }));
        wrap.querySelectorAll('[data-restore]').forEach((b) => b.addEventListener('click', async () => {
            b.disabled = true;
            try {
                const done = await api('versions/restore', { method: 'POST', json: { share: inShare, path: filePath, id: b.dataset.restore } });
                close();
                toast(`Restored as "${done.name}".`);
                if (!found) load();
            } catch (err) {
                b.disabled = false;
                $('ver-error').textContent = err.message;
            }
        }));
    }

    // WebDAV (files_dav.py, port 8091): the same folders in Finder, Windows
    // Explorer or a file app on a phone.
    $('connect-nav').addEventListener('click', () => {
        closeSide();
        const secure = location.protocol === 'https:';
        const url = secure ? `https://${location.hostname}:9444/` : `http://${location.hostname}:8091/`;
        const wrap = $('dialog');
        wrap.innerHTML = `<div class="dialog wide"><h2>Open your folders on a computer</h2>
            <p>Your shared folders also open in the file manager of a computer or phone, with the same name and password as here (WebDAV).${me?.role === 'admin' ? ' The admin account cannot be used there; sign in as one of the people from Storage › Users.' : ''}</p>
            <div class="linkbox"><input readonly value="${esc(url)}" id="dav-url"><button type="button" class="btn primary" id="dav-copy">${icon('copy')}Copy</button></div>
            <div class="howto">
                <p><strong>Mac:</strong> Finder › Go › Connect to Server, paste the address.</p>
                <p><strong>Linux:</strong> Files › Other Locations, enter <code>${secure ? `davs://${esc(location.hostname)}:9444/` : `dav://${esc(location.hostname)}:8091/`}</code>.</p>
                <p><strong>Windows:</strong> This PC › Map network drive › "Connect to a Web site…", paste the address. ${secure ? 'This works once the NAS certificate is trusted (Settings › Security in AlvaOS).' : `Windows only signs in to WebDAV over HTTPS: open Files with https:// first. The shared folders (\\\\${esc(location.hostname)}) also work.`}</p>
                <p><strong>Phone:</strong> a file app with WebDAV, like Documents (iPhone) or Solid Explorer (Android).</p>
            </div>
            <div class="actions"><button type="button" class="btn primary" data-close>Done</button></div></div>`;
        wrap.hidden = false;
        $('dav-copy').onclick = () => copy(url);
        wrap.querySelector('[data-close]').onclick = () => { wrap.hidden = true; wrap.innerHTML = ''; };
    });

    // Photos, a Hub app of its own (opened from the app bar): every picture
    // and video of the open shared folder, newest first.
    function leavePhotos() {
        if (current === 'photos') current = 'files';
        markRail();
    }
    async function showPhotos() {
        leaveView();
        current = 'photos';
        closeSide();
        $('search').value = '';
        const id = ++searchId;
        markRail();
        $('items').innerHTML = '<div class="empty">Looking for photos…</div>';
        document.querySelectorAll('.side-item').forEach((b) => b.classList.remove('active'));
        try {
            // Own photos and the photo libraries (chosen on the admin's Hub page), together.
            const { sources } = await api('photos/sources');
            const parts = await Promise.all(sources.map((src) => api(`media?${new URLSearchParams({ share: src.share, path: src.path })}`)
                .catch(() => ({ results: [], complete: true }))));   // e.g. a Photos folder not made yet
            if (id !== searchId) return;
            // The date the photo was taken (from the photo itself) where known, else the file's.
            found = parts.flatMap((p) => p.results || [])
                .sort((a, b) => shotAt(b).localeCompare(shotAt(a)));
            foundInfo = { q: '', complete: parts.every((p) => p.complete), photos: true, sources };
            selected = new Set();
            render();
        } catch (err) {
            toast(err.message, 'error');
        }
    }

    $('links-nav').addEventListener('click', async () => {
        closeSide();
        const wrap = $('dialog');
        wrap.hidden = false;
        const paint = async () => {
            let links = [];
            let error = '';
            try { links = (await api('links')).links || []; } catch (err) { error = err.message; }
            wrap.innerHTML = `<div class="dialog wide"><h2>Shared links</h2><p>Links you made. Anyone with a link can open what it points to until it expires or you remove it.</p>
                <div class="trash-list">${error ? esc(error) : links.length ? links.map((l) => `<div class="trash-row"><div><strong>${icon(l.kind === 'folder' ? 'folder' : 'file')} ${esc(l.name)}</strong>
                <small>${l.mode === 'upload' ? `Upload only${l.max_bytes ? ` (${bytes(l.received || 0)} of ${bytes(l.max_bytes)})` : l.received ? ` (${bytes(l.received)} received)` : ''} · ` : ''}${esc([l.share, ...l.path.split('/').slice(0, -1)].join(' › '))} · ${l.expires_at ? `until ${esc(when(l.expires_at))}` : 'no end date'}${l.has_password ? ' · password' : ''}${me?.role === 'admin' && l.owner !== me.user ? ` · by ${esc(l.owner)}` : ''}</small></div>
                <div style="display:flex;gap:6px"><button type="button" class="btn" data-copy="${esc(l.url)}">${icon('copy')}<span class="hide-phone">Copy</span></button><button type="button" class="btn danger" data-del="${esc(l.id)}">Remove</button></div></div>`).join('') : '<div class="empty" style="padding:30px 0">No shared links yet. Right-click a file or folder and choose "Share link".</div>'}</div>
                <div class="actions"><button type="button" class="btn primary" data-close>Done</button></div></div>`;
            wrap.querySelector('[data-close]').onclick = () => { wrap.hidden = true; wrap.innerHTML = ''; };
            wrap.querySelectorAll('[data-copy]').forEach((b) => { b.onclick = () => copy(fullUrl(b.dataset.copy)); });
            wrap.querySelectorAll('[data-del]').forEach((b) => { b.onclick = async () => {
                b.disabled = true;
                try { await api(`links/${encodeURIComponent(b.dataset.del)}/delete`, { method: 'POST' }); toast('Link removed. It does not work any more.'); } catch (err) { toast(err.message, 'error'); }
                paint();
            }; });
        };
        wrap.innerHTML = '<div class="dialog"><p>Loading…</p></div>';
        paint();
    });

    // ── Moving ─────────────────────────────────────────────────────────────
    async function moveTo(list, to, copying) {
        if (to === path && !copying) return;
        try {
            if (copying) toast(list.length === 1 ? `Copying "${list[0].name}"…` : `Copying ${list.length} items…`);
            const res = await api(copying ? 'copy' : 'move', { method: 'POST', json: { share, path, names: list.map((e) => e.name), to } });
            const where = [share, ...(to ? to.split('/') : [])].join(' › ');
            const verb = copying ? 'Copied' : 'Moved';
            toast(res.failed?.length ? res.failed[0] : `${verb} ${res.moved.length === 1 ? `"${res.moved[0]}"` : `${res.moved.length} items`} to ${where}.`, res.failed?.length ? 'error' : '');
            selected = new Set();
            load();
        } catch (err) { toast(err.message, 'error'); }
    }

    function moveDialog(list, copying) {
        const wrap = $('dialog');
        const moving = new Set(list.map((e) => rel(e)));
        let at = path;
        const paint = async () => {
            let folders = [];
            let error = '';
            try {
                folders = ((await api(`list?share=${encodeURIComponent(share)}&path=${encodeURIComponent(at)}`)).entries || [])
                    .filter((e) => e.type === 'folder' && !e.name.startsWith('.'));
            } catch (err) { error = err.message; }
            const parts = at ? at.split('/') : [];
            const crumbs = [`<button type="button" class="crumb" data-to="">${esc(share)}</button>`,
                ...parts.map((p, i) => `<span class="sep">›</span><button type="button" class="crumb" data-to="${esc(parts.slice(0, i + 1).join('/'))}">${esc(p)}</button>`)].join('');
            const what = list.length === 1 ? `"${list[0].name}"` : `${list.length} items`;
            wrap.innerHTML = `<div class="dialog wide"><h2>${copying ? 'Copy' : 'Move'} ${esc(what)}</h2>
                <div class="crumbs" style="margin-bottom:8px">${crumbs}</div>
                <div class="trash-list">${error ? esc(error) : folders.length ? folders.map((f) => {
                    const target = at ? `${at}/${f.name}` : f.name;
                    const blocked = moving.has(target);
                    return `<button type="button" class="side-item" data-into="${esc(target)}"${blocked ? ' disabled style="opacity:.45"' : ''}><span class="ic">${icon('folder-fill')}</span><span>${esc(f.name)}</span></button>`;
                }).join('') : '<div class="empty" style="padding:24px 0">No folders here.</div>'}</div>
                <div class="actions"><button type="button" class="btn" data-cancel>Cancel</button>
                <button type="button" class="btn primary" data-here${at === path && !copying ? ' disabled' : ''}>${copying ? 'Copy here' : 'Move here'}</button></div></div>`;
            wrap.querySelectorAll('[data-to]').forEach((b) => { b.onclick = () => { at = b.dataset.to; paint(); }; });
            wrap.querySelectorAll('[data-into]').forEach((b) => { b.onclick = () => { at = b.dataset.into; paint(); }; });
            wrap.querySelector('[data-cancel]').onclick = () => { wrap.hidden = true; wrap.innerHTML = ''; };
            wrap.querySelector('[data-here]').onclick = () => { wrap.hidden = true; wrap.innerHTML = ''; moveTo(list, at, copying); };
        };
        wrap.hidden = false;
        wrap.innerHTML = '<div class="dialog"><p>Loading…</p></div>';
        paint();
    }

    // Drag items onto a folder (or a folder in the path bar) to move them there.
    const DRAG = 'application/x-alvaos-files';
    let dragging = [];
    $('items').addEventListener('dragstart', (e) => {
        const item = e.target.closest('[data-i]');
        if (!item || access() !== 'write') return;
        const entry = shown[Number(item.dataset.i)];
        if (!selected.has(entry.name)) { selected = new Set([entry.name]); paintSelection(); }
        dragging = selectedEntries();
        e.dataTransfer.setData(DRAG, dragging.map((x) => x.name).join('\n'));
        e.dataTransfer.effectAllowed = 'move';
    });
    $('items').addEventListener('dragend', () => { dragging = []; document.querySelectorAll('.drop-target').forEach((x) => x.classList.remove('drop-target')); });
    function dropTarget(e) {
        if (!Array.from(e.dataTransfer?.types || []).includes(DRAG)) return null;
        const folder = e.target.closest('[data-i]');
        if (folder) {
            const entry = shown[Number(folder.dataset.i)];
            if (entry.type !== 'folder' || dragging.some((d) => d.name === entry.name)) return null;
            return { el: folder, to: rel(entry) };
        }
        const crumb = e.target.closest('.crumb[data-path]');
        if (crumb && crumb.dataset.path !== path) return { el: crumb, to: crumb.dataset.path };
        return null;
    }
    ['items', 'crumbs'].forEach((id) => {
        $(id).addEventListener('dragover', (e) => {
            const t = dropTarget(e);
            document.querySelectorAll('.drop-target').forEach((x) => { if (!t || x !== t.el) x.classList.remove('drop-target'); });
            if (!t) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            t.el.classList.add('drop-target');
        });
        $(id).addEventListener('drop', (e) => {
            const t = dropTarget(e);
            if (!t) return;
            e.preventDefault();
            e.stopPropagation();
            t.el.classList.remove('drop-target');
            const list = dragging.length ? dragging : selectedEntries();
            dragging = [];
            moveTo(list, t.to);
        });
    });

    // ── Trash ──────────────────────────────────────────────────────────────
    $('trash-nav').addEventListener('click', async () => {
        closeSide();
        const forShare = share;
        const wrap = $('dialog');
        wrap.hidden = false;
        const paint = async () => {
            let items = [];
            let error = '';
            try { items = (await api(`trash?share=${encodeURIComponent(forShare)}`)).items || []; } catch (err) { error = err.message; }
            wrap.innerHTML = `<div class="dialog wide"><h2>Trash of ${esc(forShare)}</h2><p>Deleted items stay here for 30 days, also what was deleted from a computer over the network.</p>
                <div class="trash-list">${error ? esc(error) : items.length ? items.map((it) => `<div class="trash-row"><div><strong>${esc(it.name)}</strong>
                <small>From ${esc([forShare, ...(it.folder ? it.folder.split('/') : [])].join(' › '))} · ${esc(when(it.deleted_at))}${it.type === 'file' ? ` · ${bytes(it.size_bytes)}` : ''}${it.from_network ? ' · deleted from a computer' : ''}</small></div>
                ${access() === 'write' ? `<button type="button" class="btn" data-restore="${esc(it.id)}">${icon('undo')}Put back</button>` : ''}</div>`).join('') : '<div class="empty" style="padding:30px 0">The trash is empty.</div>'}</div>
                <div class="actions">${me?.role === 'admin' && items.length ? '<button type="button" class="btn danger" data-empty>Empty trash</button>' : ''}<button type="button" class="btn primary" data-close>Done</button></div></div>`;
            wrap.querySelector('[data-close]').onclick = () => { wrap.hidden = true; wrap.innerHTML = ''; if (share === forShare) load(); };
            wrap.querySelectorAll('[data-restore]').forEach((b) => { b.onclick = async () => {
                b.disabled = true;
                try { const r = await api('trash/restore', { method: 'POST', json: { share: forShare, id: b.dataset.restore } }); toast(`"${r.name}" is back.`); } catch (err) { toast(err.message, 'error'); }
                paint();
            }; });
            const empty = wrap.querySelector('[data-empty]');
            if (empty) empty.onclick = async () => {
                if (!window.confirm(`Empty the trash of ${forShare}? This cannot be undone.`)) return;
                try { await api('trash/empty', { method: 'POST', json: { share: forShare } }); } catch (err) { toast(err.message, 'error'); }
                paint();
            };
        };
        wrap.innerHTML = '<div class="dialog"><p>Loading…</p></div>';
        paint();
    });

    // ── Upload ─────────────────────────────────────────────────────────────
    const uploads = [];
    function paintUploads() {
        const live = uploads.filter((u) => !u.hideAt || u.hideAt > Date.now());
        $('uploads').hidden = !live.length;
        $('uploads').innerHTML = live.map((u) => `<div class="up-row ${u.state}"><div class="up-name"><span>${esc(u.name)}</span><span>${esc(u.label)}</span></div><div class="up-bar"><span style="width:${u.pct}%"></span></div></div>`).join('');
    }
    // Big files go in pieces. When the connection drops, the NAS says how much
    // it has, and the upload continues from there.
    const PIECE = 16 * 1024 * 1024;
    const q = (t, name, extra) => new URLSearchParams({ share: t.share, path: t.path, name, ...(extra || {}) });

    function sendPiece(t, file, offset, onProgress) {
        return new Promise((resolve, reject) => {
            const blob = file.slice(offset, Math.min(file.size, offset + PIECE));
            const xhr = new XMLHttpRequest();
            xhr.open('POST', `/api/upload/piece?${q(t, file.name, { offset: String(offset) })}`);
            xhr.setRequestHeader('X-AlvaOS-Files', '1');
            xhr.setRequestHeader('Content-Type', 'application/octet-stream');
            xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(offset + e.loaded); };
            xhr.onload = () => {
                let data = {};
                try { data = JSON.parse(xhr.responseText || '{}'); } catch (_e) { /* not JSON */ }
                if (xhr.status >= 200 && xhr.status < 300) resolve(Number(data.size));
                else { const err = new Error(data.error || 'Did not upload'); err.retry = xhr.status >= 500 || xhr.status === 0; reject(err); }
            };
            xhr.onerror = () => { const err = new Error('Connection lost'); err.retry = true; reject(err); };
            xhr.send(blob);
        });
    }

    async function uploadOne(file, t) {
        const u = { name: file.name, pct: 0, state: '', label: 'Waiting…' };
        uploads.push(u);
        paintUploads();
        const finish = (state, label) => {
            Object.assign(u, { state, label, pct: 100, hideAt: Date.now() + (state === 'done' ? 4000 : 15000) });
            paintUploads();
            setTimeout(paintUploads, state === 'done' ? 4100 : 15100);
            return state === 'done';
        };
        const progress = (sent) => {
            u.pct = file.size ? Math.min(100, Math.round((sent / file.size) * 100)) : 100;
            u.label = `${u.pct}%`;
            paintUploads();
        };
        try {
            // A leftover from an earlier, unrelated attempt must not be continued.
            await api('upload/abort', { method: 'POST', json: { share: t.share, path: t.path, name: file.name } });
            let offset = 0;
            let tries = 0;
            let first = true;   // an empty file is one empty piece
            while (first || offset < file.size) {
                try {
                    offset = await sendPiece(t, file, offset, progress);
                    first = false;
                    tries = 0;
                } catch (err) {
                    if (!err.retry || tries >= 6) throw err;
                    tries += 1;
                    u.label = `Connection lost, trying again (${tries})…`;
                    paintUploads();
                    await new Promise((r) => setTimeout(r, Math.min(30000, 1500 * 2 ** tries)));
                    offset = Number((await api(`upload/status?${q(t, file.name)}`)).size) || 0;
                    first = false;
                }
            }
            await api('upload/finish', { method: 'POST', json: { share: t.share, path: t.path, name: file.name, size: file.size, modified: file.lastModified } });
            return finish('done', 'Done');
        } catch (err) {
            api('upload/abort', { method: 'POST', json: { share: t.share, path: t.path, name: file.name } }).catch(() => {});
            return finish('failed', err.message || 'Did not upload');
        }
    }

    async function uploadFiles(files) {
        if (!files.length) return;
        if (access() !== 'write') { toast(`You can only look at "${share}".`, 'error'); return; }
        const t = { share, path };
        let ok = 0;
        for (const f of files) if (await uploadOne(f, t)) ok += 1;
        if (ok && t.share === share && t.path === path) load();
    }
    $('file-input').addEventListener('change', (e) => { uploadFiles(Array.from(e.target.files || [])); e.target.value = ''; });
    let depth = 0;
    const content = $('content');
    const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files');
    content.addEventListener('dragenter', (e) => {
        if (!hasFiles(e) || access() !== 'write') return;
        e.preventDefault();
        depth += 1;
        $('drop-text').textContent = `Drop to upload to ${[share, ...(path ? path.split('/') : [])].join(' › ')}`;
        $('drop').hidden = false;
    });
    content.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
    content.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) $('drop').hidden = true; });
    content.addEventListener('drop', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        depth = 0;
        $('drop').hidden = true;
        uploadFiles(Array.from(e.dataTransfer.files || []));
    });

    // ── Viewer ─────────────────────────────────────────────────────────────
    let viewing = -1;
    const viewable = (e) => e.type === 'file' && ['image', 'video', 'audio'].some((k) => VIEW[k].test(e.name));
    async function showViewer(entry, kind) {
        viewing = shown.indexOf(entry);
        $('viewer-title').textContent = entry.name;
        $('viewer-body').innerHTML = '<div>Loading…</div>';
        $('viewer').hidden = false;
        $('viewer-prev').hidden = neighbour(-1) < 0;
        $('viewer-next').hidden = neighbour(1) < 0;
        $('viewer-download').onclick = () => download([entry]);
        try {
            const url = await link(entry, true);
            if (shown[viewing] !== entry) return;
            if (kind === 'image') $('viewer-body').innerHTML = `<img src="${esc(url)}" alt="${esc(entry.name)}">`;
            else if (kind === 'video') $('viewer-body').innerHTML = `<video src="${esc(url)}" controls autoplay playsinline></video>`;
            else if (kind === 'audio') $('viewer-body').innerHTML = `<audio src="${esc(url)}" controls autoplay></audio>`;
            else { const pre = document.createElement('pre'); pre.textContent = await (await fetch(url)).text(); $('viewer-body').replaceChildren(pre); }
        } catch (err) {
            $('viewer-body').textContent = err.message;
        }
    }
    function neighbour(step) {
        for (let i = viewing + step; i >= 0 && i < shown.length; i += step) if (viewable(shown[i])) return i;
        return -1;
    }
    function step(d) { const i = neighbour(d); if (i >= 0) showViewer(shown[i], Object.keys(VIEW).find((k) => VIEW[k].test(shown[i].name))); }
    function closeViewer() { $('viewer').hidden = true; $('viewer-body').innerHTML = ''; viewing = -1; }
    $('viewer-close').addEventListener('click', closeViewer);
    $('viewer-prev').addEventListener('click', () => step(-1));
    $('viewer-next').addEventListener('click', () => step(1));

    // ── Keyboard ───────────────────────────────────────────────────────────
    document.addEventListener('keydown', (e) => {
        if (!$('viewer').hidden) {
            if (e.key === 'Escape') closeViewer();
            if (e.key === 'ArrowLeft') step(-1);
            if (e.key === 'ArrowRight') step(1);
            return;
        }
        if (!$('dialog').hidden || $('app').hidden || VIEWS[current]) return;
        if (e.target.closest('input, textarea')) { if (e.key === 'Escape') e.target.blur(); return; }
        const sel = selectedEntries();
        if (e.key === 'Escape' && found && !selected.size) clearSearch();
        else if (e.key === 'Escape') { hideMenus(); selected = new Set(); paintSelection(); }
        else if (e.key === 'Enter' && sel.length === 1) open(sel[0]);
        else if ((e.key === 'Delete' || (e.key === 'Backspace' && e.metaKey)) && sel.length && access() === 'write') remove(sel);
        else if (e.key === 'F2' && sel.length === 1 && access() === 'write') { e.preventDefault(); rename(sel[0]); }
        else if (e.key === 'Backspace' || (e.altKey && e.key === 'ArrowUp')) { e.preventDefault(); if (found) clearSearch(); else up(); }
        else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); selected = new Set(shown.map((x) => x.name)); paintSelection(); }
        else if (['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key) && shown.length) {
            e.preventDefault();
            const cols = view === 'grid' ? Math.max(1, Math.round($('items').clientWidth / ($('items').firstElementChild?.offsetWidth || 150))) : 1;
            const delta = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols }[e.key];
            const next = Math.max(0, Math.min(shown.length - 1, (anchor < 0 ? -delta : anchor) + delta));
            select(next, e.shiftKey ? { shiftKey: true } : null);
            if (!e.shiftKey) anchor = next;
            $('items').querySelector(`[data-i="${next}"]`)?.scrollIntoView({ block: 'nearest' });
        }
    });

    // ── The Hub's app bar ──────────────────────────────────────────────────
    // Which apps this person sees comes from the server (hub_apps.py); the
    // admin turns them on and off. With one app the bar stays hidden.
    let hubApps = [];
    function renderRail() {
        hubApps = (me.hub && me.hub.apps) || [];
        const store = (me.hub && me.hub.store) || [];
        const rail = $('rail');
        if (hubApps.length < 2 && !store.length) {
            rail.hidden = true;
            $('app').classList.remove('with-rail');
            return;
        }
        rail.innerHTML = `<img class="rail-logo" src="hub.svg" alt="" title="${esc((me.hub && me.hub.name) || 'AlvaOS Hub')}">`
            + hubApps.map((a) => `<button type="button" class="rail-app" data-app="${esc(a.id)}">${icon(a.icon)}<span>${esc(a.name)}</span></button>`).join('')
            + (store.length ? `<span class="rail-gap" aria-hidden="true"></span><button type="button" class="rail-app" id="rail-store" aria-haspopup="menu" aria-expanded="false">${icon('apps')}<span>Apps</span></button>
                <div class="menu store-menu" id="store-menu" hidden role="menu" aria-label="Apps">${store.map((a) => `
                    <a role="menuitem" href="${esc(storeUrl(a))}" target="_blank" rel="noopener"><span class="store-letter" aria-hidden="true">${esc(a.name.charAt(0).toUpperCase())}</span><span class="store-name">${esc(a.name)}<small>${esc(storeUrl(a).replace(/^https?:\/\//, '').replace(/\/$/, ''))}</small></span>${icon('open')}</a>`).join('')}</div>` : '');
        rail.hidden = false;
        $('app').classList.add('with-rail');
        rail.querySelectorAll('[data-app]').forEach((b) => b.addEventListener('click', () => openApp(b.dataset.app)));
        const storeBtn = $('rail-store');
        if (storeBtn) storeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const menu = $('store-menu');
            const opening = menu.hidden;
            hideMenus();
            menu.hidden = !opening;
            storeBtn.setAttribute('aria-expanded', String(opening));
            // Beside the button in the rail on the left; above the bar on a phone (CSS).
            const box = storeBtn.getBoundingClientRect();
            const side = getComputedStyle(rail).flexDirection === 'column';
            menu.style.left = side ? `${box.right + 8}px` : '';
            menu.style.top = side ? `${Math.max(8, Math.min(box.top, innerHeight - menu.offsetHeight - 8))}px` : '';
            menu.style.bottom = side ? 'auto' : '';
            if (opening) menu.querySelector('a')?.focus();
        });
        markRail();
    }
    // Apps from the App Store live on ports of their own, always plain http
    // (they bring no certificate of this NAS).
    const storeUrl = (a) => `http://${location.hostname}:${a.port}${a.path || '/'}`;
    function markRail() {
        $('rail').querySelectorAll('[data-app]').forEach((b) => {
            if (b.dataset.app === current) b.setAttribute('aria-current', 'page');
            else b.removeAttribute('aria-current');
        });
    }

    // Calendar and Chat are views of their own (calendar.js, chat.js): they
    // take the place of the Files sidebar and list and draw themselves.
    const VIEWS = { calendar: 'calendar-view', chat: 'chat-view' };
    let current = 'files';
    function leaveView() {
        Object.values(VIEWS).forEach((id) => { $(id).hidden = true; });
        $('app').classList.remove('in-view');
    }
    function openApp(id, push = true) {
        if (id === 'photos') { showPhotos(); return; }
        if (!VIEWS[id]) { go(share, path, push); return; }
        closeSide();
        Object.entries(VIEWS).forEach(([k, el]) => { $(el).hidden = k !== id; });
        $('app').classList.add('in-view');
        current = id;
        markRail();
        const hash = `#app=${id}`;
        if (push && location.hash !== hash) history.pushState(null, '', hash);
        const app = (window.HubApps || {})[id];
        if (app) app.show($(VIEWS[id]));
    }
    // What the other apps use from here.
    window.Hub = {
        api, toast, esc, icon,
        addIcons: (paths) => Object.assign(P, paths),
        me: () => me,
        signOut,
        // The person and "Sign out", at the foot of each app's sidebar.
        foot: () => `<div class="side-foot"><div class="me"><span class="avatar">${esc((me?.user || '?').slice(0, 1).toUpperCase())}</span><span>${esc(me?.user || '')}</span></div><button type="button" class="link" data-signout>Sign out</button></div>`,
    };
    document.addEventListener('click', (e) => { if (e.target.closest('[data-signout]')) signOut(); });

    // ── Start ──────────────────────────────────────────────────────────────
    async function start() {
        try {
            me = await api('me');
        } catch (_e) {
            showSignin();
            return;
        }
        $('signin').hidden = true;
        $('app').hidden = false;
        $('me-name').textContent = me.user;
        $('me-avatar').textContent = (me.user || '?').slice(0, 1).toUpperCase();
        $('nas-name').textContent = me.nas_name || '';
        document.title = me.nas_name ? `AlvaOS Hub · ${me.nas_name}` : 'AlvaOS Hub';
        renderRail();
        const q = new URLSearchParams(location.hash.slice(1));
        const has = (id) => hubApps.some((a) => a.id === id);
        // The app in the address (#app=calendar), else the first in the bar.
        const wantedApp = VIEWS[q.get('app')] && has(q.get('app')) ? q.get('app')
            : !q.get('share') && VIEWS[(hubApps[0] || {}).id] ? hubApps[0].id : '';
        if (wantedApp) {
            openApp(wantedApp, false);
            return;
        }
        leaveView();
        if (!has('files')) {
            $('crumbs').innerHTML = '';
            $('share-list').innerHTML = '';
            ['links-nav', 'trash-nav', 'connect-nav', 'new-btn'].forEach((id) => { $(id).hidden = true; });
            $('items').innerHTML = `<div class="empty">${icon('grid')}<strong>No apps for you yet</strong>Ask whoever runs this NAS to turn on an app for you in the Hub.</div>`;
            return;
        }
        if (!me.shares.length) {
            $('crumbs').innerHTML = '';
            $('items').innerHTML = `<div class="empty">${icon('folder')}<strong>No shared folders for you yet</strong>Ask whoever runs this NAS to give you access to a shared folder.</div>`;
            $('new-btn').hidden = true;
            return;
        }
        const wanted = me.shares.find((s) => s.name === q.get('share'));
        go(wanted ? wanted.name : me.shares[0].name, wanted ? q.get('path') || '' : '', false);
        history.replaceState(null, '', hashFor(share, path));
    }

    // Installable as an app where the browser allows it (HTTPS or localhost).
    if ('serviceWorker' in navigator && window.isSecureContext) {
        navigator.serviceWorker.register('/sw.js').catch(() => { /* fine without */ });
    }

    paintIcons();
    start();
})();
