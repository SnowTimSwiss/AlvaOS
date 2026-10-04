// AlvaOS Updates page.
//
// The first screen says in one sentence whether AlvaOS is up to date, shows
// what is new when it is not, and offers one button. System packages, going
// back to an earlier version, USB installs, history and settings are folded
// away below. API_BASE comes from app.js.

let lastCheck = null;          // last /updates/alvaos/check answer
let debianState = null;        // last /updates/debian/check answer
let statusPoll = null;
let reconnectPoll = null;
let transitionActive = false;
let transitionDisconnected = false;

const $ = (id) => document.getElementById(id);

async function apiFetch(path, options = {}) {
    const skipAuthRedirect = !!options.skipAuthRedirect;
    delete options.skipAuthRedirect;
    const headers = options.headers || {};
    if (options.json) {
        headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.json);
        delete options.json;
    }
    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
    if (res.status === 401 && !skipAuthRedirect) {
        window.location.href = '/login.html';
        return null;
    }
    return res;
}

async function readJson(res) {
    if (!res) return null;
    try {
        return await res.json();
    } catch (_err) {
        return null;
    }
}

function apiErrorMessage(res, data, fallback) {
    if (data && typeof data.error === 'string' && data.error.trim()) return data.error.trim();
    if (!res) return fallback;
    if (res.status === 403) return 'Only an administrator can do this.';
    if (res.status >= 500) return `${fallback}. The details are in Settings › Diagnostics.`;
    return fallback;
}

function plural(n, one, many) {
    return `${n} ${n === 1 ? one : (many || `${one}s`)}`;
}

function timeAgo(value) {
    const t = new Date(value).getTime();
    if (!Number.isFinite(t)) return '';
    const min = Math.round((Date.now() - t) / 60000);
    if (min < 1) return 'just now';
    if (min < 60) return `${plural(min, 'minute')} ago`;
    const hours = Math.round(min / 60);
    if (hours < 24) return `${plural(hours, 'hour')} ago`;
    const days = Math.round(hours / 24);
    if (days === 1) return 'yesterday';
    if (days < 30) return `${days} days ago`;
    return `on ${new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}`;
}

function cleanVersion(tag) {
    return String(tag || '').trim().replace(/^v(?=\d)/i, '');
}

// ── Status line ─────────────────────────────────────────────────────────────

function setStatus(level, title, sub, { install = false, check = true } = {}) {
    const box = $('upd-status');
    box.dataset.level = level;
    const iconName = { ok: 'circle-check-big', info: 'circle-arrow-up', warn: 'triangle-alert', bad: 'circle-alert', loading: 'loader-circle' }[level] || 'info';
    $('upd-status-icon').innerHTML = window.alvaIcon ? window.alvaIcon(iconName, '', 'aria-hidden="true"') : '';
    if (window.renderAlvaIcons) window.renderAlvaIcons($('upd-status-icon'));
    $('upd-title').textContent = title;
    $('upd-sub').textContent = sub;
    $('upd-install-btn').hidden = !install;
    $('upd-check-btn').hidden = !check;
}

