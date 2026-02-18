let backupSources = [];
let backupTargets = [];
let backupSettings = {};
let backupStatus = {};
let backupSystemState = {};
let buddyState = {};

function backupToken() {
    return localStorage.getItem('alvaos_token') || '';
}

function backupNotify(message, type = 'info') {
    if (window.showToast) {
        window.showToast(message, type);
        return;
    }
    if (type === 'error') console.error(message);
    else console.log(message);
}

function backupEscapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function backupFormatDate(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '-';
    return date.toLocaleString();
}

async function backupApi(path, options = {}) {
    const headers = options.headers || {};
    headers.Authorization = backupToken();
    if (options.json) {
        headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.json);
        delete options.json;
    }
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    if (response.status === 401) {
        window.location.href = '/login.html';
        return null;
    }
    return response;
}

async function backupReadJson(response) {
    if (!response) return null;
    try {
        return await response.json();
    } catch {
        return null;
    }
}

// ----------------------------------------------------------------------------
// UTILS
// ----------------------------------------------------------------------------

function selectedSources() {
    return Array.from(document.querySelectorAll('.backup-source-toggle:checked'))
        .map((el) => String(el.value || '').trim())
        .filter(Boolean);
}

function setSelectOptions(selectId, selectedValue = '', items = []) {
    const selectEl = document.getElementById(selectId);
    if (!selectEl) return;

    const options = [];
    options.push('<option value="">Default save location (auto)</option>');

    const usedPaths = new Set();

    items.forEach((target) => {
        if (!target.path) return;
        usedPaths.add(target.path);
        options.push(`<option value="${backupEscapeHtml(target.path)}">${backupEscapeHtml(target.name)} (${backupEscapeHtml(target.path)})</option>`);
    });

    // If selected value is custom (not in list), add it
    const selectedText = String(selectedValue || '');
    if (selectedText && !usedPaths.has(selectedText)) {
        options.push(`<option value="${backupEscapeHtml(selectedText)}">Custom save location: ${backupEscapeHtml(selectedText)}</option>`);
    }

    selectEl.innerHTML = options.join('');

    if (selectedText) {
        selectEl.value = selectedText;
    } else {
        selectEl.value = "";
    }
}

// ----------------------------------------------------------------------------
// RENDER UI
// ----------------------------------------------------------------------------

function renderSources() {
    const container = document.getElementById('backup-sources');
    if (!container) return;
    if (!backupSources.length) {
        container.innerHTML = '<div class="metric-sub">No available backup sources found. Create Btrfs pools/subvolumes first.</div>';
        return;
    }

    const pb = backupSettings.pool_backup || {};
    const configured = new Set((pb.sources || []).map((p) => String(p)));

    container.innerHTML = backupSources.map((source) => `
        <label class="source-item">
            <input class="backup-source-toggle" type="checkbox" value="${backupEscapeHtml(source.path)}" ${configured.has(source.path) ? 'checked' : ''}>
            <div class="source-meta">
                <div class="source-name">${backupEscapeHtml(source.name)}</div>
                <div class="source-path">${backupEscapeHtml(source.path)}</div>
            </div>
        </label>
    `).join('');
}

