let backupSources = [];
let backupTargets = [];
let backupSettings = {};
let backupStatus = {};
let backupSystemState = {};
let buddyState = {};
let buddySettings = {};
let buddyRemoteSnapshots = [];
let buddyRemoteVault = {};
let selectedBuddyRestorePeerId = '';

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
    let response;
    try {
        response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    } catch (error) {
        const detail = String(error?.message || '').trim();
        backupNotify(detail ? `Network error: ${detail}` : 'Network error while contacting backup API', 'error');
        return null;
    }
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

function selectedBuddyPeerSources(nodeId) {
    const target = String(nodeId || '').trim();
    return Array.from(document.querySelectorAll('.buddy-peer-source-toggle'))
        .filter((el) => String(el.dataset.nodeId || '').trim() === target && !!el.checked)
        .map((el) => String(el.value || '').trim())
        .filter(Boolean);
}

function buddyIncomingOptions() {
    const options = [];
    const seen = new Set();
    const sourceList = Array.isArray(backupSources) ? backupSources : [];
    sourceList
        .filter((item) => String(item?.kind || '').toLowerCase() === 'subvolume')
        .forEach((item) => {
            const path = String(item?.path || '').trim();
            if (!path || seen.has(path)) return;
            seen.add(path);
            options.push({
                path,
                name: String(item?.name || path)
            });
        });
    return options;
}

function setSelectOptions(selectId, selectedValue = '', items = []) {
    const selectEl = document.getElementById(selectId);
    if (!selectEl) return;

    const options = [];
    options.push('<option value="" disabled selected>Select target path</option>');

    const usedPaths = new Set();

    items.forEach((target) => {
        if (!target.path) return;
        usedPaths.add(target.path);
        options.push(`<option value="${backupEscapeHtml(target.path)}">${backupEscapeHtml(target.name)} (${backupEscapeHtml(target.path)})</option>`);
    });

    // Keep showing a previously configured path if it is no longer available.
    const selectedText = String(selectedValue || '');
    if (selectedText && !usedPaths.has(selectedText)) {
        options.push(`<option value="${backupEscapeHtml(selectedText)}">Previously configured: ${backupEscapeHtml(selectedText)}</option>`);
    }

    selectEl.innerHTML = options.join('');

    if (selectedText) {
        selectEl.value = selectedText;
    } else {
        selectEl.value = "";
    }
}

