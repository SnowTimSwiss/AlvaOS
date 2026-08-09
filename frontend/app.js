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
let csrfToken = localStorage.getItem('alvaos_csrf_token') || null;
let csrfTokenPromise = null;

function isApiRequest(resource) {
    const url = typeof resource === 'string' ? resource : resource?.url;
    if (!url) return false;
    try {
        const parsed = new URL(url, window.location.origin);
        return parsed.origin === window.location.origin && parsed.pathname.startsWith('/api/v1/');
    } catch {
        return false;
    }
}

function getRequestMethod(resource, options = {}) {
    return String(options.method || resource?.method || 'GET').toUpperCase();
}

function isStateChangingMethod(method) {
    return ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);
}

function headersToObject(headers) {
    const result = {};
    if (!headers) return result;

    if (headers instanceof Headers) {
        headers.forEach((value, key) => {
            result[key] = value;
        });
        return result;
    }

    if (Array.isArray(headers)) {
        headers.forEach(([key, value]) => {
            result[key] = value;
        });
        return result;
    }

    return { ...headers };
}

function storeCsrfToken(token) {
    csrfToken = token || null;
    if (csrfToken) {
        localStorage.setItem('alvaos_csrf_token', csrfToken);
    } else {
        localStorage.removeItem('alvaos_csrf_token');
    }
}

// Page Loading Overlay
function showPageLoading() {
    const overlay = document.getElementById('page-loading-overlay');
    if (overlay) {
        overlay.style.display = 'flex';
    }
}

function hidePageLoading() {
    const overlay = document.getElementById('page-loading-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

// Add loading state to navigation links
document.addEventListener('DOMContentLoaded', function() {
    const navLinks = document.querySelectorAll('.nav-link');
    navLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            const href = this.getAttribute('href');
            if (href && !href.startsWith('#') && href !== window.location.pathname) {
                showPageLoading();
            }
        });
    });
    
    // Hide loading overlay when page is fully loaded
    window.addEventListener('load', hidePageLoading);
});

// Fetch CSRF token after login
async function fetchCsrfToken() {
    if (csrfToken) return csrfToken;
    if (csrfTokenPromise) return csrfTokenPromise;

    csrfTokenPromise = (async () => {
        const token = localStorage.getItem('alvaos_token');
        if (!token) return null;

        const response = await window.__alvaosNativeFetch(`${API_BASE}/auth/csrf-token`, {
            headers: { 'Authorization': token }
        });
        if (!response.ok) return null;

        const data = await response.json();
        storeCsrfToken(data.csrf_token || null);
        return csrfToken;
    })();

    try {
        return await csrfTokenPromise;
    } catch (error) {
        console.error('Failed to fetch CSRF token:', error);
        return null;
    } finally {
        csrfTokenPromise = null;
    }
}

// Get headers for API requests, including CSRF token for state-changing operations
function getHeaders(includeCsrf = false) {
    const token = localStorage.getItem('alvaos_token');
    const headers = {
        'Content-Type': 'application/json'
    };
    if (token) {
        headers['Authorization'] = token;
    }
    if (includeCsrf && csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
    }
    return headers;
}

function installAuthenticatedFetch() {
    if (window.__alvaosNativeFetch) return;

    window.__alvaosNativeFetch = window.fetch.bind(window);
    window.fetch = async function alvaosFetch(resource, options = {}) {
        if (!isApiRequest(resource)) {
            return window.__alvaosNativeFetch(resource, options);
        }

        const token = localStorage.getItem('alvaos_token') || '';
        const method = getRequestMethod(resource, options);
        const headers = headersToObject(options.headers || resource?.headers);

        if (token && !headers.Authorization && !headers.authorization) {
            headers.Authorization = token;
        }

        if (token && isStateChangingMethod(method) && !headers['X-CSRF-Token'] && !headers['x-csrf-token']) {
            const freshToken = await fetchCsrfToken();
            if (freshToken) headers['X-CSRF-Token'] = freshToken;
        }

        return window.__alvaosNativeFetch(resource, { ...options, headers });
    };
}

