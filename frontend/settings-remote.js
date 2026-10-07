// Settings › Remote access: WireGuard to this NAS (backend/remote_access.py).
// Each phone or computer gets its own key; its configuration is shown once
// as a QR code and a file, and never again.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let state = null;
    let warning = '';   // why the home may not be reachable from outside (CGNAT, two routers)

    async function api(path, options = {}) {
        const res = await fetch(`${API_BASE}${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }

    function when(iso) {
        if (!iso) return 'Not connected yet';
        const d = new Date(iso);
        const minutes = Math.round((Date.now() - d.getTime()) / 60000);
        if (minutes < 3) return 'Connected now';
        return `Last connected ${d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`;
    }

    function render() {
        const body = $('remote-body');
        $('remote-enabled').checked = !!state.enabled;
        $('remote-enabled').disabled = !state.wireguard_installed;
        if (!state.wireguard_installed) {
            $('remote-summary').textContent = 'WireGuard is not installed on this NAS.';
            body.innerHTML = '';
            return;
        }
        $('remote-summary').textContent = !state.enabled ? 'Off.'
            : state.running ? `On. ${state.devices.length} device${state.devices.length === 1 ? '' : 's'}.`
                : 'On, but the tunnel is not running. Turn it off and on again.';
        if (!state.enabled) {
            body.innerHTML = `
                <div class="remote-intro">
                    <p><strong>How it works</strong></p>
                    <ol>
                        <li>Turn it on and enter the public address of your home.</li>
                        <li>In your router, forward one UDP port to this NAS (the page tells you which).</li>
                        <li>Add each phone or laptop here; it gets a QR code for the free WireGuard app.</li>
                    </ol>
                </div>`;
            return;
        }
        const lan = (state.lan_addresses || [])[0] || 'this NAS';
        const duck = state.duckdns || {};
        body.innerHTML = `
            <div class="set-row">
                <div class="set-text"><div class="set-label"><label for="remote-endpoint">Public address of your home</label></div>
                    <div class="set-desc">A name from a dynamic DNS service (like home.duckdns.org), or the address your internet provider gives you.</div></div>
                <div class="set-value remote-endpoint">
                    <input type="text" id="remote-endpoint" value="${esc(state.endpoint)}" placeholder="home.example.net" autocomplete="off" spellcheck="false">
                </div>
                <div class="set-action">
                    <button type="button" class="btn-secondary" id="remote-find">Find it</button>
                    <button type="button" class="btn-primary" id="remote-save">Save</button>
                </div>
            </div>
            <details class="remote-ddns"${duck.domain ? ' open' : ''}>
                <summary>${duck.domain ? `DuckDNS keeps <strong>${esc(duck.domain)}.duckdns.org</strong> up to date` : 'Your address changes? Get a free name from DuckDNS'}</summary>
                <p class="set-desc">Most internet connections get a new address now and then. Make a free name at <span class="mono-text">duckdns.org</span> (sign in, add a domain) and enter it here with the token shown there; AlvaOS updates it every 10 minutes.</p>
                <div class="remote-ddns-form">
                    <label>Name <span class="remote-ddns-name"><input type="text" id="duck-domain" value="${esc(duck.domain)}" placeholder="myhome" autocomplete="off" spellcheck="false"><span>.duckdns.org</span></span></label>
                    <label>Token <input type="password" id="duck-token" placeholder="${duck.domain ? 'Saved. Type to replace it.' : 'a1b2c3d4-…'}" autocomplete="off" spellcheck="false"></label>
                    <div class="remote-ddns-actions">
                        <button type="button" class="btn-primary" id="duck-save">Save</button>
                        ${duck.domain ? '<button type="button" class="btn-secondary btn-quiet" id="duck-off">Stop using DuckDNS</button>' : ''}
                    </div>
                </div>
                ${duck.domain ? `<p class="set-desc">${duck.last_error ? `<span class="remote-bad">${esc(duck.last_error)}</span>` : duck.last_update ? `Last updated ${esc(new Date(duck.last_update).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }))}.` : 'Not updated yet.'}</p>` : ''}
            </details>
            <div class="set-row">
                <div class="set-text"><div class="set-label">In your router</div>
                    <div class="set-desc">${state.upnp
        ? `Your router forwards <strong>UDP port ${esc(state.port)}</strong> to this NAS; AlvaOS asked it automatically and renews it every day.`
        : `Forward <strong>UDP port ${esc(state.port)}</strong> to <strong>${esc(lan)}</strong> (this NAS). Often under "Port forwarding", "Port sharing" or "NAT". Nothing else needs to be opened.`}</div></div>
                <div class="set-action">${state.upnp_installed && !state.upnp ? '<button type="button" class="btn-secondary" id="remote-upnp">Open it automatically</button>' : ''}</div>
            </div>
            ${warning ? `<div class="remote-warning" role="alert">${esc(warning)}</div>` : ''}
            <h3 class="remote-subtitle">Devices</h3>
            <div class="remote-devices">${state.devices.length ? state.devices.map((d) => `
                <div class="remote-device">
                    <div><strong>${esc(d.name)}</strong><span>${esc(when(d.last_seen))} · ${esc(d.address)}</span></div>
                    <button type="button" class="btn-secondary btn-quiet" data-remove="${esc(d.id)}" data-name="${esc(d.name)}">Remove</button>
                </div>`).join('') : '<p class="set-desc">No devices yet.</p>'}
            </div>
            <form class="remote-add" id="remote-add">
                <input type="text" id="remote-name" placeholder="Name, like Anna's phone" maxlength="40" aria-label="Device name" autocomplete="off">
                <button type="submit" class="btn-primary" ${state.endpoint ? '' : 'disabled title="Enter the public address first"'}>Add a device</button>
            </form>
            <p class="set-desc remote-open">Connected through the tunnel, open AlvaOS at <span class="mono-text">${esc(state.open_url)}</span>.</p>`;

        $('remote-save').addEventListener('click', () => save({ endpoint: $('remote-endpoint').value.trim() }, 'Saved.'));
        $('remote-find').addEventListener('click', async () => {
            try {
                const data = await api('/remote-access/public-address', { method: 'POST', body: '{}' });
                const typed = data.address || $('remote-endpoint').value;
                warning = data.warning || '';
                render();
                $('remote-endpoint').value = typed;
                if (data.address) window.showToast?.('Found. Press Save. A name from a dynamic DNS service keeps working when this address changes.', 'info');
            } catch (e) {
                window.showToast?.(e.message, 'error');
            }
        });
        $('duck-save').addEventListener('click', () => save({ duckdns: { domain: $('duck-domain').value.trim(), token: $('duck-token').value.trim() } },
            'Saved. The name now points to your home.'));
        $('duck-off')?.addEventListener('click', () => save({ duckdns: null }, 'DuckDNS is no longer updated.'));
        $('remote-upnp')?.addEventListener('click', async (event) => {
            event.target.disabled = true;
            event.target.textContent = 'Asking the router...';
            try {
                const data = await api('/remote-access/router', { method: 'POST', body: '{}' });
                state = data;
                warning = data.warning || '';
                window.showToast?.(data.message, 'success');
            } catch (e) {
                window.showToast?.(e.message, 'error');
            }
            render();
        });
        $('remote-add').addEventListener('submit', async (event) => {
            event.preventDefault();
            const name = $('remote-name').value.trim();
            if (!name) { $('remote-name').focus(); return; }
            try {
                const data = await api('/remote-access/devices', { method: 'POST', body: JSON.stringify({ name }) });
                showDevice(data.device);
                load();
            } catch (e) {
                window.showToast?.(e.message, 'error');
            }
        });
        body.querySelectorAll('[data-remove]').forEach((b) => b.addEventListener('click', async () => {
            const ok = await window.showConfirm(`Remove "${b.dataset.name}"?\nThis device can no longer connect from outside.`,
                { danger: true, confirmLabel: 'Remove' });
            if (!ok) return;
            try {
                const data = await api(`/remote-access/devices/${encodeURIComponent(b.dataset.remove)}`, { method: 'DELETE' });
                window.showToast?.(data.message, 'success');
            } catch (e) {
                window.showToast?.(e.message, 'error');
            }
            load();
        }));
    }

    // The configuration of a new device: once, then it is gone from the NAS.
    function showDevice(device) {
        const blob = URL.createObjectURL(new Blob([device.config], { type: 'text/plain' }));
        const dlg = openDialog({
            title: `Connect "${esc(device.name)}"`,
            size: 'wide',
            bodyClass: 'remote-connect',
            focus: false,
            actions: '<button type="button" class="btn-primary" data-close>Done</button>',
            onClose: () => URL.revokeObjectURL(blob),
            body: `
                    <ol>
                        <li>Install the free <strong>WireGuard</strong> app (App Store, Google Play, wireguard.com for Windows, Mac and Linux).</li>
                        <li><strong>Phone:</strong> in the app, add a tunnel › scan from QR code.
                            ${device.qr ? `<img class="remote-qr" src="${esc(device.qr)}" alt="QR code with the WireGuard configuration for ${esc(device.name)}">` : ''}</li>
                        <li><strong>Computer:</strong> <a href="${blob}" download="${esc(device.file_name)}">download the file</a> and import it in the app.</li>
                        <li>Switch the tunnel on, then open <span class="mono-text">${esc(device.open_url)}</span>.</li>
                    </ol>
                    <p class="remote-once"><strong>Shown only now.</strong> The NAS does not keep this key. If you need it again, remove the device and add it anew.</p>`,
        });
        dlg.$('[data-close]').focus();
    }

    async function save(payload, message) {
        try {
            const data = await api('/remote-access', { method: 'POST', body: JSON.stringify(payload) });
            state = data;
            render();
            if (message || data.message) window.showToast?.(message || data.message, 'success');
        } catch (e) {
            window.showToast?.(e.message, 'error');
            load();
        }
    }

    async function load() {
        if (!$('remote-body')) return;
        try {
            state = await api('/remote-access');
        } catch (e) {
            $('remote-summary').textContent = e.message;
            return;
        }
        render();
    }

    $('remote-enabled')?.addEventListener('change', (event) => save({ enabled: event.target.checked }));
    load();
})();