function renderDataSnapshots(items) {
    const container = document.getElementById('pool-snapshots-list');
    if (!container) return;
    const snapshots = Array.isArray(items) ? items : [];
    if (!snapshots.length) {
        container.innerHTML = '<div class="metric-sub">No snapshots found.</div>';
        return;
    }

    container.innerHTML = `
        <table class="snapshots-table">
            <thead>
                <tr>
                    <th>Created</th>
                    <th>Source</th>
                    <th>Name</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                ${snapshots.map((entry) => `
                    <tr>
                        <td class="mono-text">${backupEscapeHtml(backupFormatDate(entry.created_at))}</td>
                        <td><div class="source-path" title="${backupEscapeHtml(entry.source_path)}">${backupEscapeHtml(entry.source_path || '-')}</div></td>
                        <td>${backupEscapeHtml(entry.snapshot_name || '-')}</td>
                        <td>
                            <button class="btn-secondary backup-restore-btn" data-snapshot-path="${backupEscapeHtml(entry.snapshot_path || '')}" data-source-path="${backupEscapeHtml(entry.source_path || '')}">
                                Restore
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderSystemSnapshots(items) {
    const container = document.getElementById('system-snapshots-list');
    if (!container) return;
    const snapshots = Array.isArray(items) ? items : [];
    if (!snapshots.length) {
        container.innerHTML = '<div class="metric-sub">No system snapshots found.</div>';
        return;
    }

    container.innerHTML = `
        <table class="snapshots-table">
            <thead>
                <tr>
                    <th>Created</th>
                    <th>Name</th>
                    <th>Path</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                ${snapshots.map((entry) => `
                    <tr>
                        <td class="mono-text">${backupEscapeHtml(backupFormatDate(entry.created_at))}</td>
                        <td>${backupEscapeHtml(entry.snapshot_name || '-')}</td>
                        <td><div class="source-path">${backupEscapeHtml(entry.snapshot_path || '-')}</div></td>
                        <td>
                            <button class="btn-secondary backup-system-rollback-btn" data-snapshot-path="${backupEscapeHtml(entry.snapshot_path || '')}">
                                Prepare Rollback
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function updateStatusUi() {
    // Pool Status
    document.getElementById('pool-last-run').textContent = backupFormatDate(backupStatus.pool_last_run_at);
    document.getElementById('pool-next-run').textContent = backupFormatDate(backupStatus.pool_next_run_at);
    document.getElementById('pool-last-status').textContent = backupStatus.pool_last_status || 'idle';

    const poolError = document.getElementById('pool-last-error');
    if (backupStatus.pool_last_error) {
        poolError.textContent = `Error: ${backupStatus.pool_last_error}`;
        poolError.style.display = 'block';
    } else {
        poolError.style.display = 'none';
    }

    // System Status
    document.getElementById('system-last-run').textContent = backupFormatDate(backupStatus.system_last_run_at);
    document.getElementById('system-next-run').textContent = backupFormatDate(backupStatus.system_next_run_at);
    document.getElementById('system-last-status').textContent = backupStatus.system_last_status || 'idle';

    const sysError = document.getElementById('system-last-error');
    if (backupStatus.system_last_error) {
        sysError.textContent = `Error: ${backupStatus.system_last_error}`;
        sysError.style.display = 'block';
    } else {
        sysError.style.display = 'none';
    }

    // System Info
    const sysSupport = document.getElementById('system-support-info');
    const systemRunBtn = document.getElementById('system-run-btn');
    if (backupSystemState.supported === false) {
        sysSupport.textContent = `Unsupported (${backupSystemState.reason || 'unknown'})`;
        sysSupport.style.color = 'var(--error)';
        if (systemRunBtn) systemRunBtn.disabled = true;
    } else {
        sysSupport.textContent = `Supported (Root Subvol: ${backupSystemState.root_subvolume_id})`;
        sysSupport.style.color = 'var(--success)';
        if (systemRunBtn) systemRunBtn.disabled = false;
    }

    const sysPending = document.getElementById('system-pending-rollback');
    if (backupStatus.system_pending_reboot) {
        sysPending.textContent = `YES - Will rollback to ${backupStatus.system_pending_snapshot_path} on reboot`;
        sysPending.style.color = 'var(--warning)';
    } else {
        sysPending.textContent = 'None';
        sysPending.style.color = 'var(--text-secondary)';
    }
}

function fillSettingsUi() {
    // Pool Settings
    const pb = backupSettings.pool_backup || {};
    document.getElementById('pool-enabled').checked = !!pb.enabled;
    document.getElementById('pool-interval').value = String(pb.interval_minutes || 1440);
    document.getElementById('pool-keep-last').value = String(pb.keep_last || 30);

    // System Settings
    const sb = backupSettings.system_backup || {};
    document.getElementById('system-enabled').checked = !!sb.enabled;
    document.getElementById('system-interval').value = String(sb.interval_minutes || 10080);
    document.getElementById('system-keep-last').value = String(sb.keep_last || 10);
}

function renderBuddyStatus() {
    const identity = buddyState.identity || {};
    const peers = Array.isArray(buddyState.peers) ? buddyState.peers : [];
    const tunnel = buddyState.tunnel || {};

    const supportedEl = document.getElementById('buddy-support-info');
    const tunnelEl = document.getElementById('buddy-tunnel-state');
    const nodeEl = document.getElementById('buddy-node-id');
    const keyEl = document.getElementById('buddy-public-key');
    const ipEl = document.getElementById('buddy-tunnel-ip');
    const portEl = document.getElementById('buddy-listen-port');
    const peersEl = document.getElementById('buddy-peers-list');

    if (supportedEl) supportedEl.textContent = buddyState.supported ? 'Yes' : 'No';
    if (tunnelEl) tunnelEl.textContent = (tunnel.state || 'unknown').toUpperCase();
    if (nodeEl) nodeEl.textContent = identity.node_id || '-';
    if (keyEl) keyEl.textContent = identity.public_key || '-';
    if (ipEl) ipEl.textContent = identity.tunnel_ip || '-';
    if (portEl) portEl.textContent = String(identity.listen_port || '-');

    if (!peersEl) return;
    if (!peers.length) {
        peersEl.innerHTML = '<div class="metric-sub">No buddies paired yet.</div>';
        return;
    }

    peersEl.innerHTML = peers.map((peer) => `
        <div class="list-item" style="align-items:flex-start; gap:12px;">
            <div style="display:flex; flex-direction:column; gap:3px;">
                <div><strong>${backupEscapeHtml(peer.name || peer.node_id || 'Buddy')}</strong></div>
                <div class="metric-sub mono-text">${backupEscapeHtml(peer.node_id || '-')}</div>
                <div class="metric-sub">Endpoint: ${backupEscapeHtml(peer.endpoint || '(not set)')}</div>
                <div class="metric-sub">Tunnel IP: ${backupEscapeHtml(peer.tunnel_ip || '-')}</div>
                <div class="metric-sub">Status: ${backupEscapeHtml(peer.status || 'unknown')}</div>
                ${peer.last_error ? `<div class="metric-sub" style="color: var(--error);">Error: ${backupEscapeHtml(peer.last_error)}</div>` : ''}
            </div>
            <button class="btn-secondary buddy-remove-peer-btn" data-node-id="${backupEscapeHtml(peer.node_id || '')}">Remove</button>
        </div>
    `).join('');
}

// ----------------------------------------------------------------------------
// ACTIONS
// ----------------------------------------------------------------------------

async function savePoolSettings() {
    const payload = {
        pool_backup: {
            enabled: document.getElementById('pool-enabled').checked,
            interval_minutes: Number(document.getElementById('pool-interval').value || 1440),
            keep_last: Number(document.getElementById('pool-keep-last').value || 30),
            target_path: document.getElementById('pool-target-path').value || '',
            sources: selectedSources()
        }
    };

    const response = await backupApi('/backup/settings', { method: 'POST', json: payload });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to save pool settings', 'error');
        return;
    }
    backupSettings = data.settings || backupSettings;
    backupStatus = data.status || backupStatus;
    fillSettingsUi();
    updateStatusUi();
    backupNotify('Pool backup settings saved', 'success');
}

async function saveSystemSettings() {
    const payload = {
        system_backup: {
            enabled: document.getElementById('system-enabled').checked,
            interval_minutes: Number(document.getElementById('system-interval').value || 10080),
            keep_last: Number(document.getElementById('system-keep-last').value || 10),
            target_path: document.getElementById('system-target-path').value || ''
        }
    };
    // Note: sources for system are implicit (root)

    const response = await backupApi('/backup/settings', { method: 'POST', json: payload });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to save system settings', 'error');
        return;
    }
    backupSettings = data.settings || backupSettings;
    backupStatus = data.status || backupStatus;
    fillSettingsUi();
    updateStatusUi();
    backupNotify('System backup settings saved', 'success');
}

