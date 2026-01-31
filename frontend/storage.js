// AlvaOS Storage Management Logic
// API_BASE is defined in app.js

// Tab switching
document.addEventListener('DOMContentLoaded', () => {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.dataset.tab;

            // Update active states
            tabBtns.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            document.getElementById(`tab-${tabName}`).classList.add('active');

            // Load data for the selected tab
            if (tabName === 'disks') {
                loadDisks();
            } else if (tabName === 'pools') {
                loadPools();
            } else if (tabName === 'shares') {
                loadShares();
            }
        });
    });

    // Initial load
    loadDisks();

    // Event listeners
    const refreshBtn = document.getElementById('refresh-disks-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadDisks);

    const createPoolBtn = document.getElementById('create-pool-btn');
    if (createPoolBtn) createPoolBtn.addEventListener('click', showCreatePoolDialog);

    const createShareBtn = document.getElementById('create-share-btn');
    if (createShareBtn) createShareBtn.addEventListener('click', showCreateShareDialog);
});

// Load Disks
async function loadDisks() {
    const container = document.getElementById('disks-container');
    container.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">Loading disks...</p>';

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/storage/disks`, {
            headers: { 'Authorization': token || '' }
        });

        if (!response.ok) {
            throw new Error('Failed to load disks');
        }

        const data = await response.json();
        displayDisks(data.disks);
    } catch (error) {
        console.error('Error loading disks:', error);
        container.innerHTML = '<p style="text-align: center; color: var(--accent-danger);">Failed to load disks. Check backend connection.</p>';
    }
}

// Display Disks
function displayDisks(disks) {
    const container = document.getElementById('disks-container');

    if (!disks || disks.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-secondary); grid-column: 1/-1;">No disks detected.</p>';
        return;
    }

    container.innerHTML = '';

    disks.forEach(disk => {
        const diskCard = document.createElement('div');
        diskCard.className = 'card';
        if (disk.is_system_disk) {
            diskCard.style.borderLeft = '3px solid var(--accent-warning)';
        }

        // Status indicator
        let statusColor = 'var(--text-secondary)';
        let statusText = 'Unknown';
        if (disk.smart_status === 'healthy') {
            statusColor = 'var(--accent-success)';
            statusText = 'Healthy';
        } else if (disk.smart_status === 'failed') {
            statusColor = 'var(--accent-danger)';
            statusText = 'Failed';
        }

        diskCard.innerHTML = `
            <div class="card-header">
                <div class="card-title">
                    ${disk.is_removable ? '🔌' : '💿'} /dev/${disk.name}
                </div>
                <div style="font-size: 0.8rem; font-weight: 600; color: ${statusColor}; display: flex; align-items: center; gap: 4px;">
                    <div style="width: 8px; height: 8px; border-radius: 50%; background: ${statusColor};"></div>
                    ${statusText}
                </div>
            </div>
            
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px;">
                <div>
                    <span class="setting-label">Model</span>
                    <span class="setting-val">${disk.model}</span>
                </div>
                <div>
                    <span class="setting-label">Size</span>
                    <span class="setting-val">${disk.size}</span>
                </div>
                <div>
                    <span class="setting-label">Serial</span>
                    <span class="setting-val" style="font-size: 0.8rem;">${disk.serial}</span>
                </div>
                <div>
                    <span class="setting-label">Filesystem</span>
                    <span class="setting-val">${disk.fstype || 'None'}</span>
                </div>
                <div style="grid-column: 1 / -1;">
                    <span class="setting-label">Mount Point</span>
                    <span class="setting-val">${disk.mountpoint || 'Not mounted'}</span>
                </div>
            </div>

            <div style="display: flex; gap: 8px; margin-top: auto;">
                ${!disk.is_system_disk ? `
                    <button onclick="wipeDisk('${disk.name}')" class="btn-secondary" 
                        style="flex: 1; border-color: var(--accent-danger); color: var(--accent-danger); font-size: 0.85rem;" 
                        title="Completely erase disk">
                        Wipe
                    </button>
                ` : ''}
                <button onclick="viewDiskDetails('${disk.name}')" class="btn-secondary" 
                    style="flex: 2; font-size: 0.85rem;">
                    Health Details
                </button>
            </div>
            
            ${disk.is_system_disk ? `
                <div style="margin-top: 12px; font-size: 0.75rem; color: var(--accent-warning); display: flex; align-items: center; gap: 4px;">
                    <span>⚠️</span> System Disk - Restricted Actions
                </div>
            ` : ''}
        `;

        container.appendChild(diskCard);
    });
}

// Load Pools
async function loadPools() {
    const container = document.getElementById('pools-container');
    container.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">Loading pools...</p>';

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/storage/pools`, {
            headers: { 'Authorization': token || '' }
        });

        if (!response.ok) {
            throw new Error('Failed to load pools');
        }

        const data = await response.json();
        displayPools(data.pools);
    } catch (error) {
        console.error('Error loading pools:', error);
        container.innerHTML = '<p style="text-align: center; color: var(--accent-danger);">Failed to load pools.</p>';
    }
}

