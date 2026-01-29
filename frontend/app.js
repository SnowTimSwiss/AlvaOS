// AlvaOS Web UI - System Info Dashboard
const API_BASE = '/api/v1';
let updateInterval;

// Fetch system information
async function fetchSystemInfo() {
    try {
        const response = await fetch(`${API_BASE}/system/info`);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        updateDashboard(data);
    } catch (error) {
        console.error('Error fetching system info:', error);
        showError('Failed to fetch system information. Please check if the backend is running.');
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
    document.getElementById('mem-progress').style.width =
        `${data.memory.percent}%`;

    // Disk Information
    document.getElementById('disk-total').textContent =
        `${data.disk.total_gb} GB`;
    document.getElementById('disk-used').textContent =
        `${data.disk.used_gb} GB`;
    document.getElementById('disk-free').textContent =
        `${data.disk.free_gb} GB`;
    document.getElementById('disk-progress').style.width =
        `${data.disk.percent}%`;

    // System Information
    document.getElementById('hostname').textContent = data.network.hostname;
    document.getElementById('ip-address').textContent = data.network.ip_address;
    document.getElementById('os-version').textContent =
        `${data.system.os} ${data.system.os_version}`;
    document.getElementById('uptime').textContent =
        `${data.system.uptime_hours} hours`;

    // Update timestamp
    const now = new Date();
    document.getElementById('last-update').textContent =
        now.toLocaleTimeString();

    // Remove error if present
    const errorDiv = document.querySelector('.error');
    if (errorDiv) {
        errorDiv.remove();
    }
}

// Show error message
function showError(message) {
    const existing = document.querySelector('.error');
    if (existing) return;

    const errorDiv = document.createElement('div');
    errorDiv.className = 'error';
    errorDiv.textContent = message;

    const container = document.querySelector('.container');
    container.insertBefore(errorDiv, container.firstChild);
}

// Initialize dashboard
function init() {
    console.log('AlvaOS Dashboard initializing...');

    // Initial fetch
    fetchSystemInfo();

    // Update every 5 seconds
    updateInterval = setInterval(fetchSystemInfo, 5000);

    console.log('Dashboard initialized. Updating every 5 seconds.');
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