async function runPoolBackupNow() {
    const sources = selectedSources();
    if (!sources.length) {
        backupNotify('Select at least one source first.', 'warning');
        return;
    }

    const manualTarget = document.getElementById('pool-run-target-path')?.value
        || document.getElementById('pool-target-path')?.value
        || undefined;

    backupNotify('Starting pool backup...', 'info');
    const response = await backupApi('/backup/run', {
        method: 'POST',
        json: {
            backup_type: 'pool',
            sources: sources,
            target_path: manualTarget
        }
    });

    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Backup failed', 'error');
        return;
    }

    const createdCount = Array.isArray(data.created) ? data.created.length : 0;
    const failedCount = Array.isArray(data.failed) ? data.failed.length : 0;
    if (failedCount > 0) backupNotify(`Backup finished with errors: ${failedCount} failed`, 'warning');
    else backupNotify(`Pool backup completed (${createdCount} snapshots created)`, 'success');

    await Promise.all([loadBackupStatus(), loadDataSnapshots()]);
}

async function runSystemBackupNow() {
    if (backupSystemState.supported === false) {
        backupNotify(`System backup is not supported on this system (${backupSystemState.reason || 'unknown'})`, 'error');
        return;
    }

    const manualTarget = document.getElementById('system-run-target-path')?.value
        || document.getElementById('system-target-path')?.value
        || undefined;

    backupNotify('Starting system backup...', 'info');
    const response = await backupApi('/backup/run', {
        method: 'POST',
        json: {
            backup_type: 'system',
            target_path: manualTarget
        }
    });

    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'System backup failed', 'error');
        return;
    }

    backupNotify('System backup completed successfully', 'success');
    await Promise.all([loadBackupStatus(), loadSystemSnapshots()]);
}

