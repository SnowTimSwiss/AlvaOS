// AlvaOS System Settings Logic
const API_BASE = '/api/v1';

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

// Fetch System Settings
async function fetchSettings() {
    try {
        const token = localStorage.getItem('alvaos_token');
        const headers = { 'Authorization': token || '' };

        // 1. Fetch Network & Hostname (Enhanced Info)
        const netRes = await fetch(`${API_BASE}/system/network`, { headers });
        if (netRes.ok) {
            const netData = await netRes.json();

            // Populate Network Card
            els.netInterface.textContent = netData.interface;
            els.netIp.textContent = netData.ip_address;
            els.netMask.textContent = netData.subnet_mask;
            els.netGateway.textContent = netData.gateway;
            els.netDns.textContent = netData.dns.join(', ');

            // Populate Hostname Input
            els.hostnameInput.value = netData.hostname;
        }

        // 2. Fetch Logs
        const logRes = await fetch(`${API_BASE}/system/logs`, { headers });
        if (logRes.ok) {
            const logData = await logRes.json();
            els.logViewer.textContent = logData.logs.join('\n');
            // Scroll to bottom
            els.logViewer.scrollTop = els.logViewer.scrollHeight;
        }

    } catch (error) {
        console.error('Error fetching settings:', error);
    }
}

// Update Hostname
async function updateHostname() {
    const newHostname = els.hostnameInput.value.trim();
    if (!newHostname) return;

    try {
        const token = localStorage.getItem('alvaos_token');
        const res = await fetch(`${API_BASE}/system/hostname`, {
            method: 'PUT',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ hostname: newHostname })
        });

        if (res.ok) {
            alert('Hostname updated! System may need a reboot.');
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
        const token = localStorage.getItem('alvaos_token');
        const res = await fetch(`${API_BASE}/system/time`, {
            method: 'POST',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
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
    if (!confirm(`Are you sure you want to ${action} the system?`)) return;

    try {
        const token = localStorage.getItem('alvaos_token');
        const res = await fetch(`${API_BASE}/system/power`, {
            method: 'POST',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ action })
        });

        if (res.ok) {
            alert(`System ${action} initiated. Web interface will close.`);
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
