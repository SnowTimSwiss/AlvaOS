// Settings › Remote access: Tailscale for your own devices, Cloudflare Tunnel
// for the Hub at your own domain (backend/remote_access.py).
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let state = null;
    let poll = 0;

    async function api(path, options = {}) {
        const res = await fetch(`${API_BASE}/remote-access${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }
    const note = (msg, kind) => (window.showNotification ? window.showNotification(msg, kind) : null);
    const when = (iso) => {
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? '' : d.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    };

    // ── Tailscale ──────────────────────────────────────────────────────────
    function renderTailscale(ts) {
        $('ts-enabled').checked = ts.enabled;
        const summary = {
            off: 'Off.',
            starting: 'Starting… (the first time it is downloaded, that takes a minute)',
            login: 'Waiting for you to sign in.',
            approve: 'Signed in; approve this NAS in the Tailscale admin console.',
            stopped: 'Stopped.',
            on: `On${ts.tailnet ? ` in ${esc(ts.tailnet)}` : ''}.`,
        }[ts.state] || 'Checking...';
        $('ts-summary').innerHTML = summary;
        const body = $('ts-body');
        if (!ts.enabled) { body.innerHTML = ''; return; }
        if (ts.state === 'login') {
            body.innerHTML = `<div class="remote-step">
                <p><strong>1.</strong> Sign this NAS in to your Tailscale account (or make one; it is free for home use):</p>
                ${ts.login_url ? `<p><a class="btn-primary" href="${esc(ts.login_url)}" target="_blank" rel="noopener">Sign in to Tailscale</a></p>` : '<p class="set-desc">The sign-in link appears in a moment…</p>'}
                <p><strong>2.</strong> Install the Tailscale app on your phone and laptop and sign in there with the same account.</p></div>`;
            return;
        }
        if (ts.state !== 'on') { body.innerHTML = ''; return; }
        const address = ts.addresses[0] || '';
        body.innerHTML = `
            <div class="set-row"><div class="set-text"><div class="set-label">Open AlvaOS from your devices</div>
                <div class="set-desc">With the Tailscale app on, type this into the browser:</div></div>
                <div class="set-value mono-text">${address ? `http://${esc(address)}:8080` : '-'}</div></div>
            ${ts.name ? `<div class="set-row"><div class="set-text"><div class="set-label">Its name in Tailscale</div><div class="set-desc">The Hub: http://${esc(ts.name)}:8090 · shared folders: \\\\${esc(ts.name)} (Windows) or smb://${esc(ts.name)} (Mac)</div></div>
                <div class="set-value mono-text">${esc(ts.name)}</div></div>` : ''}
            <div class="remote-devices"><div class="set-label">Devices in your Tailscale network</div>
                ${ts.devices.length ? ts.devices.map((d) => `<div class="remote-device"><span class="dot ${d.online ? 'ok' : ''}"></span>
                    <span>${esc(d.name)}</span><span class="set-desc">${esc(d.os)}${d.online ? ' · online' : d.last_seen ? ` · last seen ${esc(when(d.last_seen))}` : ''}</span></div>`).join('')
                    : '<p class="set-desc">None yet: install the Tailscale app on a phone or computer and sign in with the same account.</p>'}</div>
            <p><button type="button" class="btn-secondary" id="ts-logout">Use another Tailscale account</button></p>`;
        $('ts-logout').onclick = async () => {
            if (!(await window.showConfirm('Sign this NAS out of Tailscale?\nYour devices cannot reach it until you sign in again.', { confirmLabel: 'Sign out', danger: true }))) return;
            try { show(await api('/tailscale', { method: 'POST', body: JSON.stringify({ logout: true }) })); } catch (e) { note(e.message, 'error'); }
        };
    }

    // ── Cloudflare Tunnel ──────────────────────────────────────────────────
    function renderCloudflare(cf) {
        const body = $('cf-body');
        if (cf.enabled) {
            body.innerHTML = `
                <div class="set-row"><div class="set-text"><div class="set-label">The Hub on the internet</div>
                    <div class="set-desc">${cf.running ? 'Connected' : 'Not connected right now'}${cf.connected_at ? ` · set up ${esc(when(cf.connected_at))}` : ''}. Share links from the Hub use this address. Phones can sync the calendar with it too.</div></div>
                    <div class="set-value mono-text">${cf.url ? `<a href="${esc(cf.url)}" target="_blank" rel="noopener">${esc(cf.url.replace('https://', ''))}</a>` : 'Your hostname in Cloudflare'}</div>
                    <div class="set-action"><button type="button" class="btn-secondary" id="cf-remove">Remove</button></div></div>`;
            $('cf-remove').onclick = async () => {
                if (!(await window.showConfirm(`Take the Hub off ${cf.hostname || 'the internet'}?${cf.mode === 'api' ? '\nThe tunnel and its DNS name are removed in Cloudflare too.' : ''}`, { confirmLabel: 'Remove', danger: true }))) return;
                try { show(await api('/cloudflare', { method: 'DELETE' })); note('The Hub is no longer on the internet.', 'success'); } catch (e) { note(e.message, 'error'); }
            };
            return;
        }
        body.innerHTML = `
            <div class="cf-steps">
                <p><strong>1.</strong> In Cloudflare: My Profile › API Tokens › Create Token › “Create Custom Token” with the permissions <em>Account › Cloudflare Tunnel › Edit</em> and <em>Zone › DNS › Edit</em> (for your domain). Paste it here:</p>
                <div class="cf-line"><input type="password" id="cf-token" class="input" autocomplete="off" placeholder="API token" aria-label="Cloudflare API token">
                    <button type="button" class="btn-secondary" id="cf-check">Find my domains</button></div>
                <div id="cf-zone-step" hidden>
                    <p><strong>2.</strong> The address of the Hub:</p>
                    <div class="cf-line"><input id="cf-name" class="input" value="cloud" aria-label="Name" maxlength="63"><span>.</span><select id="cf-zone" class="input" aria-label="Domain"></select>
                        <button type="button" class="btn-primary" id="cf-make">Put the Hub there</button></div>
                </div>
                <details class="cf-manual"><summary>I made a tunnel in Cloudflare myself</summary>
                    <p class="set-desc">Zero Trust › Networks › Tunnels › your tunnel › Docker: copy the token after <code>--token</code>. Give the tunnel a public hostname that points to <code>http://localhost:8090</code>.</p>
                    <div class="cf-line"><input type="password" id="cf-tunnel" class="input" autocomplete="off" placeholder="Tunnel token" aria-label="Tunnel token">
                        <input id="cf-host" class="input" placeholder="cloud.example.com" aria-label="Public hostname">
                        <button type="button" class="btn-secondary" id="cf-use">Connect</button></div>
                </details>
                <p class="set-desc">Cloudflare ends the encryption in its network and does not allow large video streams through a tunnel: for movies at home use Tailscale.</p>
            </div>`;
        $('cf-check').onclick = async () => {
            try {
                const { zones } = await api('/cloudflare/zones', { method: 'POST', body: JSON.stringify({ api_token: $('cf-token').value }) });
                if (!zones.length) { note('This token sees no domain. Give it DNS › Edit for your domain.', 'error'); return; }
                $('cf-zone').innerHTML = zones.map((z) => `<option value="${esc(z.id)}">${esc(z.name)}</option>`).join('');
                $('cf-zone-step').hidden = false;
                $('cf-name').focus();
            } catch (e) { note(e.message, 'error'); }
        };
        $('cf-make').onclick = async (e) => {
            e.target.disabled = true;
            e.target.textContent = 'Setting up…';
            try {
                const data = await api('/cloudflare', { method: 'POST', body: JSON.stringify({ api_token: $('cf-token').value, zone_id: $('cf-zone').value, name: $('cf-name').value.trim() }) });
                note(data.message, 'success');
                show(data);
            } catch (err) {
                note(err.message, 'error');
                e.target.disabled = false;
                e.target.textContent = 'Put the Hub there';
            }
        };
        $('cf-use').onclick = async () => {
            try {
                const data = await api('/cloudflare', { method: 'POST', body: JSON.stringify({ tunnel_token: $('cf-tunnel').value.trim(), hostname: $('cf-host').value.trim() }) });
                note(data.message, 'success');
                show(data);
            } catch (e) { note(e.message, 'error'); }
        };
    }

    function show(data) {
        state = data;
        renderTailscale(data.tailscale);
        renderCloudflare(data.cloudflare);
        // While Tailscale starts or waits for the sign-in, look again now and then.
        clearTimeout(poll);
        if (data.tailscale.enabled && data.tailscale.state !== 'on' && !document.hidden) poll = setTimeout(load, 4000);
    }

    async function load() {
        try { show(await api('')); } catch (e) { $('ts-summary').textContent = e.message; }
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (!$('ts-body')) return;
        load();
        $('ts-enabled').addEventListener('change', async (e) => {
            const on = e.target.checked;
            e.target.disabled = true;
            try { show(await api('/tailscale', { method: 'POST', body: JSON.stringify({ enabled: on }) })); } catch (err) { note(err.message, 'error'); e.target.checked = !on; }
            e.target.disabled = false;
        });
    });
    window.addEventListener('hashchange', () => { if (location.hash === '#remote' && state) load(); });
})();
