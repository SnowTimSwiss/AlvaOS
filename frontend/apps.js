// AlvaOS Apps Management
// API_BASE is defined in app.js
let authToken = localStorage.getItem('alvaos_token');

async function apiFetch(url, options = {}) {
    const response = await window.fetch(url, options);
    if (response.status === 401) {
        localStorage.removeItem('alvaos_token');
        window.location.href = '/login.html';
    }
    return response;
}

let installedAppsCache = [];
let containersCache = [];
let selectedAppId = null;
const appDetailsCache = {};
const appUpdateStatusCache = {};
const appUpdateStatusPending = new Set();
const appTogglePending = new Set();
let activeLogsContainerId = null;
let activeLogsRequestId = 0;
let activeTerminalContainerId = null;
let containersLoaded = false;
let containersLoading = false;
let installedLoadRequestId = 0;
let availableAppsLoadPromise = null;
let availableAppsCache = [];
let availableSearchQuery = '';
let availableCategoryFilter = 'all';
let composePoolsLoadPromise = null;

function showNotification(message, type = 'info') {
    if (window.showToast) {
        window.showToast(message, type);
        return;
    }
    if (type === 'error') {
        console.error(message);
    } else {
        console.log(message);
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Strong random value for app secrets the user never types by hand (database
// passwords, signing keys). Uses the browser CSPRNG, not Math.random.
function generateSecret(length = 28) {
    const alphabet = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    const bytes = new Uint32Array(length);
    crypto.getRandomValues(bytes);
    let out = '';
    for (let i = 0; i < length; i++) {
        out += alphabet[bytes[i] % alphabet.length];
    }
    return out;
}

function setLoadingButtonState(button, isLoading, loadingLabel = 'Loading...') {
    if (!button) return;
    if (isLoading) {
        if (!button.dataset.defaultLabel) {
            button.dataset.defaultLabel = button.textContent.trim() || 'Refresh';
        }
        button.disabled = true;
        button.textContent = loadingLabel;
        return;
    }

    button.disabled = false;
    if (button.dataset.defaultLabel) {
        button.textContent = button.dataset.defaultLabel;
    }
}

function formatDate(value) {
    if (!value) return 'Unknown';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Unknown';
    return date.toLocaleString();
}

async function loadAppUpdateStatus(appId, forceRefresh = false) {
    if (!appId) return null;
    if (appUpdateStatusPending.has(appId) && !forceRefresh) {
        return appUpdateStatusCache[appId] || null;
    }

    appUpdateStatusPending.add(appId);
    if (!forceRefresh && !appUpdateStatusCache[appId]) {
        appUpdateStatusCache[appId] = { update_status: 'checking', update_available: null };
    }

    try {
        const url = new URL(`${API_BASE}/apps/${appId}/update-status`, window.location.origin);
        if (forceRefresh) url.searchParams.set('force_refresh', '1');

        const response = await apiFetch(url.toString(), {
            headers: { 'Authorization': authToken }
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload?.error || 'Failed to load app update status');
        }

        appUpdateStatusCache[appId] = payload || {};
        installedAppsCache = installedAppsCache.map((app) => (
            app.app_id === appId ? { ...app, ...payload } : app
        ));
        return payload;
    } catch (error) {
        appUpdateStatusCache[appId] = {
            update_status: 'unknown',
            update_available: null,
            update_check_error: error.message || 'Failed to load app update status'
        };
        return appUpdateStatusCache[appId];
    } finally {
        appUpdateStatusPending.delete(appId);
        if (selectedAppId === appId) {
            renderInstalledList();
            renderInspector();
        }
    }
}

function setStoreMeta(message) {
    const metaEl = document.getElementById('apps-store-meta');
    if (metaEl) metaEl.textContent = message;
}

function setComposeMeta(message, isError = false) {
    const metaEl = document.getElementById('compose-add-meta');
    if (!metaEl) return;
    metaEl.textContent = message;
    metaEl.style.color = isError ? 'var(--accent-danger)' : 'var(--text-secondary)';
}

function normalizeSearchText(value) {
    return String(value || '').toLowerCase().trim();
}

function populateCategoryFilter(apps) {
    const selectEl = document.getElementById('app-category-filter');
    if (!selectEl) return;

    const categories = Array.from(new Set(
        apps.map((app) => String(app?.category || 'Other'))
    )).sort((a, b) => a.localeCompare(b));

    const previousValue = availableCategoryFilter;
    selectEl.innerHTML = `
        <option value="all">All categories</option>
        ${categories.map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join('')}
    `;

    if (previousValue !== 'all' && categories.includes(previousValue)) {
        selectEl.value = previousValue;
    } else {
        selectEl.value = 'all';
        availableCategoryFilter = 'all';
    }
}

function getFilteredAvailableApps() {
    const query = normalizeSearchText(availableSearchQuery);
    const categoryFilter = String(availableCategoryFilter || 'all');

    return availableAppsCache.filter((app) => {
        const category = String(app?.category || 'Other');
        if (categoryFilter !== 'all' && category !== categoryFilter) return false;

        if (!query) return true;

        const haystack = `${app?.name || ''} ${app?.description || ''} ${app?.id || ''}`;
        return normalizeSearchText(haystack).includes(query);
    });
}

function renderAvailableApps() {
    const container = document.getElementById('apps-container');
    if (!container) return;

    const total = availableAppsCache.length;
    if (total === 0) {
        setStoreMeta('No apps available in the catalog.');
        container.innerHTML = `
            <div class="empty-state" style="grid-column: 1/-1;">
                <div class="empty-state-icon">${window.alvaIcon ? window.alvaIcon('loader-circle', '', 'aria-hidden="true"') : '...'}</div>
                <div>No apps available</div>
            </div>
        `;
        return;
    }

    const filteredApps = getFilteredAvailableApps();
    setStoreMeta(`Showing ${filteredApps.length} of ${total} app${total === 1 ? '' : 's'}.`);

    if (filteredApps.length === 0) {
        container.innerHTML = `
            <div class="empty-state" style="grid-column: 1/-1;">
                <div class="empty-state-icon">${window.alvaIcon ? window.alvaIcon('triangle-alert', '', 'aria-hidden="true"') : '!'}</div>
                <div>No apps match your current filters</div>
            </div>
        `;
        return;
    }

    const installedIds = new Set(installedAppsCache.map((a) => a.app_id));
    container.innerHTML = filteredApps.map((app) => {
        const installed = installedIds.has(app.id);
        const needs = describeNeeds(app);
        const version = String(app.version || '');
        const versionText = version && version !== 'latest' ? `v${escapeHtml(version.replace(/^v/i, ''))}` : '';
        return `
        <div class="app-card" onclick="showAppDetails('${escapeHtml(app.id)}')">
            <div class="app-icon">${getAppIcon(app)}</div>
            <div class="app-name">${escapeHtml(app.name)}</div>
            <div class="app-description">${escapeHtml(app.description)}</div>
            ${needs.text ? `<div class="app-needs">${escapeHtml(needs.text)}</div>` : ''}
            ${needs.conflict && !installed ? `<div class="app-needs warn">${escapeHtml(needs.conflict)}</div>` : ''}
            <div class="app-footer">
                <span class="app-footer-meta">${escapeHtml(app.category || '')}${versionText ? ` · ${versionText}` : ''}</span>
                ${installed
                    ? `<button class="btn-secondary" style="padding: 6px 12px; font-size: 0.85rem;" onclick="event.stopPropagation(); showInstalledApp('${escapeHtml(app.id)}')">Installed</button>`
                    : `<button class="btn-primary" style="padding: 6px 12px; font-size: 0.85rem;" onclick="event.stopPropagation(); installApp('${escapeHtml(app.id)}')">Install</button>`}
            </div>
        </div>`;
    }).join('');
}

function normalizeIconPath(iconPath) {
    const value = String(iconPath || '').trim();
    if (!value) return '';
    if (/^https?:\/\//i.test(value)) return value;
    if (value.startsWith('/')) return value;
    return `/${value}`;
}

function getCategoryIconMarkup(category) {
    const icons = {
        'Productivity': 'briefcase',
        'Media': 'film',
        'Development': 'code-xml',
        'Smart Home': 'house',
        'Network': 'globe',
        'Security': 'shield-check',
        'Monitoring': 'activity',
        'Search': 'search',
        'AI': 'bot',
        'Other': 'package'
    };
    const iconName = icons[category] || 'package';
    return window.alvaIcon ? window.alvaIcon(iconName, '', 'aria-hidden="true"') : '';
}

function getAppIcon(appOrCategory) {
    if (appOrCategory && typeof appOrCategory === 'object') {
        const fallback = getCategoryIconMarkup(appOrCategory.category);
        const iconUrl = normalizeIconPath(appOrCategory.icon);
        if (!iconUrl) return fallback;
        // The category icon stays underneath; the picture covers it only once
        // it has loaded, so a missing icon never shows a broken image.
        return `${fallback}<img class="app-icon-image" src="${escapeHtml(iconUrl)}" alt="" loading="lazy" decoding="async"
            onload="this.classList.add('loaded')" onerror="this.remove()">`;
    }
    return getCategoryIconMarkup(appOrCategory);
}

// Host ports used by installed apps: { 8096: 'Jellyfin' }.
function portsInUse() {
    const used = {};
    installedAppsCache.forEach((app) => {
        getContainersForApp(app.app_id).forEach((container) => {
            parseHostPorts(container.Ports).forEach((port) => { used[port] = app.name || app.app_id; });
        });
    });
    return used;
}

function describeNeeds(app) {
    const needs = app.needs || {};
    const ports = Array.isArray(needs.ports) ? needs.ports : [];
    const folders = Array.isArray(needs.folders) ? needs.folders : [];
    const used = portsInUse();
    const conflict = ports.find((p) => used[p.port]);
    const web = ports.find((p) => /web/i.test(p.description || '')) || ports[0];
    const bits = [];
    if (web) bits.push(`Opens on port ${web.port}`);
    if (ports.length > 1) bits.push(`${ports.length - 1} more port${ports.length > 2 ? 's' : ''}`);
    if (folders.length) bits.push(`${folders.length} folder${folders.length === 1 ? '' : 's'} on your storage`);
    return {
        text: bits.join(' · '),
        conflict: conflict ? `Port ${conflict.port} is already used by ${used[conflict.port]}` : '',
    };
}

function showInstalledApp(appId) {
    setActiveTab('installed');
    selectInstalledApp(appId);
}

// Running / stopped / partly running, in words a person understands.
function appRunState(appId) {
    if (!containersLoaded) return { key: 'unknown', label: '' };
    const own = getContainersForApp(appId);
    const running = own.filter((c) => c.State === 'running').length;
    if (own.length === 0) return { key: 'stopped', label: 'Not started' };
    if (running === own.length) return { key: 'running', label: 'Running' };
    if (running === 0) return { key: 'stopped', label: 'Stopped' };
    return { key: 'partial', label: 'Not fully running' };
}

function appQuickAddress(appId) {
    const port = getContainersForApp(appId).flatMap((c) => parseHostPorts(c.Ports))[0];
    return port ? `${window.location.protocol}//${window.location.hostname}:${port}` : '';
}

function setActiveTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    document.querySelectorAll('.tab-panel').forEach((panel) => {
        panel.classList.remove('active');
    });

    const targetPanel = document.getElementById(`tab-${tabName}`);
    if (targetPanel) targetPanel.classList.add('active');

    if (tabName === 'store') {
        loadAvailableApps();
    } else {
        loadInstalledWorkspace(true);
    }
}

// Tab switching
document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
        setActiveTab(btn.dataset.tab);
    });
});

