// The "Hub" page in AlvaOS: turn the Hub on or off, open it, and choose which
// Hub apps are in it and who sees each one, and where they keep things
// (backend/hub_apps.py, docs/HUB.md).
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

    const poolOptions = (chosen) => (state.pools || []).map((p) =>
        `<option value="${esc(p.id)}"${p.id === chosen ? ' selected' : ''}>${esc(p.name)}</option>`).join('');

    // People who see an app that keeps their data in the personal folder, but have none.
    function personalFoldersHtml() {
        const missing = state.needs_personal_folder || [];
        if (!missing.length) return '';
        const pools = state.pools || [];
        return `
            <div class="hub-place">
                <strong>Personal folders</strong>
                <div class="fl-note">${esc(missing.join(', '))} ${missing.length === 1 ? 'has' : 'have'} no personal folder yet, so their own photos have nowhere to go.</div>
                ${pools.length ? `<div class="hub-row">
                    <label>Make them on <select id="hub-pf-pool" aria-label="Pool for the personal folders">${poolOptions(pools[0].id)}</select></label>
                    <label>Limit <input type="number" id="hub-pf-limit" min="1" step="1" placeholder="none" aria-label="Limit per person in GB"> GB each</label>
                    <button type="button" class="btn-primary" id="hub-pf-make">Make personal folders</button>
                </div>` : '<div class="hub-desc">Set up storage first: Storage › Pools.</div>'}
            </div>`;
    }

    function placeHtml(app) {
        const loc = app.location || { mode: 'personal' };
        const onPool = loc.mode === 'pool';
        return `
            <div class="hub-place">
                <strong>${esc(app.name)}: everyone's own ${esc(app.personal.toLowerCase())}</strong>
                <div class="hub-desc">${onPool
                    ? `Each person gets a folder of their own on the pool (like “${esc(`${(state.people || [])[0] || 'anna'}-${app.id}`)}”), only for them.`
                    : `In each person's personal folder, in “${esc(app.personal)}”. It counts against their personal folder's limit.`}</div>
                <div class="hub-row">
                    <label>Keep them in
                        <select data-place="${esc(app.id)}" aria-label="Where ${esc(app.name)} keeps everyone's own data">
                            <option value=""${onPool ? '' : ' selected'}>Their personal folder</option>
                            ${(state.pools || []).map((p) => `<option value="${esc(p.id)}"${onPool && loc.pool_id === p.id ? ' selected' : ''}>A folder per person on ${esc(p.name)}</option>`).join('')}
                        </select>
                    </label>
                    ${onPool ? `<label>Limit <input type="number" min="1" step="1" data-limit="${esc(app.id)}" value="${loc.limit_gb ? esc(loc.limit_gb) : ''}" placeholder="none" aria-label="Limit per person in GB"> GB per person</label>
                    <button type="button" class="btn-secondary" data-limit-save="${esc(app.id)}">Save limit</button>` : ''}
                </div>
            </div>`;
    }

    function librariesHtml(app) {
        const all = state.libraries_to_choose || [];
        const chosen = app.libraries || [];
        return `
            <div class="hub-place">
                <strong>${esc(app.name)}: libraries for the household</strong>
                <div class="hub-desc">Shared folders whose pictures appear in ${esc(app.name)} for everyone who may open them, next to their own.</div>
                ${all.length ? `<div class="hub-people hub-row" role="group" aria-label="${esc(app.name)} libraries">${all.map((n) => `
                    <label><input type="checkbox" data-library="${esc(app.id)}" value="${esc(n)}"${chosen.includes(n) ? ' checked' : ''}> ${esc(n)}</label>`).join('')}</div>`
                    : '<div class="hub-desc">There are no shared folders yet: Storage › Shared folders.</div>'}
            </div>`;
    }

    function cacheHtml() {
        const chosen = (state.storage || {}).cache_pool || '';
        const pools = state.pools || [];
        return `
            <div class="hub-place">
                <strong>Thumbnails and other caches</strong>
                <div class="hub-desc">Made again when needed: not backed up and not counted against limits.</div>
                <div class="hub-row">
                    <label>Keep them on
                        <select id="hub-cache" aria-label="Where the Hub keeps caches">
                            <option value=""${chosen ? '' : ' selected'}>The system disk</option>
                            ${poolOptions(chosen)}
                        </select>
                    </label>
                </div>
                ${state.cache_on_system_disk && pools.length ? '<div class="fl-note">The system disk is small. With many photos, choose a pool.</div>' : ''}
            </div>`;
    }

    function renderStorage() {
        const apps = (state.apps || []).filter((a) => a.enabled);
        $('hub-storage').hidden = !state.enabled || !apps.some((a) => a.personal || a.libraries);
        $('hub-storage-list').innerHTML = personalFoldersHtml()
            + apps.filter((a) => a.personal).map(placeHtml).join('')
            + apps.filter((a) => a.libraries).map(librariesHtml).join('')
            + cacheHtml();
        const names = Object.fromEntries((state.apps || []).map((a) => [a.id, a.name]));
        const limitOf = (id) => {
            const box = document.querySelector(`[data-limit="${id}"]`);
            return box && box.value ? Number(box.value) : null;
        };
        document.querySelectorAll('[data-place]').forEach((sel) => sel.addEventListener('change', () => {
            const id = sel.dataset.place;
            const location = sel.value ? { mode: 'pool', pool_id: sel.value, limit_gb: limitOf(id) } : { mode: 'personal' };
            change(id, { location }, sel.value ? '' : `${names[id]} keeps everyone's data in their personal folder.`);
        }));
        document.querySelectorAll('[data-limit-save]').forEach((btn) => btn.addEventListener('click', () => {
            const id = btn.dataset.limitSave;
            const app = state.apps.find((a) => a.id === id);
            change(id, { location: { ...app.location, limit_gb: limitOf(id) } }, 'Saved. New folders get this limit.');
        }));
        document.querySelectorAll('[data-library]').forEach((box) => box.addEventListener('change', () => {
            const id = box.dataset.library;
            const chosen = [...document.querySelectorAll(`[data-library="${id}"]:checked`)].map((b) => b.value);
            change(id, { libraries: chosen }, '');
        }));
        const cache = $('hub-cache');
        if (cache) cache.addEventListener('change', () => post({ storage: { cache_pool: cache.value } },
            cache.value ? 'Caches now go to that pool.' : 'Caches now stay on the system disk.'));
        const make = $('hub-pf-make');
        if (make) make.addEventListener('click', () => {
            make.disabled = true;
            const limit = $('hub-pf-limit').value;
            post({ personal_folders: { pool_id: $('hub-pf-pool').value, limit_gb: limit ? Number(limit) : null } }, '');
        });
    }

    function render() {
        renderTop();
        renderApps();
        renderStorage();
    }

    async function post(body, message) {
        try {
            state = await call({ method: 'POST', body: JSON.stringify(body) });
            const said = [message, state.message].filter(Boolean).join(' ');
            if (said) window.showToast(said, 'success');
        } catch (e) {
            window.showToast(e.message, 'error');
        }
        render();
    }

    function change(id, values, message) {
        return post({ apps: { [id]: values } }, message);
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
