// Calendar in the AlvaOS Hub, like Google Calendar: day, week, month and
// schedule, calendars with colours, tasks, repeating events. Click or drag
// in the grid to make an event, drag it to move it, pull its lower edge to
// make it longer. The data is files in the person's personal folder and in
// shared folders marked as family calendars (backend/hub_calendar.py).
(function () {
    'use strict';
    const H = window.Hub;
    if (!H) return;
    const { esc, icon, api, toast } = H;
    H.addIcons({
        'chev-left': '<path d="m15 18-6-6 6-6"/>',
        'chev-right': '<path d="m9 18 6-6-6-6"/>',
        'chev-down': '<path d="m6 9 6 6 6-6"/>',
        check: '<path d="M20 6 9 17l-5-5"/>',
        'check-circle': '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
        circle: '<circle cx="12" cy="12" r="10"/>',
        'map-pin': '<path d="M20 10c0 4.99-5.54 10.19-7.4 11.8a1 1 0 0 1-1.2 0C9.54 20.19 4 14.99 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
        notes: '<path d="M15 12h-5M15 8h-5M19 17V5a2 2 0 0 0-2-2H4"/><path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"/>',
        repeat: '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
        tasks: '<path d="M13 5h8M13 12h8M13 19h8"/><path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/>',
        palette: '<circle cx="13.5" cy="6.5" r=".5" fill="currentColor"/><circle cx="17.5" cy="10.5" r=".5" fill="currentColor"/><circle cx="8.5" cy="7.5" r=".5" fill="currentColor"/><circle cx="6.5" cy="12.5" r=".5" fill="currentColor"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.93 0 1.65-.75 1.65-1.69 0-.44-.18-.84-.44-1.13-.29-.29-.44-.65-.44-1.13a1.64 1.64 0 0 1 1.67-1.67h2c3.05 0 5.55-2.5 5.55-5.55C21.97 6.01 17.46 2 12 2z"/>',
        phone: '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>',
        users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    });

    // ── Dates ──────────────────────────────────────────────────────────────
    // Times are local times without a zone ("2026-10-05T09:30"); whole days
    // are dates ("2026-10-05", the last day included).
    const pad = (n) => String(n).padStart(2, '0');
    const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const stamp = (d) => `${ymd(d)}T${hm(d)}`;
    const day0 = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, d.getHours(), d.getMinutes());
    const addMin = (d, n) => new Date(d.getTime() + n * 60000);
    const sameDay = (a, b) => ymd(a) === ymd(b);
    const weekStart = (d) => addDays(day0(d), -((d.getDay() + 6) % 7));   // Monday
    const daysBetween = (a, b) => Math.round((day0(b) - day0(a)) / 86400000);
    function parse(value) {
        const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?$/.exec(value || '');
        return m ? new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)) : null;
    }
    const fmt = (d, o) => d.toLocaleDateString('en-GB', o);
    const longDay = (d) => fmt(d, { weekday: 'long', day: 'numeric', month: 'long' });
    const sameYear = (d) => d.getFullYear() === new Date().getFullYear();

    function whenText(start, end, allDay) {
        if (allDay) {
            const last = addDays(end, -1);
            return sameDay(start, last) ? longDay(start) : `${fmt(start, { day: 'numeric', month: 'long' })} – ${fmt(last, { day: 'numeric', month: 'long', year: sameYear(last) ? undefined : 'numeric' })}`;
        }
        if (sameDay(start, end) || (hm(end) === '00:00' && sameDay(start, addMin(end, -1)))) return `${longDay(start)} · ${hm(start)} – ${hm(end)}`;
        return `${fmt(start, { day: 'numeric', month: 'short' })}, ${hm(start)} – ${fmt(end, { day: 'numeric', month: 'short' })}, ${hm(end)}`;
    }

    // ── State ──────────────────────────────────────────────────────────────
    const store = {
        get(k, d) { try { const v = localStorage.getItem(`alvaos_cal_${k}`); return v === null ? d : JSON.parse(v); } catch (_e) { return d; } },
        set(k, v) { try { localStorage.setItem(`alvaos_cal_${k}`, JSON.stringify(v)); } catch (_e) { /* off */ } },
    };
    const phone = window.matchMedia('(max-width: 760px)');
    let root = null;
    let built = false;
    let places = [];
    let colors = [];
    let hasOwn = true;
    let loaded = false;
    let view = store.get('view', phone.matches ? 'schedule' : 'week');
    let cursor = day0(new Date());
    let miniMonth = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const hidden = new Set(store.get('hidden', []));
    let tasksOpen = store.get('tasks', false);
    let addingTask = false;
    let pop = null;
    const HOUR = 48;   // pixels per hour in day and week

    const $c = (sel) => root.querySelector(sel);
    const placeOf = (id) => places.find((p) => p.id === id);
    const writable = () => places.filter((p) => p.writable);
    const calOf = (place, id) => place.calendars.find((c) => c.id === id) || place.calendars[0];
    const calKey = (place, cal) => `${place.id}|${cal.id}`;
    const TASK_COLOR = '#1a73e8';

    // ── Repeats ────────────────────────────────────────────────────────────
    const REPEATS = [['', 'Does not repeat'], ['daily', 'Every day'], ['weekdays', 'Every weekday (Monday to Friday)'],
        ['weekly', 'Every week'], ['monthly', 'Every month'], ['yearly', 'Every year']];
    function repeatText(ev) {
        const s = parse(ev.start);
        const base = {
            daily: 'Every day', weekdays: 'Every weekday', weekly: `Every week on ${fmt(s, { weekday: 'long' })}`,
            monthly: `Every month on day ${s.getDate()}`, yearly: `Every year on ${fmt(s, { day: 'numeric', month: 'long' })}`,
        }[ev.repeat] || '';
        return base && ev.until ? `${base}, until ${fmt(parse(ev.until), { day: 'numeric', month: 'long', year: 'numeric' })}` : base;
    }

    // The times an event happens that touch [from, to).
    function occurrences(ev, from, to) {
        const s = parse(ev.start);
        let e = parse(ev.end);
        if (!s || !e) return [];
        if (ev.all_day) e = addDays(e, 1);
        const length = e - s;
        const out = [];
        const until = ev.until ? addDays(parse(ev.until), 1) : null;
        const push = (start) => {
            if (until && start >= until) return false;
            if (start >= to) return false;
            const end = new Date(start.getTime() + length);
            if (end > from) out.push([start, end]);
            return true;
        };
        if (!ev.repeat) { push(s); return out; }
        if (ev.repeat === 'daily' || ev.repeat === 'weekly') {
            const step = ev.repeat === 'daily' ? 1 : 7;
            let k = Math.max(0, Math.floor((daysBetween(s, from) - Math.ceil(length / 86400000)) / step) - 1);
            for (let n = 0; n < 1000; n += 1, k += 1) if (!push(addDays(s, k * step))) break;
        } else if (ev.repeat === 'weekdays') {
            let d = new Date(Math.max(s, addDays(from, -Math.ceil(length / 86400000) - 1)));
            d = new Date(d.getFullYear(), d.getMonth(), d.getDate(), s.getHours(), s.getMinutes());
            if (d < s) d = new Date(s);
            for (let n = 0; n < 1000; n += 1, d = addDays(d, 1)) {
                if (d.getDay() === 0 || d.getDay() === 6) continue;
                if (!push(d)) break;
            }
        } else {
            const months = ev.repeat === 'monthly' ? 1 : 12;
            let k = Math.max(0, Math.floor(((from.getFullYear() - s.getFullYear()) * 12 + from.getMonth() - s.getMonth()) / months) - 2);
            for (let n = 0; n < 400; n += 1, k += 1) {
                const d = new Date(s.getFullYear(), s.getMonth() + k * months, s.getDate(), s.getHours(), s.getMinutes());
                if (d.getDate() !== s.getDate()) continue;   // no 31 February
                if (!push(d)) break;
            }
        }
        return out;
    }

    // Everything shown between from and to: events and tasks with a day.
    function instances(from, to) {
        const out = [];
        places.forEach((place) => {
            place.events.forEach((ev) => {
                const cal = calOf(place, ev.calendar);
                if (hidden.has(calKey(place, cal))) return;
                occurrences(ev, from, to).forEach(([start, end]) => out.push({
                    key: `${place.id}|${ev.id}|${+start}`, kind: 'event', item: ev, place, cal, start, end,
                    allDay: !!ev.all_day, color: ev.color || cal.color, title: (ev.title || '(No title)') + (ev.born ? ` (${start.getFullYear() - ev.born})` : ''),
                }));
            });
            if (hidden.has(`${place.id}|tasks`)) return;
            place.tasks.forEach((t) => {
                if (!t.date) return;
                const start = parse(t.time ? `${t.date}T${t.time}` : t.date);
                const end = t.time ? addMin(start, 30) : addDays(start, 1);
                if (start < to && end > from) {
                    out.push({ key: `${place.id}|t|${t.id}`, kind: 'task', item: t, place, start, end, allDay: !t.time,
                        color: TASK_COLOR, title: t.title, done: t.done });
                }
            });
        });
        return out.sort((a, b) => a.start - b.start || b.end - a.end);
    }

    // ── Talking to the NAS ─────────────────────────────────────────────────
    async function refresh() {
        try {
            const got = await api('calendar');
            places = got.places || [];
            colors = got.colors || [];
            const bday = places.find((p) => p.id === 'birthdays');
            if (bday && store.get('bdaycolor', '')) bday.calendars[0].color = store.get('bdaycolor', '');
            hasOwn = !!got.has_own;
            loaded = true;
            (got.problems || []).forEach((p) => toast(p, 'error'));
        } catch (err) {
            loaded = true;
            toast(err.message, 'error');
        }
        render();
    }

    async function save(placeId, kind, item) {
        const got = await api('calendar/item', { method: 'POST', json: { place: placeId, kind, item } });
        const place = placeOf(placeId);
        const list = place[{ event: 'events', task: 'tasks', calendar: 'calendars' }[kind]];
        const i = list.findIndex((x) => x.id === got.item.id);
        if (i >= 0) list[i] = got.item; else list.push(got.item);
        place.calendars = got.calendars || place.calendars;
        render();
        return got.item;
    }

    async function remove(placeId, kind, id) {
        await api('calendar/delete', { method: 'POST', json: { place: placeId, kind, id } });
        const place = placeOf(placeId);
        if (kind === 'calendar') {
            place.calendars = place.calendars.filter((c) => c.id !== id);
            place.events = place.events.filter((e) => e.calendar !== id);
        } else {
            const key = kind === 'event' ? 'events' : 'tasks';
            place[key] = place[key].filter((x) => x.id !== id);
        }
        render();
    }

    // ── Layout ─────────────────────────────────────────────────────────────
    function build() {
        root.innerHTML = `
            <aside class="cal-side" id="cal-side">
                <div class="side-head"><span class="cal-logo">${icon('calendar')}</span><div><h1 class="side-title">Calendar</h1></div></div>
                <button type="button" class="cal-create" id="cal-create">${icon('plus')}<span>Create</span></button>
                <div class="cal-mini" id="cal-mini"></div>
                <div class="cal-lists" id="cal-lists"></div>
                <button type="button" class="cal-sync" id="cal-sync">${icon('phone')}<span>On your phone and computer</span></button>
                ${H.foot()}
            </aside>
            <div class="cal-scrim" id="cal-scrim" hidden></div>
            <section class="cal-main">
                <header class="cal-bar">
                    <button type="button" class="icon-btn only-phone" id="cal-menu" aria-label="Calendars">${icon('menu')}</button>
                    <button type="button" class="btn" id="cal-today">Today</button>
                    <div class="cal-nav">
                        <button type="button" class="icon-btn" id="cal-prev" aria-label="Back">${icon('chev-left')}</button>
                        <button type="button" class="icon-btn" id="cal-next" aria-label="Forward">${icon('chev-right')}</button>
                    </div>
                    <h2 class="cal-title" id="cal-title"></h2>
                    <div class="cal-bar-right">
                        <div class="cal-views" role="group" aria-label="View">
                            ${[['day', 'Day', 'd'], ['week', phone.matches ? '3 days' : 'Week', 'w'], ['month', 'Month', 'm'], ['schedule', 'Schedule', 'a']]
                                .map(([v, label, key]) => `<button type="button" data-view="${v}" title="${label} (${key})">${label}</button>`).join('')}
                        </div>
                        <button type="button" class="icon-btn" id="cal-tasks-btn" aria-label="Tasks" title="Tasks">${icon('tasks')}</button>
                    </div>
                </header>
                <div class="cal-note" id="cal-note" hidden></div>
                <div class="cal-body" id="cal-body"></div>
            </section>
            <aside class="cal-tasks" id="cal-tasks" hidden></aside>`;
        $c('#cal-sync').addEventListener('click', syncDialog);
        $c('#cal-create').addEventListener('click', (e) => {
            const start = new Date(cursor);
            const now = new Date();
            start.setHours(sameDay(cursor, now) ? now.getHours() + 1 : 9, 0);
            quickCreate(e.currentTarget, start, addMin(start, 60), false);
        });
        $c('#cal-today').addEventListener('click', () => { cursor = day0(new Date()); miniMonth = new Date(cursor.getFullYear(), cursor.getMonth(), 1); render(); });
        $c('#cal-prev').addEventListener('click', () => move(-1));
        $c('#cal-next').addEventListener('click', () => move(1));
        $c('#cal-menu').addEventListener('click', () => { $c('#cal-side').classList.add('open'); $c('#cal-scrim').hidden = false; });
        $c('#cal-scrim').addEventListener('click', closeSide);
        $c('#cal-tasks-btn').addEventListener('click', () => { tasksOpen = !tasksOpen; store.set('tasks', tasksOpen); render(); });
        root.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
        $c('#cal-mini').addEventListener('click', onMini);
        $c('#cal-lists').addEventListener('change', onToggle);
        $c('#cal-lists').addEventListener('click', onListClick);
        $c('#cal-body').addEventListener('pointerdown', onPointerDown);
        $c('#cal-body').addEventListener('click', onBodyClick);
        $c('#cal-tasks').addEventListener('click', onTasksClick);
        $c('#cal-tasks').addEventListener('keydown', onTasksKey);
        document.addEventListener('keydown', onKey);
        document.addEventListener('pointerdown', (e) => {
            if (pop && !pop.contains(e.target) && !e.target.closest('.cal-pop-keep')) closePop();
        });
        window.addEventListener('resize', () => { if (!root.hidden && view === 'month') renderBody(); });
        setInterval(() => { if (!root.hidden && (view === 'week' || view === 'day')) paintNow(); }, 60000);
        built = true;
    }
    function closeSide() { $c('#cal-side').classList.remove('open'); $c('#cal-scrim').hidden = true; }

    function setView(v) {
        view = v;
        store.set('view', v);
        closePop();
        render();
    }
    function days() { return view === 'day' ? 1 : phone.matches ? 3 : 7; }
    function rangeStart() { return view === 'week' && !phone.matches ? weekStart(cursor) : cursor; }
    function move(step) {
        closePop();
        if (view === 'month') cursor = new Date(cursor.getFullYear(), cursor.getMonth() + step, 1);
        else if (view === 'schedule') cursor = addDays(cursor, step * 14);
        else cursor = addDays(cursor, step * days());
        miniMonth = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
        render();
    }

    function render() {
        if (!root || root.hidden) return;
        renderSide();
        renderBar();
        renderBody();
        renderTasks();
    }

    function renderBar() {
        let title;
        if (view === 'month' || view === 'schedule') title = fmt(cursor, { month: 'long', year: 'numeric' });
        else if (view === 'day') title = fmt(cursor, { day: 'numeric', month: 'long', year: 'numeric' });
        else {
            const a = rangeStart();
            const b = addDays(a, days() - 1);
            title = a.getMonth() === b.getMonth() ? fmt(a, { month: 'long', year: 'numeric' })
                : `${fmt(a, { month: 'short', year: a.getFullYear() === b.getFullYear() ? undefined : 'numeric' })} – ${fmt(b, { month: 'short', year: 'numeric' })}`;
        }
        $c('#cal-title').textContent = title;
        root.querySelectorAll('[data-view]').forEach((b) => b.setAttribute('aria-pressed', b.dataset.view === view));
        $c('#cal-tasks-btn').setAttribute('aria-pressed', tasksOpen);
        $c('#cal-create').disabled = loaded && !writable().length;
        const note = $c('#cal-note');
        note.hidden = !loaded || hasOwn;
        note.textContent = places.length
            ? 'You have no personal folder yet, so there is no calendar of your own. Ask whoever runs this NAS to make you one.'
            : 'There is no calendar for you yet: you need a personal folder. Ask whoever runs this NAS to make you one.';
    }

    // ── Sidebar: little month and the calendars ────────────────────────────
    function renderSide() {
        const first = miniMonth;
        const start = weekStart(first);
        const todayKey = ymd(new Date());
        const shownFrom = view === 'month' ? new Date(cursor.getFullYear(), cursor.getMonth(), 1) : rangeStart();
        const shownTo = view === 'month' ? new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1) : addDays(rangeStart(), view === 'schedule' ? 1 : days());
        let cells = '';
        for (let i = 0; i < 42; i += 1) {
            const d = addDays(start, i);
            const cls = [d.getMonth() !== first.getMonth() ? 'out' : '', ymd(d) === todayKey ? 'today' : '',
                (view === 'week' || view === 'day') && d >= shownFrom && d < shownTo ? 'in' : '', sameDay(d, cursor) ? 'sel' : ''].filter(Boolean).join(' ');
            cells += `<button type="button" class="${cls}" data-day="${ymd(d)}">${d.getDate()}</button>`;
        }
        $c('#cal-mini').innerHTML = `
            <div class="cal-mini-head"><span>${esc(fmt(first, { month: 'long', year: 'numeric' }))}</span>
                <button type="button" class="icon-btn" data-mini="-1" aria-label="Previous month">${icon('chev-left')}</button>
                <button type="button" class="icon-btn" data-mini="1" aria-label="Next month">${icon('chev-right')}</button></div>
            <div class="cal-mini-grid">${['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((x) => `<span>${x}</span>`).join('')}${cells}</div>`;

        const item = (key, name, color, extra) => `
            <label class="cal-item" style="--c:${esc(color)}">
                <input type="checkbox" data-key="${esc(key)}"${hidden.has(key) ? '' : ' checked'}>
                <span class="cal-box">${icon('check')}</span><span class="cal-name">${esc(name)}</span>${extra || ''}
            </label>`;
        const section = (place) => `
            <section class="cal-group">
                <div class="cal-group-head"><span>${place.own ? 'My calendars' : `${icon('users')}${esc(place.name)}`}</span>
                    ${place.writable ? `<button type="button" class="icon-btn" data-add-cal="${esc(place.id)}" title="Add a calendar" aria-label="Add a calendar">${icon('plus')}</button>` : '<span class="cal-ro" title="You can look, not change">read only</span>'}</div>
                ${place.calendars.map((c) => item(calKey(place, c), c.name, c.color,
                    place.writable ? `<button type="button" class="cal-more" data-edit-cal="${esc(calKey(place, c))}" aria-label="Change ${esc(c.name)}">${icon('more')}</button>`
                        : place.id === 'birthdays' ? `<button type="button" class="cal-more" data-bday-color aria-label="Change the colour of ${esc(c.name)}">${icon('more')}</button>` : '')).join('')}
                ${place.writable || place.tasks.length ? item(`${place.id}|tasks`, place.own ? 'Tasks' : `Tasks of ${place.name}`, TASK_COLOR) : ''}
            </section>`;
        $c('#cal-lists').innerHTML = places.map(section).join('')
            || (loaded ? '<p class="cal-empty-side">No calendars yet.</p>' : '');
    }

    function onMini(e) {
        const step = e.target.closest('[data-mini]');
        if (step) { miniMonth = new Date(miniMonth.getFullYear(), miniMonth.getMonth() + Number(step.dataset.mini), 1); renderSide(); return; }
        const day = e.target.closest('[data-day]');
        if (day) { cursor = parse(day.dataset.day); closeSide(); render(); }
    }
    function onToggle(e) {
        const box = e.target.closest('[data-key]');
        if (!box) return;
        if (box.checked) hidden.delete(box.dataset.key); else hidden.add(box.dataset.key);
        store.set('hidden', [...hidden]);
        renderBody();
    }
    function onListClick(e) {
        const add = e.target.closest('[data-add-cal]');
        if (add) { e.preventDefault(); calendarDialog(placeOf(add.dataset.addCal), null); return; }
        if (e.target.closest('[data-bday-color]')) { e.preventDefault(); birthdayColorDialog(); return; }
        const edit = e.target.closest('[data-edit-cal]');
        if (edit) {
            e.preventDefault();
            const [pid, cid] = edit.dataset.editCal.split('|');
            const place = placeOf(pid);
            calendarDialog(place, place.calendars.find((c) => c.id === cid));
        }
    }

    // ── The big part: day, week, month, schedule ───────────────────────────
    function renderBody() {
        closePop(true);
        const body = $c('#cal-body');
        body.className = `cal-body is-${view}`;
        if (!loaded) { body.innerHTML = '<div class="cal-loading">Loading…</div>'; return; }
        if (view === 'month') body.innerHTML = monthHtml(body);
        else if (view === 'schedule') body.innerHTML = scheduleHtml();
        else {
            const keep = scrollTop;
            body.innerHTML = weekHtml();
            const scroll = $c('.wk-scroll');
            // The header lines up with the grid next to the scroll bar.
            $c('.wk').style.setProperty('--sb', `${scroll.offsetWidth - scroll.clientWidth}px`);
            if (keep === null) {
                // Start a little before now, or before the first event if that is earlier.
                const first = Math.min(...[...shownItems.values()].filter((x) => !x.allDay).map((x) => x.start.getHours()), 24);
                scroll.scrollTop = Math.max(0, Math.min(new Date().getHours() - 1.5, first - 0.5, 8) * HOUR);
            } else scroll.scrollTop = keep;
            scroll.addEventListener('scroll', () => { scrollTop = scroll.scrollTop; }, { passive: true });
            paintNow();
        }
    }

    function chipText(x, withTime) {
        const done = x.kind === 'task' ? `<span class="chip-task">${icon(x.done ? 'check-circle' : 'circle')}</span>` : '';
        return `${done}${withTime && !x.allDay ? `<span class="chip-time">${hm(x.start)}</span> ` : ''}<span class="chip-title">${esc(x.title)}</span>`;
    }

    // Lanes for things that span days: [{x, from, to, lane}] for a row of n days.
    function lanes(items, rowStart, n) {
        const used = [];
        return items.map((x) => {
            const from = Math.max(0, daysBetween(rowStart, x.start));
            const to = Math.min(n - 1, daysBetween(rowStart, addMin(x.end, -1)));
            let lane = 0;
            while ((used[lane] || []).some(([a, b]) => !(to < a || from > b))) lane += 1;
            (used[lane] = used[lane] || []).push([from, to]);
            return { x, from, to, lane };
        });
    }

    let shownItems = new Map();
    let scrollTop = null;   // kept while moving through weeks
    function remember(list) { list.forEach((x) => shownItems.set(x.key, x)); }

    function weekHtml() {
        const n = days();
        const start = rangeStart();
        const end = addDays(start, n);
        const list = instances(start, end);
        shownItems = new Map();
        remember(list);
        const todayKey = ymd(new Date());
        const allDay = list.filter((x) => x.allDay);
        const timed = list.filter((x) => !x.allDay);
        const placed = lanes(allDay, start, n);
        const laneCount = placed.reduce((m, p) => Math.max(m, p.lane + 1), 0);
        const head = Array.from({ length: n }, (_, i) => {
            const d = addDays(start, i);
            return `<div class="wk-day${ymd(d) === todayKey ? ' today' : ''}"><span class="dow">${esc(fmt(d, { weekday: 'short' }).toUpperCase())}</span>
                <button type="button" class="dnum" data-goto="${ymd(d)}" aria-label="${esc(longDay(d))}">${d.getDate()}</button></div>`;
        }).join('');
        const allDayHtml = placed.map((p) => `<button type="button" class="chip solid${p.x.done ? ' done' : ''}" data-key="${esc(p.x.key)}"
            style="grid-column:${p.from + 1} / ${p.to + 2};grid-row:${p.lane + 1};--c:${esc(p.x.color)}">${chipText(p.x, false)}</button>`).join('');
        const cols = Array.from({ length: n }, (_, i) => {
            const d = addDays(start, i);
            const dayStart = day0(d);
            const dayEnd = addDays(dayStart, 1);
            const segs = timed.filter((x) => x.start < dayEnd && x.end > dayStart).map((x) => ({
                x, s: Math.max(0, (Math.max(x.start, dayStart) - dayStart) / 60000),
                e: Math.min(1440, (Math.min(x.end, dayEnd) - dayStart) / 60000),
            }));
            layoutColumn(segs);
            const evs = segs.map((g) => {
                const top = (g.s / 60) * HOUR;
                const height = Math.max(18, ((g.e - g.s) / 60) * HOUR - 2);
                const short = height < 40;
                const canChange = g.x.place.writable;
                return `<div class="ev${short ? ' short' : ''}${g.x.kind === 'task' ? ' task' : ''}${g.x.done ? ' done' : ''}${canChange ? '' : ' ro'}" data-key="${esc(g.x.key)}"
                    style="top:${top}px;height:${height}px;left:calc(${(g.col / g.n) * 100}% + 1px);width:calc(${(1 / g.n) * 100}% - ${g.n > 1 ? 3 : 8}px);--c:${esc(g.x.color)}">
                    <div class="ev-title">${g.x.kind === 'task' ? `<span class="chip-task">${icon(g.x.done ? 'check-circle' : 'circle')}</span>` : ''}${esc(g.x.title)}${short ? `<span class="ev-time">, ${hm(g.x.start)}</span>` : ''}</div>
                    ${short ? '' : `<div class="ev-time">${hm(g.x.start)} – ${hm(g.x.end)}</div>${g.x.item.location ? `<div class="ev-time">${esc(g.x.item.location)}</div>` : ''}`}
                    ${canChange && g.x.kind === 'event' && !g.x.item.all_day ? '<div class="ev-resize" data-resize></div>' : ''}
                </div>`;
            }).join('');
            return `<div class="wk-col${ymd(d) === todayKey ? ' today' : ''}" data-date="${ymd(d)}">${evs}</div>`;
        }).join('');
        const times = Array.from({ length: 24 }, (_, h) => `<span style="top:${h * HOUR}px">${h ? `${pad(h)}:00` : ''}</span>`).join('');
        return `
            <div class="wk" style="--days:${n};--hour:${HOUR}px">
                <div class="wk-head"><div class="wk-gutter"></div>${head}</div>
                <div class="wk-allday"><div class="wk-gutter"></div>
                    <div class="wk-allday-grid" data-allday="${ymd(start)}" style="grid-template-rows:repeat(${Math.max(1, laneCount)}, 24px)">${allDayHtml}</div></div>
                <div class="wk-scroll">
                    <div class="wk-grid" style="height:${24 * HOUR}px">
                        <div class="wk-times">${times}</div>
                        ${cols}
                    </div>
                </div>
            </div>`;
    }

    // Overlapping events share the column side by side.
    function layoutColumn(items) {
        items.sort((a, b) => a.s - b.s || b.e - a.e);
        let cluster = [];
        let columns = [];
        let clusterEnd = -1;
        const flush = () => { cluster.forEach((it) => { it.n = columns.length; }); cluster = []; columns = []; };
        items.forEach((it) => {
            if (it.s >= clusterEnd) { flush(); clusterEnd = -1; }
            let c = columns.findIndex((end) => end <= it.s);
            if (c < 0) { c = columns.length; columns.push(it.e); } else columns[c] = it.e;
            it.col = c;
            cluster.push(it);
            clusterEnd = Math.max(clusterEnd, it.e);
        });
        flush();
    }

    function paintNow() {
        const grid = $c('.wk-grid');
        if (!grid) return;
        grid.querySelectorAll('.now').forEach((x) => x.remove());
        const now = new Date();
        const col = grid.querySelector(`.wk-col[data-date="${ymd(now)}"]`);
        if (!col) return;
        const line = document.createElement('div');
        line.className = 'now';
        line.style.top = `${((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR}px`;
        col.appendChild(line);
    }

    function monthHtml(body) {
        const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
        const start = weekStart(first);
        const weeks = Math.ceil((daysBetween(start, first) + new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate()) / 7);
        const end = addDays(start, weeks * 7);
        const list = instances(start, end);
        shownItems = new Map();
        remember(list);
        const todayKey = ymd(new Date());
        const rowHeight = Math.max(92, ((body.clientHeight || 600) - 30) / weeks);
        const fit = Math.max(1, Math.floor((rowHeight - 28) / 22));
        const rows = [];
        for (let w = 0; w < weeks; w += 1) {
            const ws = addDays(start, w * 7);
            const we = addDays(ws, 7);
            const inWeek = list.filter((x) => x.start < we && x.end > ws);
            // Whole days and longer first, as bars; the rest as dots.
            const spanning = inWeek.filter((x) => x.allDay || daysBetween(x.start, addMin(x.end, -1)) > 0);
            const single = inWeek.filter((x) => !spanning.includes(x));
            const placed = lanes([...spanning, ...single], ws, 7);
            const hiddenPerDay = Array(7).fill(0);
            const cells = placed.filter((p) => {
                if (p.lane < fit - 1 || (p.lane === fit - 1 && placed.every((q) => q.lane < fit || q.to < p.from || q.from > p.to))) return true;
                for (let d = p.from; d <= p.to; d += 1) hiddenPerDay[d] += 1;
                return false;
            });
            const chips = cells.map((p) => {
                const solid = spanning.includes(p.x);
                return `<button type="button" class="chip${solid ? ' solid' : ''}${p.x.done ? ' done' : ''}" data-key="${esc(p.x.key)}"
                    style="grid-column:${p.from + 1} / ${p.to + 2};grid-row:${p.lane + 2};--c:${esc(p.x.color)}">${solid ? '' : '<span class="dot"></span>'}${chipText(p.x, !solid)}</button>`;
            }).join('');
            const more = hiddenPerDay.map((c, d) => (c ? `<button type="button" class="more" data-goto="${ymd(addDays(ws, d))}" style="grid-column:${d + 1};grid-row:${fit + 1}">${c} more</button>` : '')).join('');
            const cellsBg = Array.from({ length: 7 }, (_, d) => {
                const day = addDays(ws, d);
                return `<div class="mo-cell${day.getMonth() !== first.getMonth() ? ' out' : ''}" data-date="${ymd(day)}" style="grid-column:${d + 1};grid-row:1 / -1"></div>
                    <div class="mo-num${ymd(day) === todayKey ? ' today' : ''}" style="grid-column:${d + 1};grid-row:1"><button type="button" data-goto="${ymd(day)}">${day.getDate() === 1 ? esc(fmt(day, { day: 'numeric', month: 'short' })) : day.getDate()}</button></div>`;
            }).join('');
            rows.push(`<div class="mo-week" style="grid-template-rows:26px repeat(${fit}, 22px) 1fr">${cellsBg}${chips}${more}</div>`);
        }
        const head = Array.from({ length: 7 }, (_, d) => `<span>${esc(fmt(addDays(start, d), { weekday: 'short' }).toUpperCase())}</span>`).join('');
        return `<div class="mo"><div class="mo-head">${head}</div><div class="mo-grid" style="grid-template-rows:repeat(${weeks}, minmax(0, 1fr))">${rows.join('')}</div></div>`;
    }

    function scheduleHtml() {
        const start = day0(cursor);
        const end = addDays(start, 42);
        const list = instances(start, end);
        shownItems = new Map();
        remember(list);
        const todayKey = ymd(new Date());
        const out = [];
        for (let i = 0; i < 42; i += 1) {
            const d = addDays(start, i);
            const next = addDays(d, 1);
            const items = list.filter((x) => x.start < next && x.end > d);
            if (!items.length && ymd(d) !== todayKey) continue;
            if (d.getDate() === 1 || !out.length) out.push(`<div class="sch-month">${esc(fmt(d, { month: 'long', year: 'numeric' }))}</div>`);
            out.push(`<div class="sch-day${ymd(d) === todayKey ? ' today' : ''}">
                <button type="button" class="sch-date" data-goto="${ymd(d)}"><span class="n">${d.getDate()}</span><span class="w">${esc(fmt(d, { month: 'short', weekday: 'short' }).toUpperCase())}</span></button>
                <div class="sch-items">${items.length ? items.map((x) => `
                    <button type="button" class="sch-item${x.done ? ' done' : ''}" data-key="${esc(x.key)}" style="--c:${esc(x.color)}">
                        <span class="dot"></span><span class="sch-time">${x.allDay ? 'All day' : `${sameDay(x.start, d) ? hm(x.start) : '…'} – ${sameDay(x.end, d) || hm(x.end) === '00:00' ? hm(x.end) : '…'}`}</span>
                        <span class="sch-title">${x.kind === 'task' ? `<span class="chip-task">${icon(x.done ? 'check-circle' : 'circle')}</span>` : ''}${esc(x.title)}</span>
                    </button>`).join('') : '<div class="sch-none">Nothing planned</div>'}</div>
            </div>`);
        }
        if (out.length <= 2 && !list.length) out.push('<div class="sch-empty">Nothing in the next six weeks. Press “Create” to plan something.</div>');
        return `<div class="sch">${out.join('')}</div>`;
    }

    function onBodyClick(e) {
        if (Date.now() - lastDrag < 300) return;
        const go = e.target.closest('[data-goto]');
        if (go) { cursor = parse(go.dataset.goto); setView('day'); return; }
        const chip = e.target.closest('.chip[data-key], .sch-item[data-key]');
        if (chip) { details(chip, shownItems.get(chip.dataset.key)); return; }
        const cell = e.target.closest('.mo-cell');
        if (cell) { const d = parse(cell.dataset.date); quickCreate(cell, d, addDays(d, 1), true); return; }
        const allday = e.target.closest('.wk-allday-grid');
        if (allday && e.target === allday) {
            const rect = allday.getBoundingClientRect();
            const d = addDays(parse(allday.dataset.allday), Math.floor(((e.clientX - rect.left) / rect.width) * days()));
            quickCreate(allday, d, addDays(d, 1), true, { x: e.clientX, y: e.clientY });
        }
    }

    // ── Dragging in the day and week grid ──────────────────────────────────
    let drag = null;
    let lastDrag = 0;
    const SNAP = 15;
    function minutesAt(col, y) {
        const rect = col.getBoundingClientRect();
        return Math.max(0, Math.min(1440, Math.round((((y - rect.top) / HOUR) * 60) / SNAP) * SNAP));
    }
    function colAt(x, y) {
        const el = document.elementFromPoint(x, y);
        return el && el.closest ? el.closest('.wk-col') : null;
    }

    function onPointerDown(e) {
        if (e.button !== 0) return;
        const evEl = e.target.closest('.ev');
        const col = e.target.closest('.wk-col');
        const chip = e.target.closest('.mo-week .chip');
        if (evEl) {
            const x = shownItems.get(evEl.dataset.key);
            if (!x) return;
            drag = { kind: e.target.closest('[data-resize]') ? 'resize' : 'move', x, el: evEl, startX: e.clientX, startY: e.clientY, moved: false,
                col: evEl.closest('.wk-col') };
        } else if (chip) {
            const x = shownItems.get(chip.dataset.key);
            if (!x) return;
            drag = { kind: 'month', x, el: chip, startX: e.clientX, startY: e.clientY, moved: false, from: chip.closest('.mo-week') };
        } else if (col) {
            if (!writable().length) return;
            const m = minutesAt(col, e.clientY);
            drag = { kind: 'new', col, a: m, b: m + 60, startY: e.clientY, startX: e.clientX, moved: false };
        } else return;
        e.preventDefault();
        const body = $c('#cal-body');
        body.setPointerCapture(e.pointerId);
        const up = (ev) => {
            body.removeEventListener('pointermove', onDragMove);
            body.removeEventListener('pointerup', up);
            body.removeEventListener('pointercancel', up);
            onDragEnd(ev);
        };
        body.addEventListener('pointermove', onDragMove);
        body.addEventListener('pointerup', up);
        body.addEventListener('pointercancel', up);
    }

    function onDragMove(e) {
        if (!drag) return;
        if (!drag.moved && Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) < 5) return;
        if (!drag.moved) {
            drag.moved = true;
            closePop();
            if (drag.kind !== 'new' && !drag.x.place.writable) { toast(`You can only look at "${drag.x.place.name}".`, 'error'); drag = null; return; }
            if (drag.kind === 'new') {
                drag.ghost = document.createElement('div');
                drag.ghost.className = 'ev ghost';
                drag.col.appendChild(drag.ghost);
            } else if (drag.kind !== 'month') {
                drag.el.classList.add('dragging');
            }
        }
        if (drag.kind === 'new') {
            const m = minutesAt(drag.col, e.clientY);
            const a = Math.min(drag.a, m);
            const b = Math.max(drag.a + SNAP, m);
            drag.range = [a, b];
            Object.assign(drag.ghost.style, { top: `${(a / 60) * HOUR}px`, height: `${((b - a) / 60) * HOUR - 2}px`, left: '1px', width: 'calc(100% - 8px)' });
            drag.ghost.innerHTML = `<div class="ev-title">(No title)</div><div class="ev-time">${pad(Math.floor(a / 60))}:${pad(a % 60)} – ${pad(Math.floor(b / 60) % 24)}:${pad(b % 60)}</div>`;
        } else if (drag.kind === 'move') {
            const col = colAt(e.clientX, e.clientY) || drag.col;
            const minutes = Math.round((((e.clientY - drag.startY) / HOUR) * 60) / SNAP) * SNAP;
            const dayShift = daysBetween(parse(drag.col.dataset.date), parse(col.dataset.date));
            drag.shift = dayShift * 1440 + minutes;
            if (col !== drag.el.parentElement) col.appendChild(drag.el);
            const top = parseFloat(drag.el.dataset.top || drag.el.style.top);
            drag.el.dataset.top = drag.el.dataset.top || String(top);
            drag.el.style.top = `${Number(drag.el.dataset.top) + (minutes / 60) * HOUR}px`;
            drag.el.style.left = '1px';
            drag.el.style.width = 'calc(100% - 8px)';
            const s = addMin(drag.x.start, drag.shift);
            const t = drag.el.querySelector('.ev-time');
            if (t) t.textContent = `${hm(s)} – ${hm(addMin(drag.x.end, drag.shift))}`;
        } else if (drag.kind === 'resize') {
            const m = minutesAt(drag.col, e.clientY);
            const startMin = (drag.x.start - day0(drag.x.start)) / 60000;
            const endMin = sameDay(drag.x.start, drag.x.end) ? Math.max(startMin + SNAP, m) : m;
            drag.newEnd = new Date(day0(drag.x.start).getTime() + endMin * 60000);
            if (drag.newEnd <= drag.x.start) drag.newEnd = addMin(drag.x.start, SNAP);
            drag.el.style.height = `${((drag.newEnd - drag.x.start) / 3600000) * HOUR - 2}px`;
            const t = drag.el.querySelector('.ev-time');
            if (t) t.textContent = `${hm(drag.x.start)} – ${hm(drag.newEnd)}`;
        } else if (drag.kind === 'month') {
            const el = document.elementFromPoint(e.clientX, e.clientY);
            const cell = el && el.closest ? (el.closest('[data-date]') || el.closest('.mo-week')) : null;
            root.querySelectorAll('.mo-cell.drop').forEach((c) => c.classList.remove('drop'));
            let target = cell && cell.classList.contains('mo-cell') ? cell : null;
            if (!target && cell) {
                // Over another chip: the day under the pointer in that week.
                const week = cell.closest('.mo-week');
                const rect = week.getBoundingClientRect();
                target = week.querySelectorAll('.mo-cell')[Math.min(6, Math.floor(((e.clientX - rect.left) / rect.width) * 7))];
            }
            if (target) { target.classList.add('drop'); drag.target = target.dataset.date; }
            drag.el.classList.add('dragging');
        }
    }

    async function onDragEnd(e) {
        const d = drag;
        drag = null;
        if (!d) return;
        if (!d.moved) {
            if (d.kind === 'new') {
                const day = parse(d.col.dataset.date);
                const start = addMin(day, d.a);
                quickCreate(null, start, addMin(start, 60), false, { x: e.clientX, y: e.clientY, col: d.col, a: d.a, b: d.a + 60 });
            } else details(d.el, d.x);
            return;
        }
        lastDrag = Date.now();
        try {
            if (d.kind === 'new') {
                const day = parse(d.col.dataset.date);
                const [a, b] = d.range;
                quickCreate(null, addMin(day, a), addMin(day, b), false, { x: e.clientX, y: e.clientY, ghost: d.ghost });
                return;
            }
            if (d.kind === 'month') {
                root.querySelectorAll('.mo-cell.drop').forEach((c) => c.classList.remove('drop'));
                if (!d.target) { renderBody(); return; }
                const shift = daysBetween(day0(d.x.start), parse(d.target));
                if (shift) await shiftItem(d.x, shift * 1440); else renderBody();
                return;
            }
            if (d.kind === 'move' && d.shift) await shiftItem(d.x, d.shift);
            else if (d.kind === 'resize' && d.newEnd) {
                const ev = d.x.item;
                const length = d.newEnd - d.x.start;
                await save(d.x.place.id, 'event', { ...ev, end: stamp(new Date(parse(ev.start).getTime() + length)) });
            } else renderBody();
        } catch (err) {
            toast(err.message, 'error');
            renderBody();
        }
    }

    // Moving one repeat moves them all (the whole series), as the editor says.
    async function shiftItem(x, minutes) {
        if (x.kind === 'task') {
            const t = x.item;
            const s = addMin(x.start, minutes);
            return save(x.place.id, 'task', { ...t, date: ymd(s), time: t.time ? hm(s) : '' });
        }
        const ev = x.item;
        if (ev.all_day) {
            const daysShift = Math.round(minutes / 1440);
            return save(x.place.id, 'event', { ...ev, start: ymd(addDays(parse(ev.start), daysShift)), end: ymd(addDays(parse(ev.end), daysShift)) });
        }
        return save(x.place.id, 'event', { ...ev, start: stamp(addMin(parse(ev.start), minutes)), end: stamp(addMin(parse(ev.end), minutes)) });
    }

    // ── Little cards: details and quick create ─────────────────────────────
    function closePop(keepGhosts) {
        if (pop) { pop.remove(); pop = null; }
        if (!keepGhosts && root) root.querySelectorAll('.ev.ghost').forEach((g) => g.remove());
    }
    function place(card, anchor, at) {
        document.body.appendChild(card);
        const w = card.offsetWidth;
        const h = card.offsetHeight;
        let x;
        let y;
        if (anchor) {
            const r = anchor.getBoundingClientRect();
            x = r.right + 10 + w < window.innerWidth ? r.right + 10 : r.left - w - 10;
            if (x < 8) x = Math.min(window.innerWidth - w - 8, Math.max(8, r.left));
            y = r.top;
        } else {
            x = at.x + 16 + w < window.innerWidth ? at.x + 16 : at.x - w - 16;
            y = at.y - 40;
        }
        card.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, x))}px`;
        card.style.top = `${Math.max(8, Math.min(window.innerHeight - h - 8, y))}px`;
        pop = card;
    }

    function details(anchor, x) {
        if (!x) return;
        closePop();
        const card = document.createElement('div');
        card.className = 'cal-pop';
        card.setAttribute('role', 'dialog');
        const item = x.item;
        const canChange = x.place.writable;
        const when = x.kind === 'task'
            ? (item.time ? `${longDay(x.start)} · ${item.time}` : longDay(x.start))
            : whenText(x.start, x.end, x.allDay);
        card.innerHTML = `
            <div class="pop-tools">
                ${canChange ? `<button type="button" class="icon-btn" data-act="edit" title="Change" aria-label="Change">${icon('pen')}</button>
                <button type="button" class="icon-btn" data-act="delete" title="Delete" aria-label="Delete">${icon('trash')}</button>` : ''}
                <button type="button" class="icon-btn" data-act="close" title="Close" aria-label="Close">${icon('x')}</button>
            </div>
            <div class="pop-row"><span class="sq" style="--c:${esc(x.color)}"></span>
                <div><h3 class="${x.done ? 'done' : ''}">${esc(x.title)}</h3><div class="pop-sub">${esc(when)}</div>
                ${x.kind === 'event' && item.repeat ? `<div class="pop-sub">${esc(repeatText(item))}</div>` : ''}</div></div>
            ${item.location ? `<div class="pop-row">${icon('map-pin')}<div>${esc(item.location)}</div></div>` : ''}
            ${item.notes ? `<div class="pop-row">${icon('notes')}<div class="pop-notes">${esc(item.notes)}</div></div>` : ''}
            <div class="pop-row">${icon(x.kind === 'task' ? 'tasks' : 'calendar')}<div>${esc(x.kind === 'task' ? (x.place.own ? 'My tasks' : `Tasks of ${x.place.name}`) : x.cal.name)}${x.place.own ? '' : ` <span class="pop-place">· ${esc(x.place.name)}</span>`}</div></div>
            ${x.kind === 'task' && canChange ? `<div class="pop-actions"><button type="button" class="btn" data-act="done">${icon(item.done ? 'circle' : 'check')}${item.done ? 'Mark as not done' : 'Mark as done'}</button></div>` : ''}`;
        card.addEventListener('click', async (e) => {
            const act = e.target.closest('[data-act]');
            if (!act) return;
            const a = act.dataset.act;
            if (a === 'close') closePop();
            else if (a === 'edit') { closePop(); if (x.kind === 'task') taskDialog(x.place, item); else eventDialog(x.place, item); }
            else if (a === 'delete') {
                closePop();
                try {
                    await remove(x.place.id, x.kind, item.id);
                    toast(x.kind === 'task' ? 'Task deleted.' : item.repeat ? 'Event deleted, with all its repeats.' : 'Event deleted.');
                } catch (err) { toast(err.message, 'error'); }
            } else if (a === 'done') {
                closePop();
                try { await save(x.place.id, 'task', { ...item, done: !item.done }); } catch (err) { toast(err.message, 'error'); }
            }
        });
        place(card, anchor);
    }

    function targetOptions(selected, forTasks) {
        return writable().map((p) => (forTasks
            ? `<option value="${esc(p.id)}"${selected === p.id ? ' selected' : ''}>${esc(p.own ? 'My tasks' : `Tasks of ${p.name}`)}</option>`
            : p.calendars.map((c) => `<option value="${esc(calKey(p, c))}"${selected === calKey(p, c) ? ' selected' : ''}>${esc(c.name)}${p.own ? '' : ` (${esc(p.name)})`}</option>`).join(''))).join('');
    }
    function defaultTarget() {
        const p = writable().find((x) => x.own) || writable()[0];
        const c = p && (p.calendars.find((x) => !hidden.has(calKey(p, x))) || p.calendars[0]);
        return p ? calKey(p, c) : '';
    }


    // A time written in the title moves out of it: "20:00 Choir", "19:30-21 Choir",
    // "Choir um 20 Uhr". Colon or "Uhr"/"h" is needed, so "5 friends" stays a title.
    function timeInTitle(text) {
        const t = String(text || '').trim();
        const at = (h, m) => (Number(h) <= 23 && Number(m || 0) <= 59 ? { h: Number(h), m: Number(m || 0) } : null);
        let m = /^(\d{1,2})(?:([:.])(\d{2}))?(?:\s*[-–]\s*(\d{1,2})(?:[:.](\d{2}))?)?\s*(uhr|h)?\s+(?:um\s+)?(\S.*)$/i.exec(t);
        if (m && (m[2] === ':' || m[6])) {
            const from = at(m[1], m[3]);
            const to = m[4] !== undefined ? at(m[4], m[5]) : null;
            if (from && (m[4] === undefined || to)) return { title: m[7].trim(), from, to };
        }
        m = /^(\S.*?)\s+(?:um\s+|ab\s+)?(\d{1,2})(?:(:)(\d{2})|(?:[.:](\d{2}))?\s*(uhr|h))$/i.exec(t);
        if (m) {
            const from = at(m[2], m[4] || m[5]);
            if (from) return { title: m[1].trim(), from, to: null };
        }
        return null;
    }
    // Where the start and end of an item go when the title names a time (the length stays).
    function withTitleTime(parsed, start, end, wasAllDay) {
        const s = new Date(start.getFullYear(), start.getMonth(), start.getDate(), parsed.from.h, parsed.from.m);
        const length = wasAllDay ? 60 * 60000 : Math.max(15 * 60000, end - start);
        let e = parsed.to ? new Date(s.getFullYear(), s.getMonth(), s.getDate(), parsed.to.h, parsed.to.m) : new Date(s.getTime() + length);
        if (e <= s) e = new Date(s.getTime() + length);
        return { start: s, end: e };
    }


    // ── Birthdays: only in the calendar, with a new contact, or on a contact there is ──
    const hasContacts = () => (H.me()?.hub?.apps || []).some((a) => a.id === 'contacts');
    let contactChoices = null;
    async function loadContactChoices(select) {
        if (!select || select.dataset.loaded) return;
        select.dataset.loaded = '1';
        try {
            contactChoices = (await api('contacts')).contacts || [];
        } catch (_e) { contactChoices = []; }
        if (!contactChoices.length) return;
        const name = (c) => [c.first, c.last].filter(Boolean).join(' ') || c.org || 'No name';
        select.insertAdjacentHTML('beforeend', `<optgroup label="Add to a contact">${contactChoices
            .slice().sort((a, b) => name(a).localeCompare(name(b)))
            .map((c) => `<option value="c:${esc(c.id)}">${esc(name(c))}</option>`).join('')}</optgroup>`);
    }
    async function saveBirthday(card, title, day) {
        const name = title.value.trim();
        const how = card.querySelector('[data-bday-how]').value;
        const born = card.querySelector('[data-born]').value.trim();
        if (!how.startsWith('c:') && !name) { title.focus(); toast('Whose birthday is it?', 'error'); return; }
        if (born && !/^\d{4}$/.test(born)) { toast('The year is four digits, like 1985.', 'error'); return; }
        const mmdd = `${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
        const birthday = born ? `${born}-${mmdd}` : `--${mmdd}`;
        try {
            if (how === 'event') {
                const target = defaultTarget().split('|');
                const first = born ? `${born}-${mmdd}` : ymd(day);
                await save(target[0], 'event', { title: `${name}'s birthday`, calendar: target[1], all_day: true, start: first, end: first,
                    repeat: 'yearly', until: '', color: '', location: '', notes: '' });
            } else if (how === 'new') {
                const words = name.split(/\s+/);
                const last = words.length > 1 ? words.pop() : '';
                await api('contacts/item', { method: 'POST', json: { item: { first: words.join(' '), last, birthday } } });
                toast(`${name} is in your contacts now.`);
                await refresh();
            } else {
                const c = (contactChoices || []).find((x) => `c:${x.id}` === how);
                if (!c) { toast('That contact is not there any more.', 'error'); return; }
                await api('contacts/item', { method: 'POST', json: { item: { ...c, birthday } } });
                toast('The birthday is saved in the contact.');
                await refresh();
            }
            closePop();
        } catch (err) { toast(err.message, 'error'); }
    }

    function quickCreate(anchor, start, end, allDay, at) {
        closePop(true);
        root.querySelectorAll('.ev.ghost').forEach((g) => { if (!at || g !== at.ghost) g.remove(); });
        if (!writable().length) { toast('There is no calendar here you may change.', 'error'); return; }
        if (at && at.col && !at.ghost) {
            const g = document.createElement('div');
            g.className = 'ev ghost';
            Object.assign(g.style, { top: `${(at.a / 60) * HOUR}px`, height: `${((at.b - at.a) / 60) * HOUR - 2}px`, left: '1px', width: 'calc(100% - 8px)' });
            g.innerHTML = `<div class="ev-title">(No title)</div><div class="ev-time">${hm(start)} – ${hm(end)}</div>`;
            at.col.appendChild(g);
        }
        let kind = 'event';
        const card = document.createElement('div');
        card.className = 'cal-pop create';
        card.setAttribute('role', 'dialog');
        const whenLine = () => (kind === 'birthday' ? `${fmt(start, { day: 'numeric', month: 'long' })}, every year`
            : kind === 'task'
            ? (allDay ? longDay(start) : `${longDay(start)} · ${hm(start)}`)
            : whenText(start, end, allDay));
        card.innerHTML = `
            <div class="pop-tools"><button type="button" class="icon-btn" data-act="close" aria-label="Close">${icon('x')}</button></div>
            <input class="pop-title" placeholder="Add title, or “20:00 Choir”" aria-label="Title" maxlength="300">
            <div class="pop-tabs" role="tablist"><button type="button" role="tab" data-kind="event" aria-selected="true">Event</button><button type="button" role="tab" data-kind="task" aria-selected="false">Task</button><button type="button" role="tab" data-kind="birthday" aria-selected="false">Birthday</button></div>
            <div class="pop-row">${icon('clock')}<div class="pop-when">${esc(whenLine())}</div></div>
            <div class="pop-row" data-for="event">${icon('calendar')}<select class="pop-select" data-target aria-label="Calendar">${targetOptions(defaultTarget(), false)}</select></div>
            <div class="pop-row" data-for="task" hidden>${icon('tasks')}<select class="pop-select" data-task-target aria-label="Task list">${targetOptions((writable().find((p) => p.own) || writable()[0]).id, true)}</select></div>
            <div class="pop-row" data-for="birthday" hidden>${icon('clock')}<input class="pop-select" data-born type="number" min="1900" max="2100" placeholder="Year born (optional)" aria-label="Year born"></div>
            <div class="pop-row" data-for="birthday" hidden>${icon('users')}<select class="pop-select" data-bday-how aria-label="Where to keep it"><option value="event">Only in the calendar</option>${hasContacts() ? '<option value="new">And make a new contact</option>' : ''}</select></div>
            <div class="pop-actions"><button type="button" class="btn ghost" data-act="more">More options</button><button type="button" class="btn primary" data-act="save">Save</button></div>`;
        const title = card.querySelector('.pop-title');
        const setKind = (k) => {
            kind = k;
            card.querySelectorAll('[data-kind]').forEach((b) => b.setAttribute('aria-selected', b.dataset.kind === k));
            card.querySelectorAll('[data-for]').forEach((r) => { r.hidden = r.dataset.for !== k; });
            card.querySelector('.pop-when').textContent = whenLine();
            card.querySelector('[data-act=more]').hidden = k === 'birthday';
            title.placeholder = k === 'birthday' ? 'Name' : 'Add title, or “20:00 Choir”';
            if (k === 'birthday' && hasContacts()) loadContactChoices(card.querySelector('[data-bday-how]'));
        };
        const draft = () => {
            const named = timeInTitle(title.value);
            if (kind === 'task') {
                return { place: card.querySelector('[data-task-target]').value,
                    item: { title: named ? named.title : title.value.trim(), date: ymd(start),
                        time: named ? `${pad(named.from.h)}:${pad(named.from.m)}` : (allDay ? '' : hm(start)), notes: '', done: false } };
            }
            const [pid, cid] = card.querySelector('[data-target]').value.split('|');
            let s = start; let e = end; let whole = allDay;
            if (named) { ({ start: s, end: e } = withTitleTime(named, start, end, allDay)); whole = false; }
            return { place: pid, item: { title: named ? named.title : title.value.trim(), calendar: cid, all_day: whole,
                start: whole ? ymd(s) : stamp(s), end: whole ? ymd(addDays(e, -1)) : stamp(e), color: '', repeat: '', location: '', notes: '' } };
        };
        const submit = async () => {
            if (kind === 'birthday') { await saveBirthday(card, title, start); return; }
            const { place: pid, item } = draft();
            if (kind === 'task' && !item.title) { title.focus(); toast('Give the task a title.', 'error'); return; }
            try {
                await save(pid, kind, item);
                closePop();
            } catch (err) { toast(err.message, 'error'); }
        };
        card.addEventListener('click', (e) => {
            const k = e.target.closest('[data-kind]');
            if (k) { setKind(k.dataset.kind); title.focus(); return; }
            const act = e.target.closest('[data-act]');
            if (!act) return;
            if (act.dataset.act === 'close') closePop();
            else if (act.dataset.act === 'save') submit();
            else if (act.dataset.act === 'more') {
                const { place: pid, item } = draft();
                closePop();
                if (kind === 'task') taskDialog(placeOf(pid), item, true); else eventDialog(placeOf(pid), item, true);
            }
        });
        title.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } if (e.key === 'Escape') closePop(); });
        place(card, anchor, at);
        title.focus();
    }

    // ── Dialogs: the whole event, a task, a calendar ───────────────────────
    function dialog(html, wide) {
        const wrap = document.createElement('div');
        wrap.className = 'dialog-wrap cal-dialog-wrap';
        wrap.innerHTML = `<div class="dialog cal-dialog${wide ? ' wide' : ''}" role="dialog" aria-modal="true">${html}</div>`;
        document.body.appendChild(wrap);
        const close = () => { wrap.remove(); document.removeEventListener('keydown', onEsc, true); root.querySelectorAll('.ev.ghost').forEach((g) => g.remove()); };
        const onEsc = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
        document.addEventListener('keydown', onEsc, true);
        wrap.addEventListener('pointerdown', (e) => { if (e.target === wrap) close(); });
        wrap.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', close));
        return { el: wrap.firstElementChild, close };
    }

    function swatches(name, chosen, withDefault) {
        return `<div class="swatches" role="radiogroup">${withDefault ? `<label title="Colour of the calendar"><input type="radio" name="${name}" value=""${chosen ? '' : ' checked'}><span class="sw default"></span></label>` : ''}
            ${colors.map((c) => `<label><input type="radio" name="${name}" value="${esc(c)}"${chosen === c ? ' checked' : ''}><span class="sw" style="--c:${esc(c)}"></span></label>`).join('')}</div>`;
    }

    function eventDialog(placeObj, ev, isNew) {
        const existing = !isNew && ev.id;
        const s = parse(ev.start);
        const e0 = parse(ev.end);
        const target = placeObj ? calKey(placeObj, calOf(placeObj, ev.calendar)) : defaultTarget();
        const d = dialog(`
            <input class="ed-title" name="title" placeholder="Add title" value="${esc(ev.title || '')}" maxlength="300" aria-label="Title">
            <div class="ed-when">
                <input type="date" name="sd" value="${ymd(s)}" aria-label="Start day">
                <input type="time" name="st" value="${ev.all_day ? '09:00' : hm(s)}" step="900" aria-label="Start time">
                <span>to</span>
                <input type="time" name="et" value="${ev.all_day ? '10:00' : hm(e0)}" step="900" aria-label="End time">
                <input type="date" name="ed" value="${ymd(e0)}" aria-label="End day">
            </div>
            <div class="ed-line">
                <label class="ed-check"><input type="checkbox" name="all_day"${ev.all_day ? ' checked' : ''}> All day</label>
                <select name="repeat" aria-label="Repeat">${REPEATS.map(([v, label]) => `<option value="${v}"${(ev.repeat || '') === v ? ' selected' : ''}>${label}</option>`).join('')}</select>
                <label class="ed-until" ${ev.repeat ? '' : 'hidden'}>until <input type="date" name="until" value="${esc(ev.until || '')}" aria-label="Last day"></label>
            </div>
            <div class="ed-field">${icon('calendar')}<select name="target" aria-label="Calendar">${targetOptions(target, false)}</select></div>
            <div class="ed-field">${icon('palette')}${swatches('color', ev.color || '', true)}</div>
            <div class="ed-field">${icon('map-pin')}<input name="location" placeholder="Add location" value="${esc(ev.location || '')}" maxlength="300"></div>
            <div class="ed-field top">${icon('notes')}<textarea name="notes" placeholder="Add description" rows="4" maxlength="8000">${esc(ev.notes || '')}</textarea></div>
            ${existing && ev.repeat ? '<p class="ed-hint">Changes apply to every repeat of this event.</p>' : ''}
            <div class="actions">${existing ? '<button type="button" class="btn ghost danger" data-del>Delete</button><span class="grow"></span>' : ''}
                <button type="button" class="btn" data-close>Cancel</button><button type="button" class="btn primary" data-save>Save</button></div>`, true);
        const f = (n) => d.el.querySelector(`[name="${n}"]`);
        const sync = () => {
            const all = f('all_day').checked;
            f('st').hidden = all;
            f('et').hidden = all;
            d.el.querySelector('.ed-until').hidden = !f('repeat').value;
        };
        f('all_day').addEventListener('change', sync);
        f('repeat').addEventListener('change', sync);
        // Moving the start keeps the length, like Google.
        let lastStart = parse(`${f('sd').value}T${f('st').value}`);
        const keepLength = () => {
            const now = parse(`${f('sd').value}T${f('st').value}`);
            const end = parse(`${f('ed').value}T${f('et').value}`);
            if (!now || !end || !lastStart) return;
            const moved = addMin(end, (now - lastStart) / 60000);
            f('ed').value = ymd(moved);
            f('et').value = hm(moved);
            lastStart = now;
        };
        f('sd').addEventListener('change', keepLength);
        f('st').addEventListener('change', keepLength);
        sync();
        f('title').focus();
        const del = d.el.querySelector('[data-del]');
        if (del) del.addEventListener('click', async () => {
            try { await remove(placeObj.id, 'event', ev.id); d.close(); toast(ev.repeat ? 'Event deleted, with all its repeats.' : 'Event deleted.'); } catch (err) { toast(err.message, 'error'); }
        });
        d.el.querySelector('[data-save]').addEventListener('click', async () => {
            const all = f('all_day').checked;
            const [pid, cid] = f('target').value.split('|');
            let named = timeInTitle(f('title').value);
            let [sDay, sTime, eDay, eTime] = [f('sd').value, f('st').value, f('ed').value, f('et').value];
            let whole = all;
            if (named && parse(sDay)) {
                const from = parse(`${sDay}T${sTime || '00:00'}`);
                const till = parse(`${eDay || sDay}T${eTime || sTime || '00:00'}`) || from;
                const moved = withTitleTime(named, from, till, all);
                [sDay, sTime, eDay, eTime] = [ymd(moved.start), hm(moved.start), ymd(moved.end), hm(moved.end)];
                whole = false;
            } else named = null;
            const item = {
                ...(existing && pid === placeObj.id ? { id: ev.id } : {}),
                title: named ? named.title : f('title').value.trim(), calendar: cid, all_day: whole,
                start: whole ? sDay : `${sDay}T${sTime}`,
                end: whole ? eDay : `${eDay}T${eTime}`,
                repeat: f('repeat').value, until: f('repeat').value ? f('until').value : '',
                color: (d.el.querySelector('[name="color"]:checked') || {}).value || '',
                location: f('location').value.trim(), notes: f('notes').value,
            };
            try {
                await save(pid, 'event', item);
                if (existing && pid !== placeObj.id) await remove(placeObj.id, 'event', ev.id);   // moved to another place
                d.close();
            } catch (err) { toast(err.message, 'error'); }
        });
    }

    function taskDialog(placeObj, task, isNew) {
        const existing = !isNew && task.id;
        const own = placeObj || writable().find((p) => p.own) || writable()[0];
        const d = dialog(`
            <input class="ed-title" name="title" placeholder="Add title" value="${esc(task.title || '')}" maxlength="300" aria-label="Title">
            <div class="ed-when">
                <input type="date" name="date" value="${esc(task.date || '')}" aria-label="Day">
                <input type="time" name="time" value="${esc(task.time || '')}" step="900" aria-label="Time">
                <span class="ed-hint">Day and time are up to you.</span>
            </div>
            ${writable().length > 1 ? `<div class="ed-field">${icon('tasks')}<select name="target" aria-label="Task list">${targetOptions(own.id, true)}</select></div>` : ''}
            <div class="ed-field top">${icon('notes')}<textarea name="notes" placeholder="Add details" rows="4" maxlength="8000">${esc(task.notes || '')}</textarea></div>
            <label class="ed-check"><input type="checkbox" name="done"${task.done ? ' checked' : ''}> Done</label>
            <div class="actions">${existing ? '<button type="button" class="btn ghost danger" data-del>Delete</button><span class="grow"></span>' : ''}
                <button type="button" class="btn" data-close>Cancel</button><button type="button" class="btn primary" data-save>Save</button></div>`);
        const f = (n) => d.el.querySelector(`[name="${n}"]`);
        f('title').focus();
        const del = d.el.querySelector('[data-del]');
        if (del) del.addEventListener('click', async () => {
            try { await remove(own.id, 'task', task.id); d.close(); toast('Task deleted.'); } catch (err) { toast(err.message, 'error'); }
        });
        d.el.querySelector('[data-save]').addEventListener('click', async () => {
            const pid = f('target') ? f('target').value : own.id;
            const item = { ...(existing && pid === own.id ? { id: task.id } : {}), title: f('title').value.trim(),
                date: f('date').value, time: f('date').value ? f('time').value : '', notes: f('notes').value, done: f('done').checked };
            try {
                await save(pid, 'task', item);
                if (existing && pid !== own.id) await remove(own.id, 'task', task.id);
                d.close();
            } catch (err) { toast(err.message, 'error'); }
        });
    }

    // The same calendars on a phone or computer, over CalDAV (backend/hub_caldav.py).
    function syncDialog() {
        const user = (H.me() || {}).user || '';
        const secure = location.protocol === 'https:';
        const server = secure ? location.host : `${location.hostname}:9443`;
        const d = dialog(`
            <h2>On your phone and computer</h2>
            <p>Your calendars and tasks sync with the calendar app you already use. Changes show up on both sides.</p>
            <dl class="sync-facts">
                <dt>Server</dt><dd><code>${esc(server)}</code></dd>
                <dt>Name</dt><dd><code>${esc(user)}</code></dd>
                <dt>Password</dt><dd>The one you sign in with here</dd>
            </dl>
            <details class="sync-how"><summary>iPhone, iPad and Mac</summary>
                <p>Settings › Apps › Calendar › Calendar Accounts › Add Account › Other › <b>Add CalDAV Account</b>. Enter the server, name and password. Tasks show up in Reminders.</p></details>
            <details class="sync-how"><summary>Android</summary>
                <p>Install <b>DAVx⁵</b> (free in F-Droid, also in the Play Store), add an account with “URL and user name” and enter <code>https://${esc(server)}/</code>. Calendars appear in the phone's calendar app, tasks in Tasks.org or jtx Board.</p></details>
            <details class="sync-how"><summary>Thunderbird and Outlook</summary>
                <p>Thunderbird: New Calendar › On the Network, location <code>https://${esc(server)}/dav/${esc(user)}/</code>. Outlook needs a CalDAV add-in.</p></details>
            <p class="sync-note">The phone has to trust this NAS once: open <a href="/alvaos-ca.crt">its certificate</a> on the phone and install it (on an iPhone also turn it on under Settings › General › About › Certificate Trust Settings). Away from home this works over remote access.</p>
            <div class="actions"><span class="grow"></span><button type="button" class="btn primary" data-close>Done</button></div>`);
        d.el.querySelector('[data-close]').focus();
    }

    function calendarDialog(placeObj, cal) {
        const d = dialog(`
            <h2>${cal ? 'Change calendar' : 'New calendar'}</h2>
            <p>${placeObj.own ? 'Only you see your calendars.' : `Everyone who may open “${esc(placeObj.name)}” sees it.`}</p>
            <input name="name" placeholder="Name, like Work or Sport" value="${esc(cal ? cal.name : '')}" maxlength="80" aria-label="Name">
            ${swatches('color', cal ? cal.color : colors[(placeObj.calendars.length * 3) % colors.length], false)}
            <div class="actions">${cal && placeObj.calendars.length > 1 ? '<button type="button" class="btn ghost danger" data-del>Delete</button><span class="grow"></span>' : ''}
                <button type="button" class="btn" data-close>Cancel</button><button type="button" class="btn primary" data-save>Save</button></div>`);
        const name = d.el.querySelector('[name="name"]');
        name.focus();
        const del = d.el.querySelector('[data-del]');
        if (del) del.addEventListener('click', async () => {
            const count = placeObj.events.filter((e) => e.calendar === cal.id).length;
            del.textContent = count ? `Delete it and its ${count} event${count === 1 ? '' : 's'}?` : 'Delete it?';
            if (!del.dataset.sure) { del.dataset.sure = '1'; return; }
            try { await remove(placeObj.id, 'calendar', cal.id); d.close(); toast(`${cal.name} deleted.`); } catch (err) { toast(err.message, 'error'); }
        });
        const submit = async () => {
            try {
                await save(placeObj.id, 'calendar', { ...(cal ? { id: cal.id } : {}), name: name.value.trim(),
                    color: (d.el.querySelector('[name="color"]:checked') || {}).value });
                d.close();
            } catch (err) { toast(err.message, 'error'); }
        };
        name.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
        d.el.querySelector('[data-save]').addEventListener('click', submit);
    }

    // The colour of the read-only Birthdays calendar is the person's own choice, kept in this browser.
    function birthdayColorDialog() {
        const place = placeOf('birthdays');
        if (!place) return;
        const d = dialog(`
            <h2>Birthdays</h2>
            <p>The birthdays of your contacts. Choose the colour they have in your calendar.</p>
            ${swatches('color', place.calendars[0].color, false)}
            <div class="actions"><button type="button" class="btn" data-close>Cancel</button><button type="button" class="btn primary" data-save>Save</button></div>`);
        d.el.querySelector('[data-save]').addEventListener('click', () => {
            const color = (d.el.querySelector('[name="color"]:checked') || {}).value;
            if (color) { place.calendars[0].color = color; store.set('bdaycolor', color); }
            d.close();
            render();
        });
    }

    // ── Tasks, next to the calendar ────────────────────────────────────────
    let taskPlace = '';
    function renderTasks() {
        const panel = $c('#cal-tasks');
        panel.hidden = !tasksOpen;
        root.classList.toggle('with-tasks', tasksOpen);
        if (!tasksOpen) return;
        const lists = places.filter((p) => p.writable || p.tasks.length);
        const current = placeOf(taskPlace) || lists.find((p) => p.own) || lists[0];
        if (!current) {
            panel.innerHTML = `<header class="tk-head"><h2>Tasks</h2><button type="button" class="icon-btn" data-tk="close" aria-label="Close">${icon('x')}</button></header><p class="tk-empty">There is no task list for you yet.</p>`;
            return;
        }
        taskPlace = current.id;
        const open = current.tasks.filter((t) => !t.done).sort((a, b) => (a.date || '9999').localeCompare(b.date || '9999') || (a.time || '').localeCompare(b.time || ''));
        const done = current.tasks.filter((t) => t.done);
        const today = ymd(new Date());
        const row = (t) => `
            <div class="tk${t.done ? ' done' : ''}" data-task="${esc(t.id)}">
                <button type="button" class="tk-check" data-tk="toggle" aria-label="${t.done ? 'Mark as not done' : 'Mark as done'}"${current.writable ? '' : ' disabled'}>${icon(t.done ? 'check-circle' : 'circle')}</button>
                <button type="button" class="tk-body" data-tk="open">
                    <span class="tk-title">${esc(t.title)}</span>
                    ${t.notes ? `<span class="tk-notes">${esc(t.notes.split('\n')[0])}</span>` : ''}
                    ${t.date ? `<span class="tk-date${!t.done && t.date < today ? ' late' : ''}">${esc(t.date === today ? 'Today' : fmt(parse(t.date), { weekday: 'short', day: 'numeric', month: 'short' }))}${t.time ? `, ${esc(t.time)}` : ''}</span>` : ''}
                </button>
            </div>`;
        panel.innerHTML = `
            <header class="tk-head">
                <div><div class="tk-kicker">Tasks</div>
                ${lists.length > 1 ? `<select class="tk-list" data-tk-list aria-label="Task list">${lists.map((p) => `<option value="${esc(p.id)}"${p.id === current.id ? ' selected' : ''}>${esc(p.own ? 'My tasks' : p.name)}</option>`).join('')}</select>` : '<h2>My tasks</h2>'}</div>
                <button type="button" class="icon-btn" data-tk="close" aria-label="Close tasks">${icon('x')}</button>
            </header>
            ${current.writable ? (addingTask
                ? `<div class="tk-new"><span class="tk-check">${icon('circle')}</span><input id="tk-new" placeholder="Title" maxlength="300" aria-label="New task"></div>`
                : `<button type="button" class="tk-add" data-tk="add">${icon('check-circle')}<span>Add a task</span></button>`) : ''}
            <div class="tk-list-open">${open.map(row).join('') || (addingTask ? '' : '<div class="tk-empty">No tasks yet. Add one, give it a day, and it shows in the calendar too.</div>')}</div>
            ${done.length ? `<details class="tk-done"${store.get('done_open', false) ? ' open' : ''}><summary>Completed (${done.length})</summary>${done.map(row).join('')}</details>` : ''}`;
        const input = panel.querySelector('#tk-new');
        if (input) {
            input.focus();
            input.addEventListener('blur', () => { setTimeout(() => { if (addingTask && document.activeElement !== input) { addingTask = false; renderTasks(); } }, 150); });
        }
        const listSel = panel.querySelector('[data-tk-list]');
        if (listSel) listSel.addEventListener('change', () => { taskPlace = listSel.value; renderTasks(); });
        const det = panel.querySelector('.tk-done');
        if (det) det.addEventListener('toggle', () => store.set('done_open', det.open));
    }
    async function onTasksClick(e) {
        const b = e.target.closest('[data-tk]');
        if (!b) return;
        const act = b.dataset.tk;
        const current = placeOf(taskPlace);
        if (act === 'close') { tasksOpen = false; store.set('tasks', false); render(); return; }
        if (act === 'add') { addingTask = true; renderTasks(); return; }
        const t = current && current.tasks.find((x) => x.id === b.closest('[data-task]').dataset.task);
        if (!t) return;
        if (act === 'toggle') {
            try { await save(current.id, 'task', { ...t, done: !t.done }); if (!t.done) toast('Task done.'); } catch (err) { toast(err.message, 'error'); }
        } else if (act === 'open' && current.writable) taskDialog(current, t);
    }
    async function onTasksKey(e) {
        if (e.target.id !== 'tk-new') return;
        if (e.key === 'Escape') { addingTask = false; renderTasks(); return; }
        if (e.key !== 'Enter' || !e.target.value.trim()) return;
        const title = e.target.value.trim();
        e.target.value = '';
        try { await save(taskPlace, 'task', { title, date: '', time: '', notes: '', done: false }); } catch (err) { toast(err.message, 'error'); }
    }

    // ── Keys, like Google Calendar ─────────────────────────────────────────
    function onKey(e) {
        if (!root || root.hidden || e.defaultPrevented) return;
        if (document.querySelector('.cal-dialog-wrap')) return;
        if (e.key === 'Escape' && pop) { closePop(); return; }
        if (e.target.closest('input, textarea, select') || e.ctrlKey || e.metaKey || e.altKey) return;
        const map = { d: 'day', w: 'week', m: 'month', a: 'schedule' };
        if (map[e.key]) setView(map[e.key]);
        else if (e.key === 't') $c('#cal-today').click();
        else if (e.key === 'j' || e.key === 'n' || e.key === 'ArrowRight') move(1);
        else if (e.key === 'k' || e.key === 'p' || e.key === 'ArrowLeft') move(-1);
        else if (e.key === 'c') { e.preventDefault(); $c('#cal-create').click(); }
        else return;
        e.preventDefault();
    }

    window.addEventListener('hub-signout', () => {
        closePop();
        places = [];
        loaded = false;
        taskPlace = '';
    });

    window.HubApps = window.HubApps || {};
    window.HubApps.calendar = {
        show(el) {
            root = el;
            if (!built) build();
            render();
            refresh();
        },
    };
})();
