// AlvaOS System Settings Logic
// API_BASE is defined in app.js

function showNotification(message, type = 'info') {
    if (window.showToast) {
        window.showToast(message, type);
    } else {
        console.log(`[${type}] ${message}`);
    }
}

function showError(message) {
    showNotification(message, 'error');
}

function showSuccess(message) {
    showNotification(message, 'success');
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Persistent page elements (status views; forms live inside modals built on demand)
const els = {
    // Network
    netInterface: document.getElementById('net-interface'),
    netIp: document.getElementById('net-ip'),
    netMask: document.getElementById('net-mask'),
    netGateway: document.getElementById('net-gateway'),
    netDns: document.getElementById('net-dns'),
    hostnameDisplay: document.getElementById('current-hostname-display'),

    // Time
    timeDisplay: document.getElementById('system-time-display'),
    timezoneDisplay: document.getElementById('timezone-display'),
    ntpDisplay: document.getElementById('ntp-display'),

    // Logs
    logViewer: document.getElementById('log-viewer'),

    // UPS (read-only status)
    upsSummary: document.getElementById('ups-summary'),
    upsDevice: document.getElementById('ups-device'),
    upsAcState: document.getElementById('ups-ac-state'),
    upsBatteryState: document.getElementById('ups-battery-state'),
    upsCapacity: document.getElementById('ups-capacity'),
    upsChargeSupport: document.getElementById('ups-charge-support'),
    upsThresholdCurrent: document.getElementById('ups-threshold-current'),
    upsLastAction: document.getElementById('ups-last-action'),

    // Alerts summary
    telegramSummaryDot: document.getElementById('telegram-summary-dot'),
    telegramSummaryStatus: document.getElementById('telegram-summary-status'),
    telegramSummaryChat: document.getElementById('telegram-summary-chat'),

    // 2FA
    tfaStatusText: document.getElementById('tfa-status-text'),
    tfaStatusDot: document.getElementById('tfa-status-dot'),
    tfaEnableBtn: document.getElementById('tfa-enable-btn'),
    tfaInstallBtn: document.getElementById('tfa-install-btn'),
    tfaDisableBtn: document.getElementById('tfa-disable-btn'),

    // SSH remote access
    sshStatusText: document.getElementById('ssh-status-text'),
    sshStatusDot: document.getElementById('ssh-status-dot'),
    sshEnableBtn: document.getElementById('ssh-enable-btn'),
    sshDisableBtn: document.getElementById('ssh-disable-btn'),

    // Watchdog
    watchdogList: document.getElementById('watchdog-service-list'),
    watchdogCheckBtn: document.getElementById('watchdog-check-btn'),
    watchdogLastRun: document.getElementById('watchdog-last-run'),
    watchdogRecoveryLog: document.getElementById('watchdog-recovery-log'),
    watchdogRecoveryItems: document.getElementById('watchdog-recovery-items'),
};

let currentHostname = '';
let currentTimeSettings = {
    timezone: null,
    ntp: null
};

let currentAlertSettings = null;
let currentUpsSettings = null;
let currentUpsStatus = null;

// Modal element references, only set while a modal is open
let telegramModalEls = null;

function getHeaders() {
    const token = localStorage.getItem('alvaos_token');
    return {
        'Authorization': token || '',
        'Content-Type': 'application/json'
    };
}

// --- Reusable modal helper (matches the user-management modal pattern) ---

function openSysModal({ title, description = '', bodyHtml = '', confirmLabel = 'Save', cancelLabel = 'Cancel', onConfirm = null, onClose = null }) {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';

    const panel = document.createElement('div');
    panel.className = 'sys-modal-panel';
    panel.innerHTML = `
        <div class="sys-modal-head">
            <h3 class="sys-modal-title">${escapeHtml(title)}</h3>
            <button class="sys-modal-x" type="button" aria-label="Close">&times;</button>
        </div>
        ${description ? `<p class="sys-modal-text">${description}</p>` : ''}
        <div class="sys-modal-body">${bodyHtml}</div>
        <div class="sys-modal-actions">
            <button type="button" class="btn-secondary sys-modal-cancel">${escapeHtml(cancelLabel)}</button>
            ${onConfirm ? `<button type="button" class="btn-primary sys-modal-confirm">${escapeHtml(confirmLabel)}</button>` : ''}
        </div>
    `;

    modal.appendChild(panel);
    document.body.appendChild(modal);
    if (window.lucide) window.lucide.createIcons();

    let closed = false;
    const close = () => {
        if (closed) return;
        closed = true;
        modal.remove();
        if (onClose) onClose();
    };

    panel.querySelector('.sys-modal-x').addEventListener('click', close);
    panel.querySelector('.sys-modal-cancel').addEventListener('click', close);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) close();
    });

    const confirmBtn = panel.querySelector('.sys-modal-confirm');
    if (confirmBtn && onConfirm) {
        confirmBtn.addEventListener('click', () => onConfirm({ panel, modal, close, confirmBtn }));
    }

    return { panel, modal, close };
}

// --- Data loading ---

