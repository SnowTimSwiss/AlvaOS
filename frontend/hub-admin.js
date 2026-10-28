// The "Hub" page in AlvaOS: turn the Hub on or off, open it, and choose which
// Hub apps are in it and who sees each one, and where they keep things
// (backend/hub_apps.py, docs/HUB.md).
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = window.escapeHtml || ((v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));
    const appUrl = (port) => `${window.location.protocol}//${window.location.hostname}:${port}/`;
    const svg = (name) => (window.alvaIcon ? window.alvaIcon(name, '', 'aria-hidden="true"') : '');
    let state = null;

    // "Everyone | Only some people", and the people as chips to switch on and off.
    const seg = (attr, id, some, name, noPeople) => `
        <div class="seg" role="group" aria-label="Who sees ${esc(name)}">
            <button type="button" ${attr}="${esc(id)}" data-v="all" aria-pressed="${!some}">Everyone</button>
            <button type="button" ${attr}="${esc(id)}" data-v="some" aria-pressed="${some}"${noPeople ? ' disabled' : ''}>Only some people</button>
        </div>`;
    const chip = (attr, id, person, on) => `
        <label class="chip"><input type="checkbox" ${attr}="${esc(id)}" value="${esc(person)}"${on ? ' checked' : ''}><span class="chip-av" aria-hidden="true">${esc(person.charAt(0).toUpperCase())}</span>${esc(person)}</label>`;

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
                ${state.running ? '' : '<span class="fl-addr" id="fl-starting">Starting…</span>'}`;
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
        watchStart();
    }

    function whoHtml(app) {
        if (!app.enabled) return '';
        const some = Array.isArray(app.people);
        const people = state.people || [];
        return `
            <div class="hub-who">
                <span class="hub-who-label">Who sees it</span>
                ${seg('data-who', app.id, some, app.name, !people.length)}
                ${some ? `<div class="hub-people chips" role="group" aria-label="People who see ${esc(app.name)}">${people.map((p) => chip('data-person', app.id, p, app.people.includes(p))).join('')}</div>` : ''}
            </div>`;
    }

    // Chat: the AI services it talks to (Settings › AI), which models it
    // offers (all, only some, all but some; whole services or single models),
    // the same for single people on top, and how long unused chats stay.
    let chatWho = '';      // '' = everyone, else a person
    let chatDraft = null;  // the rule being changed, before Save
    const KEEP = [[7, '7 days'], [14, '14 days'], [30, '30 days'], [90, '90 days'], [365, 'a year'], [0, 'never']];
    function chatModels() {
        return ((state.chat_service || {}).services || []).filter((s) => s.ready)
            .flatMap((s) => s.models.map((m) => ({ id: `${s.id}/${m}`, model: m, service: s.id })));
    }
    function applyRule(items, rule) {
        if (!rule || rule.mode === 'all') return items;
        const refs = new Set(rule.items || []);
        const listed = (i) => refs.has(i.service) || refs.has(i.id);
        return items.filter((i) => (rule.mode === 'only' ? listed(i) : !listed(i)));
    }
    function savedRule(app) {
        return chatWho ? (app.people_access || {})[chatWho] || null : app.access;
    }
    function modelsHtml(app) {
        const service = state.chat_service || {};
        const services = (service.services || []);
        if (!service.ready) {
            return `<div class="hub-chat hub-chat-setup"><div>${services.length
                ? 'The AI services have no models Chat can use yet, or need a key.'
                : 'Chat needs an AI service first: Ollama at home (free, stays in your house), or a service with a key.'}</div>
                <div class="hub-form"><a class="btn-primary" href="system.html#assistant">${services.length ? 'Configure AI' : 'Set up an AI service'}</a></div></div>`;
        }
        const all = chatModels();
        const rule = chatDraft || savedRule(app) || { mode: chatWho ? 'same' : 'all', items: [] };
        const refs = new Set(rule.items || []);
        const people = Array.isArray(app.people) ? app.people : (state.people || []);
        const own = app.people_access || {};
        const listing = rule.mode === 'only' || rule.mode === 'except';
        const tree = listing ? services.filter((s) => s.ready && s.models.length).map((s) => `
            <div class="hub-svc">
                <label class="hub-svc-name"><input type="checkbox" data-ref="${esc(s.id)}"${refs.has(s.id) ? ' checked' : ''}> <strong>${esc(s.name)}</strong> <span>whole service, also models added later</span></label>
                <div class="hub-people">${s.models.map((m) => `
                    <label><input type="checkbox" data-ref="${esc(`${s.id}/${m}`)}"${refs.has(`${s.id}/${m}`) || refs.has(s.id) ? ' checked' : ''}${refs.has(s.id) ? ' disabled' : ''}> ${esc(m)}</label>`).join('')}</div>
            </div>`).join('') : '';
        // What it comes to, for everyone or for this person.
        let result = applyRule(all, chatWho ? app.access : rule);
        if (chatWho && rule.mode !== 'same') result = applyRule(result, rule);
        const names = result.map((i) => i.model);
        const dirty = !!chatDraft;
        return `
            <div class="hub-chat">
                <div class="hub-chat-head"><span>${services.length} AI service${services.length === 1 ? '' : 's'} with ${all.length} model${all.length === 1 ? '' : 's'}. Keys stay on the NAS.</span>
                    <a class="btn-secondary" href="system.html#assistant">Configure AI</a></div>
                <div class="hub-models">
                    <div class="hub-form">
                        <label>Models for
                            <select id="chat-who" aria-label="Models for">
                                <option value=""${chatWho ? '' : ' selected'}>Everyone</option>
                                ${people.map((p) => `<option value="${esc(p)}"${chatWho === p ? ' selected' : ''}>${esc(p)}${own[p] ? ' (own rule)' : ''}</option>`).join('')}
                            </select></label>
                        <select id="chat-mode" aria-label="Which models">
                            ${chatWho ? `<option value="same"${rule.mode === 'same' ? ' selected' : ''}>The same as everyone</option>` : `<option value="all"${rule.mode === 'all' ? ' selected' : ''}>All models</option>`}
                            <option value="only"${rule.mode === 'only' ? ' selected' : ''}>${chatWho ? 'Of those, only these' : 'Only these'}</option>
                            <option value="except"${rule.mode === 'except' ? ' selected' : ''}>${chatWho ? 'Of those, all but these' : 'All but these'}</option>
                        </select>
                    </div>
                    ${tree}
                    <div>${chatWho ? `${esc(chatWho)} chooses from` : 'People choose from'}: <strong>${names.length ? esc(names.join(', ')) : 'no model'}</strong></div>
                    ${dirty ? `<div class="hub-form"><button type="button" class="btn-primary" id="chat-rule-save">Save</button><button type="button" class="btn-secondary btn-quiet" id="chat-rule-cancel">Cancel</button></div>` : ''}
                </div>
                ${searchHtml(app)}
                <div class="hub-form"><label>Chats nobody used for
                    <select id="chat-keep" aria-label="Delete unused chats after">${KEEP.map(([d, label]) => `<option value="${d}"${app.keep_days === d ? ' selected' : ''}>${label}</option>`).join('')}${KEEP.some(([d]) => d === app.keep_days) ? '' : `<option value="${app.keep_days}" selected>${app.keep_days} days</option>`}</select></label>
                    <span>are deleted, unless pinned or kept.</span></div>
            </div>`;
    }
    function searchHtml(app) {
        if (!(state.chat_service || {}).search) {
            return '<div>Web search is off: turn it on in <a href="system.html#assistant">Settings › AI</a> (DuckDuckGo, nothing to install).</div>';
        }
        const some = Array.isArray(app.search_people);
        const people = Array.isArray(app.people) ? app.people : (state.people || []);
        return `<div class="hub-form"><label>Web search (DuckDuckGo)
                <select id="chat-search" aria-label="Who may search the web">
                    <option value="all"${some ? '' : ' selected'}>Everyone</option>
                    <option value="some"${some ? ' selected' : ''}>Only some people</option>
                </select></label></div>
            ${some ? `<div class="hub-people chips" role="group" aria-label="People who may search the web">${people.map((p) => chip('data-search-person', 'search', p, app.search_people.includes(p))).join('')}</div>` : ''}`;
    }
    function bindModels() {
        const app = (state.apps || []).find((a) => a.id === 'chat');
        const who = $('chat-who');
        if (!app || !who) return;
        who.addEventListener('change', () => { chatWho = who.value; chatDraft = null; renderApps(); });
        const current = () => chatDraft || savedRule(app) || { mode: chatWho ? 'same' : 'all', items: [] };
        $('chat-mode').addEventListener('change', (e) => {
            const before = current();
            chatDraft = { mode: e.target.value, items: e.target.value === before.mode ? before.items : [] };
            renderApps();
        });
        document.querySelectorAll('[data-ref]').forEach((box) => box.addEventListener('change', () => {
            const rule = current();
            const items = new Set(rule.items || []);
            const ref = box.dataset.ref;
            if (box.checked) items.add(ref); else items.delete(ref);
            if (!ref.includes('/') && box.checked) [...items].forEach((r) => { if (r.startsWith(`${ref}/`)) items.delete(r); });
            chatDraft = { mode: rule.mode, items: [...items] };
            renderApps();
        }));
        const save = $('chat-rule-save');
        if (save) save.addEventListener('click', async () => {
            const rule = chatDraft;
            chatDraft = null;
            if (chatWho) {
                await change('chat', { people_access: { [chatWho]: rule.mode === 'same' ? null : rule } },
                    rule.mode === 'same' ? `${chatWho} has the same models as everyone.` : `Models for ${chatWho} saved.`);
            } else {
                await change('chat', { access: rule }, 'The models in Chat are saved.');
            }
        });
        const cancel = $('chat-rule-cancel');
        if (cancel) cancel.addEventListener('click', () => { chatDraft = null; renderApps(); });
        const search = $('chat-search');
        if (search) search.addEventListener('change', () => change('chat', { search_people: search.value === 'all' ? null : [] },
            search.value === 'all' ? 'Everyone may search the web in Chat.' : 'Switch on who may search the web.'));
        document.querySelectorAll('[data-search-person]').forEach((box) => box.addEventListener('change', () => {
            const who = [...document.querySelectorAll('[data-search-person]:checked')].map((b) => b.value);
            change('chat', { search_people: who }, '');
        }));
        $('chat-keep').addEventListener('change', (e) => {
            const days = Number(e.target.value);
            change('chat', { keep_days: days }, days ? `Unused chats are deleted after ${KEEP.find(([d]) => d === days)?.[1] || `${days} days`}.` : 'Chats are kept until people delete them.');
        });
    }

    // Who the Hub's people are, and the way to the place where they are made (Storage › Users).
    function renderPeople() {
        const bar = $('hub-people-bar');
        if (!bar) return;
        const people = state.people || [];
        bar.innerHTML = people.length
            ? `<span><strong>${people.length === 1 ? '1 person' : `${people.length} people`}</strong> can sign in:</span>
               <div class="hub-people chips">${people.map((p) => `<span class="chip"><span class="chip-av" aria-hidden="true">${esc(p.charAt(0).toUpperCase())}</span>${esc(p)}</span>`).join('')}</div>
               <a class="btn-secondary" href="storage.html#users">Manage people</a>`
            : `<span>Nobody can sign in to the Hub yet. Everyone has their own name and password.</span>
               <a class="btn-primary" href="storage.html#users" style="margin-left:auto;text-decoration:none">Add people</a>`;
    }

    function renderApps() {
        $('hub-apps').hidden = !state.enabled;
        renderPeople();
        const names = Object.fromEntries((state.apps || []).map((a) => [a.id, a.name]));
        $('hub-app-list').innerHTML = (state.apps || []).map((app) => `
            <div class="hub-app">
                <span class="hub-app-ic">${svg(app.icon)}</span>
                <div>
                    <strong>${esc(app.name)}</strong>
                    <div class="hub-desc">${esc(app.description)}</div>
                    ${app.needs.length ? `<div class="hub-needs">Part of ${esc(app.needs.map((n) => names[n] || n).join(', '))}: only there while that is on.</div>` : ''}
                    ${whoHtml(app)}
                    ${app.models_choice && app.enabled ? modelsHtml(app) : ''}
                </div>
                <label class="toggle" title="${app.enabled ? 'On' : 'Off'}"><input type="checkbox" data-app="${esc(app.id)}"${app.enabled ? ' checked' : ''} aria-label="${esc(app.name)} in the Hub"><span class="toggle-slider"></span></label>
            </div>`).join('');
        document.querySelectorAll('[data-app]').forEach((box) => box.addEventListener('change', () =>
            change(box.dataset.app, { enabled: box.checked }, `${names[box.dataset.app]} is ${box.checked ? 'on' : 'off'} in the Hub.${
                box.checked && box.dataset.app === 'chat' && !(state.chat_service || {}).ready ? ' Next: set up an AI service below.' : ''}`)));
        document.querySelectorAll('[data-who]').forEach((btn) => btn.addEventListener('click', () => {
            const id = btn.dataset.who;
            const all = btn.dataset.v === 'all';
            if (all === !Array.isArray(((state.apps || []).find((a) => a.id === id) || {}).people)) return;
            // "Only some people" starts with everyone on; the admin then switches people off.
            change(id, { people: all ? null : [...(state.people || [])] },
                all ? `Everyone sees ${names[id]}.` : `Switch off who should not see ${names[id]}.`);
        }));
        document.querySelectorAll('[data-person]').forEach((box) => box.addEventListener('change', () => {
            const id = box.dataset.person;
            const chosen = [...document.querySelectorAll(`[data-person="${id}"]:checked`)].map((b) => b.value);
            if (!chosen.length) {
                box.checked = true;
                window.showToast('Leave at least one person, or choose Everyone.', 'info');
                return;
            }
            change(id, { people: chosen }, '');
        }));
        bindModels();
        renderStore();
    }

    // Apps from the App Store: a tile in the Hub opens them (their own page,
    // port and sign-in). Off until shown here.
    function renderStore() {
        const box = $('hub-store-list');
        if (!box) return;
        const apps = state.store_apps || [];
        $('hub-store').hidden = !state.enabled;
        if (!apps.length) {
            box.innerHTML = '<p class="hub-st-note">No installed app has a page to open yet. Apps from the <a href="apps.html">App Store</a> show up here.</p>';
            return;
        }
        const people = state.people || [];
        box.innerHTML = apps.map((app) => {
            const some = Array.isArray(app.people);
            return `
            <div class="hub-app">
                <span class="hub-app-ic hub-letter" aria-hidden="true">${esc(app.name.charAt(0).toUpperCase())}</span>
                <div>
                    <strong>${esc(app.name)}</strong>
                    <div class="hub-desc">Opens ${esc(window.location.hostname)}:${esc(app.port)}${esc(app.path === '/' ? '' : app.path)} in a new tab, with its own sign-in.</div>
                    ${app.shown ? `<div class="hub-who">
                        <span class="hub-who-label">Who sees it</span>
                        ${seg('data-store-who', app.id, some, app.name, !people.length)}
                        ${some ? `<div class="hub-people chips" role="group" aria-label="People who see ${esc(app.name)}">${people.map((p) => chip('data-store-person', app.id, p, app.people.includes(p))).join('')}</div>` : ''}
                    </div>` : ''}
                </div>
                <label class="toggle" title="${app.shown ? 'Shown' : 'Not shown'}"><input type="checkbox" data-store="${esc(app.id)}"${app.shown ? ' checked' : ''} aria-label="${esc(app.name)} in the Hub"><span class="toggle-slider"></span></label>
            </div>`;
        }).join('');
        const names = Object.fromEntries(apps.map((a) => [a.id, a.name]));
        const changeStore = (id, body, message) => post({ store: { [id]: body } }, message);
        box.querySelectorAll('[data-store]').forEach((b) => b.addEventListener('change', () =>
            changeStore(b.dataset.store, { shown: b.checked }, `${names[b.dataset.store]} is ${b.checked ? 'now' : 'no longer'} a tile in the Hub.`)));
        box.querySelectorAll('[data-store-who]').forEach((btn) => btn.addEventListener('click', () => {
            const id = btn.dataset.storeWho;
            const all = btn.dataset.v === 'all';
            if (all === !Array.isArray((apps.find((a) => a.id === id) || {}).people)) return;
            changeStore(id, { people: all ? null : [...people] },
                all ? `Everyone sees ${names[id]}.` : `Switch off who should not see ${names[id]}.`);
        }));
        box.querySelectorAll('[data-store-person]').forEach((b) => b.addEventListener('change', () => {
            const id = b.dataset.storePerson;
            const chosen = [...box.querySelectorAll(`[data-store-person="${id}"]:checked`)].map((x) => x.value);
            if (!chosen.length) {
                b.checked = true;
                window.showToast('Leave at least one person, or choose Everyone.', 'info');
                return;
            }
            changeStore(id, { people: chosen }, '');
        }));
    }

    const poolOptions = (chosen) => (state.pools || []).map((p) =>
        `<option value="${esc(p.id)}"${p.id === chosen ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
    const poolName = (id) => ((state.pools || []).find((p) => p.id === id) || {}).name || 'a pool';
    const list = (names) => names.length > 2 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names.join(' and ');
    // Which rows are open, so they stay open when the page draws itself again.
    const opened = new Set();
    let moreOpen = false;

    // One line per topic: what it is now, and the details only when opened.
    function row(key, icon, title, value, body, { tone = '', action = '' } = {}) {
        const open = opened.has(key);
        return `
            <div class="hub-st${open ? ' open' : ''}${tone ? ` is-${tone}` : ''}">
                <div class="hub-st-head">
                    <button type="button" class="hub-st-toggle" data-row="${esc(key)}" aria-expanded="${open}">
                        <span class="hub-st-ic">${svg(icon)}</span>
                        <span class="hub-st-text"><strong>${esc(title)}</strong><span class="hub-st-value">${value}</span></span>
                        <span class="hub-st-chev">${svg('chevron-down')}</span>
                    </button>
                    ${action}
                </div>
                ${open ? `<div class="hub-st-body">${body}</div>` : ''}
            </div>`;
    }

    function personalRow() {
        const missing = state.needs_personal_folder || [];
        const pools = state.pools || [];
        if (!missing.length) {
            return row('personal', 'users', 'Personal folders', 'Everyone who keeps things in the Hub has one.',
                '<p class="hub-st-note">New people get one when you add them in Storage › Users.</p>');
        }
        const body = pools.length ? `
            <div class="hub-form">
                <label>On <select id="hub-pf-pool" aria-label="Pool for the personal folders">${poolOptions(pools[0].id)}</select></label>
                <label>Limit <input type="number" id="hub-pf-limit" min="1" step="1" placeholder="none" aria-label="Limit per person in GB"> GB each</label>
            </div>
            <p class="hub-st-note">Only that person can open their folder, over the network too.</p>`
            : '<p class="hub-st-note">Set up storage first: Storage › Pools.</p>';
        return row('personal', 'users', 'Personal folders',
            `${esc(list(missing))} ${missing.length === 1 ? 'has' : 'have'} none yet, so ${esc(list((state.apps || []).filter((a) => a.enabled && a.personal).map((a) => a.name)))} cannot keep their things.`, body,
            { tone: 'warn', action: pools.length ? '<button type="button" class="btn-primary hub-st-act" id="hub-pf-make">Make them</button>' : '' });
    }

    function librariesRow(apps) {
        const all = state.libraries_to_choose || [];
        return apps.map((app) => {
            const chosen = app.libraries || [];
            const what = { photos: ['the pictures of these folders', 'Only everyone\'s own photos are shown.', 'photo libraries'],
                calendar: ['a family calendar for each of these folders', 'Only everyone\'s own calendars.', 'family calendars'] }[app.id]
                || [`these folders in ${app.name}`, 'None yet.', `folders in ${app.name}`];
            const body = all.length ? `
                <p class="hub-st-note">Everyone sees ${esc(what[0])} in ${esc(app.name)}, if they may open the folder${app.id === 'calendar' ? ', and changes it if they may change the folder' : ''}.</p>
                <div class="hub-people" role="group" aria-label="${esc(app.name)} libraries">${all.map((n) => `
                    <label><input type="checkbox" data-library="${esc(app.id)}" value="${esc(n)}"${chosen.includes(n) ? ' checked' : ''}> ${esc(n)}</label>`).join('')}</div>`
                : '<p class="hub-st-note">There are no shared folders yet: Storage › Shared folders.</p>';
            return row(`lib-${app.id}`, app.icon, `Shared ${what[2]}`,
                chosen.length ? esc(list(chosen)) : `None yet. ${esc(what[1])}`, body);
        }).join('');
    }

    function placeText(app) {
        const loc = app.location || { mode: 'personal' };
        if (loc.mode !== 'pool') return `${esc(app.name)}: in the personal folder`;
        return `${esc(app.name)}: a folder per person on ${esc(poolName(loc.pool_id))}${loc.limit_gb ? `, ${esc(loc.limit_gb)} GB each` : ''}`;
    }

    function placesRow(apps) {
        const body = apps.map((app) => {
            const loc = app.location || { mode: 'personal' };
            const onPool = loc.mode === 'pool';
            return `
                <div class="hub-st-app">
                    <div class="hub-form">
                        <label>${esc(app.name)}
                            <select data-place="${esc(app.id)}" aria-label="Where ${esc(app.name)} keeps everyone's own data">
                                <option value=""${onPool ? '' : ' selected'}>In the personal folder (${esc(app.personal.startsWith('.') ? `hidden folder ${app.personal}` : app.personal)})</option>
                                ${(state.pools || []).map((p) => `<option value="${esc(p.id)}"${onPool && loc.pool_id === p.id ? ' selected' : ''}>A folder per person on ${esc(p.name)}</option>`).join('')}
                            </select>
                        </label>
                        ${onPool ? `<label>Limit <input type="number" min="1" step="1" data-limit="${esc(app.id)}" value="${loc.limit_gb ? esc(loc.limit_gb) : ''}" placeholder="none" aria-label="Limit per person in GB"> GB each</label>
                        <button type="button" class="btn-secondary" data-limit-save="${esc(app.id)}">Save</button>` : ''}
                    </div>
                    <p class="hub-st-note">${onPool
                        ? `Each person gets a shared folder of their own, like “${esc(`${(state.people || [])[0] || 'anna'}-${app.id}`)}”, that only they can open.`
                        : 'Counts against the limit of their personal folder.'}</p>
                </div>`;
        }).join('');
        return row('places', 'hard-drive', 'Everyone\'s own data per app', apps.map(placeText).join(' · '),
            `<p class="hub-st-note">For example the photos on the large disk and everything else on the fast one.</p>${body}`);
    }

    function cacheRow() {
        const chosen = (state.storage || {}).cache_pool || '';
        const warn = state.cache_on_system_disk && (state.pools || []).length;
        return row('cache', 'layers', 'Thumbnails and caches',
            chosen ? `On ${esc(poolName(chosen))}` : `On the system disk${warn ? '. It is small: choose a pool if there are many photos.' : ''}`, `
            <div class="hub-form">
                <label>Keep them on
                    <select id="hub-cache" aria-label="Where the Hub keeps caches">
                        <option value=""${chosen ? '' : ' selected'}>The system disk</option>
                        ${poolOptions(chosen)}
                    </select>
                </label>
            </div>
            <p class="hub-st-note">Made again when needed: not backed up and not counted against limits.</p>`,
            { tone: warn ? 'warn' : '' });
    }

    function renderStorage() {
        const apps = (state.apps || []).filter((a) => a.enabled);
        const personal = apps.filter((a) => a.personal);
        const withLibraries = apps.filter((a) => a.libraries);
        $('hub-storage').hidden = !state.enabled || !(personal.length || withLibraries.length);
        const suggest = state.cache_on_system_disk && (state.pools || []).length;
        $('hub-storage-list').innerHTML = `
            ${personal.length ? personalRow() : ''}
            ${librariesRow(withLibraries)}
            <details class="hub-more" id="hub-more"${moreOpen ? ' open' : ''}>
                <summary>More options${suggest ? ' <span class="hub-badge">1 tip</span>' : ''}</summary>
                ${personal.length ? placesRow(personal) : ''}
                ${cacheRow()}
            </details>`;
        $('hub-more').addEventListener('toggle', () => { moreOpen = $('hub-more').open; });
        document.querySelectorAll('[data-row]').forEach((btn) => btn.addEventListener('click', () => {
            const key = btn.dataset.row;
            if (opened.has(key)) opened.delete(key); else opened.add(key);
            renderStorage();
        }));
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
            cache.value ? 'Thumbnails now go to that pool.' : 'Thumbnails now stay on the system disk.'));
        const make = $('hub-pf-make');
        if (make) make.addEventListener('click', () => {
            make.disabled = true;
            // Closed row: the first pool, no limit. Opened: what was chosen there.
            const pool = $('hub-pf-pool') ? $('hub-pf-pool').value : state.pools[0].id;
            const limit = $('hub-pf-limit') ? $('hub-pf-limit').value : '';
            post({ personal_folders: { pool_id: pool, limit_gb: limit ? Number(limit) : null } }, '');
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

    // Turned on but not up yet (just after an update, or just turned on): ask again until it is, and
    // offer a restart when it does not come up.
    let waiting = null;
    function watchStart() {
        clearTimeout(waiting);
        if (!state || !state.enabled || state.running) return;
        const began = Date.now();
        const look = async () => {
            try { state = await call(); } catch (e) { /* the backend is restarting too: ask again */ }
            if (state && state.running) { render(); return; }
            if (Date.now() - began > 60000) {
                const note = $('fl-starting');
                if (note) note.outerHTML = '<span class="fl-addr">Not responding.</span> <button type="button" class="btn-secondary" id="fl-restart">Restart it</button>';
                const again = $('fl-restart');
                if (again) again.onclick = async () => { await turn(false); await turn(true); };
                return;
            }
            waiting = setTimeout(look, 2000);
        };
        waiting = setTimeout(look, 2000);
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
