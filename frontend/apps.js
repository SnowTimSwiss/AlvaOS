// AlvaOS Apps Management
// API_BASE is defined in app.js
let authToken = localStorage.getItem('alvaos_token');

let installedAppsCache = [];
let containersCache = [];
let selectedAppId = null;
const appDetailsCache = {};
let activeLogsContainerId = null;
let activeLogsRequestId = 0;
let containersLoaded = false;
let containersLoading = false;
let installedLoadRequestId = 0;

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

function formatDate(value) {
    if (!value) return 'Unknown';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Unknown';
    return date.toLocaleString();
}

function getAppEmoji(category) {
    const emojis = {
        'Productivity': '\uD83D\uDCDD',
        'Media': '\uD83C\uDFAC',
        'Development': '\uD83D\uDCBB',
        'Smart Home': '\uD83C\uDFE0',
        'Network': '\uD83C\uDF10',
        'Security': '\uD83D\uDD12',
        'Other': '\uD83D\uDCE6'
    };
    return emojis[category] || '\uD83D\uDCE6';
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
    const response = await fetch(`${API_BASE}/apps/installed`, {
        headers: { 'Authorization': authToken }
    });

    if (!response.ok) throw new Error('Failed to load installed apps');

    const data = await response.json();
    return data.apps || [];
}

async function fetchContainersData() {
    const response = await fetch(`${API_BASE}/containers`, {
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
        const response = await fetch(`${API_BASE}/apps/available/${appId}`, {
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
        const preferred = schemaPorts.find((item) =>
            String(item?.description || '').toLowerCase().includes('web')
        ) || schemaPorts[0];

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
                    <div class="empty-state-icon">...</div>
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
                                    <button class="btn-icon" onclick="viewLogs('${escapeHtml(container.ID)}')">Logs</button>
                                    <button class="btn-icon btn-danger" onclick="deleteContainer('${escapeHtml(container.ID)}')">Delete</button>
                                </div>
                            </td>
                        </tr>
                    `;
                }).join('')}
            </tbody>
        </table>
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
    if (!container) return;
    const requestId = ++installedLoadRequestId;

    container.innerHTML = `
        <div class="empty-state">
            <div class="empty-state-icon">...</div>
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
                    <div class="empty-state-icon">...</div>
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
        console.error('Error loading installed workspace:', error);
        containersLoaded = false;
        containersLoading = false;
        containersCache = [];
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">!</div>
                <div>Failed to load installed apps</div>
                <p style="font-size: 0.85rem; margin-top: 8px; color: var(--accent-danger);">${escapeHtml(error.message)}</p>
            </div>
        `;
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
    if (!container) return;

    try {
        const response = await fetch(`${API_BASE}/apps/available`, {
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to load apps');

        const data = await response.json();
        const apps = data.apps || [];

        if (apps.length === 0) {
            container.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="empty-state-icon">...</div>
                    <div>No apps available</div>
                </div>
            `;
            return;
        }

        container.innerHTML = apps.map((app) => `
            <div class="app-card" onclick="showAppDetails('${escapeHtml(app.id)}')">
                <div class="app-icon">${getAppEmoji(app.category)}</div>
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

    } catch (error) {
        console.error('Error loading apps:', error);
        container.innerHTML = `
            <div class="empty-state" style="grid-column: 1/-1;">
                <div class="empty-state-icon">!</div>
                <div>Failed to load apps</div>
                <p style="font-size: 0.85rem; margin-top: 8px; color: var(--accent-danger);">${escapeHtml(error.message)}</p>
            </div>
        `;
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
    closeBtns.forEach(btn => {
        btn.onclick = () => {
            modal.style.display = 'none';
        };
    });

    try {
        // Fetch app details
        const appRes = await fetch(`${API_BASE}/apps/available/${appId}`, {
            headers: { 'Authorization': authToken }
        });
        if (!appRes.ok) throw new Error('Failed to load app details');
        const app = await appRes.json();

        // Populate info
        document.getElementById('install-app-name').textContent = app.name || appId;
        document.getElementById('install-app-version').textContent = `Version: ${app.version || 'latest'}`;
        document.getElementById('install-app-desc').textContent = app.description || '';
        document.getElementById('install-app-icon').textContent = getAppEmoji(app.category);

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
        const poolsRes = await fetch(`${API_BASE}/storage/pools`, {
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
                const installRes = await fetch(`${API_BASE}/apps/install`, {
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
    console.log('Show details for:', appId);
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
        const response = await fetch(`${API_BASE}/apps/${appId}`, {
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

async function startContainer(containerId) {
    try {
        const response = await fetch(`${API_BASE}/containers/${containerId}/start`, {
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
        const response = await fetch(`${API_BASE}/containers/${containerId}/stop`, {
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
        const response = await fetch(`${API_BASE}/containers/${containerId}?force=true`, {
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

async function loadContainerLogs(containerId, scrollToBottom = false) {
    const metaEl = document.getElementById('container-logs-meta');
    const contentEl = document.getElementById('container-logs-content');
    if (!metaEl || !contentEl) return;

    const requestId = ++activeLogsRequestId;

    try {
        const response = await fetch(`${API_BASE}/containers/${containerId}/logs?lines=400`, {
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
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        closeLogsModal();
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
            const res = await fetch(`${API_BASE}/apps/install/status`, {
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

