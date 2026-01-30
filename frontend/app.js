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
        showError('System connection interrupted. Check backend status.');

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

// Show error message
function showError(message) {
    const existing = document.querySelector('.error-banner');
    if (existing) return;

    const errorDiv = document.createElement('div');
    errorDiv.className = 'error-banner';
    errorDiv.style.cssText = 'background: #f85149; color: white; padding: 10px; text-align: center; font-size: 0.875rem;';
    errorDiv.textContent = message;

    document.body.prepend(errorDiv);
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
    fetchSystemInfo();

    // Update every 5 seconds
    updateInterval = setInterval(fetchSystemInfo, 5000);

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