async function fetchSettings() {
    try {
        const netRes = await fetch(`${API_BASE}/system/network`, { headers: getHeaders() });

        if (netRes.status === 401) {
            window.location.href = '/login.html';
            return;
        }

        if (netRes.ok) {
            const netData = await netRes.json();
            if (els.netInterface) els.netInterface.textContent = netData.interface || 'N/A';
            if (els.netIp) els.netIp.textContent = netData.ip_address || 'N/A';
            if (els.netMask) els.netMask.textContent = netData.subnet_mask || 'N/A';
            if (els.netGateway) els.netGateway.textContent = netData.gateway || 'N/A';
            if (els.netDns) {
                els.netDns.textContent = (netData.dns && netData.dns.length > 0) ? netData.dns.join('\n') : 'N/A';
            }

            currentHostname = netData.hostname || '';
            if (els.hostnameDisplay) els.hostnameDisplay.textContent = currentHostname || '-';
        } else {
            console.warn('Network fetch failed with status:', netRes.status);
        }
    } catch (e) {
        console.error('Network fetch error:', e);
    }

    try {
        const timeRes = await fetch(`${API_BASE}/system/time`, { headers: getHeaders() });
        if (timeRes.ok) {
            const timeData = await timeRes.json();
            currentTimeSettings.timezone = timeData.timezone || 'UTC';
            currentTimeSettings.ntp = !!timeData.ntp_enabled;
            renderTimeSummary();
        }
    } catch (e) {
        console.warn('Time fetch error', e);
    }

    try {
        const logRes = await fetch(`${API_BASE}/system/logs`, { headers: getHeaders() });
        if (logRes.ok) {
            const logData = await logRes.json();
            if (els.logViewer) {
                els.logViewer.textContent = Array.isArray(logData.logs) ? logData.logs.join('\n') : 'No logs available.';
                els.logViewer.scrollTop = els.logViewer.scrollHeight;
            }
        } else if (els.logViewer) {
            els.logViewer.textContent = 'Could not read the system log. The log service may be busy; try again in a moment.';
        }
    } catch (e) {
        console.warn('Log fetch error', e);
        if (els.logViewer) {
            els.logViewer.textContent = 'Connection error loading logs.';
        }
    }

    await fetchUpsSettings();
    await fetchAlertSettings();
    await fetch2faStatus();
    await fetchSshStatus();
    await fetchWatchdogStatus();
}

// --- Network / Hostname ---

function openHostnameModal() {
    const { panel, close } = openSysModal({
        title: 'Change hostname',
        description: 'The name this server shows on the network. A reboot may be required to apply everywhere.',
        confirmLabel: 'Save hostname',
        bodyHtml: `
            <label class="setting-label" for="m-hostname-input">Hostname</label>
            <input type="text" id="m-hostname-input" class="mono-text" style="width:100%;"
                value="${escapeHtml(currentHostname)}" placeholder="e.g. alvaos">
        `,
        onConfirm: async ({ confirmBtn }) => {
            const input = panel.querySelector('#m-hostname-input');
            const ok = await updateHostname(String(input?.value || '').trim(), confirmBtn);
            if (ok) close();
        }
    });
    panel.querySelector('#m-hostname-input')?.focus();
}

async function updateHostname(newHostname, confirmBtn) {
    if (!newHostname) {
        showError('Hostname cannot be empty.');
        return false;
    }

    // Validate hostname (RFC 1123)
    const hostnamePattern = /^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/;
    if (!hostnamePattern.test(newHostname)) {
        showError('Invalid hostname. Must start/end with alphanumeric, contain only letters, numbers, and hyphens, and be 1-63 characters.');
        return false;
    }

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Saving...';
    }

    try {
        const res = await fetch(`${API_BASE}/system/hostname`, {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify({ hostname: newHostname })
        });

        if (res.ok) {
            showNotification('Hostname updated! System may need a reboot.', 'success');
            currentHostname = newHostname;
            if (els.hostnameDisplay) els.hostnameDisplay.textContent = newHostname;
            return true;
        }

        const err = await res.json().catch(() => ({}));
        showError('Failed to update: ' + (err.error || 'Unknown error'));
        return false;
    } catch (error) {
        showError('Connection failed');
        return false;
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Save hostname';
        }
    }
}

// --- Time ---

function renderTimeSummary() {
    if (els.timezoneDisplay) els.timezoneDisplay.textContent = currentTimeSettings.timezone || '-';
    if (els.ntpDisplay) {
        if (currentTimeSettings.ntp === null) {
            els.ntpDisplay.textContent = '-';
        } else {
            els.ntpDisplay.textContent = currentTimeSettings.ntp ? 'Enabled' : 'Disabled';
        }
    }
}

