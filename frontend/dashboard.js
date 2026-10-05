// AlvaOS dashboard: the calm overview.
//
// One status line answers "is it fine, and what do I do next?". Four cards
// (storage, backup, apps, updates) each lead to their page. Live resources are
// small and quiet, with history and hardware details one click away.
//
// Everything the status line says comes from a list of "issues" that the cards
// report. Each issue has a level, one sentence and the page where it is fixed:
//   bad   - action is required now (the only level that is red)
//   warn  - worth a look soon
//   setup - a next step on a NAS that is not fully set up yet (calm blue)
//   info  - good to know, e.g. an update is ready
(function () {
    const API = '/api/v1';
    const SYSTEM_POLL_MS = 5000;
    const SYSTEM_POLL_HIDDEN_MS = 30000;
    const SLOW_POLL_MS = 30000;
    const SLOW_POLL_HIDDEN_MS = 120000;
    const HISTORY_KEY = 'alvaos_dashboard_history_v2';
    const HISTORY_MAX = 120;
    const LEVEL_ORDER = { bad: 0, warn: 1, setup: 2, info: 3 };

    const esc = window.escapeHtml || ((v) => String(v ?? ''));
    const issues = {};          // source -> [issue]
    let lastSystem = null;      // last /system/info payload
    let lastCounters = null;    // { ts, net, disk } for rate calculation
    let history = [];
    let systemTimer = null;
    let slowTimer = null;
    let systemBusy = false;
    let slowBusy = false;
    let slowLoaded = false;     // no verdict before alerts, backup and apps are known

    // ── Small helpers ────────────────────────────────────────────────────────

    function $(id) { return document.getElementById(id); }

    function setText(id, value) {
        const el = $(id);
        if (el) el.textContent = value;
    }

    function plural(n, one, many) {
        return `${n} ${n === 1 ? one : (many || `${one}s`)}`;
    }

    function num(value) {
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
    }

    function formatBytes(bytes) {
        const value = Number(bytes) || 0;
        const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
        let i = 0;
        let v = value;
        while (v >= 1000 && i < units.length - 1) { v /= 1000; i += 1; }
        const digits = v >= 100 || i === 0 ? 0 : (v >= 10 ? 1 : 2);
        return `${v.toFixed(digits)} ${units[i]}`;
    }

    function formatGb(gb) {
        return formatBytes((Number(gb) || 0) * 1024 ** 3);
    }

    function formatRate(bytesPerSec) {
        if (bytesPerSec === null) return '-';
        if (bytesPerSec < 1000) return 'idle';
        return `${formatBytes(bytesPerSec)}/s`;
    }

    function formatUptime(hours) {
        const total = Math.max(0, Number(hours) || 0);
        const days = Math.floor(total / 24);
        const rest = Math.floor(total % 24);
        if (days > 0) return `${plural(days, 'day')}${rest ? `, ${plural(rest, 'hour')}` : ''}`;
        if (rest > 0) return plural(rest, 'hour');
        return 'less than an hour';
    }

    // "2 hours ago", "yesterday at 02:00", "on 3 Sep"
    function timeAgo(value) {
        const t = new Date(value).getTime();
        if (!Number.isFinite(t)) return '';
        const sec = Math.round((Date.now() - t) / 1000);
        if (sec < 60) return 'just now';
        const min = Math.round(sec / 60);
        if (min < 60) return `${plural(min, 'minute')} ago`;
        const hours = Math.round(min / 60);
        if (hours < 24) return `${plural(hours, 'hour')} ago`;
        const days = Math.round(hours / 24);
        if (days === 1) return 'yesterday';
        if (days < 14) return `${days} days ago`;
        return `on ${new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}`;
    }

    // "today at 02:00", "tomorrow at 02:00", "on Mon 6 Oct"
    function whenNext(value) {
        const d = new Date(value);
        if (!Number.isFinite(d.getTime())) return '';
        const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
        const dayDiff = Math.round((startOfDay(d) - startOfDay(new Date())) / 86400000);
        if (d.getTime() < Date.now()) return 'soon';
        if (dayDiff === 0) return `today at ${time}`;
        if (dayDiff === 1) return `tomorrow at ${time}`;
        return `on ${d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}`;
    }

    function safeRoute(route) {
        const value = String(route || '').trim();
        return /^[a-z0-9_-]+\.html(#[a-z0-9=_-]*)?$/i.test(value) ? value : '';
    }

    async function getJson(path) {
        try {
            const response = await fetch(`${API}${path}`);
            if (response.status === 401) {
                window.location.href = '/login.html';
                return null;
            }
            if (!response.ok) return null;
            return await response.json();
        } catch (_error) {
            return null;
        }
    }

    function renderIcons(root) {
        if (window.renderAlvaIcons) window.renderAlvaIcons(root);
    }

    function icon(name) {
        return window.alvaIcon ? window.alvaIcon(name, '', 'aria-hidden="true"') : '';
    }

    // ── Cards ────────────────────────────────────────────────────────────────

    // state: ok | warn | bad | setup | neutral | loading | unknown
    function setCard(key, { state, label, value, sub, listHtml }) {
        const card = $(`card-${key}`);
        if (!card) return;
        card.dataset.state = state;
        setText(`card-${key}-state`, label);
        if (value !== undefined) setText(`card-${key}-value`, value);
        if (sub !== undefined) setText(`card-${key}-sub`, sub);
        const list = $(`card-${key}-list`);
        if (list && listHtml !== undefined) list.innerHTML = listHtml;
    }

    function setIssues(source, list) {
        issues[source] = Array.isArray(list) ? list : [];
        renderStatusLine();
    }

    function renderStorage(system, alertsPayload) {
        // The backup disk is a pool too, but it is unplugged most of the time
        // and holds copies, not the files: it is shown on the Backup page.
        const pools = (Array.isArray(system?.storage_pools) ? system.storage_pools : []).filter((p) => !p.is_backup_disk);
        if (pools.length === 0) {
            setCard('storage', {
                state: 'setup',
                label: 'Not set up',
                value: 'No storage yet',
                sub: 'Combine one or more disks into a pool to start storing files.',
                listHtml: '',
            });
            setIssues('storage', [{
                level: 'setup',
                title: 'Next step: set up storage',
                detail: 'Choose the disks AlvaOS may use for your files.',
                href: 'storage.html',
                action: 'Set up storage',
            }]);
            return;
        }

        const mounted = pools.filter((p) => p.mounted && num(p.total_gb) !== null);
        const offline = pools.filter((p) => !p.mounted);
        const total = mounted.reduce((sum, p) => sum + p.total_gb, 0);
        const free = mounted.reduce((sum, p) => sum + (num(p.free_gb) || 0), 0);
        const fullest = mounted.reduce((max, p) => Math.max(max, num(p.percent) || 0), 0);

        // A degraded pool only shows up in the alerts, so read it from there.
        const alerts = Array.isArray(alertsPayload?.alerts) ? alertsPayload.alerts : [];
        const degraded = alerts.some((a) => /^pool-.*-degraded$/.test(String(a.id || '')));

        let state = 'ok';
        let label = 'Healthy';
        if (offline.length > 0 || degraded) {
            state = 'bad';
            label = degraded ? 'Disk missing' : 'Offline';
        } else if (fullest >= 95) {
            state = 'bad';
            label = 'Almost full';
        } else if (fullest >= 85) {
            state = 'warn';
            label = 'Filling up';
        }

        const rows = pools.map((pool) => {
            const name = esc(pool.name || pool.id || 'pool');
            if (!pool.mounted || num(pool.percent) === null) {
                return `<div class="ov-row"><span class="ov-row-name">${name}</span><span class="ov-row-meta">offline</span></div>`;
            }
            const pct = Math.max(0, Math.min(100, num(pool.percent)));
            const barState = pct >= 95 ? 'bad' : (pct >= 85 ? 'warn' : '');
            return `
                <div class="ov-row">
                    <span class="ov-row-name">${name}</span>
                    <span class="ov-row-meta">${esc(formatGb(pool.free_gb))} free</span>
                </div>
                <div class="ov-bar" role="img" aria-label="${name}: ${pct.toFixed(0)}% used"><span class="${barState}" style="width:${pct}%"></span></div>`;
        }).join('');

        // Data checks (btrfs scrub): the most recent one over all pools, and
        // the worst thing any of them found.
        const checks = pools.map((p) => p.last_check).filter((c) => c && typeof c === 'object');
        const running = checks.find((c) => c.state === 'running');
        const damaged = checks.some((c) => Number(c.uncorrectable) > 0);
        const repaired = checks.reduce((sum, c) => sum + (Number(c.errors) || 0), 0);
        const latest = checks.map((c) => c.started_at).filter(Boolean).sort().pop();
        let checkLine = '';
        if (running) {
            checkLine = `Checking data now${num(running.percent) !== null ? ` (${Math.round(running.percent)}%)` : ''}`;
        } else if (latest) {
            checkLine = `Data checked ${timeAgo(latest)}: ${damaged ? 'damaged files found' : repaired ? `${repaired} problem${repaired === 1 ? '' : 's'} repaired` : 'no problems'}`;
        } else if (checks.length) {
            checkLine = 'Data not checked yet: the first check runs at night';
        }
        if (damaged && state !== 'bad') {
            state = 'bad';
            label = 'Damaged files';
        } else if (repaired && state === 'ok') {
            state = 'warn';
            label = 'Check disks';
        }
        const checkHtml = checkLine
            ? `<div class="ov-row ov-hint"><span class="ov-row-name"><span class="ov-dot ${damaged ? 'bad' : repaired ? 'warn' : running ? 'info' : 'ok'}"></span>${esc(checkLine)}</span></div>`
            : '';

        setCard('storage', {
            state,
            label,
            value: mounted.length > 0 ? `${formatGb(free)} free` : 'Storage is offline',
            sub: mounted.length > 0
                ? `of ${formatGb(total)} in ${plural(pools.length, 'pool')}`
                : 'Your files cannot be reached right now.',
            listHtml: rows + checkHtml,
        });
        // Problems with pools come in through the alerts (with the right
        // wording and link), so the card adds no issues of its own here.
        setIssues('storage', []);
    }

    function renderBackup(statusPayload, buddyPayload) {
        if (!statusPayload) {
            setCard('backup', { state: 'unknown', label: 'Unknown', value: 'Could not check backups', sub: 'Open Backup to see details.', listHtml: '' });
            setIssues('backup', []);
            return;
        }
        const status = statusPayload.status || {};
        const peers = Array.isArray(buddyPayload?.peers) ? buddyPayload.peers : [];
        const lastRun = status.pool_last_run_at;
        const nextRun = status.pool_next_run_at;
        const error = String(status.pool_last_error || '').trim();
        const found = [];

        let state = 'ok';
        let label = 'Protected';
        let value;
        let sub;

        if (error) {
            state = 'bad';
            label = 'Failed';
            value = lastRun ? `Last backup failed ${timeAgo(lastRun)}` : 'The last backup failed';
            sub = error.length > 90 ? `${error.slice(0, 90)}...` : error;
            found.push({ level: 'bad', title: 'The last backup did not finish', detail: 'Your files are not protected until it runs again.', href: 'backup.html', action: 'Open backup' });
        } else if (!lastRun && !nextRun) {
            state = 'setup';
            label = 'Not set up';
            value = 'No backups yet';
            sub = 'Turn on automatic backups to keep older versions of your files.';
            found.push({ level: 'setup', title: 'Next step: turn on backups', detail: 'Keep older versions of your files, and a copy at a friend\'s NAS.', href: 'backup.html', action: 'Set up backup' });
        } else {
            value = lastRun ? `Last backup ${timeAgo(lastRun)}` : 'First backup is scheduled';
            sub = nextRun ? `Next one ${whenNext(nextRun)}` : 'Automatic backups are off';
            if (!nextRun) {
                state = 'neutral';
                label = 'Manual';
            }
        }

        const peerRows = peers.slice(0, 3).map((peer) => {
            const online = peer?.runtime?.online === true && peer?.runtime?.connected === true;
            const sending = peer?.policy?.enabled === true;
            return `<div class="ov-row"><span class="ov-row-name"><span class="ov-dot ${online ? 'ok' : 'off'}"></span>${esc(peer.name || peer.node_id || 'Buddy')}</span><span class="ov-row-meta">${online ? 'online' : 'offline'}${sending ? '' : ', not sending'}</span></div>`;
        }).join('');
        const buddyHtml = peers.length
            ? peerRows
            : (state === 'ok' || state === 'neutral' ? '<div class="ov-row ov-hint"><span>No buddy yet: an offsite copy at a friend\'s NAS.</span></div>' : '');

        setCard('backup', { state, label, value, sub, listHtml: buddyHtml });
        setIssues('backup', found);
    }

    function isContainerForApp(container, appId) {
        const labels = String(container?.Labels || '');
        if (labels.includes(`com.docker.compose.project=alvaos-${appId}`)) return true;
        return String(container?.Names || '').split(',').some((n) => n.trim().includes(`alvaos-${appId}`));
    }

    function renderApps(appsPayload, containersPayload) {
        const apps = Array.isArray(appsPayload?.apps) ? appsPayload.apps : null;
        if (apps === null) {
            setCard('apps', { state: 'unknown', label: 'Unknown', value: 'Could not check apps', sub: 'Open Apps to see details.', listHtml: '' });
            setIssues('apps', []);
            return;
        }
        if (apps.length === 0) {
            setCard('apps', { state: 'neutral', label: 'None yet', value: 'No apps installed', sub: 'Media server, photo backup, password manager and more, in one click.', listHtml: '' });
            setIssues('apps', []);
            return;
        }

        const containers = Array.isArray(containersPayload?.containers) ? containersPayload.containers : null;
        const rows = apps.map((app) => {
            const id = String(app.app_id || '');
            const own = containers ? containers.filter((c) => isContainerForApp(c, id)) : [];
            const running = own.filter((c) => c?.State === 'running').length;
            let status = 'unknown';
            if (containers) {
                if (own.length > 0 && running === own.length) status = 'running';
                else if (running > 0) status = 'partial';
                else status = 'stopped';
            }
            return { id, name: app.name || id, status, update: app.update_available === true };
        });

        const running = rows.filter((r) => r.status === 'running').length;
        const stopped = rows.filter((r) => r.status === 'stopped').length;
        const partial = rows.filter((r) => r.status === 'partial');
        const updates = rows.filter((r) => r.update).length;

        // A stopped app is a choice, not a problem. Only an app that is half
        // running (one of its containers died) is worth a look.
        let state = 'ok';
        let label = 'Running';
        if (partial.length) {
            state = 'warn';
            label = 'Check';
        } else if (!containers) {
            state = 'unknown';
            label = 'Unknown';
        } else if (running === 0) {
            state = 'neutral';
            label = 'All stopped';
        }

        const parts = [`${running} running`];
        if (stopped) parts.push(`${stopped} stopped`);
        if (updates) parts.push(plural(updates, 'update') + ' ready');

        const shown = rows.slice(0, 4);
        const statusText = { running: 'running', stopped: 'stopped', partial: 'not fully running', unknown: '' };
        const dotState = { running: 'ok', stopped: 'off', partial: 'warn', unknown: 'off' };
        const list = shown.map((r) => `<div class="ov-row"><span class="ov-row-name"><span class="ov-dot ${dotState[r.status]}"></span>${esc(r.name)}</span><span class="ov-row-meta">${statusText[r.status]}</span></div>`).join('')
            + (rows.length > shown.length ? `<div class="ov-row ov-hint"><span>and ${rows.length - shown.length} more</span></div>` : '');

        setCard('apps', {
            state,
            label,
            value: plural(apps.length, 'app') + ' installed',
            sub: containers ? parts.join(', ') : 'Could not check which apps are running.',
            listHtml: list,
        });
        setIssues('apps', partial.map((r) => ({
            level: 'warn',
            title: `${r.name} is not fully running`,
            detail: 'Part of the app stopped. Restarting it usually helps.',
            href: 'apps.html',
            action: 'Open apps',
        })));
    }

    function renderUpdates(available, version) {
        const current = lastSystem?.version ? `AlvaOS ${lastSystem.version}` : 'AlvaOS';
        if (available) {
            setCard('updates', {
                state: 'info',
                label: 'Ready',
                value: `${version ? `AlvaOS ${version}` : 'An update'} is ready`,
                sub: 'Installing takes a few minutes. Your files stay where they are.',
            });
            setIssues('updates', [{
                level: 'info',
                title: `${version ? `AlvaOS ${version}` : 'An update'} is ready to install`,
                detail: 'See what changed and install it when it suits you.',
                href: 'updates.html',
                action: 'View update',
            }]);
        } else {
            setCard('updates', {
                state: 'ok',
                label: 'Up to date',
                value: `${current} is up to date`,
                sub: 'AlvaOS looks for updates on its own.',
            });
            setIssues('updates', []);
        }
    }

    // notifications.js decides whether an update is available and tells us.
    window.alvaosDashboardUpdates = renderUpdates;

    function alertsToIssues(payload) {
        const alerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
        return alerts.map((a) => {
            const severity = String(a.severity || '').toLowerCase();
            let href = safeRoute(a.route);
            let action = a.action_label || 'Open';
            // Load alerts point at the dashboard itself: open the details instead.
            if (href === 'index.html') {
                href = '#live-resources';
                action = 'Show details';
            }
            return {
                level: severity === 'critical' ? 'bad' : (severity === 'warning' ? 'warn' : 'info'),
                title: a.title || 'Something needs a look',
                detail: a.message || '',
                href,
                action,
            };
        });
    }

    // ── Status line ──────────────────────────────────────────────────────────

    function allIssues() {
        return ['alerts', 'storage', 'backup', 'apps', 'updates']
            .flatMap((key) => issues[key] || [])
            .sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level]);
    }

    function renderStatusLine() {
        const line = $('status-line');
        if (!line || !lastSystem || !slowLoaded) return;

        const list = allIssues();
        const top = list[0];
        const iconEl = $('status-line-icon');
        const action = $('status-line-action');
        const host = lastSystem?.network?.hostname || 'Your NAS';
        const uptime = formatUptime(lastSystem?.system?.uptime_hours);

        let level = 'ok';
        let iconName = 'circle-check-big';
        if (top) {
            level = top.level;
            iconName = { bad: 'circle-alert', warn: 'triangle-alert', setup: 'circle-arrow-up', info: 'info' }[top.level] || 'info';
        }
        line.dataset.level = level;
        iconEl.innerHTML = icon(iconName);
        renderIcons(iconEl);

        if (!top) {
            setText('status-line-title', 'Everything is fine');
            setText('status-line-sub', `${host} has been running for ${uptime}. Nothing needs your attention.`);
            action.hidden = true;
        } else {
            setText('status-line-title', top.title);
            setText('status-line-sub', top.detail || `${host} is running. Everything else is fine.`);
            if (top.href) {
                action.hidden = false;
                action.href = top.href;
                action.textContent = top.action || 'Open';
                action.className = `status-line-action ${top.level === 'bad' ? 'btn-primary' : 'btn-secondary'}`;
            } else {
                action.hidden = true;
            }
        }

        const more = $('status-more');
        // One next step is enough: the others are in "Getting started".
        const rest = list.slice(1).filter((issue) => issue.level !== 'setup').slice(0, 4);
        if (rest.length === 0) {
            more.hidden = true;
            more.innerHTML = '';
        } else {
            more.hidden = false;
            more.innerHTML = rest.map((issue) => `
                <li class="status-more-item" data-level="${issue.level}">
                    <span class="ov-dot ${issue.level === 'bad' ? 'bad' : (issue.level === 'warn' ? 'warn' : 'info')}"></span>
                    <span class="status-more-title">${esc(issue.title)}</span>
                    ${issue.href ? `<a href="${esc(issue.href)}">${esc(issue.action || 'Open')}</a>` : ''}
                </li>`).join('')
                + (list.length > 5 ? `<li class="status-more-item status-more-count">and ${list.length - 5} more</li>` : '');
        }

        // The brand block in the sidebar follows the status line.
        if (window.alvaosBrandStatus) window.alvaosBrandStatus(level);
    }

    // ── Live resources ───────────────────────────────────────────────────────

    function loadHistory() {
        try {
            const parsed = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
            const cutoff = Date.now() - 4 * 3600 * 1000;
            history = Array.isArray(parsed) ? parsed.filter((s) => s && s.ts >= cutoff).slice(-HISTORY_MAX) : [];
        } catch (_error) {
            history = [];
        }
    }

    function saveHistory() {
        try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-HISTORY_MAX))); } catch (_error) { /* quota */ }
    }

    function sparkPath(values, min, max) {
        const pts = values.map((v, i) => [i, v]).filter(([, v]) => Number.isFinite(v));
        if (pts.length < 2) return '';
        const span = Math.max(max - min, 0.0001);
        const xDiv = Math.max(values.length - 1, 1);
        return pts.map(([i, v], n) => {
            const x = (i / xDiv) * 100;
            const y = 30 - Math.max(0, Math.min(1, (v - min) / span)) * 28 - 1;
            return `${n ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`;
        }).join(' ');
    }

    function renderSpark(key, values, { min, max, format }) {
        const finite = values.filter(Number.isFinite);
        const latest = finite.length ? finite[finite.length - 1] : null;
        setText(`history-${key}-current`, latest === null ? 'n/a' : format(latest));
        const lo = min ?? (finite.length ? Math.min(...finite) : 0);
        const hi = max ?? (finite.length ? Math.max(...finite, lo + 1) : 1);
        const path = $(`history-${key}-path`);
        if (path) path.setAttribute('d', sparkPath(values, lo, hi));
    }

    function renderHistory() {
        if (!$('live-resources')?.open) return;
        const s = history.slice(-HISTORY_MAX);
        renderSpark('cpu', s.map((x) => x.cpu), { min: 0, max: 100, format: (v) => `${v.toFixed(0)}%` });
        renderSpark('mem', s.map((x) => x.mem), { min: 0, max: 100, format: (v) => `${v.toFixed(0)}%` });
        const net = s.map((x) => x.net);
        const netPeak = Math.max(0, ...net.filter(Number.isFinite));
        renderSpark('net', net, { min: 0, max: Math.max(netPeak * 1.25, 100000), format: (v) => formatRate(v) });
        const temps = s.map((x) => x.temp);
        const finiteTemps = temps.filter(Number.isFinite);
        renderSpark('temp', temps, {
            min: finiteTemps.length ? Math.min(...finiteTemps) - 3 : 0,
            max: finiteTemps.length ? Math.max(...finiteTemps) + 3 : 1,
            format: (v) => `${v.toFixed(0)} °C`,
        });
        if (s.length < 2) {
            setText('history-range-label', 'Collecting samples...');
        } else {
            const minutes = Math.max(1, Math.round((s[s.length - 1].ts - s[0].ts) / 60000));
            setText('history-range-label', `Last ${plural(minutes, 'minute')}`);
        }
    }

    function rate(prev, next, seconds) {
        if (prev === null || next === null || seconds <= 0 || next < prev) return null;
        return (next - prev) / seconds;
    }

    function renderLive(data) {
        const cpu = num(data?.cpu?.usage_percent) || 0;
        const mem = num(data?.memory?.percent) || 0;
        const temp = num(data?.cpu?.temperature_c);

        setText('live-cpu', `${cpu.toFixed(0)}%`);
        $('live-cpu-bar').style.width = `${Math.min(100, cpu)}%`;
        setText('live-mem', `${mem.toFixed(0)}%`);
        // The amount too, not only the share: "5.2 of 16 GB".
        if (data?.memory?.total_gb) setText('live-mem-meta', `${formatGb(data.memory.used_gb)} of ${formatGb(data.memory.total_gb)}`);
        $('live-mem-bar').style.width = `${Math.min(100, mem)}%`;

        const io = data?.io || {};
        const now = Date.now();
        const counters = {
            ts: now,
            rx: num(io.net_bytes_recv), tx: num(io.net_bytes_sent),
            rd: num(io.disk_read_bytes), wr: num(io.disk_write_bytes),
        };
        let netRate = null;
        if (lastCounters) {
            const sec = (now - lastCounters.ts) / 1000;
            const rx = rate(lastCounters.rx, counters.rx, sec);
            const tx = rate(lastCounters.tx, counters.tx, sec);
            const rd = rate(lastCounters.rd, counters.rd, sec);
            const wr = rate(lastCounters.wr, counters.wr, sec);
            if (rx !== null && tx !== null) {
                netRate = rx + tx;
                setText('live-net', formatRate(netRate));
                setText('live-net-meta', `↓ ${formatRate(rx)}  ↑ ${formatRate(tx)}`);
            }
            if (rd !== null && wr !== null) {
                setText('live-disk', formatRate(rd + wr));
                setText('live-disk-meta', `read ${formatRate(rd)}, write ${formatRate(wr)}`);
            }
        } else {
            setText('live-net-meta', 'measuring...');
            setText('live-disk-meta', 'measuring...');
        }
        lastCounters = counters;

        history.push({ ts: now, cpu, mem, net: netRate, temp });
        if (history.length > HISTORY_MAX) history = history.slice(-HISTORY_MAX);
        saveHistory();
        renderHistory();

        // Details
        setText('hostname', data?.network?.hostname || '-');
        if (window.alvaosBrandName) window.alvaosBrandName(data?.network?.hostname);
        setText('ip-address', data?.network?.ip_address || '-');
        setText('uptime', formatUptime(data?.system?.uptime_hours));
        setText('cpu-model', data?.cpu?.model || '-');
        setText('cpu-cores', `${data?.cpu?.cores ?? '-'} / ${data?.cpu?.threads ?? '-'}`);
        setText('mem-detail', `${formatGb(data?.memory?.used_gb)} of ${formatGb(data?.memory?.total_gb)} in use`);
        setText('os-version', `${data?.system?.os || '-'} ${data?.system?.os_version || ''}`.trim());
        setText('alvaos-version', data?.version || '-');
        setText('last-update', new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    }

    // ── Polling ──────────────────────────────────────────────────────────────

    let lastAlerts = null;

    async function pollSystem() {
        if (systemBusy) return;
        systemBusy = true;
        try {
            let response;
            try {
                response = await fetch(`${API}/system/info`);
            } catch (_error) {
                if (window.handleConnectionError) window.handleConnectionError();
                return;
            }
            if (response.status === 401) {
                window.location.href = '/login.html';
                return;
            }
            if (!response.ok) return;
            const data = await response.json().catch(() => null);
            if (!data) return;
            lastSystem = data;
            renderLive(data);
            renderStorage(data, lastAlerts);
        } finally {
            systemBusy = false;
        }
    }

    async function pollSlow() {
        if (slowBusy) return;
        slowBusy = true;
        try {
            const [alerts, backup, buddy, apps, containers] = await Promise.all([
                getJson('/alerts'),
                getJson('/backup/status'),
                getJson('/backup/pairing/status'),
                getJson('/apps/installed'),
                getJson('/containers'),
            ]);
            lastAlerts = alerts;
            setIssues('alerts', alertsToIssues(alerts));
            if (lastSystem) renderStorage(lastSystem, alerts);
            renderBackup(backup, buddy);
            renderApps(apps, containers);
            slowLoaded = true;
            renderStatusLine();
        } finally {
            slowBusy = false;
        }
    }

    function hidden() { return document.visibilityState === 'hidden'; }

    function scheduleSystem(delay) {
        clearTimeout(systemTimer);
        systemTimer = setTimeout(async () => {
            await pollSystem();
            scheduleSystem();
        }, delay ?? (hidden() ? SYSTEM_POLL_HIDDEN_MS : SYSTEM_POLL_MS));
    }

    function scheduleSlow(delay) {
        clearTimeout(slowTimer);
        slowTimer = setTimeout(async () => {
            await pollSlow();
            scheduleSlow();
        }, delay ?? (hidden() ? SLOW_POLL_HIDDEN_MS : SLOW_POLL_MS));
    }

    async function refresh() {
        await pollSystem();
        await pollSlow();
    }

    window.alvaosDashboardRefresh = refresh;

    async function start() {
        if (!$('status-line')) return;
        if (!localStorage.getItem('alvaos_token')) return; // app.js redirects to login

        loadHistory();
        const live = $('live-resources');
        live.addEventListener('toggle', renderHistory);
        // Alerts about load link to "#live-resources": open the details there.
        const openFromHash = () => {
            if (window.location.hash === '#live-resources') {
                live.open = true;
                live.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        };
        window.addEventListener('hashchange', openFromHash);

        // Until notifications.js reports, assume the cached answer.
        renderUpdates(localStorage.getItem('alvaos_update_available') === 'true',
            localStorage.getItem('alvaos_update_version') || '');

        await pollSystem();
        renderUpdates(localStorage.getItem('alvaos_update_available') === 'true',
            localStorage.getItem('alvaos_update_version') || '');
        await pollSlow();
        openFromHash();
        scheduleSystem();
        scheduleSlow();

        document.addEventListener('visibilitychange', () => {
            if (hidden()) {
                scheduleSystem();
                scheduleSlow();
            } else {
                scheduleSystem(300);
                scheduleSlow(600);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
