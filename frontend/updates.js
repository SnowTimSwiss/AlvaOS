// API_BASE is defined in app.js or global scope

let lastRelease = null;
let statusPoll = null;
let reconnectPoll = null;
let updateTransitionActive = false;
let updateTransitionDisconnected = false;
let debianOsUpgradeState = null;

function getToken() {
    return localStorage.getItem('alvaos_token') || '';
}

async function apiFetch(path, options = {}) {
    const skipAuthRedirect = !!options.skipAuthRedirect;
    delete options.skipAuthRedirect;
    const headers = options.headers || {};
    headers['Authorization'] = getToken();
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

function setStatus(text) {
    const el = document.getElementById('update-status');
    if (el) el.textContent = text;
}

function apiErrorMessage(res, data, fallback) {
    if (data && typeof data.error === 'string' && data.error.trim()) {
        return data.error.trim();
    }
    if (!res) return fallback;
    if (res.status === 401) return 'Authentication required. Please log in again.';
    if (res.status === 403) return 'Admin privileges required for this action.';
    if (res.status >= 500) return 'Backend update operation failed. Check backend logs.';
    return fallback;
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

function formatDebianRelease(info) {
    const version = String(info && info.version ? info.version : '').trim();
    const codename = String(info && info.codename ? info.codename : '').trim();
    if (version && codename) return `Debian ${version} (${codename})`;
    if (version) return `Debian ${version}`;
    if (codename) return codename;
    return 'unknown';
}

function renderDebianOsUpgrade(osUpgrade) {
    debianOsUpgradeState = osUpgrade || null;
    const statusEl = document.getElementById('debian-os-upgrade-status');
    const btn = document.getElementById('debian-os-upgrade-btn');
    if (!statusEl || !btn) return;

    if (!osUpgrade || osUpgrade.error) {
        statusEl.textContent = osUpgrade && osUpgrade.error
            ? `OS release upgrade check failed: ${osUpgrade.error}`
            : 'OS release upgrade status unavailable.';
        btn.disabled = true;
        btn.textContent = 'Upgrade OS Release';
        return;
    }

    const currentLabel = formatDebianRelease(osUpgrade.current || {});
    const target = osUpgrade.target || {};
    const targetLabel = formatDebianRelease(target);
    if (osUpgrade.available && target.codename) {
        const stepwiseNote = osUpgrade.stepwise ? ' Stepwise upgrade required.' : '';
        statusEl.textContent = `OS release upgrade available: ${currentLabel} -> ${targetLabel}.${stepwiseNote}`;
        btn.disabled = false;
        btn.textContent = `Upgrade to ${targetLabel}`;
    } else {
        statusEl.textContent = `Current OS release: ${currentLabel}. No release upgrade available.`;
        btn.disabled = true;
        btn.textContent = 'Upgrade OS Release';
    }
}

function ensureUpdateTransitionOverlay() {
    let overlay = document.getElementById('update-transition-overlay');
    if (overlay) return overlay;

    if (!document.getElementById('update-transition-style')) {
        const style = document.createElement('style');
        style.id = 'update-transition-style';
        style.textContent = `
            @keyframes alvaos-update-spin { 100% { transform: rotate(360deg); } }
        `;
        document.head.appendChild(style);
    }

    overlay = document.createElement('div');
    overlay.id = 'update-transition-overlay';
    overlay.style.cssText = `
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.85);
        z-index: 25000;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 16px;
    `;
    overlay.innerHTML = `
        <div style="text-align:center; color:white; max-width:640px;">
            <div style="width:52px; height:52px; margin:0 auto 16px auto; border:4px solid rgba(255,255,255,0.25); border-top-color:#fff; border-radius:50%; animation: alvaos-update-spin 1s linear infinite;"></div>
            <h2 id="update-transition-title" style="margin:0 0 8px 0; font-size:1.5rem; font-weight:700;">Preparing update</h2>
            <p id="update-transition-message" style="margin:0; color:rgba(255,255,255,0.85); line-height:1.4;">
                Please do not reload or close.
            </p>
        </div>
    `;
    document.body.appendChild(overlay);
    return overlay;
}

function setUpdateTransitionMessage(title, message) {
    const overlay = ensureUpdateTransitionOverlay();
    const titleEl = overlay.querySelector('#update-transition-title');
    const messageEl = overlay.querySelector('#update-transition-message');
    if (titleEl) titleEl.textContent = title;
    if (messageEl) messageEl.textContent = message;
}

function clearUpdateTransition() {
    if (statusPoll) {
        clearInterval(statusPoll);
        statusPoll = null;
    }
    if (reconnectPoll) {
        clearInterval(reconnectPoll);
        reconnectPoll = null;
    }
    updateTransitionActive = false;
    updateTransitionDisconnected = false;
    document.getElementById('update-transition-overlay')?.remove();
}

function beginUpdateTransition(label) {
    if (reconnectPoll) {
        clearInterval(reconnectPoll);
        reconnectPoll = null;
    }
    updateTransitionActive = true;
    updateTransitionDisconnected = false;
    setUpdateTransitionMessage(
        'Installing update',
        `${label} started. Please wait, services are being restarted.`
    );
}

function markUpdateDisconnected() {
    if (!updateTransitionActive || updateTransitionDisconnected) return;
    updateTransitionDisconnected = true;
    setUpdateTransitionMessage(
        'Restoring connection',
        'The update process is restarting AlvaOS. The web interface will reconnect automatically shortly.'
    );
}

function startReconnectPoll() {
    if (!updateTransitionActive || reconnectPoll) return;
    reconnectPoll = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/system/info`, {
                headers: { Authorization: getToken() },
                cache: 'no-store'
            });
            if (res.ok) {
                clearInterval(reconnectPoll);
                reconnectPoll = null;
                setUpdateTransitionMessage('Update completed', 'Web interface is reloading...');
                setTimeout(() => {
                    window.location.reload();
                }, 700);
                return;
            }
            if (res.status === 401) {
                clearInterval(reconnectPoll);
                reconnectPoll = null;
                setUpdateTransitionMessage('Update completed', 'Please log in again...');
                setTimeout(() => {
                    window.location.href = '/login.html';
                }, 700);
            }
        } catch (err) {
            // still restarting
        }
    }, 2500);
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

async function checkAlvaosUpdates(forceRefresh = false) {
    const channel = document.getElementById('update-channel-select')?.value || 'stable';
    lastRelease = null;
    setStatus(`Checking ${channel} channel...`);
    try {
        const forceParam = forceRefresh ? '&force=1' : '';
        const res = await apiFetch(`/updates/alvaos/check?channel=${encodeURIComponent(channel)}${forceParam}`);
        if (!res) return;
        const data = await readJson(res);
        if (data && data.current_version) {
            setCurrentVersion(data.current_version);
        }
        if (!res.ok || !data || data.error) {
            renderRelease(null, false, null);
            setStatus('Update check failed');
            window.showToast(apiErrorMessage(res, data, 'Update check failed'), 'error');
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
            window.showToast(apiErrorMessage(res, data, 'Update failed'), 'error');
            setStatus('Install failed');
            return;
        }
        if (window.setUpdateIndicators) {
            window.setUpdateIndicators({
                available: false,
                version: '',
                checkedAt: Date.now()
            });
        }
        window.showToast('Update started', 'success');
        beginUpdateTransition('AlvaOS-Update');
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
            if (list) list.innerHTML = '<div class="metric-sub">Could not check for updates right now. Your system is unchanged - try again in a moment.</div>';
            renderDebianOsUpgrade(null);
            window.showToast(apiErrorMessage(res, data, 'Debian check failed'), 'error');
            return;
        }

        renderDebianOsUpgrade(data.os_upgrade || null);
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
                    <input type="checkbox" class="debian-package" value="${escapeHtml(pkg.package)}">
                    <span>${escapeHtml(pkg.package)}</span>
                </label>
                <span class="mono-text">${escapeHtml(pkg.version)}</span>
            `;
            list.appendChild(row);
        });
    } catch (err) {
        if (list) list.innerHTML = '<div class="metric-sub">Could not check for updates right now. Your system is unchanged - try again in a moment.</div>';
        renderDebianOsUpgrade(null);
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
            window.showToast(apiErrorMessage(res, data, 'Debian updates failed'), 'error');
            return;
        }
        window.showToast('Debian updates started', 'success');
        pollUpdateStatus();
        await Promise.all([checkDebianUpdates(), loadUpdateHistory()]);
    } catch (err) {
        window.showToast('Debian updates failed', 'error');
    }
}

