let backupSources = [];
let backupTargets = [];
let backupSettings = {};
let backupStatus = {};
let backupSystemState = {};

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

function selectedSources() {
    return Array.from(document.querySelectorAll('.backup-source-toggle:checked'))
        .map((el) => String(el.value || '').trim())
        .filter(Boolean);
}

function setSelectOptions(selectId, selectedValue = '', includeDefault = true) {
    const selectEl = document.getElementById(selectId);
    if (!selectEl) return;

    const options = [];
    if (includeDefault) {
        options.push('<option value="">Default (auto)</option>');
    }

    backupTargets.forEach((target) => {
        options.push(`<option value="${backupEscapeHtml(target.path)}">${backupEscapeHtml(target.name)} (${backupEscapeHtml(target.path)})</option>`);
    });
    const selectedText = String(selectedValue || '');
    if (selectedText && !backupTargets.some((target) => String(target.path) === selectedText)) {
        options.push(`<option value="${backupEscapeHtml(selectedText)}">Custom: ${backupEscapeHtml(selectedText)}</option>`);
    }
    selectEl.innerHTML = options.join('');

    const value = selectedText;
    if (value && Array.from(selectEl.options).some((opt) => opt.value === value)) {
        selectEl.value = value;
    } else if (!includeDefault && selectEl.options.length > 0) {
        selectEl.value = selectEl.options[0].value;
    }
}

