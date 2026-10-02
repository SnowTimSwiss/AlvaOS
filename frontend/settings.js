// Settings page additions: the address to open AlvaOS, changing the admin
// password, and the logs folded away. The rest of the page is system.js.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = window.escapeHtml || ((v) => String(v ?? ''));

    // ── Network: where to open AlvaOS ────────────────────────────────────────

    function openUrl() {
        const name = ($('current-hostname-display')?.textContent || '').trim();
        const port = window.location.port ? `:${window.location.port}` : '';
        const host = name && name !== '-' ? `${name}.local` : window.location.hostname;
        return `${window.location.protocol}//${host}${port}`;
    }

    function renderOpenUrl() {
        const el = $('net-open-url');
        if (el) el.textContent = openUrl().replace(/^https?:\/\//, '');
    }

    const hostEl = $('current-hostname-display');
    if (hostEl) new MutationObserver(renderOpenUrl).observe(hostEl, { childList: true, characterData: true, subtree: true });
    renderOpenUrl();

    $('net-open-copy')?.addEventListener('click', async () => {
        const text = openUrl();
        try {
            await navigator.clipboard.writeText(text);
        } catch (_e) {
            const area = document.createElement('textarea');
            area.value = text;
            document.body.appendChild(area);
            area.select();
            try { document.execCommand('copy'); } catch (_ignored) { /* nothing more to try */ }
            area.remove();
        }
        window.showToast('Address copied', 'success');
    });

    // ── Security: change the admin password ──────────────────────────────────

    function passwordDialog() {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="pw-title">
                <div class="modal-title"><span id="pw-title">Change the admin password</span>
                    <button type="button" class="modal-close-x" aria-label="Close">&times;</button></div>
                <form class="modal-body pw-form" novalidate>
                    <label class="pw-field">Current password
                        <input type="password" id="pw-current" autocomplete="current-password" required></label>
                    <label class="pw-field">New password
                        <input type="password" id="pw-new" autocomplete="new-password" required>
                        <small id="pw-new-hint">At least 8 characters. A short sentence is easy to remember.</small></label>
                    <label class="pw-field">Type the new one again
                        <input type="password" id="pw-repeat" autocomplete="new-password" required>
                        <small id="pw-repeat-hint">&nbsp;</small></label>
                    <div class="pw-error" id="pw-error" role="alert"></div>
                    <p class="pw-note">Other browsers and apps are signed out and have to sign in with the new password. This browser stays signed in.</p>
                    <div class="modal-actions">
                        <button type="button" class="btn-secondary" id="pw-cancel">Cancel</button>
                        <button type="submit" class="btn-primary" id="pw-save" disabled>Change password</button>
                    </div>
                </form>
            </div>`;
        document.body.appendChild(overlay);

        const close = () => overlay.remove();
        overlay.querySelector('.modal-close-x').addEventListener('click', close);
        overlay.querySelector('#pw-cancel').addEventListener('click', close);
        if (window.attachModalDismiss) window.attachModalDismiss(overlay, close);

        const current = overlay.querySelector('#pw-current');
        const next = overlay.querySelector('#pw-new');
        const repeat = overlay.querySelector('#pw-repeat');
        const save = overlay.querySelector('#pw-save');
        const error = overlay.querySelector('#pw-error');

        const validate = () => {
            const longEnough = next.value.length >= 8;
            const same = next.value && repeat.value === next.value;
            overlay.querySelector('#pw-new-hint').textContent = !next.value || longEnough
                ? 'At least 8 characters. A short sentence is easy to remember.'
                : `${8 - next.value.length} more character${8 - next.value.length === 1 ? '' : 's'}.`;
            overlay.querySelector('#pw-repeat-hint').innerHTML = repeat.value
                ? (same ? 'Matches.' : 'Not the same yet.')
                : '&nbsp;';
            save.disabled = !(current.value && longEnough && same);
        };
        [current, next, repeat].forEach((input) => input.addEventListener('input', validate));

        overlay.querySelector('form').addEventListener('submit', async (event) => {
            event.preventDefault();
            if (save.disabled) return;
            save.disabled = true;
            save.textContent = 'Changing...';
            error.textContent = '';
            try {
                const res = await fetch(`${API_BASE}/auth/password`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ current_password: current.value, new_password: next.value }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.error || 'The password was not changed.');
                close();
                const others = Number(data.signed_out) || 0;
                window.showToast(others
                    ? `Password changed. ${others} other session${others === 1 ? ' was' : 's were'} signed out.`
                    : 'Password changed.', 'success');
                document.dispatchEvent(new CustomEvent('alvaos-sessions-changed'));
            } catch (e) {
                error.textContent = e.message;
                save.textContent = 'Change password';
                validate();
                current.focus();
            }
        });
        current.focus();
    }

    $('change-password-btn')?.addEventListener('click', passwordDialog);

    // ── Diagnostics: logs on request ─────────────────────────────────────────

    $('logs-details')?.addEventListener('toggle', (event) => {
        if (event.target.open) {
            const viewer = $('log-viewer');
            if (viewer) viewer.scrollTop = viewer.scrollHeight;
        }
    });

    window.escapeHtml = window.escapeHtml || esc;
})();
