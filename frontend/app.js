// AlvaOS Web UI - System Info Dashboard
const API_BASE = '/api/v1';
let updateInterval;
let alertsInterval;

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function updateClock() {
    const clockElement = document.getElementById('clock');
    if (!clockElement) return;

    const now = new Date();
    clockElement.textContent = now.toLocaleTimeString();
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function setWidth(id, value) {
    const el = document.getElementById(id);
    if (el) el.style.width = value;
}

function setStatusDot(level) {
    const dot = document.querySelector('.status-dot');
    if (!dot) return;

    if (level === 'critical') {
        dot.className = 'status-dot critical';
        return;
    }
    if (level === 'warning') {
        dot.className = 'status-dot warning';
        return;
    }
    dot.className = 'status-dot online';
}

function safeRoute(route) {
    const value = String(route || '').trim();
    if (/^[a-z0-9_-]+\.html$/i.test(value)) return value;
    return '';
}

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
            return null;
        }

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        updateDashboard(data);
        return data;
    } catch (error) {
        console.error('Error fetching system info:', error);
        handleConnectionError();
        setStatusDot('critical');
        return null;
    }
}

function updateDashboard(data) {
    if (!data || typeof data !== 'object') return;

    const cpu = data.cpu || {};
    const memory = data.memory || {};
    const disk = data.disk || {};

    const cpuUsage = Number(cpu.usage_percent) || 0;
    const cpuFrequencyMhz = Number(cpu.frequency_mhz) || 0;

    setText('cpu-cores', `${cpu.cores ?? '-'} / ${cpu.threads ?? '-'}`);
    setText('cpu-usage', `${cpuUsage.toFixed(1)}%`);
    setText('cpu-freq', `${cpuFrequencyMhz.toFixed(0)} MHz`);

    const cpuModelEl = document.getElementById('cpu-model');
    if (cpuModelEl) {
        cpuModelEl.textContent = cpu.model || 'Unknown CPU';
    }

    const cpuTempEl = document.getElementById('cpu-temp');
    if (cpuTempEl) {
        cpuTempEl.textContent = (cpu.temperature_c || cpu.temperature_c === 0)
            ? `${Number(cpu.temperature_c).toFixed(1)} degC`
            : 'N/A';
    }

    setWidth('cpu-progress', `${Math.max(0, Math.min(100, cpuUsage))}%`);

    const memPercent = Number(memory.percent) || 0;
    setText('mem-total', `${Number(memory.total_gb || 0).toFixed(2)} GB`);
    setText('mem-used', `${Number(memory.used_gb || 0).toFixed(2)} GB`);
    setText('mem-available', `${Number(memory.available_gb || 0).toFixed(2)} GB`);
    setText('mem-usage-percent', `${memPercent.toFixed(1)}%`);
    setWidth('mem-progress', `${Math.max(0, Math.min(100, memPercent))}%`);

    const allPools = Array.isArray(data.storage_pools) ? data.storage_pools : [];
    const mountedPools = allPools.filter((pool) =>
        pool?.mounted
        && typeof pool.total_gb === 'number'
        && typeof pool.used_gb === 'number'
        && typeof pool.free_gb === 'number'
    );

    if (mountedPools.length > 0) {
        const poolTotal = mountedPools.reduce((sum, pool) => sum + pool.total_gb, 0);
        const poolUsed = mountedPools.reduce((sum, pool) => sum + pool.used_gb, 0);
        const poolFree = mountedPools.reduce((sum, pool) => sum + pool.free_gb, 0);
        const poolPercent = poolTotal > 0 ? (poolUsed / poolTotal) * 100 : 0;

        setText('disk-total', `${poolTotal.toFixed(2)} GB`);
        setText('disk-used', `${poolUsed.toFixed(2)} GB`);
        setText('disk-free', `${poolFree.toFixed(2)} GB`);
        setText('disk-usage-percent', `${poolPercent.toFixed(1)}%`);
        setWidth('disk-progress', `${Math.max(0, Math.min(100, poolPercent))}%`);
    } else {
        const diskPercent = Number(disk.percent) || 0;
        setText('disk-total', `${Number(disk.total_gb || 0).toFixed(2)} GB`);
        setText('disk-used', `${Number(disk.used_gb || 0).toFixed(2)} GB`);
        setText('disk-free', `${Number(disk.free_gb || 0).toFixed(2)} GB`);
        setText('disk-usage-percent', `${diskPercent.toFixed(1)}%`);
        setWidth('disk-progress', `${Math.max(0, Math.min(100, diskPercent))}%`);
    }

    const poolStorageListEl = document.getElementById('pool-storage-list');
    if (poolStorageListEl) {
        if (allPools.length === 0) {
            poolStorageListEl.innerHTML = `
                <div style="background:var(--bg-body); padding:8px; border-radius:4px; border:1px solid var(--border-default); font-size:0.8rem; color:var(--text-secondary);">
                    No pools configured
                </div>
            `;
        } else {
            poolStorageListEl.innerHTML = allPools.map((pool) => {
                if (!pool.mounted || typeof pool.percent !== 'number') {
                    return `
                        <div style="background:var(--bg-body); padding:8px; border-radius:4px; border:1px solid var(--border-default);">
                            <div style="display:flex; justify-content:space-between; font-size:0.8rem;">
                                <span>${escapeHtml(pool.name)}</span>
                                <span style="color:var(--text-secondary);">Not mounted</span>
                            </div>
                            <div class="mono-text" style="font-size:0.75rem; color:var(--text-secondary); margin-top:4px;">
                                ${escapeHtml(pool.mount_point || '')}
                            </div>
                        </div>
                    `;
                }

                return `
                    <div style="background:var(--bg-body); padding:8px; border-radius:4px; border:1px solid var(--border-default);">
                        <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
                            <span style="font-size:0.8rem;">${escapeHtml(pool.name)}</span>
                            <span class="mono-text" style="font-size:0.8rem;">${Number(pool.percent || 0).toFixed(1)}%</span>
                        </div>
                        <div class="progress-track" style="margin-top:8px; height:5px;">
                            <div class="progress-bar bar-disk" style="width:${Number(pool.percent || 0)}%"></div>
                        </div>
                        <div class="mono-text" style="font-size:0.75rem; color:var(--text-secondary); margin-top:6px;">
                            ${Number(pool.used_gb || 0).toFixed(2)} GB / ${Number(pool.total_gb || 0).toFixed(2)} GB
                        </div>
                    </div>
                `;
            }).join('');
        }
    }

    const network = data.network || {};
    const system = data.system || {};

    setText('hostname', network.hostname || '-');
    setText('ip-address', network.ip_address || '-');
    setText('os-version', `${system.os || '-'} ${system.os_version || ''}`.trim());
    setText('uptime', `${Number(system.uptime_hours || 0).toFixed(1)} hours`);
    setText('last-update', new Date().toLocaleTimeString());

    const errorDiv = document.querySelector('.error-banner');
    if (errorDiv) {
        errorDiv.remove();
    }
}