function renderSources() {
    const container = document.getElementById('backup-sources');
    if (!container) return;
    if (!backupSources.length) {
        container.innerHTML = '<div class="metric-sub">No backup sources found. Create pools/subvolumes first.</div>';
        return;
    }

    const configured = new Set((backupSettings.sources || []).map((p) => String(p)));
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
    const container = document.getElementById('backup-snapshots-list');
    if (!container) return;
    const snapshots = Array.isArray(items) ? items : [];
    if (!snapshots.length) {
        container.innerHTML = '<div class="metric-sub">No data snapshots yet.</div>';
        return;
    }

    container.innerHTML = `
        <table class="snapshots-table">
            <thead>
                <tr>
                    <th>Created</th>
                    <th>Source</th>
                    <th>Name</th>
                    <th>Path</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                ${snapshots.map((entry) => `
                    <tr>
                        <td class="mono-text">${backupEscapeHtml(backupFormatDate(entry.created_at))}</td>
                        <td><div class="source-path">${backupEscapeHtml(entry.source_path || '-')}</div></td>
                        <td>${backupEscapeHtml(entry.snapshot_name || '-')}</td>
                        <td><div class="source-path">${backupEscapeHtml(entry.snapshot_path || '-')}</div></td>
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
    const container = document.getElementById('backup-system-snapshots-list');
    if (!container) return;
    const snapshots = Array.isArray(items) ? items : [];
    if (!snapshots.length) {
        container.innerHTML = '<div class="metric-sub">No system snapshots yet.</div>';
        return;
    }
    container.innerHTML = `
        <table class="snapshots-table">
            <thead>
                <tr>
                    <th>Created</th>
                    <th>Name</th>
                    <th>Path</th>
                    <th>Rollback</th>
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
    const lastRunEl = document.getElementById('backup-last-run');
    const nextRunEl = document.getElementById('backup-next-run');
    const systemStateEl = document.getElementById('backup-system-state');
    const systemLastEl = document.getElementById('backup-system-last-snapshot');
    const systemPendingEl = document.getElementById('backup-system-pending');

    if (lastRunEl) lastRunEl.textContent = `Last run: ${backupFormatDate(backupStatus.last_run_at)}`;
    if (nextRunEl) nextRunEl.textContent = `Next run: ${backupFormatDate(backupStatus.next_run_at)}`;
    if (systemLastEl) systemLastEl.textContent = `Last system snapshot: ${backupFormatDate(backupStatus.system_last_snapshot_at)}`;
    if (systemPendingEl) {
        systemPendingEl.textContent = backupStatus.system_pending_reboot
            ? `Pending rollback after reboot: ${backupStatus.system_pending_snapshot_path || 'unknown'}`
            : 'Pending rollback: none';
    }
    if (systemStateEl) {
        if (backupSystemState.supported === false) {
            systemStateEl.textContent = `System snapshot unsupported: ${backupSystemState.reason || 'unknown reason'}`;
        } else {
            systemStateEl.textContent = `Root subvol: ${backupSystemState.root_subvolume_id ?? '-'} | Default: ${backupSystemState.default_subvolume_id ?? '-'}`;
        }
    }
}

function fillSettingsUi() {
    const autoEnabled = document.getElementById('backup-auto-enabled');
    const interval = document.getElementById('backup-interval');
    const keepLast = document.getElementById('backup-keep-last');
    const keepSystemLast = document.getElementById('backup-keep-system-last');
    const includeSystemSchedule = document.getElementById('backup-include-system-schedule');

    if (autoEnabled) autoEnabled.checked = !!backupSettings.auto_enabled;
    if (interval) interval.value = String(backupSettings.interval_minutes || 1440);
    if (keepLast) keepLast.value = String(backupSettings.keep_last || 30);
    if (keepSystemLast) keepSystemLast.value = String(backupSettings.keep_system_last || 10);
    if (includeSystemSchedule) includeSystemSchedule.checked = !!backupSettings.include_system_in_schedule;
}

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

async function loadBackupSettings() {
    const response = await backupApi('/backup/settings');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to load backup settings', 'error');
        return;
    }
    backupSettings = data.settings || {};
    backupStatus = data.status || {};
    fillSettingsUi();
    updateStatusUi();
}

async function loadBackupStatus() {
    const response = await backupApi('/backup/status');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) return;
    backupStatus = data.status || backupStatus || {};
    backupSystemState = data.system || backupSystemState || {};
    updateStatusUi();
}

async function loadBackupSources() {
    const response = await backupApi('/backup/sources');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to load backup sources', 'error');
        return;
    }
    backupSources = data.sources || [];
    renderSources();
}

async function loadBackupTargets() {
    const response = await backupApi('/backup/targets');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to load backup targets', 'error');
        return;
    }
    backupTargets = data.targets || [];
    setSelectOptions('backup-target-path', backupSettings.snapshot_target_path || '');
    setSelectOptions('backup-system-target-path', backupSettings.system_snapshot_target_path || '', false);
}

async function loadDataSnapshots() {
    const response = await backupApi('/backup/snapshots');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to load snapshots', 'error');
        return;
    }
    renderDataSnapshots(data.snapshots || []);
}

async function loadSystemSnapshots() {
    const response = await backupApi('/backup/system/snapshots');
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to load system snapshots', 'error');
        return;
    }
    renderSystemSnapshots(data.snapshots || []);
}

async function saveBackupSettings() {
    const payload = {
        auto_enabled: document.getElementById('backup-auto-enabled')?.checked || false,
        interval_minutes: Number(document.getElementById('backup-interval')?.value || 1440),
        keep_last: Number(document.getElementById('backup-keep-last')?.value || 30),
        keep_system_last: Number(document.getElementById('backup-keep-system-last')?.value || 10),
        include_system_in_schedule: document.getElementById('backup-include-system-schedule')?.checked || false,
        snapshot_target_path: document.getElementById('backup-target-path')?.value || '',
        system_snapshot_target_path: document.getElementById('backup-system-target-path')?.value || '',
        sources: selectedSources()
    };

    const response = await backupApi('/backup/settings', { method: 'POST', json: payload });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to save settings', 'error');
        return;
    }

    backupSettings = data.settings || backupSettings;
    backupStatus = data.status || backupStatus;
    fillSettingsUi();
    updateStatusUi();
    backupNotify('Backup settings saved', 'success');
}

async function runBackupNow() {
    const sources = selectedSources();
    const includeSystem = !!(document.getElementById('backup-include-system-schedule')?.checked);
    const targetPath = document.getElementById('backup-target-path')?.value || '';
    if (!sources.length && !includeSystem) {
        backupNotify('Select at least one source or include system snapshots.', 'warning');
        return;
    }

    const response = await backupApi('/backup/run', {
        method: 'POST',
        json: {
            sources,
            include_system: includeSystem,
            target_path: targetPath || undefined
        }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Backup run failed', 'error');
        return;
    }

    const createdCount = Array.isArray(data.created) ? data.created.length : 0;
    const failedCount = Array.isArray(data.failed) ? data.failed.length : 0;
    if (failedCount > 0) backupNotify(`Backup completed with issues (${createdCount} created, ${failedCount} failed)`, 'warning');
    else backupNotify(`Snapshot run completed (${createdCount} created)`, 'success');

    await Promise.all([loadBackupSettings(), loadDataSnapshots(), loadSystemSnapshots(), loadBackupStatus()]);
}

async function createSystemSnapshot() {
    const targetPath = document.getElementById('backup-system-target-path')?.value || '';
    const response = await backupApi('/backup/system/snapshot', {
        method: 'POST',
        json: {
            target_path: targetPath || undefined
        }
    });
    const data = await backupReadJson(response);
    if (!response || !response.ok || !data) {
        backupNotify(data?.error || 'Failed to create full system snapshot', 'error');
        return;
    }
    backupNotify('Full system snapshot created', 'success');
    await Promise.all([loadSystemSnapshots(), loadBackupStatus(), loadBackupSettings()]);
}

async function restoreDataSnapshot(snapshotPath, sourcePath) {
    if (!snapshotPath) return;
    const ok = await window.showConfirm('Restore this snapshot?\nCurrent data will be replaced and moved to a pre-restore backup path.');
    if (!ok) return;

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

function initBackupHandlers() {
    document.getElementById('backup-sources-refresh-btn')?.addEventListener('click', loadBackupSources);
    document.getElementById('backup-snapshots-refresh-btn')?.addEventListener('click', loadDataSnapshots);
    document.getElementById('backup-system-refresh-btn')?.addEventListener('click', loadSystemSnapshots);
    document.getElementById('backup-save-settings-btn')?.addEventListener('click', saveBackupSettings);
    document.getElementById('backup-run-btn')?.addEventListener('click', runBackupNow);
    document.getElementById('backup-system-create-btn')?.addEventListener('click', createSystemSnapshot);

    document.getElementById('backup-snapshots-list')?.addEventListener('click', (event) => {
        const btn = event.target.closest('.backup-restore-btn');
        if (!btn) return;
        restoreDataSnapshot(btn.dataset.snapshotPath || '', btn.dataset.sourcePath || '');
    });

    document.getElementById('backup-system-snapshots-list')?.addEventListener('click', (event) => {
        const btn = event.target.closest('.backup-system-rollback-btn');
        if (!btn) return;
        prepareSystemRollback(btn.dataset.snapshotPath || '');
    });
}

async function initBackupPage() {
    initTabs();
    initBackupHandlers();
    await loadBackupSettings();
    await loadBackupSources();
    await loadBackupTargets();
    await loadDataSnapshots();
    await loadSystemSnapshots();
    await loadBackupStatus();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBackupPage);
} else {
    initBackupPage();
}
