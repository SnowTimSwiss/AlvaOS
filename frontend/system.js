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

    telegramEnabled: document.getElementById('telegram-enabled-toggle'),
    telegramToken: document.getElementById('telegram-bot-token'),
    telegramTokenStatus: document.getElementById('telegram-token-status'),
    telegramPairingStatus: document.getElementById('telegram-pairing-status'),
    telegramPairingCommand: document.getElementById('telegram-pairing-command'),
    telegramPairedChat: document.getElementById('telegram-paired-chat'),
};

let currentTimeSettings = {
    timezone: null,
    ntp: null
};

let currentAlertSettings = null;

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

    await fetchAlertSettings();
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
});
