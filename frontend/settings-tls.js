// Settings › Security: HTTPS with this NAS's own certificate authority
// (backend/tls_manager.py). Every device trusts the authority once; then
// https://<nas>:8443 opens without a warning.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = window.escapeHtml || ((v) => String(v ?? ''));
    let tls = null;

    const httpsUrl = () => `https://${window.location.hostname}:${(tls && tls.ports && tls.ports.web) || 8443}/`;

    function render() {
        const secure = window.location.protocol === 'https:';
        const status = $('tls-status');
        const dot = $('tls-dot');
        if (!status) return;
        if (!tls || !tls.ready) {
            status.textContent = 'Starts with the next restart';
            dot.style.background = 'var(--text-secondary)';
            return;
        }
        status.textContent = tls.https_only ? 'HTTPS only' : secure ? 'In use now' : 'Ready';
        dot.style.background = secure ? 'var(--accent-success)' : 'var(--accent-primary)';
        $('tls-desc').innerHTML = secure
            ? 'This page is encrypted. Devices that trust this NAS open it without a warning.'
            : `Ready at <a href="${esc(httpsUrl())}">${esc(httpsUrl())}</a>. Trust this NAS once on each device, then use that address.`;
    }

    const STEPS = [
        ['Windows', 'Open the downloaded file › Install Certificate › Local Machine › "Place all certificates in the following store" › Trusted Root Certification Authorities.'],
        ['Mac', 'Open the downloaded file; it is added to Keychain Access. Double-click "AlvaOS … local authority" › Trust › When using this certificate: Always Trust.'],
        ['iPhone and iPad', 'Download it in Safari and allow the profile. Then Settings › General › VPN & Device Management › install it, and Settings › General › About › Certificate Trust Settings › turn it on.'],
        ['Android', 'Settings › Security › More security settings › Encryption & credentials › Install a certificate › CA certificate, then choose the downloaded file.'],
        ['Linux and Firefox', 'Firefox keeps its own list: Settings › Privacy & Security › Certificates › View Certificates › Authorities › Import. On Linux in general: copy it to /usr/local/share/ca-certificates/ and run update-ca-certificates.'],
    ];

    function dialog() {
        const dlg = openDialog({
            title: 'Trust this NAS on your devices',
            size: 'wide',
            focus: false,
            actions: '<button type="button" class="btn-primary" data-close>Done</button>',
            body: `
                    <p style="margin-top:0;">AlvaOS made its own certificate authority. A device that trusts it opens every page of this NAS encrypted and without a warning, also Files and its WebDAV address.</p>
                    <ol class="tls-steps">
                        <li><a class="btn-primary" href="${API_BASE}/system/tls/ca.crt" download>Download the certificate</a></li>
                        <li>Install it as trusted:
                            ${STEPS.map(([who, how]) => `<details class="tls-os modal-details"><summary>${esc(who)}</summary><p>${esc(how)}</p></details>`).join('')}</li>
                        <li>Open <a href="${esc(httpsUrl())}">${esc(httpsUrl())}</a> and bookmark it.</li>
                    </ol>
                    <label class="tls-only">
                        <input type="checkbox" id="tls-only" ${tls.https_only ? 'checked' : ''} ${window.location.protocol === 'https:' ? '' : 'disabled'}>
                        <span><strong>HTTPS only</strong>
                        <small>${window.location.protocol === 'https:'
        ? 'Plain http:// addresses send everyone to https://. Turn it on once every device you use trusts this NAS.'
        : 'Open this page with https:// first; then you can turn this on without locking yourself out.'}</small></span>
                    </label>
                    <div class="pw-error" id="tls-error" role="alert"></div>
                    <details class="set-details modal-details" style="margin-top: 10px;">
                        <summary>Certificate details</summary>
                        <div class="modal-disclosure-panel"><dl class="set-kv">
                            <div><dt>Authority</dt><dd class="mono-text">${esc(tls.authority || '')}</dd></div>
                            <div><dt>Fingerprint (SHA-256)</dt><dd class="mono-text" style="word-break: break-all;">${esc(tls.fingerprint || '')}</dd></div>
                            <div><dt>Names</dt><dd class="mono-text">${esc([...(tls.names || []), ...(tls.addresses || [])].join(', '))}</dd></div>
                            <div><dt>HTTPS ports</dt><dd class="mono-text">AlvaOS ${tls.ports.web} · Files ${tls.ports.files} · WebDAV ${tls.ports.dav}</dd></div>
                        </dl>
                        <p class="field-hint">Compare the fingerprint if a device shows it while installing. The certificate renews itself when the NAS gets a new address; the authority stays the same, so devices keep trusting it.</p></div>
                    </details>`,
        });
        const overlay = dlg.dialog;
        overlay.querySelector('[data-close]').focus();
        overlay.querySelector('#tls-only').addEventListener('change', async (event) => {
            const on = event.target.checked;
            overlay.querySelector('#tls-error').textContent = '';
            try {
                const res = await fetch(`${API_BASE}/system/tls`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ https_only: on }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.error || 'That did not work.');
                tls.https_only = data.https_only;
                render();
                window.showToast?.(on ? 'Only HTTPS from now on.' : 'Plain http:// works again.', 'success');
            } catch (e) {
                event.target.checked = !on;
                overlay.querySelector('#tls-error').textContent = e.message;
            }
        });
    }

    async function load() {
        if (!$('tls-status')) return;
        try {
            const res = await fetch(`${API_BASE}/system/tls`);
            tls = res.ok ? await res.json() : null;
        } catch (_e) {
            tls = null;
        }
        render();
    }

    $('tls-setup-btn')?.addEventListener('click', () => {
        if (tls && tls.ready) dialog();
        else window.showToast?.('HTTPS starts with the next restart of AlvaOS.', 'info');
    });
    load();
})();