// Release notes are Markdown from GitHub. Show headings, lists and paragraphs
// as plain text structure; never as HTML.
function renderNotes(markdown) {
    const body = $('upd-notes-body');
    body.innerHTML = '';
    const lines = String(markdown || '').replace(/\r/g, '').split('\n');
    let list = null;
    let para = [];
    const flushPara = () => {
        if (para.length) {
            const p = document.createElement('p');
            p.textContent = para.join(' ');
            body.appendChild(p);
            para = [];
        }
    };
    const strip = (text) => text.replace(/\*\*|__|`/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').trim();
    lines.forEach((raw) => {
        const line = raw.trim();
        const heading = line.match(/^#{1,6}\s+(.*)$/);
        const item = line.match(/^[-*+]\s+(.*)$/);
        if (!line) {
            flushPara();
            list = null;
        } else if (heading) {
            flushPara();
            list = null;
            const h = document.createElement('h3');
            h.textContent = strip(heading[1]);
            body.appendChild(h);
        } else if (item) {
            flushPara();
            if (!list) {
                list = document.createElement('ul');
                body.appendChild(list);
            }
            const li = document.createElement('li');
            li.textContent = strip(item[1]);
            list.appendChild(li);
        } else {
            list = null;
            para.push(strip(line));
        }
    });
    flushPara();
    if (!body.childNodes.length) {
        const p = document.createElement('p');
        p.textContent = 'This release has no notes.';
        body.appendChild(p);
    }
}

function renderCheck(data) {
    lastCheck = data;
    const current = cleanVersion(data?.current_version);
    if (current && current !== 'unknown') $('upd-current-version').textContent = current;

    if (data?.update_available && data.release) {
        const latest = cleanVersion(data.latest_version || data.release.tag_name);
        setStatus('info', `AlvaOS ${latest} is ready to install`,
            `You have ${current || 'an older version'}. See what changed below.`, { install: true, check: false });
        $('upd-notes-title').textContent = `What's new in ${latest}`;
        $('upd-notes-date').textContent = data.release.published_at ? `Released ${timeAgo(data.release.published_at)}` : '';
        renderNotes(data.release.body);
        $('upd-notes').hidden = false;
    } else {
        $('upd-notes').hidden = true;
        const warning = data?.warning ? ' (from the last successful check)' : '';
        setStatus('ok', `AlvaOS ${current || ''} is up to date`.replace('  ', ' '),
            `Checked ${timeAgo(new Date())}${warning}.`, { check: true });
    }
}

async function checkAlvaos(force = false) {
    const channel = $('set-channel')?.value || 'stable';
    if (force) {
        $('upd-check-btn').disabled = true;
        $('upd-check-btn').textContent = 'Checking...';
    }
    try {
        const res = await apiFetch(`/updates/alvaos/check?channel=${encodeURIComponent(channel)}${force ? '&force=1' : ''}`);
        if (!res) return;
        const data = await readJson(res);
        if (!res.ok || !data || data.error) {
            $('upd-notes').hidden = true;
            setStatus('warn', 'Could not look for updates',
                'AlvaOS could not reach GitHub. Check the internet connection, or install from a USB stick below.', { check: true });
            if (data?.current_version) $('upd-current-version').textContent = cleanVersion(data.current_version);
            return;
        }
        if (window.setUpdateIndicators) {
            window.setUpdateIndicators({ available: !!data.update_available, version: data.latest_version || '', checkedAt: Date.now() });
        }
        renderCheck(data);
    } catch (_err) {
        setStatus('warn', 'Could not look for updates', 'The NAS did not answer. Try again in a moment.', { check: true });
    } finally {
        $('upd-check-btn').disabled = false;
        $('upd-check-btn').textContent = 'Check now';
    }
}

function debAssetUrl(release) {
    const deb = (release?.assets || []).find((a) => String(a.name || '').endsWith('.deb'));
    return deb ? deb.browser_download_url : null;
}

async function installAlvaos() {
    const release = lastCheck?.release;
    const url = debAssetUrl(release);
    if (!url) {
        window.showToast('This release has no installable package yet. Try again later.', 'warning');
        return;
    }
    const version = cleanVersion(lastCheck.latest_version || release.tag_name);
    const ok = await window.showConfirm(`Install AlvaOS ${version}?\nAlvaOS downloads the update, checks its signature and installs it. The web interface restarts; your files, apps and settings stay.`, { confirmLabel: 'Install' });
    if (!ok) return;

    $('upd-install-btn').disabled = true;
    showProgress(5, 'Starting the download...');
    try {
        const res = await apiFetch('/updates/alvaos/apply', { method: 'POST', json: { url, version: release.tag_name || '' } });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            hideProgress();
            setStatus('bad', 'The update did not install', apiErrorMessage(res, data, 'Installing failed'), { install: true, check: true });
            $('upd-install-btn').textContent = 'Try again';
            loadRollback();
            return;
        }
        if (window.setUpdateIndicators) window.setUpdateIndicators({ available: false, version: '', checkedAt: Date.now() });
        startTransition(`Installing AlvaOS ${version}`);
    } catch (_err) {
        hideProgress();
        setStatus('bad', 'The update did not install', 'The NAS did not answer. Try again in a moment.', { install: true, check: true });
    } finally {
        $('upd-install-btn').disabled = false;
    }
}