function updateAlertSummary(summary) {
    const critical = Number(summary?.critical) || 0;
    const warning = Number(summary?.warning) || 0;

    setText('alert-count-critical', String(critical));
    setText('alert-count-warning', String(warning));

    const statusEl = document.getElementById('health-overall-status');
    if (statusEl) {
        if (critical > 0) {
            statusEl.className = 'health-status-pill critical';
            statusEl.textContent = 'Critical';
        } else if (warning > 0) {
            statusEl.className = 'health-status-pill warning';
            statusEl.textContent = 'Warning';
        } else {
            statusEl.className = 'health-status-pill healthy';
            statusEl.textContent = 'Healthy';
        }
    }

    if (critical > 0) {
        setStatusDot('critical');
    } else if (warning > 0) {
        setStatusDot('warning');
    } else {
        setStatusDot('online');
    }
}

function renderAlerts(payload) {
    const listEl = document.getElementById('dashboard-alert-list');
    if (!listEl) return;

    const alerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
    if (alerts.length === 0) {
        listEl.innerHTML = '<div class="alerts-empty">No active alerts. Your system looks healthy.</div>';
        updateAlertSummary(payload?.summary || { critical: 0, warning: 0 });
        setText('alerts-last-scan', new Date().toLocaleTimeString());
        return;
    }

    const html = alerts.slice(0, 12).map((alert) => {
        const severity = String(alert?.severity || 'info').toLowerCase();
        const route = safeRoute(alert?.route);
        const clickable = Boolean(route);
        const tag = clickable ? 'a' : 'div';
        const actionLabel = clickable
            ? escapeHtml(String(alert?.action_label || 'Open'))
            : '';

        return `
            <${tag} class="alert-row ${escapeHtml(severity)} ${clickable ? 'clickable' : ''}" ${clickable ? `href="${escapeHtml(route)}"` : ''}>
                <div class="alert-severity-chip">${escapeHtml(severity)}</div>
                <div class="alert-content">
                    <div class="alert-title">${escapeHtml(alert?.title || 'Alert')}</div>
                    <div class="alert-message">${escapeHtml(alert?.message || '')}</div>
                </div>
                ${clickable ? `<div class="alert-action">${actionLabel}</div>` : ''}
            </${tag}>
        `;
    }).join('');

    listEl.innerHTML = html;
    updateAlertSummary(payload?.summary || {});

    const scanTime = payload?.generated_at ? new Date(payload.generated_at) : new Date();
    setText('alerts-last-scan', scanTime.toLocaleTimeString());
}