function setBuddyIncomingPathOptions(selectedValue = '') {
    const selectEl = document.getElementById('buddy-incoming-path');
    if (!selectEl) return;
    const entries = buddyIncomingOptions();
    const options = [];
    const seen = new Set();

    options.push('<option value="" disabled selected>Select incoming path</option>');
    entries.forEach((entry) => {
        seen.add(entry.path);
        options.push(`<option value="${backupEscapeHtml(entry.path)}">${backupEscapeHtml(entry.name)} (${backupEscapeHtml(entry.path)})</option>`);
    });

    const selectedText = String(selectedValue || '');
    if (selectedText && !seen.has(selectedText)) {
        options.push(`<option value="${backupEscapeHtml(selectedText)}">Previously configured: ${backupEscapeHtml(selectedText)}</option>`);
    }
    selectEl.innerHTML = options.join('');
    if (selectedText) {
        selectEl.value = selectedText;
    } else {
        selectEl.value = '';
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

function renderBuddyPeerSourcePicker(nodeId, configuredSources = []) {
    const normalizedNodeId = String(nodeId || '').trim();
    const selected = new Set((Array.isArray(configuredSources) ? configuredSources : []).map((path) => String(path)));
    const buddySources = (Array.isArray(backupSources) ? backupSources : [])
        .filter((source) => String(source?.kind || '').toLowerCase() === 'subvolume');
    if (!buddySources.length) {
        return '<div class="metric-sub">No eligible subvolume sources found.</div>';
    }
    return `
        <div class="buddy-peer-sources">
            ${buddySources.map((source) => `
                <label class="buddy-peer-source-item">
                    <input
                        type="checkbox"
                        class="buddy-peer-source-toggle"
                        data-node-id="${backupEscapeHtml(normalizedNodeId)}"
                        value="${backupEscapeHtml(source.path)}"
                        ${selected.has(source.path) ? 'checked' : ''}
                    >
                    <div class="source-meta">
                        <div class="source-name">${backupEscapeHtml(source.name)}</div>
                        <div class="source-path">${backupEscapeHtml(source.path)}</div>
                    </div>
                </label>
            `).join('')}
        </div>
    `;
}

function backupFolderName(path) {
    const trimmed = String(path || '').replace(/\/+$/, '');
    return trimmed.split('/').pop() || trimmed || '-';
}

// Newest few restore points stay visible, older ones fold away.
function snapshotListHtml(rows) {
    const VISIBLE = 5;
    const rest = rows.slice(VISIBLE);
    return `<div class="snap-list">${rows.slice(0, VISIBLE).join('')}</div>`
        + (rest.length
            ? `<details class="snap-more"><summary>Show ${rest.length} older</summary><div class="snap-list">${rest.join('')}</div></details>`
            : '');
}

function renderDataSnapshots(items) {
    const container = document.getElementById('pool-snapshots-list');
    if (!container) return;
    const snapshots = Array.isArray(items) ? items : [];
    if (!snapshots.length) {
        container.innerHTML = '<div class="metric-sub">No restore points yet. They appear after the first backup.</div>';
        return;
    }

    container.innerHTML = snapshotListHtml(snapshots.map((entry) => `
        <div class="snap-row">
            <div class="snap-info" title="${backupEscapeHtml(entry.source_path)} &bull; ${backupEscapeHtml(entry.snapshot_name)}">
                <div class="snap-when">${backupEscapeHtml(backupFolderName(entry.source_path))}</div>
                <div class="snap-meta">${backupEscapeHtml(backupFormatDate(entry.created_at))}</div>
            </div>
            <div class="snap-actions">
                <button class="btn-secondary backup-restore-btn" data-snapshot-path="${backupEscapeHtml(entry.snapshot_path || '')}" data-source-path="${backupEscapeHtml(entry.source_path || '')}">
                    Restore
                </button>
                <button class="btn-secondary backup-delete-btn" data-snapshot-path="${backupEscapeHtml(entry.snapshot_path || '')}">
                    Delete
                </button>
            </div>
        </div>
    `));
}

function renderSystemSnapshots(items) {
    const container = document.getElementById('system-snapshots-list');
    if (!container) return;
    const snapshots = Array.isArray(items) ? items : [];
    if (!snapshots.length) {
        container.innerHTML = '<div class="metric-sub">No restore points yet. They appear after the first system backup.</div>';
        return;
    }

    container.innerHTML = snapshotListHtml(snapshots.map((entry) => `
        <div class="snap-row">
            <div class="snap-info" title="${backupEscapeHtml(entry.snapshot_path)}">
                <div class="snap-when">${backupEscapeHtml(backupFormatDate(entry.created_at))}</div>
            </div>
            <div class="snap-actions">
                <button class="btn-secondary backup-system-rollback-btn" data-snapshot-path="${backupEscapeHtml(entry.snapshot_path || '')}">
                    Prepare rollback
                </button>
                <button class="btn-secondary backup-system-delete-btn" data-snapshot-path="${backupEscapeHtml(entry.snapshot_path || '')}">
                    Delete
                </button>
            </div>
        </div>
    `));
}

// Map a backup run status into a calm pill and tint the status icon to match.
// A backup that never ran and is not scheduled reads "Not set up", not "Healthy".
function setStatusPill(pillId, icId, status, error, notSetUp = false) {
    const pill = document.getElementById(pillId);
    const ic = document.getElementById(icId);
    const value = String(status || '').toLowerCase();
    let cls = 'ok';
    let label = 'Healthy';
    if (error || value === 'error') {
        cls = 'bad';
        label = 'Needs attention';
    } else if (value === 'partial') {
        cls = 'warn';
        label = 'Needs attention';
    } else if (value === 'success') {
        cls = 'ok';
        label = 'Healthy';
    } else if (notSetUp) {
        cls = 'warn';
        label = 'Not set up';
    } else {
        cls = 'ok';
        label = 'Ready';
    }
    if (pill) {
        pill.className = `pill ${cls}`;
        pill.textContent = label;
    }
    if (ic) {
        ic.classList.toggle('warn', cls !== 'ok');
    }
}

// One plain sentence instead of raw timestamps and status codes.
function backupSummaryText(enabled, lastAt, nextAt, lastStatus) {
    const outcome = { success: 'completed', partial: 'finished with warnings', error: 'failed' }[String(lastStatus || '').toLowerCase()] || '';
    const next = enabled && nextAt ? `Next: ${backupFormatDate(nextAt)}.` : '';
    if (!lastAt) {
        if (enabled) return `No backup yet. ${next || 'The first one runs soon.'}`.trim();
        return 'No backup yet. Turn on automatic backups or press Back up now.';
    }
    const last = `Last backup ${backupFormatDate(lastAt)}${outcome ? ` (${outcome})` : ''}.`;
    return `${last} ${enabled ? next : 'Automatic backups are off.'}`.trim();
}

function updateStatusUi() {
    const poolEnabled = !!(backupSettings.pool_backup || {}).enabled;
    const systemEnabled = !!(backupSettings.system_backup || {}).enabled;

    // Data status
    document.getElementById('pool-status-sub').textContent = backupSummaryText(
        poolEnabled, backupStatus.pool_last_run_at, backupStatus.pool_next_run_at, backupStatus.pool_last_status);
    setStatusPill('pool-status-pill', 'pool-status-ic', backupStatus.pool_last_status, backupStatus.pool_last_error,
        !backupStatus.pool_last_run_at && !poolEnabled);

    const poolError = document.getElementById('pool-last-error');
    if (backupStatus.pool_last_error) {
        poolError.textContent = `Error: ${backupStatus.pool_last_error}`;
        poolError.style.display = 'block';
    } else {
        poolError.style.display = 'none';
    }

    // System status
    document.getElementById('system-status-sub').textContent = backupSummaryText(
        systemEnabled, backupStatus.system_last_run_at, backupStatus.system_next_run_at, backupStatus.system_last_status);
    setStatusPill('system-status-pill', 'system-status-ic', backupStatus.system_last_status, backupStatus.system_last_error,
        !backupStatus.system_last_run_at && !systemEnabled);

    const sysError = document.getElementById('system-last-error');
    if (backupStatus.system_last_error) {
        sysError.textContent = `Error: ${backupStatus.system_last_error}`;
        sysError.style.display = 'block';
    } else {
        sysError.style.display = 'none';
    }

    // System info: only shown when there is something to say.
    const sysSupport = document.getElementById('system-support-info');
    const systemRunBtn = document.getElementById('system-run-btn');
    const unsupported = backupSystemState.supported === false;
    if (unsupported) {
        sysSupport.textContent = `Not available: ${backupSystemState.reason || 'unknown reason'}`;
        sysSupport.style.color = 'var(--error)';
    } else {
        sysSupport.textContent = 'Yes';
        sysSupport.style.color = 'var(--accent-success)';
    }
    if (systemRunBtn) systemRunBtn.disabled = unsupported;

    const sysPending = document.getElementById('system-pending-rollback');
    if (backupStatus.system_pending_reboot) {
        sysPending.textContent = `Yes. This NAS returns to ${backupStatus.system_pending_snapshot_path} at the next reboot.`;
        sysPending.style.color = 'var(--accent-warning)';
    } else {
        sysPending.textContent = 'None';
        sysPending.style.color = 'var(--text-secondary)';
    }
    const infoGrid = sysSupport.closest('.backup-info-grid');
    if (infoGrid) infoGrid.style.display = unsupported || backupStatus.system_pending_reboot ? '' : 'none';
}

function fillSettingsUi() {
    // Pool Settings
    const pb = backupSettings.pool_backup || {};
    document.getElementById('pool-enabled').checked = !!pb.enabled;
    document.getElementById('pool-interval').value = String(pb.interval_minutes || 1440);
    document.getElementById('pool-keep-last').value = String(pb.keep_last || 30);
    document.getElementById('pool-interval').disabled = !pb.enabled;

    // System Settings
    const sb = backupSettings.system_backup || {};
    document.getElementById('system-enabled').checked = !!sb.enabled;
    document.getElementById('system-interval').value = String(sb.interval_minutes || 10080);
    document.getElementById('system-keep-last').value = String(sb.keep_last || 10);
    document.getElementById('system-interval').disabled = !sb.enabled;
}

function fillBuddySettingsUi() {
    setBuddyIncomingPathOptions(buddySettings.incoming_path || '');

    const incomingQuotaEl = document.getElementById('buddy-incoming-quota');
    if (incomingQuotaEl) incomingQuotaEl.value = String(buddySettings.incoming_quota_gb || 200);

    const encryptionEnabledEl = document.getElementById('buddy-encryption-enabled');
    if (encryptionEnabledEl) encryptionEnabledEl.checked = !!buddySettings.encryption_enabled;
    const encryptionPasswordEl = document.getElementById('buddy-encryption-password');
    if (encryptionPasswordEl) {
        encryptionPasswordEl.value = '';
    }
    updateBuddySecurityUi();
}

function updateBuddySecurityUi() {
    const enabledEl = document.getElementById('buddy-encryption-enabled');
    const enabled = !!enabledEl?.checked;
    const passwordEl = document.getElementById('buddy-encryption-password');
    const passwordWrap = document.getElementById('buddy-encryption-settings');
    const stateEl = document.getElementById('buddy-encryption-state');
    const helpEl = document.getElementById('buddy-encryption-help');
    const hasStoredPassword = !!buddySettings?.encryption_password_set;

    if (stateEl) {
        stateEl.textContent = enabled ? 'Enabled' : 'Disabled';
        stateEl.classList.toggle('on', enabled);
        stateEl.classList.toggle('off', !enabled);
    }

    if (passwordWrap) {
        passwordWrap.classList.toggle('disabled', !enabled);
    }

    if (passwordEl) {
        passwordEl.disabled = !enabled;
        if (!enabled) {
            passwordEl.placeholder = 'Enable encryption to set a password';
        } else {
            passwordEl.placeholder = hasStoredPassword
                ? 'Password already set (enter to change)'
                : 'Set encryption password';
        }
    }

    if (helpEl) {
        helpEl.textContent = enabled
            ? 'When enabled, the password is required for restore and rollback.'
            : 'Encryption is disabled. Restores do not require an extra password.';
    }
}

function getRollbackPassphraseIfNeeded() {
    if (!buddySettings?.encryption_enabled) {
        return { ok: true, passphrase: '' };
    }
    return { ok: false, needsPrompt: true, passphrase: '' };
}

async function requestBackupPassphrase(message, confirmLabel = 'Continue') {
    const passphrase = await window.showPrompt(message, {
        type: 'password',
        label: 'Encryption Password',
        placeholder: 'Encryption password',
        confirmLabel
    });
    if (passphrase === null) return { ok: false, passphrase: '' };
    if (!String(passphrase).trim()) {
        backupNotify('Encryption password is required', 'warning');
        return { ok: false, passphrase: '' };
    }
    return { ok: true, passphrase: String(passphrase) };
}

function showRebootRequiredDialog() {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal-content">
            <div class="modal-title">Reboot Required</div>
            <div class="modal-body">
                The restore was prepared successfully.<br>
                A reboot is required to activate the restored snapshot.
            </div>
            <div class="modal-actions">
                <button id="backup-reboot-later" class="btn-secondary">Later</button>
                <button id="backup-reboot-now" class="btn-primary">Reboot now</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    const rebootBtn = overlay.querySelector('#backup-reboot-now');
    const laterBtn = overlay.querySelector('#backup-reboot-later');
    if (rebootBtn) rebootBtn.focus();

    return new Promise((resolve) => {
        const finish = (value) => {
            overlay.remove();
            resolve(value);
        };
        if (laterBtn) laterBtn.onclick = () => finish(false);
        if (rebootBtn) rebootBtn.onclick = () => finish(true);
    });
}

async function rebootSystemNow() {
    const response = await backupApi('/system/power', {
        method: 'POST',
        json: { action: 'reboot' }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok) {
        backupNotify(data?.error || 'Failed to trigger reboot', 'error');
        return false;
    }
    if (window.handleConnectionError) {
        window.handleConnectionError();
    } else {
        backupNotify('Reboot command sent', 'success');
    }
    return true;
}

function buddyPeerField(className, nodeId) {
    const target = String(nodeId || '').trim();
    return Array.from(document.querySelectorAll(`.${className}`))
        .find((el) => String(el.dataset.nodeId || '').trim() === target) || null;
}

function getBuddyTransferPeerId() {
    return String(selectedBuddyRestorePeerId || '').trim();
}

function setBuddyTransferPeerOptions(peers) {
    const listEl = document.getElementById('buddy-restore-buddy-list');
    if (!listEl) return;

    const list = Array.isArray(peers) ? peers : [];
    const previous = String(selectedBuddyRestorePeerId || '').trim();
    listEl.closest('.buddy-restore-layout')?.classList.toggle('single', list.length <= 1);
    if (!list.length) {
        selectedBuddyRestorePeerId = '';
        listEl.innerHTML = '<div class="metric-sub">No buddy available.</div>';
        const labelEl = document.getElementById('buddy-restore-selected-label');
        if (labelEl) labelEl.textContent = 'Snapshots';
        return;
    }

    const values = new Set(list.map((peer) => String(peer.node_id || '').trim()));
    selectedBuddyRestorePeerId = values.has(previous) ? previous : String(list[0].node_id || '');

    listEl.innerHTML = list.map((peer) => {
        const nodeId = String(peer.node_id || '').trim();
        const activeClass = nodeId === selectedBuddyRestorePeerId ? ' active' : '';
        const online = peer?.runtime?.online === true ? 'Online' : 'Offline';
        return `
            <button class="buddy-restore-buddy${activeClass}" data-node-id="${backupEscapeHtml(nodeId)}">
                <div><strong>${backupEscapeHtml(peer.name || nodeId || 'Buddy')}</strong></div>
                <div class="metric-sub mono-text">${backupEscapeHtml(nodeId)}</div>
                <div class="metric-sub">${backupEscapeHtml(online)}</div>
            </button>
        `;
    }).join('');

    const selectedPeer = list.find((peer) => String(peer.node_id || '').trim() === selectedBuddyRestorePeerId);
    const labelEl = document.getElementById('buddy-restore-selected-label');
    if (labelEl) {
        const peerName = selectedPeer?.name || selectedBuddyRestorePeerId || 'Buddy';
        labelEl.textContent = `Backups on ${peerName}`;
    }
}

function renderBuddyRemoteSnapshotsList() {
    const container = document.getElementById('buddy-remote-snapshots-list');
    if (!container) return;
    if (!Array.isArray(buddyRemoteSnapshots) || !buddyRemoteSnapshots.length) {
        container.innerHTML = '<div class="metric-sub">No remote backups available for this buddy.</div>';
        return;
    }
    const vault = buddyRemoteVault || {};
    const usage = vault.size_bytes
        ? `<div class="metric-sub buddy-vault-usage">Encrypted vault on this buddy: ${backupEscapeHtml(backupFormatBytes(vault.size_bytes - (vault.free_bytes || 0)))} used of ${backupEscapeHtml(backupFormatBytes(vault.size_bytes))}${vault.listed_at ? ` · as of ${backupEscapeHtml(backupFormatDate(vault.listed_at))}` : ''}</div>`
        : '';
    container.innerHTML = usage + buddyRemoteSnapshots.map((item) => {
        const isVault = item.kind === 'vault';
        const needsPassword = isVault ? Boolean(item.needs_passphrase) : Boolean(item.encrypted);
        const isFullSystem = String(item.source_path || '').trim() === '/';
        const title = isFullSystem
            ? `Full system <span class="snap-badge">Reboot required</span>`
            : `<span title="${backupEscapeHtml(item.source_path || '')}">${backupEscapeHtml(backupFolderName(item.source_path))}</span>`;
        return `
        <div class="buddy-remote-item">
            <div class="buddy-remote-meta">
                <div><strong>${title}</strong></div>
                <div class="metric-sub">Snapshot: ${backupEscapeHtml(item.snapshot_name || '-')}</div>
                <div class="metric-sub">Created: ${backupEscapeHtml(backupFormatDate(item.created_at))}</div>
                <div class="metric-sub">${isVault
                    ? 'Encrypted vault'
                    : `Old format · Encrypted: ${item.encrypted ? 'Yes' : 'No'} · ${backupEscapeHtml(backupFormatBytes(item.size_bytes))}`}</div>
            </div>
            <div class="buddy-remote-actions">
                <button
                    class="btn-secondary buddy-restore-remote-btn"
                    data-stream-id="${backupEscapeHtml(item.id || '')}"
                    data-source-path="${backupEscapeHtml(item.source_path || '')}"
                    data-encrypted="${needsPassword ? '1' : '0'}"
                >
                    Restore
                </button>
                <button
                    class="btn-secondary buddy-delete-remote-btn"
                    data-stream-id="${backupEscapeHtml(item.id || '')}"
                    data-encrypted="${isVault && needsPassword ? '1' : '0'}"
                >
                    Delete
                </button>
            </div>
        </div>
    `;
    }).join('');
}

function renderBuddyStatus() {
    const identity = buddyState.identity || {};
    const peers = Array.isArray(buddyState.peers) ? buddyState.peers : [];
    const tunnel = buddyState.tunnel || {};
    const requirements = buddyState.requirements || {};

    const supportedEl = document.getElementById('buddy-support-info');
    const tunnelEl = document.getElementById('buddy-tunnel-state');
    const nodeEl = document.getElementById('buddy-node-id');
    const keyEl = document.getElementById('buddy-public-key');
    const ipEl = document.getElementById('buddy-tunnel-ip');
    const portEl = document.getElementById('buddy-listen-port');
    const peersEl = document.getElementById('buddy-peers-list');

    if (supportedEl) {
        supportedEl.textContent = buddyState.supported ? 'Yes' : 'Limited (pairing and settings only)';
        supportedEl.style.color = buddyState.supported ? 'var(--accent-success)' : 'var(--accent-warning)';
    }
    if (tunnelEl) {
        const tunnelState = String(tunnel.state || 'unknown').toLowerCase();
        tunnelEl.textContent = tunnelState.toUpperCase();
        if (tunnelState === 'up') tunnelEl.style.color = 'var(--accent-success)';
        else if (tunnelState === 'down') tunnelEl.style.color = 'var(--error)';
        else tunnelEl.style.color = 'var(--text-secondary)';
    }
    if (nodeEl) nodeEl.textContent = identity.node_id || '-';
    if (keyEl) keyEl.textContent = identity.public_key || '-';
    if (ipEl) ipEl.textContent = identity.tunnel_ip || '-';
    if (portEl) portEl.textContent = String(identity.listen_port || '-');

    // Calm status pill for the Buddy tab header.
    const buddyPill = document.getElementById('buddy-status-pill');
    const buddyIc = document.getElementById('buddy-status-ic');
    const tunnelUp = String(tunnel.state || '').toLowerCase() === 'up';
    let buddyCls = 'ok';
    let buddyLabel = 'Connected';
    if (!buddyState.supported) {
        buddyCls = 'warn';
        buddyLabel = 'Limited';
    } else if (!peers.length) {
        buddyCls = 'warn';
        buddyLabel = 'Not set up';
    } else if (tunnelUp) {
        buddyCls = 'ok';
        buddyLabel = 'Connected';
    } else {
        buddyCls = 'warn';
        buddyLabel = 'Not connected';
    }
    if (buddyPill) {
        buddyPill.className = `pill ${buddyCls}`;
        buddyPill.textContent = buddyLabel;
    }
    if (buddyIc) buddyIc.classList.toggle('warn', buddyCls !== 'ok');

    if (tunnelEl && identity.key_error && !buddyState.supported) {
        tunnelEl.title = identity.key_error;
    } else if (tunnelEl && !buddyState.supported && !requirements.wg_cmd) {
        tunnelEl.title = 'WireGuard tools are missing. Pairing/settings work, tunnel starts after installing wireguard-tools.';
    }

    // One plain sentence about where things stand.
    const statusSub = document.getElementById('buddy-status-sub');
    if (statusSub) {
        const sending = peers.filter((peer) => peer.policy?.enabled === true);
        if (!buddyState.supported) {
            statusSub.textContent = 'This system cannot run the encrypted connection yet. See Technical details.';
        } else if (!peers.length) {
            statusSub.textContent = 'Keep an encrypted copy of your data at a friend\'s or family member\'s AlvaOS. Only you can read it.';
        } else if (!tunnelUp) {
            statusSub.textContent = 'The connection to your buddies is down. Try Test connection, or restart it under Technical details.';
        } else if (sending.length) {
            statusSub.textContent = `Sending to ${sending.map((peer) => peer.name || 'a buddy').join(', ')} automatically.`;
        } else {
            statusSub.textContent = 'Paired, but automatic sending is off. Open More on a buddy to turn it on.';
        }
    }

    if (!peersEl) return;
    const restoreCard = document.getElementById('buddy-restore-card');
    if (restoreCard) restoreCard.style.display = peers.length ? '' : 'none';
    setBuddyTransferPeerOptions(peers);
    if (!peers.length) {
        peersEl.innerHTML = `
            <div class="buddy-empty">
                <p class="metric-sub">No buddy yet. Pair with someone you trust and their AlvaOS keeps an encrypted copy of your data, far away from a fire or a break-in.</p>
                <button class="btn-primary buddy-add-btn">Add a buddy</button>
            </div>`;
        buddyRemoteSnapshots = [];
        renderBuddyRemoteSnapshotsList();
        return;
    }

    // Keep "More" panels open across a refresh.
    const openPeers = new Set(Array.from(peersEl.querySelectorAll('details.buddy-more[open]'))
        .map((el) => el.dataset.nodeId));

    peersEl.innerHTML = peers.map((peer) => {
        const runtime = peer?.runtime || {};
        const online = runtime.online === true;
        const connected = runtime.connected === true;
        const reachable = online && connected;
        const handshakeText = runtime.latest_handshake || '-';
        const intervalText = { 60: 'every hour', 360: 'every 6 hours', 720: 'every 12 hours', 1440: 'every day', 10080: 'every week' }[Number(peer.policy?.interval_minutes || 1440)] || 'on a schedule';
        const sendingText = peer.policy?.enabled === true
            ? `Sends ${intervalText} at ${peer.policy?.send_time || '02:00'}`
            : 'Automatic sending is off';
        const nodeAttr = backupEscapeHtml(peer.node_id || '');
        const policyOutgoing = Array.isArray(peer.policy?.outgoing_sources)
            ? peer.policy.outgoing_sources
            : [];

        return `
        <div class="buddy-peer-card">
            <div class="buddy-peer-main">
                <div class="buddy-peer-top">
                    <div>
                        <div class="buddy-peer-header">
                            <strong>${backupEscapeHtml(peer.name || peer.node_id || 'Buddy')}</strong>
                            <span class="buddy-state-pill${reachable ? ' ok' : ''}">${reachable ? 'Online' : 'Offline'}</span>
                        </div>
                        <div class="metric-sub">${backupEscapeHtml(sendingText)}${runtime.latest_handshake ? ` &bull; last contact ${backupEscapeHtml(handshakeText)}` : ''}</div>
                    </div>
                    <div class="buddy-peer-actions">
                        <button class="btn-primary buddy-backup-now-peer-btn" data-node-id="${nodeAttr}">Send backup now</button>
                    </div>
                </div>
                ${peer.last_error ? `<div class="metric-sub" style="color: var(--error);">Error: ${backupEscapeHtml(peer.last_error)}</div>` : ''}
                <details class="buddy-more" data-node-id="${nodeAttr}"${openPeers.has(peer.node_id || '') ? ' open' : ''}>
                <summary>More: schedule, limits, tools</summary>
                <div class="buddy-more-actions">
                    <button class="btn-secondary buddy-send-system-peer-btn" data-node-id="${nodeAttr}">Send full system</button>
                    <button class="btn-secondary buddy-test-peer-btn" data-node-id="${nodeAttr}">Test connection</button>
                    <button class="btn-secondary buddy-remove-peer-btn" data-node-id="${nodeAttr}">Remove</button>
                </div>
                <div class="buddy-peer-meta-grid">
                    <div class="metric-sub buddy-peer-meta-item">Buddy ID: <span class="mono-text">${backupEscapeHtml(peer.node_id || '-')}</span></div>
                    <div class="metric-sub buddy-peer-meta-item">Address: ${backupEscapeHtml(peer.endpoint || '(not set)')}</div>
                    <div class="metric-sub buddy-peer-meta-item">Tunnel IP: ${backupEscapeHtml(peer.tunnel_ip || '-')}</div>
                    <div class="metric-sub buddy-peer-meta-item">Status: ${backupEscapeHtml(peer.status || 'unknown')}</div>
                </div>
                <div class="buddy-peer-policy">
                    <div class="buddy-peer-policy-grid">
                        <div class="setting-group">
                            <label class="setting-label">Send automatically</label>
                            <label class="toggle">
                                <input type="checkbox" class="buddy-peer-enabled" data-node-id="${backupEscapeHtml(peer.node_id || '')}" ${peer.policy?.enabled === true ? 'checked' : ''}>
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                        <div class="setting-group">
                            <label class="setting-label">How often</label>
                            <select class="select-input buddy-peer-interval" data-node-id="${backupEscapeHtml(peer.node_id || '')}">
                                <option value="60" ${String(peer.policy?.interval_minutes || 1440) === '60' ? 'selected' : ''}>Every hour</option>
                                <option value="360" ${String(peer.policy?.interval_minutes || 1440) === '360' ? 'selected' : ''}>Every 6 hours</option>
                                <option value="720" ${String(peer.policy?.interval_minutes || 1440) === '720' ? 'selected' : ''}>Every 12 hours</option>
                                <option value="1440" ${String(peer.policy?.interval_minutes || 1440) === '1440' ? 'selected' : ''}>Daily</option>
                                <option value="10080" ${String(peer.policy?.interval_minutes || 1440) === '10080' ? 'selected' : ''}>Weekly</option>
                            </select>
                        </div>
                        <div class="setting-group">
                            <label class="setting-label">Send at</label>
                            <input type="time" class="select-input buddy-peer-send-time" data-node-id="${backupEscapeHtml(peer.node_id || '')}" value="${backupEscapeHtml(peer.policy?.send_time || '02:00')}">
                        </div>
                        <div class="setting-group">
                            <label class="setting-label">Space for this buddy's backups here (GB)</label>
                            <input type="number" min="1" max="20000" class="select-input buddy-peer-quota" data-node-id="${backupEscapeHtml(peer.node_id || '')}" value="${backupEscapeHtml(String(peer.policy?.max_storage_gb || buddySettings?.incoming_quota_gb || 200))}">
                        </div>
                        <div class="setting-group" style="grid-column: 1 / -1;">
                            <label class="setting-label">Snapshots kept on this buddy</label>
                            <div class="buddy-retention-row">
                                ${[['keep_daily', 'daily', 14], ['keep_weekly', 'weekly', 8], ['keep_monthly', 'monthly', 12]].map(([key, label, fallback]) => `
                                <label class="metric-sub buddy-retention-field">
                                    <input type="number" min="0" max="366" class="select-input buddy-peer-${key}" data-node-id="${backupEscapeHtml(peer.node_id || '')}" value="${backupEscapeHtml(String(peer.policy?.[key] ?? fallback))}">
                                    ${label}
                                </label>`).join('')}
                            </div>
                            <div class="metric-sub">The newest snapshot is always kept. Older ones are thinned out like this, and the oldest go first when the buddy's space runs low.</div>
                        </div>
                        <div class="setting-group" style="grid-column: 1 / -1;">
                            <label class="setting-label">Folders to send</label>
                            ${renderBuddyPeerSourcePicker(peer.node_id || '', policyOutgoing)}
                        </div>
                    </div>
                    <div class="buddy-peer-policy-footer">
                        <button class="btn-secondary buddy-save-peer-policy-btn" data-node-id="${backupEscapeHtml(peer.node_id || '')}">Save</button>
                    </div>
                </div>
                </details>
            </div>
        </div>
    `;
    }).join('');
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
        const detailFail = Array.isArray(data?.result?.failed) && data.result.failed.length
            ? (data.result.failed[0]?.error || '')
            : '';
        backupNotify(detailFail || data?.error || 'Backup failed', 'error');
        return;
    }

    const createdCount = Array.isArray(data.created) ? data.created.length : 0;
    const failedCount = Array.isArray(data.failed) ? data.failed.length : 0;
    const firstFailure = Array.isArray(data.failed) && data.failed.length ? (data.failed[0]?.error || '') : '';
    if (failedCount > 0) {
        backupNotify(
            firstFailure || `Backup finished with errors: ${failedCount} failed`,
            'warning'
        );
    } else {
        backupNotify(`Pool backup completed (${createdCount} snapshots created)`, 'success');
    }

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
        const detailFail = Array.isArray(data?.result?.failed) && data.result.failed.length
            ? (data.result.failed[0]?.error || '')
            : '';
        backupNotify(detailFail || data?.error || 'System backup failed', 'error');
        return;
    }

    const createdCount = Array.isArray(data.created) ? data.created.length : 0;
    const failedCount = Array.isArray(data.failed) ? data.failed.length : 0;
    const firstFailure = Array.isArray(data.failed) && data.failed.length ? (data.failed[0]?.error || '') : '';
    if (failedCount > 0) {
        backupNotify(
            firstFailure || `System backup finished with errors: ${failedCount} failed`,
            'warning'
        );
    } else {
        backupNotify(`System backup completed (${createdCount} snapshots created)`, 'success');
    }
    await Promise.all([loadBackupStatus(), loadSystemSnapshots()]);
}

async function restoreDataSnapshot(snapshotPath, sourcePath) {
    if (!snapshotPath) return;
    const ok = await window.showConfirm('Restore this snapshot?\nCurrent data will be replaced and moved to a pre-restore backup path.');
    if (!ok) return;
    let pass = getRollbackPassphraseIfNeeded();
    if (pass.needsPrompt) {
        pass = await requestBackupPassphrase('Restore Snapshot\nEncryption password required for rollback or restore.', 'Start Restore');
    }
    if (!pass.ok) return;

    backupNotify('Restoring snapshot...', 'info');
    const response = await backupApi('/backup/restore', {
        method: 'POST',
        json: {
            snapshot_path: snapshotPath,
            source_path: sourcePath || undefined,
            encryption_passphrase: pass.passphrase || undefined
        }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Restore failed', 'error');
        return;
    }
    const restoreResult = (data && typeof data.result === 'object' && data.result) ? data.result : null;
    const pendingRemount = Boolean(restoreResult?.pending_remount);
    const restoreMessage = String(
        data?.message
        || restoreResult?.message
        || (pendingRemount ? 'Restore prepared. Remount/reboot required.' : 'Snapshot restore completed')
    );
    backupNotify(restoreMessage, pendingRemount ? 'warning' : 'success');
    if (pendingRemount) {
        const rebootNow = await showRebootRequiredDialog();
        if (rebootNow) await rebootSystemNow();
    }
    await loadDataSnapshots();
}

async function deleteDataSnapshot(snapshotPath) {
    const snap = String(snapshotPath || '').trim();
    if (!snap) return;
    const ok = await window.showConfirm('Delete this snapshot? This cannot be undone.');
    if (!ok) return;

    const response = await backupApi('/backup/snapshots', {
        method: 'DELETE',
        json: { snapshot_path: snap }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to delete snapshot', 'error');
        return;
    }
    backupNotify('Snapshot deleted', 'success');
    await Promise.all([loadDataSnapshots(), loadBackupStatus()]);
}

async function deleteSystemSnapshot(snapshotPath) {
    const snap = String(snapshotPath || '').trim();
    if (!snap) return;
    const ok = await window.showConfirm('Delete this system snapshot? This cannot be undone.');
    if (!ok) return;

    const response = await backupApi('/backup/snapshots', {
        method: 'DELETE',
        json: { snapshot_path: snap }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to delete system snapshot', 'error');
        return;
    }
    backupNotify('System snapshot deleted', 'success');
    await Promise.all([loadSystemSnapshots(), loadBackupStatus()]);
}

async function prepareSystemRollback(snapshotPath) {
    if (!snapshotPath) return;
    const ok = await window.showConfirm('Prepare full system rollback to this snapshot?\nSystem will boot into that snapshot after reboot.');
    if (!ok) return;
    let pass = getRollbackPassphraseIfNeeded();
    if (pass.needsPrompt) {
        pass = await requestBackupPassphrase('System Rollback\nEncryption password required for rollback or restore.', 'Prepare Rollback');
    }
    if (!pass.ok) return;

    const response = await backupApi('/backup/system/rollback', {
        method: 'POST',
        json: {
            snapshot_path: snapshotPath,
            encryption_passphrase: pass.passphrase || undefined
        }
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
    buddySettings = data.settings || buddySettings || {};
    fillBuddySettingsUi();
    renderBuddyStatus();
    await loadBuddyRemoteSnapshots();
}

async function loadBuddySettings() {
    const response = await backupApi('/backup/buddy/settings');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to load buddy settings', 'error');
        return;
    }
    buddySettings = data.settings || {};
    fillBuddySettingsUi();
}

async function generateBuddyToken() {
    const expiresMinutes = Number(document.getElementById('buddy-token-expiry')?.value || 0);

    const response = await backupApi('/backup/pairing/generate', {
        method: 'POST',
        json: {
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

    // Primary path: modern clipboard API
    try {
        if (navigator?.clipboard?.writeText) {
            await navigator.clipboard.writeText(token);
            backupNotify('Token copied to clipboard', 'success');
            return;
        }
    } catch {
        // fallback below
    }

    // Fallback path: temporary textarea + execCommand copy
    try {
        const temp = document.createElement('textarea');
        temp.value = token;
        temp.setAttribute('readonly', 'readonly');
        temp.style.position = 'fixed';
        temp.style.left = '-9999px';
        temp.style.top = '0';
        document.body.appendChild(temp);
        temp.focus();
        temp.select();
        temp.setSelectionRange(0, temp.value.length);
        const ok = document.execCommand('copy');
        document.body.removeChild(temp);
        if (ok) {
            backupNotify('Token copied to clipboard', 'success');
            return;
        }
    } catch {
        // manual fallback below
    }

    try {
        const tokenEl = document.getElementById('buddy-generated-token');
        if (tokenEl) {
            tokenEl.focus();
            tokenEl.select();
            tokenEl.setSelectionRange(0, tokenEl.value.length);
        }
        backupNotify('Clipboard blocked. Token is selected, copy with Ctrl+C (or Cmd+C).', 'warning');
    } catch {
        backupNotify('Failed to copy automatically. Copy the token manually from the text box.', 'warning');
    }
}

async function validateBuddyToken() {
    const token = document.getElementById('buddy-token-input')?.value?.trim() || '';
    if (!token) {
        backupNotify('Paste a buddy token first', 'warning');
        return;
    }

    const response = await backupApi('/backup/pairing/validate', {
        method: 'POST',
        json: {
            token: token
        }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Pairing failed', 'error');
        return;
    }

    if (data?.reciprocal?.success === false) {
        backupNotify(
            `Buddy paired locally, but reciprocal pairing failed: ${data.reciprocal.error || 'unknown error'}`,
            'warning'
        );
    } else if (data?.reciprocal?.success === true) {
        backupNotify('Buddy paired on both NAS systems', 'success');
    } else {
        backupNotify('Buddy paired successfully', 'success');
    }
    const tokenInput = document.getElementById('buddy-token-input');
    if (tokenInput) tokenInput.value = '';
    await loadBuddyStatus();
}

async function saveBuddySettings() {
    const encryptionEnabled = !!document.getElementById('buddy-encryption-enabled')?.checked;
    const encryptionPassword = document.getElementById('buddy-encryption-password')?.value || '';
    const incomingPath = document.getElementById('buddy-incoming-path')?.value?.trim() || '';
    if (!incomingPath) {
        backupNotify('Please select a local incoming data path first', 'warning');
        return;
    }
    const payload = {
        enabled: true,
        incoming_path: incomingPath,
        incoming_quota_gb: Number(document.getElementById('buddy-incoming-quota')?.value || 200),
        recursive_retention: true,
        encryption_enabled: encryptionEnabled
    };
    if (encryptionPassword.trim()) {
        payload.encryption_password = encryptionPassword.trim();
    }

    const response = await backupApi('/backup/buddy/settings', {
        method: 'POST',
        json: payload
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to save buddy settings', 'error');
        return;
    }
    buddySettings = data.settings || buddySettings;
    fillBuddySettingsUi();
    backupNotify('Buddy settings saved', 'success');
}

async function saveBuddyPeerPolicy(nodeId) {
    const target = String(nodeId || '').trim();
    if (!target) return;
    const enabledEl = buddyPeerField('buddy-peer-enabled', target);
    const intervalEl = buddyPeerField('buddy-peer-interval', target);
    const sendTimeEl = buddyPeerField('buddy-peer-send-time', target);
    const quotaEl = buddyPeerField('buddy-peer-quota', target);

    const payload = {
        enabled: !!enabledEl?.checked,
        interval_minutes: Number(intervalEl?.value || 1440),
        send_time: String(sendTimeEl?.value || '02:00'),
        max_storage_gb: Number(quotaEl?.value || buddySettings?.incoming_quota_gb || 200),
        outgoing_sources: selectedBuddyPeerSources(target)
    };
    for (const key of ['keep_daily', 'keep_weekly', 'keep_monthly']) {
        const el = buddyPeerField(`buddy-peer-${key}`, target);
        if (el && el.value !== '') payload[key] = Number(el.value);
    }

    const response = await backupApi(`/backup/buddy/peers/${encodeURIComponent(target)}/policy`, {
        method: 'POST',
        json: payload
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to save buddy policy', 'error');
        return;
    }
    backupNotify('Buddy policy saved', 'success');
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

// ----------------------------------------------------------------------------
// RECOVERY KIT
// ----------------------------------------------------------------------------

function renderRecoveryKitStatus(status) {
    const pill = document.getElementById('buddy-kit-pill');
    const text = document.getElementById('buddy-kit-status');
    if (!pill || !text) return;
    if (!status?.exported_at) {
        pill.textContent = 'Not saved';
        pill.className = 'pill warn';
        text.textContent = 'No recovery kit saved yet. Without it, a replacement NAS cannot reach this NAS\'s snapshots on your buddies.';
    } else if (status.up_to_date) {
        pill.textContent = 'Up to date';
        pill.className = 'pill ok';
        text.textContent = `Last saved ${backupFormatDate(status.exported_at)}.`;
    } else {
        pill.textContent = 'Outdated';
        pill.className = 'pill warn';
        text.textContent = `Last saved ${backupFormatDate(status.exported_at)}. Your buddies changed since then; download a new kit.`;
    }
}

async function loadRecoveryKitStatus() {
    const response = await backupApi('/backup/buddy/recovery-kit');
    const data = await backupReadJson(response);
    if (response && response.ok && data) renderRecoveryKitStatus(data);
}

function downloadTextFile(filename, content) {
    const blob = new Blob([content + '\n'], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportRecoveryKit() {
    const first = await window.showPrompt(
        'Recovery kit password\nThe kit is locked with this password. You need it again to use the kit, and nobody can recover it for you.',
        { type: 'password', label: 'Password (at least 8 characters)', placeholder: 'Kit password', confirmLabel: 'Next' }
    );
    if (first === null) return;
    if (String(first).length < 8) {
        backupNotify('The password needs at least 8 characters', 'warning');
        return;
    }
    const second = await window.showPrompt('Repeat the password', {
        type: 'password', label: 'Password', placeholder: 'Kit password', confirmLabel: 'Download kit'
    });
    if (second === null) return;
    if (second !== first) {
        backupNotify('The passwords do not match', 'warning');
        return;
    }

    const response = await backupApi('/backup/buddy/recovery-kit', { method: 'POST', json: { passphrase: first } });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data?.kit) {
        backupNotify(data?.error || 'Could not create the recovery kit', 'error');
        return;
    }
    downloadTextFile(data.filename || 'alvaos-buddy-recovery-kit.txt', data.kit);
    backupNotify('Recovery kit downloaded. Store it somewhere other than this NAS.', 'success');
    await loadRecoveryKitStatus();
}

async function readRecoveryKitInput() {
    const file = document.getElementById('buddy-kit-file')?.files?.[0];
    if (file) return (await file.text()).trim();
    return String(document.getElementById('buddy-kit-text')?.value || '').trim();
}

async function importRecoveryKit() {
    const kit = await readRecoveryKitInput();
    if (!kit) {
        backupNotify('Choose the recovery kit file or paste its contents first', 'warning');
        return;
    }
    const passphrase = await window.showPrompt('Recovery kit password', {
        type: 'password', label: 'Password', placeholder: 'Kit password', confirmLabel: 'Restore identity'
    });
    if (passphrase === null || !String(passphrase)) return;

    let response = await backupApi('/backup/buddy/recovery-kit/import', { method: 'POST', json: { kit, passphrase } });
    let data = await backupReadJson(response);
    if (response && response.status === 409 && data?.needs_confirmation) {
        const ok = await window.showConfirm(
            'Replace this NAS\'s buddy identity?\nThis NAS is already paired with buddies. Restoring the kit replaces its identity and buddy list with the ones from the kit.'
        );
        if (!ok) return;
        response = await backupApi('/backup/buddy/recovery-kit/import', {
            method: 'POST', json: { kit, passphrase, replace: true }
        });
        data = await backupReadJson(response);
    }
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Could not restore from the recovery kit', 'error');
        return;
    }
    const kitText = document.getElementById('buddy-kit-text');
    if (kitText) kitText.value = '';
    const kitFile = document.getElementById('buddy-kit-file');
    if (kitFile) kitFile.value = '';
    backupNotify(data.message || 'Identity restored', 'success');
    await loadBuddyStatus();
    await loadRecoveryKitStatus();
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
    if (data?.reciprocal?.attempted === true && data?.reciprocal?.success === false) {
        backupNotify(
            `Buddy removed locally, but remote cleanup failed: ${data.reciprocal.error || 'unknown error'}`,
            'warning'
        );
    } else {
        backupNotify('Buddy removed', 'success');
    }
    await loadBuddyStatus();
}

async function testBuddyPeerConnection(nodeId) {
    const target = String(nodeId || '').trim();
    if (!target) return;

    const response = await backupApi('/backup/pairing/test', {
        method: 'POST',
        json: { node_id: target }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Connection test failed', 'error');
        return;
    }

    if (data.connection_ok) {
        backupNotify(data.message || 'Connection test successful', 'success');
    } else {
        backupNotify(data.message || 'Connection test failed', 'warning');
    }
    await loadBuddyStatus();
}

async function sendFullSystemToBuddy(nodeId) {
    const target = String(nodeId || '').trim();
    if (!target) {
        backupNotify('Select a buddy first', 'warning');
        return;
    }
    const ok = await window.showConfirm(
        'Send a full system copy to this buddy?\n\n'
        + 'This sends an encrypted snapshot of the whole system (root), which can be large. '
        + 'It lets you later restore the entire system from this buddy (a reboot is required for that restore).'
    );
    if (!ok) return;
    await syncBuddyNow(target, null, { system: true });
}

async function syncBuddyNow(preferredNodeId = '', preferredSources = null, options = {}) {
    const requestedNodeId = String(preferredNodeId || '').trim();
    const nodeId = requestedNodeId || getBuddyTransferPeerId();
    if (!nodeId) {
        backupNotify('Select a buddy first', 'warning');
        return;
    }
    if (requestedNodeId) selectedBuddyRestorePeerId = requestedNodeId;
    const fullSystem = !!options.system;
    const sourceList = Array.isArray(preferredSources)
        ? preferredSources.map((p) => String(p || '').trim()).filter((p) => p.startsWith('/'))
        : null;
    const payload = { node_id: nodeId };
    if (sourceList && sourceList.length) payload.sources = sourceList;
    if (fullSystem) payload.system = true;
    backupNotify(fullSystem ? 'Starting full system backup to buddy...' : 'Starting buddy backup...', 'info');
    const response = await backupApi('/backup/buddy/sync', {
        method: 'POST',
        json: payload
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Buddy backup failed', 'error');
        return;
    }
    const firstFailure = Array.isArray(data.failed) && data.failed.length
        ? String(data.failed[0]?.error || '').trim()
        : '';
    if (String(data.status || '') === 'success') {
        backupNotify('Buddy backup completed', 'success');
    } else if (String(data.status || '') === 'partial') {
        backupNotify(firstFailure ? `Buddy backup completed with warnings: ${firstFailure}` : 'Buddy backup completed with warnings', 'warning');
    } else {
        if (firstFailure.toLowerCase().includes('peer is missing api secret')) {
            backupNotify('Buddy backup failed: missing API secret. Remove buddy and pair again once to refresh secure exchange.', 'error');
        } else {
            backupNotify(firstFailure || 'Buddy backup failed', 'error');
        }
    }
    await loadBuddyStatus();
    await loadBuddyRemoteSnapshots();
}

function backupFormatBytes(bytes) {
    const value = Number(bytes || 0);
    if (!value) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    const exp = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
    return `${(value / 1024 ** exp).toFixed(exp ? 1 : 0)} ${units[exp]}`;
}

// Shows what was saved at the last sync; `refresh` opens the vault on the buddy to look now.
async function loadBuddyRemoteSnapshots(refresh = false, passphrase = '') {
    const nodeId = getBuddyTransferPeerId();
    if (!nodeId) {
        buddyRemoteSnapshots = [];
        buddyRemoteVault = {};
        renderBuddyRemoteSnapshotsList();
        return;
    }
    const response = refresh
        ? await backupApi('/backup/buddy/remote/snapshots', {
            method: 'POST',
            json: { node_id: nodeId, encryption_passphrase: passphrase || undefined }
        })
        : await backupApi(`/backup/buddy/remote/snapshots?node_id=${encodeURIComponent(nodeId)}`);
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to load remote backups', 'error');
        return;
    }
    if (refresh && data.vault_error) {
        if (data.needs_passphrase && !passphrase && /password/i.test(data.vault_error)) {
            // A reinstalled NAS: the vault key on the buddy opens with the encryption password.
            const entered = await requestBackupPassphrase('Unlock backups on this buddy\nEnter the encryption password you used for buddy backups.', 'Unlock');
            if (entered.ok) return loadBuddyRemoteSnapshots(true, entered.passphrase);
        } else {
            backupNotify(data.vault_error, 'error');
        }
    }
    buddyRemoteSnapshots = Array.isArray(data.streams) ? data.streams : [];
    buddyRemoteVault = data.vault || {};
    renderBuddyRemoteSnapshotsList();
}

async function restoreBuddyRemoteSnapshot(streamId, sourcePath, encrypted) {
    const nodeId = getBuddyTransferPeerId();
    const stream = String(streamId || '').trim();
    if (!nodeId || !stream) {
        backupNotify('Select a buddy and snapshot first', 'warning');
        return;
    }
    const isFullSystem = String(sourcePath || '').trim() === '/';
    const confirmMessage = isFullSystem
        ? 'Restore the FULL SYSTEM from this buddy?\n\nThis prepares a rollback of the entire system to the buddy\'s copy and requires a reboot to take effect. The current system is kept until you reboot.'
        : 'Restore this remote snapshot to the local source path? Current local data will be moved to a pre-restore backup path.';
    const ok = await window.showConfirm(confirmMessage);
    if (!ok) return;

    let passphrase = '';
    if (encrypted) {
        const entered = await requestBackupPassphrase('Remote Restore\nEncryption password required for remote restore.', 'Start Restore');
        if (!entered.ok) return;
        passphrase = entered.passphrase;
    }

    const response = await backupApi('/backup/buddy/restore/remote', {
        method: 'POST',
        json: {
            node_id: nodeId,
            stream_id: stream,
            source_path: String(sourcePath || ''),
            encryption_passphrase: passphrase || undefined
        }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Remote restore failed', 'error');
        return;
    }
    const restoreResult = (data && typeof data.result === 'object' && data.result) ? data.result : null;
    const pendingRemount = Boolean(restoreResult?.pending_remount);
    const restoreMessage = String(
        data?.message
        || restoreResult?.message
        || (pendingRemount ? 'Remote restore prepared. Remount/reboot required.' : 'Remote restore completed')
    );
    backupNotify(restoreMessage, pendingRemount ? 'warning' : 'success');
    if (pendingRemount) {
        const rebootNow = await showRebootRequiredDialog();
        if (rebootNow) await rebootSystemNow();
    }
    await Promise.all([loadBuddyStatus(), loadDataSnapshots(), loadBuddyRemoteSnapshots()]);
}

async function deleteBuddyRemoteSnapshot(streamId, needsPassword = false) {
    const nodeId = getBuddyTransferPeerId();
    const stream = String(streamId || '').trim();
    if (!nodeId || !stream) {
        backupNotify('Select a buddy and snapshot first', 'warning');
        return;
    }
    const ok = await window.showConfirm('Delete this snapshot on your buddy? This cannot be undone.\n\nOther snapshots are not affected.');
    if (!ok) return;
    let passphrase = '';
    if (needsPassword) {
        const entered = await requestBackupPassphrase('Unlock backups on this buddy\nEnter the encryption password you used for buddy backups.', 'Delete');
        if (!entered.ok) return;
        passphrase = entered.passphrase;
    }

    const response = await backupApi('/backup/buddy/remote/snapshot', {
        method: 'DELETE',
        json: {
            node_id: nodeId,
            stream_id: stream,
            encryption_passphrase: passphrase || undefined
        }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to delete remote snapshot', 'error');
        return;
    }
    backupNotify('Remote snapshot deleted', 'success');
    await loadBuddyRemoteSnapshots();
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
    setBuddyIncomingPathOptions(buddySettings.incoming_path || '');
    renderBuddyStatus();
}

async function loadBackupTargets() {
    const response = await backupApi('/backup/targets');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify('Failed to load backup targets', 'error');
        return;
    }
    backupTargets = data.targets || [];

    // Pool Target Select
    const pb = backupSettings.pool_backup || {};
    setSelectOptions('pool-target-path', pb.target_path || '', backupTargets);
    setSelectOptions('pool-run-target-path', pb.target_path || '', backupTargets);

    // System Target Select
    const sb = backupSettings.system_backup || {};
    const systemTargets = backupTargets.filter((target) => String(target?.kind || '').toLowerCase() === 'system');
    const filteredSystemTargets = systemTargets.length ? systemTargets : backupTargets;
    setSelectOptions('system-target-path', sb.target_path || '', filteredSystemTargets);
    setSelectOptions('system-run-target-path', sb.target_path || '', filteredSystemTargets);
}

async function loadDataSnapshots() {
    const response = await backupApi('/backup/snapshots?snapshot_class=data');
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

    // Automatic backups: the toggle and interval save on change, no extra button.
    const saveAutoPool = async () => {
        if (document.getElementById('pool-enabled').checked && !selectedSources().length) {
            backupNotify('Choose which folders to back up under "More options" first.', 'warning');
            fillSettingsUi();
            return;
        }
        await savePoolSettings();
        fillSettingsUi();
    };
    const saveAutoSystem = async () => {
        await saveSystemSettings();
        fillSettingsUi();
    };
    document.getElementById('pool-enabled')?.addEventListener('change', saveAutoPool);
    document.getElementById('pool-interval')?.addEventListener('change', saveAutoPool);
    document.getElementById('system-enabled')?.addEventListener('change', saveAutoSystem);
    document.getElementById('system-interval')?.addEventListener('change', saveAutoSystem);

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
        const deleteBtn = event.target.closest('.backup-delete-btn');
        if (deleteBtn) {
            deleteDataSnapshot(deleteBtn.dataset.snapshotPath || '');
            return;
        }
        const btn = event.target.closest('.backup-restore-btn');
        if (!btn) return;
        restoreDataSnapshot(btn.dataset.snapshotPath || '', btn.dataset.sourcePath || '');
    });

    document.getElementById('system-snapshots-list')?.addEventListener('click', (event) => {
        const deleteBtn = event.target.closest('.backup-system-delete-btn');
        if (deleteBtn) {
            deleteSystemSnapshot(deleteBtn.dataset.snapshotPath || '');
            return;
        }
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
    document.getElementById('buddy-save-settings-btn')?.addEventListener('click', saveBuddySettings);
    document.getElementById('buddy-kit-export-btn')?.addEventListener('click', exportRecoveryKit);
    document.getElementById('buddy-kit-import-btn')?.addEventListener('click', importRecoveryKit);
    document.getElementById('buddy-encryption-enabled')?.addEventListener('change', updateBuddySecurityUi);
    document.getElementById('buddy-refresh-remote-btn')?.addEventListener('click', () => loadBuddyRemoteSnapshots(true));
    document.getElementById('buddy-peers-list')?.addEventListener('click', (event) => {
        if (event.target.closest('.buddy-add-btn')) {
            const pairing = document.getElementById('buddy-pairing');
            if (pairing) {
                pairing.open = true;
                pairing.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
            return;
        }
        const saveBtn = event.target.closest('.buddy-save-peer-policy-btn');
        if (saveBtn) {
            saveBuddyPeerPolicy(saveBtn.dataset.nodeId || '');
            return;
        }
        const backupBtn = event.target.closest('.buddy-backup-now-peer-btn');
        if (backupBtn) {
            const nodeId = String(backupBtn.dataset.nodeId || '').trim();
            const selectedNow = selectedBuddyPeerSources(nodeId);
            syncBuddyNow(nodeId, selectedNow);
            return;
        }
        const sendSystemBtn = event.target.closest('.buddy-send-system-peer-btn');
        if (sendSystemBtn) {
            const nodeId = String(sendSystemBtn.dataset.nodeId || '').trim();
            sendFullSystemToBuddy(nodeId);
            return;
        }
        const testBtn = event.target.closest('.buddy-test-peer-btn');
        if (testBtn) {
            testBuddyPeerConnection(testBtn.dataset.nodeId || '');
            return;
        }
        const btn = event.target.closest('.buddy-remove-peer-btn');
        if (!btn) return;
        removeBuddyPeer(btn.dataset.nodeId || '');
    });
    document.getElementById('buddy-restore-buddy-list')?.addEventListener('click', (event) => {
        const btn = event.target.closest('.buddy-restore-buddy');
        if (!btn) return;
        const nodeId = String(btn.dataset.nodeId || '').trim();
        if (!nodeId || nodeId === selectedBuddyRestorePeerId) return;
        selectedBuddyRestorePeerId = nodeId;
        renderBuddyStatus();
        loadBuddyRemoteSnapshots();
    });
    document.getElementById('buddy-remote-snapshots-list')?.addEventListener('click', (event) => {
        const deleteBtn = event.target.closest('.buddy-delete-remote-btn');
        if (deleteBtn) {
            deleteBuddyRemoteSnapshot(deleteBtn.dataset.streamId || '', String(deleteBtn.dataset.encrypted || '') === '1');
            return;
        }
        const btn = event.target.closest('.buddy-restore-remote-btn');
        if (!btn) return;
        restoreBuddyRemoteSnapshot(
            btn.dataset.streamId || '',
            btn.dataset.sourcePath || '',
            String(btn.dataset.encrypted || '') === '1'
        );
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
        loadBuddyStatus(),
        loadBuddySettings(),
        loadRecoveryKitStatus()
    ]);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBackupPage);
} else {
    initBackupPage();
}