function openTimeModal() {
    const baseZones = ['UTC', 'Europe/Berlin', 'America/New_York', 'Asia/Tokyo'];
    const zones = [...baseZones];
    if (currentTimeSettings.timezone && !zones.includes(currentTimeSettings.timezone)) {
        zones.unshift(currentTimeSettings.timezone);
    }
    const options = zones.map(z =>
        `<option value="${escapeHtml(z)}" ${z === currentTimeSettings.timezone ? 'selected' : ''}>${escapeHtml(z)}</option>`
    ).join('');

    const { panel, close } = openSysModal({
        title: 'Change time settings',
        description: 'Set the timezone and whether the clock syncs automatically over NTP.',
        confirmLabel: 'Save time settings',
        bodyHtml: `
            <div class="setting-group">
                <label class="setting-label" for="m-timezone-select">Timezone</label>
                <select id="m-timezone-select" style="width:100%;">${options}</select>
            </div>
            <label style="display:flex; align-items:center; cursor:pointer; margin-top:8px;">
                <input type="checkbox" id="m-ntp-toggle" style="margin-right:8px;" ${currentTimeSettings.ntp ? 'checked' : ''}>
                <span style="font-size:0.85rem; color:var(--text-secondary);">Enable NTP Sync</span>
            </label>
        `,
        onConfirm: async ({ confirmBtn }) => {
            const tz = panel.querySelector('#m-timezone-select')?.value;
            const ntp = !!panel.querySelector('#m-ntp-toggle')?.checked;
            const ok = await updateTimeSettings(tz, ntp, confirmBtn);
            if (ok) close();
        }
    });
}

async function updateTimeSettings(timezone, ntp, confirmBtn) {
    const payload = {};

    if (timezone && timezone !== currentTimeSettings.timezone) {
        payload.timezone = timezone;
    }
    if (currentTimeSettings.ntp === null || ntp !== currentTimeSettings.ntp) {
        payload.ntp = ntp;
    }

    if (Object.keys(payload).length === 0) {
        showError('No time settings changed.');
        return false;
    }

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Saving...';
    }

    try {
        const res = await fetch(`${API_BASE}/system/time`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));

        if (res.ok) {
            currentTimeSettings.timezone = payload.timezone ?? currentTimeSettings.timezone;
            if (Object.prototype.hasOwnProperty.call(payload, 'ntp')) {
                currentTimeSettings.ntp = payload.ntp;
            }
            renderTimeSummary();

            if (Array.isArray(data.warnings) && data.warnings.length) {
                showNotification(`Time settings updated with warnings:\n${data.warnings.join('\n')}`, 'warning');
            } else {
                showNotification(data.message || 'Time settings updated.', 'success');
            }
            return true;
        }

        showError(`Failed to update time settings: ${data.error || 'Unknown error'}`);
        return false;
    } catch (e) {
        console.error(e);
        showError('Connection failed');
        return false;
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Save time settings';
        }
    }
}

// --- Battery UPS ---

function renderUpsSettings(data) {
    const settings = data?.settings || currentUpsSettings || {};
    const status = data?.status || currentUpsStatus || {};
    currentUpsSettings = settings;
    currentUpsStatus = status;

    if (els.upsSummary) {
        if (settings.enabled) {
            els.upsSummary.textContent = `Enabled — shutdown at ${Number(settings.shutdown_percent || 20)}%`;
            els.upsSummary.style.color = 'var(--accent-success)';
        } else {
            els.upsSummary.textContent = 'Disabled';
            els.upsSummary.style.color = 'var(--text-secondary)';
        }
    }

    if (els.upsDevice) {
        els.upsDevice.textContent = status.battery_present ? (status.battery_name || 'BAT') : 'No battery detected';
    }
    if (els.upsAcState) {
        if (status.on_ac_power === true) {
            els.upsAcState.textContent = 'AC Online';
        } else if (status.on_ac_power === false) {
            els.upsAcState.textContent = 'On Battery';
        } else {
            els.upsAcState.textContent = 'Unknown';
        }
    }
    if (els.upsBatteryState) {
        els.upsBatteryState.textContent = status.battery_status || '-';
    }
    if (els.upsCapacity) {
        els.upsCapacity.textContent = Number.isFinite(status.capacity_percent) ? `${status.capacity_percent}%` : '-';
    }
    if (els.upsChargeSupport) {
        els.upsChargeSupport.textContent = status.charge_limit_supported ? 'Supported' : 'Not supported';
    }
    if (els.upsThresholdCurrent) {
        const end = status?.charge_limit_current?.end_percent;
        els.upsThresholdCurrent.textContent = Number.isFinite(end) ? `${end}%` : '-';
    }
    if (els.upsLastAction) {
        const action = status?.last_action || {};
        if (action.type && action.message) {
            els.upsLastAction.textContent = `${action.type}: ${action.message}`;
        } else {
            els.upsLastAction.textContent = '-';
        }
    }
}

async function fetchUpsSettings() {
    try {
        const res = await fetch(`${API_BASE}/system/power/ups`, { headers: getHeaders() });
        if (res.status === 401) {
            window.location.href = '/login.html';
            return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            return;
        }
        renderUpsSettings(data);
    } catch (e) {
        console.warn('UPS settings fetch failed', e);
    }
}