window.alvaosStoreCsrfToken = storeCsrfToken;
window.alvaosFetchCsrfToken = fetchCsrfToken;
window.alvaosGetHeaders = getHeaders;
installAuthenticatedFetch();

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Escape a value for use inside a quoted JS string in an inline handler, e.g.
// onclick="doThing('${jsArg(name)}')". The browser HTML-decodes the attribute
// first and only then parses it as JS, so the value has to survive both: escape
// for JS, then for HTML. Without this a share or disk name containing a quote
// breaks the handler outright.
function escapeJsString(value) {
    return String(value ?? '')
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/"/g, '\\"')
        .replace(/\n/g, '\\n')
        .replace(/\r/g, '\\r')
        .replace(/\u2028/g, '\\u2028')
        .replace(/\u2029/g, '\\u2029');
}

function jsArg(value) {
    return escapeHtml(escapeJsString(value));
}

window.escapeHtml = escapeHtml;
window.escapeJsString = escapeJsString;
window.jsArg = jsArg;

// Ends the session on the server too. Sessions outlive a backend restart, so
// dropping the local token alone would leave it usable until it expires.
async function alvaosLogout() {
    const token = localStorage.getItem('alvaos_token');
    if (token) {
        try {
            await window.__alvaosNativeFetch(`${API_BASE}/auth/logout`, {
                method: 'POST',
                headers: {
                    'Authorization': token,
                    'X-CSRF-Token': (await fetchCsrfToken()) || ''
                }
            });
        } catch (error) {
            // Even if the call fails, clear locally and send the user to login.
            console.error('Logout request failed:', error);
        }
    }
    localStorage.removeItem('alvaos_token');
    localStorage.removeItem('alvaos_csrf_token');
    csrfToken = null;
    window.location.href = 'login.html';
}

window.alvaosLogout = alvaosLogout;

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

function formatUptime(hours) {
    const totalHours = Math.max(0, Number(hours) || 0);
    const days = Math.floor(totalHours / 24);
    const remHours = Math.floor(totalHours % 24);
    if (days > 0) return `${days} day${days === 1 ? '' : 's'}, ${remHours} hour${remHours === 1 ? '' : 's'}`;
    if (remHours > 0) return `${remHours} hour${remHours === 1 ? '' : 's'}`;
    return 'less than an hour';
}

function formatStorageSize(gb) {
    const value = Number(gb) || 0;
    if (value >= 1024) return `${(value / 1024).toFixed(2)} TB`;
    return `${value.toFixed(2)} GB`;
}

function setPill(id, state, text) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = `pill ${state}`;
    el.textContent = text;
}

function setCardAttention(id, isAttention) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('attention', !!isAttention);
}

const focusAttention = { storage: false, backup: false, alerts: false, updates: false, apps: false };

function renderHero() {
    const titleEl = document.getElementById('dashboard-hero-title');
    if (!titleEl) return;

    const subEl = document.getElementById('dashboard-hero-sub');
    const iconWrap = document.getElementById('dashboard-hero-icon');
    const count = Object.values(focusAttention).filter(Boolean).length;

    if (count === 0) {
        titleEl.textContent = 'Everything looks healthy';
        if (iconWrap) {
            iconWrap.classList.remove('attention');
            iconWrap.innerHTML = window.alvaIcon ? window.alvaIcon('circle-check-big', '', 'aria-hidden="true"') : '';
        }
    } else {
        titleEl.textContent = `${count} thing${count === 1 ? '' : 's'} want${count === 1 ? 's' : ''} your attention`;
        if (iconWrap) {
            iconWrap.classList.add('attention');
            iconWrap.innerHTML = window.alvaIcon ? window.alvaIcon('triangle-alert', '', 'aria-hidden="true"') : '';
        }
    }
    if (window.renderAlvaIcons && iconWrap) window.renderAlvaIcons(iconWrap);

    if (subEl) {
        const hostname = document.getElementById('hostname')?.textContent || 'AlvaOS';
        const uptime = document.getElementById('uptime')?.textContent || '-';
        subEl.textContent = count === 0
            ? `${hostname} • up ${uptime} • everything is healthy`
            : `${hostname} • up ${uptime} • everything else is healthy`;
    }
}

function setFocusAttention(key, isAttention) {
    focusAttention[key] = !!isAttention;
    renderHero();
}

window.alvaosSetFocusAttention = setFocusAttention;

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
        suffix: ' °C',
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

        let data;
        try {
            data = await response.json();
        } catch (jsonError) {
            console.error('Failed to parse JSON response:', jsonError);
            throw new Error('Invalid response format from server');
        }
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
            ? `${cpuTemp.toFixed(1)} °C`
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
        historyDiskPercent = poolTotal > 0 ? (poolUsed / poolTotal) * 100 : 0;
    }

    renderStorageFocus(allPools, mountedPools);

    const network = data.network || {};
    const system = data.system || {};

    setText('hostname', network.hostname || '-');
    setText('ip-address', network.ip_address || '-');
    setText('os-version', `${system.os || '-'} ${system.os_version || ''}`.trim());
    setText('uptime', formatUptime(system.uptime_hours));
    setText('last-update', new Date().toLocaleTimeString());
    renderHero();

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

