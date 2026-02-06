const API_BASE = '/api/v1';

let lastRelease = null;
let statusPoll = null;

function getToken() {
    return localStorage.getItem('alvaos_token') || '';
}

async function apiFetch(path, options = {}) {
    const headers = options.headers || {};
    headers['Authorization'] = getToken();
    if (options.json) {
        headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.json);
        delete options.json;
    }
    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
    if (res.status === 401) {
        window.location.href = '/login.html';
        return null;
    }
    return res;
}

function setStatus(text) {
    const el = document.getElementById('update-status');
    if (el) el.textContent = text;
}

function setProgress(active, percent = 0) {
    const bar = document.getElementById('alvaos-update-progress');
    if (!bar) return;
    if (active) {
        bar.classList.add('indeterminate');
        bar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
    } else {
        bar.classList.remove('indeterminate');
        bar.style.width = '0%';
    }
}

function setCurrentVersion(version) {
    const el = document.getElementById('current-version');
    if (el) el.textContent = version || '-';
}

async function readJson(res) {
    if (!res) return null;
    try {
        return await res.json();
    } catch (err) {
        return null;
    }
}

function renderRelease(release, updateAvailable, latestVersion) {
    const card = document.getElementById('update-available-card');
    if (!card) return;
    if (!updateAvailable || !release) {
        card.style.display = 'none';
        return;
    }
    card.style.display = 'block';
    const latestEl = document.getElementById('latest-version');
    const notesEl = document.getElementById('release-notes');
    if (latestEl) latestEl.textContent = latestVersion || release.tag_name || '-';
    if (notesEl) notesEl.textContent = release.body || 'No release notes.';
}

async function checkAlvaosUpdates() {
    const channel = document.getElementById('update-channel-select')?.value || 'stable';
    lastRelease = null;
    setStatus(`Checking ${channel} channel...`);
    try {
        const res = await apiFetch(`/updates/alvaos/check?channel=${encodeURIComponent(channel)}`);
        if (!res) return;
        const data = await readJson(res);
        if (data && data.current_version) {
            setCurrentVersion(data.current_version);
        }
        if (!res.ok || !data || data.error) {
            renderRelease(null, false, null);
            setStatus('Update check failed');
            window.showToast(data?.error || 'Update check failed', 'error');
            return;
        }
        if (window.setUpdateIndicators) {
            window.setUpdateIndicators({
                available: !!data.update_available,
                version: data.latest_version || '',
                checkedAt: Date.now()
            });
        }
        renderRelease(data.release, data.update_available, data.latest_version);
        lastRelease = data.release;
        setStatus(data.update_available ? 'Update available' : 'Up to date');
    } catch (err) {
        renderRelease(null, false, null);
        setStatus('Update check failed');
        window.showToast('Update check failed', 'error');
    }
}

function getDebAssetUrl(release) {
    if (!release || !release.assets) return null;
    const deb = release.assets.find(a => String(a.name || '').endsWith('.deb'));
    return deb ? deb.browser_download_url : null;
}

async function applyAlvaosUpdate() {
    if (!lastRelease) {
        window.showToast('No update selected.', 'warning');
        return;
    }
    const url = getDebAssetUrl(lastRelease);
    if (!url) {
        window.showToast('No .deb asset found in release.', 'error');
        return;
    }
    const ok = await window.showConfirm('Install AlvaOS update?\nThe system will install the update package.');
    if (!ok) return;

    setStatus('Installing update...');
    setProgress(true, 10);
    try {
        const res = await apiFetch('/updates/alvaos/apply', {
            method: 'POST',
            json: { url, version: lastRelease.tag_name || '' }
        });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            window.showToast(data.error || 'Update failed', 'error');
            setStatus('Install failed');
            return;
        }
        window.showToast('Update started', 'success');
        setProgress(true, 25);
        pollUpdateStatus();
    } catch (err) {
        window.showToast('Update failed', 'error');
        setStatus('Install failed');
    }
}

