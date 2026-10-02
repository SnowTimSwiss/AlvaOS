// AlvaOS Storage Management Logic
// API_BASE is defined in app.js

const IGNORED_DETECTED_POOLS_KEY = 'alvaos_ignored_detected_pools';
let storageDisksCache = [];

function showNotification(message, type = 'info') {
    if (window.showToast) {
        window.showToast(message, type);
    } else {
        console.log(`[${type}] ${message}`);
    }
}

function showError(message) {
    showNotification(message, 'error');
}

function showSuccess(message) {
    showNotification(message, 'success');
}

function diskDetailsForConfirm(diskName) {
    const disk = storageDisksCache.find((item) => item.name === diskName || item.path === `/dev/${diskName}`) || {};
    return [
        { label: 'Device', value: disk.path || `/dev/${diskName}` },
        { label: 'Size', value: disk.size || 'Unknown' },
        { label: 'Model', value: disk.model || 'Unknown' },
        { label: 'Serial', value: disk.serial || 'Unknown' }
    ];
}

async function confirmDanger(message, requireText, options = {}) {
    if (typeof window.showConfirm === 'function') {
        return await window.showConfirm(message, {
            danger: true,
            requireText: options.requireCheckbox ? undefined : requireText,
            requireCheckbox: options.requireCheckbox,
            confirmLabel: options.confirmLabel || 'Confirm',
            warning: options.warning || 'This operation cannot be undone.',
            details: options.details || []
        });
    }
    return false;
}

async function apiFetch(url, options = {}) {
    const response = await window.fetch(url, options);
    if (response.status === 401) {
        localStorage.removeItem('alvaos_token');
        window.location.href = '/login.html';
    }
    return response;
}

function getIgnoredDetectedPools() {
    try {
        const raw = localStorage.getItem(IGNORED_DETECTED_POOLS_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? new Set(parsed.map((v) => String(v))) : new Set();
    } catch {
        return new Set();
    }
}

function saveIgnoredDetectedPools(setValue) {
    const values = Array.from(setValue || []).map((v) => String(v)).filter(Boolean);
    localStorage.setItem(IGNORED_DETECTED_POOLS_KEY, JSON.stringify(values));
}

function ignoreDetectedPool(poolId) {
    if (!poolId) return;
    const ignored = getIgnoredDetectedPools();
    ignored.add(String(poolId));
    saveIgnoredDetectedPools(ignored);
    if (window.showToast) window.showToast('Pool detection ignored for now.', 'info');
    loadPools();
}

function unignoreDetectedPool(poolId) {
    if (!poolId) return;
    const ignored = getIgnoredDetectedPools();
    if (ignored.delete(String(poolId))) {
        saveIgnoredDetectedPools(ignored);
    }
}

async function importDetectedPool(poolId, poolName) {
    if (!poolId) return;
    if (!await showConfirm(`Import existing pool "${poolName || poolId}"?\n\nThis will mount it and manage it in AlvaOS.`)) {
        return;
    }

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await apiFetch(`${API_BASE}/storage/pools/import`, {
            method: 'POST',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                pool_id: poolId,
                pool_name: poolName || undefined
            })
        });

        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Failed to import pool');

        unignoreDetectedPool(poolId);
        if (window.showToast) window.showToast(result.message || 'Pool imported', 'success');
        showStorageTab('pools');
    } catch (error) {
        showError(`Error: ${error.message}`);
    }
}

// Tab switching. The tab is kept in the URL (#disks), so a link or a reload
// lands on the same tab. Pools come first: that is where the data lives.
const STORAGE_TABS = ['pools', 'disks', 'shares', 'users'];

// #pool=<id> opens one pool's detail view inside the Pools tab.
function showStorageTab(tabName) {
    const poolId = String(tabName || '').startsWith('pool=') ? decodeURIComponent(tabName.slice(5)) : '';
    const name = poolId ? 'pools' : (STORAGE_TABS.includes(tabName) ? tabName : 'pools');
    const hash = poolId ? `pool=${encodeURIComponent(poolId)}` : name;
    document.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.tab-content').forEach((c) => c.classList.toggle('active', c.id === `tab-${name}`));
    if (location.hash.slice(1) !== hash) {
        history.replaceState(null, '', hash === 'pools' ? location.pathname : `#${hash}`);
    }
    if (name === 'disks') {
        loadDisks();
    } else if (name === 'pools') {
        openedPoolId = poolId;
        loadPools();
    } else if (name === 'shares') {
        loadShares();
    } else if (name === 'users' && typeof loadUsers === 'function') {
        loadUsers();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.tab-btn').forEach((btn) => {
        btn.addEventListener('click', () => showStorageTab(btn.dataset.tab));
    });
    window.addEventListener('hashchange', () => showStorageTab(location.hash.slice(1)));

    showStorageTab(location.hash.slice(1));

    const refreshBtn = document.getElementById('refresh-disks-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadDisks);

    const createPoolBtn = document.getElementById('create-pool-btn');
    if (createPoolBtn) createPoolBtn.addEventListener('click', () => showCreatePoolDialog());

    const createShareBtn = document.getElementById('create-share-btn');
    if (createShareBtn) createShareBtn.addEventListener('click', showCreateShareDialog);
});