function renderStorageFocus(allPools, mountedPools) {
    const poolsEl = document.getElementById('dashboard-storage-pools');
    const valueEl = document.getElementById('dashboard-storage-value');
    const subEl = document.getElementById('dashboard-storage-sub');
    if (!poolsEl && !valueEl && !subEl) return;

    if (allPools.length === 0) {
        if (valueEl) valueEl.textContent = 'No storage pools yet';
        if (subEl) subEl.textContent = 'Create a pool to start using storage.';
        if (poolsEl) poolsEl.innerHTML = '';
        setPill('dashboard-storage-pill', 'warn', 'No pools');
        setCardAttention('dashboard-storage-card', true);
        setFocusAttention('storage', true);
        return;
    }

    const poolTotal = mountedPools.reduce((sum, pool) => sum + pool.total_gb, 0);
    const poolFree = mountedPools.reduce((sum, pool) => sum + pool.free_gb, 0);
    const hasUnmounted = allPools.some((pool) => !pool.mounted);
    const maxPercent = mountedPools.reduce((max, pool) => Math.max(max, Number(pool.percent) || 0), 0);

    let state = 'ok';
    let pillText = 'Healthy';
    if (hasUnmounted) {
        state = 'warn';
        pillText = 'Pool issue';
    } else if (maxPercent >= 90) {
        state = 'bad';
        pillText = 'Nearly full';
    } else if (maxPercent >= 75) {
        state = 'warn';
        pillText = 'Getting full';
    }

    if (valueEl) {
        valueEl.textContent = mountedPools.length > 0
            ? `${formatStorageSize(poolFree)} free of ${formatStorageSize(poolTotal)}`
            : 'No mounted pools';
    }
    if (subEl) {
        subEl.textContent = `${mountedPools.length} of ${allPools.length} pool${allPools.length === 1 ? '' : 's'} online`;
    }

    if (poolsEl) {
        poolsEl.innerHTML = allPools.map((pool) => {
            const name = escapeHtml(pool.name || 'pool');
            if (!pool.mounted || typeof pool.percent !== 'number') {
                return `
                    <div class="pool-item">
                        <div class="pool-item-top"><span class="name">${name}</span><span class="pct">not mounted</span></div>
                        <div class="pool-bar"><span class="warn" style="width:100%"></span></div>
                    </div>
                `;
            }

            const percent = Math.max(0, Math.min(100, Number(pool.percent) || 0));
            const barState = percent >= 90 ? 'bad' : (percent >= 75 ? 'warn' : '');
            return `
                <div class="pool-item">
                    <div class="pool-item-top"><span class="name">${name}</span><span class="pct">${percent.toFixed(0)}%</span></div>
                    <div class="pool-bar"><span class="${barState}" style="width:${percent}%"></span></div>
                </div>
            `;
        }).join('');
    }

    setPill('dashboard-storage-pill', state, pillText);
    setCardAttention('dashboard-storage-card', state !== 'ok');
    setFocusAttention('storage', state !== 'ok');
}

function updateAlertSummary(summary) {
    const critical = Number(summary?.critical) || 0;
    const warning = Number(summary?.warning) || 0;

    if (critical > 0) {
        setStatusDot('critical');
    } else if (warning > 0) {
        setStatusDot('warning');
    } else {
        setStatusDot('online');
    }

    return { critical, warning };
}