async function checkDebianUpdates() {
    const list = document.getElementById('debian-updates-list');
    if (list) list.innerHTML = '<div class="metric-sub">Checking...</div>';
    try {
        const res = await apiFetch('/updates/debian/check');
        if (!res) return;
        const data = await readJson(res);
        if (!res.ok || !data || data.error) {
            if (list) list.innerHTML = '<div class="metric-sub">Failed to check updates.</div>';
            window.showToast(data?.error || 'Debian check failed', 'error');
            return;
        }

        const updates = data.updates || [];
        if (!updates.length) {
            if (list) list.innerHTML = '<div class="metric-sub">No updates available.</div>';
            return;
        }
        list.innerHTML = '';
        updates.forEach(pkg => {
            const row = document.createElement('div');
            row.className = 'list-item';
            row.innerHTML = `
                <label style="display:flex; gap:8px; align-items:center;">
                    <input type="checkbox" class="debian-package" value="${pkg.package}">
                    <span>${pkg.package}</span>
                </label>
                <span class="mono-text">${pkg.version}</span>
            `;
            list.appendChild(row);
        });
    } catch (err) {
        if (list) list.innerHTML = '<div class="metric-sub">Failed to check updates.</div>';
        window.showToast('Debian check failed', 'error');
    }
}

async function applyDebianUpdates() {
    const boxes = Array.from(document.querySelectorAll('.debian-package:checked'));
    if (!boxes.length) {
        window.showToast('Select at least one package.', 'warning');
        return;
    }
    const packages = boxes.map(b => b.value);
    const ok = await window.showConfirm('Apply Debian updates?\nSelected packages will be installed.');
    if (!ok) return;

    try {
        const res = await apiFetch('/updates/debian/apply', {
            method: 'POST',
            json: { packages }
        });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            window.showToast(data.error || 'Debian updates failed', 'error');
            return;
        }
        window.showToast('Debian updates started', 'success');
        pollUpdateStatus();
    } catch (err) {
        window.showToast('Debian updates failed', 'error');
    }
}

async function scanOfflineUpdates() {
    const list = document.getElementById('offline-list');
    if (list) list.innerHTML = '<div class="metric-sub">Scanning...</div>';
    try {
        const res = await apiFetch('/updates/offline/scan', { method: 'POST', json: {} });
        if (!res) return;
        const data = await readJson(res);
        if (!res.ok || !data) {
            if (list) list.innerHTML = '<div class="metric-sub">Failed to scan offline updates.</div>';
            window.showToast('Offline scan failed', 'error');
            return;
        }
        const packages = data.packages || [];
        if (!packages.length) {
            if (list) list.innerHTML = `
                <div class="metric-sub">
                    No offline packages found.<br>
                    <small style="opacity:0.8;">Make sure your USB stick uses a supported filesystem (FAT32, NTFS, EXT4, exFAT) and the .deb package is in a top-level directory.</small>
                </div>
            `;
            return;
        }
        list.innerHTML = '';
        packages.forEach(pkg => {
            const row = document.createElement('div');
            row.className = 'list-item';
            row.innerHTML = `
                <div style="display:flex; flex-direction:column;">
                    <span>${pkg.name}</span>
                    <span class="metric-sub">${pkg.path}</span>
                </div>
                <button class="btn-secondary" data-offline-path="${pkg.path}">Install</button>
            `;
            list.appendChild(row);
        });

        list.querySelectorAll('button[data-offline-path]').forEach(btn => {
            btn.addEventListener('click', () => applyOfflineUpdate(btn.dataset.offlinePath));
        });
    } catch (err) {
        if (list) list.innerHTML = '<div class="metric-sub">Failed to scan offline updates.</div>';
        window.showToast('Offline scan failed', 'error');
    }
}

async function applyOfflineUpdate(path) {
    const ok = await window.showConfirm('Install offline update?\nThis will install the selected package.');
    if (!ok) return;
    try {
        const res = await apiFetch('/updates/offline/apply', {
            method: 'POST',
            json: { path }
        });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            window.showToast(data.error || 'Offline update failed', 'error');
            return;
        }
        window.showToast('Offline update started', 'success');
        pollUpdateStatus();
    } catch (err) {
        window.showToast('Offline update failed', 'error');
    }
}