async function restoreDataSnapshot(snapshotPath, sourcePath) {
    if (!snapshotPath) return;
    const ok = await window.showConfirm('Restore this snapshot?\nCurrent data will be replaced and moved to a pre-restore backup path.');
    if (!ok) return;

    backupNotify('Restoring snapshot...', 'info');
    const response = await backupApi('/backup/restore', {
        method: 'POST',
        json: { snapshot_path: snapshotPath, source_path: sourcePath || undefined }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Restore failed', 'error');
        return;
    }
    backupNotify('Snapshot restore completed', 'success');
    await loadDataSnapshots();
}

async function prepareSystemRollback(snapshotPath) {
    if (!snapshotPath) return;
    const ok = await window.showConfirm('Prepare full system rollback to this snapshot?\nSystem will boot into that snapshot after reboot.');
    if (!ok) return;

    const response = await backupApi('/backup/system/rollback', {
        method: 'POST',
        json: { snapshot_path: snapshotPath }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to prepare system rollback', 'error');
        return;
    }
    backupNotify('Rollback prepared. Reboot required.', 'success');
    await loadBackupStatus();
}

// ----------------------------------------------------------------------------
// LOADERS
// ----------------------------------------------------------------------------

async function loadBackupSettings() {
    const response = await backupApi('/backup/settings');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify('Failed to load settings', 'error');
        return;
    }
    backupSettings = data.settings || {};
    backupStatus = data.status || {};
    fillSettingsUi();
    updateStatusUi();

    // Set target options after loading settings
    // We assume targets are loaded separately, but we can try to set values now if targets exist
    // Actually we await loadBackupTargets in init
}

async function loadBackupStatus() {
    const response = await backupApi('/backup/status');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) return;
    backupStatus = data.status || backupStatus || {};
    backupSystemState = data.system || backupSystemState || {};
    updateStatusUi();
}