function openUpsModal() {
    const settings = currentUpsSettings || {};
    const status = currentUpsStatus || {};
    const chargeSupported = !!status.charge_limit_supported;

    const { panel, close } = openSysModal({
        title: 'Configure Battery UPS',
        description: 'Use a laptop battery as backup power and shut down safely before it runs out.',
        confirmLabel: 'Save UPS settings',
        bodyHtml: `
            <label style="display:flex; align-items:center; cursor:pointer; margin-bottom:14px;">
                <input type="checkbox" id="m-ups-enabled" style="margin-right:8px;" ${settings.enabled ? 'checked' : ''}>
                <span style="font-size:0.85rem; color:var(--text-secondary);">Enable Battery UPS Mode</span>
            </label>
            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                <div>
                    <label class="setting-label" for="m-ups-charge">Charge Limit (%)</label>
                    <input type="number" id="m-ups-charge" min="50" max="100" style="width:100%;"
                        value="${Number(settings.charge_limit_percent || 80)}" ${chargeSupported ? '' : 'disabled title="This battery does not support configurable charge limits."'}>
                </div>
                <div>
                    <label class="setting-label" for="m-ups-shutdown">Shutdown At (%)</label>
                    <input type="number" id="m-ups-shutdown" min="5" max="80" style="width:100%;"
                        value="${Number(settings.shutdown_percent || 20)}">
                </div>
            </div>
            ${chargeSupported ? '' : '<p class="metric-sub" style="margin-top:10px;">This battery does not support configurable charge limits.</p>'}
        `,
        onConfirm: async ({ confirmBtn }) => {
            const enabled = !!panel.querySelector('#m-ups-enabled')?.checked;
            const chargeLimit = Number(panel.querySelector('#m-ups-charge')?.value || 80);
            const shutdownLimit = Number(panel.querySelector('#m-ups-shutdown')?.value || 20);
            const ok = await saveUpsSettings(enabled, chargeLimit, shutdownLimit, confirmBtn);
            if (ok) close();
        }
    });
}

async function saveUpsSettings(enabled, chargeLimit, shutdownLimit, confirmBtn) {
    if (!Number.isFinite(chargeLimit) || chargeLimit < 50 || chargeLimit > 100) {
        showError('Charge Limit must be between 50 and 100.');
        return false;
    }
    if (!Number.isFinite(shutdownLimit) || shutdownLimit < 5 || shutdownLimit > 80) {
        showError('Shutdown At must be between 5 and 80.');
        return false;
    }
    if (shutdownLimit >= chargeLimit) {
        showError('Shutdown At must be lower than Charge Limit.');
        return false;
    }

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Saving...';
    }

    try {
        const res = await fetch(`${API_BASE}/system/power/ups`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({
                enabled,
                charge_limit_percent: chargeLimit,
                shutdown_percent: shutdownLimit,
            })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            showError(data.error || 'Failed to save UPS settings');
            return false;
        }
        renderUpsSettings(data);
        showNotification('Battery UPS settings saved.', 'success');
        return true;
    } catch (e) {
        console.error(e);
        showError('Connection failed while saving UPS settings.');
        return false;
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Save UPS settings';
        }
    }
}

// --- Alerts / Telegram ---

function renderAlertsSummary() {
    const settings = currentAlertSettings || {};
    const telegram = settings.telegram || {};
    const pairing = settings.pairing || {};

    if (els.telegramSummaryStatus) {
        let text = 'Not configured';
        let color = 'var(--text-secondary)';
        if (telegram.enabled && telegram.paired) {
            text = 'Active — alerts will be delivered';
            color = 'var(--accent-success)';
        } else if (telegram.paired) {
            text = 'Paired, delivery disabled';
            color = 'var(--accent-warning)';
        } else if (pairing.active) {
            text = 'Pairing pending';
            color = 'var(--accent-warning)';
        }
        els.telegramSummaryStatus.textContent = text;
        if (els.telegramSummaryDot) els.telegramSummaryDot.style.background = color;
    }

    if (els.telegramSummaryChat) {
        els.telegramSummaryChat.textContent = telegram.paired_chat || '-';
    }
}

function renderTelegramModal() {
    if (!telegramModalEls) return;
    const settings = currentAlertSettings || {};
    const telegram = settings.telegram || {};
    const pairing = settings.pairing || {};

    if (telegramModalEls.enabled) telegramModalEls.enabled.checked = !!telegram.enabled;

    if (telegramModalEls.tokenStatus) {
        telegramModalEls.tokenStatus.textContent = telegram.bot_token_configured
            ? 'Token is configured. Leave the field empty to keep it unchanged.'
            : 'No token saved.';
    }

    if (telegramModalEls.pairingStatus) {
        if (telegram.paired) {
            telegramModalEls.pairingStatus.textContent = 'Paired';
            telegramModalEls.pairingStatus.style.color = 'var(--accent-success)';
        } else if (pairing.active) {
            telegramModalEls.pairingStatus.textContent = 'Pairing pending';
            telegramModalEls.pairingStatus.style.color = 'var(--accent-warning)';
        } else {
            telegramModalEls.pairingStatus.textContent = 'Not paired';
            telegramModalEls.pairingStatus.style.color = 'var(--text-secondary)';
        }
    }

    if (telegramModalEls.pairingCommand) {
        telegramModalEls.pairingCommand.textContent = (pairing.active && pairing.command) ? pairing.command : '-';
    }
    if (telegramModalEls.pairedChat) {
        telegramModalEls.pairedChat.textContent = telegram.paired_chat || '-';
    }
}