async function applyAllDebianUpdates() {
    const ok = await window.showConfirm('Apply all Debian updates?\nAll currently available package updates will be installed.');
    if (!ok) return;

    try {
        const res = await apiFetch('/updates/debian/apply', {
            method: 'POST',
            json: {}
        });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            window.showToast(apiErrorMessage(res, data, 'Applying all Debian updates failed'), 'error');
            return;
        }
        window.showToast('All Debian updates started', 'success');
        pollUpdateStatus();
        await Promise.all([checkDebianUpdates(), loadUpdateHistory()]);
    } catch (err) {
        window.showToast('Applying all Debian updates failed', 'error');
    }
}

async function applyDebianOsUpgrade() {
    const osUpgrade = debianOsUpgradeState;
    const target = osUpgrade && osUpgrade.target ? osUpgrade.target : null;
    if (!osUpgrade || !osUpgrade.available || !target || !target.codename) {
        window.showToast('No Debian OS release upgrade available.', 'warning');
        return;
    }

    const currentLabel = formatDebianRelease(osUpgrade.current || {});
    const targetLabel = formatDebianRelease(target);
    const ok = await window.showConfirm(
        `Upgrade Debian OS release?\n${currentLabel} -> ${targetLabel}\n\nThis may take a long time and can require a reboot.`
    );
    if (!ok) return;

    setStatus(`Upgrading OS to ${targetLabel}...`);
    try {
        const res = await apiFetch('/updates/debian/os-upgrade', {
            method: 'POST',
            json: { target_codename: target.codename }
        });
        if (!res) return;
        const data = (await readJson(res)) || {};
        if (!res.ok || !data.success) {
            window.showToast(apiErrorMessage(res, data, 'Debian OS upgrade failed'), 'error');
            return;
        }
        window.showToast('Debian OS upgrade completed. Reboot recommended.', 'success');
        pollUpdateStatus();
        await Promise.all([checkDebianUpdates(), loadUpdateHistory()]);
    } catch (err) {
        window.showToast('Debian OS upgrade failed', 'error');
    }
}