async function loadBuddyStatus() {
    const response = await backupApi('/backup/pairing/status');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to load buddy status', 'error');
        return;
    }
    buddyState = data;
    renderBuddyStatus();
}

async function generateBuddyToken() {
    const endpoint = document.getElementById('buddy-endpoint-input')?.value?.trim() || '';
    const expiresMinutes = Number(document.getElementById('buddy-token-expiry')?.value || 20);

    const response = await backupApi('/backup/pairing/generate', {
        method: 'POST',
        json: {
            endpoint: endpoint,
            expires_minutes: expiresMinutes
        }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to generate pairing token', 'error');
        return;
    }

    const tokenEl = document.getElementById('buddy-generated-token');
    if (tokenEl) tokenEl.value = data.token || '';
    backupNotify('Pairing token generated', 'success');
    await loadBuddyStatus();
}

async function copyBuddyToken() {
    const token = document.getElementById('buddy-generated-token')?.value || '';
    if (!token) {
        backupNotify('No token to copy', 'warning');
        return;
    }
    try {
        await navigator.clipboard.writeText(token);
        backupNotify('Token copied to clipboard', 'success');
    } catch {
        backupNotify('Failed to copy token', 'error');
    }
}

async function validateBuddyToken() {
    const token = document.getElementById('buddy-token-input')?.value?.trim() || '';
    if (!token) {
        backupNotify('Paste a buddy token first', 'warning');
        return;
    }
    const endpointOverride = document.getElementById('buddy-endpoint-override')?.value?.trim() || '';
    const nameOverride = document.getElementById('buddy-name-override')?.value?.trim() || '';

    const response = await backupApi('/backup/pairing/validate', {
        method: 'POST',
        json: {
            token: token,
            endpoint_override: endpointOverride,
            name_override: nameOverride
        }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Pairing failed', 'error');
        return;
    }

    backupNotify('Buddy paired successfully', 'success');
    const tokenInput = document.getElementById('buddy-token-input');
    if (tokenInput) tokenInput.value = '';
    await loadBuddyStatus();
}

async function restartBuddyTunnel() {
    const response = await backupApi('/backup/pairing/restart', {
        method: 'POST',
        json: {}
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to restart buddy tunnel', 'error');
        return;
    }
    backupNotify('Buddy tunnel restarted', 'success');
    await loadBuddyStatus();
}

async function removeBuddyPeer(nodeId) {
    const target = String(nodeId || '').trim();
    if (!target) return;
    const ok = await window.showConfirm('Remove this buddy peer?');
    if (!ok) return;

    const response = await backupApi('/backup/pairing/remove', {
        method: 'POST',
        json: { node_id: target }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to remove buddy peer', 'error');
        return;
    }
    backupNotify('Buddy removed', 'success');
    await loadBuddyStatus();
}

async function loadBackupSources() {
    const response = await backupApi('/backup/sources');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify('Failed to load backup sources', 'error');
        return;
    }
    backupSources = data.sources || [];
    renderSources();
}

async function loadBackupTargets() {
    const response = await backupApi('/backup/targets');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify('Failed to load backup targets', 'error');
        return;
    }
    backupTargets = data.targets || [];

    // Filter targets for pool vs system?
    // Generally all targets are valid for pool.
    // For system, only those on root filesystem are valid, but backend logic enforces it.
    // We can show all for now.

    // Pool Target Select
    const pb = backupSettings.pool_backup || {};
    setSelectOptions('pool-target-path', pb.target_path || '', backupTargets);
    setSelectOptions('pool-run-target-path', pb.target_path || '', backupTargets);

    // System Target Select
    const sb = backupSettings.system_backup || {};
    setSelectOptions('system-target-path', sb.target_path || '', backupTargets);
    setSelectOptions('system-run-target-path', sb.target_path || '', backupTargets);
}

async function loadDataSnapshots() {
    const response = await backupApi('/backup/snapshots');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify('Failed to load snapshots', 'error');
        return;
    }
    renderDataSnapshots(data.snapshots || []);
}

async function loadSystemSnapshots() {
    const response = await backupApi('/backup/system/snapshots');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify('Failed to load system snapshots', 'error');
        return;
    }
    renderSystemSnapshots(data.snapshots || []);
}

