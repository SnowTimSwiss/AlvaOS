// AlvaOS System Settings Logic
// API_BASE is defined in app.js

const els = {
    hostnameInput: document.getElementById('hostname-input'),
    saveBtn: document.getElementById('save-hostname-btn'),
    netInterface: document.getElementById('net-interface'),
    netIp: document.getElementById('net-ip'),
    netMask: document.getElementById('net-mask'),
    netGateway: document.getElementById('net-gateway'),
    netDns: document.getElementById('net-dns'),
    logViewer: document.getElementById('log-viewer'),
    timeDisplay: document.getElementById('system-time-display'),
    upsEnabledToggle: document.getElementById('ups-enabled-toggle'),
    upsChargeLimitInput: document.getElementById('ups-charge-limit-input'),
    upsShutdownLimitInput: document.getElementById('ups-shutdown-limit-input'),
    upsSaveBtn: document.getElementById('ups-save-btn'),
    upsDevice: document.getElementById('ups-device'),
    upsAcState: document.getElementById('ups-ac-state'),
    upsBatteryState: document.getElementById('ups-battery-state'),
    upsCapacity: document.getElementById('ups-capacity'),
    upsChargeSupport: document.getElementById('ups-charge-support'),
    upsThresholdCurrent: document.getElementById('ups-threshold-current'),
    upsLastAction: document.getElementById('ups-last-action'),

    telegramEnabled: document.getElementById('telegram-enabled-toggle'),
    telegramToken: document.getElementById('telegram-bot-token'),
    telegramTokenStatus: document.getElementById('telegram-token-status'),
    telegramPairingStatus: document.getElementById('telegram-pairing-status'),
    telegramPairingCommand: document.getElementById('telegram-pairing-command'),
    telegramPairedChat: document.getElementById('telegram-paired-chat'),

    // 2FA
    tfaStatusText: document.getElementById('tfa-status-text'),
    tfaStatusDot: document.getElementById('tfa-status-dot'),
    tfaEnableBtn: document.getElementById('tfa-enable-btn'),
    tfaInstallBtn: document.getElementById('tfa-install-btn'),
    tfaDisableBtn: document.getElementById('tfa-disable-btn'),

    // Watchdog
    watchdogList: document.getElementById('watchdog-service-list'),
    watchdogCheckBtn: document.getElementById('watchdog-check-btn'),
    watchdogLastRun: document.getElementById('watchdog-last-run'),
    watchdogRecoveryLog: document.getElementById('watchdog-recovery-log'),
    watchdogRecoveryItems: document.getElementById('watchdog-recovery-items'),
};

let currentTimeSettings = {
    timezone: null,
    ntp: null
};

let currentAlertSettings = null;
let currentUpsSettings = null;

function getHeaders() {
    const token = localStorage.getItem('alvaos_token');
    return {
        'Authorization': token || '',
        'Content-Type': 'application/json'
    };
}

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

            if (els.hostnameInput) {
                const hostnameVal = netData.hostname || '';
                els.hostnameInput.value = hostnameVal;
                const displayElem = document.getElementById('current-hostname-display');
                if (displayElem) displayElem.textContent = hostnameVal;
            }
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
            const tzSelect = document.getElementById('timezone-select');
            const ntpToggle = document.getElementById('ntp-toggle');

            currentTimeSettings.timezone = timeData.timezone || 'UTC';
            currentTimeSettings.ntp = !!timeData.ntp_enabled;

            if (tzSelect) {
                const tzValue = currentTimeSettings.timezone;
                const hasOption = Array.from(tzSelect.options).some(opt => opt.value === tzValue);
                if (!hasOption) {
                    const opt = document.createElement('option');
                    opt.value = tzValue;
                    opt.textContent = tzValue;
                    tzSelect.appendChild(opt);
                }
                tzSelect.value = tzValue;
            }

            if (ntpToggle) ntpToggle.checked = timeData.ntp_enabled;
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
            els.logViewer.textContent = 'Failed to load logs.';
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
    await fetchWatchdogStatus();
}