function renderAlertSettings(settings) {
    currentAlertSettings = settings || null;
    renderAlertsSummary();
    renderTelegramModal();
}

async function fetchAlertSettings() {
    try {
        const res = await fetch(`${API_BASE}/alerts/settings`, { headers: getHeaders() });
        if (res.status === 401) {
            window.location.href = '/login.html';
            return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            if (window.showToast) window.showToast(data.error || 'Failed to load alert settings', 'error');
            return;
        }
        renderAlertSettings(data.settings || {});
    } catch (e) {
        console.warn('Alert settings fetch failed', e);
    }
}

function openTelegramModal() {
    const { panel, close } = openSysModal({
        title: 'Configure Telegram',
        description: 'Pair a Telegram bot to receive critical system alerts.',
        cancelLabel: 'Close',
        onConfirm: null,
        onClose: () => { telegramModalEls = null; },
        bodyHtml: `
            <div class="setting-group">
                <label style="display: flex; align-items: center; cursor: pointer;">
                    <input type="checkbox" id="telegram-enabled-toggle" style="margin-right:8px;">
                    <span style="font-size:0.85rem; color:var(--text-secondary);">Enable Telegram delivery</span>
                </label>
            </div>

            <div class="setting-group">
                <label class="setting-label" for="telegram-bot-token">Bot Token</label>
                <input type="password" id="telegram-bot-token" placeholder="123456789:AA..." autocomplete="off" style="width:100%;">
                <div class="metric-sub" id="telegram-token-status" style="margin-top:6px;">No token saved.</div>
            </div>

            <div style="display:flex; gap:10px; flex-wrap: wrap; margin-bottom: 12px;">
                <button type="button" id="telegram-save-btn" class="btn-primary">Save Settings</button>
                <button type="button" id="telegram-start-pairing-btn" class="btn-secondary">Generate Pairing Code</button>
            </div>

            <div class="list-box" style="margin-bottom: 12px;">
                <div class="list-item">
                    <span>Pairing Status</span>
                    <span id="telegram-pairing-status" class="mono-text">Not paired</span>
                </div>
                <div class="list-item">
                    <span>Pairing Command</span>
                    <span id="telegram-pairing-command" class="mono-text">-</span>
                </div>
                <div class="list-item">
                    <span>Paired Chat</span>
                    <span id="telegram-paired-chat" class="mono-text">-</span>
                </div>
            </div>

            <div style="display:flex; gap:10px; flex-wrap: wrap;">
                <button type="button" id="telegram-check-pairing-btn" class="btn-secondary">Check Pairing</button>
                <button type="button" id="telegram-test-btn" class="btn-secondary">Send Test Alert</button>
                <button type="button" id="telegram-unpair-btn" class="btn-secondary"
                    style="border-color:var(--accent-danger); color:var(--accent-danger);">Unpair</button>
            </div>
        `
    });

    telegramModalEls = {
        enabled: panel.querySelector('#telegram-enabled-toggle'),
        token: panel.querySelector('#telegram-bot-token'),
        tokenStatus: panel.querySelector('#telegram-token-status'),
        pairingStatus: panel.querySelector('#telegram-pairing-status'),
        pairingCommand: panel.querySelector('#telegram-pairing-command'),
        pairedChat: panel.querySelector('#telegram-paired-chat'),
    };

    panel.querySelector('#telegram-save-btn')?.addEventListener('click', saveTelegramSettings);
    panel.querySelector('#telegram-start-pairing-btn')?.addEventListener('click', startTelegramPairing);
    panel.querySelector('#telegram-check-pairing-btn')?.addEventListener('click', checkTelegramPairing);
    panel.querySelector('#telegram-test-btn')?.addEventListener('click', sendTelegramTest);
    panel.querySelector('#telegram-unpair-btn')?.addEventListener('click', unpairTelegram);

    renderTelegramModal();
}

async function saveTelegramSettings() {
    if (!telegramModalEls) return;
    const payload = {
        telegram: {
            enabled: !!telegramModalEls.enabled?.checked,
        }
    };

    const tokenValue = String(telegramModalEls.token?.value || '').trim();
    if (tokenValue) {
        payload.telegram.bot_token = tokenValue;
    }

    try {
        const res = await fetch(`${API_BASE}/alerts/settings`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            showError(data.error || 'Failed to save Telegram settings');
            return;
        }

        if (telegramModalEls.token) telegramModalEls.token.value = '';
        renderAlertSettings(data.settings || {});
        showNotification('Telegram settings saved.', 'success');
    } catch (e) {
        console.error(e);
        showError('Connection failed while saving Telegram settings.');
    }
}