// ----------------------------------------------------------------------------
// INIT
// ----------------------------------------------------------------------------

function initTabs() {
    const buttons = document.querySelectorAll('.tab-btn');
    buttons.forEach((btn) => {
        btn.addEventListener('click', () => {
            buttons.forEach((b) => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.querySelectorAll('.tab-panel').forEach((panel) => {
                panel.classList.toggle('active', panel.id === `tab-${tab}`);
            });
        });
    });
}

function initBackupHandlers() {
    // Pool Handlers
    document.getElementById('pool-sources-refresh-btn')?.addEventListener('click', loadBackupSources);
    document.getElementById('pool-snapshots-refresh-btn')?.addEventListener('click', loadDataSnapshots);
    document.getElementById('pool-save-settings-btn')?.addEventListener('click', savePoolSettings);
    document.getElementById('pool-run-btn')?.addEventListener('click', runPoolBackupNow);
    document.getElementById('pool-target-path')?.addEventListener('change', (event) => {
        const runTarget = document.getElementById('pool-run-target-path');
        if (runTarget) runTarget.value = event.target?.value || '';
    });

    // System Handlers
    document.getElementById('system-run-btn')?.addEventListener('click', runSystemBackupNow);
    document.getElementById('system-save-settings-btn')?.addEventListener('click', saveSystemSettings);
    document.getElementById('system-snapshots-refresh-btn')?.addEventListener('click', loadSystemSnapshots);
    document.getElementById('system-target-path')?.addEventListener('change', (event) => {
        const runTarget = document.getElementById('system-run-target-path');
        if (runTarget) runTarget.value = event.target?.value || '';
    });

    // Delegate Event Listeners for Lists
    document.getElementById('pool-snapshots-list')?.addEventListener('click', (event) => {
        const btn = event.target.closest('.backup-restore-btn');
        if (!btn) return;
        restoreDataSnapshot(btn.dataset.snapshotPath || '', btn.dataset.sourcePath || '');
    });

    document.getElementById('system-snapshots-list')?.addEventListener('click', (event) => {
        const btn = event.target.closest('.backup-system-rollback-btn');
        if (!btn) return;
        prepareSystemRollback(btn.dataset.snapshotPath || '');
    });

    // Buddy Handlers
    document.getElementById('buddy-refresh-btn')?.addEventListener('click', loadBuddyStatus);
    document.getElementById('buddy-restart-tunnel-btn')?.addEventListener('click', restartBuddyTunnel);
    document.getElementById('buddy-generate-token-btn')?.addEventListener('click', generateBuddyToken);
    document.getElementById('buddy-copy-token-btn')?.addEventListener('click', copyBuddyToken);
    document.getElementById('buddy-validate-token-btn')?.addEventListener('click', validateBuddyToken);
    document.getElementById('buddy-peers-list')?.addEventListener('click', (event) => {
        const btn = event.target.closest('.buddy-remove-peer-btn');
        if (!btn) return;
        removeBuddyPeer(btn.dataset.nodeId || '');
    });
}

async function initBackupPage() {
    initTabs();
    initBackupHandlers();

    await loadBackupSettings(); // Load first to have settings for checks
    await Promise.all([
        loadBackupSources(),
        loadBackupTargets(), // Relies on settings for default selection
        loadDataSnapshots(),
        loadSystemSnapshots(),
        loadBackupStatus(),
        loadBuddyStatus()
    ]);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBackupPage);
} else {
    initBackupPage();
}
