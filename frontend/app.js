// AlvaOS Web UI - System Info Dashboard
const API_BASE = '/api/v1';
let updateInterval;

// Update Clock
function updateClock() {
    const clockElement = document.getElementById('clock');
    if (!clockElement) return;

    const now = new Date();
    clockElement.textContent = now.toLocaleTimeString();
}

// Fetch system information
async function fetchSystemInfo() {
    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/system/info`, {
            headers: {
                'Authorization': token || ''
            }
        });

        if (response.status === 401) {
            window.location.href = '/login.html';
            return;
        }

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        updateDashboard(data);
    } catch (error) {
        console.error('Error fetching system info:', error);

        // Only show full blocking error if we are genuinely disconnected
        // Wait one cycle? No, if fetch fails, trigger logic immediately.
        handleConnectionError();

        // Update status dot to critical
        const dot = document.querySelector('.status-dot');
        if (dot) dot.className = 'status-dot critical';
    }
}

// Update dashboard with system data
function updateDashboard(data) {
    // CPU Information
    document.getElementById('cpu-cores').textContent =
        `${data.cpu.cores} / ${data.cpu.threads}`;
    document.getElementById('cpu-usage').textContent =
        `${data.cpu.usage_percent.toFixed(1)}%`;
    document.getElementById('cpu-freq').textContent =
        `${data.cpu.frequency_mhz} MHz`;
    const cpuModelEl = document.getElementById('cpu-model');
    if (cpuModelEl) {
        cpuModelEl.textContent = data.cpu.model || 'Unknown CPU';
    }
    const cpuTempEl = document.getElementById('cpu-temp');
    if (cpuTempEl) {
        cpuTempEl.textContent = (data.cpu.temperature_c || data.cpu.temperature_c === 0)
            ? `${data.cpu.temperature_c}°C`
            : 'N/A';
    }
    document.getElementById('cpu-progress').style.width =
        `${data.cpu.usage_percent}%`;

    // Memory Information
    document.getElementById('mem-total').textContent =
        `${data.memory.total_gb} GB`;
    document.getElementById('mem-used').textContent =
        `${data.memory.used_gb} GB`;
    document.getElementById('mem-available').textContent =
        `${data.memory.available_gb} GB`;
    document.getElementById('mem-usage-percent').textContent =
        `${data.memory.percent.toFixed(1)}%`;
    document.getElementById('mem-progress').style.width =
        `${data.memory.percent}%`;

    // Disk Information
    document.getElementById('disk-total').textContent =
        `${data.disk.total_gb} GB`;
    document.getElementById('disk-used').textContent =
        `${data.disk.used_gb} GB`;
    document.getElementById('disk-free').textContent =
        `${data.disk.free_gb} GB`;
    document.getElementById('disk-usage-percent').textContent =
        `${data.disk.percent.toFixed(1)}%`;
    document.getElementById('disk-progress').style.width =
        `${data.disk.percent}%`;

    // System Information
    document.getElementById('hostname').textContent = data.network.hostname;
    document.getElementById('ip-address').textContent = data.network.ip_address;
    document.getElementById('os-version').textContent =
        `${data.system.os} ${data.system.os_version}`;
    document.getElementById('uptime').textContent =
        `${data.system.uptime_hours.toFixed(1)} hours`;

    // Update timestamp
    const now = new Date();
    document.getElementById('last-update').textContent =
        now.toLocaleTimeString();

    // Reset status dot
    const dot = document.querySelector('.status-dot');
    if (dot) dot.className = 'status-dot online';

    // Remove error if present
    const errorDiv = document.querySelector('.error-banner');
    if (errorDiv) {
        errorDiv.remove();
    }
}

// Show error message (using Toast or overlay for critical)
function showError(message) {
    if (window.showToast) {
        window.showToast(message, 'error');
    } else {
        console.error(message);
    }
}

// Global Reconnect Logic
let isReconnecting = false;

function handleConnectionError() {
    if (isReconnecting) return;
    isReconnecting = true;

    // Show reconnect overlay
    const overlay = document.createElement('div');
    overlay.id = 'reconnect-overlay';
    overlay.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.85); z-index: 20000;
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        color: white; backdrop-filter: blur(5px);
    `;
    overlay.innerHTML = `
        <div style="font-size: 3rem; margin-bottom: 1rem; animation: spin 1s linear infinite;">↻</div>
        <h2 style="margin-bottom: 0.5rem;">Connection Lost</h2>
        <p style="color: var(--text-secondary);">Waiting for AlvaOS to come back online...</p>
        <style>@keyframes spin { 100% { transform: rotate(360deg); } }</style>
    `;
    document.body.appendChild(overlay);

    // Initial check delay (give it time to actually shut down)
    setTimeout(startPolling, 3000);

    function startPolling() {
        const interval = setInterval(async () => {
            try {
                const controller = new AbortController();
                const id = setTimeout(() => controller.abort(), 2000);

                const token = localStorage.getItem('alvaos_token');
                const res = await fetch(`${API_BASE}/system/info`, {
                    signal: controller.signal,
                    headers: { 'Authorization': token || '' }
                });
                clearTimeout(id);

                if (res.ok || res.status === 401) {
                    clearInterval(interval);
                    isReconnecting = false;
                    document.getElementById('reconnect-overlay').remove();

                    if (res.status === 401) {
                        window.location.href = '/login.html';
                    } else {
                        window.showToast('We are back online!', 'success');
                        // Refresh data immediately
                        if (document.getElementById('cpu-usage')) fetchSystemInfo();
                        // Reload strictly if we were in a "dead" state
                    }
                }
            } catch (e) {
                // Still down, keep waiting
            }
        }, 3000);
    }
}

// Initialize dashboard
async function init() {
    console.log('AlvaOS Dashboard initializing...');

    // Check if setup is complete
    try {
        const response = await fetch(`${API_BASE}/setup/status`);
        if (response.ok) {
            const data = await response.json();
            if (!data.setup_complete) {
                // Redirect to setup wizard
                window.location.href = '/setup.html';
                return;
            } else {
                // Setup complete, check if we have a token
                const token = localStorage.getItem('alvaos_token');
                if (!token) {
                    window.location.href = '/login.html';
                    return;
                }
            }
        }
    } catch (error) {
        console.error('Failed to check setup status:', error);
    }

    // Start clock
    updateClock();
    setInterval(updateClock, 1000);

    // Initial fetch
    if (document.getElementById('cpu-usage')) {
        fetchSystemInfo();
        // Update every 5 seconds
        updateInterval = setInterval(fetchSystemInfo, 5000);
    } else {
        console.log('Not on dashboard, skipping system info polling.');
    }

    console.log('Dashboard initialized.');
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (updateInterval) {
        clearInterval(updateInterval);
    }
});

// Start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