async function startTelegramPairing() {
    try {
        const res = await fetch(`${API_BASE}/alerts/telegram/pairing/start`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({})
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            showError(data.error || 'Failed to create pairing code');
            return;
        }

        if (data.pairing) {
            renderAlertSettings({ ...(currentAlertSettings || {}), pairing: data.pairing, telegram: currentAlertSettings?.telegram || {} });
        }
        showNotification(data.message || 'Pairing code generated.', 'info');
        await fetchAlertSettings();
    } catch (e) {
        console.error(e);
        showError('Connection failed while generating a pairing code.');
    }
}

async function checkTelegramPairing() {
    try {
        const res = await fetch(`${API_BASE}/alerts/telegram/pairing/check`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({})
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            showError(data.error || 'Pairing check failed');
            return;
        }

        if (data.settings) {
            renderAlertSettings(data.settings);
        }

        if (data.paired) {
            showNotification(data.message || 'Telegram pairing complete.', 'success');
        } else {
            showNotification(data.message || 'No matching Telegram pairing message found yet.', 'info');
        }
    } catch (e) {
        console.error(e);
        showError('Connection failed while checking Telegram pairing.');
    }
}

async function sendTelegramTest() {
    try {
        const res = await fetch(`${API_BASE}/alerts/telegram/test`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({})
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            showError(data.error || 'Failed to send test alert');
            return;
        }

        showNotification(data.message || 'Test alert sent.', 'success');
    } catch (e) {
        console.error(e);
        showError('Connection failed while sending test alert.');
    }
}

async function unpairTelegram() {
    const confirmed = await showConfirm('Remove Telegram pairing?\nYou can pair it again later.');
    if (!confirmed) return;

    try {
        const res = await fetch(`${API_BASE}/alerts/telegram/unpair`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({})
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            showError(data.error || 'Failed to remove pairing');
            return;
        }

        if (data.settings) {
            renderAlertSettings(data.settings);
        }
        showNotification(data.message || 'Telegram pairing removed.', 'success');
    } catch (e) {
        console.error(e);
        showError('Connection failed while removing Telegram pairing.');
    }
}

// --- Power actions ---

async function sendPowerAction(action) {
    if (!await showConfirm(`Are you sure you want to ${action} the system?`)) return;

    try {
        const res = await fetch(`${API_BASE}/system/power`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ action })
        });

        if (res.ok) {
            if (window.handleConnectionError) window.handleConnectionError();
        } else {
            const err = await res.json().catch(() => ({}));
            showError('Power action failed: ' + (err.error || 'Unknown error'));
        }
    } catch (e) {
        console.error('Power action error:', e);
        showError('Connection failed');
    }
}

// --- 2FA Management ---

async function fetch2faStatus() {
    try {
        const res = await fetch(`${API_BASE}/auth/2fa/status`, { headers: getHeaders() });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Failed to fetch 2FA status');

        const enabled = !!data.enabled;
        const totpAvailable = !!data.totp_available;

        if (!totpAvailable) {
            els.tfaStatusText.textContent = 'Library Not Installed';
            els.tfaStatusDot.style.background = 'var(--accent-warning)';
        } else {
            els.tfaStatusText.textContent = enabled ? 'Enabled' : 'Disabled';
            els.tfaStatusDot.style.background = enabled ? 'var(--accent-success)' : 'var(--text-secondary)';
        }

        if (els.tfaEnableBtn) {
            els.tfaEnableBtn.style.display = (!totpAvailable || enabled) ? 'none' : 'block';
        }
        if (els.tfaInstallBtn) {
            els.tfaInstallBtn.style.display = totpAvailable ? 'none' : 'block';
        }
        if (els.tfaDisableBtn) {
            els.tfaDisableBtn.style.display = (totpAvailable && enabled) ? 'block' : 'none';
        }
    } catch (e) {
        console.error('Failed to fetch 2FA status:', e);
    }
}

// --- SSH remote access ---

async function fetchSshStatus() {
    if (!els.sshStatusText) return;
    try {
        const res = await fetch(`${API_BASE}/system/ssh`, { headers: getHeaders() });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Failed to fetch SSH status');

        const enabled = !!data.enabled;
        const available = data.available !== false;

        if (!available) {
            els.sshStatusText.textContent = 'Not available on this system';
            els.sshStatusDot.style.background = 'var(--text-secondary)';
            if (els.sshEnableBtn) els.sshEnableBtn.style.display = 'none';
            if (els.sshDisableBtn) els.sshDisableBtn.style.display = 'none';
            return;
        }

        els.sshStatusText.textContent = enabled ? 'Enabled' : 'Disabled';
        // Enabled SSH is a deliberate choice, not a fault: amber, never red.
        els.sshStatusDot.style.background = enabled ? 'var(--accent-warning)' : 'var(--text-secondary)';
        if (els.sshEnableBtn) els.sshEnableBtn.style.display = enabled ? 'none' : 'block';
        if (els.sshDisableBtn) els.sshDisableBtn.style.display = enabled ? 'block' : 'none';
    } catch (e) {
        els.sshStatusText.textContent = 'Unknown';
        els.sshStatusDot.style.background = 'var(--text-secondary)';
    }
}