function renderAlerts(payload) {
    const card = document.getElementById('dashboard-alerts-card');
    if (!card) return;

    const alerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
    const { critical, warning } = updateAlertSummary(payload?.summary || { critical: 0, warning: 0 });
    const scanTime = (payload?.generated_at ? new Date(payload.generated_at) : new Date()).toLocaleTimeString();

    const valueEl = document.getElementById('dashboard-alerts-value');
    const subEl = document.getElementById('dashboard-alerts-sub');
    const footEl = document.getElementById('dashboard-alerts-foot');
    const linkEl = document.getElementById('dashboard-alerts-link');

    if (alerts.length === 0) {
        if (valueEl) valueEl.textContent = 'No active alerts';
        if (subEl) subEl.textContent = `Last scan ${scanTime} • 0 critical, 0 warnings`;
        setPill('dashboard-alerts-pill', 'ok', 'All clear');
        if (footEl) footEl.style.display = 'none';
        setCardAttention('dashboard-alerts-card', false);
        setFocusAttention('alerts', false);
        return;
    }

    const top = alerts[0] || {};
    if (valueEl) valueEl.textContent = String(top.title || 'Active alert');
    if (subEl) {
        subEl.textContent = `${alerts.length} alert${alerts.length === 1 ? '' : 's'} • last scan ${scanTime} • ${critical} critical, ${warning} warning`;
    }

    setPill('dashboard-alerts-pill', critical > 0 ? 'bad' : 'warn', `${alerts.length} alert${alerts.length === 1 ? '' : 's'}`);
    setCardAttention('dashboard-alerts-card', true);
    setFocusAttention('alerts', true);

    const route = safeRoute(top.route);
    if (footEl && linkEl) {
        if (route) {
            linkEl.setAttribute('href', route);
            footEl.style.display = 'flex';
        } else {
            footEl.style.display = 'none';
        }
    }
}

async function fetchAlerts() {
    const card = document.getElementById('dashboard-alerts-card');
    if (!card) return;

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

        let data;
        try {
            data = await response.json();
        } catch (jsonError) {
            console.error('Failed to parse JSON response for alerts:', jsonError);
            throw new Error('Invalid response format from server');
        }
        renderAlerts(data);
    } catch (error) {
        console.warn('Error fetching alerts:', error);
    }
}

async function refreshDashboardData() {
    await runSystemFetch();
    await runAlertsFetch();
}