// Load Disks
async function loadDisks() {
    const container = document.getElementById('disks-container');
    const refreshBtn = document.getElementById('refresh-disks-btn');

    container.innerHTML = '<div style="text-align: center; padding: 2rem;"><div class="spinner"></div><p style="color: var(--text-secondary); margin-top: 1rem;">Loading disks...</p></div>';
    if (refreshBtn) refreshBtn.disabled = true;

    try {
        const token = localStorage.getItem('alvaos_token');
        const headers = { 'Authorization': token || '' };
        const [response, poolsResponse] = await Promise.all([
            apiFetch(`${API_BASE}/storage/disks`, { headers }),
            apiFetch(`${API_BASE}/storage/pools`, { headers }).catch(() => null)
        ]);

        if (!response.ok) {
            throw new Error('Failed to load disks');
        }

        const data = await response.json();
        const poolsData = poolsResponse && poolsResponse.ok ? await poolsResponse.json() : {};
        storagePoolsCache = Array.isArray(poolsData.pools) ? poolsData.pools : [];
        displayDisks(data.disks, data.error);
    } catch (error) {
        console.error('Error loading disks:', error);
        renderLoadFailure(container, {
            title: 'Could not read the disks',
            detail: 'The storage service did not answer. It may still be starting up after a restart or update.',
            onRetry: loadDisks
        });
    } finally {
        if (refreshBtn) refreshBtn.disabled = false;
    }
}

// Pools a disk can be added to: managed, not the system pool.
function poolsForNewDisks() {
    return storagePoolsCache.filter((pool) => pool && pool.is_managed !== false && !pool.is_system_pool);
}

function diskTitle(disk) {
    const model = disk.model && disk.model !== 'Unknown' ? disk.model : (disk.is_removable ? 'USB disk' : 'Disk');
    return `${model} · ${disk.size || 'unknown size'}`;
}

function diskMeta(disk) {
    const parts = [disk.path || `/dev/${disk.name}`];
    const transport = String(disk.transport || '').toLowerCase();
    if (transport && transport !== 'unknown') parts.push(transport === 'nvme' ? 'NVMe' : transport.toUpperCase());
    if (disk.serial && disk.serial !== 'N/A') parts.push(`S/N ${disk.serial}`);
    if (disk.temp !== null && disk.temp !== undefined && disk.temp !== '') parts.push(`${disk.temp} °C`);
    return parts.join(' · ');
}

function diskHealthPill(disk) {
    if (disk.smart_status === 'healthy') return '<span class="pill ok">Healthy</span>';
    if (disk.smart_status === 'failed') return '<span class="pill bad">Failing</span>';
    return '';
}

function diskActions(disk) {
    const usage = disk.usage || {};
    const name = jsArg(disk.name);
    const buttons = [];
    if (usage.can_add_to_pool) {
        const targets = poolsForNewDisks();
        buttons.push(`<button type="button" class="${targets.length ? 'btn-secondary' : 'btn-primary'}" onclick="createPoolWithDisk('${name}')">Create pool</button>`);
        if (targets.length) {
            buttons.push(`<button type="button" class="btn-primary" onclick="addDiskToPool('${name}')">${targets.length === 1 ? `Add to “${escapeHtml(targets[0].name)}”` : 'Add to a pool'}</button>`);
        }
    }
    if (usage.role === 'other_pool' && usage.pool_id) {
        buttons.push(`<button type="button" class="btn-secondary" onclick="importDetectedPool('${jsArg(usage.pool_id)}', '${jsArg(usage.pool_name || '')}')">Import pool</button>`);
    }
    if (usage.role === 'pool') {
        buttons.push(`<button type="button" class="btn-secondary" onclick="showStorageTab('pools')">Open pool</button>`);
    }
    if (usage.can_erase) {
        buttons.push(`<button type="button" class="btn-secondary btn-erase" onclick="wipeDisk('${name}')">Erase disk</button>`);
    }
    buttons.push(`<button type="button" class="btn-secondary btn-quiet" onclick="viewDiskDetails('${name}')">Health details</button>`);
    return buttons.join('');
}

function renderDiskRow(disk) {
    const usage = disk.usage || {};
    const icon = disk.is_removable ? 'plug-zap' : (usage.role === 'system' ? 'server' : 'hard-drive');
    const quiet = ['system', 'in_use'].includes(usage.role);
    return `
        <div class="disk-row" data-disk="${escapeHtml(disk.name)}" data-role="${escapeHtml(usage.role || '')}">
            <div class="disk-row-icon">${window.alvaIcon ? window.alvaIcon(icon, '', 'aria-hidden="true"') : ''}</div>
            <div class="disk-row-name">${escapeHtml(diskTitle(disk))}</div>
            ${diskHealthPill(disk)}
            <div class="disk-row-meta">${escapeHtml(diskMeta(disk))}</div>
            <div class="disk-row-usage${quiet ? ' muted' : ''}">${escapeHtml(usage.summary || '')}</div>
            <div class="disk-row-actions">${diskActions(disk)}</div>
        </div>
    `;
}

