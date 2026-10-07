// Contacts in the AlvaOS Hub: a person's address book. A list with a search
// on the left, the open contact on the right (a page of its own on a phone).
// The contacts are one file in the person's folder (backend/hub_contacts.py);
// phones and computers sync them over CardDAV (backend/hub_carddav.py).
(function () {
    'use strict';
    const H = window.Hub;
    if (!H) return;
    const { esc, icon, api, toast } = H;
    H.addIcons({
        contact: '<path d="M16 2v2M7 22v-2a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v2M8 2v2"/><circle cx="12" cy="11" r="3"/><rect width="18" height="18" x="3" y="4" rx="2"/>',
        mail: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
        'map-pin': '<path d="M20 10c0 4.99-5.54 10.19-7.4 11.8a1 1 0 0 1-1.2 0C9.54 20.19 4 14.99 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
        cake: '<path d="M20 21v-8a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8M4 16s.5-1 2-1 2.5 2 4 2 2.5-2 4-2 2.5 2 4 2 2-1 2-1M2 21h20M7 8v3M12 8v3M17 8v3M7 4h.01M12 4h.01M17 4h.01"/>',
        trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
        'chev-left': '<path d="m15 18-6-6 6-6"/>',
        'user-pen': '<path d="M11.5 15H7a4 4 0 0 0-4 4v2M21.378 16.626a1 1 0 0 0-3.004-3.004l-4.01 4.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z"/><circle cx="10" cy="7" r="4"/>',
        notes: '<path d="M15 12h-5M15 8h-5M19 17V5a2 2 0 0 0-2-2H4"/><path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"/>',
        globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20M2 12h20"/>',
        briefcase: '<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" y="6" rx="2"/>',
    });

    const PHONE_KINDS = [['mobile', 'Mobile'], ['home', 'Home'], ['work', 'Work'], ['other', 'Other']];
    const MAIL_KINDS = [['home', 'Home'], ['work', 'Work'], ['other', 'Other']];
    const COLORS = ['#039be5', '#7986cb', '#33b679', '#8e24aa', '#e67c73', '#f4511e', '#3f51b5', '#0b8043', '#616161'];

    let root = null;
    let built = false;
    let loaded = false;
    let items = [];
    let hasOwn = true;
    let writable = true;
    let problem = '';
    let query = '';
    let openId = '';
    let draft = null;        // the contact being edited (a copy), or null
    let confirmDelete = false;
    const $c = (sel) => root.querySelector(sel);

    const nameOf = (c) => [c.first, c.last].filter(Boolean).join(' ') || c.org || (c.emails[0] || {}).value || (c.phones[0] || {}).value || 'No name';
    const sortKey = (c) => (c.last || c.first || c.org || nameOf(c)).toLowerCase() + '\u0000' + (c.first || '').toLowerCase();
    const initial = (c) => (nameOf(c).trim()[0] || '?').toUpperCase();
    const colorOf = (c) => { let h = 0; for (const ch of nameOf(c)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return COLORS[h % COLORS.length]; };
    const avatar = (c, big) => `<span class="ct-avatar${big ? ' big' : ''}" style="background:${colorOf(c)}" aria-hidden="true">${esc(initial(c))}</span>`;
    const label = (kinds, type) => (kinds.find((k) => k[0] === type) || [type, 'Other'])[1];

    function birthdayText(b) {
        const m = /^(\d{4}-|--)(\d{2})-(\d{2})$/.exec(b || '');
        if (!m) return '';
        const d = new Date(m[1] === '--' ? 2000 : +m[1].slice(0, 4), +m[2] - 1, +m[3]);
        return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: m[1] === '--' ? undefined : 'numeric' });
    }

    // ── Loading ────────────────────────────────────────────────────────────
    async function refresh() {
        try {
            const data = await api('contacts');
            items = data.contacts || [];
            hasOwn = data.has_own !== false;
            writable = data.writable !== false;
            problem = '';
        } catch (err) {
            problem = err.message;
        }
        loaded = true;
        if (!items.some((c) => c.id === openId)) openId = '';
        render();
    }

    // ── Drawing ────────────────────────────────────────────────────────────
    function build() {
        root.classList.add('contacts');
        root.innerHTML = `
            <aside class="ct-side" id="ct-side">
                <div class="ct-top">
                    <div class="ct-search">${icon('search')}<input id="ct-q" type="search" placeholder="Search contacts" aria-label="Search contacts" autocomplete="off"></div>
                    <button type="button" class="icon-btn" id="ct-more" aria-label="More" title="More" aria-haspopup="menu" aria-expanded="false">${icon('more')}</button>
                    <div class="ct-menu" id="ct-menu" hidden>
                        <button type="button" data-do="import">${icon('upload')}Import a vCard file…</button>
                        <button type="button" data-do="export">${icon('download')}Export all as vCard</button>
                        <button type="button" data-do="sync">${icon('phone')}Sync with your phone…</button>
                    </div>
                </div>
                <button type="button" class="ct-new" id="ct-new">${icon('plus')}<span>New contact</span></button>
                <nav class="ct-list" id="ct-list" aria-label="Contacts"></nav>
                ${H.foot()}
            </aside>
            <section class="ct-main" id="ct-main"></section>
            <input type="file" id="ct-file" accept=".vcf,text/vcard,text/x-vcard" hidden>`;
        $c('#ct-q').addEventListener('input', (e) => { query = e.target.value.trim().toLowerCase(); renderList(); });
        $c('#ct-new').addEventListener('click', () => startEdit(null));
        $c('#ct-list').addEventListener('click', (e) => {
            const b = e.target.closest('[data-id]');
            if (b) show(b.dataset.id);
        });
        $c('#ct-more').addEventListener('click', (e) => { e.stopPropagation(); const m = $c('#ct-menu'); m.hidden = !m.hidden; $c('#ct-more').setAttribute('aria-expanded', String(!m.hidden)); });
        document.addEventListener('click', () => { if (root && $c('#ct-menu')) { $c('#ct-menu').hidden = true; $c('#ct-more').setAttribute('aria-expanded', 'false'); } });
        $c('#ct-menu').addEventListener('click', (e) => {
            const b = e.target.closest('[data-do]');
            if (!b) return;
            if (b.dataset.do === 'import') $c('#ct-file').click();
            if (b.dataset.do === 'export') window.location.href = '/api/contacts/export';
            if (b.dataset.do === 'sync') syncHelp();
        });
        $c('#ct-file').addEventListener('change', importFile);
        $c('#ct-main').addEventListener('click', onMainClick);
        $c('#ct-main').addEventListener('submit', (e) => { e.preventDefault(); save(); });
        built = true;
    }

    function render() {
        if (!built) return;
        root.classList.toggle('open', !!openId || !!draft);
        $c('#ct-new').hidden = !writable || !hasOwn;
        renderList();
        renderMain();
    }

    function renderList() {
        const list = $c('#ct-list');
        if (problem) { list.innerHTML = `<div class="ct-empty-list">${esc(problem)}</div>`; return; }
        if (!loaded) { list.innerHTML = '<div class="ct-empty-list">Loading…</div>'; return; }
        const shown = items.filter((c) => !query || [nameOf(c), c.org, c.nickname, ...c.phones.map((p) => p.value.replace(/\s/g, '')), ...c.emails.map((e) => e.value)]
            .join(' ').toLowerCase().includes(query.replace(/\s/g, '') && /^[+\d\s()-]+$/.test(query) ? query.replace(/\s/g, '') : query))
            .sort((a, b) => sortKey(a).localeCompare(sortKey(b), undefined, { numeric: true }));
        if (!shown.length) {
            list.innerHTML = `<div class="ct-empty-list">${items.length ? 'Nobody matches.' : 'No contacts yet.'}</div>`;
            return;
        }
        let letter = '';
        const favs = shown.filter((c) => c.favourite);
        const row = (c) => `<button type="button" class="ct-row${c.id === openId ? ' active' : ''}" data-id="${esc(c.id)}">${avatar(c)}<span class="ct-row-text"><strong>${esc(nameOf(c))}</strong>${c.org && (c.first || c.last) ? `<small>${esc(c.org)}</small>` : ''}</span></button>`;
        let html = favs.length && !query ? `<div class="ct-letter">Favourites</div>${favs.map(row).join('')}` : '';
        for (const c of shown) {
            const l = initial({ ...c, first: '', last: '', org: sortKey(c)[0] || '#' });
            const head = /[A-Z]/.test(l) ? l : '#';
            if (head !== letter) { letter = head; html += `<div class="ct-letter">${esc(head)}</div>`; }
            html += row(c);
        }
        list.innerHTML = html;
    }

    function rows(c) {
        const out = [];
        c.phones.forEach((p) => out.push(`<div class="ct-line">${icon('phone')}<div><a href="tel:${esc(p.value.replace(/[^\d+]/g, ''))}">${esc(p.value)}</a><small>${label(PHONE_KINDS, p.type)}</small></div></div>`));
        c.emails.forEach((e) => out.push(`<div class="ct-line">${icon('mail')}<div><a href="mailto:${esc(e.value)}">${esc(e.value)}</a><small>${label(MAIL_KINDS, e.type)}</small></div></div>`));
        c.addresses.forEach((a) => out.push(`<div class="ct-line">${icon('map-pin')}<div class="ct-multi">${esc(a.value)}<small>${label(MAIL_KINDS, a.type)}</small></div></div>`));
        if (c.birthday) out.push(`<div class="ct-line">${icon('cake')}<div>${esc(birthdayText(c.birthday))}<small>Birthday</small></div></div>`);
        if (c.url) out.push(`<div class="ct-line">${icon('globe')}<div>${/^https?:\/\//i.test(c.url) ? `<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.url)}</a>` : esc(c.url)}<small>Website</small></div></div>`);
        if (c.notes) out.push(`<div class="ct-line">${icon('notes')}<div class="ct-multi">${esc(c.notes)}<small>Notes</small></div></div>`);
        return out.join('');
    }

    function renderMain() {
        const main = $c('#ct-main');
        if (draft) { main.innerHTML = editForm(draft); const first = main.querySelector('[name=first]'); if (first && !first.value) first.focus(); return; }
        const c = items.find((x) => x.id === openId);
        if (!c) {
            const msg = !hasOwn ? 'You have no place for contacts yet. Ask whoever runs this NAS for a personal folder.'
                : !items.length && loaded && !problem ? 'No contacts yet. Add one, or import a vCard file from the menu. Your phone can keep them in sync too: menu › Sync with your phone.'
                    : 'Choose a contact.';
            main.innerHTML = `<div class="ct-empty">${icon('contact')}<p>${esc(msg)}</p></div>`;
            return;
        }
        main.innerHTML = `
            <header class="ct-bar"><button type="button" class="icon-btn ct-back" data-act="back" aria-label="Back to the list">${icon('chev-left')}</button><span class="ct-grow"></span>
                ${writable ? `<button type="button" class="icon-btn${c.favourite ? ' on' : ''}" data-act="fav" aria-pressed="${!!c.favourite}" aria-label="Favourite" title="Favourite">${icon(c.favourite ? 'heart-fill' : 'heart')}</button>
                <button type="button" class="btn" data-act="edit">${icon('pen')}<span>Edit</span></button>
                <button type="button" class="btn${confirmDelete ? ' danger' : ''}" data-act="delete">${icon('trash')}<span>${confirmDelete ? 'Really delete?' : 'Delete'}</span></button>` : ''}
            </header>
            <div class="ct-card">
                <div class="ct-head">${avatar(c, true)}<div><h2>${esc(nameOf(c))}</h2>${c.nickname ? `<div class="ct-sub">“${esc(c.nickname)}”</div>` : ''}${[c.title, c.org].filter(Boolean).length ? `<div class="ct-sub">${esc([c.title, c.org].filter(Boolean).join(' · '))}</div>` : ''}</div></div>
                <div class="ct-lines">${rows(c) || '<p class="ct-sub">Nothing else saved.</p>'}</div>
            </div>`;
    }

    // ── Editing ────────────────────────────────────────────────────────────
    function kindSelect(name, kinds, value) {
        return `<select name="${name}-type" aria-label="Kind">${kinds.map(([v, l]) => `<option value="${v}"${v === value ? ' selected' : ''}>${l}</option>`).join('')}</select>`;
    }
    function listField(key, title, kinds, entries, multiline, placeholder) {
        const list = entries.length ? entries : [{ type: kinds[0][0], value: '' }];
        return `<fieldset class="ct-set" data-list="${key}"><legend>${title}</legend>
            ${list.map((e) => `<div class="ct-entry">${kindSelect(key, kinds, e.type)}${multiline
                ? `<textarea name="${key}-value" rows="2" placeholder="${placeholder}" maxlength="300">${esc(e.value)}</textarea>`
                : `<input name="${key}-value" value="${esc(e.value)}" placeholder="${placeholder}" maxlength="160" autocomplete="off">`}
                <button type="button" class="icon-btn" data-act="drop" aria-label="Remove">${icon('x')}</button></div>`).join('')}
            <button type="button" class="link" data-act="add" data-key="${key}">${icon('plus')} Add</button></fieldset>`;
    }
    function editForm(c) {
        return `<form class="ct-form" novalidate>
            <header class="ct-bar"><button type="button" class="icon-btn ct-back" data-act="cancel" aria-label="Cancel">${icon('chev-left')}</button>
                <h2 class="ct-title">${c.id ? 'Edit contact' : 'New contact'}</h2><span class="ct-grow"></span>
                <button type="button" class="btn" data-act="cancel">Cancel</button><button type="submit" class="btn primary" id="ct-save">Save</button></header>
            <div class="ct-card">
                <div class="ct-grid">
                    <label>First name<input name="first" value="${esc(c.first)}" maxlength="80" autocomplete="off"></label>
                    <label>Last name<input name="last" value="${esc(c.last)}" maxlength="80" autocomplete="off"></label>
                    <label>Company<input name="org" value="${esc(c.org)}" maxlength="120" autocomplete="off"></label>
                    <label>Job title<input name="title" value="${esc(c.title)}" maxlength="120" autocomplete="off"></label>
                    <label>Nickname<input name="nickname" value="${esc(c.nickname)}" maxlength="80" autocomplete="off"></label>
                    <label>Birthday<input name="birthday" value="${esc(c.birthday)}" placeholder="1985-03-09 or --03-09" maxlength="10" autocomplete="off"></label>
                </div>
                ${listField('phones', 'Phone numbers', PHONE_KINDS, c.phones, false, 'Number')}
                ${listField('emails', 'Email addresses', MAIL_KINDS, c.emails, false, 'name@example.org')}
                ${listField('addresses', 'Addresses', MAIL_KINDS, c.addresses, true, 'Street\nCity\nCountry')}
                <div class="ct-grid one"><label>Website<input name="url" value="${esc(c.url)}" maxlength="300" autocomplete="off"></label>
                    <label>Notes<textarea name="notes" rows="3" maxlength="4000">${esc(c.notes)}</textarea></label></div>
                <p class="ct-error" id="ct-error" role="alert"></p>
            </div></form>`;
    }
    const blank = () => ({ id: '', first: '', last: '', org: '', title: '', nickname: '', phones: [], emails: [], addresses: [], birthday: '', url: '', notes: '', favourite: false });
    function startEdit(c) {
        confirmDelete = false;
        draft = JSON.parse(JSON.stringify(c || blank()));
        if (!c) openId = '';
        render();
    }
    function readForm() {
        const form = $c('.ct-form');
        const val = (n) => form.querySelector(`[name=${n}]`).value.trim();
        const list = (key) => [...form.querySelectorAll(`[data-list=${key}] .ct-entry`)].map((row) => ({
            type: row.querySelector('select').value, value: row.querySelector('input, textarea').value.trim() })).filter((e) => e.value);
        return { ...draft, first: val('first'), last: val('last'), org: val('org'), title: val('title'), nickname: val('nickname'), birthday: val('birthday'),
            url: val('url'), notes: val('notes'), phones: list('phones'), emails: list('emails'), addresses: list('addresses') };
    }
    async function save() {
        if (!draft) return;
        const item = readForm();
        const btn = $c('#ct-save');
        btn.disabled = true;
        try {
            const res = await api('contacts/item', { method: 'POST', json: { item } });
            const saved = res.result;
            const at = items.findIndex((c) => c.id === saved.id);
            if (at >= 0) items[at] = saved; else items.push(saved);
            draft = null;
            openId = saved.id;
            render();
        } catch (err) {
            btn.disabled = false;
            const box = $c('#ct-error');
            if (box) box.textContent = err.message;
        }
    }

    async function onMainClick(e) {
        const b = e.target.closest('[data-act]');
        if (!b) return;
        const act = b.dataset.act;
        const c = items.find((x) => x.id === openId);
        if (act === 'back') { openId = ''; confirmDelete = false; render(); }
        else if (act === 'cancel') { draft = null; render(); }
        else if (act === 'edit' && c) startEdit(c);
        else if (act === 'fav' && c) {
            try { const res = await api('contacts/item', { method: 'POST', json: { item: { ...c, favourite: !c.favourite } } }); Object.assign(c, res.result); render(); } catch (err) { toast(err.message, 'error'); }
        } else if (act === 'delete' && c) {
            if (!confirmDelete) { confirmDelete = true; render(); setTimeout(() => { if (confirmDelete) { confirmDelete = false; if (built) renderMain(); } }, 4000); return; }
            confirmDelete = false;
            try { await api('contacts/delete', { method: 'POST', json: { ids: [c.id] } }); items = items.filter((x) => x.id !== c.id); openId = ''; render(); toast('Contact deleted.'); } catch (err) { toast(err.message, 'error'); }
        } else if (act === 'add') {
            draft = readForm();
            draft[b.dataset.key].push({ type: b.dataset.key === 'phones' ? 'mobile' : 'home', value: '' });
            const keep = b.dataset.key;
            renderMain();
            const rowsEl = $c(`[data-list=${keep}] .ct-entry:last-of-type`);
            if (rowsEl) rowsEl.querySelector('input, textarea').focus();
        } else if (act === 'drop') {
            const set = b.closest('[data-list]');
            draft = readForm();
            const list = draft[set.dataset.list];
            const index = [...set.querySelectorAll('.ct-entry')].indexOf(b.closest('.ct-entry'));
            list.splice(index, 1);
            renderMain();
        }
    }

    function show(id) {
        draft = null;
        confirmDelete = false;
        openId = id;
        render();
        $c('#ct-main').scrollTop = 0;
    }

    // ── Import, and syncing with a phone ───────────────────────────────────
    async function importFile(e) {
        const file = e.target.files[0];
        e.target.value = '';
        if (!file) return;
        try {
            const text = await file.text();
            const res = (await api('contacts/import', { method: 'POST', json: { text } })).result;
            toast(`${res.added} added${res.skipped ? `, ${res.skipped} were there already` : ''}.`);
            await refresh();
        } catch (err) { toast(err.message, 'error'); }
    }
    function syncHelp() {
        const wrap = document.getElementById('dialog');
        const host = location.origin;
        wrap.hidden = false;
        wrap.innerHTML = `<div class="dialog wide"><h2>Sync with your phone</h2>
            <p>Your phone or computer can keep these contacts (and your calendar) up to date by itself. Use your AlvaOS name and password.</p>
            <p><strong>Server address</strong><br><code>${esc(host)}</code></p>
            <p><strong>iPhone</strong>: Settings › Contacts › Accounts › Add Account › Other › Add CardDAV Account. Server: the address above.<br>
            <strong>Android</strong>: install DAVx5, add an account with a URL and your name, base URL <code>${esc(host)}/dav/</code>.<br>
            <strong>Thunderbird, Mac</strong>: add a CardDAV address book with <code>${esc(host)}/dav/</code>.</p>
            <p class="muted">Away from home this needs the internet address of your NAS (Settings › Remote access). Photos and groups of a phone's contacts are not kept.</p>
            <div class="actions"><button type="button" class="btn primary" data-close>Done</button></div></div>`;
        wrap.querySelector('[data-close]').onclick = () => { wrap.hidden = true; wrap.innerHTML = ''; };
    }

    window.addEventListener('hub-signout', () => { items = []; loaded = false; openId = ''; draft = null; query = ''; if (built && $c('#ct-q')) $c('#ct-q').value = ''; });

    window.HubApps = window.HubApps || {};
    window.HubApps.contacts = {
        show(el) {
            root = el;
            if (!built) build();
            render();
            refresh();
        },
    };
})();