async function fetchDashboardJson(path) {
    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}${path}`, {
            headers: { Authorization: token || '' }
        });
        if (!response.ok) return null;
        return await response.json();
    } catch (_error) {
        return null;
    }
}

function renderBackupFocus(statusPayload, buddyPayload) {
    const card = document.getElementById('dashboard-backup-card');
    if (!card) return;

    const valueEl = document.getElementById('dashboard-backup-value');
    const subEl = document.getElementById('dashboard-backup-sub');
    const status = statusPayload?.status || {};
    const peers = Array.isArray(buddyPayload?.peers) ? buddyPayload.peers : [];

    const lastRunAt = status.pool_last_run_at;
    const nextRunAt = status.pool_next_run_at;
    const hasError = !!status.pool_last_error;

    let state = 'ok';
    let pillText = 'Up to date';
    if (hasError) {
        state = 'bad';
        pillText = 'Backup failed';
    } else if (!lastRunAt) {
        state = 'warn';
        pillText = 'Not set up';
    }

    if (valueEl) {
        valueEl.textContent = lastRunAt
            ? `Last backup ${new Date(lastRunAt).toLocaleString()}`
            : 'No backup has run yet';
    }
    if (subEl) {
        const nextRunText = nextRunAt ? `next run ${new Date(nextRunAt).toLocaleString()}` : 'no schedule set';
        const peerText = peers.length > 0
            ? `${peers.length} buddy peer${peers.length === 1 ? '' : 's'} paired`
            : 'no buddy paired yet';
        subEl.textContent = `${nextRunText} • ${peerText}`;
    }

    setPill('dashboard-backup-pill', state, pillText);
    setCardAttention('dashboard-backup-card', state !== 'ok');
    setFocusAttention('backup', state !== 'ok');
}

function renderAppsFocus(apps, containers) {
    const card = document.getElementById('dashboard-apps-card');
    if (!card) return;

    const valueEl = document.getElementById('dashboard-apps-value');
    const subEl = document.getElementById('dashboard-apps-sub');
    const linkEl = document.getElementById('dashboard-apps-link');
    const totalApps = Array.isArray(apps) ? apps.length : 0;

    if (totalApps === 0) {
        if (valueEl) valueEl.textContent = 'No apps installed yet';
        if (subEl) subEl.textContent = 'Browse the app store to get started.';
        if (linkEl) linkEl.textContent = 'Open app store →';
        setPill('dashboard-apps-pill', 'ok', 'None installed');
        setCardAttention('dashboard-apps-card', false);
        setFocusAttention('apps', false);
        return;
    }

    const containerList = Array.isArray(containers) ? containers : [];
    const runningCount = containerList.filter((c) => c?.State === 'running').length;
    const totalContainers = containerList.length;
    const allRunning = totalContainers > 0 && runningCount === totalContainers;

    if (valueEl) valueEl.textContent = `${totalApps} app${totalApps === 1 ? '' : 's'} installed`;
    if (subEl) subEl.textContent = `${runningCount} of ${totalContainers} container${totalContainers === 1 ? '' : 's'} running`;
    if (linkEl) linkEl.textContent = 'Manage apps →';

    const state = allRunning ? 'ok' : 'warn';
    setPill('dashboard-apps-pill', state, allRunning ? 'All running' : `${totalContainers - runningCount} stopped`);
    setCardAttention('dashboard-apps-card', !allRunning);
    setFocusAttention('apps', !allRunning);
}

async function loadDashboardExtras() {
    if (!document.getElementById('dashboard-backup-card') && !document.getElementById('dashboard-apps-card')) return;

    const [statusPayload, buddyPayload, appsPayload, containersPayload] = await Promise.all([
        fetchDashboardJson('/backup/status'),
        fetchDashboardJson('/backup/pairing/status'),
        fetchDashboardJson('/apps/installed'),
        fetchDashboardJson('/containers')
    ]);

    renderBackupFocus(statusPayload, buddyPayload);
    renderAppsFocus(appsPayload?.apps, containersPayload?.containers);
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
        loadDashboardExtras();
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

// ── Tab accessibility ────────────────────────────────────────────────────────
// Each page wires its own tab switching. Rather than change five different
// implementations, this decorates whatever is on the page with the ARIA tab
// pattern and adds arrow-key navigation, delegating the actual switch back to
// the page's existing click handler.
function enhanceTabBars() {
    document.querySelectorAll('.tab-bar').forEach((bar) => {
        const tabs = Array.from(bar.querySelectorAll('.tab-btn'));
        if (tabs.length === 0) return;

        bar.setAttribute('role', 'tablist');

        const syncState = () => {
            tabs.forEach((tab) => {
                const isActive = tab.classList.contains('active');
                tab.setAttribute('role', 'tab');
                tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
                // Roving tabindex: one stop for the whole tablist.
                tab.tabIndex = isActive ? 0 : -1;

                const name = tab.getAttribute('data-tab');
                const panel = name && document.getElementById(`tab-${name}`);
                if (panel) {
                    tab.setAttribute('aria-controls', panel.id);
                    panel.setAttribute('role', 'tabpanel');
                    if (!panel.getAttribute('aria-label') && tab.textContent.trim()) {
                        panel.setAttribute('aria-label', tab.textContent.trim());
                    }
                }
            });
        };

        syncState();
        // The page toggles .active on click; re-sync right after it does.
        bar.addEventListener('click', () => setTimeout(syncState, 0));

        bar.addEventListener('keydown', (event) => {
            const currentIndex = tabs.indexOf(document.activeElement);
            if (currentIndex === -1) return;

            let nextIndex = null;
            if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
            else if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
            else if (event.key === 'Home') nextIndex = 0;
            else if (event.key === 'End') nextIndex = tabs.length - 1;
            if (nextIndex === null) return;

            event.preventDefault();
            tabs[nextIndex].focus();
            tabs[nextIndex].click();
        });
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', enhanceTabBars);
} else {
    enhanceTabBars();
}

// ── Calm failure states ──────────────────────────────────────────────────────
// A section that could not load is not an emergency and must not be painted in
// the colour reserved for "you have to act now". It says what did not work, what
// it means, and offers a way forward - per the No Fear UX rules in DESIGN.md.
function renderLoadFailure(container, options = {}) {
    if (!container) return;
    const title = options.title || 'Could not load this section';
    const detail = options.detail
        || 'The NAS did not answer. It may still be starting up after an update.';
    const retryId = `retry-${Math.random().toString(36).slice(2, 9)}`;

    container.innerHTML = `
        <div class="load-failure" style="grid-column: 1/-1;">
            <div class="load-failure-title">${escapeHtml(title)}</div>
            <p class="load-failure-detail">${escapeHtml(detail)}</p>
            ${options.onRetry ? `<button class="btn-secondary" id="${retryId}">Try again</button>` : ''}
        </div>
    `;

    if (options.onRetry) {
        const button = document.getElementById(retryId);
        if (button) {
            button.addEventListener('click', () => {
                button.disabled = true;
                button.textContent = 'Retrying...';
                Promise.resolve(options.onRetry()).catch(() => {
                    button.disabled = false;
                    button.textContent = 'Try again';
                });
            });
        }
    }
}

window.renderLoadFailure = renderLoadFailure;
