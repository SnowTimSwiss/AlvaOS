// The "Files" page in AlvaOS: turn the Files app on, open it, or turn it off.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = window.escapeHtml || ((v) => String(v ?? ''));
    const appUrl = (port) => `${window.location.protocol}//${window.location.hostname}:${port}/`;

    async function call(options) {
        const res = await fetch(`${API_BASE}/files-app`, { ...options, headers: { 'Content-Type': 'application/json' } });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }

    function render(state) {
        const actions = $('fl-actions');
        if (state.enabled) {
            const url = appUrl(state.port);
            actions.innerHTML = `
                <a class="btn-primary" href="${esc(url)}" target="_blank" rel="noopener">Open Files</a>
                <span class="fl-addr">${esc(url.replace(/^https?:\/\//, '').replace(/\/$/, ''))}</span>
                <button type="button" class="btn-secondary btn-quiet" id="fl-turn-off">Turn off</button>`;
            $('fl-turn-off').onclick = () => toggle(false);
            if (!state.running) {
                actions.insertAdjacentHTML('beforeend', '<span class="fl-addr">Starting…</span>');
            }
        } else {
            actions.innerHTML = '<button type="button" class="btn-primary" id="fl-turn-on">Turn on</button><span class="fl-addr">Opens port ' + esc(state.port) + ' on this NAS.</span>';
            $('fl-turn-on').onclick = () => toggle(true);
        }
        const waiting = state.people_without_password || [];
        $('fl-note').hidden = !(state.enabled && waiting.length);
        $('fl-note').innerHTML = waiting.length
            ? `${esc(waiting.join(', '))} ${waiting.length === 1 ? 'needs' : 'need'} their password set once more before signing in to Files (passwords from before Files are not stored in a form it can check): <a href="storage.html#users">Storage › Users</a>.`
            : '';
    }

    async function toggle(on) {
        const btn = $(on ? 'fl-turn-on' : 'fl-turn-off');
        if (btn) btn.disabled = true;
        try {
            render(await call({ method: 'POST', body: JSON.stringify({ enabled: on }) }));
            window.showToast(on ? 'AlvaOS Files is on.' : 'AlvaOS Files is off.', 'success');
        } catch (e) {
            window.showToast(e.message, 'error');
            if (btn) btn.disabled = false;
        }
    }

    call({}).then(render).catch((e) => { $('fl-actions').innerHTML = `<span class="fl-addr">${esc(e.message)}</span>`; });
})();
