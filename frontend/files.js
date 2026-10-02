// AlvaOS Files: browse the shared folders, look at photos, play videos,
// download files. Downloads use short-lived links from the backend, so the
// browser downloads big files itself instead of holding them in memory.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = window.escapeHtml || ((v) => String(v ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`));
    const KINDS = {
        image: /\.(jpe?g|png|gif|webp|avif|bmp)$/i,
        video: /\.(mp4|webm|m4v)$/i,
        audio: /\.(mp3|ogg|wav|flac|m4a)$/i,
        text: /\.(txt|md|log|csv|json|xml|ya?ml|ini|conf|cfg)$/i,
        pdf: /\.pdf$/i,
    };
    const TEXT_LIMIT = 2 * 1024 * 1024;

    let shares = [];
    let share = '';
    let path = '';
    let entries = [];
    let shown = [];
    let viewerIndex = -1;
    let loadId = 0;

    const icon = (name) => (window.alvaIcon ? window.alvaIcon(name, '', 'aria-hidden="true"') : '');
    const kindOf = (name) => Object.keys(KINDS).find((k) => KINDS[k].test(name)) || '';

    function formatBytes(bytes) {
        const n = Number(bytes) || 0;
        if (n < 1024) return `${n} B`;
        const units = ['KB', 'MB', 'GB', 'TB'];
        let v = n / 1024;
        let i = 0;
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
        return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
    }

    function formatDate(iso) {
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? '' : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    }

    async function api(url, options) {
        const res = await fetch(`${API_BASE}${url}`, options);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }

    async function linkFor(rel, inline) {
        const data = await api('/files/link', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ share, path: rel, inline: !!inline }),
        });
        return data.url;
    }

    // ── Navigation ──────────────────────────────────────────────────────────

    function setHash() {
        const params = new URLSearchParams();
        if (share) params.set('share', share);
        if (path) params.set('path', path);
        const next = `#${params.toString()}`;
        if (window.location.hash !== next) history.pushState(null, '', next);
    }

    function readHash() {
        const params = new URLSearchParams(window.location.hash.slice(1));
        return { share: params.get('share') || '', path: params.get('path') || '' };
    }

    function go(nextShare, nextPath, push = true) {
        share = nextShare;
        path = nextPath || '';
        if (push) setHash();
        renderShares();
        load();
    }

    function renderShares() {
        $('files-shares').innerHTML = shares.map((s) => `
            <button type="button" class="files-share" role="tab" aria-selected="${s.name === share}" data-share="${esc(s.name)}">
                ${icon('folder')}${esc(s.name)}
            </button>`).join('');
    }

    function renderCrumbs() {
        const parts = path ? path.split('/') : [];
        const crumbs = [`<button type="button" class="files-crumb" data-path="">${esc(share)}</button>`];
        parts.forEach((part, i) => {
            crumbs.push(`<span aria-hidden="true">&rsaquo;</span><button type="button" class="files-crumb" data-path="${esc(parts.slice(0, i + 1).join('/'))}">${esc(part)}</button>`);
        });
        $('files-crumbs').innerHTML = crumbs.join('');
    }

    // ── Listing ─────────────────────────────────────────────────────────────

    function sorted(list) {
        const how = $('files-sort').value;
        const filter = $('files-filter').value.trim().toLowerCase();
        const out = list.filter((e) => !filter || e.name.toLowerCase().includes(filter));
        const folderFirst = (a, b) => (a.type === 'folder' ? 0 : 1) - (b.type === 'folder' ? 0 : 1);
        out.sort((a, b) => folderFirst(a, b)
            || (how === 'new' ? String(b.modified_at).localeCompare(String(a.modified_at))
                : how === 'big' ? (b.size_bytes || 0) - (a.size_bytes || 0)
                    : a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })));
        return out;
    }

    function renderList() {
        shown = sorted(entries.filter((e) => !e.name.startsWith('.')));
        const list = $('files-list');
        if (!shown.length) {
            list.innerHTML = `<div class="files-empty">${entries.length ? 'Nothing here matches the filter.' : 'This folder is empty.'}</div>`;
        } else {
            list.innerHTML = shown.map((e, i) => {
                const folder = e.type === 'folder';
                const kind = kindOf(e.name);
                const ic = folder ? 'folder' : kind === 'image' ? 'image' : kind === 'video' ? 'film' : kind === 'text' || kind === 'pdf' ? 'file-text' : 'file';
                const size = folder ? '' : formatBytes(e.size_bytes);
                const date = formatDate(e.modified_at);
                return `
                    <div class="files-row${folder ? ' folder' : ''}">
                        <span class="ic">${icon(ic)}</span>
                        <div class="files-text">
                            <button type="button" class="files-name" data-index="${i}" title="${esc(e.name)}">${esc(e.name)}</button>
                            <span class="files-sub">${esc([size, date].filter(Boolean).join(' · '))}</span>
                        </div>
                        <span class="files-size">${esc(size)}</span>
                        <span class="files-date">${esc(date)}</span>
                        <div class="files-more">
                            <button type="button" class="files-dl" data-menu="${i}" aria-haspopup="menu" aria-label="More for ${esc(e.name)}" title="More">&#8943;</button>
                        </div>
                    </div>`;
            }).join('');
        }
        const folders = entries.filter((e) => e.type === 'folder' && !e.name.startsWith('.')).length;
        const files = entries.length - folders - entries.filter((e) => e.name.startsWith('.') && e.type !== 'folder').length;
        const total = entries.filter((e) => e.type !== 'folder').reduce((s, e) => s + (e.size_bytes || 0), 0);
        $('files-foot').textContent = [
            folders ? `${folders} folder${folders === 1 ? '' : 's'}` : '',
            files ? `${files} file${files === 1 ? '' : 's'} (${formatBytes(total)})` : '',
        ].filter(Boolean).join(', ');
    }

    async function load() {
        const id = ++loadId;
        $('files-card').hidden = false;
        renderCrumbs();
        $('files-list').innerHTML = '<div class="files-empty">Loading...</div>';
        $('files-foot').textContent = '';
        try {
            const data = await api(`/files/list?share=${encodeURIComponent(share)}&path=${encodeURIComponent(path)}`);
            if (id !== loadId) return;
            path = data.path || '';
            entries = Array.isArray(data.entries) ? data.entries : [];
            renderCrumbs();
            renderList();
        } catch (e) {
            if (id !== loadId) return;
            entries = [];
            $('files-list').innerHTML = `<div class="files-empty">${esc(e.message)}</div>`;
        }
    }

    // ── Opening and downloading ─────────────────────────────────────────────

    const relOf = (entry) => (path ? `${path}/${entry.name}` : entry.name);

    async function download(entry) {
        try {
            const a = document.createElement('a');
            a.href = await linkFor(relOf(entry), false);
            a.download = entry.name;
            document.body.appendChild(a);
            a.click();
            a.remove();
        } catch (e) {
            window.showToast(e.message, 'error');
        }
    }

    function viewable(entry) {
        const kind = kindOf(entry.name);
        return entry.type !== 'folder' && ['image', 'video', 'audio'].includes(kind);
    }

    async function open(entry) {
        if (entry.type === 'folder') {
            go(share, relOf(entry));
            return;
        }
        const kind = kindOf(entry.name);
        if (kind === 'pdf') {
            const tab = window.open('', '_blank', 'noopener');
            try {
                const url = await linkFor(relOf(entry), true);
                if (tab) tab.location.href = url; else window.location.href = url;
            } catch (e) {
                if (tab) tab.close();
                window.showToast(e.message, 'error');
            }
            return;
        }
        if (kind === 'text' && (entry.size_bytes || 0) <= TEXT_LIMIT) {
            showViewer(entry, 'text');
            return;
        }
        if (viewable(entry)) {
            showViewer(entry, kind);
            return;
        }
        download(entry);
    }

    async function showViewer(entry, kind) {
        const viewer = $('viewer');
        viewerIndex = shown.indexOf(entry);
        $('viewer-title').textContent = entry.name;
        $('viewer-body').innerHTML = '<div style="color:#cbd5e1">Loading...</div>';
        viewer.hidden = false;
        updateViewerNav();
        try {
            const url = await linkFor(relOf(entry), true);
            if (shown[viewerIndex] !== entry) return;
            $('viewer-download').onclick = (ev) => { ev.preventDefault(); download(entry); };
            if (kind === 'image') {
                $('viewer-body').innerHTML = `<img src="${esc(url)}" alt="${esc(entry.name)}">`;
            } else if (kind === 'video') {
                $('viewer-body').innerHTML = `<video src="${esc(url)}" controls autoplay playsinline></video>`;
            } else if (kind === 'audio') {
                $('viewer-body').innerHTML = `<audio src="${esc(url)}" controls autoplay></audio>`;
            } else {
                const text = await (await fetch(url)).text();
                const pre = document.createElement('pre');
                pre.textContent = text;
                $('viewer-body').replaceChildren(pre);
            }
        } catch (e) {
            $('viewer-body').innerHTML = `<div style="color:#fca5a5">${esc(e.message)}</div>`;
        }
    }

    function neighbour(step) {
        for (let i = viewerIndex + step; i >= 0 && i < shown.length; i += step) {
            if (viewable(shown[i])) return i;
        }
        return -1;
    }

    function updateViewerNav() {
        $('viewer-prev').hidden = neighbour(-1) < 0;
        $('viewer-next').hidden = neighbour(1) < 0;
    }

    function step(dir) {
        const i = neighbour(dir);
        if (i >= 0) showViewer(shown[i], kindOf(shown[i].name));
    }

    function closeViewer() {
        $('viewer').hidden = true;
        $('viewer-body').innerHTML = '';   // stops a playing video
        viewerIndex = -1;
    }

    // ── Changes: menu, rename, delete, new folder ───────────────────────────

    function closeMenus() {
        document.querySelectorAll('.files-menu').forEach((m) => m.remove());
    }

    function toggleMenu(button, entry) {
        const open = button.parentElement.querySelector('.files-menu');
        closeMenus();
        if (open) return;
        const i = shown.indexOf(entry);
        const menu = document.createElement('div');
        menu.className = 'files-menu';
        menu.setAttribute('role', 'menu');
        menu.innerHTML = `
            ${entry.type === 'folder' ? '' : `<button type="button" role="menuitem" data-act="download" data-index="${i}">${icon('download')} Download</button>`}
            <button type="button" role="menuitem" data-act="rename" data-index="${i}">Rename</button>
            <button type="button" role="menuitem" class="danger" data-act="delete" data-index="${i}">${icon('trash-2')} Delete</button>`;
        button.parentElement.appendChild(menu);
        menu.querySelector('button')?.focus();
    }

    async function post(url, body) {
        return api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    }

    async function runAction(action, entry) {
        if (!entry) return;
        if (action === 'download') { download(entry); return; }
        if (action === 'rename') {
            const name = await window.showPrompt(`Rename "${entry.name}"`, {
                title: 'Rename', label: 'New name', value: entry.name, confirmLabel: 'Rename',
            });
            if (!name || name === entry.name) return;
            try {
                await post('/files/rename', { share, path, old: entry.name, new: name.trim() });
                window.showToast(`Renamed to "${name.trim()}".`, 'success');
                load();
            } catch (e) {
                window.showToast(e.message, 'error');
            }
            return;
        }
        if (action === 'delete') {
            const what = entry.type === 'folder' ? `the folder "${entry.name}" and everything in it` : `"${entry.name}"`;
            if (!await window.showConfirm(`Delete ${what}?\nIt goes to the trash of "${share}" and can be put back for 30 days.`, { confirmLabel: 'Delete' })) return;
            try {
                await post('/files/delete', { share, path, names: [entry.name] });
                window.showToast(`"${entry.name}" is in the trash. "Trash" at the bottom puts it back.`, 'success');
                load();
            } catch (e) {
                window.showToast(e.message, 'error');
            }
        }
    }

    async function newFolder() {
        const name = await window.showPrompt('New folder', { title: 'New folder', label: 'Name', placeholder: 'Holidays 2026', confirmLabel: 'Create' });
        if (!name || !name.trim()) return;
        try {
            await post('/files/mkdir', { share, path, name: name.trim() });
            load();
        } catch (e) {
            window.showToast(e.message, 'error');
        }
    }

    // ── Upload ──────────────────────────────────────────────────────────────

    let uploadLimit = 4 * 1024 ** 3;
    const uploads = [];

    function renderUploads() {
        const box = $('files-uploads');
        const active = uploads.filter((u) => !u.hideAt || u.hideAt > Date.now());
        box.hidden = !active.length;
        box.innerHTML = active.map((u) => `
            <div class="up-row ${u.state}">
                <div class="up-name"><span title="${esc(u.name)}">${esc(u.name)}</span><span>${esc(u.label)}</span></div>
                <div class="up-bar"><span style="width: ${u.percent}%"></span></div>
            </div>`).join('');
    }

    function uploadOne(file, target) {
        return new Promise((resolve) => {
            const item = { name: file.name, percent: 0, state: '', label: 'Waiting...' };
            uploads.push(item);
            renderUploads();
            const finish = (state, label) => {
                item.state = state;
                item.label = label;
                item.percent = 100;
                item.hideAt = Date.now() + (state === 'done' ? 4000 : 15000);
                renderUploads();
                setTimeout(renderUploads, state === 'done' ? 4100 : 15100);
                resolve(state === 'done');
            };
            if (file.size > uploadLimit) {
                finish('failed', 'Over 4 GB: use the shared folder');
                return;
            }
            (async () => {
                const csrf = typeof fetchCsrfToken === 'function' ? await fetchCsrfToken() : null;
                const xhr = new XMLHttpRequest();
                const q = new URLSearchParams({ share: target.share, path: target.path, name: file.name });
                xhr.open('POST', `${API_BASE}/files/upload?${q}`);
                xhr.setRequestHeader('Authorization', localStorage.getItem('alvaos_token') || '');
                if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
                xhr.setRequestHeader('Content-Type', 'application/octet-stream');
                xhr.upload.onprogress = (e) => {
                    if (!e.lengthComputable) return;
                    item.percent = Math.round((e.loaded / e.total) * 100);
                    item.label = `${item.percent}%`;
                    renderUploads();
                };
                xhr.onload = () => {
                    let error = '';
                    try { error = JSON.parse(xhr.responseText || '{}').error || ''; } catch (_e) { /* not JSON */ }
                    if (xhr.status >= 200 && xhr.status < 300) finish('done', 'Done');
                    else finish('failed', error || 'Did not upload');
                };
                xhr.onerror = () => finish('failed', 'Connection lost');
                xhr.send(file);
            })();
        });
    }

    async function uploadFiles(files) {
        if (!files.length || !share) return;
        const target = { share, path };
        let ok = 0;
        for (const file of files) {
            if (await uploadOne(file, target)) ok += 1;
        }
        if (ok && target.share === share && target.path === path) load();
    }

    // ── Trash ───────────────────────────────────────────────────────────────

    async function showTrash() {
        const forShare = share;
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="trash-title">
                <div class="modal-title"><span id="trash-title">Trash of "${esc(forShare)}"</span>
                    <button type="button" class="modal-close-x" aria-label="Close">&times;</button></div>
                <div class="modal-body">
                    <p class="files-note" style="margin-top:0">Deleted items stay here for 30 days, then they are gone for good.</p>
                    <div class="trash-list" id="trash-list"><div class="files-empty">Loading...</div></div>
                </div>
                <div class="modal-actions">
                    <button type="button" class="btn-secondary" id="trash-empty" disabled>Empty trash</button>
                    <button type="button" class="btn-primary" id="trash-close">Done</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const close = () => { overlay.remove(); if (share === forShare) load(); };
        overlay.querySelector('.modal-close-x').onclick = close;
        overlay.querySelector('#trash-close').onclick = close;
        if (window.attachModalDismiss) window.attachModalDismiss(overlay, close);

        const render = async () => {
            const list = overlay.querySelector('#trash-list');
            let items = [];
            try {
                items = (await api(`/files/trash?share=${encodeURIComponent(forShare)}`)).items || [];
            } catch (e) {
                list.innerHTML = `<div class="files-empty">${esc(e.message)}</div>`;
                return;
            }
            overlay.querySelector('#trash-empty').disabled = !items.length;
            list.innerHTML = items.length ? items.map((it) => `
                <div class="trash-row">
                    <div><strong>${esc(it.name)}</strong>
                        <small>From ${esc([forShare, ...(it.folder ? it.folder.split('/') : [])].join(' › '))} · deleted ${esc(formatDate(it.deleted_at))}${it.type === 'file' ? ` · ${esc(formatBytes(it.size_bytes))}` : ''}</small></div>
                    <button type="button" class="btn-secondary" data-restore="${esc(it.id)}">Put back</button>
                </div>`).join('') : '<div class="files-empty">The trash is empty.</div>';
        };
        overlay.addEventListener('click', async (e) => {
            const btn = e.target.closest('[data-restore]');
            if (!btn) return;
            btn.disabled = true;
            try {
                const res = await post('/files/trash/restore', { share: forShare, id: btn.dataset.restore });
                window.showToast(`"${res.name}" is back in ${[forShare, ...(res.folder ? res.folder.split('/') : [])].join(' › ')}.`, 'success');
            } catch (err) {
                window.showToast(err.message, 'error');
            }
            render();
        });
        overlay.querySelector('#trash-empty').onclick = async () => {
            if (!await window.showConfirm(`Empty the trash of "${forShare}"?\nEverything in it is deleted for good.`, { confirmLabel: 'Empty trash' })) return;
            try {
                await post('/files/trash/empty', { share: forShare });
            } catch (err) {
                window.showToast(err.message, 'error');
            }
            render();
        };
        render();
    }

    // ── Wiring ──────────────────────────────────────────────────────────────

    $('files-shares').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-share]');
        if (btn) go(btn.dataset.share, '');
    });
    $('files-crumbs').addEventListener('click', (e) => {
        const btn = e.target.closest('.files-crumb');
        if (btn && btn !== $('files-crumbs').lastElementChild) go(share, btn.dataset.path);
    });
    $('files-list').addEventListener('click', (e) => {
        const more = e.target.closest('[data-menu]');
        if (more) { toggleMenu(more, shown[Number(more.dataset.menu)]); return; }
        const act = e.target.closest('[data-act]');
        if (act) { closeMenus(); runAction(act.dataset.act, shown[Number(act.dataset.index)]); return; }
        const name = e.target.closest('.files-name');
        if (name) open(shown[Number(name.dataset.index)]);
    });
    document.addEventListener('click', (e) => { if (!e.target.closest('.files-more')) closeMenus(); });
    $('files-upload-btn').addEventListener('click', () => $('files-upload-input').click());
    $('files-upload-input').addEventListener('change', (e) => {
        uploadFiles(Array.from(e.target.files || []));
        e.target.value = '';
    });
    $('files-mkdir-btn').addEventListener('click', newFolder);
    $('files-trash-btn').addEventListener('click', showTrash);
    let dragDepth = 0;
    const card = $('files-card');
    const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files');
    card.addEventListener('dragenter', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth += 1;
        $('files-drop-text').textContent = `Drop to upload to ${[share, ...(path ? path.split('/') : [])].join(' › ')}`;
        $('files-drop').hidden = false;
    });
    card.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
    card.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('files-drop').hidden = true; });
    card.addEventListener('drop', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth = 0;
        $('files-drop').hidden = true;
        uploadFiles(Array.from(e.dataTransfer.files || []));
    });
    $('files-filter').addEventListener('input', renderList);
    $('files-sort').addEventListener('change', renderList);
    $('viewer-close').addEventListener('click', closeViewer);
    $('viewer-prev').addEventListener('click', () => step(-1));
    $('viewer-next').addEventListener('click', () => step(1));
    document.addEventListener('keydown', (e) => {
        if ($('viewer').hidden) return;
        if (e.key === 'Escape') closeViewer();
        if (e.key === 'ArrowLeft') step(-1);
        if (e.key === 'ArrowRight') step(1);
    });
    window.addEventListener('popstate', () => {
        const state = readHash();
        if (state.share && shares.some((s) => s.name === state.share)) go(state.share, state.path, false);
    });

    async function start() {
        try {
            const data = await api('/files/shares');
            shares = data.shares || [];
            uploadLimit = Number(data.upload_limit_bytes) || uploadLimit;
        } catch (e) {
            $('files-shares').innerHTML = `<p class="files-note">${esc(e.message)}</p>`;
            return;
        }
        if (!shares.length) {
            $('files-shares').innerHTML = '<div class="files-card files-empty" style="width:100%">No shared folders yet. <a href="storage.html#shares">Share a folder</a> in Storage, then it shows up here.</div>';
            return;
        }
        $('files-note').hidden = false;
        const state = readHash();
        const first = shares.some((s) => s.name === state.share) ? state.share : shares[0].name;
        go(first, first === state.share ? state.path : '', false);
    }

    start();
})();