async function scanOfflinePackages(listId, packageType) {
    const list = document.getElementById(listId);
    if (list) list.innerHTML = '<div class="metric-sub">Scanning...</div>';
    try {
        const res = await apiFetch('/updates/offline/scan', { method: 'POST', json: {} });
        if (!res) return;
        const data = await readJson(res);
        if (!res.ok || !data) {
            if (list) list.innerHTML = '<div class="metric-sub">Could not read the USB stick. Check that it is plugged in and uses FAT32, NTFS, EXT4 or exFAT.</div>';
            window.showToast(apiErrorMessage(res, data, 'Offline scan failed'), 'error');
            return;
        }
        const packages = (data.packages || []).filter(pkg => pkg.type === packageType);
        if (!packages.length) {
            if (list) list.innerHTML = `
                <div class="metric-sub">
                    No ${packageType === 'alvaos' ? 'AlvaOS' : 'system'} packages found.<br>
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
                    <span>${escapeHtml(pkg.name)}</span>
                    <span class="metric-sub">${escapeHtml(pkg.path)}</span>
                </div>
                <button class="btn-secondary" data-offline-path="${escapeHtml(pkg.path)}">Install</button>
            `;
            list.appendChild(row);
        });

        list.querySelectorAll('button[data-offline-path]').forEach(btn => {
            btn.addEventListener('click', () => applyOfflinePackage(btn.dataset.offlinePath, listId, packageType));
        });
    } catch (err) {
        if (list) list.innerHTML = '<div class="metric-sub">Could not read the USB stick. Check that it is plugged in and uses FAT32, NTFS, EXT4 or exFAT.</div>';
        window.showToast('Offline scan failed', 'error');
    }
}