// Display Pools
function displayPools(pools) {
    const container = document.getElementById('pools-container');

    if (!pools || pools.length === 0) {
        container.innerHTML = `
            <div style="padding: 2rem; text-align:center; color: var(--text-secondary); grid-column: 1/-1;">
                No storage pools configured yet. <br> <span style="font-size:0.875rem;">Create a pool to start managing your storage.</span>
            </div>
        `;
        return;
    }

    container.innerHTML = '';

    pools.forEach(pool => {
        const poolCard = document.createElement('div');
        poolCard.className = 'card';

        poolCard.innerHTML = `
            <div class="card-header">
                <div class="card-title">🗄️ ${pool.name}</div>
                <div style="font-size: 0.8rem; font-weight: 600; color: var(--accent-success); display: flex; align-items: center; gap: 4px;">
                    <div style="width: 8px; height: 8px; border-radius: 50%; background: var(--accent-success);"></div>
                    Active
                </div>
            </div>
            
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px;">
                <div>
                    <span class="setting-label">RAID Level</span>
                    <span class="setting-val">${pool.raid_level}</span>
                </div>
                <div>
                    <span class="setting-label">Devices</span>
                    <span class="setting-val">${pool.devices.length}</span>
                </div>
                <div>
                    <span class="setting-label">Total Size</span>
                    <span class="setting-val">${pool.total_size || 'N/A'}</span>
                </div>
                <div>
                    <span class="setting-label">Used</span>
                    <span class="setting-val">${pool.used_size || 'N/A'}</span>
                </div>
                <div style="grid-column: 1 / -1;">
                    <span class="setting-label">Disk Members</span>
                    <span class="setting-val" style="font-size: 0.8rem;">${pool.devices.join(', ')}</span>
                </div>
            </div>

            <div style="display: flex; gap: 8px; margin-top: auto; flex-wrap: wrap;">
                <button onclick="manageSubvolumes('${pool.id}')" class="btn-primary" 
                    style="flex: 1; min-width: 100px; font-size: 0.85rem;">
                    Subvolumes
                </button>
                <button onclick="showExpandPoolDialog('${pool.id}', '${pool.name}')" class="btn-secondary" 
                    style="flex: 1; min-width: 100px; font-size: 0.85rem; border-color: var(--accent-success); color: var(--accent-success);">
                    Expand
                </button>
                <button onclick="deletePool('${pool.id}', '${pool.name}')" class="btn-secondary" 
                    style="flex: 1; min-width: 100px; font-size: 0.85rem; border-color: var(--accent-danger); color: var(--accent-danger);">
                    Delete
                </button>
            </div>
        `;

        container.appendChild(poolCard);
    });
}