// Display Disks, grouped by what they are used for.
function displayDisks(disks, notice) {
    storageDisksCache = Array.isArray(disks) ? disks : [];
    const container = document.getElementById('disks-container');

    if (!storageDisksCache.length) {
        container.innerHTML = `<p style="text-align: center; color: var(--text-secondary); padding: 2rem 0;">${escapeHtml(notice || 'No disks detected. Connect a disk and press Refresh.')}</p>`;
        return;
    }

    const role = (disk) => (disk.usage && disk.usage.role) || '';
    const groups = [
        {
            title: 'Available',
            hint: 'Not part of a pool yet. Empty disks can be added right away; disks with old data need to be erased first.',
            disks: storageDisksCache.filter((d) => ['empty', 'has_data', 'other_pool'].includes(role(d)))
        },
        {
            title: 'In use by AlvaOS',
            disks: storageDisksCache.filter((d) => ['pool', 'system'].includes(role(d)))
        },
        {
            title: 'Used outside AlvaOS',
            hint: 'Mounted by something other than AlvaOS. They are left alone.',
            disks: storageDisksCache.filter((d) => role(d) === 'in_use' || !role(d))
        }
    ];

    container.innerHTML = groups.filter((g) => g.disks.length).map((g) => `
        <section class="disk-group">
            <h3 class="disk-group-title">${escapeHtml(g.title)}</h3>
            ${g.hint ? `<p class="disk-group-hint">${escapeHtml(g.hint)}</p>` : ''}
            ${g.disks.map(renderDiskRow).join('')}
        </section>
    `).join('');
}

function createPoolWithDisk(diskName) {
    const disk = storageDisksCache.find((d) => d.name === diskName);
    showCreatePoolDialog(disk ? [disk.path] : []);
}

async function addDiskToPool(diskName) {
    const disk = storageDisksCache.find((d) => d.name === diskName);
    const targets = poolsForNewDisks();
    if (!disk || !targets.length) return;
    let pool = targets[0];
    if (targets.length > 1) {
        pool = await choosePool(targets);
        if (!pool) return;
    }
    showExpandPoolDialog(pool.id, pool.name, [disk.path]);
}

function choosePool(pools) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="choose-pool-title" style="max-width: 420px;">
                <div class="modal-title" id="choose-pool-title">
                    <span>Add to which pool?</span>
                    <button type="button" class="modal-close-x" aria-label="Close">&times;</button>
                </div>
                <div class="choice-list">
                    ${pools.map((p, i) => `
                        <label class="choice">
                            <input type="radio" name="choose-pool" value="${i}" ${i === 0 ? 'checked' : ''}>
                            <div><strong>${escapeHtml(p.name)}</strong><span>${escapeHtml(p.used_size || '?')} used of ${escapeHtml(p.total_size || '?')}</span></div>
                        </label>
                    `).join('')}
                </div>
                <div class="modal-actions">
                    <button type="button" class="btn-secondary" data-act="cancel">Cancel</button>
                    <button type="button" class="btn-primary" data-act="ok">Continue</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        const close = (value) => { overlay.remove(); resolve(value); };
        overlay.querySelector('.modal-close-x').onclick = () => close(null);
        overlay.querySelector('[data-act="cancel"]').onclick = () => close(null);
        overlay.querySelector('[data-act="ok"]').onclick = () => {
            const picked = overlay.querySelector('input[name="choose-pool"]:checked');
            close(picked ? pools[Number(picked.value)] : null);
        };
        attachModalDismiss(overlay, () => close(null));
    });
}

// Pools (overview and detail) live in storage-pools.js.

// Shares live in storage-shares.js.

// Erase a disk that still holds old data, so it can go into a pool.
async function wipeDisk(diskName) {
    const disk = storageDisksCache.find((item) => item.name === diskName) || {};
    const usage = disk.usage || {};
    const what = usage.role === 'other_pool'
        ? `It holds the Btrfs pool "${usage.pool_name || 'unknown'}". Everything in that pool is deleted.`
        : 'Everything on it is deleted: files, partitions and file systems.';
    if (!await confirmDanger(`Erase ${disk.path || `/dev/${diskName}`}?\n\n${what} Your pools and other disks are not touched.`, null, {
        confirmLabel: 'Erase disk',
        requireCheckbox: `I understand that the data on ${disk.path || `/dev/${diskName}`} is gone for good.`,
        warning: 'This cannot be undone.',
        details: diskDetailsForConfirm(diskName)
    })) {
        return;
    }

    const token = localStorage.getItem('alvaos_token');
    try {
        const response = await apiFetch(`${API_BASE}/storage/disks/${encodeURIComponent(diskName)}/wipe`, {
            method: 'POST',
            headers: { 'Authorization': token || '' }
        });

        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'The disk could not be erased.');

        showSuccess(result.message);
        loadDisks();
    } catch (error) {
        showError(error.message);
        loadDisks();
    }
}

