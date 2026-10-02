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
                        ${folder ? '<span></span>' : `<button type="button" class="files-dl" data-download="${i}" aria-label="Download ${esc(e.name)}" title="Download">${icon('download')}</button>`}
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
        const dl = e.target.closest('[data-download]');
        if (dl) { download(shown[Number(dl.dataset.download)]); return; }
        const name = e.target.closest('.files-name');
        if (name) open(shown[Number(name.dataset.index)]);
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
            shares = (await api('/files/shares')).shares || [];
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