async function setSshAccess(enabled) {
    if (enabled) {
        const confirmed = await showConfirm(
            'Enable SSH access?\n\n' +
            'Anyone who knows the root password will be able to log in to this ' +
            'machine over the network. Leave it off unless you need a command line.'
        );
        if (!confirmed) return;
    }

    const button = enabled ? els.sshEnableBtn : els.sshDisableBtn;
    const originalText = button?.textContent;
    if (button) {
        button.disabled = true;
        button.textContent = enabled ? 'Enabling...' : 'Disabling...';
    }

    try {
        const res = await fetch(`${API_BASE}/system/ssh`, {
            method: 'POST',
            headers: getHeaders(true),
            body: JSON.stringify({ enabled })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showError(data.error || 'Could not change SSH access. Nothing was changed.');
            return;
        }
        showSuccess(enabled ? 'SSH access is now enabled.' : 'SSH access is now disabled.');
        await fetchSshStatus();
    } catch (e) {
        showError('Could not reach the NAS to change SSH access. Nothing was changed.');
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = originalText;
        }
    }
}

async function install2faLibrary() {
    if (!await showConfirm('Install missing 2FA libraries now?')) return;

    const installButton = els.tfaInstallBtn;
    const originalText = installButton?.textContent || 'Install 2FA Library';
    if (installButton) {
        installButton.disabled = true;
        installButton.textContent = 'Installing...';
    }

    try {
        const res = await fetch(`${API_BASE}/auth/2fa/install`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({})
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Failed to install 2FA library');

        if (window.showToast) window.showToast(data.message || '2FA library installed.', 'success');
        await fetch2faStatus();
    } catch (e) {
        showError(e.message || 'Failed to install 2FA library');
    } finally {
        if (installButton) {
            installButton.disabled = false;
            installButton.textContent = originalText;
        }
    }
}

async function setup2fa() {
    try {
        // 1. Get Secret & QR
        const res = await fetch(`${API_BASE}/auth/2fa/setup`, {
            method: 'POST',
            headers: getHeaders()
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Setup failed');
        const setupSecret = String(data.secret || '').trim();
        if (!setupSecret) throw new Error('Setup failed: missing secret from server');

        const { panel, close } = openSysModal({
            title: 'Setup 2FA',
            description: 'Scan this QR code with your authenticator app (Google Authenticator, Authy, Aegis).',
            confirmLabel: 'Verify & Enable',
            bodyHtml: `
                <img src="${escapeHtml(String(data.qr_code || ''))}" style="width: 200px; height: 200px; margin: 0 auto 1.5rem; display: block; background: white; padding: 10px; border-radius: 8px;" onerror="this.style.display='none';">
                <div class="setting-group" style="text-align: left;">
                    <label class="setting-label" for="tfa-verify-code">Verification Code</label>
                    <input type="text" id="tfa-verify-code" placeholder="6-digit code" style="width: 100%;">
                </div>
            `,
            onConfirm: async ({ confirmBtn }) => {
                const code = panel.querySelector('#tfa-verify-code')?.value;
                confirmBtn.disabled = true;
                confirmBtn.textContent = 'Verifying...';
                try {
                    const verifyRes = await fetch(`${API_BASE}/auth/2fa/verify-setup`, {
                        method: 'POST',
                        headers: getHeaders(),
                        body: JSON.stringify({ secret: setupSecret, code })
                    });
                    const vData = await verifyRes.json();
                    if (!verifyRes.ok) throw new Error(vData.error || 'Verification failed');

                    close();
                    if (window.showToast) window.showToast('Two-Factor Authentication enabled successfully!', 'success');
                    fetch2faStatus();
                } catch (err) {
                    showError(err.message);
                    confirmBtn.disabled = false;
                    confirmBtn.textContent = 'Verify & Enable';
                }
            }
        });
        panel.querySelector('#tfa-verify-code')?.focus();
    } catch (e) {
        showError(e.message);
    }
}

async function disable2fa() {
    const password = await window.showPrompt('Disable 2FA\nPlease enter your root password to continue.', {
        type: 'password',
        label: 'Root Password',
        placeholder: 'Current root password',
        confirmLabel: 'Disable 2FA'
    });
    if (!password) return;

    try {
        const res = await fetch(`${API_BASE}/auth/2fa/disable`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ password })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to disable 2FA');

        if (window.showToast) window.showToast('2FA has been disabled.', 'success');
        fetch2faStatus();
    } catch (e) {
        showError(e.message);
    }
}

// --- Watchdog Management ---

async function fetchWatchdogStatus() {
    try {
        const res = await fetch(`${API_BASE}/watchdog/status`, { headers: getHeaders() });
        const data = await res.json();
        if (!res.ok) return;

        renderWatchdog(data);
    } catch (e) {
        console.error('Watchdog fetch error:', e);
    }
}

function renderWatchdog(data) {
    if (!els.watchdogList) return;

    // Services
    els.watchdogList.innerHTML = '';
    data.services.forEach(svc => {
        const item = document.createElement('div');
        item.className = 'list-item';
        item.innerHTML = `
            <span>${svc.label} <small style="color:var(--text-secondary); margin-left:8px;">${svc.name}</small></span>
            <span class="status-badge" style="border:none; background:transparent; padding:0;">
                <span class="status-dot" style="background:${svc.active ? 'var(--accent-success)' : 'var(--accent-danger)'};"></span>
                <span>${svc.active ? 'Active' : 'Stopped'}</span>
            </span>
        `;
        els.watchdogList.appendChild(item);
    });

    // Last Run
    if (data.last_check) {
        const date = new Date(data.last_check);
        els.watchdogLastRun.textContent = `Last check: ${date.toLocaleTimeString()}`;
    }

    // Recoveries
    if (data.recoveries && data.recoveries.length > 0) {
        els.watchdogRecoveryLog.style.display = 'block';
        els.watchdogRecoveryItems.innerHTML = '';
        data.recoveries.forEach(rec => {
            const date = new Date(rec.timestamp);
            const item = document.createElement('div');
            item.className = 'list-item';
            item.style.fontSize = '0.8rem';
            item.innerHTML = `
                <div style="display:flex; flex-direction:column;">
                    <span style="font-weight:600;">Restarted ${rec.label}</span>
                    <span style="color:var(--text-secondary);">${date.toLocaleString()}</span>
                    ${rec.error ? `<span style="color:var(--accent-danger); font-size:0.75rem;">Error: ${rec.error}</span>` : ''}
                </div>
                <span style="color:${rec.recovered ? 'var(--accent-success)' : 'var(--accent-danger)'}; font-weight:600;">
                    ${rec.recovered ? 'FIXED' : 'FAILED'}
                </span>
            `;
            els.watchdogRecoveryItems.appendChild(item);
        });
    } else {
        els.watchdogRecoveryLog.style.display = 'none';
    }
}

async function runWatchdogCheck() {
    els.watchdogCheckBtn.disabled = true;
    els.watchdogCheckBtn.textContent = 'Checking...';
    try {
        const res = await fetch(`${API_BASE}/watchdog/check`, {
            method: 'POST',
            headers: getHeaders()
        });
        const data = await res.json();
        if (res.ok) {
            renderWatchdog({
                services: data.services,
                last_check: data.last_check,
                recoveries: [] // Fetch full status next to get log
            });
            await fetchWatchdogStatus();
            if (window.showToast) window.showToast('System health check complete.', 'success');
        }
    } catch (e) {
        console.error(e);
    } finally {
        els.watchdogCheckBtn.disabled = false;
        els.watchdogCheckBtn.textContent = 'Run Check Now';
    }
}

// --- Tab switching ---

function setupTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabPanels = document.querySelectorAll('.tab-panel');

    const show = (btn) => {
        const tabName = btn.dataset.tab;
        tabBtns.forEach(b => b.classList.remove('active'));
        tabPanels.forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(`tab-${tabName}`)?.classList.add('active');
    };

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            show(btn);
            // Keep the tab in the URL (system.html#security), like Storage does.
            history.replaceState(null, '', `#${btn.dataset.tab}`);
        });
    });

    const fromHash = () => {
        const name = window.location.hash.slice(1);
        const btn = Array.from(tabBtns).find((b) => b.dataset.tab === name);
        if (btn) show(btn);
    };
    fromHash();
    window.addEventListener('hashchange', fromHash);
}

