// Settings › Security › Signed in: where AlvaOS is signed in, and signing out
// one browser or everywhere else.
(function () {
    const list = document.getElementById('sessions-list');
    const othersButton = document.getElementById('sessions-revoke-others');
    if (!list) return;

    const esc = window.escapeHtml || ((v) => String(v ?? ''));
    const PHONE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="20" x="5" y="2" rx="2"/><path d="M12 18h.01"/></svg>';
    const SCREEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8M12 17v4"/></svg>';

    function ago(value) {
        const t = new Date(value).getTime();
        if (!Number.isFinite(t)) return '';
        const min = Math.round((Date.now() - t) / 60000);
        if (min < 6) return 'active now';
        if (min < 60) return `${min} minutes ago`;
        const hours = Math.round(min / 60);
        if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
        return new Date(t).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    }

    async function load() {
        let data = null;
        try {
            const res = await fetch(`${API_BASE}/auth/sessions`);
            if (res.ok) data = await res.json();
        } catch (_e) { /* shown below */ }
        const sessions = Array.isArray(data?.sessions) ? data.sessions : null;
        if (!sessions) {
            list.innerHTML = '<div class="metric-sub">Could not read the list right now.</div>';
            othersButton.hidden = true;
            return;
        }
        othersButton.hidden = sessions.filter((s) => !s.current).length === 0;
        list.innerHTML = sessions.map((s) => {
            const mobile = /iPhone|Android|iPad/.test(s.device || '');
            const where = [s.ip, s.current ? 'active now' : `last used ${ago(s.last_seen_at)}`].filter(Boolean).join(' · ');
            return `
                <div class="session-row">
                    <span class="session-icon">${mobile ? PHONE : SCREEN}</span>
                    <span class="session-main"><strong>${esc(s.device)}${s.current ? '<span class="session-here">This browser</span>' : ''}</strong><small>${esc(where)}${s.created_at ? ` · signed in ${esc(ago(s.created_at))}` : ''}</small></span>
                    ${s.current ? '' : `<button type="button" class="btn-secondary" data-session="${esc(s.id)}">Sign out</button>`}
                </div>`;
        }).join('');
        list.querySelectorAll('[data-session]').forEach((button) => button.addEventListener('click', () => signOut(button)));
    }

    async function signOut(button) {
        button.disabled = true;
        try {
            const res = await fetch(`${API_BASE}/auth/sessions/${encodeURIComponent(button.dataset.session)}`, { method: 'DELETE' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok && res.status !== 404) throw new Error(data.error || 'Could not sign it out.');
            window.showToast('Signed out.', 'success');
        } catch (e) {
            window.showToast(e.message, 'error');
        }
        load();
    }

    othersButton.addEventListener('click', async () => {
        const ok = await window.showConfirm('Sign out everywhere else?\nEvery other browser and app has to sign in again. This browser stays signed in.', { confirmLabel: 'Sign out' });
        if (!ok) return;
        try {
            const res = await fetch(`${API_BASE}/auth/sessions/revoke-others`, { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || 'Could not sign out the others.');
            window.showToast(`Signed out ${data.signed_out} other session${data.signed_out === 1 ? '' : 's'}.`, 'success');
        } catch (e) {
            window.showToast(e.message, 'error');
        }
        load();
    });

    document.addEventListener('alvaos-sessions-changed', load);
    load();
})();
