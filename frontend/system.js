// AlvaOS System Settings Logic
// API_BASE is defined in app.js

// DOM Elements
const els = {
    hostnameInput: document.getElementById('hostname-input'),
    saveBtn: document.getElementById('save-hostname-btn'),
    netInterface: document.getElementById('net-interface'),
    netIp: document.getElementById('net-ip'),
    netMask: document.getElementById('net-mask'),
    netGateway: document.getElementById('net-gateway'),
    netDns: document.getElementById('net-dns'),
    logViewer: document.getElementById('log-viewer'),
    timeDisplay: document.getElementById('system-time-display')
};

// Helper: Get Request Headers with fresh token
function getHeaders() {
    const token = localStorage.getItem('alvaos_token');
    return {
        'Authorization': token || '',
        'Content-Type': 'application/json'
    };
}

// Fetch System Settings
async function fetchSettings() {
    // 1. Fetch Network & Hostname
    try {
        const netRes = await fetch(`${API_BASE}/system/network`, { headers: getHeaders() });

        if (netRes.status === 401) {
            window.location.href = '/login.html';
            return;
        }

        if (netRes.ok) {
            const netData = await netRes.json();
            els.netInterface.textContent = netData.interface || 'N/A';
            els.netIp.textContent = netData.ip_address || 'N/A';
            els.netMask.textContent = netData.subnet_mask || 'N/A';
            els.netGateway.textContent = netData.gateway || 'N/A';
            els.netDns.textContent = (netData.dns && netData.dns.length > 0) ? netData.dns.join(', ') : 'N/A';
            if (els.hostnameInput) {
                const hostnameVal = netData.hostname || '';
                els.hostnameInput.value = hostnameVal;
                // Update display if it exists in another element
                const displayElem = document.getElementById('current-hostname-display');
                if (displayElem) displayElem.textContent = hostnameVal;
            }
        } else {
            console.warn('Network fetch failed with status:', netRes.status);
        }
    } catch (e) {
        console.error('Network fetch error:', e);
    }

    // 2. Fetch Time Settings
    try {
        const timeRes = await fetch(`${API_BASE}/system/time`, { headers: getHeaders() });
        if (timeRes.ok) {
            const timeData = await timeRes.json();
            const tzSelect = document.getElementById('timezone-select');
            const ntpToggle = document.getElementById('ntp-toggle');
            if (tzSelect) tzSelect.value = timeData.timezone || 'UTC';
            if (ntpToggle) ntpToggle.checked = timeData.ntp_enabled;
        }
    } catch (e) {
        console.warn('Time fetch error', e);
    }

    // 3. Fetch Logs
    try {
        const logRes = await fetch(`${API_BASE}/system/logs`, { headers: getHeaders() });
        if (logRes.ok) {
            const logData = await logRes.json();
            els.logViewer.textContent = logData.logs.join('\n');
            els.logViewer.scrollTop = els.logViewer.scrollHeight;
        } else {
            els.logViewer.textContent = 'Failed to load logs.';
        }
    } catch (e) {
        console.warn('Log fetch error', e);
        els.logViewer.textContent = 'Connection error loading logs.';
    }
}

// Update Hostname
async function updateHostname() {
    const newHostname = els.hostnameInput.value.trim();
    if (!newHostname) return;

    try {
        const res = await fetch(`${API_BASE}/system/hostname`, {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify({ hostname: newHostname })
        });

        if (res.ok) {
            alert('Hostname updated! System may need a reboot.');
            fetchSettings(); // Refresh
        } else {
            const err = await res.json();
            alert('Failed to update: ' + err.error);
        }
    } catch (error) {
        alert('Connection failed');
    }
}

// Update Time Settings
async function updateTimeSettings() {
    const timezone = document.getElementById('timezone-select').value;
    const ntp = document.getElementById('ntp-toggle').checked;

    try {
        const res = await fetch(`${API_BASE}/system/time`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ timezone, ntp })
        });

        if (res.ok) {
            alert('Time settings updated.');
        } else {
            alert('Failed to update time settings.');
        }
    } catch (e) {
        console.error(e);
        alert('Connection failed');
    }
}

// Power Action
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
        }
    } catch (e) {
        alert('Action failed');
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    // Start Clock
    setInterval(() => {
        if (els.timeDisplay) els.timeDisplay.textContent = new Date().toLocaleString();
    }, 1000);

    // Load Data
    fetchSettings();

    // Event Listeners
    if (els.saveBtn) els.saveBtn.addEventListener('click', updateHostname);

    // Time & Power Listeners
    const saveTimeBtn = document.getElementById('save-time-btn');
    if (saveTimeBtn) saveTimeBtn.addEventListener('click', updateTimeSettings);

    const rebootBtn = document.getElementById('reboot-btn');
    if (rebootBtn) rebootBtn.addEventListener('click', () => sendPowerAction('reboot'));

    const shutdownBtn = document.getElementById('shutdown-btn');
    if (shutdownBtn) shutdownBtn.addEventListener('click', () => sendPowerAction('shutdown'));
});