// Load Shares
async function loadShares() {
    const container = document.getElementById('shares-container');
    container.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">Loading shares...</p>';

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/storage/shares`, {
            headers: { 'Authorization': token || '' }
        });

        if (!response.ok) {
            throw new Error('Failed to load shares');
        }

        const data = await response.json();
        displayShares(data.shares);
    } catch (error) {
        console.error('Error loading shares:', error);
        container.innerHTML = '<p style="text-align: center; color: var(--accent-danger);">Failed to load shares.</p>';
    }
}

// Display Shares
function displayShares(shares) {
    const container = document.getElementById('shares-container');

    if (!shares || shares.length === 0) {
        container.innerHTML = `
            <div style="padding: 2rem; text-align:center; color: var(--text-secondary); grid-column: 1/-1;">
                No network shares configured yet. <br> <span style="font-size:0.875rem;">Create a share to access your data over the network.</span>
            </div>
        `;
        return;
    }

    container.innerHTML = '';

    shares.forEach(share => {
        const shareCard = document.createElement('div');
        shareCard.className = 'card';

        const protocolIcon = share.protocol === 'nfs' ? '📁' : '🗂️';
        const protocolName = share.protocol.toUpperCase();
        const accessType = share.read_only ? 'Read-Only' : 'Read-Write';

        shareCard.innerHTML = `
            <div class="card-header">
                <div class="card-title">${protocolIcon} ${share.name}</div>
                <div style="font-size: 0.8rem; font-weight: 600; color: var(--accent-success); display: flex; align-items: center; gap: 4px;">
                    <div style="width: 8px; height: 8px; border-radius: 50%; background: var(--accent-success);"></div>
                    Active
                </div>
            </div>
            
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px;">
                <div style="grid-column: 1 / -1;">
                    <span class="setting-label">Export Path</span>
                    <span class="setting-val">${share.path}</span>
                </div>
                <div>
                    <span class="setting-label">Protocol</span>
                    <span class="setting-val">${protocolName}</span>
                </div>
                <div>
                    <span class="setting-label">Access</span>
                    <span class="setting-val">${accessType}</span>
                </div>
                ${share.protocol === 'nfs' ? `
                    <div style="grid-column: 1 / -1;">
                        <span class="setting-label">Allowed Hosts</span>
                        <span class="setting-val" style="font-size: 0.8rem;">${share.allowed_hosts}</span>
                    </div>
                ` : ''}
                ${share.protocol === 'smb' ? `
                    <div style="grid-column: 1 / -1;">
                        <span class="setting-label">Guest Access</span>
                        <span class="setting-val">${share.guest_access ? 'Enabled' : 'Disabled'}</span>
                    </div>
                ` : ''}
            </div>

            <div style="display: flex; gap: 8px; margin-top: auto;">
                <button onclick="showConnectionInfo('${share.id}')" class="btn-primary" 
                    style="flex: 2; font-size: 0.85rem;">
                    Connection Info
                </button>
                <button onclick="deleteShare('${share.id}', '${share.name}')" class="btn-secondary" 
                    style="flex: 1; font-size: 0.85rem; border-color: var(--accent-danger); color: var(--accent-danger);">
                    Delete
                </button>
            </div>
        `;

        container.appendChild(shareCard);
    });
}

// Initialize Disk
async function initializeDisk(diskName) {
    if (!await showConfirm(`Are you sure you want to initialize /dev/${diskName}?\n\nThis will erase all data on the disk!`)) {
        return;
    }

    if (window.showToast) window.showToast('Disk initialization will be implemented in the next phase.', 'warning');
}

// Wipe Disk
async function wipeDisk(diskName) {
    if (!await showConfirm(`Are you sure you want to WIPE /dev/${diskName}?\n\n⚠️ ALL DATA, partitions and file systems will be PERMANENTLY ERASED.\nThis cannot be undone.`)) {
        return;
    }

    const token = localStorage.getItem('alvaos_token');
    try {
        const response = await fetch(`${API_BASE}/storage/disks/${diskName}/wipe`, {
            method: 'POST',
            headers: { 'Authorization': token || '' }
        });

        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Failed to wipe disk');

        alert(result.message);
        loadDisks();
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

// View Disk Details (SMART)
async function viewDiskDetails(diskName) {
    const token = localStorage.getItem('alvaos_token');

    // Create modal immediately for loading state
    const modal = document.createElement('div');
    modal.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0, 0, 0, 0.85); display: flex;
        align-items: center; justify-content: center; z-index: 10000;
    `;

    const panel = document.createElement('div');
    panel.style.cssText = `
        background: var(--bg-surface); border: 1px solid var(--bg-border);
        border-radius: 8px; padding: 2rem; max-width: 800px; width: 90%;
        max-height: 90vh; overflow-y: auto;
    `;

    panel.innerHTML = `<h2 style="color: var(--text-secondary); text-align: center;">Loading SMART data for ${diskName}...</h2>`;
    modal.appendChild(panel);
    document.body.appendChild(modal);

    try {
        const response = await fetch(`${API_BASE}/storage/disks/${diskName}/smart`, {
            headers: { 'Authorization': token || '' }
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Failed to fetch SMART data');

        // Handle case where SMART is not supported but returned 200 (common for USB)
        if (data.error) {
            panel.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
                    <h2 style="margin: 0; color: var(--text-primary);">Disk Health: /dev/${diskName}</h2>
                    <button id="close-modal-btn" style="background: transparent; border: none; color: var(--text-secondary); font-size: 1.5rem; cursor: pointer;">✕</button>
                </div>
                <div style="padding: 2rem; text-align: center; background: var(--bg-primary); border-radius: 8px; border-left: 4px solid var(--accent-warning);">
                    <div style="font-size: 3rem; margin-bottom: 1rem;">ℹ️</div>
                    <h3 style="margin-bottom: 0.5rem;">SMART Monitoring Unavailable</h3>
                    <p style="color: var(--text-secondary);">${data.error}</p>
                </div>
                <button id="close-btn" style="width: 100%; margin-top: 2rem; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">Close</button>
             `;
            const close = () => modal.remove();
            panel.querySelector('#close-modal-btn').onclick = close;
            panel.querySelector('#close-btn').onclick = close;
            return;
        }

        const status = data.smart_status?.passed ? 'Healthy' : 'Warning/Failed';
        const color = data.smart_status?.passed ? 'var(--accent-success)' : 'var(--accent-danger)';

        // Handle NVMe vs ATA structures
        const temp = data.temperature?.current ||
            data.nvme_smart_health_information_log?.temperature ||
            'N/A';
        const hours = data.power_on_time?.hours ||
            data.nvme_smart_health_information_log?.power_on_hours ||
            'N/A';

        const attributes = data.ata_smart_attributes?.table || [];

        panel.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
                <h2 style="margin: 0; color: var(--text-primary);">Disk Health: /dev/${diskName}</h2>
                <button id="close-modal-btn" style="background: transparent; border: none; color: var(--text-secondary); font-size: 1.5rem; cursor: pointer;">✕</button>
            </div>

            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-bottom: 2rem;">
                <div style="background: var(--bg-primary); padding: 1rem; border-radius: 6px; text-align: center; border-bottom: 3px solid ${color};">
                    <div style="font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase;">Status</div>
                    <div style="font-size: 1.25rem; font-weight: 700; color: ${color};">${status}</div>
                </div>
                <div style="background: var(--bg-primary); padding: 1rem; border-radius: 6px; text-align: center;">
                    <div style="font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase;">Temperature</div>
                    <div style="font-size: 1.25rem; font-weight: 700; color: var(--text-primary);">${temp}°C</div>
                </div>
                <div style="background: var(--bg-primary); padding: 1rem; border-radius: 6px; text-align: center;">
                    <div style="font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase;">Power On</div>
                    <div style="font-size: 1.25rem; font-weight: 700; color: var(--text-primary);">${hours} hrs</div>
                </div>
            </div>

            ${attributes.length > 0 ? `
                <h3 style="font-size: 1rem; margin-bottom: 1rem; color: var(--text-primary);">Detailed Attributes</h3>
                <div style="overflow-x: auto;">
                    <table style="width: 100%; border-collapse: collapse; font-size: 0.875rem;">
                        <thead>
                            <tr style="text-align: left; border-bottom: 1px solid var(--bg-border);">
                                <th style="padding: 0.75rem 0.5rem; color: var(--text-secondary);">ID</th>
                                <th style="padding: 0.75rem 0.5rem; color: var(--text-secondary);">Attribute</th>
                                <th style="padding: 0.75rem 0.5rem; color: var(--text-secondary); text-align: right;">Raw Value</th>
                                <th style="padding: 0.75rem 0.5rem; color: var(--text-secondary); text-align: right;">Normalized</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${attributes.map(attr => `
                                <tr style="border-bottom: 1px solid var(--bg-border);">
                                    <td style="padding: 0.75rem 0.5rem; font-family: monospace;">${attr.id}</td>
                                    <td style="padding: 0.75rem 0.5rem;">${attr.name}</td>
                                    <td style="padding: 0.75rem 0.5rem; text-align: right; font-family: monospace;">${attr.raw?.value}</td>
                                    <td style="padding: 0.75rem 0.5rem; text-align: right; font-family: monospace;">${attr.value}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            ` : `
                <div style="padding: 1.5rem; background: var(--bg-primary); border-radius: 6px; text-align: center; color: var(--text-secondary);">
                    No extended attribute table available for this device type.
                </div>
            `}

            <button id="close-btn" style="width: 100%; margin-top: 2rem; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">
                Close
            </button>
        `;

        const close = () => modal.remove();
        panel.querySelector('#close-modal-btn').onclick = close;
        panel.querySelector('#close-btn').onclick = close;

    } catch (error) {
        panel.innerHTML = `<div style="text-align:center; padding: 2rem;">
            <h2 style="color: var(--accent-danger);">Error</h2><p>${error.message}</p>
            <button id="err-close" style="margin-top: 1rem; padding: 0.5rem 1rem; background: var(--accent-danger); color: white; border:none; border-radius:4px; cursor:pointer;">Close</button>
        </div>`;
        panel.querySelector('#err-close').onclick = () => modal.remove();
    }
}

// Show Create Pool Dialog
async function showCreatePoolDialog() {
    // Fetch available disks
    const token = localStorage.getItem('alvaos_token');
    const response = await fetch(`${API_BASE}/storage/disks`, {
        headers: { 'Authorization': token || '' }
    });

    if (!response.ok) {
        alert('Failed to load disks');
        return;
    }

    const data = await response.json();
    const availableDisks = data.disks.filter(d => !d.is_system_disk && d.fstype === 'none');

    if (availableDisks.length === 0) {
        alert('No available disks found.\n\nAll disks are either in use or are system disks.');
        return;
    }

    // Create modal
    const modal = document.createElement('div');
    modal.id = 'pool-wizard-modal';
    modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.8);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    `;

    const wizard = document.createElement('div');
    wizard.style.cssText = `
        background: var(--bg-surface);
        border: 1px solid var(--bg-border);
        border-radius: 8px;
        padding: 2rem;
        max-width: 600px;
        width: 90%;
        max-height: 80vh;
        overflow-y: auto;
    `;

    wizard.innerHTML = `
        <h2 style="margin: 0 0 1.5rem 0; color: var(--text-primary);">Create Storage Pool</h2>
        
        <div style="margin-bottom: 1.5rem;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">Pool Name</label>
            <input type="text" id="pool-name-input" placeholder="e.g., storage-pool" 
                style="width: 100%; padding: 0.75rem; background: var(--bg-primary); border: 1px solid var(--bg-border); color: var(--text-primary); border-radius: 4px;">
        </div>

        <div style="margin-bottom: 1.5rem;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">Select Disks</label>
            <div id="disk-selection" style="max-height: 200px; overflow-y: auto; border: 1px solid var(--bg-border); border-radius: 4px; padding: 0.5rem;">
                ${availableDisks.map(disk => `
                    <label style="display: flex; align-items: center; padding: 0.5rem; cursor: pointer; border-radius: 4px;">
                        <input type="checkbox" value="${disk.path}" class="disk-checkbox" 
                            style="margin-right: 0.75rem; accent-color: var(--accent-primary);">
                        <div>
                            <div style="font-weight: 600;">${disk.name} - ${disk.size}</div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary);">${disk.model}</div>
                        </div>
                    </label>
                `).join('')}
            </div>
            <p style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">
                Selected: <span id="selected-count">0</span> disk(s)
            </p>
        </div>

        <div style="margin-bottom: 1.5rem;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">RAID Level</label>
            <select id="raid-level-select" 
                style="width: 100%; padding: 0.75rem; background: var(--bg-primary); border: 1px solid var(--bg-border); color: var(--text-primary); border-radius: 4px;">
                <option value="single">Single (No Redundancy)</option>
                <option value="raid0">RAID0 (Striping)</option>
                <option value="raid1">RAID1 (Mirroring - 2+ disks)</option>
                <option value="raid10">RAID10 (Striping + Mirroring - 4+ disks)</option>
            </select>
            <p style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;" id="raid-description">
                ⚠️ High Risk. No data protection. If the disk dies, data is lost. Full capacity (100%).
            </p>
        </div>

        <div style="display: flex; gap: 0.75rem; margin-top: 2rem;">
            <button id="cancel-pool-btn" 
                style="flex: 1; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">
                Cancel
            </button>
            <button id="create-pool-confirm-btn" 
                style="flex: 1; background: var(--accent-success); color: white; border: none; padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">
                Create Pool
            </button>
        </div>
    `;

    modal.appendChild(wizard);
    document.body.appendChild(modal);

    // Update selected count
    const checkboxes = wizard.querySelectorAll('.disk-checkbox');
    const selectedCount = wizard.querySelector('#selected-count');
    checkboxes.forEach(cb => {
        cb.addEventListener('change', () => {
            const count = wizard.querySelectorAll('.disk-checkbox:checked').length;
            selectedCount.textContent = count;
        });
    });

    // RAID level descriptions
    const raidSelect = wizard.querySelector('#raid-level-select');
    const raidDesc = wizard.querySelector('#raid-description');
    const raidDescriptions = {
        'single': '⚠️ High Risk. No data protection. If the disk dies, data is lost. Full capacity (100%).',
        'raid0': '⚠️ Very High Risk. High speed, but NO protection. If ONE disk fails, ALL data is lost. Capacity: 100%.',
        'raid1': '✅ Recommended. Mirrors data for safety. Survives 1 disk failure. Capacity: 50% (requires 2+ disks).',
        'raid10': '🚀 Best Performance & Safety. Combines speed of RAID0 with safety of RAID1. Requires 4+ disks. Capacity: 50%.'
    };

    raidSelect.addEventListener('change', () => {
        raidDesc.textContent = raidDescriptions[raidSelect.value];
    });

    // Cancel button
    wizard.querySelector('#cancel-pool-btn').addEventListener('click', () => {
        modal.remove();
    });

    // Create button
    wizard.querySelector('#create-pool-confirm-btn').addEventListener('click', async () => {
        const poolName = wizard.querySelector('#pool-name-input').value.trim();
        const selectedDisks = Array.from(wizard.querySelectorAll('.disk-checkbox:checked')).map(cb => cb.value);
        const raidLevel = raidSelect.value;

        if (!poolName) {
            alert('Please enter a pool name');
            return;
        }

        if (selectedDisks.length === 0) {
            alert('Please select at least one disk');
            return;
        }

        if (raidLevel === 'raid1' && selectedDisks.length < 2) {
            alert('RAID1 requires at least 2 disks');
            return;
        }

        if (raidLevel === 'raid10' && selectedDisks.length < 4) {
            alert('RAID10 requires at least 4 disks');
            return;
        }

        if (!await showConfirm(`Create pool "${poolName}" with ${selectedDisks.length} disk(s) in ${raidLevel.toUpperCase()} mode?\n\n⚠️ This will ERASE all data on the selected disks!`)) {
            return;
        }

        try {
            const response = await fetch(`${API_BASE}/storage/pools`, {
                method: 'POST',
                headers: {
                    'Authorization': token || '',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    name: poolName,
                    devices: selectedDisks,
                    raid_level: raidLevel
                })
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to create pool');
            }

            alert(result.message);
            modal.remove();
            loadPools();
        } catch (error) {
            alert(`Error: ${error.message}`);
        }
    });
}

// Show Expand Pool Dialog
async function showExpandPoolDialog(poolId, poolName) {
    const token = localStorage.getItem('alvaos_token');

    // Fetch available disks
    const disksResponse = await fetch(`${API_BASE}/storage/disks`, {
        headers: { 'Authorization': token || '' }
    });

    if (!disksResponse.ok) {
        alert('Failed to load disks');
        return;
    }

    const disksData = await disksResponse.json();
    const availableDisks = disksData.disks.filter(d => !d.is_system_disk && d.fstype === 'none');

    if (availableDisks.length === 0) {
        alert('No available disks found to expand the pool.');
        return;
    }

    const modal = document.createElement('div');
    modal.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0, 0, 0, 0.8); display: flex;
        align-items: center; justify-content: center; z-index: 10000;
    `;

    const dialog = document.createElement('div');
    dialog.style.cssText = `
        background: var(--bg-surface); border: 1px solid var(--bg-border);
        border-radius: 8px; padding: 2rem; max-width: 500px; width: 90%;
    `;

    dialog.innerHTML = `
        <h2 style="margin-top: 0; color: var(--text-primary);">Expand Pool: ${poolName}</h2>
        <p style="color: var(--text-secondary); font-size: 0.875rem; margin-bottom: 1.5rem;">
            Select one or more disks to add to this pool. Btrfs will immediately increase the total capacity.
        </p>
        
        <div style="max-height: 200px; overflow-y: auto; border: 1px solid var(--bg-border); border-radius: 4px; padding: 0.5rem; margin-bottom: 1.5rem;">
            ${availableDisks.map(disk => `
                <label style="display: flex; align-items: center; padding: 0.5rem; cursor: pointer;">
                    <input type="checkbox" value="${disk.path}" class="expand-disk-checkbox" style="margin-right: 0.75rem;">
                    <div>
                        <div style="font-weight: 600; color: var(--text-primary);">${disk.name} - ${disk.size}</div>
                        <div style="font-size: 0.75rem; color: var(--text-secondary);">${disk.model}</div>
                    </div>
                </label>
            `).join('')}
        </div>
        
        <div style="display: flex; gap: 0.75rem;">
            <button id="cancel-expand-btn" style="flex: 1; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.75rem; border-radius: 4px; cursor: pointer;">Cancel</button>
            <button id="confirm-expand-btn" style="flex: 1; background: var(--accent-success); color: white; border: none; padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">Add Disks</button>
        </div>
    `;

    modal.appendChild(dialog);
    document.body.appendChild(modal);

    dialog.querySelector('#cancel-expand-btn').onclick = () => modal.remove();
    dialog.querySelector('#confirm-expand-btn').onclick = async () => {
        const selectedDisks = Array.from(dialog.querySelectorAll('.expand-disk-checkbox:checked')).map(cb => cb.value);

        if (selectedDisks.length === 0) {
            alert('Please select at least one disk');
            return;
        }

        if (!await showConfirm(`Add ${selectedDisks.length} disk(s) to pool "${poolName}"?\n\n⚠️ DATA ON SELECTED DISKS WILL BE ERASED!`)) {
            return;
        }

        try {
            const response = await fetch(`${API_BASE}/storage/pools/${poolId}/expand`, {
                method: 'POST',
                headers: {
                    'Authorization': token || '',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ devices: selectedDisks })
            });

            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Failed to expand pool');

            alert(result.message);
            modal.remove();
            loadPools();
        } catch (error) {
            alert(`Error: ${error.message}`);
        }
    };
}

// Delete Pool
async function deletePool(poolId, poolName) {
    if (!await showConfirm(`Delete pool "${poolName}"?\n\n⚠️ This will unmount the pool but NOT erase the data.`)) {
        return;
    }

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/storage/pools`, {
            method: 'DELETE',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ pool_id: poolId })
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to delete pool');
        }

        alert(result.message);
        loadPools();
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

// Manage Subvolumes
async function manageSubvolumes(poolId) {
    const token = localStorage.getItem('alvaos_token');
    const response = await fetch(`${API_BASE}/storage/pools/${poolId}/subvolumes`, {
        headers: { 'Authorization': token || '' }
    });

    if (!response.ok) {
        alert('Failed to load subvolumes');
        return;
    }

    const data = await response.json();
    const subvolumes = data.subvolumes || [];

    const modal = document.createElement('div');
    modal.id = 'subvolume-modal';
    modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.8);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    `;

    const panel = document.createElement('div');
    panel.style.cssText = `
        background: var(--bg-surface);
        border: 1px solid var(--bg-border);
        border-radius: 8px;
        padding: 2rem;
        max-width: 600px;
        width: 90%;
        max-height: 80vh;
        overflow-y: auto;
    `;

    panel.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
            <h2 style="margin: 0; color: var(--text-primary);">Manage Subvolumes</h2>
            <button id="close-subvol-btn" style="background: transparent; border: none; color: var(--text-secondary); font-size: 1.5rem; cursor: pointer;">✕</button>
        </div>

        <div style="margin-bottom: 1.5rem;">
            <div style="display: flex; gap: 0.5rem;">
                <input type="text" id="new-subvol-name" placeholder="Subvolume name" 
                    style="flex: 1; padding: 0.75rem; background: var(--bg-primary); border: 1px solid var(--bg-border); color: var(--text-primary); border-radius: 4px;">
                <button id="create-subvol-btn" 
                    style="background: var(--accent-success); color: white; border: none; padding: 0.75rem 1.5rem; border-radius: 4px; cursor: pointer; font-weight: 600;">
                    Create
                </button>
            </div>
        </div>

        <div id="subvolumes-list">
            ${subvolumes.length === 0 ?
            '<p style="text-align: center; color: var(--text-secondary);">No subvolumes yet</p>' :
            subvolumes.map(sv => `
                    <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.75rem; border: 1px solid var(--bg-border); border-radius: 4px; margin-bottom: 0.5rem;">
                        <div>
                            <div style="font-weight: 600;">${sv.name}</div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary); font-family: 'JetBrains Mono', monospace;">${sv.path}</div>
                        </div>
                        <button onclick="deleteSubvolume('${poolId}', '${sv.name}')" 
                            style="background: var(--accent-danger); color: white; border: none; padding: 0.5rem 1rem; border-radius: 4px; cursor: pointer; font-size: 0.875rem;">
                            Delete
                        </button>
                    </div>
                `).join('')
        }
        </div>
    `;

    modal.appendChild(panel);
    document.body.appendChild(modal);

    panel.querySelector('#close-subvol-btn').addEventListener('click', () => {
        modal.remove();
    });

    panel.querySelector('#create-subvol-btn').addEventListener('click', async () => {
        const name = panel.querySelector('#new-subvol-name').value.trim();

        if (!name) {
            alert('Please enter a subvolume name');
            return;
        }

        try {
            const createResponse = await fetch(`${API_BASE}/storage/pools/${poolId}/subvolumes`, {
                method: 'POST',
                headers: {
                    'Authorization': token || '',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ name })
            });

            const result = await createResponse.json();

            if (!createResponse.ok) {
                throw new Error(result.error || 'Failed to create subvolume');
            }

            alert(result.message);
            modal.remove();
            manageSubvolumes(poolId);
        } catch (error) {
            alert(`Error: ${error.message}`);
        }
    });
}

