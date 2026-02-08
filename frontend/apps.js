// AlvaOS Apps Management
const API_BASE = window.location.origin;
let authToken = localStorage.getItem('alvaos_token');

// Tab switching
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tabName = btn.dataset.tab;

        // Update buttons
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        // Update content
        document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
        document.getElementById(`tab-${tabName}`).classList.add('active');

        // Load data for the active tab
        if (tabName === 'store') {
            loadAvailableApps();
        } else if (tabName === 'installed') {
            loadInstalledApps();
        } else if (tabName === 'containers') {
            loadContainers();
        }
    });
});

// Load available apps from catalog
async function loadAvailableApps() {
    const container = document.getElementById('apps-container');

    try {
        const response = await fetch(`${API_BASE}/api/v1/apps/available`, {
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to load apps');

        const data = await response.json();
        const apps = data.apps || [];

        if (apps.length === 0) {
            container.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="empty-state-icon">📦</div>
                    <div>No apps available</div>
                </div>
            `;
            return;
        }

        container.innerHTML = apps.map(app => `
            <div class="app-card" onclick="showAppDetails('${app.id}')">
                <div class="app-icon">${getAppEmoji(app.category)}</div>
                <div class="app-name">${app.name}</div>
                <div class="app-description">${app.description}</div>
                <div class="app-category">${app.category}</div>
                <div class="app-footer">
                    <span style="font-size: 0.8rem; color: var(--text-secondary);">v${app.version}</span>
                    <button class="btn-primary" style="padding: 6px 12px; font-size: 0.85rem;" onclick="event.stopPropagation(); installApp('${app.id}')">
                        Install
                    </button>
                </div>
            </div>
        `).join('');

    } catch (error) {
        console.error('Error loading apps:', error);
        container.innerHTML = `
            <div class="empty-state" style="grid-column: 1/-1;">
                <div class="empty-state-icon">⚠️</div>
                <div>Failed to load apps</div>
                <p style="font-size: 0.85rem; margin-top: 8px; color: var(--accent-danger);">${error.message}</p>
            </div>
        `;
    }
}

// Load installed apps
async function loadInstalledApps() {
    const container = document.getElementById('installed-container');

    try {
        const response = await fetch(`${API_BASE}/api/v1/apps/installed`, {
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to load installed apps');

        const data = await response.json();
        const apps = data.apps || [];

        if (apps.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">📭</div>
                    <div>No apps installed yet</div>
                    <p style="font-size: 0.85rem; margin-top: 8px;">Install apps from the App Store tab</p>
                </div>
            `;
            return;
        }

        container.innerHTML = `
            <div class="dashboard-grid">
                ${apps.map(app => `
                    <div class="card">
                        <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 12px;">
                            <div>
                                <div style="font-size: 1.05rem; font-weight: 600; margin-bottom: 4px;">${app.name}</div>
                                <div style="font-size: 0.8rem; color: var(--text-secondary);">
                                    Installed: ${new Date(app.installed_at).toLocaleDateString()}
                                </div>
                            </div>
                        </div>
                        <div class="setting-group">
                            <div class="setting-label">Storage Path</div>
                            <div class="setting-val" style="font-size: 0.8rem;">${app.storage_path}</div>
                        </div>
                        <div style="display: flex; gap: 8px; margin-top: 12px;">
                            <button class="btn-secondary" style="flex: 1; padding: 6px 12px; font-size: 0.85rem;" onclick="manageApp('${app.app_id}')">
                                Manage
                            </button>
                            <button class="btn-icon btn-danger" onclick="uninstallApp('${app.app_id}')">
                                🗑️ Uninstall
                            </button>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;

    } catch (error) {
        console.error('Error loading installed apps:', error);
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">⚠️</div>
                <div>Failed to load installed apps</div>
                <p style="font-size: 0.85rem; margin-top: 8px; color: var(--accent-danger);">${error.message}</p>
            </div>
        `;
    }
}

// Load Docker containers
async function loadContainers() {
    const wrapper = document.getElementById('containers-table-wrapper');

    try {
        const response = await fetch(`${API_BASE}/api/v1/containers`, {
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to load containers');

        const data = await response.json();
        const containers = data.containers || [];

        if (containers.length === 0) {
            wrapper.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🐳</div>
                    <div>No containers running</div>
                    <p style="font-size: 0.85rem; margin-top: 8px;">Install an app to create containers</p>
                </div>
            `;
            return;
        }

        wrapper.innerHTML = `
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
                    ${containers.map(container => {
            const isRunning = container.State === 'running';
            const name = container.Names || container.ID.substring(0, 12);
            const image = container.Image || 'unknown';
            const ports = container.Ports || 'none';

            return `
                            <tr>
                                <td><span class="mono-text" style="font-size: 0.85rem;">${name}</span></td>
                                <td>
                                    <span class="container-status ${isRunning ? 'running' : 'stopped'}">
                                        <span class="status-dot ${isRunning ? '' : 'warning'}"></span>
                                        ${container.State}
                                    </span>
                                </td>
                                <td style="font-size: 0.85rem;">${image}</td>
                                <td style="font-size: 0.85rem;">${ports}</td>
                                <td>
                                    <div class="container-actions">
                                        ${isRunning ?
                    `<button class="btn-icon" onclick="stopContainer('${container.ID}')">⏸️ Stop</button>` :
                    `<button class="btn-icon" onclick="startContainer('${container.ID}')">▶️ Start</button>`
                }
                                        <button class="btn-icon" onclick="viewLogs('${container.ID}')">📄 Logs</button>
                                        <button class="btn-icon btn-danger" onclick="deleteContainer('${container.ID}')">🗑️</button>
                                    </div>
                                </td>
                            </tr>
                        `;
        }).join('')}
                </tbody>
            </table>
        `;

    } catch (error) {
        console.error('Error loading containers:', error);
        wrapper.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">⚠️</div>
                <div>Failed to load containers</div>
                <p style="font-size: 0.85rem; margin-top: 8px; color: var(--accent-danger);">${error.message}</p>
            </div>
        `;
    }
}

// Helper: Get emoji for app category
function getAppEmoji(category) {
    const emojis = {
        'Productivity': '📝',
        'Media': '🎬',
        'Development': '💻',
        'Smart Home': '🏠',
        'Network': '🌐',
        'Security': '🔒',
        'Other': '📦'
    };
    return emojis[category] || '📦';
}

// Show app details (placeholder for future modal)
function showAppDetails(appId) {
    console.log('Show details for:', appId);
    // TODO: Implement app details modal
}

// Install app (simplified - will need wizard in future)
async function installApp(appId) {
    // For now, show a simple prompt
    // TODO: Implement proper installation wizard
    const poolPath = prompt('Enter pool path (e.g., /mnt/alvaos/main-pool):');
    if (!poolPath) return;

    try {
        const response = await fetch(`${API_BASE}/api/v1/apps/install`, {
            method: 'POST',
            headers: {
                'Authorization': authToken,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                app_id: appId,
                pool_path: poolPath
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Installation failed');
        }

        showNotification('App installed successfully!', 'success');
        loadInstalledApps();

    } catch (error) {
        console.error('Installation error:', error);
        showNotification(`Installation failed: ${error.message}`, 'error');
    }
}

// Uninstall app
async function uninstallApp(appId) {
    if (!confirm(`Are you sure you want to uninstall ${appId}?`)) return;

    const keepData = confirm('Keep app data? (Click OK to keep, Cancel to delete)');

    try {
        const response = await fetch(`${API_BASE}/api/v1/apps/${appId}`, {
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
        loadInstalledApps();
        loadContainers();

    } catch (error) {
        console.error('Uninstallation error:', error);
        showNotification(`Uninstallation failed: ${error.message}`, 'error');
    }
}

// Container actions
async function startContainer(containerId) {
    try {
        const response = await fetch(`${API_BASE}/api/v1/containers/${containerId}/start`, {
            method: 'POST',
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to start container');

        showNotification('Container started', 'success');
        loadContainers();

    } catch (error) {
        console.error('Start error:', error);
        showNotification(`Failed to start: ${error.message}`, 'error');
    }
}

async function stopContainer(containerId) {
    try {
        const response = await fetch(`${API_BASE}/api/v1/containers/${containerId}/stop`, {
            method: 'POST',
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to stop container');

        showNotification('Container stopped', 'success');
        loadContainers();

    } catch (error) {
        console.error('Stop error:', error);
        showNotification(`Failed to stop: ${error.message}`, 'error');
    }
}

async function deleteContainer(containerId) {
    if (!confirm('Are you sure you want to delete this container?')) return;

    try {
        const response = await fetch(`${API_BASE}/api/v1/containers/${containerId}?force=true`, {
            method: 'DELETE',
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to delete container');

        showNotification('Container deleted', 'success');
        loadContainers();

    } catch (error) {
        console.error('Delete error:', error);
        showNotification(`Failed to delete: ${error.message}`, 'error');
    }
}

async function viewLogs(containerId) {
    try {
        const response = await fetch(`${API_BASE}/api/v1/containers/${containerId}/logs?lines=100`, {
            headers: { 'Authorization': authToken }
        });

        if (!response.ok) throw new Error('Failed to fetch logs');

        const data = await response.json();
        alert(`Container Logs:\n\n${data.logs}`);
        // TODO: Implement proper logs modal

    } catch (error) {
        console.error('Logs error:', error);
        showNotification(`Failed to fetch logs: ${error.message}`, 'error');
    }
}

function manageApp(appId) {
    // Switch to containers tab
    document.querySelector('.tab-btn[data-tab="containers"]').click();
}

// Refresh buttons
document.getElementById('refresh-apps-btn')?.addEventListener('click', loadAvailableApps);
document.getElementById('refresh-containers-btn')?.addEventListener('click', loadContainers);

// Initial load
loadAvailableApps();
