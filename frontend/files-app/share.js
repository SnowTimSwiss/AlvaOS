// A share link from AlvaOS Files: look at and download what someone shared.
(function () {
    'use strict';
    const P = {
        folder: '<path fill="currentColor" stroke="none" d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
        file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
        image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
        video: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="m10 8 6 4-6 4Z"/>',
        download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
        x: '<path d="M18 6 6 18M6 6l12 12"/>',
    };
    const icon = (n) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n] || P.file}</svg>`;
    document.querySelectorAll('[data-icon]').forEach((el) => { el.innerHTML = icon(el.dataset.icon); });
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const token = location.pathname.split('/').filter(Boolean).pop() || '';
    const base = `/api/public/${encodeURIComponent(token)}`;
    const IMG = /\.(jpe?g|png|gif|webp|avif|bmp)$/i;
    const VID = /\.(mp4|webm|m4v)$/i;
    const AUD = /\.(mp3|ogg|wav|flac|m4a)$/i;
    const THUMB = /\.(jpe?g|png|gif|webp|bmp)$/i;
    let info = null;
    let path = '';

    async function get(url, options) {
        const res = await fetch(url, { credentials: 'same-origin', ...(options || {}) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { const e = new Error(data.error || 'This link does not work.'); e.data = data; throw e; }
        return data;
    }

    function fail(message) {
        $('pub-error').hidden = false;
        $('pub-error').innerHTML = `${icon('file')}<strong>Not available</strong>${esc(message)}`;
        $('pub-lock').hidden = true;
    }

    const fileUrl = (p, inline) => `${base}/file?${new URLSearchParams({ ...(p ? { path: p } : {}), ...(inline ? { inline: '1' } : {}) })}`;

    function showFile() {
        $('pub-file').hidden = false;
        $('pub-file-name').textContent = info.name;
        $('pub-download').href = fileUrl('', false);
        const prev = $('pub-preview');
        if (IMG.test(info.name)) prev.innerHTML = `<img src="${esc(fileUrl('', true))}" alt="${esc(info.name)}">`;
        else if (VID.test(info.name)) prev.innerHTML = `<video src="${esc(fileUrl('', true))}" controls playsinline></video>`;
        else if (AUD.test(info.name)) prev.innerHTML = `<audio src="${esc(fileUrl('', true))}" controls></audio>`;
        else prev.innerHTML = `<div class="pub-big-icon">${icon('file')}</div>`;
    }

    async function showFolder() {
        $('pub-folder').hidden = false;
        path = new URLSearchParams(location.hash.slice(1)).get('path') || '';
        const parts = path ? path.split('/') : [];
        $('pub-crumbs').innerHTML = [`<button type="button" class="crumb" data-p="">${esc(info.name)}</button>`,
            ...parts.map((p, i) => `<span class="sep">›</span><button type="button" class="crumb" data-p="${esc(parts.slice(0, i + 1).join('/'))}">${esc(p)}</button>`)].join('');
        $('pub-zip').href = `${base}/zip?${new URLSearchParams(path ? { path } : {})}`;
        $('pub-items').innerHTML = '<div class="empty">Loading…</div>';
        let entries = [];
        try {
            entries = (await get(`${base}/list?path=${encodeURIComponent(path)}`)).entries || [];
        } catch (e) {
            $('pub-items').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
            return;
        }
        entries = entries.filter((e) => e.type === 'folder' || e.type === 'file');
        entries.sort((a, b) => (a.type === 'folder' ? 0 : 1) - (b.type === 'folder' ? 0 : 1) || a.name.localeCompare(b.name, undefined, { numeric: true }));
        if (!entries.length) { $('pub-items').innerHTML = '<div class="empty">This folder is empty.</div>'; return; }
        $('pub-items').innerHTML = entries.map((e) => {
            const p = path ? `${path}/${e.name}` : e.name;
            const thumb = e.type === 'file' && THUMB.test(e.name)
                ? `<div class="thumb"><img loading="lazy" alt="" src="${esc(`${base}/thumb?${new URLSearchParams({ path: p, v: e.modified_at || '' })}`)}"></div>`
                : `<div class="thumb icon ${e.type === 'folder' ? 'kind-folder' : 'kind-file'}">${icon(e.type === 'folder' ? 'folder' : VID.test(e.name) ? 'video' : 'file')}</div>`;
            return `<div class="tile" data-p="${esc(p)}" data-type="${e.type}" data-name="${esc(e.name)}" title="${esc(e.name)}">${thumb}<div class="name">${esc(e.name)}</div>
                ${e.type === 'file' ? `<a class="more" href="${esc(fileUrl(p, false))}" download aria-label="Download ${esc(e.name)}">${icon('download')}</a>` : ''}</div>`;
        }).join('');
    }

    $('pub-crumbs').addEventListener('click', (e) => {
        const b = e.target.closest('[data-p]');
        if (b) location.hash = b.dataset.p ? `path=${encodeURIComponent(b.dataset.p)}` : '';
    });
    $('pub-items').addEventListener('click', (e) => {
        if (e.target.closest('a')) return;
        const t = e.target.closest('.tile');
        if (!t) return;
        if (t.dataset.type === 'folder') { location.hash = `path=${encodeURIComponent(t.dataset.p)}`; return; }
        const name = t.dataset.name;
        if (IMG.test(name) || VID.test(name) || AUD.test(name)) {
            $('viewer-title').textContent = name;
            $('viewer-download').href = fileUrl(t.dataset.p, false);
            const src = esc(fileUrl(t.dataset.p, true));
            $('viewer-body').innerHTML = IMG.test(name) ? `<img src="${src}" alt="">` : VID.test(name) ? `<video src="${src}" controls autoplay playsinline></video>` : `<audio src="${src}" controls autoplay></audio>`;
            $('viewer').hidden = false;
        } else {
            location.href = fileUrl(t.dataset.p, false);
        }
    });
    const closeViewer = () => { $('viewer').hidden = true; $('viewer-body').innerHTML = ''; };
    $('viewer-close').addEventListener('click', closeViewer);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeViewer(); });
    window.addEventListener('hashchange', () => { if (info && info.kind === 'folder') showFolder(); });

    $('pub-lock').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            await get(`${base}/unlock`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: $('pub-pass').value }) });
            $('pub-lock').hidden = true;
            start();
        } catch (err) {
            $('pub-lock-error').textContent = err.message;
            $('pub-pass').select();
        }
    });

    async function start() {
        try {
            info = await get(base);
        } catch (e) {
            if (e.data?.needs_password) { $('pub-lock').hidden = false; $('pub-pass').focus(); return; }
            fail(e.message);
            return;
        }
        $('pub-name').textContent = info.name;
        $('pub-by').textContent = `Shared by ${info.owner}${info.nas_name ? ` from ${info.nas_name}` : ''}${info.expires_at ? ` · until ${new Date(info.expires_at).toLocaleDateString()}` : ''}`;
        document.title = `${info.name} · shared with you`;
        if (info.kind === 'folder') showFolder(); else showFile();
    }
    start();
})();
