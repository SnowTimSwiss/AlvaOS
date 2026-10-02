// Settings › Notifications: email for problems and the weekly report.
// The mail server is filled in from the provider; the details are folded away.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = window.escapeHtml || ((v) => String(v ?? ''));
    let settings = null;

    const PROVIDERS = [
        ['gmail', 'Gmail', 'Gmail needs an app password: Google account › Security › App passwords.'],
        ['outlook', 'Outlook / Hotmail', 'Use your Outlook password, or an app password if two-step sign-in is on.'],
        ['icloud', 'iCloud Mail', 'iCloud needs an app-specific password: appleid.apple.com › Sign-In and Security.'],
        ['gmx', 'GMX', 'Turn on "POP3/IMAP access" in the GMX settings first.'],
        ['other', 'Another provider', 'Enter the mail server of your provider under "Mail server".'],
    ];

    async function api(path, options = {}) {
        const res = await fetch(`${API_BASE}${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }

    function render() {
        const email = settings?.email || {};
        const status = $('email-summary-status');
        const dot = $('email-summary-dot');
        if (!status) return;
        let text = 'Off';
        let color = 'var(--text-secondary)';
        if (email.enabled && email.configured) {
            text = email.last_error ? 'Last email failed' : 'On';
            color = email.last_error ? 'var(--accent-warning)' : 'var(--accent-success)';
        }
        status.textContent = text;
        if (dot) dot.style.background = color;
        $('email-summary').textContent = email.enabled && email.recipient
            ? `Problems that need action are emailed to ${email.recipient}.`
            : 'Problems that need action are emailed to you.';
        $('configure-email-btn').textContent = email.configured ? 'Change' : 'Set up';
        const toggle = $('weekly-report-toggle');
        if (toggle) toggle.checked = !!settings?.report?.weekly;
    }

    async function load() {
        try {
            settings = await api('/alerts/email');
        } catch (_e) {
            settings = null;
            if ($('email-summary-status')) $('email-summary-status').textContent = 'Unavailable';
            return;
        }
        render();
    }

    function dialog() {
        const email = settings?.email || {};
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="mail-title">
                <div class="modal-title"><span id="mail-title">Email notifications</span>
                    <button type="button" class="modal-close-x" aria-label="Close">&times;</button></div>
                <form class="modal-body pw-form" novalidate>
                    <label class="pw-field">Send messages to
                        <input type="email" id="mail-to" autocomplete="email" placeholder="you@example.com" value="${esc(email.recipient || '')}"></label>
                    <label class="pw-field">Your email provider
                        <select id="mail-provider">${PROVIDERS.map(([id, label]) => `<option value="${id}"${(email.configured ? email.provider : 'gmail') === id ? ' selected' : ''}>${label}</option>`).join('')}</select>
                        <small id="mail-hint"></small></label>
                    <label class="pw-field">Password for sending
                        <input type="password" id="mail-pass" autocomplete="new-password" placeholder="${email.password_set ? 'Saved. Type to replace it.' : ''}">
                        <small>AlvaOS sends from this account to the address above. It is stored on this NAS only.</small></label>
                    <details class="mail-adv" id="mail-adv">
                        <summary>Mail server</summary>
                        <div class="pw-form">
                            <div class="mail-grid">
                                <label class="pw-field">Server<input type="text" id="mail-host" placeholder="smtp.example.com" value="${esc(email.host || '')}"></label>
                                <label class="pw-field">Port<input type="number" id="mail-port" min="1" max="65535" value="${esc(email.port || 587)}"></label>
                            </div>
                            <label class="pw-field">Encryption
                                <select id="mail-security">
                                    <option value="starttls">STARTTLS (usually port 587)</option>
                                    <option value="ssl">SSL/TLS (usually port 465)</option>
                                    <option value="none">None (not recommended)</option>
                                </select></label>
                            <label class="pw-field">Sign in as<input type="text" id="mail-user" autocomplete="username" placeholder="Same as the address above" value="${esc(email.username || '')}"></label>
                            <label class="pw-field">Send from<input type="email" id="mail-from" placeholder="Same as the account" value="${esc(email.sender || '')}"></label>
                        </div>
                    </details>
                    <div class="pw-error" id="mail-error" role="alert"></div>
                    <div class="pw-ok" id="mail-ok" role="status"></div>
                    <div class="modal-actions">
                        ${email.enabled ? '<button type="button" class="btn-secondary" id="mail-off">Turn off</button>' : ''}
                        <button type="button" class="btn-secondary" id="mail-test">Send test email</button>
                        <button type="submit" class="btn-primary" id="mail-save">Save</button>
                    </div>
                </form>
            </div>`;
        document.body.appendChild(overlay);
        const close = () => overlay.remove();
        overlay.querySelector('.modal-close-x').onclick = close;
        if (window.attachModalDismiss) window.attachModalDismiss(overlay, close);

        const provider = overlay.querySelector('#mail-provider');
        const adv = overlay.querySelector('#mail-adv');
        const error = overlay.querySelector('#mail-error');
        const ok = overlay.querySelector('#mail-ok');
        overlay.querySelector('#mail-security').value = email.security || 'starttls';

        const syncProvider = () => {
            const entry = PROVIDERS.find(([id]) => id === provider.value) || PROVIDERS[4];
            overlay.querySelector('#mail-hint').textContent = entry[2];
            const preset = (settings?.presets || {})[provider.value] || {};
            if (preset.host) {
                overlay.querySelector('#mail-host').value = preset.host;
                overlay.querySelector('#mail-port').value = preset.port;
                overlay.querySelector('#mail-security').value = preset.security;
            }
            adv.open = provider.value === 'other';
        };
        provider.addEventListener('change', syncProvider);
        syncProvider();

        const collect = (enabled) => ({
            enabled,
            provider: provider.value,
            recipient: overlay.querySelector('#mail-to').value.trim(),
            password: overlay.querySelector('#mail-pass').value,
            host: overlay.querySelector('#mail-host').value.trim(),
            port: Number(overlay.querySelector('#mail-port').value || 587),
            security: overlay.querySelector('#mail-security').value,
            username: overlay.querySelector('#mail-user').value.trim(),
            sender: overlay.querySelector('#mail-from').value.trim(),
        });

        const busy = async (button, label, work) => {
            const before = button.textContent;
            button.disabled = true;
            button.textContent = label;
            error.textContent = '';
            ok.textContent = '';
            try {
                await work();
            } catch (e) {
                error.textContent = e.message;
            } finally {
                button.disabled = false;
                button.textContent = before;
            }
        };

        overlay.querySelector('#mail-test').onclick = (event) => busy(event.target, 'Sending...', async () => {
            const data = await api('/alerts/email/test', { method: 'POST', body: JSON.stringify({ email: collect(true) }) });
            ok.textContent = data.message;
        });
        overlay.querySelector('#mail-off')?.addEventListener('click', (event) => busy(event.target, 'Turning off...', async () => {
            settings = await api('/alerts/email', { method: 'POST', body: JSON.stringify({ email: { enabled: false } }) });
            render();
            close();
            window.showToast('Email notifications are off.', 'success');
        }));
        overlay.querySelector('form').addEventListener('submit', (event) => {
            event.preventDefault();
            busy(overlay.querySelector('#mail-save'), 'Saving...', async () => {
                settings = await api('/alerts/email', { method: 'POST', body: JSON.stringify({ email: collect(true) }) });
                render();
                close();
                window.showToast('Email notifications are on.', 'success');
            });
        });
        overlay.querySelector('#mail-to').focus();
    }

    $('configure-email-btn')?.addEventListener('click', () => {
        if (settings) dialog();
    });
    $('weekly-report-toggle')?.addEventListener('change', async (event) => {
        try {
            settings = await api('/alerts/email', { method: 'POST', body: JSON.stringify({ report: { weekly: event.target.checked } }) });
            window.showToast(event.target.checked ? 'Weekly report is on.' : 'Weekly report is off.', 'success');
        } catch (e) {
            event.target.checked = !event.target.checked;
            window.showToast(e.message, 'error');
        }
        render();
    });

    load();
})();