// View Disk Details (SMART)
async function viewDiskDetails(diskName) {
    const token = localStorage.getItem('alvaos_token');

    // Create modal immediately for loading state
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';

    const panel = document.createElement('div');
    panel.className = 'modal-content';
    panel.style.maxWidth = '800px';

    panel.innerHTML = `<h2 style="color: var(--text-secondary); text-align: center;">Loading SMART data for ${escapeHtml(diskName)}...</h2>`;
    modal.appendChild(panel);
    document.body.appendChild(modal);
    attachModalDismiss(modal, () => modal.remove());

    try {
        const response = await apiFetch(`${API_BASE}/storage/disks/${diskName}/smart`, {
            headers: { 'Authorization': token || '' }
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Failed to fetch SMART data');

        // Handle case where SMART is not supported but returned 200 (common for USB)
        if (data.error) {
            panel.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; flex-shrink: 0;">
                    <h2 style="margin: 0; color: var(--text-primary);">Disk Health: /dev/${escapeHtml(diskName)}</h2>
                    <button id="close-modal-btn" style="background: transparent; border: none; color: var(--text-secondary); font-size: 1.5rem; cursor: pointer;">${window.alvaIcon ? window.alvaIcon('x', '', 'aria-hidden="true"') : 'x'}</button>
                </div>
                <div style="overflow-y: auto; min-height: 0; flex: 1 1 auto;">
                <div style="padding: 2rem; text-align: center; background: var(--bg-primary); border-radius: 8px; border-left: 4px solid var(--accent-warning);">
                    <div style="font-size: 3rem; margin-bottom: 1rem;">${window.alvaIcon ? window.alvaIcon('info', '', 'aria-hidden="true"') : 'i'}</div>
                    <h3 style="margin-bottom: 0.5rem;">SMART Monitoring Unavailable</h3>
                    <p style="color: var(--text-secondary);">${escapeHtml(data.error)}</p>
                </div>
                </div>
                <button id="close-btn" style="width: 100%; margin-top: 2rem; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600; flex-shrink: 0;">Close</button>
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
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; flex-shrink: 0;">
                <h2 style="margin: 0; color: var(--text-primary);">Disk Health: /dev/${escapeHtml(diskName)}</h2>
                <button id="close-modal-btn" style="background: transparent; border: none; color: var(--text-secondary); font-size: 1.5rem; cursor: pointer;">${window.alvaIcon ? window.alvaIcon('x', '', 'aria-hidden="true"') : 'x'}</button>
            </div>

            <div style="overflow-y: auto; min-height: 0; flex: 1 1 auto;">

            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-bottom: 2rem;">
                <div style="background: var(--bg-primary); padding: 1rem; border-radius: 6px; text-align: center; border-bottom: 3px solid ${color};">
                    <div style="font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase;">Status</div>
                    <div style="font-size: 1.25rem; font-weight: 700; color: ${color};">${escapeHtml(status)}</div>
                </div>
                <div style="background: var(--bg-primary); padding: 1rem; border-radius: 6px; text-align: center;">
                    <div style="font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase;">Temperature</div>
                    <div style="font-size: 1.25rem; font-weight: 700; color: var(--text-primary);">${escapeHtml(temp)}&deg;C</div>
                </div>
                <div style="background: var(--bg-primary); padding: 1rem; border-radius: 6px; text-align: center;">
                    <div style="font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase;">Power On</div>
                    <div style="font-size: 1.25rem; font-weight: 700; color: var(--text-primary);">${escapeHtml(hours)} hrs</div>
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
                                    <td style="padding: 0.75rem 0.5rem;">${escapeHtml(attr.name)}</td>
                                    <td style="padding: 0.75rem 0.5rem; text-align: right; font-family: monospace;">${escapeHtml(attr.raw?.value)}</td>
                                    <td style="padding: 0.75rem 0.5rem; text-align: right; font-family: monospace;">${escapeHtml(attr.value)}</td>
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

            </div>

            <button id="close-btn" style="width: 100%; margin-top: 2rem; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600; flex-shrink: 0;">
                Close
            </button>
        `;

        const close = () => modal.remove();
        panel.querySelector('#close-modal-btn').onclick = close;
        panel.querySelector('#close-btn').onclick = close;

    } catch (error) {
        panel.innerHTML = `<div style="text-align:center; padding: 2rem;">
            <h2 style="color: var(--accent-danger);">Error</h2><p>${escapeHtml(error.message)}</p>
            <button id="err-close" style="margin-top: 1rem; padding: 0.5rem 1rem; background: var(--accent-danger); color: white; border:none; border-radius:4px; cursor:pointer;">Close</button>
        </div>`;
        panel.querySelector('#err-close').onclick = () => modal.remove();
    }
}

// Show Create Pool Dialog
// Disks that may go into a pool (empty ones), from a fresh disk list. When
// none are free, explains why and opens the Disks tab, where it can be fixed.
async function loadEmptyDisks() {
    const token = localStorage.getItem('alvaos_token');
    const response = await apiFetch(`${API_BASE}/storage/disks`, {
        headers: { 'Authorization': token || '' }
    });
    if (!response.ok) {
        showError('Could not read the disks.');
        return null;
    }
    const data = await response.json();
    const disks = Array.isArray(data.disks) ? data.disks : [];
    storageDisksCache = disks;
    const empty = disks.filter((d) => d.usage && d.usage.can_add_to_pool);
    if (!empty.length) {
        const withData = disks.filter((d) => d.usage && d.usage.can_erase).length;
        showNotification(withData
            ? `No empty disk. ${withData} disk(s) still have old data; erase one on the Disks tab to use it.`
            : 'No empty disk. Connect a new disk to create or grow a pool.', 'warning');
        showStorageTab('disks');
        return null;
    }
    return { empty, withData: disks.filter((d) => d.usage && d.usage.can_erase).length };
}

function diskChoiceHtml(disk, className, checked) {
    return `
        <label style="display: flex; align-items: center; padding: 0.5rem; cursor: pointer; border-radius: 4px;">
            <input type="checkbox" value="${escapeHtml(disk.path)}" class="${className}" ${checked ? 'checked' : ''}
                style="margin-right: 0.75rem; accent-color: var(--accent-primary);">
            <div>
                <div style="font-weight: 600;">${escapeHtml(diskTitle(disk))}</div>
                <div style="font-size: 0.8rem; color: var(--text-secondary); font-family: var(--font-mono);">${escapeHtml(diskMeta(disk))}</div>
            </div>
        </label>
    `;
}

async function showCreatePoolDialog(preselect = []) {
    const token = localStorage.getItem('alvaos_token');
    const found = await loadEmptyDisks();
    if (!found) return;
    const availableDisks = found.empty;
    const preselected = new Set(Array.isArray(preselect) ? preselect : []);

    // Create modal
    const modal = document.createElement('div');
    modal.id = 'pool-wizard-modal';
    modal.className = 'modal-overlay';

    const wizard = document.createElement('div');
    wizard.className = 'modal-content';
    wizard.style.maxWidth = '600px';

    wizard.innerHTML = `
        <div class="modal-title">
            <span>Create Storage Pool</span>
            <button type="button" class="modal-close-x" id="close-pool-wizard-x" aria-label="Close">&times;</button>
        </div>

        <div style="overflow-y: auto; min-height: 0; flex: 1 1 auto;">

        <div style="margin-bottom: 1.5rem;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">Pool Name</label>
            <input type="text" id="pool-name-input" placeholder="e.g., storage-pool" 
                style="width: 100%; padding: 0.75rem;">
            <div id="pool-name-error" style="color: var(--accent-danger); font-size: 0.8rem; margin-top: 4px; display: none;"></div>
        </div>

        <div style="margin-bottom: 1.5rem;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">Select Disks</label>
            <div id="disk-selection" style="max-height: 240px; overflow-y: auto; border: 1px solid var(--bg-border); border-radius: 4px; padding: 0.5rem;">
                ${availableDisks.map((disk) => diskChoiceHtml(disk, 'disk-checkbox', preselected.has(disk.path))).join('')}
            </div>
            <p style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">
                Selected: <span id="selected-count">${availableDisks.filter((d) => preselected.has(d.path)).length}</span> disk(s).
                Only empty disks are listed${found.withData ? `; ${found.withData} disk(s) with old data can be erased on the Disks tab` : ''}.
            </p>
        </div>

        <div style="margin-bottom: 1.5rem;">
            <label style="display: block; margin-bottom: 0.5rem; font-weight: 600;">RAID Level</label>
            <select id="raid-level-select" 
                style="width: 100%; padding: 0.75rem;">
            </select>
            <p style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;" id="raid-description">
            </p>
        </div>

        </div>

        <div style="display: flex; gap: 0.75rem; margin-top: 2rem; flex-shrink: 0;">
            <button id="cancel-pool-btn"
                style="flex: 1; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">
                Cancel
            </button>
            <button id="create-pool-confirm-btn" disabled
                style="flex: 1; background: var(--accent-primary); color: white; border: none; padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600; opacity: 0.55;">
                Create Pool
            </button>
        </div>
    `;

    modal.appendChild(wizard);
    document.body.appendChild(modal);

    const poolNameInput = wizard.querySelector('#pool-name-input');
    const poolNameError = wizard.querySelector('#pool-name-error');
    const createBtn = wizard.querySelector('#create-pool-confirm-btn');
    const checkboxes = wizard.querySelectorAll('.disk-checkbox');
    const selectedCount = wizard.querySelector('#selected-count');
    const raidSelect = wizard.querySelector('#raid-level-select');
    const raidDesc = wizard.querySelector('#raid-description');

    const raidDescriptions = {
        'single': 'No protection: if a disk fails, the files on it are lost. All space is usable.',
        'raid0': 'Spread across the disks for speed, with no protection: if one disk fails, everything is lost.',
        'raid1': 'Recommended. Every file is on two disks: one disk can fail. Half the space is usable.',
        'raid1c3': 'Every file is on three disks: two disks can fail. A third of the space is usable.',
        'raid1c4': 'Every file is on four disks: three disks can fail. A quarter of the space is usable.',
        'raid10': 'Mirrored and spread for speed: one disk can fail. Half the space is usable.',
        'raid5': 'Parity: one disk can fail, and all but one disk of space is usable. Btrfs still calls parity experimental; AlvaOS keeps the folder structure mirrored, but a power cut during writing can damage recent files. Use with a UPS and backups.',
        'raid6': 'Double parity: two disks can fail, and all but two disks of space is usable. Btrfs still calls parity experimental; AlvaOS keeps the folder structure mirrored three times, but a power cut during writing can damage recent files. Use with a UPS and backups.'
    };

    const updateRaidOptions = () => {
        const count = wizard.querySelectorAll('.disk-checkbox:checked').length;
        const currentVal = raidSelect.value;

        const options = [
            { value: 'raid1', label: 'Mirrored (RAID1) - recommended', min: 2 },
            { value: 'raid1c3', label: 'Three copies (RAID1c3)', min: 3 },
            { value: 'raid1c4', label: 'Four copies (RAID1c4)', min: 4 },
            { value: 'raid10', label: 'Mirrored and striped (RAID10)', min: 4 },
            { value: 'raid5', label: 'Parity (RAID5) - experimental', min: 3 },
            { value: 'raid6', label: 'Double parity (RAID6) - experimental', min: 4 },
            { value: 'single', label: 'No protection (single)', min: 1 },
            { value: 'raid0', label: 'Striped, no protection (RAID0)', min: 2 }
        ];

        const allowed = options.filter(opt => count >= opt.min);
        raidSelect.innerHTML = allowed.map(opt => `
            <option value="${escapeHtml(opt.value)}">${escapeHtml(opt.label)}</option>
        `).join('');

        if (allowed.some(opt => opt.value === currentVal)) {
            raidSelect.value = currentVal;
        } else if (allowed.length > 0) {
            const hasRaid1 = allowed.some(opt => opt.value === 'raid1');
            raidSelect.value = hasRaid1 ? 'raid1' : allowed[0].value;
        }

        updateRaidDescription();
    };

    const updateRaidDescription = () => {
        const val = raidSelect.value;
        raidDesc.textContent = raidDescriptions[val] || '';
    };

    const validateForm = () => {
        const poolName = poolNameInput.value.trim();
        const selectedDisksCount = wizard.querySelectorAll('.disk-checkbox:checked').length;
        const raidLevel = raidSelect.value;
        const poolNamePattern = /^[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?$/;
        
        let isValid = true;

        if (!poolName) {
            poolNameError.textContent = '';
            poolNameError.style.display = 'none';
            isValid = false;
        } else if (!poolNamePattern.test(poolName)) {
            poolNameError.textContent = 'Invalid pool name. Must be lowercase, start/end with alphanumeric, contain only lowercase letters, numbers, hyphens, and underscores, and be 1-63 characters.';
            poolNameError.style.display = 'block';
            isValid = false;
        } else {
            poolNameError.textContent = '';
            poolNameError.style.display = 'none';
        }

        if (selectedDisksCount === 0) {
            isValid = false;
        }

        if (raidLevel === 'raid1' && selectedDisksCount < 2) isValid = false;
        if (raidLevel === 'raid5' && selectedDisksCount < 3) isValid = false;
        if (raidLevel === 'raid1c3' && selectedDisksCount < 3) isValid = false;
        if (raidLevel === 'raid6' && selectedDisksCount < 4) isValid = false;
        if (raidLevel === 'raid1c4' && selectedDisksCount < 4) isValid = false;
        if (raidLevel === 'raid10' && selectedDisksCount < 4) isValid = false;

        createBtn.disabled = !isValid;
        createBtn.style.opacity = isValid ? '1' : '0.55';
    };

    checkboxes.forEach(cb => {
        cb.addEventListener('change', () => {
            const count = wizard.querySelectorAll('.disk-checkbox:checked').length;
            selectedCount.textContent = count;
            updateRaidOptions();
            validateForm();
        });
    });

    raidSelect.addEventListener('change', () => {
        updateRaidDescription();
        validateForm();
    });

    poolNameInput.addEventListener('input', validateForm);

    updateRaidOptions();
    validateForm();

    // Cancel button
    wizard.querySelector('#cancel-pool-btn').addEventListener('click', () => {
        modal.remove();
    });
    wizard.querySelector('#close-pool-wizard-x').addEventListener('click', () => modal.remove());
    attachModalDismiss(modal, () => modal.remove());

    // Create button
    createBtn.addEventListener('click', async () => {
        const poolName = poolNameInput.value.trim();
        const selectedDisks = Array.from(wizard.querySelectorAll('.disk-checkbox:checked')).map(cb => cb.value);
        const raidLevel = raidSelect.value;

        if (!await confirmDanger(`Create pool "${poolName}"?\n\nThis will erase all data on the selected disk(s).`, null, {
            confirmLabel: 'Create Pool',
            requireCheckbox: `I understand that creating this pool will erase all data on the selected disk(s).`,
            details: [
                { label: 'Pool', value: poolName },
                { label: 'RAID', value: raidLevel.toUpperCase() },
                { label: 'Disks', value: selectedDisks.join(', ') }
            ]
        })) {
            return;
        }

        try {
            createBtn.disabled = true;
            createBtn.textContent = 'Creating Pool...';
            createBtn.style.opacity = '0.7';

            const response = await apiFetch(`${API_BASE}/storage/pools`, {
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

            showSuccess(result.message);
            modal.remove();
            showStorageTab('pools');
        } catch (error) {
            showError(error.message);
            createBtn.disabled = false;
            createBtn.textContent = 'Create Pool';
            createBtn.style.opacity = '1';
        }
    });
}

// Show Expand Pool Dialog
async function showExpandPoolDialog(poolId, poolName, preselect = []) {
    const token = localStorage.getItem('alvaos_token');
    const found = await loadEmptyDisks();
    if (!found) return;
    const availableDisks = found.empty;
    const preselected = new Set(Array.isArray(preselect) ? preselect : []);

    const modal = document.createElement('div');
    modal.className = 'modal-overlay';

    const dialog = document.createElement('div');
    dialog.className = 'modal-content';
    dialog.style.maxWidth = '500px';

    dialog.innerHTML = `
        <div class="modal-title">
            <span>Expand Pool: ${escapeHtml(poolName)}</span>
            <button type="button" class="modal-close-x" id="close-expand-pool-x" aria-label="Close">&times;</button>
        </div>
        <p style="color: var(--text-secondary); font-size: 0.875rem; margin-bottom: 1.5rem;">
            Select one or more disks to add to this pool. Btrfs will immediately increase the total capacity.
        </p>

        <div style="max-height: 200px; overflow-y: auto; border: 1px solid var(--bg-border); border-radius: 4px; padding: 0.5rem; margin-bottom: 1.5rem;">
            ${availableDisks.map((disk) => diskChoiceHtml(disk, 'expand-disk-checkbox', preselected.has(disk.path))).join('')}
        </div>
        
        <div style="display: flex; gap: 0.75rem;">
            <button id="cancel-expand-btn" style="flex: 1; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--bg-border); padding: 0.75rem; border-radius: 4px; cursor: pointer;">Cancel</button>
            <button id="confirm-expand-btn" style="flex: 1; background: var(--accent-primary); color: white; border: none; padding: 0.75rem; border-radius: 4px; cursor: pointer; font-weight: 600;">Add Disks</button>
        </div>
    `;

    modal.appendChild(dialog);
    document.body.appendChild(modal);

    dialog.querySelector('#cancel-expand-btn').onclick = () => modal.remove();
    dialog.querySelector('#close-expand-pool-x').onclick = () => modal.remove();
    attachModalDismiss(modal, () => modal.remove());
    dialog.querySelector('#confirm-expand-btn').onclick = async () => {
        const selectedDisks = Array.from(dialog.querySelectorAll('.expand-disk-checkbox:checked')).map(cb => cb.value);

        if (selectedDisks.length === 0) {
            showError('Please select at least one disk');
            return;
        }

        if (!await confirmDanger(`Add disk(s) to pool "${poolName}"?\n\nData on the selected disk(s) will be erased.`, null, {
            confirmLabel: 'Add Disks',
            requireCheckbox: `I understand that data on the selected disk(s) will be erased.`,
            details: [
                { label: 'Pool', value: poolName },
                { label: 'Disks', value: selectedDisks.join(', ') }
            ]
        })) {
            return;
        }

        try {
            const response = await apiFetch(`${API_BASE}/storage/pools/${poolId}/expand`, {
                method: 'POST',
                headers: {
                    'Authorization': token || '',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ devices: selectedDisks })
            });

            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Failed to expand pool');

            showNotification(result.message, 'success');
            modal.remove();
            if (document.getElementById('tab-pools').classList.contains('active')) loadPools();
            else showStorageTab('pools');
        } catch (error) {
            showError(error.message);
        }
    };
}

// Remove a pool from AlvaOS. By default its data stays on the disks, so the
// pool can be imported again; erasing the disks is an explicit second choice.
function askHowToRemovePool(poolName) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="remove-pool-title" style="max-width: 480px;">
                <div class="modal-title" id="remove-pool-title">
                    <span>Remove pool “${escapeHtml(poolName)}”?</span>
                    <button type="button" class="modal-close-x" aria-label="Close">&times;</button>
                </div>
                <div class="modal-body" style="text-align: left;">
                    The pool is unmounted and disappears from AlvaOS. Shares on it must be deleted first, and apps using it must be stopped.
                    <div class="choice-list">
                        <label class="choice">
                            <input type="radio" name="remove-pool-mode" value="keep" checked>
                            <div><strong>Keep the data</strong><span>The files stay on the disks. You can import the pool again at any time, here or on another AlvaOS.</span></div>
                        </label>
                        <label class="choice">
                            <input type="radio" name="remove-pool-mode" value="erase">
                            <div><strong>Erase the disks</strong><span>Every file in the pool is deleted for good, and the disks become empty for a new pool.</span></div>
                        </label>
                    </div>
                </div>
                <div class="modal-actions">
                    <button type="button" class="btn-secondary" data-act="cancel">Cancel</button>
                    <button type="button" class="btn-primary" data-act="ok">Remove pool</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        const okBtn = overlay.querySelector('[data-act="ok"]');
        const sync = () => {
            const erase = overlay.querySelector('input[value="erase"]').checked;
            okBtn.textContent = erase ? 'Remove and erase' : 'Remove pool';
            okBtn.style.background = erase ? 'var(--accent-danger)' : '';
        };
        overlay.querySelectorAll('input[name="remove-pool-mode"]').forEach((el) => el.addEventListener('change', sync));
        const close = (value) => { overlay.remove(); resolve(value); };
        overlay.querySelector('.modal-close-x').onclick = () => close(null);
        overlay.querySelector('[data-act="cancel"]').onclick = () => close(null);
        okBtn.onclick = () => close(overlay.querySelector('input[value="erase"]').checked ? 'erase' : 'keep');
        attachModalDismiss(overlay, () => close(null));
    });
}

async function deletePool(poolId, poolName) {
    const mode = await askHowToRemovePool(poolName);
    if (!mode) return;
    const erase = mode === 'erase';
    if (erase && !await confirmDanger(`Erase every disk of pool "${poolName}"?\n\nAll files in this pool are deleted. This cannot be undone.`, poolName, {
        confirmLabel: 'Erase disks',
        warning: 'Only continue if you have a copy of anything you still need.',
        details: [{ label: 'Pool', value: poolName }]
    })) {
        return;
    }

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await apiFetch(`${API_BASE}/storage/pools`, {
            method: 'DELETE',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ pool_id: poolId, erase })
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'The pool could not be removed.');
        }

        showNotification(result.message, result.failed ? 'warning' : 'success');
        showStorageTab('pools');
    } catch (error) {
        showError(error.message);
    }
}

// Manage Subvolumes
async function manageSubvolumes(poolId) {
    const token = localStorage.getItem('alvaos_token');
    const response = await apiFetch(`${API_BASE}/storage/pools/${poolId}/subvolumes`, {
        headers: { 'Authorization': token || '' }
    });

    if (!response.ok) {
        showError('Failed to load subvolumes');
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
        background: var(--scrim);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    `;

    const panel = document.createElement('div');
    panel.style.cssText = `
        background: var(--bg-surface);
        border: 1px solid var(--border-hover);
        border-radius: 8px;
        padding: 2rem;
        max-width: 600px;
        width: 90%;
        max-height: min(80vh, 640px);
        display: flex;
        flex-direction: column;
    `;

    panel.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; flex-shrink: 0;">
            <h2 style="margin: 0; color: var(--text-primary);">Manage Subvolumes</h2>
            <button id="close-subvol-btn" style="background: transparent; border: none; color: var(--text-secondary); font-size: 1.5rem; cursor: pointer;">${window.alvaIcon ? window.alvaIcon('x', '', 'aria-hidden="true"') : 'x'}</button>
        </div>

        <div style="overflow-y: auto; min-height: 0; flex: 1 1 auto;">

        <div style="margin-bottom: 1.5rem;">
            <div style="display: flex; gap: 0.5rem;">
                <input type="text" id="new-subvol-name" placeholder="Subvolume name"
                    style="flex: 1; padding: 0.75rem;">
                <button id="create-subvol-btn"
                    style="background: var(--accent-primary); color: white; border: none; padding: 0.75rem 1.5rem; border-radius: 4px; cursor: pointer; font-weight: 600;">
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
                            <div style="font-weight: 600;">${escapeHtml(sv.name)}</div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary); font-family: var(--font-mono);">${escapeHtml(sv.path)}</div>
                        </div>
                        <button onclick="deleteSubvolume('${jsArg(poolId)}', '${jsArg(sv.name)}')"
                            style="background: var(--accent-danger); color: white; border: none; padding: 0.5rem 1rem; border-radius: 4px; cursor: pointer; font-size: 0.875rem;">
                            Delete
                        </button>
                    </div>
                `).join('')
        }
        </div>

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
            showError('Please enter a subvolume name');
            return;
        }

        try {
            const createResponse = await apiFetch(`${API_BASE}/storage/pools/${poolId}/subvolumes`, {
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

            showSuccess(result.message);
            modal.remove();
            manageSubvolumes(poolId);
        } catch (error) {
            showError(`Error: ${error.message}`);
        }
    });
}

// Delete Subvolume
async function deleteSubvolume(poolId, subvolName) {
    if (!await confirmDanger(`Delete subvolume "${subvolName}"?\n\nThis will delete all data in the subvolume.`, null, {
        confirmLabel: 'Delete Subvolume',
        requireCheckbox: `I confirm that I want to permanently delete subvolume "${subvolName}" and all its data.`,
        details: [
            { label: 'Subvolume', value: subvolName },
            { label: 'Pool ID', value: poolId }
        ]
    })) {
        return;
    }

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await apiFetch(`${API_BASE}/storage/pools/${poolId}/subvolumes`, {
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

        showSuccess(result.message);

        const modal = document.getElementById('subvolume-modal');
        if (modal) {
            modal.remove();
            manageSubvolumes(poolId);
        }
    } catch (error) {
        showError(`Error: ${error.message}`);
    }
}