async function loadUpdateHistory() {
    const list = document.getElementById('history-list');
    if (list) list.innerHTML = '<div class="metric-sub">Loading...</div>';
    const res = await apiFetch('/updates/history');
    if (!res) return;
    const data = await readJson(res);
    if (!res.ok || !data) {
        if (list) list.innerHTML = '<div class="metric-sub">No update history.</div>';
        return;
    }
    const history = data.history || [];
    if (!history.length) {
        if (list) list.innerHTML = '<div class="metric-sub">No update history.</div>';
        return;
    }
    list.innerHTML = '';
    history.slice().reverse().forEach(entry => {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `
            <div style="display:flex; flex-direction:column;">
                <span>${entry.type || 'update'} ${entry.package ? `- ${entry.package}` : ''}</span>
                <span class="metric-sub">${entry.timestamp || ''}</span>
            </div>
            <span class="mono-text">${entry.packages ? entry.packages.length + ' pkgs' : ''}</span>
        `;
        list.appendChild(row);
    });
}

async function loadSettings() {
    const res = await apiFetch('/updates/settings');
    if (!res) return;
    const data = await readJson(res);
    if (!res.ok || !data) return;
    const auto = document.getElementById('settings-auto-check');
    const autoApply = document.getElementById('settings-auto-apply');
    const channel = document.getElementById('settings-channel');
    if (auto) auto.checked = !!data.auto_check;
    if (autoApply) autoApply.checked = !!data.auto_apply;
    if (channel) channel.value = data.channel || 'stable';
    const channelSelect = document.getElementById('update-channel-select');
    if (channelSelect) channelSelect.value = data.channel || 'stable';
}

async function saveSettings() {
    const auto = document.getElementById('settings-auto-check')?.checked || false;
    const autoApply = document.getElementById('settings-auto-apply')?.checked || false;
    const channel = document.getElementById('settings-channel')?.value || 'stable';
    const res = await apiFetch('/updates/settings', {
        method: 'POST',
        json: { auto_check: auto, auto_apply: autoApply, channel }
    });
    if (!res) return;
    if (!res.ok) {
        window.showToast('Failed to save settings', 'error');
        return;
    }
    window.showToast('Settings saved', 'success');
}

async function pollUpdateStatus() {
    if (statusPoll) clearInterval(statusPoll);
    statusPoll = setInterval(async () => {
        const res = await apiFetch('/updates/status');
        if (!res) return;
        const data = await readJson(res);
        if (!res.ok || !data) return;
        if (data.status && data.status !== 'idle') {
            setStatus(data.message || data.status);
            const percent = data.progress && typeof data.progress.percent === 'number'
                ? data.progress.percent
                : (data.status === 'downloading' ? 35 : 60);
            setProgress(true, percent);
        } else if (data.status === 'idle') {
            setStatus('Idle');
            setProgress(false, 0);
            clearInterval(statusPoll);
            statusPoll = null;
        }
    }, 3000);
}

function initTabs() {
    const buttons = document.querySelectorAll('.tab-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.querySelectorAll('.tab-panel').forEach(panel => {
                panel.classList.toggle('active', panel.id === `tab-${tab}`);
            });
        });
    });
}

function initHandlers() {
    document.getElementById('check-updates-btn')?.addEventListener('click', checkAlvaosUpdates);
    document.getElementById('download-install-btn')?.addEventListener('click', applyAlvaosUpdate);
    document.getElementById('debian-check-btn')?.addEventListener('click', checkDebianUpdates);
    document.getElementById('debian-apply-btn')?.addEventListener('click', applyDebianUpdates);
    document.getElementById('offline-scan-btn')?.addEventListener('click', scanOfflineUpdates);
    document.getElementById('settings-save-btn')?.addEventListener('click', saveSettings);
}

async function init() {
    initTabs();
    initHandlers();
    await loadSettings();
    await checkAlvaosUpdates();
    await loadUpdateHistory();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