function renderUpsSettings(data) {
    const settings = data?.settings || {};
    const status = data?.status || {};
    currentUpsSettings = settings;

    if (els.upsEnabledToggle) {
        els.upsEnabledToggle.checked = !!settings.enabled;
    }
    if (els.upsChargeLimitInput) {
        els.upsChargeLimitInput.value = Number(settings.charge_limit_percent || 80);
    }
    if (els.upsShutdownLimitInput) {
        els.upsShutdownLimitInput.value = Number(settings.shutdown_percent || 20);
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

    if (els.upsChargeLimitInput) {
        els.upsChargeLimitInput.disabled = !status.charge_limit_supported;
        if (!status.charge_limit_supported) {
            els.upsChargeLimitInput.title = 'This battery does not support configurable charge limits.';
        } else {
            els.upsChargeLimitInput.title = '';
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

async function saveUpsSettings() {
    const enabled = !!els.upsEnabledToggle?.checked;
    const chargeLimit = Number(els.upsChargeLimitInput?.value || 80);
    const shutdownLimit = Number(els.upsShutdownLimitInput?.value || 20);

    if (!Number.isFinite(chargeLimit) || chargeLimit < 50 || chargeLimit > 100) {
        alert('Charge Limit must be between 50 and 100.');
        return;
    }
    if (!Number.isFinite(shutdownLimit) || shutdownLimit < 5 || shutdownLimit > 80) {
        alert('Shutdown At must be between 5 and 80.');
        return;
    }
    if (shutdownLimit >= chargeLimit) {
        alert('Shutdown At must be lower than Charge Limit.');
        return;
    }

    const payload = {
        enabled,
        charge_limit_percent: chargeLimit,
        shutdown_percent: shutdownLimit,
    };

    try {
        const res = await fetch(`${API_BASE}/system/power/ups`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            alert(data.error || 'Failed to save UPS settings');
            return;
        }
        renderUpsSettings(data);
        if (window.showToast) {
            window.showToast('Battery UPS settings saved.', 'success');
        } else {
            alert('Battery UPS settings saved.');
        }
    } catch (e) {
        console.error(e);
        alert('Connection failed while saving UPS settings.');
    }
}

function renderAlertSettings(settings) {
    currentAlertSettings = settings || null;

    const telegram = settings?.telegram || {};
    const pairing = settings?.pairing || {};

    if (els.telegramEnabled) {
        els.telegramEnabled.checked = !!telegram.enabled;
    }

    if (els.telegramTokenStatus) {
        els.telegramTokenStatus.textContent = telegram.bot_token_configured
            ? 'Token is configured. Leave the field empty to keep it unchanged.'
            : 'No token saved.';
    }

    if (els.telegramPairingStatus) {
        if (telegram.paired) {
            els.telegramPairingStatus.textContent = 'Paired';
            els.telegramPairingStatus.style.color = 'var(--accent-success)';
        } else if (pairing.active) {
            els.telegramPairingStatus.textContent = 'Pairing pending';
            els.telegramPairingStatus.style.color = 'var(--accent-warning)';
        } else {
            els.telegramPairingStatus.textContent = 'Not paired';
            els.telegramPairingStatus.style.color = 'var(--text-secondary)';
        }
    }

    if (els.telegramPairingCommand) {
        if (pairing.active && pairing.command) {
            els.telegramPairingCommand.textContent = pairing.command;
        } else {
            els.telegramPairingCommand.textContent = '-';
        }
    }

    if (els.telegramPairedChat) {
        els.telegramPairedChat.textContent = telegram.paired_chat || '-';
    }
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

async function saveTelegramSettings() {
    const payload = {
        telegram: {
            enabled: !!els.telegramEnabled?.checked,
        }
    };

    const tokenValue = String(els.telegramToken?.value || '').trim();
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
            alert(data.error || 'Failed to save Telegram settings');
            return;
        }

        if (els.telegramToken) els.telegramToken.value = '';
        renderAlertSettings(data.settings || {});
        alert('Telegram settings saved.');
    } catch (e) {
        console.error(e);
        alert('Connection failed while saving Telegram settings.');
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
            alert(data.error || 'Failed to create pairing code');
            return;
        }

        if (data.pairing) {
            renderAlertSettings({ ...(currentAlertSettings || {}), pairing: data.pairing, telegram: currentAlertSettings?.telegram || {} });
        }
        alert(data.message || 'Pairing code generated.');
        await fetchAlertSettings();
    } catch (e) {
        console.error(e);
        alert('Connection failed while generating a pairing code.');
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
            alert(data.error || 'Pairing check failed');
            return;
        }

        if (data.settings) {
            renderAlertSettings(data.settings);
        }

        if (data.paired) {
            alert(data.message || 'Telegram pairing complete.');
        } else {
            alert(data.message || 'No matching Telegram pairing message found yet.');
        }
    } catch (e) {
        console.error(e);
        alert('Connection failed while checking Telegram pairing.');
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
            alert(data.error || 'Failed to send test alert');
            return;
        }

        alert(data.message || 'Test alert sent.');
    } catch (e) {
        console.error(e);
        alert('Connection failed while sending test alert.');
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
            alert(data.error || 'Failed to remove pairing');
            return;
        }

        if (data.settings) {
            renderAlertSettings(data.settings);
        }
        alert(data.message || 'Telegram pairing removed.');
    } catch (e) {
        console.error(e);
        alert('Connection failed while removing Telegram pairing.');
    }
}

async function updateHostname() {
    const newHostname = els.hostnameInput?.value?.trim();
    if (!newHostname) return;

    try {
        const res = await fetch(`${API_BASE}/system/hostname`, {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify({ hostname: newHostname })
        });

        if (res.ok) {
            alert('Hostname updated! System may need a reboot.');
            fetchSettings();
        } else {
            const err = await res.json().catch(() => ({}));
            alert('Failed to update: ' + (err.error || 'Unknown error'));
        }
    } catch (error) {
        alert('Connection failed');
    }
}