async function fetchAlerts() {
    const listEl = document.getElementById('dashboard-alert-list');
    if (!listEl) return;

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/alerts`, {
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
        renderAlerts(data);
    } catch (error) {
        console.warn('Error fetching alerts:', error);
        setText('alerts-last-scan', 'Failed');
    }
}

async function refreshDashboardData() {
    await fetchSystemInfo();
    await fetchAlerts();
}

// Show error message (using Toast or overlay for critical)
function showError(message) {
    if (window.showToast) {
        window.showToast(message, 'error');
    } else {
        console.error(message);
    }
}

let isReconnecting = false;

function handleConnectionError() {
    if (isReconnecting) return;
    isReconnecting = true;

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
        <div style="font-size: 3rem; margin-bottom: 1rem; animation: spin 1s linear infinite;">&#8635;</div>
        <h2 style="margin-bottom: 0.5rem;">Connection Lost</h2>
        <p style="color: var(--text-secondary);">Waiting for AlvaOS to come back online...</p>
        <style>@keyframes spin { 100% { transform: rotate(360deg); } }</style>
    `;
    document.body.appendChild(overlay);

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
                    document.getElementById('reconnect-overlay')?.remove();

                    if (res.status === 401) {
                        window.location.href = '/login.html';
                    } else {
                        if (window.showToast) window.showToast('We are back online!', 'success');
                        if (document.getElementById('cpu-usage')) refreshDashboardData();
                    }
                }
            } catch (e) {
                // Still down, keep waiting
            }
        }, 3000);
    }
}

async function init() {
    console.log('AlvaOS Dashboard initializing...');

    try {
        const response = await fetch(`${API_BASE}/setup/status`);
        if (response.ok) {
            const data = await response.json();
            if (!data.setup_complete) {
                window.location.href = '/setup.html';
                return;
            }

            const token = localStorage.getItem('alvaos_token');
            if (!token) {
                window.location.href = '/login.html';
                return;
            }
        }
    } catch (error) {
        console.error('Failed to check setup status:', error);
    }

    updateClock();
    setInterval(updateClock, 1000);

    if (window.triggerUpdateCheck) {
        window.triggerUpdateCheck();
    }

    if (document.getElementById('cpu-usage')) {
        await refreshDashboardData();
        updateInterval = setInterval(fetchSystemInfo, 5000);
        alertsInterval = setInterval(fetchAlerts, 15000);
    } else {
        console.log('Not on dashboard, skipping system info polling.');
    }

    console.log('Dashboard initialized.');
}

window.addEventListener('beforeunload', () => {
    if (updateInterval) {
        clearInterval(updateInterval);
    }
    if (alertsInterval) {
        clearInterval(alertsInterval);
    }
});

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
