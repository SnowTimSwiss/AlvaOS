// Demo mode of the Hub: the whole Hub with sample data and no NAS, for trying it out (the AlvaOS
// app's "Try the demo", and what the Google Play review opens). It takes the place of the NAS's
// answers in this page only: nothing is sent anywhere and nothing is kept.
//
// It is on when the page is opened as a file (the app's copy of the Hub) or with "demo" in the
// address (index.html#demo). On a NAS the page is not in demo mode and this file does nothing.
(function () {
    const on = location.protocol === 'file:' || /[#?&]demo(?:[=&]|$)/.test(location.href);
    if (!on) return;
    window.ALVA_DEMO = true;

    // ── Dates relative to today, so the demo never looks old ───────────────
    const pad = (n) => String(n).padStart(2, '0');
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
    const day = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const at = (d, h, m = 0) => `${day(d)}T${pad(h)}:${pad(m)}`;
    const iso = (d) => `${day(d)}T10:00:00+00:00`;
    let nextId = 1000;
    const newId = () => `d${nextId++}`;

    // ── Who and what ───────────────────────────────────────────────────────
    const APPS = [
        { id: 'files', name: 'Files', icon: 'folder' }, { id: 'photos', name: 'Photos', icon: 'image' },
        { id: 'calendar', name: 'Calendar', icon: 'calendar' }, { id: 'contacts', name: 'Contacts', icon: 'contact' },
        { id: 'chat', name: 'Chat', icon: 'message-circle' },
    ];
    const shares = [{ name: 'Demo', access: 'write', limited: true }, { name: 'Family', access: 'read', from: 'sam' }];

    // Files: folders are objects, files are numbers (size in bytes).
    const tree = {
        Demo: {
            Documents: { 'Welcome to AlvaOS.txt': 1200, 'Budget 2026.pdf': 184000, 'Recipes.md': 5200 },
            Holidays: { 'Italy 2025': {}, 'Norway 2024': {} },
            Photos: {},
            Music: { 'Playlist.m3u': 900 },
            'Notes.txt': 800,
        },
        Family: { 'Family tree.pdf': 420000, 'Birthday plan.txt': 600 },
    };
    // Pictures for Photos and the holiday folders: a spread of dates over four years.
    const photos = [];
    {
        let d = addDays(today, -3);
        for (let i = 0; i < 140; i += 1) {
            const video = i % 17 === 5;
            photos.push({ name: `${video ? 'VID' : 'IMG'}_${String(4000 - i).padStart(4, '0')}.${video ? 'mp4' : 'jpg'}`, type: 'file',
                size_bytes: video ? 18000000 : 2400000, modified_at: iso(d), folder: 'Photos', share: 'Demo' });
            d = addDays(d, -(i % 5 === 0 ? 9 : 4 + (i % 3)));
        }
        photos.forEach((p) => { tree.Demo.Photos[p.name] = p.size_bytes; });
        tree.Demo.Holidays['Italy 2025'] = { 'Rome.jpg': 2300000, 'Coast.jpg': 2100000, 'Dinner.jpg': 1900000 };
        tree.Demo.Holidays['Norway 2024'] = { 'Fjord.jpg': 2600000, 'Cabin.jpg': 2200000 };
    }
    const walk = (share, path) => {
        let node = tree[share];
        for (const part of String(path || '').split('/').filter(Boolean)) { node = node && typeof node === 'object' ? node[part] : undefined; }
        return node && typeof node === 'object' ? node : null;
    };
    const entriesOf = (share, path) => {
        const node = walk(share, path);
        if (!node) return null;
        return Object.entries(node).map(([name, v], i) => ({
            name, type: typeof v === 'object' ? 'folder' : 'file', size_bytes: typeof v === 'object' ? 0 : v,
            modified_at: iso(addDays(today, -(3 + i * 7))),
        }));
    };

    // ── Calendar ────────────────────────────────────────────────────────────
    const COLORS = ['#039be5', '#7986cb', '#33b679', '#8e24aa', '#e67c73', '#f6bf26', '#f4511e', '#3f51b5', '#616161', '#0b8043', '#d50000'];
    const calendarPlace = {
        id: 'own', name: 'Demo', own: true, writable: true,
        calendars: [{ id: 'main', name: 'Personal', color: COLORS[0] }, { id: 'family', name: 'Family', color: COLORS[2] }, { id: 'work', name: 'Work', color: COLORS[3] }],
        events: [], tasks: [],
    };
    {
        const ev = (title, d, h, m, mins, calendar, extra = {}) => {
            const end = new Date(d); end.setHours(h, m + mins);
            return { id: newId(), calendar, title, start: at(d, h, m), end: at(end, end.getHours(), end.getMinutes()), all_day: false,
                color: '', location: '', notes: '', repeat: '', until: '', ...extra };
        };
        const mon = addDays(today, -((today.getDay() + 6) % 7));
        calendarPlace.events.push(
            ev('Choir rehearsal', addDays(mon, 1), 20, 0, 90, 'main', { repeat: 'weekly', location: 'Community hall' }),
            ev('Team meeting', addDays(mon, 0), 9, 30, 45, 'work', { repeat: 'weekly' }),
            ev('Dentist', addDays(today, 2), 14, 15, 45, 'main', { location: 'Dr. Meier, Bahnhofstrasse 4' }),
            ev('Lunch with Anna', addDays(today, 1), 12, 15, 75, 'main'),
            ev('Football training', addDays(mon, 3), 18, 0, 90, 'family', { repeat: 'weekly' }),
            ev('Parents evening', addDays(today, 5), 19, 0, 120, 'family'),
            ev('Movie night', addDays(today, 3), 20, 30, 150, 'family', { notes: 'Pick the film together' }),
            ev('Project review', addDays(today, 4), 10, 0, 60, 'work', { location: 'Room 2' }),
        );
        calendarPlace.events.push(
            { id: newId(), calendar: 'family', title: 'Weekend in the mountains', start: day(addDays(today, 8)), end: day(addDays(today, 9)), all_day: true, color: '', location: '', notes: '', repeat: '', until: '' },
        );
        calendarPlace.tasks.push(
            { id: newId(), title: 'Book the train tickets', notes: '', date: day(addDays(today, 2)), time: '', done: false },
            { id: newId(), title: 'Renew the insurance', notes: '', date: day(addDays(today, 6)), time: '09:00', done: false },
            { id: newId(), title: 'Back up the photos', notes: '', date: day(addDays(today, -1)), time: '', done: true },
        );
    }
    // ── Contacts ────────────────────────────────────────────────────────────
    const contacts = [
        { id: newId(), first: 'Anna', last: 'Meier', org: '', title: '', nickname: '', phones: [{ type: 'mobile', value: '+41 79 123 45 67' }], emails: [{ type: 'home', value: 'anna@example.org' }], addresses: [], birthday: `1990-${pad(((today.getMonth() + 1) % 12) + 1)}-12`, url: '', notes: '', favourite: true },
        { id: newId(), first: 'Sam', last: 'Keller', org: 'Keller Garden', title: 'Owner', nickname: '', phones: [{ type: 'work', value: '+41 44 555 01 02' }], emails: [{ type: 'work', value: 'sam@keller-garden.example' }], addresses: [{ type: 'work', value: 'Hauptstrasse 12\n8000 Zurich' }], birthday: `--${pad(today.getMonth() + 1)}-${pad(Math.min(28, today.getDate() + 3))}`, url: 'https://keller-garden.example', notes: '', favourite: false },
        { id: newId(), first: 'Lena', last: 'Brunner', org: '', title: '', nickname: 'Lenchen', phones: [{ type: 'mobile', value: '+41 78 222 33 44' }], emails: [], addresses: [], birthday: '1987-03-09', url: '', notes: 'Choir', favourite: false },
        { id: newId(), first: 'Dr.', last: 'Meier', org: 'Dental practice', title: 'Dentist', phones: [{ type: 'work', value: '+41 44 555 77 88' }], emails: [], addresses: [{ type: 'work', value: 'Bahnhofstrasse 4\n8000 Zurich' }], nickname: '', birthday: '', url: '', notes: '', favourite: false },
        { id: newId(), first: 'Tom', last: 'Weber', org: '', title: '', nickname: '', phones: [{ type: 'home', value: '+41 52 111 22 33' }], emails: [{ type: 'home', value: 'tom@example.org' }], addresses: [], birthday: '', url: '', notes: '', favourite: false },
    ];
    const birthdays = () => {
        const events = [];
        for (const c of contacts) {
            const m = /^(\d{4}-|--)(\d{2})-(\d{2})$/.exec(c.birthday || '');
            if (!m) continue;
            const year = m[1] === '--' ? '2000' : m[1].slice(0, 4);
            const d = `${year}-${m[2]}-${m[3]}`;
            events.push({ id: `birthday-${c.id}`, calendar: 'birthdays', title: `${[c.first, c.last].filter(Boolean).join(' ')}'s birthday`, start: d, end: d, all_day: true,
                color: '', location: '', notes: '', repeat: 'yearly', until: '', born: m[1] === '--' ? null : Number(m[1].slice(0, 4)) });
        }
        return events.length ? { id: 'birthdays', name: 'Birthdays', own: false, writable: false, calendars: [{ id: 'birthdays', name: 'Birthdays', color: COLORS[5] }], events, tasks: [] } : null;
    };

    // ── Photos lists, chat, devices ──────────────────────────────────────────
    const library = { albums: [{ id: 'a1b2c3d4e5f6', name: 'Holidays', items: ['Demo/Photos/IMG_3996.jpg', 'Demo/Photos/IMG_3990.jpg', 'Demo/Photos/IMG_3985.jpg'], created_at: iso(addDays(today, -30)) }],
        favourites: ['Demo/Photos/IMG_4000.jpg'], writable: true };
    const chats = [];
    const chatFiles = {};
    const devices = [{ id: 'dev1', name: 'This phone (demo)', model: 'Demo phone', platform: 'android', app_version: 'demo', created_at: iso(addDays(today, -2)),
        last_seen: iso(today), address: '', user: 'demo', this: false, backup: null }];

    // ── The answers ─────────────────────────────────────────────────────────
    const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    const refused = () => json({ error: 'This is the demo: nothing is saved here. With your own AlvaOS it works.' }, 403);

    function reply(question) {
        const q = question.toLowerCase();
        if (/\bhello|\bhi\b|hallo/.test(q)) return 'Hello! This is the demo of the AlvaOS chat. With your own NAS I answer through the AI service you set up there.';
        if (/photo|picture|bild|foto/.test(q)) return 'Your photos stay on your NAS. The app can back up this phone\'s pictures to it by itself, and Photos shows them by date with a slider.';
        return 'This is a demo answer. In your own AlvaOS the chat uses a model you choose (a local one or a cloud one), and the chats are kept in your personal folder.';
    }

    async function handle(method, url, body) {
        const path = url.pathname.replace(/^\/api\//, '');
        const q = url.searchParams;
        if (path === 'me') return json({ user: 'demo', role: 'user', shares, new_uploads: 0, nas_name: 'Demo NAS', hub: { name: 'AlvaOS Hub', apps: APPS, store: [] }, features: { heif: true } });
        if (path === 'logout') return json({ success: true });
        if (path === 'list') {
            const list = entriesOf(q.get('share'), q.get('path'));
            const info = shares.find((s) => s.name === q.get('share'));
            return list ? json({ share: q.get('share'), access: info ? info.access : 'read', path: q.get('path') || '', entries: list }) : json({ error: 'This folder is not there.' }, 404);
        }
        if (path === 'space') return json({ limit_bytes: 100 * 1024 ** 3, used_bytes: 23 * 1024 ** 3 });
        if (path === 'search') {
            const term = String(q.get('q') || '').toLowerCase();
            const out = [];
            const scan = (share, node, prefix) => {
                for (const [name, v] of Object.entries(node)) {
                    const full = prefix ? `${prefix}/${name}` : name;
                    if (name.toLowerCase().includes(term)) out.push({ name, type: typeof v === 'object' ? 'folder' : 'file', size_bytes: typeof v === 'object' ? 0 : v, modified_at: iso(addDays(today, -5)), folder: prefix, share });
                    if (typeof v === 'object' && out.length < 200) scan(share, v, full);
                }
            };
            for (const s of shares) if (q.get('everywhere') === '1' || q.get('share') === s.name) scan(s.name, tree[s.name], '');
            return json({ path: '', query: term, results: out.slice(0, 200), complete: true });
        }
        if (path === 'photos/sources') return json({ sources: [{ share: 'Demo', path: 'Photos', own: true }] });
        if (path === 'media') return json({ share: 'Demo', path: q.get('path') || 'Photos', results: photos, complete: true, dates_pending: 0 });
        if (path === 'photos/library') return json(library);
        if (path === 'photos/phones') return json({ phones: [], share: 'Demo', folder: 'Photos' });
        if (path === 'photos/favourites') {
            const { add, remove } = body || {};
            for (const r of add || []) if (!library.favourites.includes(r)) library.favourites.push(r);
            library.favourites = library.favourites.filter((r) => !(remove || []).includes(r));
            return json({ success: true, favourites: library.favourites });
        }
        if (path === 'photos/albums' && method === 'POST') {
            const album = { id: Array.from({ length: 12 }, () => '0123456789abcdef'[Math.floor(Math.random() * 16)]).join(''), name: String((body || {}).name || 'Album').slice(0, 60), items: (body || {}).items || [], created_at: iso(today) };
            library.albums.push(album);
            return json({ success: true, album });
        }
        if (path.startsWith('photos/albums/')) {
            const id = path.split('/')[2];
            const album = library.albums.find((a) => a.id === id);
            if (method === 'DELETE') library.albums = library.albums.filter((a) => a.id !== id);
            else if (album) {
                const b = body || {};
                if (b.name) album.name = String(b.name).slice(0, 60);
                for (const r of b.add || []) if (!album.items.includes(r)) album.items.push(r);
                album.items = album.items.filter((r) => !(b.remove || []).includes(r));
            }
            return json({ success: true, album });
        }
        if (path === 'calendar' && method === 'GET') {
            const places = [calendarPlace];
            const b = birthdays();
            if (b) places.push(b);
            return json({ places, has_own: true, problems: [], colors: COLORS, today: day(today) });
        }
        if (path === 'calendar/item') {
            const { kind, item } = body || {};
            const list = kind === 'event' ? calendarPlace.events : kind === 'task' ? calendarPlace.tasks : calendarPlace.calendars;
            const saved = { ...item, id: item && item.id ? item.id : newId() };
            if (kind === 'event') Object.assign(saved, { color: saved.color || '', repeat: saved.repeat || '', until: saved.until || '', location: saved.location || '', notes: saved.notes || '' });
            const i = list.findIndex((x) => x.id === saved.id);
            if (i >= 0) list[i] = saved; else list.push(saved);
            return json({ success: true, item: saved, calendars: calendarPlace.calendars });
        }
        if (path === 'calendar/delete') {
            const { kind, id } = body || {};
            if (kind === 'event') calendarPlace.events = calendarPlace.events.filter((x) => x.id !== id);
            else if (kind === 'task') calendarPlace.tasks = calendarPlace.tasks.filter((x) => x.id !== id);
            else { calendarPlace.calendars = calendarPlace.calendars.filter((x) => x.id !== id); calendarPlace.events = calendarPlace.events.filter((x) => x.calendar !== id); }
            return json({ success: true, calendars: calendarPlace.calendars });
        }
        if (path === 'contacts' && method === 'GET') return json({ contacts, has_own: true, writable: true });
        if (path === 'contacts/item') {
            const item = { ...(body || {}).item };
            item.id = item.id || newId();
            for (const k of ['phones', 'emails', 'addresses']) item[k] = item[k] || [];
            const i = contacts.findIndex((c) => c.id === item.id);
            if (i >= 0) contacts[i] = item; else contacts.push(item);
            return json({ success: true, result: item });
        }
        if (path === 'contacts/delete') {
            const ids = (body || {}).ids || [];
            for (let i = contacts.length - 1; i >= 0; i -= 1) if (ids.includes(contacts[i].id)) contacts.splice(i, 1);
            return json({ success: true, result: { deleted: ids.length } });
        }
        if (path === 'contacts/import') return json({ success: true, result: { added: 0, updated: 0, skipped: 0 } });
        if (path === 'chat' && method === 'GET') return json({ models: ['demo-model'], chats, problem: '' });
        if (path === 'chat/send') {
            const b = body || {};
            let id = b.chat;
            const now = iso(today);
            const text = reply(String(b.message || ''));
            if (!id) {
                id = newId();
                const title = String(b.message || 'New chat').slice(0, 60);
                chats.unshift({ id, title, created: now, updated: now, model: 'demo-model' });
                chatFiles[id] = { id, title, created: now, updated: now, model: 'demo-model', messages: [] };
            }
            const file = chatFiles[id];
            file.messages.push({ role: 'user', content: String(b.message || '') }, { role: 'assistant', content: text });
            const enc = new TextEncoder();
            const parts = text.match(/\S+\s*/g) || [text];
            const stream = new ReadableStream({
                async start(controller) {
                    controller.enqueue(enc.encode(`${JSON.stringify({ chat: { id, title: file.title } })}\n`));
                    for (const p of parts) { await new Promise((r) => setTimeout(r, 35)); controller.enqueue(enc.encode(`${JSON.stringify({ t: p })}\n`)); }
                    controller.enqueue(enc.encode(`${JSON.stringify({ done: true })}\n`));
                    controller.close();
                },
            });
            return new Response(stream, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } });
        }
        if (path.startsWith('chat/')) {
            const [, id, action] = path.split('/');
            if (action === 'delete') { const i = chats.findIndex((c) => c.id === id); if (i >= 0) chats.splice(i, 1); return json({ success: true }); }
            if (action === 'rename') { const c = chats.find((x) => x.id === id); if (c) c.title = String((body || {}).title || c.title).slice(0, 60); return json({ success: true }); }
            return chatFiles[id] ? json(chatFiles[id]) : json({ error: 'This chat is not there.' }, 404);
        }
        if (path === 'devices' && method === 'GET') return json({ devices });
        if (path === 'devices/pair-code') return json({ code: 'DEMO0000', link: 'alvaos://pair?c=DEMO0000', qr: '', addresses: [], expires_in: 600, away: true });
        if (path === 'links') return json({ links: [] });
        if (path === 'trash') return json({ success: true, items: [], keep_days: 30 });
        if (path === 'people') return json({ people: ['sam', 'anna'] });
        if (path === 'grants') return json({ grants: [] });
        if (path === 'mkdir' && method === 'POST') {
            const { share, path: base, name } = body || {};
            const node = walk(share, base);
            if (node && name) node[name] = {};
            return json({ success: true });
        }
        if (path === 'rename' && method === 'POST') {
            const { share, path: from, name } = body || {};
            const parts = String(from || '').split('/');
            const old = parts.pop();
            const node = walk(share, parts.join('/'));
            if (node && old in node && name) { node[name] = node[old]; delete node[old]; }
            return json({ success: true });
        }
        if (path === 'delete' && method === 'POST') {
            const { share, items } = body || {};
            for (const it of items || []) {
                const parts = String(it.path || it).split('/');
                const old = parts.pop();
                const node = walk(share, parts.join('/'));
                if (node) delete node[old];
            }
            return json({ success: true });
        }
        if (path.startsWith('upload') || path === 'zip' || path === 'link' || path.startsWith('versions') || path.startsWith('trash/') || path === 'video/convert') return refused();
        return json({ success: true });
    }

    const realFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
        const raw = typeof input === 'string' ? input : input.url;
        let url;
        try { url = new URL(raw, location.href); } catch (_e) { return realFetch(input, init); }
        if (!url.pathname.startsWith('/api/')) return realFetch(input, init);
        const method = String(init.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
        let body = null;
        if (init.body) { try { body = JSON.parse(init.body); } catch (_e) { body = null; } }
        try { return await handle(method, url, body); } catch (err) { return json({ error: String(err && err.message || err) }, 500); }
    };

    // ── Pictures: drawn here, so no file is needed ───────────────────────────
    function hash(text) { let h = 2166136261; for (let i = 0; i < text.length; i += 1) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
    function picture(path) {
        const h = hash(path);
        const hue = h % 360;
        const hue2 = (hue + 40 + (h >> 8) % 60) % 360;
        const sun = 20 + (h >> 4) % 60;
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300" preserveAspectRatio="xMidYMid slice">`
            + `<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${hue},70%,62%)"/><stop offset="1" stop-color="hsl(${hue2},65%,38%)"/></linearGradient></defs>`
            + `<rect width="400" height="300" fill="url(#g)"/><circle cx="${60 + sun * 4}" cy="${70 + sun}" r="${22 + sun % 18}" fill="rgba(255,255,255,.75)"/>`
            + `<path d="M0 300 L0 ${210 - (h >> 12) % 40} L${90 + (h >> 6) % 60} ${150 + (h >> 14) % 40} L${210 + (h >> 3) % 40} ${215 - (h >> 10) % 30} L${310} ${140 + (h >> 16) % 50} L400 ${200 - (h >> 5) % 40} L400 300 Z" fill="rgba(10,20,30,.55)"/></svg>`;
        return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
    }
    const fix = (img) => {
        const src = img.getAttribute('src') || '';
        const m = /[/]api[/](?:thumb|preview)\?([^"']*)/.exec(src);
        if (!m) return;
        const q = new URLSearchParams(m[1]);
        img.setAttribute('src', picture(`${q.get('share')}/${q.get('path')}`));
    };
    new MutationObserver((list) => {
        for (const rec of list) {
            if (rec.type === 'attributes') { if (rec.target.tagName === 'IMG') fix(rec.target); continue; }
            rec.addedNodes.forEach((n) => { if (n.nodeType === 1) { if (n.tagName === 'IMG') fix(n); n.querySelectorAll && n.querySelectorAll('img').forEach(fix); } });
        }
    }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['src'] });

    // ── A line that says it is the demo ──────────────────────────────────────
    document.addEventListener('DOMContentLoaded', () => {
        const tag = document.createElement('div');
        tag.className = 'demo-tag';
        tag.textContent = 'Demo · sample data';
        document.body.appendChild(tag);
    });
}());
