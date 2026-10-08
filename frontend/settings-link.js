// Settings › Away from home: AlvaOS Link on or off, and who is connected (backend/api_link.py).
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let timer = 0;

    async function api(options = {}) {
        const res = await fetch(`${API_BASE}/link`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }
    const note = (msg, kind) => (window.showNotification ? window.showNotification(msg, kind) : null);
    const ago = (ts) => {
        if (!ts) return 'not yet';
        const s = Math.max(0, Date.now() / 1000 - ts);
        if (s < 90) return 'just now';
        if (s < 5400) return `${Math.round(s / 60)} min ago`;
        if (s < 129600) return `${Math.round(s / 3600)} h ago`;
        return `${Math.round(s / 86400)} days ago`;
    };

    function render(state) {
        $('link-enabled').checked = !!state.enabled;
        $('link-enabled').disabled = !state.available;
        const summary = !state.available ? 'AlvaOS Link is not running on this NAS. Restart the NAS or reinstall the update.'
            : !state.enabled ? 'Off. Phones and buddies can only reach this NAS at home.'
                : !state.running ? 'Starting…'
                    : state.online ? 'On. This NAS is reachable from outside.' : 'On, looking for a way out to the internet…';
        $('link-summary').textContent = summary;
        const body = $('link-body');
        if (!state.running) { body.innerHTML = ''; return; }
        const phones = state.peers.filter((p) => p.kind === 'phone');
        const buddies = state.peers.filter((p) => p.kind === 'buddy');
        const row = (p) => `<div class="set-row"><div class="set-text"><div class="set-label">${esc(p.name || 'Unnamed')}${p.user ? ` <span class="set-desc">(${esc(p.user)})</span>` : ''}</div>
            <div class="set-desc">${p.connected ? 'Connected now' : `Last seen ${esc(ago(p.last_seen))}`}</div></div><div class="set-value"></div><div class="set-action"></div></div>`;
        body.innerHTML = `
            <div class="set-row"><div class="set-text"><div class="set-label">This NAS's Link address</div>
                <div class="set-desc">The app and your buddies find this NAS by it. It is in the QR code of the Hub (Phones and devices) and in the Buddy pairing code, so you rarely need it.</div></div>
                <div class="set-value mono-text" title="${esc(state.node_id)}">${esc(state.node_id.slice(0, 8))}…${esc(state.node_id.slice(-4))}</div><div class="set-action"></div></div>
            <h3 class="set-group-subtitle">Phones</h3>
            ${phones.length ? phones.map(row).join('') : '<p class="set-desc">No phone is paired yet. Show the QR code in the Hub (Phones and devices) while the phone is at home or away.</p>'}
            <h3 class="set-group-subtitle">Buddies</h3>
            ${buddies.length ? buddies.map(row).join('') : '<p class="set-desc">No buddy NAS is paired. See Backup › Buddy.</p>'}`;
    }

    async function load() {
        try { render(await api()); } catch (err) { $('link-summary').textContent = err.message; }
    }

    $('link-enabled').addEventListener('change', async (e) => {
        const want = e.target.checked;
        e.target.disabled = true;
        try {
            render(await api({ method: 'POST', body: JSON.stringify({ enabled: want }) }));
            note(want ? 'Away from home is on.' : 'Away from home is off. Phones and buddies reach this NAS at home only.', 'success');
        } catch (err) {
            e.target.checked = !want;
            note(err.message, 'error');
        } finally { e.target.disabled = false; }
    });

    function start() {
        load();
        clearInterval(timer);
        timer = setInterval(() => { if ($('tab-link').classList.contains('active') && !document.hidden) load(); }, 5000);
    }
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelector('[data-tab="link"]')?.addEventListener('click', start);
        if (window.location.hash === '#link') start();
    });
})();