// Delete Subvolume
async function deleteSubvolume(poolId, subvolName) {
    if (!await showConfirm(`Delete subvolume "${subvolName}"?\n\n⚠️ This will delete all data in the subvolume!`)) {
        return;
    }

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/storage/pools/${poolId}/subvolumes`, {
            method: 'DELETE',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name: subvolName })
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to delete subvolume');
        }

        alert(result.message);

        const modal = document.getElementById('subvolume-modal');
        if (modal) {
            modal.remove();
            manageSubvolumes(poolId);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

// Show Create Share Dialog
async function showCreateShareDialog() {
    const token = localStorage.getItem('alvaos_token');

    // Get available paths from backend
    let availablePaths = [];
    try {
        const pathsRes = await fetch(`${API_BASE}/storage/available-paths`, {
            headers: { 'Authorization': token || '' }
        });
        if (pathsRes.ok) {
            const pathsData = await pathsRes.json();
            availablePaths = pathsData.paths;
        }
    } catch (e) {
        console.error('Error fetching available paths:', e);
    }

    if (availablePaths.length === 0) {
        availablePaths = [{ name: 'Default Root', path: '/mnt/alvaos' }];
    }

    // Create modal
    const modal = document.createElement('div');
    modal.id = 'share-wizard-modal';
    modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.8);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    `;

    const wizard = document.createElement('div');
    wizard.style.cssText = `
        background: var(--bg-surface);
        border: 1px solid var(--bg-border);
        border-radius: 8px;
        padding: 2rem;
        max-width: 600px;
        width: 90%;
        max-height: 80vh;
        overflow-y: auto;
    `;

    wizard.innerHTML = `
        <h2 style="margin: 0 0 1.5rem 0; color: var(--text-primary);">Create Network Share</h2>
        
        <div style="margin-bottom: 1.5rem;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">Share Name</label>
            <input type="text" id="share-name-input" placeholder="e.g., documents" 
                style="width: 100%; padding: 0.75rem; background: var(--bg-primary); border: 1px solid var(--bg-border); color: var(--text-primary); border-radius: 4px;">
        </div>

        <div style="margin-bottom: 1.5rem;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">Protocol</label>
            <select id="protocol-select" 
                style="width: 100%; padding: 0.75rem; background: var(--bg-primary); border: 1px solid var(--bg-border); color: var(--text-primary); border-radius: 4px;">
                <option value="smb" selected>SMB/Samba (Windows Compatible)</option>
                <option value="nfs">NFS (Network File System)</option>
            </select>
            <p style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;" id="protocol-description">
                Best for Windows, macOS and general file sharing.
            </p>
        </div>

        <div style="margin-bottom: 1.5rem;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">Share Path (Pool or Subvolume)</label>
            <select id="path-select" 
                style="width: 100%; padding: 0.75rem; background: var(--bg-primary); border: 1px solid var(--bg-border); color: var(--text-primary); border-radius: 4px;">
                ${availablePaths.map(p => `<option value="${p.path}">${p.name} (${p.path})</option>`).join('')}
            </select>
        </div>

        <div style="margin-bottom: 1.5rem;">
            <label style="display: flex; align-items: center; cursor: pointer;">
                <input type="checkbox" id="read-only-checkbox" style="margin-right: 0.75rem; accent-color: var(--accent-primary);">
                <span style="font-weight: 600;">Read-Only Access</span>
            </label>
            <p style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.25rem; margin-left: 1.75rem;">
                Clients can only read files, not modify or delete them.
            </p>
        </div>

        <div id="nfs-options" style="margin-bottom: 1.5rem; display: none;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">Allowed Hosts (NFS)</label>
            <input type="text" id="allowed-hosts-input" value="*" placeholder="* or 192.168.1.0/24" 
                style="width: 100%; padding: 0.75rem; background: var(--bg-primary); border: 1px solid var(--bg-border); color: var(--text-primary); border-radius: 4px;">
            <p style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.25rem;">
                Use * for all hosts, or specify IP/network (e.g., 192.168.1.0/24)
            </p>
        </div>


        <div id="smb-options" style="margin-bottom: 1.5rem;">
            <label style="display: flex; align-items: center; cursor: pointer;">
                <input type="checkbox" id="guest-access-checkbox" style="margin-right: 0.75rem; accent-color: var(--accent-primary);">
                <span style="font-weight: 600;">Allow Guest Access (SMB)</span>
            </label>
            <p style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.25rem; margin-left: 1.75rem;">
                Allow access without authentication (not recommended for sensitive data).
            </p>
        </div>

        <div style="display: flex; gap: 0.75rem; margin-top: 2rem;">
            <button id="cancel-share-btn" 
                style="flex: 1; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">
                Cancel
            </button>
            <button id="create-share-confirm-btn" 
                style="flex: 1; background: var(--accent-success); color: white; border: none; padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">
                Create Share
            </button>
        </div>
    `;

    modal.appendChild(wizard);
    document.body.appendChild(modal);

    // Protocol change handler
    const protocolSelect = wizard.querySelector('#protocol-select');
    const protocolDesc = wizard.querySelector('#protocol-description');
    const nfsOptions = wizard.querySelector('#nfs-options');
    const smbOptions = wizard.querySelector('#smb-options');

    const protocolDescriptions = {
        'nfs': 'Best for Linux/Unix systems. Lightweight and fast.',
        'smb': 'Best for Windows systems. Also works on Linux and macOS.'
    };

    protocolSelect.addEventListener('change', () => {
        const protocol = protocolSelect.value;
        protocolDesc.textContent = protocolDescriptions[protocol];

        if (protocol === 'nfs') {
            nfsOptions.style.display = 'block';
            smbOptions.style.display = 'none';
        } else {
            nfsOptions.style.display = 'none';
            smbOptions.style.display = 'block';
        }
    });

    // Path selection handler
    // Path selection handler (Cleaned up: No custom path logic needed)
    const pathSelect = wizard.querySelector('#path-select');

    // Cancel button
    wizard.querySelector('#cancel-share-btn').addEventListener('click', () => {
        modal.remove();
    });

    // Create button
    wizard.querySelector('#create-share-confirm-btn').addEventListener('click', async () => {
        const shareName = wizard.querySelector('#share-name-input').value.trim();
        const protocol = protocolSelect.value;
        const sharePath = pathSelect.value;

        const readOnly = wizard.querySelector('#read-only-checkbox').checked;
        const allowedHosts = wizard.querySelector('#allowed-hosts-input').value.trim();
        const guestAccess = wizard.querySelector('#guest-access-checkbox').checked;

        if (!shareName) {
            alert('Please enter a share name');
            return;
        }

        if (!sharePath) {
            alert('Please enter a share path');
            return;
        }

        if (!await showConfirm(`Create ${protocol.toUpperCase()} share "${shareName}"?\n\nPath: ${sharePath}\nAccess: ${readOnly ? 'Read-Only' : 'Read-Write'}`)) {
            return;
        }

        try {
            const response = await fetch(`${API_BASE}/storage/shares`, {
                method: 'POST',
                headers: {
                    'Authorization': token || '',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    name: shareName,
                    path: sharePath,
                    protocol: protocol,
                    read_only: readOnly,
                    guest_access: guestAccess,
                    allowed_hosts: allowedHosts
                })
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to create share');
            }

            alert(result.message);
            modal.remove();
            loadShares();
        } catch (error) {
            alert(`Error: ${error.message}`);
        }
    });
}

