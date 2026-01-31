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
        container.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">No disks detected.</p>';
        return;
    }

    container.innerHTML = '';

    disks.forEach(disk => {
        const diskCard = document.createElement('div');
        diskCard.className = 'disk-card';
        diskCard.style.cssText = `
            background: var(--bg-surface);
            border: 1px solid var(--bg-border);
            border-radius: 6px;
            padding: 1rem;
            margin-bottom: 1rem;
            ${disk.is_system_disk ? 'border-left: 3px solid var(--accent-warning);' : ''}
        `;

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
            <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.75rem;">
                <div>
                    <h4 style="margin: 0; font-size: 1.125rem; color: var(--text-primary);">
                        💿 /dev/${disk.name}
                        ${disk.is_system_disk ? '<span style="background: var(--accent-warning); color: var(--bg-primary); padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; margin-left: 0.5rem;">SYSTEM</span>' : ''}
                    </h4>
                    <p style="margin: 0.25rem 0 0 0; color: var(--text-secondary); font-size: 0.875rem;">
                        ${disk.model} • ${disk.size}
                    </p>
                </div>
                <div style="text-align: right;">
                    <span style="color: ${statusColor}; font-weight: 600; font-size: 0.875rem;">● ${statusText}</span>
                </div>
            </div>
            
            <div class="info-list" style="margin-top: 0.75rem;">
                <div class="info-row" style="padding: 0.5rem 0; border-bottom: 1px solid var(--bg-border);">
                    <span class="metric-label">Serial</span>
                    <span class="metric-value mono" style="font-size: 0.875rem;">${disk.serial}</span>
                </div>
                <div class="info-row" style="padding: 0.5rem 0; border-bottom: 1px solid var(--bg-border);">
                    <span class="metric-label">Filesystem</span>
                    <span class="metric-value">${disk.fstype || 'none'}</span>
                </div>
                <div class="info-row" style="padding: 0.5rem 0;">
                    <span class="metric-label">Mount Point</span>
                    <span class="metric-value mono" style="font-size: 0.875rem;">${disk.mountpoint || 'Not mounted'}</span>
                </div>
            </div>

            ${disk.partitions && disk.partitions.length > 0 ? `
                <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--bg-border);">
                    <p style="font-size: 0.875rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                        <strong>Partitions (${disk.partitions.length}):</strong>
                    </p>
                    ${disk.partitions.map(p => `
                        <div style="font-size: 0.875rem; color: var(--text-secondary); margin-left: 1rem; margin-bottom: 0.25rem;">
                            • ${p.name} - ${p.size} ${p.fstype !== 'none' ? `(${p.fstype})` : ''}
                        </div>
                    `).join('')}
                </div>
            ` : ''}

            <div style="margin-top: 1rem; display: flex; gap: 0.5rem;">
                ${!disk.is_system_disk && disk.fstype === 'none' ? `
                    <button onclick="initializeDisk('${disk.name}')" 
                        style="flex: 1; background: var(--accent-primary); color: white; border: none; padding: 0.5rem; border-radius: 4px; cursor: pointer; font-size: 0.875rem;">
                        Initialize for Storage
                    </button>
                ` : ''}
                <button onclick="viewDiskDetails('${disk.name}')" 
                    style="flex: 1; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.5rem; border-radius: 4px; cursor: pointer; font-size: 0.875rem;">
                    View Details
                </button>
            </div>
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

        if (!data.pools || data.pools.length === 0) {
            container.innerHTML = `
                <p style="text-align: center; color: var(--text-secondary);">No storage pools configured yet.</p>
                <p style="text-align: center; color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
                    Create a pool to start managing your storage.
                </p>
            `;
        } else {
            // TODO: Display pools
            container.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">Pool display coming soon...</p>';
        }
    } catch (error) {
        console.error('Error loading pools:', error);
        container.innerHTML = '<p style="text-align: center; color: var(--accent-danger);">Failed to load pools.</p>';
    }
}

// Load Shares
async function loadShares() {
    const container = document.getElementById('shares-container');
    container.innerHTML = `
        <p style="text-align: center; color: var(--text-secondary);">No network shares configured yet.</p>
        <p style="text-align: center; color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
            Create a share to access your data over the network.
        </p>
    `;
}

// Initialize Disk
function initializeDisk(diskName) {
    if (!confirm(`Are you sure you want to initialize /dev/${diskName}?\n\nThis will erase all data on the disk!`)) {
        return;
    }

    alert('Disk initialization will be implemented in the next phase.');
    // TODO: Implement disk initialization
}

// View Disk Details
function viewDiskDetails(diskName) {
    alert(`Detailed SMART information for /dev/${diskName} will be shown here.\n\nComing in the next phase!`);
    // TODO: Implement disk details view with SMART data
}

// Show Create Pool Dialog
function showCreatePoolDialog() {
    alert('Pool creation wizard will be implemented in the next phase.\n\nYou will be able to:\n- Select disks\n- Choose RAID level\n- Name your pool');
    // TODO: Implement pool creation wizard
}

// Show Create Share Dialog
function showCreateShareDialog() {
    alert('Share creation wizard will be implemented in the next phase.\n\nYou will be able to:\n- Choose NFS or SMB\n- Select path\n- Configure permissions');
    // TODO: Implement share creation wizard
}