async function fetchInstalledAppsData() {
    const response = await apiFetch(`${API_BASE}/apps/installed`, {
        headers: { 'Authorization': authToken }
    });

    if (!response.ok) throw new Error('Failed to load installed apps');

    const data = await response.json();
    return data.apps || [];
}

async function fetchContainersData() {
    const response = await apiFetch(`${API_BASE}/containers`, {
        headers: { 'Authorization': authToken }
    });

    if (!response.ok) throw new Error('Failed to load containers');

    const data = await response.json();
    return data.containers || [];
}

function isContainerForApp(container, appId) {
    const labels = String(container?.Labels || '');
    const expectedProjectLabel = `com.docker.compose.project=alvaos-${appId}`;
    if (labels.includes(expectedProjectLabel)) return true;

    const namesRaw = String(container?.Names || '');
    const names = namesRaw.split(',').map((v) => v.trim());
    return names.some((name) => name.includes(`alvaos-${appId}`));
}

function getContainersForApp(appId) {
    return containersCache.filter((container) => isContainerForApp(container, appId));
}

function parseHostPorts(portsRaw) {
    const ports = [];
    const value = String(portsRaw || '');
    const matches = value.matchAll(/:(\d+)->\d+\/tcp/g);

    for (const match of matches) {
        const port = Number(match[1]);
        if (!Number.isNaN(port)) ports.push(port);
    }

    return ports;
}

function formatPortBadges(portsRaw) {
    const value = String(portsRaw || '').trim();
    if (!value) return '<span style="color: var(--text-secondary);">&mdash;</span>';

    const seen = new Set();
    const badges = [];

    value.split(',').forEach((part) => {
        const match = part.trim().match(/^(?:.*:(\d+)->)?(\d+)\/(\w+)$/);
        if (!match) return;
        const [, hostPort, containerPort, proto] = match;
        const key = `${hostPort || ''}-${containerPort}-${proto}`;
        if (seen.has(key)) return;
        seen.add(key);

        const protoSuffix = proto.toLowerCase() !== 'tcp' ? `/${proto}` : '';
        const label = hostPort
            ? `${hostPort} → ${containerPort}${protoSuffix}`
            : `${containerPort}${protoSuffix} (internal)`;
        badges.push(`<span class="port-badge">${escapeHtml(label)}</span>`);
    });

    return badges.length ? badges.join('') : '<span style="color: var(--text-secondary);">&mdash;</span>';
}