// Delete Share
async function deleteShare(shareId, shareName) {
    if (!await showConfirm(`Delete share "${shareName}"?\n\n⚠️ This will remove the share configuration.`)) {
        return;
    }

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/storage/shares`, {
            method: 'DELETE',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ share_id: shareId })
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to delete share');
        }

        alert(result.message);
        loadShares();
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

// Show Connection Info
async function showConnectionInfo(shareId) {
    const token = localStorage.getItem('alvaos_token');
    const response = await fetch(`${API_BASE}/storage/shares`, {
        headers: { 'Authorization': token || '' }
    });

    if (!response.ok) {
        alert('Failed to load share information');
        return;
    }

    const data = await response.json();
    const share = data.shares.find(s => s.id === shareId);

    if (!share) {
        alert('Share not found');
        return;
    }

    // Get server IP
    const serverIP = window.location.hostname || 'YOUR_SERVER_IP';

    let instructionsHtml = '';

    if (share.protocol === 'nfs') {
        instructionsHtml = `
            <h4 style="margin-bottom: 0.5rem; color: var(--accent-primary);">🐧 Linux/macOS (NFS)</h4>
            <div style="margin-bottom: 1.5rem;">
                <p style="margin-bottom: 0.5rem; font-size: 0.9rem;">1. Create mount point:</p>
                <div class="code-block" style="background: #1e1e1e; padding: 0.75rem; border-radius: 4px; font-family: monospace; color: #d4d4d4; margin-bottom: 1rem;">
                    sudo mkdir -p /mnt/${share.name}
                </div>
                
                <p style="margin-bottom: 0.5rem; font-size: 0.9rem;">2. Mount the share:</p>
                <div class="code-block" style="background: #1e1e1e; padding: 0.75rem; border-radius: 4px; font-family: monospace; color: #d4d4d4; margin-bottom: 1rem;">
                    sudo mount -t nfs ${serverIP}:${share.path} /mnt/${share.name}
                </div>
                
                <p style="margin-bottom: 0.5rem; font-size: 0.9rem;">3. Auto-mount (/etc/fstab):</p>
                <div class="code-block" style="background: #1e1e1e; padding: 0.75rem; border-radius: 4px; font-family: monospace; color: #d4d4d4;">
                    ${serverIP}:${share.path} /mnt/${share.name} nfs defaults 0 0
                </div>
            </div>
        `;
    } else {
        instructionsHtml = `
            <h4 style="margin-bottom: 0.5rem; color: var(--accent-primary);">🪟 Windows (SMB)</h4>
            <div style="margin-bottom: 1.5rem;">
                <p style="margin-bottom: 0.5rem; font-size: 0.9rem;">Type in File Explorer address bar:</p>
                <div class="code-block" style="background: #1e1e1e; padding: 0.75rem; border-radius: 4px; font-family: monospace; color: #d4d4d4; user-select: text;">
                    \\\\${serverIP}\\${share.name}
                </div>
            </div>

            <h4 style="margin-bottom: 0.5rem; color: var(--accent-primary);">🍎 macOS</h4>
            <div style="margin-bottom: 1.5rem;">
                <p style="margin-bottom: 0.5rem; font-size: 0.9rem;">Finder (Cmd+K):</p>
                <div class="code-block" style="background: #1e1e1e; padding: 0.75rem; border-radius: 4px; font-family: monospace; color: #d4d4d4; user-select: text;">
                    smb://${serverIP}/${share.name}
                </div>
            </div>

            <h4 style="margin-bottom: 0.5rem; color: var(--accent-primary);">🐧 Linux</h4>
            <div style="margin-bottom: 1.5rem;">
                <p style="margin-bottom: 0.5rem; font-size: 0.9rem;">Mount command:</p>
                <div class="code-block" style="background: #1e1e1e; padding: 0.75rem; border-radius: 4px; font-family: monospace; color: #d4d4d4; user-select: text;">
                    sudo mount -t cifs //${serverIP}/${share.name} /mnt/${share.name} -o username=YOUR_USER
                </div>
            </div>
        `;
    }

    const modal = document.createElement('div');
    modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.8);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    `;

    const panel = document.createElement('div');
    panel.style.cssText = `
        background: var(--bg-surface);
        border: 1px solid var(--bg-border);
        border-radius: 8px;
        padding: 2rem;
        max-width: 700px;
        width: 90%;
        max-height: 80vh;
        overflow-y: auto;
    `;

    panel.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
            <h2 style="margin: 0; color: var(--text-primary);">Connection Info: ${share.name}</h2>
            <button id="close-info-btn" style="background: transparent; border: none; color: var(--text-secondary); font-size: 1.5rem; cursor: pointer;">✕</button>
        </div>

        <div style="background: var(--bg-primary); border: 1px solid var(--bg-border); border-radius: 6px; padding: 1.5rem;">
            ${instructionsHtml}
        </div>

        <div style="margin-top: 1.5rem; padding: 1rem; background: var(--bg-primary); border-left: 3px solid var(--accent-primary); border-radius: 4px;">
            <strong>Share Details:</strong><br>
            Protocol: ${share.protocol.toUpperCase()}<br>
            Path: ${share.path}<br>
            Access: ${share.read_only ? 'Read-Only' : 'Read-Write'}<br>
            ${share.protocol === 'smb' && share.guest_access ? 'Guest Access: Enabled<br>' : ''}
        </div>

        <button id="close-btn" 
            style="width: 100%; margin-top: 1.5rem; background: var(--accent-primary); color: white; border: none; padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">
            Close
        </button>
    `;

    modal.appendChild(panel);
    document.body.appendChild(modal);

    panel.querySelector('#close-info-btn').addEventListener('click', () => {
        modal.remove();
    });

    panel.querySelector('#close-btn').addEventListener('click', () => {
        modal.remove();
    });
}

