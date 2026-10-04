// The "Hub" page in AlvaOS: turn the Hub on or off, open it, and choose which
// Hub apps are in it and who sees each one (backend/hub_apps.py).
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = window.escapeHtml || ((v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));
    const appUrl = (port) => `${window.location.protocol}//${window.location.hostname}:${port}/`;
    const svg = (name) => (window.alvaIcon ? window.alvaIcon(name, '', 'aria-hidden="true"') : '');
    let state = null;

    async function call(options = {}) {
        const res = await fetch(`${API_BASE}/hub`, { ...options, headers: { 'Content-Type': 'application/json' } });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }

    function renderTop() {
        const actions = $('fl-actions');
        if (state.enabled) {
            const url = appUrl(window.location.protocol === 'https:' ? (state.https_port || 9443) : state.port);
            actions.innerHTML = `
                <a class="btn-primary" href="${esc(url)}" target="_blank" rel="noopener">Open the Hub</a>
                <span class="fl-addr">${esc(url.replace(/^https?:\/\//, '').replace(/\/$/, ''))}</span>
                <button type="button" class="btn-secondary btn-quiet" id="fl-turn-off">Turn off</button>
                ${state.running ? '' : '<span class="fl-addr">Starting…</span>'}`;
            $('fl-turn-off').onclick = () => turn(false);
        } else {
            actions.innerHTML = `<button type="button" class="btn-primary" id="fl-turn-on">Turn on</button><span class="fl-addr">Opens port ${esc(state.port)} on this NAS.</span>`;
            $('fl-turn-on').onclick = () => turn(true);
        }
        const waiting = state.people_without_password || [];
        $('fl-note').hidden = !(state.enabled && waiting.length);
        $('fl-note').innerHTML = waiting.length
            ? `${esc(waiting.join(', '))} ${waiting.length === 1 ? 'needs' : 'need'} their password set once more before signing in to the Hub (passwords from before the Hub are not stored in a form it can check): <a href="storage.html#users">Storage › Users</a>.`
            : '';
    }

    function whoHtml(app) {
        if (!app.enabled) return '';
        const some = Array.isArray(app.people);
        const people = state.people || [];
        return `
            <div class="hub-who">
                <label>Who sees it
                    <select data-who="${esc(app.id)}" aria-label="Who sees ${esc(app.name)}">
                        <option value="all"${some ? '' : ' selected'}>Everyone</option>
                        <option value="some"${some ? ' selected' : ''}${people.length ? '' : ' disabled'}>Only some people</option>
                    </select>
                </label>
                ${some ? `<div class="hub-people" role="group" aria-label="People who see ${esc(app.name)}">${people.map((p) => `
                    <label><input type="checkbox" data-person="${esc(app.id)}" value="${esc(p)}"${app.people.includes(p) ? ' checked' : ''}> ${esc(p)}</label>`).join('')}</div>` : ''}
            </div>`;
    }

    function renderApps() {
        $('hub-apps').hidden = !state.enabled;
        const names = Object.fromEntries((state.apps || []).map((a) => [a.id, a.name]));
        $('hub-app-list').innerHTML = (state.apps || []).map((app) => `
            <div class="hub-app">
                <span class="hub-app-ic">${svg(app.icon)}</span>
                <div>
                    <strong>${esc(app.name)}</strong>
                    <div class="hub-desc">${esc(app.description)}</div>
                    ${app.needs.length ? `<div class="hub-needs">Part of ${esc(app.needs.map((n) => names[n] || n).join(', '))}: only there while that is on.</div>` : ''}
                    ${whoHtml(app)}
                </div>
                <label class="toggle" title="${app.enabled ? 'On' : 'Off'}"><input type="checkbox" data-app="${esc(app.id)}"${app.enabled ? ' checked' : ''} aria-label="${esc(app.name)} in the Hub"><span class="toggle-slider"></span></label>
            </div>`).join('');
        document.querySelectorAll('[data-app]').forEach((box) => box.addEventListener('change', () =>
            change(box.dataset.app, { enabled: box.checked }, `${names[box.dataset.app]} is ${box.checked ? 'on' : 'off'} in the Hub.`)));
        document.querySelectorAll('[data-who]').forEach((sel) => sel.addEventListener('change', () => {
            const id = sel.dataset.who;
            // "Only some people" starts with everyone ticked; the admin then unticks.
            change(id, { people: sel.value === 'all' ? null : [...(state.people || [])] },
                sel.value === 'all' ? `Everyone sees ${names[id]}.` : `Untick who should not see ${names[id]}.`);
        }));
        document.querySelectorAll('[data-person]').forEach((box) => box.addEventListener('change', () => {
            const id = box.dataset.person;
            const chosen = [...document.querySelectorAll(`[data-person="${id}"]:checked`)].map((b) => b.value);
            if (!chosen.length) {
                box.checked = true;
                window.showToast('Leave at least one person, or choose everyone.', 'info');
                return;
            }
            change(id, { people: chosen }, '');
        }));
    }

    function render() {
        renderTop();
        renderApps();
    }

    async function change(id, values, message) {
        try {
            state = await call({ method: 'POST', body: JSON.stringify({ apps: { [id]: values } }) });
            if (message) window.showToast(message, 'success');
        } catch (e) {
            window.showToast(e.message, 'error');
        }
        render();
    }

    async function turn(on) {
        const btn = $(on ? 'fl-turn-on' : 'fl-turn-off');
        if (btn) btn.disabled = true;
        try {
            state = await call({ method: 'POST', body: JSON.stringify({ enabled: on }) });
            window.showToast(on ? 'AlvaOS Hub is on.' : 'AlvaOS Hub is off.', 'success');
        } catch (e) {
            window.showToast(e.message, 'error');
        }
        render();
    }

    call().then((data) => { state = data; render(); })
        .catch((e) => { $('fl-actions').innerHTML = `<span class="fl-addr">${esc(e.message)}</span>`; });
})();