document.addEventListener('DOMContentLoaded', () => {
    setupTabs();

    setInterval(() => {
        if (els.timeDisplay) els.timeDisplay.textContent = new Date().toLocaleString();
    }, 1000);

    fetchSettings();

    document.getElementById('change-hostname-btn')?.addEventListener('click', openHostnameModal);
    document.getElementById('change-time-btn')?.addEventListener('click', openTimeModal);
    document.getElementById('configure-telegram-btn')?.addEventListener('click', openTelegramModal);
    document.getElementById('configure-ups-btn')?.addEventListener('click', openUpsModal);

    document.getElementById('reboot-btn')?.addEventListener('click', () => sendPowerAction('reboot'));
    document.getElementById('shutdown-btn')?.addEventListener('click', () => sendPowerAction('shutdown'));

    els.tfaEnableBtn?.addEventListener('click', setup2fa);
    els.tfaInstallBtn?.addEventListener('click', install2faLibrary);
    els.tfaDisableBtn?.addEventListener('click', disable2fa);
    els.sshEnableBtn?.addEventListener('click', () => setSshAccess(true));
    els.sshDisableBtn?.addEventListener('click', () => setSshAccess(false));
    els.watchdogCheckBtn?.addEventListener('click', runWatchdogCheck);

    // Poll watchdog and UPS status periodically
    setInterval(fetchWatchdogStatus, 30000);
    setInterval(fetchUpsSettings, 30000);
});