async function getAppDetails(appId) {
    if (appDetailsCache[appId]) return appDetailsCache[appId];

    try {
        const response = await apiFetch(`${API_BASE}/apps/available/${appId}`, {
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) return null;

        const data = await response.json();
        appDetailsCache[appId] = data;
        return data;
    } catch {
        return null;
    }
}

function buildWebUiUrl(appId, appDetails, appContainers) {
    const host = window.location.hostname || 'localhost';
    let configuredPath = String(
        appDetails?.config_schema?.webui_path || appDetails?.webui_path || '/'
    );
    let webUiPath = configuredPath.startsWith('/') ? configuredPath : `/${configuredPath}`;
    let scheme = 'http';

    if (appId === 'vaultwarden' && window.location.protocol === 'https:') {
        scheme = 'https';
    } else if (appId === 'vaultwarden' && webUiPath === '/') {
        // Vaultwarden web vault needs HTTPS; use admin panel path for HTTP setups.
        webUiPath = '/admin';
    }

    const schemaPorts = appDetails?.config_schema?.ports;
    if (Array.isArray(schemaPorts) && schemaPorts.length > 0) {
        const preferred = schemaPorts.find((item) => {
            const desc = String(item?.description || '').toLowerCase();
            return desc.includes('web') || desc.includes('ui');
        });

        if (!preferred) {
            return null;
        }

        const externalPort = Number(preferred?.external);
        if (!Number.isNaN(externalPort) && externalPort > 0) {
            if (externalPort === 80) return `${scheme}://${host}${webUiPath}`;
            return `${scheme}://${host}:${externalPort}${webUiPath}`;
        }
    }

    for (const container of appContainers) {
        const hostPorts = parseHostPorts(container?.Ports);
        if (hostPorts.length > 0) {
            const port = hostPorts[0];
            if (port === 80) return `${scheme}://${host}${webUiPath}`;
            return `${scheme}://${host}:${port}${webUiPath}`;
        }
    }

    return null;
}

function renderInstalledList() {
    const list = document.getElementById('apps-list');
    if (!list) return;

    list.innerHTML = installedAppsCache.map((app) => {
        const state = appRunState(app.app_id);
        const toggling = appTogglePending.has(app.app_id);
        const address = state.key === 'running' ? appQuickAddress(app.app_id) : '';
        const cachedUpdate = appUpdateStatusCache[app.app_id] || {};
        const updateReady = (cachedUpdate.update_available ?? app.update_available) === true && app.source !== 'custom_compose';
        const sub = toggling
            ? 'Working...'
            : state.key === 'partial'
                ? 'Part of it stopped. Restart it in the details.'
                : address
                    ? `Open at ${address.replace(/^https?:\/\//, '')}`
                    : (state.key === 'stopped' ? 'Not running' : (containersLoading ? 'Checking...' : ''));
        const action = toggling
            ? ''
            : address
                ? `<a class="btn-secondary app-quick" href="${escapeHtml(address)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Open</a>`
                : (state.key === 'stopped' && getContainersForApp(app.app_id).length
                    ? `<button class="btn-secondary app-quick" onclick="event.stopPropagation(); toggleAppRunning('${escapeHtml(app.app_id)}')">Start</button>`
                    : '');
        return `
            <div class="app-list-item ${selectedAppId === app.app_id ? 'active' : ''}" role="button" tabindex="0"
                onclick="selectInstalledApp('${escapeHtml(app.app_id)}')"
                onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectInstalledApp('${escapeHtml(app.app_id)}'); }">
                <div class="app-list-top">
                    <div class="app-list-name">${escapeHtml(app.name || app.app_id)}</div>
                    ${state.label ? `<span class="app-state ${state.key}">${escapeHtml(state.label)}</span>` : ''}
                </div>
                <div class="app-list-bottom">
                    <div class="app-count">${escapeHtml(sub)}${updateReady ? ' <span class="app-update-dot">Update ready</span>' : ''}</div>
                    ${action}
                </div>
            </div>
        `;
    }).join('');
}

async function renderInspector() {
    const inspector = document.getElementById('app-inspector');
    if (!inspector) return;

    const selected = installedAppsCache.find((app) => app.app_id === selectedAppId);
    if (!selected) {
        inspector.innerHTML = `
            <div class="inspector-card">
                <div class="empty-state">
                    <div class="empty-state-icon">${window.alvaIcon ? window.alvaIcon('loader-circle', '', 'aria-hidden="true"') : '...'}</div>
                    <div>Select an app to see details.</div>
                </div>
            </div>
        `;
        return;
    }

    const selectedAtRender = selected.app_id;
    const appContainers = containersLoaded ? getContainersForApp(selected.app_id) : [];
    const runningCount = appContainers.filter((c) => c.State === 'running').length;
    const appDetails = await getAppDetails(selected.app_id);
    const cachedUpdateState = appUpdateStatusCache[selected.app_id];
    const effectiveUpdateState = cachedUpdateState
        ? { ...selected, ...cachedUpdateState }
        : (
            selected.update_status || selected.update_available != null
                ? selected
                : { ...selected, update_status: selected.source === 'custom_compose' ? 'unsupported' : 'checking' }
        );

    if (selectedAppId !== selectedAtRender) {
        return;
    }

    if (selected.source !== 'custom_compose' && !appUpdateStatusPending.has(selected.app_id) && !cachedUpdateState) {
        void loadAppUpdateStatus(selected.app_id);
    }

    const webUiUrl = buildWebUiUrl(selected.app_id, appDetails, appContainers);
    const appId = selected.app_id;
    const escId = escapeHtml(appId);
    const isCustom = String(selected.source || 'catalog') === 'custom_compose';
    const toggling = appTogglePending.has(appId);
    const updateAvailable = effectiveUpdateState.update_available === true && !isCustom;

    // Calm sub-status line: installed date + a quiet update hint (never red).
    const statusBits = [`Installed ${escapeHtml(formatDate(selected.installed_at))}`];
    if (isCustom) {
        statusBits.push('Custom app');
    } else if (String(effectiveUpdateState.update_status || '') === 'checking') {
        statusBits.push('Checking for updates&hellip;');
    } else if (effectiveUpdateState.update_available === false) {
        statusBits.push('Up to date');
    }

    inspector.innerHTML = `
        <div class="inspector-card">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:16px;">
                <div style="min-width:0;">
                    <div style="font-size:1.2rem; font-weight:600;">${escapeHtml(selected.name || appId)}</div>
                    <div class="inspector-substatus">
                        <span class="app-state ${appRunState(appId).key}">${escapeHtml(appRunState(appId).label || (runningCount > 0 ? 'Running' : 'Stopped'))}</span>
                        <span>${statusBits.join(' &middot; ')}</span>
                    </div>
                </div>
                <div class="menu-wrap">
                    <button class="menu-btn" aria-label="More actions" onclick="toggleInspectorMenu('app-actions-menu', event)">&#8943;</button>
                    <div class="menu-pop" id="app-actions-menu">
                        ${appContainers.length > 0 ? `
                            <button class="menu-item" onclick="toggleInspectorMenu('app-actions-menu', event); restartApp('${escId}')">
                                ${window.alvaIcon ? window.alvaIcon('rotate-cw') : ''} Restart app
                            </button>` : ''}
                        ${!isCustom ? `
                            <button class="menu-item" onclick="toggleInspectorMenu('app-actions-menu', event); checkAppForUpdates('${escId}')">
                                ${window.alvaIcon ? window.alvaIcon('refresh-cw') : ''} Check for updates
                            </button>` : ''}
                        <div class="menu-sep"></div>
                        <button class="menu-item danger" onclick="toggleInspectorMenu('app-actions-menu', event); uninstallApp('${escId}')">
                            ${window.alvaIcon ? window.alvaIcon('trash-2') : ''} Uninstall app&hellip;
                        </button>
                    </div>
                </div>
            </div>

            <div class="inspector-actions" style="margin-bottom:0;">
                ${webUiUrl
                    ? `<a class="btn-primary-lg" href="${escapeHtml(webUiUrl)}" target="_blank" rel="noopener noreferrer">${window.alvaIcon ? window.alvaIcon('external-link') : ''} Open Web UI</a>`
                    : '<button class="btn-secondary" disabled style="opacity:0.6; cursor:not-allowed;">No Web UI detected</button>'}
                ${appContainers.length > 0 ? `
                    <button class="btn-secondary" ${toggling ? 'disabled style="opacity:0.6; cursor:not-allowed;"' : ''}
                        onclick="toggleAppRunning('${escId}')">${toggling ? '...' : (runningCount > 0 ? 'Stop' : 'Start')}</button>
                ` : ''}
                ${updateAvailable ? `
                    <button class="btn-update" onclick="updateApp('${escId}')">
                        ${window.alvaIcon ? window.alvaIcon('circle-arrow-up') : ''} Update
                    </button>
                ` : ''}
            </div>

            <details class="disc">
                <summary>${window.alvaIcon ? window.alvaIcon('info') : ''} Details &amp; storage <span class="chev">${window.alvaIcon ? window.alvaIcon('chevron-right') : '&rsaquo;'}</span></summary>
                <div class="disc-body">
                    <div class="inspector-grid" style="margin:4px 0 0;">
                        <div>
                            <div class="inspector-label">App ID</div>
                            <div class="inspector-value mono-text">${escId}</div>
                        </div>
                        <div>
                            <div class="inspector-label">Storage path</div>
                            <div class="inspector-value mono-text">${escapeHtml(selected.storage_path || '-')}</div>
                        </div>
                        <div>
                            <div class="inspector-label">Pool</div>
                            <div class="inspector-value mono-text">${escapeHtml(selected.pool_path || '-')}</div>
                        </div>
                        <div>
                            <div class="inspector-label">Subvolume</div>
                            <div class="inspector-value mono-text">${escapeHtml(selected.parent_subvolume || '-')}</div>
                        </div>
                        <div>
                            <div class="inspector-label">Source</div>
                            <div class="inspector-value">${isCustom ? 'Custom compose' : 'App catalog'}</div>
                        </div>
                    </div>
                </div>
            </details>

            <details class="disc">
                <summary>${window.alvaIcon ? window.alvaIcon('layers') : ''} Advanced &middot; Containers${containersLoaded ? ` (${appContainers.length})` : ''} <span class="chev">${window.alvaIcon ? window.alvaIcon('chevron-right') : '&rsaquo;'}</span></summary>
                <div class="disc-body">
                    ${containersLoaded ? renderContainerTable(appContainers) : `
                        <div class="empty-state" style="padding: 1.5rem 1rem;">
                            <div>Loading container data...</div>
                        </div>
                    `}
                </div>
            </details>
        </div>
    `;
}

function renderContainerTable(containers) {
    if (containers.length === 0) {
        return `
            <div class="empty-state" style="padding: 1.5rem 1rem;">
                <div>No containers found for this app.</div>
            </div>
        `;
    }

    return `
        <div class="table-scroll">
        <table class="containers-table">
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Status</th>
                    <th>Image</th>
                    <th>Ports</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${containers.map((container, i) => {
                    const isRunning = container.State === 'running';
                    const name = container.Names || (container.ID || '').substring(0, 12);
                    const image = container.Image || 'unknown';
                    const portBadges = formatPortBadges(container.Ports);
                    // Security: Use JSON.stringify to safely embed IDs as a JS string literal,
                    // then HTML-escape the result so its double quotes don't terminate the
                    // (also double-quoted) onclick="..." attribute they sit inside - without
                    // this, the browser truncated the handler at the literal's opening quote
                    // and every container action button below silently did nothing.
                    const idJson = escapeHtml(JSON.stringify(container.ID || ''));

                    return `
                        <tr>
                            <td><span class="mono-text" style="font-size: 0.85rem;">${escapeHtml(name)}</span></td>
                            <td>
                                <span class="container-status ${isRunning ? 'running' : 'stopped'}">${escapeHtml(container.State || 'unknown')}</span>
                            </td>
                            <td style="font-size: 0.85rem;">${escapeHtml(image)}</td>
                            <td style="font-size: 0.85rem; line-height: 1.6;">${portBadges}</td>
                            <td>
                                <div class="container-actions">
                                    ${isRunning
                                        ? `<button class="btn-icon" onclick="stopContainer(${idJson})">Stop</button>`
                                        : `<button class="btn-icon" onclick="startContainer(${idJson})">Start</button>`}
                                    <button class="btn-icon" onclick="openTerminalModal(${idJson})">Terminal</button>
                                    <button class="btn-icon" onclick="viewLogs(${idJson})">Logs</button>
                                    <button class="btn-icon btn-danger" onclick="deleteContainer(${idJson})">Delete</button>
                                </div>
                            </td>
                        </tr>
                    `;
                }).join('')}
            </tbody>
        </table>
        </div>
    `;
}

function renderInstalledWorkspace() {
    const container = document.getElementById('installed-container');
    if (!container) return;

    container.innerHTML = `
        <div class="apps-workspace">
            <div id="apps-list" class="apps-list"></div>
            <div id="app-inspector"></div>
        </div>
    `;

    renderInstalledList();
    renderInspector();
}

async function loadInstalledWorkspace(preserveSelection = true) {
    const container = document.getElementById('installed-container');
    const refreshBtn = document.getElementById('refresh-installed-btn');
    if (!container) return;
    const requestId = ++installedLoadRequestId;
    setLoadingButtonState(refreshBtn, true, 'Refreshing...');

    container.innerHTML = `
        <div class="empty-state">
            <div class="empty-state-icon">${window.alvaIcon ? window.alvaIcon('loader-circle', '', 'aria-hidden="true"') : '...'}</div>
            <div>Loading installed apps...</div>
        </div>
    `;

    try {
        const apps = await fetchInstalledAppsData();
        if (requestId !== installedLoadRequestId) return;
        installedAppsCache = apps;

        if (installedAppsCache.length === 0) {
            selectedAppId = null;
            containersLoaded = false;
            containersLoading = false;
            containersCache = [];
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">${window.alvaIcon ? window.alvaIcon('loader-circle', '', 'aria-hidden="true"') : '...'}</div>
                    <div>No apps installed yet</div>
                    <p style="font-size: 0.9rem; margin-top: 8px;">Install your first app from the store.</p>
                    <button id="open-store-btn" class="btn-link" style="margin-top: 12px;">Open App Store</button>
                </div>
            `;

            const openStoreBtn = document.getElementById('open-store-btn');
            if (openStoreBtn) {
                openStoreBtn.addEventListener('click', () => setActiveTab('store'));
            }
            return;
        }

        if (!preserveSelection || !installedAppsCache.some((app) => app.app_id === selectedAppId)) {
            selectedAppId = installedAppsCache[0].app_id;
        }

        containersLoading = true;
        containersLoaded = false;
        renderInstalledWorkspace();

        try {
            const containers = await fetchContainersData();
            if (requestId !== installedLoadRequestId) return;
            containersCache = containers;
            containersLoaded = true;
        } catch (containersError) {
            if (requestId !== installedLoadRequestId) return;
            console.error('Error loading containers:', containersError);
            containersCache = [];
            containersLoaded = false;
        } finally {
            if (requestId !== installedLoadRequestId) return;
            containersLoading = false;
            renderInstalledList();
            renderInspector();
            // The store marks installed apps and port clashes, so it needs
            // the installed list and the containers too.
            if (availableAppsCache.length) renderAvailableApps();
        }
    } catch (error) {
        if (requestId !== installedLoadRequestId) return;
        console.error('Error loading installed workspace:', error);
        containersLoaded = false;
        containersLoading = false;
        containersCache = [];
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">${window.alvaIcon ? window.alvaIcon('triangle-alert', '', 'aria-hidden="true"') : '!'}</div>
                <div>Failed to load installed apps</div>
                <p style="font-size: 0.85rem; margin-top: 8px; color: var(--accent-danger);">${escapeHtml(error.message)}</p>
            </div>
        `;
    } finally {
        if (requestId === installedLoadRequestId) {
            setLoadingButtonState(refreshBtn, false);
        }
    }
}
function selectInstalledApp(appId) {
    selectedAppId = appId;
    renderInstalledList();
    renderInspector();
}

// Load available apps from catalog
async function loadAvailableApps() {
    const container = document.getElementById('apps-container');
    const refreshBtn = document.getElementById('refresh-apps-btn');
    if (!container) return;
    if (availableAppsLoadPromise) return availableAppsLoadPromise;

    availableAppsLoadPromise = (async () => {
        setLoadingButtonState(refreshBtn, true, 'Refreshing...');

        try {
            const response = await apiFetch(`${API_BASE}/apps/available`, {
                headers: { 'Authorization': authToken }
            });

            if (!response.ok) throw new Error('Failed to load apps');

            const data = await response.json();
            availableAppsCache = Array.isArray(data.apps) ? data.apps : [];
            populateCategoryFilter(availableAppsCache);
            renderAvailableApps();
        } catch (error) {
            console.error('Error loading apps:', error);
            availableAppsCache = [];
            setStoreMeta('Failed to load app catalog.');
            container.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="empty-state-icon">${window.alvaIcon ? window.alvaIcon('triangle-alert', '', 'aria-hidden="true"') : '!'}</div>
                    <div>Failed to load apps</div>
                    <p style="font-size: 0.85rem; margin-top: 8px; color: var(--accent-danger);">${escapeHtml(error.message)}</p>
                </div>
            `;
        } finally {
            availableAppsLoadPromise = null;
            setLoadingButtonState(refreshBtn, false);
        }
    })();

    return availableAppsLoadPromise;
}

function slugifyAppId(value) {
    return String(value || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9._-]+/g, '-')
        .replace(/[-_.]{2,}/g, '-')
        .replace(/^[-_.]+|[-_.]+$/g, '')
        .slice(0, 64)
        .replace(/[-_.]+$/g, '');
}

function updateComposeDeployButtonState() {
    const deployBtn = document.getElementById('compose-deploy-btn');
    const nameInput = document.getElementById('compose-app-name');
    const poolSelect = document.getElementById('compose-pool-select');
    const yamlInput = document.getElementById('compose-yaml-input');
    if (!deployBtn || !nameInput || !poolSelect || !yamlInput) return;

    const hasName = String(nameInput.value || '').trim().length > 0;
    const hasPool = String(poolSelect.value || '').trim().length > 0;
    const hasYaml = String(yamlInput.value || '').trim().length > 0;
    deployBtn.disabled = !(hasName && hasPool && hasYaml) || poolSelect.disabled;
}

async function loadComposePoolOptions() {
    const poolSelect = document.getElementById('compose-pool-select');
    if (!poolSelect) return;
    if (composePoolsLoadPromise) return composePoolsLoadPromise;

    const previousValue = String(poolSelect.value || '');
    const deployBtn = document.getElementById('compose-deploy-btn');

    composePoolsLoadPromise = (async () => {
        poolSelect.disabled = true;
        poolSelect.innerHTML = '<option value="">Loading pools...</option>';
        if (deployBtn) deployBtn.disabled = true;

        try {
            const response = await apiFetch(`${API_BASE}/storage/pools`, {
                headers: { 'Authorization': authToken }
            });
            if (!response.ok) throw new Error('Failed to load storage pools');

            const data = await response.json();
            const pools = Array.isArray(data?.pools) ? data.pools : [];

            if (pools.length === 0) {
                poolSelect.innerHTML = '<option value="">No pools available</option>';
                setComposeMeta('Create a storage pool first to deploy compose apps.', true);
                return;
            }

            poolSelect.innerHTML = pools.map((pool) => `
                <option value="${escapeHtml(pool.mount_point)}">${escapeHtml(pool.name)} (${escapeHtml(pool.total_size)} total, ${escapeHtml(pool.mount_point)})</option>
            `).join('');

            if (previousValue && pools.some((pool) => String(pool.mount_point) === previousValue)) {
                poolSelect.value = previousValue;
            }

            poolSelect.disabled = false;
            setComposeMeta('Tip: Use ${POOL_PATH} for data paths inside the selected app storage folder.', false);
        } catch (error) {
            console.error('Error loading compose pools:', error);
            poolSelect.innerHTML = '<option value="">Storage pools unavailable - try again shortly</option>';
            setComposeMeta(`Failed to load pools: ${error.message}`, true);
        } finally {
            composePoolsLoadPromise = null;
            updateComposeDeployButtonState();
        }
    })();

    return composePoolsLoadPromise;
}

async function deployComposeApp() {
    const nameInput = document.getElementById('compose-app-name');
    const poolSelect = document.getElementById('compose-pool-select');
    const yamlInput = document.getElementById('compose-yaml-input');
    const deployBtn = document.getElementById('compose-deploy-btn');
    if (!nameInput || !poolSelect || !yamlInput || !deployBtn) return;

    const appName = String(nameInput.value || '').trim();
    const poolPath = String(poolSelect.value || '').trim();
    const composeYaml = String(yamlInput.value || '').trim();

    const nameError = document.getElementById('compose-app-name-error');
    const poolError = document.getElementById('compose-pool-select-error');
    const yamlError = document.getElementById('compose-yaml-input-error');
    const setFieldError = (el, message) => {
        if (!el) return;
        el.textContent = message || '';
        el.style.display = message ? 'block' : 'none';
    };
    setFieldError(nameError, '');
    setFieldError(poolError, '');
    setFieldError(yamlError, '');

    const appId = slugifyAppId(appName);

    if (!appName) {
        setFieldError(nameError, 'Please enter an app name');
        return;
    }
    if (!appId) {
        setFieldError(nameError, 'App name contains no valid characters');
        return;
    }
    if (!poolPath) {
        setFieldError(poolError, 'Please select a storage pool');
        return;
    }
    if (!composeYaml) {
        setFieldError(yamlError, 'Please paste a Docker Compose YAML file');
        return;
    }

    setLoadingButtonState(deployBtn, true, 'Deploying...');
    try {
        const response = await apiFetch(`${API_BASE}/apps/install/compose`, {
            method: 'POST',
            headers: {
                'Authorization': authToken,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                app_name: appName,
                app_id: appId,
                pool_path: poolPath,
                compose_yaml: composeYaml
            })
        });

        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload?.error || 'Failed to start compose deployment');
        }

        const resolvedAppId = String(payload?.app_id || appId);
        showNotification(`Deployment started for "${appName}"`, 'info');

        await waitForAppOperation(resolvedAppId, 'install', 1800);

        showNotification(`App "${appName}" deployed successfully`, 'success');
        nameInput.value = '';
        setActiveTab('installed');
    } catch (error) {
        console.error('Compose deploy error:', error);
        showNotification(`Compose deployment failed: ${error.message}`, 'error');
    } finally {
        setLoadingButtonState(deployBtn, false);
        updateComposeDeployButtonState();
    }
}

// Keep compatibility for existing calls
async function loadInstalledApps() {
    await loadInstalledWorkspace(true);
}

async function loadContainers() {
    await loadInstalledWorkspace(true);
}

// Show installation wizard instead of prompt
async function showInstallWizard(appId) {
    const modal = document.getElementById('install-modal');
    const poolSelect = document.getElementById('install-pool-select');
    const envFieldsGroup = document.getElementById('install-env-fields-group');
    const envFields = document.getElementById('install-env-fields');
    const confirmBtn = document.getElementById('confirm-install-btn');
    const closeBtns = document.querySelectorAll('.close-modal-btn');

    // Reset modal
    modal.style.display = 'flex';
    poolSelect.innerHTML = '<option value="">Loading pools...</option>';
    if (envFields) envFields.innerHTML = '';
    if (envFieldsGroup) envFieldsGroup.style.display = 'none';
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Install Application';

    // Close modal handlers
    const closeInstallModal = () => { modal.style.display = 'none'; };
    closeBtns.forEach(btn => {
        btn.onclick = closeInstallModal;
    });
    if (!modal.dataset.dismissAttached) {
        modal.dataset.dismissAttached = 'true';
        attachModalDismiss(modal, closeInstallModal);
    }

    try {
        // Fetch app details
        const appRes = await apiFetch(`${API_BASE}/apps/available/${appId}`, {
            headers: { 'Authorization': authToken }
        });
        if (!appRes.ok) throw new Error('Failed to load app details');
        const app = await appRes.json();

        // Populate info
        document.getElementById('install-app-name').textContent = app.name || appId;
        document.getElementById('install-app-version').textContent = `Version: ${app.version || 'latest'}`;
        document.getElementById('install-app-desc').textContent = app.description || '';
        document.getElementById('install-app-icon').innerHTML = getAppIcon(app);

        const environmentSchema = Array.isArray(app?.config_schema?.environment)
            ? app.config_schema.environment
            : [];
        if (envFields && envFieldsGroup && environmentSchema.length > 0) {
            envFieldsGroup.style.display = 'block';
            envFields.innerHTML = environmentSchema.map((entry, idx) => {
                const key = String(entry?.key || '').trim();
                if (!key) return '';
                const description = String(entry?.description || '');
                const isRequired = entry?.required === true;
                const isSecretField = entry?.secret === true
                    || (entry?.secret !== false && /(password|token|secret|key)/i.test(key));
                // Required fields start empty on purpose: a prefilled placeholder
                // is exactly how apps used to ship with a known password.
                const defaultValue = isRequired ? '' : String(entry?.default ?? '');
                const canGenerate = entry?.generate === true;

                return `
                    <div class="setting-group" style="margin-bottom: 0;">
                        <label class="setting-label" for="install-env-${idx}">
                            ${escapeHtml(key)}${isRequired ? ' <span style="color: var(--accent-primary);">*</span>' : ''}
                        </label>
                        ${description ? `<p style="font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 6px;">${escapeHtml(description)}</p>` : ''}
                        <div style="display: flex; gap: 8px;">
                            <input
                                id="install-env-${idx}"
                                data-env-key="${escapeHtml(key)}"
                                data-env-required="${isRequired ? 'true' : 'false'}"
                                type="${isSecretField ? 'password' : 'text'}"
                                value="${escapeHtml(defaultValue)}"
                                ${isRequired ? 'required aria-required="true"' : ''}
                                placeholder="${escapeHtml(isRequired ? 'Required' : (defaultValue || key))}"
                                style="flex: 1; padding: 10px;">
                            ${canGenerate ? `<button type="button" class="btn-secondary"
                                data-generate-for="install-env-${idx}"
                                style="white-space: nowrap; padding: 10px 12px;">Generate</button>` : ''}
                        </div>
                    </div>
                `;
            }).join('');

            // "Generate" fills a strong random value, so the common case is one
            // click rather than the user inventing a password for a database
            // they will never log into by hand.
            envFields.querySelectorAll('[data-generate-for]').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const target = document.getElementById(btn.getAttribute('data-generate-for'));
                    if (!target) return;
                    target.value = generateSecret();
                    target.type = 'text';
                    target.dispatchEvent(new Event('input', { bubbles: true }));
                });
            });

            // Pre-generate where we can, so a careful default is already in place.
            envFields.querySelectorAll('[data-generate-for]').forEach((btn) => {
                const target = document.getElementById(btn.getAttribute('data-generate-for'));
                if (target && !target.value) target.value = generateSecret();
            });
        }

        // Fetch pools
        const poolsRes = await apiFetch(`${API_BASE}/storage/pools`, {
            headers: { 'Authorization': authToken }
        });
        if (!poolsRes.ok) throw new Error('Failed to load storage pools');
        const poolsData = await poolsRes.json();
        const pools = poolsData.pools || [];

        if (pools.length === 0) {
            poolSelect.innerHTML = '<option value="">No pools available - create one first!</option>';
        } else {
            poolSelect.innerHTML = pools.map(p => `
                <option value="${escapeHtml(p.mount_point)}">${escapeHtml(p.name)} (${escapeHtml(p.total_size)} total, ${escapeHtml(p.mount_point)})</option>
            `).join('');
            confirmBtn.disabled = false;
        }

        // Handle confirm
        confirmBtn.onclick = async () => {
            const poolPath = poolSelect.value;
            const environmentVars = {};
            const missingFields = [];
            document.querySelectorAll('#install-env-fields [data-env-key]').forEach((inputEl) => {
                const key = inputEl.getAttribute('data-env-key');
                if (!key) return;
                const value = (inputEl.value || '').trim();
                const required = inputEl.getAttribute('data-env-required') === 'true';
                if (required && (!value || value.toUpperCase() === 'CHANGEME')) {
                    missingFields.push({ key, el: inputEl });
                    return;
                }
                if (value !== '') {
                    environmentVars[key] = inputEl.value;
                }
            });

            if (missingFields.length > 0) {
                missingFields.forEach(({ el }) => { el.style.borderColor = 'var(--accent-warning)'; });
                missingFields[0].el.focus();
                showNotification(
                    `Still needed: ${missingFields.map(f => f.key).join(', ')}. ` +
                    'Use Generate if you do not have a value in mind.',
                    'warning'
                );
                return;
            }

            if (!poolPath) {
                showNotification('Please select a storage pool', 'error');
                return;
            }

            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Starting...';

            try {
                const installRes = await apiFetch(`${API_BASE}/apps/install`, {
                    method: 'POST',
                    headers: {
                        'Authorization': authToken,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        app_id: appId,
                        pool_path: poolPath,
                        environment_vars: Object.keys(environmentVars).length ? environmentVars : undefined
                    })
                });

                if (!installRes.ok) {
                    const errorData = await installRes.json();
                    throw new Error(errorData.error || 'Failed to initiate installation');
                }

                // Show progress container
                document.getElementById('install-progress-container').style.display = 'block';
                document.getElementById('install-log-content').textContent = '';

                // Start polling
                pollInstallStatus(appId);

            } catch (error) {
                console.error('Install error:', error);
                showNotification(`Installation failed: ${error.message}`, 'error');
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Install Application';
            }
        };

    } catch (err) {
        showNotification(err.message, 'error');
        modal.style.display = 'none';
    }
}

function showAppDetails(appId) {
    showInstallWizard(appId);
}

async function installApp(appId) {
    showInstallWizard(appId);
}

async function showUninstallDialog(appId) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal-content">
                <div class="modal-title">Uninstall ${escapeHtml(appId)}?</div>
                <div class="modal-body">This will stop and remove all associated containers.</div>
                <label style="display:flex; align-items:center; gap:10px; cursor:pointer; margin-bottom: 8px;">
                    <input type="checkbox" class="uninstall-keep-data" checked>
                    <span>Keep app data (storage subvolumes)</span>
                </label>
                <div style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 16px;">
                    Uncheck to permanently delete all app data.
                </div>
                <div class="modal-actions">
                    <button class="btn-secondary" data-action="cancel">Cancel</button>
                    <button class="btn-primary" data-action="confirm" style="background: var(--accent-danger); border: none; color: white;">Uninstall</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        const keepDataToggle = overlay.querySelector('.uninstall-keep-data');
        const close = (value) => {
            overlay.remove();
            resolve(value);
        };

        overlay.querySelector('[data-action="cancel"]').onclick = () => close(null);
        overlay.querySelector('[data-action="confirm"]').onclick = () => close(!!keepDataToggle?.checked);
    });
}

async function uninstallApp(appId) {
    const keepData = await showUninstallDialog(appId);
    if (keepData === null) return;

    try {
        const response = await apiFetch(`${API_BASE}/apps/${appId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': authToken,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ keep_data: keepData })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Uninstallation failed');
        }

        showNotification('App uninstalled successfully!', 'success');
        await loadInstalledWorkspace(false);

    } catch (error) {
        console.error('Uninstallation error:', error);
        showNotification(`Uninstallation failed: ${error.message}`, 'error');
    }
}

function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForAppOperation(appId, expectedAction = 'update', timeoutSeconds = 900) {
    const started = Date.now();

    while (true) {
        if ((Date.now() - started) / 1000 > timeoutSeconds) {
            throw new Error('Timed out while waiting for app operation status');
        }

        const response = await apiFetch(`${API_BASE}/apps/install/status`, {
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) {
            await wait(2000);
            continue;
        }

        const data = await response.json();
        const statusAppId = String(data?.app_id || '');
        const status = String(data?.status || '').toLowerCase();
        const action = String(data?.action || 'install').toLowerCase();

        if (statusAppId !== appId) {
            await wait(1500);
            continue;
        }

        if (expectedAction && action !== String(expectedAction).toLowerCase()) {
            await wait(1500);
            continue;
        }

        if (status === 'success') {
            return data;
        }

        if (status === 'error') {
            throw new Error(data?.message || 'Operation failed');
        }

        await wait(1500);
    }
}

async function updateApp(appId) {
    const ok = await window.showConfirm(`Update "${appId}" now?\nThis pulls new images and recreates app containers.`);
    if (!ok) return;

    try {
        const response = await apiFetch(`${API_BASE}/apps/${appId}/update`, {
            method: 'POST',
            headers: {
                'Authorization': authToken,
                'Content-Type': 'application/json'
            }
        });

        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload?.error || 'Failed to start update');
        }

        showNotification(`Update started for "${appId}"`, 'info');
        await waitForAppOperation(appId, 'update', 1800);
        showNotification(`App "${appId}" updated successfully`, 'success');
        await loadInstalledWorkspace(true);
    } catch (error) {
        console.error('Update error:', error);
        showNotification(`Update failed: ${error.message}`, 'error');
    }
}

// Toggle an inspector overflow (...) menu, closing any other open menu first.
function toggleInspectorMenu(id, event) {
    if (event) event.stopPropagation();
    const pop = document.getElementById(id);
    if (!pop) return;
    const willOpen = !pop.classList.contains('open');
    document.querySelectorAll('.menu-pop.open').forEach((m) => {
        if (m !== pop) m.classList.remove('open');
    });
    pop.classList.toggle('open', willOpen);
}

function closeAllInspectorMenus() {
    document.querySelectorAll('.menu-pop.open').forEach((m) => m.classList.remove('open'));
}

// Force a fresh update check from the (...) menu and report the outcome calmly.
async function checkAppForUpdates(appId) {
    showNotification('Checking for updates...', 'info');
    await loadAppUpdateStatus(appId, true);
    if (selectedAppId === appId) await renderInspector();

    const state = appUpdateStatusCache[appId] || {};
    if (state.update_available === true) {
        showNotification('Update available', 'info');
    } else if (state.update_available === false) {
        showNotification('App is up to date', 'success');
    } else {
        showNotification(state.update_check_error || 'Update status unavailable right now', 'error');
    }
}

// Restart an app: stop any running containers, then start all of them again.
async function restartApp(appId) {
    if (appTogglePending.has(appId)) return;
    const appContainers = getContainersForApp(appId);
    if (appContainers.length === 0) return;

    appTogglePending.add(appId);
    renderInstalledList();
    if (selectedAppId === appId) await renderInspector();

    try {
        const running = appContainers.filter((c) => c.State === 'running');
        await Promise.allSettled(running.map((c) =>
            apiFetch(`${API_BASE}/containers/${c.ID}/stop`, {
                method: 'POST',
                headers: { 'Authorization': authToken }
            })
        ));
        const results = await Promise.allSettled(appContainers.map((c) =>
            apiFetch(`${API_BASE}/containers/${c.ID}/start`, {
                method: 'POST',
                headers: { 'Authorization': authToken }
            }).then((res) => {
                if (!res.ok) throw new Error(`Failed to start ${c.Names || c.ID}`);
            })
        ));

        const failedCount = results.filter((r) => r.status === 'rejected').length;
        if (failedCount > 0) {
            showNotification(`Restarted with ${failedCount} container(s) failing to start`, 'error');
        } else {
            showNotification('App restarted', 'success');
        }
    } catch (error) {
        console.error('Restart app error:', error);
        showNotification(`Failed to restart app: ${error.message}`, 'error');
    } finally {
        appTogglePending.delete(appId);
        await loadInstalledWorkspace(true);
    }
}

async function toggleAppRunning(appId) {
    if (appTogglePending.has(appId)) return;
    const appContainers = getContainersForApp(appId);
    if (appContainers.length === 0) return;

    const anyRunning = appContainers.some((c) => c.State === 'running');
    const action = anyRunning ? 'stop' : 'start';
    const targets = anyRunning
        ? appContainers.filter((c) => c.State === 'running')
        : appContainers.filter((c) => c.State !== 'running');

    appTogglePending.add(appId);
    renderInstalledList();
    if (selectedAppId === appId) await renderInspector();

    try {
        const results = await Promise.allSettled(targets.map((c) =>
            apiFetch(`${API_BASE}/containers/${c.ID}/${action}`, {
                method: 'POST',
                headers: { 'Authorization': authToken }
            }).then((res) => {
                if (!res.ok) throw new Error(`Failed to ${action} ${c.Names || c.ID}`);
            })
        ));

        const failedCount = results.filter((r) => r.status === 'rejected').length;
        if (failedCount > 0) {
            showNotification(`Failed to ${action} ${failedCount} container(s)`, 'error');
        } else {
            showNotification(`App ${action === 'stop' ? 'stopped' : 'started'}`, 'success');
        }
    } catch (error) {
        console.error('Toggle app running error:', error);
        showNotification(`Failed to ${action} app: ${error.message}`, 'error');
    } finally {
        appTogglePending.delete(appId);
        await loadInstalledWorkspace(true);
    }
}

async function startContainer(containerId) {
    try {
        const response = await apiFetch(`${API_BASE}/containers/${containerId}/start`, {
            method: 'POST',
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to start container');

        showNotification('Container started', 'success');
        await loadInstalledWorkspace(true);

    } catch (error) {
        console.error('Start error:', error);
        showNotification(`Failed to start: ${error.message}`, 'error');
    }
}

async function stopContainer(containerId) {
    try {
        const response = await apiFetch(`${API_BASE}/containers/${containerId}/stop`, {
            method: 'POST',
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to stop container');

        showNotification('Container stopped', 'success');
        await loadInstalledWorkspace(true);

    } catch (error) {
        console.error('Stop error:', error);
        showNotification(`Failed to stop: ${error.message}`, 'error');
    }
}

async function deleteContainer(containerId) {
    const ok = typeof window.showConfirm === 'function'
        ? await window.showConfirm(`Delete container "${containerId}"?\n\nThis force-removes the Docker container. App data volumes are not deleted.`)
        : false;
    if (!ok) return;

    try {
        const response = await apiFetch(`${API_BASE}/containers/${containerId}?force=true`, {
            method: 'DELETE',
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to delete container');

        showNotification('Container deleted', 'success');
        await loadInstalledWorkspace(true);

    } catch (error) {
        console.error('Delete error:', error);
        showNotification(`Failed to delete: ${error.message}`, 'error');
    }
}

function findContainerById(containerId) {
    return containersCache.find((container) => container.ID === containerId)
        || containersCache.find((container) => String(container.ID || '').startsWith(containerId));
}

function openLogsModal(containerId) {
    const modal = document.getElementById('container-logs-modal');
    const titleEl = document.getElementById('container-logs-title');
    const metaEl = document.getElementById('container-logs-meta');
    const contentEl = document.getElementById('container-logs-content');

    if (!modal || !titleEl || !metaEl || !contentEl) {
        showNotification('Logs modal not available', 'error');
        return;
    }

    activeLogsContainerId = containerId;

    const container = findContainerById(containerId);
    const displayName = container?.Names || containerId;

    titleEl.textContent = `Container Logs: ${displayName}`;
    metaEl.textContent = `Container ID: ${containerId}`;
    contentEl.textContent = 'Loading logs...';

    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');

    loadContainerLogs(containerId, true);
}

function closeLogsModal() {
    const modal = document.getElementById('container-logs-modal');
    if (!modal) return;

    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    activeLogsContainerId = null;
}

function openTerminalModal(containerId) {
    const modal = document.getElementById('container-terminal-modal');
    const titleEl = document.getElementById('container-terminal-title');
    const metaEl = document.getElementById('container-terminal-meta');
    const outputEl = document.getElementById('container-terminal-output');
    const inputEl = document.getElementById('container-terminal-input');

    if (!modal || !titleEl || !metaEl || !outputEl || !inputEl) {
        showNotification('Terminal modal not available', 'error');
        return;
    }

    activeTerminalContainerId = containerId;
    const container = findContainerById(containerId);
    const displayName = container?.Names || containerId;

    titleEl.textContent = `Container Terminal: ${displayName}`;
    metaEl.textContent = `Container ID: ${containerId}`;
    outputEl.textContent = '# Connected. Enter a shell command and press Run.\n';
    inputEl.value = '';

    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    inputEl.focus();
}

function closeTerminalModal() {
    const modal = document.getElementById('container-terminal-modal');
    if (!modal) return;

    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    activeTerminalContainerId = null;
}

function appendTerminalOutput(text) {
    const outputEl = document.getElementById('container-terminal-output');
    if (!outputEl) return;
    outputEl.textContent += `${text}\n`;
    outputEl.scrollTop = outputEl.scrollHeight;
}

async function runTerminalCommand() {
    if (!activeTerminalContainerId) return;

    const inputEl = document.getElementById('container-terminal-input');
    const runBtn = document.getElementById('container-terminal-run-btn');
    if (!inputEl || !runBtn) return;

    const command = String(inputEl.value || '').trim();
    if (!command) {
        showNotification('Please enter a command', 'error');
        return;
    }

    runBtn.disabled = true;
    appendTerminalOutput(`$ ${command}`);

    try {
        const response = await apiFetch(`${API_BASE}/containers/${activeTerminalContainerId}/exec`, {
            method: 'POST',
            headers: {
                'Authorization': authToken,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                command,
                timeout: 120
            })
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data?.error || 'Failed to run command');
        }

        const output = String(data?.output || '').trim();
        if (output) appendTerminalOutput(output);
        appendTerminalOutput(`[exit ${Number(data?.exit_code ?? 1)}]\n`);
    } catch (error) {
        appendTerminalOutput(`Error: ${error.message}\n`);
    } finally {
        runBtn.disabled = false;
        inputEl.focus();
        inputEl.select();
    }
}

function clearTerminalOutput() {
    const outputEl = document.getElementById('container-terminal-output');
    if (!outputEl) return;
    outputEl.textContent = '';
}

async function loadContainerLogs(containerId, scrollToBottom = false) {
    const metaEl = document.getElementById('container-logs-meta');
    const contentEl = document.getElementById('container-logs-content');
    if (!metaEl || !contentEl) return;

    const requestId = ++activeLogsRequestId;

    try {
        const response = await apiFetch(`${API_BASE}/containers/${containerId}/logs?lines=400`, {
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to fetch logs');

        const data = await response.json();
        if (requestId !== activeLogsRequestId || activeLogsContainerId !== containerId) return;

        contentEl.textContent = data.logs || '(no log output)';
        metaEl.textContent = `Container ID: ${containerId} | Updated: ${new Date().toLocaleTimeString()}`;

        if (scrollToBottom) {
            contentEl.scrollTop = contentEl.scrollHeight;
        }

    } catch (error) {
        if (requestId !== activeLogsRequestId || activeLogsContainerId !== containerId) return;
        console.error('Logs error:', error);
        contentEl.textContent = `Could not read this app's logs.\n\nThe app itself keeps running - only the log view is unavailable.\n\nDetails: ${error.message}`;
        metaEl.textContent = `Container ID: ${containerId} | Error`;
    }
}

function viewLogs(containerId) {
    openLogsModal(containerId);
}

function manageApp(appId) {
    setActiveTab('installed');
    if (installedAppsCache.some((app) => app.app_id === appId)) {
        selectInstalledApp(appId);
    }
}

document.getElementById('refresh-apps-btn')?.addEventListener('click', async () => {
    const composeEl = document.querySelector('.compose-quick-add');
    const isComposeVisible = composeEl && window.getComputedStyle(composeEl).display !== 'none';
    const promises = [loadAvailableApps()];
    if (isComposeVisible) {
        promises.push(loadComposePoolOptions());
    }
    await Promise.all(promises);
});
document.getElementById('refresh-installed-btn')?.addEventListener('click', () => loadInstalledWorkspace(true));
document.getElementById('app-search-input')?.addEventListener('input', (event) => {
    availableSearchQuery = String(event.target?.value || '');
    renderAvailableApps();
});
document.getElementById('app-category-filter')?.addEventListener('change', (event) => {
    availableCategoryFilter = String(event.target?.value || 'all');
    renderAvailableApps();
});
document.getElementById('container-logs-close-btn')?.addEventListener('click', closeLogsModal);
document.getElementById('container-logs-close-x')?.addEventListener('click', closeLogsModal);
document.getElementById('container-logs-refresh-btn')?.addEventListener('click', () => {
    if (activeLogsContainerId) {
        loadContainerLogs(activeLogsContainerId, false);
    }
});
document.getElementById('container-logs-modal')?.addEventListener('click', (event) => {
    if (event.target?.id === 'container-logs-modal') {
        closeLogsModal();
    }
});
document.getElementById('container-terminal-close-btn')?.addEventListener('click', closeTerminalModal);
document.getElementById('container-terminal-close-x')?.addEventListener('click', closeTerminalModal);
document.getElementById('container-terminal-run-btn')?.addEventListener('click', runTerminalCommand);
document.getElementById('container-terminal-clear-btn')?.addEventListener('click', clearTerminalOutput);
document.getElementById('container-terminal-input')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        runTerminalCommand();
    }
});
document.getElementById('compose-app-name')?.addEventListener('input', updateComposeDeployButtonState);
document.getElementById('compose-pool-select')?.addEventListener('change', updateComposeDeployButtonState);
document.getElementById('compose-yaml-input')?.addEventListener('input', updateComposeDeployButtonState);
document.getElementById('compose-deploy-btn')?.addEventListener('click', deployComposeApp);
document.getElementById('compose-refresh-pools-btn')?.addEventListener('click', loadComposePoolOptions);
document.getElementById('toggle-compose-btn')?.addEventListener('click', () => {
    const composeEl = document.querySelector('.compose-quick-add');
    if (!composeEl) return;
    const isHidden = window.getComputedStyle(composeEl).display === 'none';
    composeEl.style.display = isHidden ? 'block' : 'none';

    const toggleBtn = document.getElementById('toggle-compose-btn');
    if (toggleBtn) {
        toggleBtn.classList.toggle('active', isHidden);
    }

    if (isHidden) {
        loadComposePoolOptions();
    }
});
document.getElementById('container-terminal-modal')?.addEventListener('click', (event) => {
    if (event.target?.id === 'container-terminal-modal') {
        closeTerminalModal();
    }
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        closeLogsModal();
        closeTerminalModal();
        closeAllInspectorMenus();
    }
});
// Any click outside an open (...) menu dismisses it. Menu buttons/items call
// stopPropagation, so in-menu clicks never reach this handler.
document.addEventListener('click', closeAllInspectorMenus);

async function pollInstallStatus(appId) {
    const statusEl = document.getElementById('install-progress-status');
    const percentEl = document.getElementById('install-progress-percent');
    const barEl = document.getElementById('install-progress-bar');
    const logEl = document.getElementById('install-log-content');
    const logContainer = document.getElementById('install-log-container');
    const confirmBtn = document.getElementById('confirm-install-btn');

    const poll = async () => {
        try {
            const res = await apiFetch(`${API_BASE}/apps/install/status`, {
                headers: { 'Authorization': authToken }
            });
            if (!res.ok) {
                setTimeout(poll, 2000);
                return;
            }

            const data = await res.json();

            if (data.status === 'idle') {
                setTimeout(poll, 1000);
                return;
            }

            statusEl.textContent = data.message || 'Installing...';
            percentEl.textContent = `${data.progress}%`;
            barEl.style.width = `${data.progress}%`;

            if (data.logs && data.logs.length > 0) {
                logEl.textContent = data.logs.join('\n');
                logContainer.scrollTop = logContainer.scrollHeight;
            }

            if (data.status === 'success') {
                showNotification(`App "${appId}" installed successfully!`, 'success');
                confirmBtn.textContent = 'Done';
                setTimeout(async () => {
                    document.getElementById('install-modal').style.display = 'none';
                    await loadInstalledWorkspace(true);
                    document.getElementById('install-progress-container').style.display = 'none';
                }, 2000);
                return;
            }

            if (data.status === 'error') {
                showNotification(`Installation failed: ${data.message}`, 'error');
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Retry Installation';
                statusEl.textContent = 'Installation failed';
                statusEl.style.color = 'var(--accent-danger)';
                return;
            }

            setTimeout(poll, 1000);

        } catch (error) {
            console.error('Polling error:', error);
            setTimeout(poll, 2000);
        }
    };

    poll();
}

// Default view: installed apps + containers
setActiveTab('installed');