async function updateTimeSettings() {
    const timezone = document.getElementById('timezone-select')?.value;
    const ntp = !!document.getElementById('ntp-toggle')?.checked;
    const payload = {};

    if (timezone && timezone !== currentTimeSettings.timezone) {
        payload.timezone = timezone;
    }
    if (currentTimeSettings.ntp === null || ntp !== currentTimeSettings.ntp) {
        payload.ntp = ntp;
    }

    if (Object.keys(payload).length === 0) {
        alert('No time settings changed.');
        return;
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

            if (Array.isArray(data.warnings) && data.warnings.length) {
                alert(`Time settings updated with warnings:\n${data.warnings.join('\n')}`);
            } else {
                alert(data.message || 'Time settings updated.');
            }
        } else {
            alert(`Failed to update time settings: ${data.error || 'Unknown error'}`);
        }
    } catch (e) {
        console.error(e);
        alert('Connection failed');
    }
}

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
            alert('Power action failed: ' + (err.error || 'Unknown error'));
        }
    } catch (e) {
        console.error('Power action error:', e);
        alert('Connection failed');
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
        alert(e.message || 'Failed to install 2FA library');
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

        // Create a simple modal div for setup
        const modal = document.createElement('div');
        modal.className = 'setup-modal';
        modal.style = `
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.8); display: flex; align-items: center;
            justify-content: center; z-index: 9999; padding: 20px;
        `;
        modal.innerHTML = `
            <div class="card" style="max-width: 400px; width: 100%; text-align: center; border: 1px solid var(--border-default); background: var(--bg-card); padding: 24px; border-radius: 8px;">
                <h2 style="margin-bottom: 1rem; color: var(--text-primary);">Setup 2FA</h2>
                <p style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 1.5rem;">
                    Scan this QR code with your authenticator app (Google Authenticator, Authy, Aegis).
                </p>
                <img src="${data.qr_code}" style="width: 200px; height: 200px; margin: 0 auto 1.5rem; display: block; background: white; padding: 10px; border-radius: 8px;">
                <div class="setting-group" style="text-align: left;">
                    <label class="setting-label">Verification Code</label>
                    <input type="text" id="tfa-verify-code" placeholder="6-digit code" style="width: 100%; background: var(--bg-body); border: 1px solid var(--border-default); color: var(--text-primary); padding: 8px; border-radius: 4px;">
                </div>
                <div style="display: flex; gap: 10px; margin-top: 1.5rem;">
                    <button id="tfa-cancel-setup" class="btn-secondary" style="flex:1;">Cancel</button>
                    <button id="tfa-confirm-setup" class="btn-primary" style="flex:1;">Verify & Enable</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        document.getElementById('tfa-cancel-setup').onclick = () => modal.remove();
        document.getElementById('tfa-confirm-setup').onclick = async () => {
            const code = document.getElementById('tfa-verify-code').value;
            try {
                const verifyRes = await fetch(`${API_BASE}/auth/2fa/verify-setup`, {
                    method: 'POST',
                    headers: getHeaders(),
                    body: JSON.stringify({ secret: setupSecret, code })
                });
                const vData = await verifyRes.json();
                if (!verifyRes.ok) throw new Error(vData.error || 'Verification failed');

                modal.remove();
                if (window.showToast) window.showToast('Two-Factor Authentication enabled successfully!', 'success');
                fetch2faStatus();
            } catch (err) {
                alert(err.message);
            }
        };
    } catch (e) {
        alert(e.message);
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
        alert(e.message);
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

document.addEventListener('DOMContentLoaded', () => {
    setInterval(() => {
        if (els.timeDisplay) els.timeDisplay.textContent = new Date().toLocaleString();
    }, 1000);

    fetchSettings();

    if (els.saveBtn) els.saveBtn.addEventListener('click', updateHostname);

    const saveTimeBtn = document.getElementById('save-time-btn');
    if (saveTimeBtn) saveTimeBtn.addEventListener('click', updateTimeSettings);

    const rebootBtn = document.getElementById('reboot-btn');
    if (rebootBtn) rebootBtn.addEventListener('click', () => sendPowerAction('reboot'));

    const shutdownBtn = document.getElementById('shutdown-btn');
    if (shutdownBtn) shutdownBtn.addEventListener('click', () => sendPowerAction('shutdown'));

    document.getElementById('telegram-save-btn')?.addEventListener('click', saveTelegramSettings);
    document.getElementById('telegram-start-pairing-btn')?.addEventListener('click', startTelegramPairing);
    document.getElementById('telegram-check-pairing-btn')?.addEventListener('click', checkTelegramPairing);
    document.getElementById('telegram-test-btn')?.addEventListener('click', sendTelegramTest);
    document.getElementById('telegram-unpair-btn')?.addEventListener('click', unpairTelegram);

    els.tfaEnableBtn?.addEventListener('click', setup2fa);
    els.tfaInstallBtn?.addEventListener('click', install2faLibrary);
    els.tfaDisableBtn?.addEventListener('click', disable2fa);
    els.watchdogCheckBtn?.addEventListener('click', runWatchdogCheck);
    els.upsSaveBtn?.addEventListener('click', saveUpsSettings);

    // Poll watchdog status every 30s
    setInterval(fetchWatchdogStatus, 30000);
    setInterval(fetchUpsSettings, 30000);
});
