// AlvaOS Web UI - System Info Dashboard
const API_BASE = '/api/v1';
let updateInterval;
let alertsInterval;
let isSystemFetchInFlight = false;
let isAlertsFetchInFlight = false;
const SYSTEM_POLL_MS_ACTIVE = 5000;
const SYSTEM_POLL_MS_HIDDEN = 20000;
const ALERTS_POLL_MS_ACTIVE = 15000;
const ALERTS_POLL_MS_HIDDEN = 60000;
const HISTORY_CACHE_KEY = 'alvaos_dashboard_history_v1';
const HISTORY_MAX_SAMPLES = 120;
let usageHistory = [];

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

function toFiniteNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function getLastFinite(values) {
    for (let i = values.length - 1; i >= 0; i -= 1) {
        if (Number.isFinite(values[i])) return values[i];
    }
    return null;
}

function loadUsageHistory() {
    usageHistory = [];
    try {
        const raw = localStorage.getItem(HISTORY_CACHE_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return;

        const cutoff = Date.now() - (4 * 60 * 60 * 1000);
        const cleaned = parsed
            .map((sample) => ({
                ts: toFiniteNumber(sample?.ts) || Date.now(),
                cpu: toFiniteNumber(sample?.cpu),
                memory: toFiniteNumber(sample?.memory),
                disk: toFiniteNumber(sample?.disk),
                temp: toFiniteNumber(sample?.temp),
            }))
            .filter((sample) => sample.ts >= cutoff)
            .slice(-HISTORY_MAX_SAMPLES);

        usageHistory = cleaned;
    } catch (_error) {
        usageHistory = [];
    }
}

function saveUsageHistory() {
    try {
        localStorage.setItem(HISTORY_CACHE_KEY, JSON.stringify(usageHistory.slice(-HISTORY_MAX_SAMPLES)));
    } catch (_error) {
        // Ignore storage quota/runtime issues.
    }
}

function buildLinePath(values, minValue, maxValue) {
    if (!Array.isArray(values) || values.length < 2) return '';
    const points = values
        .map((value, idx) => ({ value, idx }))
        .filter((point) => Number.isFinite(point.value));
    if (points.length < 2) return '';

    const width = 100;
    const height = 30;
    const xDivisor = Math.max(values.length - 1, 1);
    const range = Math.max(maxValue - minValue, 0.0001);

    return points.map((point, pointIndex) => {
        const x = (point.idx / xDivisor) * width;
        const normalized = (point.value - minValue) / range;
        const y = height - (Math.max(0, Math.min(1, normalized)) * height);
        const cmd = pointIndex === 0 ? 'M' : 'L';
        return `${cmd}${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(' ');
}

function renderHistorySeries(pathId, valueId, values, options = {}) {
    const pathEl = document.getElementById(pathId);
    const valueEl = document.getElementById(valueId);
    if (!pathEl || !valueEl) return;

    const suffix = String(options.suffix || '');
    const decimals = Number.isFinite(options.decimals) ? options.decimals : 1;
    const latest = getLastFinite(values);
    valueEl.textContent = Number.isFinite(latest) ? `${latest.toFixed(decimals)}${suffix}` : 'N/A';

    let minValue = toFiniteNumber(options.min);
    let maxValue = toFiniteNumber(options.max);
    if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
        const finiteValues = values.filter((v) => Number.isFinite(v));
        if (finiteValues.length > 0) {
            minValue = Math.min(...finiteValues);
            maxValue = Math.max(...finiteValues);
            if (minValue === maxValue) {
                minValue -= 1;
                maxValue += 1;
            }
        }
    }

    if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
        pathEl.setAttribute('d', '');
        return;
    }

    pathEl.setAttribute('d', buildLinePath(values, minValue, maxValue));
}

function renderUsageHistory() {
    if (!document.getElementById('history-cpu-path')) return;

    const samples = usageHistory.slice(-HISTORY_MAX_SAMPLES);
    const cpuValues = samples.map((sample) => sample.cpu);
    const memValues = samples.map((sample) => sample.memory);
    const diskValues = samples.map((sample) => sample.disk);
    const tempValues = samples.map((sample) => sample.temp);

    renderHistorySeries('history-cpu-path', 'history-cpu-current', cpuValues, {
        min: 0,
        max: 100,
        suffix: '%',
        decimals: 1,
    });
    renderHistorySeries('history-mem-path', 'history-mem-current', memValues, {
        min: 0,
        max: 100,
        suffix: '%',
        decimals: 1,
    });
    renderHistorySeries('history-disk-path', 'history-disk-current', diskValues, {
        min: 0,
        max: 100,
        suffix: '%',
        decimals: 1,
    });

    const tempFinite = tempValues.filter((v) => Number.isFinite(v));
    const tempMin = tempFinite.length ? Math.max(20, Math.min(...tempFinite) - 3) : null;
    const tempMax = tempFinite.length ? Math.max(tempMin + 10, Math.max(...tempFinite) + 3) : null;
    renderHistorySeries('history-temp-path', 'history-temp-current', tempValues, {
        min: tempMin,
        max: tempMax,
        suffix: ' degC',
        decimals: 1,
    });

    const rangeEl = document.getElementById('history-range-label');
    if (!rangeEl) return;
    if (samples.length < 2) {
        rangeEl.textContent = 'Collecting samples...';
        return;
    }

    const minutes = Math.max(1, Math.round((samples[samples.length - 1].ts - samples[0].ts) / 60000));
    rangeEl.textContent = `Last ${minutes} minute${minutes === 1 ? '' : 's'}`;
}

function appendUsageSample(sample) {
    usageHistory.push(sample);
    if (usageHistory.length > HISTORY_MAX_SAMPLES) {
        usageHistory = usageHistory.slice(-HISTORY_MAX_SAMPLES);
    }
    saveUsageHistory();
    renderUsageHistory();
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
    const cpuTemp = toFiniteNumber(cpu.temperature_c);

    setText('cpu-cores', `${cpu.cores ?? '-'} / ${cpu.threads ?? '-'}`);
    setText('cpu-usage', `${cpuUsage.toFixed(1)}%`);
    setText('cpu-freq', `${cpuFrequencyMhz.toFixed(0)} MHz`);

    const cpuModelEl = document.getElementById('cpu-model');
    if (cpuModelEl) {
        cpuModelEl.textContent = cpu.model || 'Unknown CPU';
    }

    const cpuTempEl = document.getElementById('cpu-temp');
    if (cpuTempEl) {
        cpuTempEl.textContent = (cpuTemp || cpuTemp === 0)
            ? `${cpuTemp.toFixed(1)} degC`
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
    let historyDiskPercent = Number(disk.percent) || 0;

    if (mountedPools.length > 0) {
        const poolTotal = mountedPools.reduce((sum, pool) => sum + pool.total_gb, 0);
        const poolUsed = mountedPools.reduce((sum, pool) => sum + pool.used_gb, 0);
        const poolFree = mountedPools.reduce((sum, pool) => sum + pool.free_gb, 0);
        const poolPercent = poolTotal > 0 ? (poolUsed / poolTotal) * 100 : 0;
        historyDiskPercent = poolPercent;

        setText('disk-total', `${poolTotal.toFixed(2)} GB`);
        setText('disk-used', `${poolUsed.toFixed(2)} GB`);
        setText('disk-free', `${poolFree.toFixed(2)} GB`);
        setText('disk-usage-percent', `${poolPercent.toFixed(1)}%`);
        setWidth('disk-progress', `${Math.max(0, Math.min(100, poolPercent))}%`);
    } else {
        const diskPercent = Number(disk.percent) || 0;
        historyDiskPercent = diskPercent;
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

    appendUsageSample({
        ts: Date.now(),
        cpu: cpuUsage,
        memory: memPercent,
        disk: historyDiskPercent,
        temp: cpuTemp,
    });

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
    await runSystemFetch();
    await runAlertsFetch();
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

async function runSystemFetch() {
    if (isSystemFetchInFlight) return null;
    isSystemFetchInFlight = true;
    try {
        return await fetchSystemInfo();
    } finally {
        isSystemFetchInFlight = false;
    }
}

async function runAlertsFetch() {
    if (isAlertsFetchInFlight) return null;
    isAlertsFetchInFlight = true;
    try {
        return await fetchAlerts();
    } finally {
        isAlertsFetchInFlight = false;
    }
}

function getSystemPollDelay() {
    return document.visibilityState === 'hidden' ? SYSTEM_POLL_MS_HIDDEN : SYSTEM_POLL_MS_ACTIVE;
}

function getAlertsPollDelay() {
    return document.visibilityState === 'hidden' ? ALERTS_POLL_MS_HIDDEN : ALERTS_POLL_MS_ACTIVE;
}

function scheduleSystemPoll(delay = getSystemPollDelay()) {
    if (updateInterval) {
        clearTimeout(updateInterval);
    }
    updateInterval = setTimeout(async () => {
        await runSystemFetch();
        scheduleSystemPoll();
    }, delay);
}

function scheduleAlertsPoll(delay = getAlertsPollDelay()) {
    if (alertsInterval) {
        clearTimeout(alertsInterval);
    }
    alertsInterval = setTimeout(async () => {
        await runAlertsFetch();
        scheduleAlertsPoll();
    }, delay);
}

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
        <div style="font-size: 3rem; margin-bottom: 1rem; animation: spin 1s linear infinite;">${window.alvaIcon ? window.alvaIcon('refresh-cw', '', 'aria-hidden="true"') : '...'}</div>
        <h2 style="margin-bottom: 0.5rem;">Connection Lost</h2>
        <p style="color: var(--text-secondary);">Waiting for AlvaOS to come back online...</p>
        <style>@keyframes spin { 100% { transform: rotate(360deg); } }</style>
    `;
    document.body.appendChild(overlay);
    if (window.renderAlvaIcons) window.renderAlvaIcons(overlay);

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
        loadUsageHistory();
        renderUsageHistory();
        await refreshDashboardData();
        scheduleSystemPoll();
        scheduleAlertsPoll();
        document.addEventListener('visibilitychange', () => {
            scheduleSystemPoll(document.visibilityState === 'visible' ? 1000 : getSystemPollDelay());
            scheduleAlertsPoll(document.visibilityState === 'visible' ? 1200 : getAlertsPollDelay());
            if (document.visibilityState === 'visible') {
                runSystemFetch();
                runAlertsFetch();
            }
        });
    } else {
        console.log('Not on dashboard, skipping system info polling.');
    }

    console.log('Dashboard initialized.');
}

window.addEventListener('beforeunload', () => {
    if (updateInterval) {
        clearTimeout(updateInterval);
    }
    if (alertsInterval) {
        clearTimeout(alertsInterval);
    }
});

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