async function applyOfflinePackage(path, listId, packageType) {
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
            window.showToast(apiErrorMessage(res, data, 'Offline update failed'), 'error');
            return;
        }
        if (packageType === 'alvaos') {
            window.showToast('Offline update started', 'success');
            beginUpdateTransition('Offline-Update');
            pollUpdateStatus();
        } else {
            window.showToast('Package installed', 'success');
            await Promise.all([scanOfflinePackages(listId, packageType), loadUpdateHistory()]);
        }
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
                <span>${escapeHtml(entry.type || 'update')} ${entry.package ? `- ${escapeHtml(entry.package)}` : ''}</span>
                <span class="metric-sub">${escapeHtml(entry.timestamp || '')}</span>
            </div>
            <span class="mono-text">${escapeHtml(entry.packages ? entry.packages.length + ' pkgs' : '')}</span>
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
    const autoApplyDebian = document.getElementById('settings-auto-apply-debian');
    const channel = document.getElementById('settings-channel');
    if (auto) auto.checked = !!data.auto_check;
    if (autoApply) autoApply.checked = !!data.auto_apply;
    if (autoApplyDebian) autoApplyDebian.checked = !!data.auto_apply_debian;
    if (channel) channel.value = data.channel || 'stable';
    const channelSelect = document.getElementById('update-channel-select');
    if (channelSelect) channelSelect.value = data.channel || 'stable';
}

async function saveSettings() {
    const auto = document.getElementById('settings-auto-check')?.checked || false;
    const autoApply = document.getElementById('settings-auto-apply')?.checked || false;
    const autoApplyDebian = document.getElementById('settings-auto-apply-debian')?.checked || false;
    const channel = document.getElementById('settings-channel')?.value || 'stable';
    const res = await apiFetch('/updates/settings', {
        method: 'POST',
        json: { auto_check: auto, auto_apply: autoApply, auto_apply_debian: autoApplyDebian, channel }
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
        try {
            const res = await apiFetch('/updates/status', {
                skipAuthRedirect: updateTransitionActive
            });
            if (!res) return;

            if (updateTransitionActive && (res.status === 401 || res.status >= 500)) {
                markUpdateDisconnected();
                if (statusPoll) {
                    clearInterval(statusPoll);
                    statusPoll = null;
                }
                startReconnectPoll();
                return;
            }

            const data = await readJson(res);
            if (!res.ok || !data) return;

            if (data.status && data.status !== 'idle') {
                setStatus(data.message || data.status);
                const percent = data.progress && typeof data.progress.percent === 'number'
                    ? data.progress.percent
                    : (data.status === 'downloading' ? 35 : 60);
                setProgress(true, percent);
                if (updateTransitionActive) {
                    setUpdateTransitionMessage(
                        'Installing update',
                        data.message || 'Update in progress...'
                    );
                }
            } else if (data.status === 'idle') {
                setStatus('Idle');
                setProgress(false, 0);
                clearInterval(statusPoll);
                statusPoll = null;
                if (window.setUpdateIndicators) {
                    window.setUpdateIndicators({
                        available: false,
                        version: '',
                        checkedAt: Date.now()
                    });
                }
                if (updateTransitionActive) {
                    if (updateTransitionDisconnected) {
                        startReconnectPoll();
                    } else {
                        setUpdateTransitionMessage('Update completed', 'Web interface is reloading...');
                        setTimeout(() => {
                            window.location.reload();
                        }, 700);
                    }
                }
            }
        } catch (err) {
            if (updateTransitionActive) {
                markUpdateDisconnected();
                if (statusPoll) {
                    clearInterval(statusPoll);
                    statusPoll = null;
                }
                startReconnectPoll();
            }
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
    document.getElementById('check-updates-btn')?.addEventListener('click', () => checkAlvaosUpdates(true));
    document.getElementById('download-install-btn')?.addEventListener('click', applyAlvaosUpdate);
    document.getElementById('debian-check-btn')?.addEventListener('click', checkDebianUpdates);
    document.getElementById('debian-apply-btn')?.addEventListener('click', applyDebianUpdates);
    document.getElementById('debian-apply-all-btn')?.addEventListener('click', applyAllDebianUpdates);
    document.getElementById('debian-os-upgrade-btn')?.addEventListener('click', applyDebianOsUpgrade);
    document.getElementById('offline-alvaos-scan-btn')?.addEventListener('click', () => scanOfflinePackages('offline-alvaos-list', 'alvaos'));
    document.getElementById('offline-system-scan-btn')?.addEventListener('click', () => scanOfflinePackages('offline-system-list', 'system'));
    document.getElementById('settings-save-btn')?.addEventListener('click', saveSettings);
}

async function init() {
    initTabs();
    initHandlers();
    await loadSettings();
    await checkAlvaosUpdates();
    await checkDebianUpdates();
    await loadUpdateHistory();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