// ── Progress and reconnecting while AlvaOS restarts ─────────────────────────

function showProgress(percent, text) {
    $('upd-progress').hidden = false;
    $('upd-progress-bar').style.width = `${Math.max(3, Math.min(100, percent))}%`;
    $('upd-progress-text').textContent = text;
}

function hideProgress() {
    $('upd-progress').hidden = true;
}

function startTransition(title) {
    transitionActive = true;
    transitionDisconnected = false;
    setStatus('loading', title, 'Keep this page open. It reconnects by itself when AlvaOS is back.', { check: false });
    showProgress(10, 'Preparing...');
    pollStatus();
}

function markDisconnected() {
    if (!transitionActive || transitionDisconnected) return;
    transitionDisconnected = true;
    showProgress(90, 'AlvaOS is restarting. Reconnecting...');
}

function startReconnect() {
    if (reconnectPoll) return;
    reconnectPoll = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/system/info`, { cache: 'no-store' });
            if (res.ok || res.status === 401) {
                clearInterval(reconnectPoll);
                reconnectPoll = null;
                showProgress(100, 'Done. Reloading...');
                setTimeout(() => {
                    window.location.href = res.status === 401 ? '/login.html' : 'updates.html';
                }, 700);
            }
        } catch (_err) {
            // still restarting
        }
    }, 2500);
}

function stopStatusPoll() {
    if (statusPoll) clearInterval(statusPoll);
    statusPoll = null;
}

function pollStatus() {
    stopStatusPoll();
    statusPoll = setInterval(async () => {
        try {
            const res = await apiFetch('/updates/status', { skipAuthRedirect: transitionActive });
            if (!res) return;
            if (transitionActive && (res.status === 401 || res.status >= 500)) {
                markDisconnected();
                stopStatusPoll();
                startReconnect();
                return;
            }
            const data = await readJson(res);
            if (!res.ok || !data) return;
            if (data.status === 'error') {
                stopStatusPoll();
                transitionActive = false;
                hideProgress();
                setStatus('bad', 'The update did not install', data.details?.error || data.message || 'Installing failed.', { install: !!lastCheck?.update_available, check: true });
                loadRollback();
                return;
            }
            if (data.status && data.status !== 'idle') {
                const percent = typeof data.progress?.percent === 'number' ? data.progress.percent : 50;
                showProgress(percent, data.message || 'Working...');
            } else if (data.status === 'idle' && transitionActive) {
                stopStatusPoll();
                if (transitionDisconnected) {
                    startReconnect();
                } else {
                    showProgress(100, 'Done. Reloading...');
                    setTimeout(() => window.location.reload(), 700);
                }
            }
        } catch (_err) {
            if (transitionActive) {
                markDisconnected();
                stopStatusPoll();
                startReconnect();
            }
        }
    }, 3000);
}

// ── System packages (Debian) ────────────────────────────────────────────────

function debianLabel(info) {
    const version = String(info?.version || '').trim();
    const codename = String(info?.codename || '').trim();
    if (version && codename) return `Debian ${version} (${codename})`;
    return version ? `Debian ${version}` : (codename || 'Debian');
}

async function checkDebian() {
    try {
        const res = await apiFetch('/updates/debian/check');
        if (!res) return;
        const data = await readJson(res);
        if (!res.ok || !data || data.error) {
            $('upd-system-text').textContent = 'Could not check the base system right now. Nothing was changed.';
            $('upd-system-install').hidden = true;
            $('upd-system-more').hidden = true;
            return;
        }
        debianState = data;
        const updates = Array.isArray(data.updates) ? data.updates : [];
        if (!updates.length) {
            $('upd-system-text').textContent = `${debianLabel(data.os_upgrade?.current)} is up to date.`;
            $('upd-system-install').hidden = true;
            $('upd-system-more').hidden = true;
        } else {
            $('upd-system-text').textContent = `${plural(updates.length, 'package update')} ready, mostly security and bug fixes.`;
            $('upd-system-install').hidden = false;
            $('upd-system-more').hidden = false;
            const list = $('upd-system-list');
            list.innerHTML = '';
            updates.forEach((pkg) => {
                const row = document.createElement('label');
                row.className = 'upd-pkg';
                row.innerHTML = `<input type="checkbox" class="upd-pkg-box" value="${escapeHtml(pkg.package)}"><span>${escapeHtml(pkg.package)}</span><span class="mono-text">${escapeHtml(pkg.version || '')}</span>`;
                list.appendChild(row);
            });
        }
        const os = data.os_upgrade;
        if (os?.available && os.target?.codename) {
            $('upd-os-upgrade').hidden = false;
            $('upd-os-upgrade-text').textContent = `A new Debian release is available: ${debianLabel(os.target)}.${os.stepwise ? ' It is installed one release at a time.' : ''}`;
        } else {
            $('upd-os-upgrade').hidden = true;
        }
    } catch (_err) {
        $('upd-system-text').textContent = 'Could not check the base system right now. Nothing was changed.';
    }
}

async function applyDebian(packages) {
    const count = packages ? packages.length : (debianState?.updates || []).length;
    const ok = await window.showConfirm(`Install ${plural(count, 'system package update')}?\nThis usually takes a minute and needs no restart.`, { confirmLabel: 'Install' });
    if (!ok) return;
    $('upd-system-install').disabled = true;
    $('upd-system-text').textContent = 'Installing system updates...';
    try {
        const res = await apiFetch('/updates/debian/apply', { method: 'POST', json: packages ? { packages } : {} });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            window.showToast(apiErrorMessage(res, data, 'System updates did not install'), 'error');
        } else {
            window.showToast('System updates installed', 'success');
        }
    } catch (_err) {
        window.showToast('System updates did not install', 'error');
    } finally {
        $('upd-system-install').disabled = false;
        await Promise.all([checkDebian(), loadHistory()]);
    }
}

async function applyOsUpgrade() {
    const os = debianState?.os_upgrade;
    if (!os?.available || !os.target?.codename) return;
    const ok = await window.showConfirm(
        `Upgrade to ${debianLabel(os.target)}?\nThis takes a long time and needs a restart afterwards. Make sure a backup has run first.`,
        { confirmLabel: 'Upgrade', danger: true }
    );
    if (!ok) return;
    $('upd-os-upgrade-btn').disabled = true;
    $('upd-os-upgrade-text').textContent = 'Upgrading the base system. This can take a long time...';
    try {
        const res = await apiFetch('/updates/debian/os-upgrade', { method: 'POST', json: { target_codename: os.target.codename } });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            window.showToast(apiErrorMessage(res, data, 'The release upgrade did not finish'), 'error');
        } else {
            window.showToast('Release upgrade done. Restart the NAS in Settings › Power.', 'success');
        }
    } catch (_err) {
        window.showToast('The release upgrade did not finish', 'error');
    } finally {
        $('upd-os-upgrade-btn').disabled = false;
        await Promise.all([checkDebian(), loadHistory()]);
    }
}

// ── Going back to an earlier version ────────────────────────────────────────

async function loadRollback() {
    const res = await apiFetch('/updates/rollback').catch(() => null);
    const data = await readJson(res);
    const versions = Array.isArray(data?.versions) ? data.versions : [];
    const wayBack = $('upd-way-back');
    if (wayBack && data && typeof data.way_back_ready === 'boolean') {
        wayBack.hidden = false;
        const current = escapeHtml(cleanVersion(data.current_version || ''));
        wayBack.innerHTML = data.way_back_ready
            ? `${window.alvaIcon ? window.alvaIcon('shield-check', '', 'aria-hidden="true"') : ''}<span><strong>Safety net ready.</strong> If an update fails, AlvaOS goes back to ${current} on its own.</span>`
            : `${window.alvaIcon ? window.alvaIcon('triangle-alert', '', 'aria-hidden="true"') : ''}<span><strong>Safety net not ready yet.</strong> The signed package of ${current} is fetched from its release as soon as the NAS is online, so a failed update can go back to it.</span>`;
        wayBack.classList.toggle('warn', !data.way_back_ready);
    }
    const section = $('upd-rollback');
    section.hidden = versions.length === 0;
    if (!versions.length) return;
    const list = $('upd-rollback-list');
    list.innerHTML = versions.map((v) => `
        <div class="upd-row">
            <div><strong>AlvaOS ${escapeHtml(cleanVersion(v.version))}</strong><small>Downloaded ${escapeHtml(timeAgo(v.downloaded_at))}</small></div>
            <button type="button" class="btn-secondary" data-rollback="${escapeHtml(v.version)}">Go back</button>
        </div>`).join('');
    list.querySelectorAll('[data-rollback]').forEach((btn) => btn.addEventListener('click', () => rollback(btn.dataset.rollback)));
}

async function rollback(version) {
    const ok = await window.showConfirm(
        `Go back to AlvaOS ${cleanVersion(version)}?\nThe earlier version is installed the same way as an update. Your files and apps stay. Settings that the newer version changed may not carry over.`,
        { confirmLabel: 'Go back' }
    );
    if (!ok) return;
    try {
        const res = await apiFetch('/updates/rollback', { method: 'POST', json: { version } });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            window.showToast(apiErrorMessage(res, data, 'Going back did not work'), 'error');
            return;
        }
        $('upd-rollback').open = false;
        startTransition(`Going back to AlvaOS ${cleanVersion(version)}`);
    } catch (_err) {
        window.showToast('Going back did not work', 'error');
    }
}

// ── USB stick ───────────────────────────────────────────────────────────────

async function scanOffline() {
    const list = $('upd-offline-list');
    list.innerHTML = '<div class="metric-sub">Looking...</div>';
    try {
        const res = await apiFetch('/updates/offline/scan', { method: 'POST', json: {} });
        if (!res) return;
        const data = await readJson(res);
        if (!res.ok || !data) {
            list.innerHTML = '<div class="metric-sub">Could not read the USB stick. Check that it is plugged in and uses FAT32, exFAT, NTFS or ext4.</div>';
            return;
        }
        const packages = Array.isArray(data.packages) ? data.packages : [];
        if (!packages.length) {
            list.innerHTML = '<div class="metric-sub">No packages found. The .deb file has to be in the top folder of the stick.</div>';
            return;
        }
        list.innerHTML = packages.map((pkg) => `
            <div class="upd-row">
                <div><strong>${escapeHtml(pkg.name)}</strong><small>${pkg.type === 'alvaos' ? 'AlvaOS update' : 'System package'} &middot; ${escapeHtml(pkg.path)}</small></div>
                <button type="button" class="btn-secondary" data-offline="${escapeHtml(pkg.path)}" data-type="${escapeHtml(pkg.type)}">Install</button>
            </div>`).join('');
        list.querySelectorAll('[data-offline]').forEach((btn) => btn.addEventListener('click', () => applyOffline(btn.dataset.offline, btn.dataset.type)));
    } catch (_err) {
        list.innerHTML = '<div class="metric-sub">Could not read the USB stick.</div>';
    }
}

async function applyOffline(path, type) {
    const ok = await window.showConfirm('Install this package?\nAlvaOS packages are checked for a valid signature before they are installed.', { confirmLabel: 'Install' });
    if (!ok) return;
    try {
        const res = await apiFetch('/updates/offline/apply', { method: 'POST', json: { path } });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            window.showToast(apiErrorMessage(res, data, 'The package did not install'), 'error');
            return;
        }
        if (type === 'alvaos') {
            $('upd-offline').open = false;
            startTransition('Installing AlvaOS from the USB stick');
        } else {
            window.showToast('Package installed', 'success');
            loadHistory();
        }
    } catch (_err) {
        window.showToast('The package did not install', 'error');
    }
}

// ── History ─────────────────────────────────────────────────────────────────

function historyText(entry) {
    if (entry.type === 'alvaos') {
        const version = entry.version
            || (String(entry.package || '').match(/_(\d[\w.+~-]*?)(?:_[a-z0-9]+)?\.deb$/i) || [])[1];
        return version ? `AlvaOS ${cleanVersion(version)} installed` : 'AlvaOS update installed';
    }
    if (entry.type === 'rollback') {
        return entry.version ? `An update failed; went back to AlvaOS ${cleanVersion(entry.version)}` : 'An update failed; went back to the earlier version';
    }
    if (entry.type === 'debian') {
        const n = Array.isArray(entry.packages) ? entry.packages.length : 0;
        return n ? `${plural(n, 'system package')} updated` : 'System packages updated';
    }
    if (entry.type === 'debian-os') return `Upgraded to ${debianLabel(entry.to || entry.target)}`;
    if (entry.package) return `${String(entry.package).split('/').pop()} installed`;
    return 'System updated';
}

async function loadHistory() {
    const list = $('upd-history-list');
    const res = await apiFetch('/updates/history').catch(() => null);
    const data = await readJson(res);
    const history = Array.isArray(data?.history) ? data.history.slice().reverse() : [];
    if (!history.length) {
        list.innerHTML = '<div class="metric-sub">Nothing installed through this page yet.</div>';
        return;
    }
    list.innerHTML = history.slice(0, 20).map((entry) => `
        <div class="upd-row">
            <div><strong>${escapeHtml(historyText(entry))}</strong><small title="${escapeHtml(entry.timestamp || '')}">${escapeHtml(timeAgo(entry.timestamp))}</small></div>
        </div>`).join('');
}

// ── Settings (saved on change) ──────────────────────────────────────────────

async function loadSettings() {
    const res = await apiFetch('/updates/settings').catch(() => null);
    const data = await readJson(res);
    if (!res?.ok || !data) return;
    $('set-auto-check').checked = !!data.auto_check;
    $('set-auto-apply').checked = !!data.auto_apply;
    $('set-auto-apply-debian').checked = !!data.auto_apply_debian;
    $('set-channel').value = data.channel || 'stable';
}

async function saveSettings() {
    const payload = {
        auto_check: $('set-auto-check').checked,
        auto_apply: $('set-auto-apply').checked,
        auto_apply_debian: $('set-auto-apply-debian').checked,
        channel: $('set-channel').value,
    };
    const res = await apiFetch('/updates/settings', { method: 'POST', json: payload }).catch(() => null);
    if (!res || !res.ok) {
        window.showToast('The setting was not saved. Try again.', 'error');
        await loadSettings();
        return;
    }
    window.showToast('Saved', 'success');
}

// ── Start ───────────────────────────────────────────────────────────────────

async function resumeIfBusy() {
    const res = await apiFetch('/updates/status').catch(() => null);
    const data = await readJson(res);
    if (data && ['downloading', 'installing'].includes(data.status)) {
        startTransition('An update is being installed');
        return true;
    }
    return false;
}

async function init() {
    $('upd-check-btn').addEventListener('click', () => checkAlvaos(true));
    $('upd-install-btn').addEventListener('click', installAlvaos);
    $('upd-system-install').addEventListener('click', () => applyDebian(null));
    $('upd-system-install-selected').addEventListener('click', () => {
        const packages = Array.from(document.querySelectorAll('.upd-pkg-box:checked')).map((b) => b.value);
        if (!packages.length) {
            window.showToast('Tick at least one package.', 'info', { record: false });
            return;
        }
        applyDebian(packages);
    });
    $('upd-os-upgrade-btn').addEventListener('click', applyOsUpgrade);
    $('upd-offline-scan').addEventListener('click', scanOffline);
    ['set-auto-check', 'set-auto-apply', 'set-auto-apply-debian'].forEach((id) => $(id).addEventListener('change', saveSettings));
    $('set-channel').addEventListener('change', async () => {
        await saveSettings();
        checkAlvaos(true);
    });

    await loadSettings();
    if (await resumeIfBusy()) return;
    await checkAlvaos(false);
    checkDebian();
    loadRollback();
    loadHistory();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
