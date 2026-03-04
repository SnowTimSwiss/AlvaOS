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
    if (window.alert) window.alert(message);
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
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

function setStoreMeta(message) {
    const metaEl = document.getElementById('apps-store-meta');
    if (metaEl) metaEl.textContent = message;
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

    container.innerHTML = filteredApps.map((app) => `
        <div class="app-card" onclick="showAppDetails('${escapeHtml(app.id)}')">
            <div class="app-icon">${getAppIcon(app)}</div>
            <div class="app-name">${escapeHtml(app.name)}</div>
            <div class="app-description">${escapeHtml(app.description)}</div>
            <div class="app-category">${escapeHtml(app.category)}</div>
            <div class="app-footer">
                <span style="font-size: 0.8rem; color: var(--text-secondary);">v${escapeHtml(app.version)}</span>
                <button class="btn-primary" style="padding: 6px 12px; font-size: 0.85rem;" onclick="event.stopPropagation(); installApp('${escapeHtml(app.id)}')">
                    Install
                </button>
            </div>
        </div>
    `).join('');
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
        'Development': 'code-2',
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
        const iconUrl = normalizeIconPath(appOrCategory.icon);
        if (iconUrl) {
            const appName = String(appOrCategory.name || appOrCategory.id || 'App');
            return `
                <img
                    class="app-icon-image"
                    src="${escapeHtml(iconUrl)}"
                    alt="${escapeHtml(appName)} icon"
                    loading="lazy"
                    decoding="async">
            `;
        }
        return getCategoryIconMarkup(appOrCategory.category);
    }

    return getCategoryIconMarkup(appOrCategory);
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
        const appContainers = getContainersForApp(app.app_id);
        const running = appContainers.filter((c) => c.State === 'running').length;
        const total = appContainers.length;
        const statusText = containersLoaded
            ? `${running}/${total} containers running`
            : (containersLoading ? 'Loading containers...' : 'Container status unavailable');

        return `
            <button class="app-list-item ${selectedAppId === app.app_id ? 'active' : ''}" onclick="selectInstalledApp('${escapeHtml(app.app_id)}')">
                <div style="font-weight: 600;">${escapeHtml(app.name || app.app_id)}</div>
                <div class="app-count">${statusText}</div>
            </button>
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

    if (selectedAppId !== selectedAtRender) {
        return;
    }

    const webUiUrl = buildWebUiUrl(selected.app_id, appDetails, appContainers);

    inspector.innerHTML = `
        <div class="inspector-card">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:8px;">
                <div>
                    <div style="font-size:1.2rem; font-weight:600;">${escapeHtml(selected.name || selected.app_id)}</div>
                    <div class="inspector-value" style="color: var(--text-secondary);">${escapeHtml(selected.app_id)}</div>
                </div>
                <span class="container-status ${runningCount > 0 ? 'running' : 'stopped'}">${runningCount > 0 ? 'Running' : 'Stopped'}</span>
            </div>

            <div class="inspector-actions">
                ${webUiUrl ? `<a class="btn-link" href="${escapeHtml(webUiUrl)}" target="_blank" rel="noopener noreferrer">Open Web UI</a>` : '<button class="btn-secondary" disabled style="opacity:0.6; cursor:not-allowed;">No Web UI detected</button>'}
                <button class="btn-icon" onclick="updateApp('${escapeHtml(selected.app_id)}')">Update</button>
                <button class="btn-icon btn-danger" onclick="uninstallApp('${escapeHtml(selected.app_id)}')">Uninstall</button>
            </div>

            <div class="inspector-grid">
                <div>
                    <div class="inspector-label">Installed</div>
                    <div class="inspector-value">${escapeHtml(formatDate(selected.installed_at))}</div>
                </div>
                <div>
                    <div class="inspector-label">Storage Path</div>
                    <div class="inspector-value mono-text">${escapeHtml(selected.storage_path || '-')}</div>
                </div>
                <div>
                    <div class="inspector-label">Pool</div>
                    <div class="inspector-value mono-text">${escapeHtml(selected.pool_path || '-')}</div>
                </div>
                <div>
                    <div class="inspector-label">Parent Subvolume</div>
                    <div class="inspector-value mono-text">${escapeHtml(selected.parent_subvolume || '-')}</div>
                </div>
            </div>

            <div style="font-size: 0.95rem; font-weight:600; margin: 16px 0 10px;">Containers${containersLoaded ? ` (${appContainers.length})` : ''}</div>
            ${containersLoaded ? renderContainerTable(appContainers) : `
                <div class="empty-state" style="padding: 1.5rem 1rem;">
                    <div>Loading container data...</div>
                </div>
            `}
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
                ${containers.map((container) => {
                    const isRunning = container.State === 'running';
                    const name = container.Names || (container.ID || '').substring(0, 12);
                    const image = container.Image || 'unknown';
                    const ports = container.Ports || 'none';

                    return `
                        <tr>
                            <td><span class="mono-text" style="font-size: 0.85rem;">${escapeHtml(name)}</span></td>
                            <td>
                                <span class="container-status ${isRunning ? 'running' : 'stopped'}">${escapeHtml(container.State || 'unknown')}</span>
                            </td>
                            <td style="font-size: 0.85rem;">${escapeHtml(image)}</td>
                            <td style="font-size: 0.85rem;">${escapeHtml(ports)}</td>
                            <td>
                                <div class="container-actions">
                                    ${isRunning
                                        ? `<button class="btn-icon" onclick="stopContainer('${escapeHtml(container.ID)}')">Stop</button>`
                                        : `<button class="btn-icon" onclick="startContainer('${escapeHtml(container.ID)}')">Start</button>`}
                                    <button class="btn-icon" onclick="openTerminalModal('${escapeHtml(container.ID)}')">Terminal</button>
                                    <button class="btn-icon" onclick="viewLogs('${escapeHtml(container.ID)}')">Logs</button>
                                    <button class="btn-icon btn-danger" onclick="deleteContainer('${escapeHtml(container.ID)}')">Delete</button>
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
    closeBtns.forEach(btn => {
        btn.onclick = () => {
            modal.style.display = 'none';
        };
    });

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
                const defaultValue = String(entry?.default ?? '');
                const isSecretField = /(password|token|secret|key)/i.test(key);

                return `
                    <div class="setting-group" style="margin-bottom: 0;">
                        <label class="setting-label">${escapeHtml(key)}</label>
                        ${description ? `<p style="font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 6px;">${escapeHtml(description)}</p>` : ''}
                        <input
                            id="install-env-${idx}"
                            data-env-key="${escapeHtml(key)}"
                            type="${isSecretField ? 'password' : 'text'}"
                            value="${escapeHtml(defaultValue)}"
                            placeholder="${escapeHtml(defaultValue || key)}"
                            style="width: 100%; padding: 10px;">
                    </div>
                `;
            }).join('');
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
            document.querySelectorAll('#install-env-fields [data-env-key]').forEach((inputEl) => {
                const key = inputEl.getAttribute('data-env-key');
                if (!key) return;
                const value = inputEl.value;
                if (value !== null && value !== undefined && value !== '') {
                    environmentVars[key] = value;
                }
            });

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
    const ok = window.confirm(`Update "${appId}" now?\nThis pulls new images and recreates app containers.`);
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
    if (!confirm('Are you sure you want to delete this container?')) return;

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
        contentEl.textContent = `Failed to fetch logs:\n${error.message}`;
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

document.getElementById('refresh-apps-btn')?.addEventListener('click', loadAvailableApps);
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
document.getElementById('container-terminal-run-btn')?.addEventListener('click', runTerminalCommand);
document.getElementById('container-terminal-clear-btn')?.addEventListener('click', clearTerminalOutput);
document.getElementById('container-terminal-input')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        runTerminalCommand();
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
    }
});

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


