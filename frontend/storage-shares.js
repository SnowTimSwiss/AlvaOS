// AlvaOS Storage: shared folders.
//
// A share card says where the folder lives, who can open it and the address
// to type on a computer, with a copy button. "Share a folder" asks three
// things (name, where, who); SMB vs NFS and the NFS client list sit under
// "More options". Uses helpers from app.js, storage.js and storage-pools.js.

let sharesCache = [];
let shareUsersCache = [];

const SHARE_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$/;

function nasHost() {
    return window.location.hostname || 'alvaos';
}

function shareAddress(share) {
    return share.protocol === 'nfs' ? `${nasHost()}:${share.path}` : `\\\\${nasHost()}\\${share.name}`;
}

// "main › media" for a folder in a pool, "main (whole pool)" for the pool itself.
function shareLocation(share) {
    const pool = storagePoolsCache
        .filter((p) => p.mount_point && (share.path === p.mount_point || String(share.path).startsWith(`${p.mount_point}/`)))
        .sort((a, b) => b.mount_point.length - a.mount_point.length)[0];
    if (!pool) return share.path;
    const rest = String(share.path).slice(pool.mount_point.length).replace(/^\//, '');
    return rest ? `${pool.name} › ${rest.split('/').join(' › ')}` : `${pool.name} (whole pool)`;
}

function joinNames(names) {
    if (names.length <= 1) return names.join('');
    return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

// Who can open a share, in one sentence, and whether that needs attention.
function shareAccessText(share) {
    if (share.protocol === 'nfs') {
        const hosts = String(share.allowed_hosts || '*');
        return { text: hosts === '*' ? 'Any computer on your network (NFS).' : `Computers: ${hosts} (NFS).`, tone: '' };
    }
    const perms = share.smb_permissions || {};
    const editors = Object.keys(perms).filter((u) => perms[u] === 'write' && !share.read_only).sort();
    const readers = Object.keys(perms).filter((u) => perms[u] === 'read' || (perms[u] === 'write' && share.read_only)).sort();
    const parts = [];
    if (share.guest_access) parts.push(share.read_only ? 'Everyone on your network can open it (read only)' : 'Everyone on your network can open and change it');
    if (editors.length) parts.push(`${joinNames(editors)} can edit`);
    if (readers.length) parts.push(`${joinNames(readers)} can read`);
    if (!parts.length) return { text: 'Nobody can open it yet. Choose who has access.', tone: 'warn' };
    return { text: `${parts.join(' · ')}.`, tone: '' };
}

async function copyText(text, button) {
    try {
        await navigator.clipboard.writeText(text);
    } catch {
        const area = document.createElement('textarea');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        area.remove();
    }
    if (button) {
        const label = button.textContent;
        button.textContent = 'Copied';
        setTimeout(() => { button.textContent = label; }, 1500);
    }
}

function addressBox(text) {
    return `<div class="address-box"><code>${escapeHtml(text)}</code><button type="button" class="btn-secondary" onclick="event.stopPropagation(); copyText('${jsArg(text)}', this)">Copy</button></div>`;
}

// ── Loading ──────────────────────────────────────────────────────────────────

async function sharesGet(path) {
    const token = localStorage.getItem('alvaos_token');
    const response = await apiFetch(`${API_BASE}${path}`, { headers: { 'Authorization': token || '' } });
    if (!response.ok) throw new Error(`Failed to load ${path}`);
    return response.json();
}

async function loadShares() {
    const container = document.getElementById('shares-container');
    if (!sharesCache.length) {
        container.innerHTML = '<div style="text-align: center; padding: 2rem;"><div class="spinner"></div></div>';
    }
    try {
        const [shares, pools, users] = await Promise.all([
            sharesGet('/storage/shares'),
            sharesGet('/storage/pools').catch(() => ({ pools: storagePoolsCache })),
            sharesGet('/users').catch(() => ({ users: [] }))
        ]);
        sharesCache = Array.isArray(shares.shares) ? shares.shares : [];
        if (Array.isArray(pools.pools)) storagePoolsCache = pools.pools;
        shareUsersCache = Array.isArray(users.users) ? users.users : [];
        displayShares(sharesCache);
    } catch (error) {
        console.error('Error loading shares:', error);
        renderLoadFailure(container, {
            title: 'Could not read the shares',
            detail: 'The file sharing service did not answer. Existing shares keep working; only this list is unavailable.',
            onRetry: loadShares
        });
    }
}

function displayShares(shares) {
    const container = document.getElementById('shares-container');
    if (!shares.length) {
        const hasPool = storagePoolsCache.some((p) => p.is_managed !== false && !p.is_system_pool);
        container.innerHTML = `
            <div class="pool-section" style="grid-column: 1 / -1; text-align: center; padding: 2.5rem 1.5rem;">
                <div class="pool-name" style="margin-bottom: 6px;">Nothing shared yet</div>
                <p style="color: var(--text-secondary); margin: 0 auto 16px; max-width: 460px;">
                    ${hasPool ? 'Share a folder and open it from Windows, macOS, Linux or your phone, like a drive.'
                        : 'Shared folders live in a storage pool. Create a pool first.'}
                </p>
                ${hasPool ? '<button type="button" class="btn-primary" onclick="showCreateShareDialog()">Share a folder</button>'
                    : '<button type="button" class="btn-primary" onclick="showStorageTab(\'pools\')">Go to pools</button>'}
            </div>`;
        return;
    }
    container.innerHTML = shares.slice().sort((a, b) => String(a.name).localeCompare(String(b.name))).map(renderShareCard).join('');
}

function shareGb(bytes) {
    const gb = Number(bytes || 0) / 1024 ** 3;
    if (gb < 1) return gb === 0 ? '0 GB' : `${Math.max(1, Math.round(gb * 1024))} MB`;
    return gb >= 1000 ? `${(gb / 1024).toFixed(1)} TB` : `${gb >= 10 ? Math.round(gb) : gb.toFixed(1)} GB`;
}

// "12 GB of 50 GB" with a bar, for shares with a space limit.
function shareLimitHtml(share) {
    if (!share.quota_bytes) return '';
    const used = share.used_bytes;
    const pct = used != null ? Math.min(100, Math.round((used / share.quota_bytes) * 100)) : 0;
    const tone = pct >= 95 ? 'var(--accent-danger)' : pct >= 85 ? 'var(--accent-warning)' : 'var(--accent-primary)';
    return `<div class="pool-line">${icon('hard-drive')}<span>${used != null ? `${shareGb(used)} of ${shareGb(share.quota_bytes)} used` : `Limit ${shareGb(share.quota_bytes)}`}</span></div>
        ${used != null ? `<div class="share-limit-bar" role="img" aria-label="${pct}% of the limit used"><span style="width:${pct}%;background:${tone}"></span></div>` : ''}`;
}

function shareIsWholePool(share) {
    return storagePoolsCache.some((p) => p.mount_point === share.path);
}

function renderShareCard(share) {
    const id = jsArg(share.id);
    const access = shareAccessText(share);
    return `
        <div class="pool-card ${access.tone === 'warn' ? 'notice' : ''}" style="cursor: default;" data-share="${escapeHtml(share.id)}">
            <div class="pool-head">
                <span class="disk-row-icon" style="grid-row: auto;">${icon('folder-open')}</span>
                <span class="pool-name">${escapeHtml(share.name)}</span>
                ${share.personal_for ? '<span class="tag" style="margin-left: auto;">Personal</span>' : ''}
                <span class="tag" style="${share.personal_for ? '' : 'margin-left: auto;'}">${escapeHtml(String(share.protocol || 'smb').toUpperCase())}</span>
            </div>
            <div class="share-where">${escapeHtml(shareLocation(share))}</div>
            <div class="pool-line ${access.tone}">${icon(access.tone ? 'triangle-alert' : 'users')}<span>${escapeHtml(access.text)}</span></div>
            ${shareLimitHtml(share)}
            ${addressBox(shareAddress(share))}
            <div class="pool-actions">
                <button type="button" class="btn-secondary" onclick="showConnectionInfo('${id}')">How to connect</button>
                ${share.protocol === 'smb' ? `<button type="button" class="${access.tone ? 'btn-primary' : 'btn-secondary'}" onclick="showShareAccessDialog('${id}')">Access</button>` : ''}
                ${shareIsWholePool(share) ? '' : `<button type="button" class="btn-secondary" onclick="showShareLimitDialog('${id}')">Space limit</button>`}
                <button type="button" class="btn-secondary btn-quiet" style="margin-left: auto;" onclick="deleteShare('${id}', '${jsArg(share.name)}')">Stop sharing</button>
            </div>
        </div>`;
}

// ── Who can open it (used by "Share a folder" and "Access") ──────────────────

function accessEditorHtml(share) {
    const perms = (share && share.smb_permissions) || {};
    const guest = !!(share && share.guest_access);
    const readOnly = !!(share && share.read_only);
    return `
        <div class="choice-list" style="margin-top: 6px;">
            <label class="choice">
                <input type="radio" name="share-who" value="people" ${guest ? '' : 'checked'}>
                <div><strong>Only people I choose</strong><span>They sign in with their name and password.</span></div>
            </label>
            <label class="choice">
                <input type="radio" name="share-who" value="everyone" ${guest ? 'checked' : ''}>
                <div><strong>Everyone on my network</strong><span>No password. Fine for media at home, not for private files.</span></div>
            </label>
        </div>
        <div data-who="people" style="margin-top: 10px;">
            <div class="people-list">
                ${shareUsersCache.map((u) => {
                    const role = perms[u.username] === 'write' && readOnly ? 'read' : (perms[u.username] || 'deny');
                    return `
                    <div class="person-access">
                        <div style="display: flex; align-items: center; gap: 10px;"><span class="avatar" style="width: 28px; height: 28px; font-size: 0.8rem;">${escapeHtml(u.username.slice(0, 1))}</span>${escapeHtml(u.username)}</div>
                        <select class="perm-select" data-user="${escapeHtml(u.username)}">
                            <option value="deny" ${role === 'deny' ? 'selected' : ''}>No access</option>
                            <option value="read" ${role === 'read' ? 'selected' : ''}>Can read</option>
                            <option value="write" ${role === 'write' ? 'selected' : ''}>Can edit</option>
                        </select>
                    </div>`;
                }).join('') || '<div class="field-hint">Nobody has an account yet. Add the first person here.</div>'}
            </div>
            <details class="modal-float-details" style="margin-top: 8px;" ${shareUsersCache.length ? '' : 'open'}>
                <summary style="cursor: pointer; color: var(--accent-primary); font-size: 0.85rem;">Add a person</summary>
                <div class="modal-disclosure-panel" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin-top: 8px;">
                    <input type="text" data-new-user placeholder="Name, e.g. anna" autocomplete="off">
                    <input type="password" data-new-pass placeholder="Password (8+ characters)" autocomplete="new-password">
                    <button type="button" class="btn-secondary" data-add-person>Add</button>
                </div>
                <div class="field-error" data-person-error></div>
            </details>
        </div>
        <div data-who="everyone" style="margin-top: 10px;">
            <label style="display: flex; gap: 8px; align-items: center; cursor: pointer;">
                <input type="checkbox" data-everyone-readonly style="width: auto; accent-color: var(--accent-primary);" ${readOnly ? 'checked' : ''}>
                <span>Only allow reading, nobody can change or delete files</span>
            </label>
        </div>`;
}

// Wires the editor inside `root`; returns a function that reads the choice.
function bindAccessEditor(root) {
    const sync = () => {
        const who = root.querySelector('input[name="share-who"]:checked').value;
        root.querySelectorAll('[data-who]').forEach((el) => { el.hidden = el.dataset.who !== who; });
    };
    root.querySelectorAll('input[name="share-who"]').forEach((el) => el.addEventListener('change', sync));
    sync();

    const addBtn = root.querySelector('[data-add-person]');
    addBtn.addEventListener('click', async () => {
        const name = root.querySelector('[data-new-user]');
        const pass = root.querySelector('[data-new-pass]');
        const error = root.querySelector('[data-person-error]');
        error.textContent = '';
        if (!USERNAME_PATTERN.test(name.value.trim())) {
            error.textContent = 'Names use lowercase letters, numbers, _ or - (2 to 32 characters).';
            return;
        }
        if (pass.value.length < 8) {
            error.textContent = 'The password needs at least 8 characters.';
            return;
        }
        addBtn.disabled = true;
        const response = await usersApi('/users', { method: 'POST', json: { username: name.value.trim(), password: pass.value } });
        const result = await usersReadJson(response);
        addBtn.disabled = false;
        if (!response || !response.ok) {
            error.textContent = usersApiError(response, result, 'The person could not be added.');
            return;
        }
        const username = name.value.trim();
        shareUsersCache.push({ username });
        const list = root.querySelector('.people-list');
        if (!list.querySelector('.person-access')) list.innerHTML = '';
        list.insertAdjacentHTML('beforeend', `
            <div class="person-access">
                <div style="display: flex; align-items: center; gap: 10px;"><span class="avatar" style="width: 28px; height: 28px; font-size: 0.8rem;">${escapeHtml(username.slice(0, 1))}</span>${escapeHtml(username)}</div>
                <select class="perm-select" data-user="${escapeHtml(username)}">
                    <option value="deny">No access</option>
                    <option value="read">Can read</option>
                    <option value="write" selected>Can edit</option>
                </select>
            </div>`);
        name.value = '';
        pass.value = '';
        usersNotify(`${username} was added.`, 'success');
    });

    return () => {
        const everyone = root.querySelector('input[name="share-who"]:checked').value === 'everyone';
        const permissions = {};
        if (!everyone) {
            root.querySelectorAll('.perm-select').forEach((sel) => {
                if (sel.value !== 'deny') permissions[sel.dataset.user] = sel.value;
            });
        }
        const readOnly = everyone && root.querySelector('[data-everyone-readonly]').checked;
        return { guest_access: everyone, read_only: readOnly, smb_permissions: permissions };
    };
}

function shareModal(title, bodyHtml, confirmLabel) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal-content modal-disclosure-frame" role="dialog" aria-modal="true" aria-labelledby="share-modal-title" style="max-width: 560px;">
            <div class="modal-title" id="share-modal-title">
                <span>${escapeHtml(title)}</span>
                <button type="button" class="modal-close-x" aria-label="Close">&times;</button>
            </div>
            <div class="modal-body" style="text-align: left;">${bodyHtml}<div class="field-error" data-form-error></div></div>
            <div class="modal-actions">
                <button type="button" class="btn-secondary" data-act="cancel">Cancel</button>
                <button type="button" class="btn-primary" data-act="ok">${escapeHtml(confirmLabel)}</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('.modal-close-x').onclick = close;
    overlay.querySelector('[data-act="cancel"]').onclick = close;
    attachModalDismiss(overlay, close);
    return { overlay, close, ok: overlay.querySelector('[data-act="ok"]'), error: overlay.querySelector('[data-form-error]') };
}

async function sendShareJson(method, path, body) {
    const token = localStorage.getItem('alvaos_token');
    const response = await apiFetch(`${API_BASE}${path}`, {
        method,
        headers: { 'Authorization': token || '', 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'The request failed.');
    return result;
}

// ── Share a folder ───────────────────────────────────────────────────────────

async function showCreateShareDialog() {
    try {
        const [pools, users] = await Promise.all([sharesGet('/storage/pools'), sharesGet('/users').catch(() => ({ users: [] }))]);
        storagePoolsCache = Array.isArray(pools.pools) ? pools.pools : storagePoolsCache;
        shareUsersCache = Array.isArray(users.users) ? users.users : [];
    } catch {
        showError('Could not read the pools.');
        return;
    }
    const pools = storagePoolsCache.filter((p) => p.is_managed !== false && !p.is_system_pool && p.mount_point);
    if (!pools.length) {
        showNotification('Shared folders live in a storage pool. Create a pool first.', 'warning');
        showStorageTab('pools');
        return;
    }
    let existing = [];
    try {
        existing = ((await sharesGet('/storage/available-paths')).paths || []).map((p) => p.path);
    } catch {
        existing = [];
    }

    const modal = shareModal('Share a folder', `
        <div class="form-field">
            <label class="field-label" for="share-name">Name</label>
            <input type="text" id="share-name" placeholder="e.g. Media" autocomplete="off" maxlength="63">
            <div class="field-hint">Shown on your computers. Letters, numbers, "-" and "_".</div>
        </div>
        <div class="form-field">
            <label class="field-label" for="share-pool">Where</label>
            <select id="share-pool" ${pools.length === 1 ? 'hidden' : ''}>
                ${pools.map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}${p.free_bytes ? ` · ${escapeHtml(formatBytes(p.free_bytes))} free` : ''}</option>`).join('')}
            </select>
            <select id="share-folder" style="margin-top: 6px;"></select>
        </div>
        <div class="form-field">
            <span class="field-label">Who can open it</span>
            <div id="share-access">${accessEditorHtml(null)}</div>
        </div>
        <details class="form-field modal-float-details">
            <summary style="cursor: pointer; color: var(--text-secondary); font-size: 0.85rem;">More options</summary>
            <div class="modal-disclosure-panel" style="margin-top: 10px;">
                <label class="field-label" for="share-protocol">Protocol</label>
                <select id="share-protocol">
                    <option value="smb" selected>SMB: Windows, macOS, Linux, phones (recommended)</option>
                    <option value="nfs">NFS: Linux and Unix computers</option>
                </select>
                <div id="nfs-options" hidden style="margin-top: 10px;">
                    <label class="field-label" for="share-hosts">Allowed computers</label>
                    <input type="text" id="share-hosts" value="*" autocomplete="off">
                    <div class="field-hint">* for any computer, or addresses and networks, e.g. 192.168.1.0/24 192.168.1.50</div>
                    <label style="display: flex; gap: 8px; align-items: center; margin-top: 8px; cursor: pointer;">
                        <input type="checkbox" id="share-nfs-readonly" style="width: auto;"> Read only
                    </label>
                </div>
            </div>
        </details>`, 'Share folder');

    const root = modal.overlay;
    const nameInput = root.querySelector('#share-name');
    const poolSelect = root.querySelector('#share-pool');
    const folderSelect = root.querySelector('#share-folder');
    const protocol = root.querySelector('#share-protocol');
    const readAccess = bindAccessEditor(root.querySelector('#share-access'));

    const renderFolders = () => {
        const pool = pools.find((p) => String(p.id) === poolSelect.value) || pools[0];
        const name = nameInput.value.trim();
        const inPool = existing.filter((path) => path.startsWith(`${pool.mount_point}/`) && path !== pool.mount_point);
        const current = folderSelect.value;
        folderSelect.innerHTML = [
            `<option value="new">${escapeHtml(name ? `A new folder “${name}” in ${pool.name}` : `A new folder in ${pool.name}`)}</option>`,
            ...inPool.map((path) => `<option value="${escapeHtml(path)}">Existing folder: ${escapeHtml(path.slice(pool.mount_point.length + 1))}</option>`),
            `<option value="pool">The whole pool ${escapeHtml(pool.name)}</option>`
        ].join('');
        if ([...folderSelect.options].some((o) => o.value === current)) folderSelect.value = current;
    };
    nameInput.addEventListener('input', renderFolders);
    poolSelect.addEventListener('change', renderFolders);
    protocol.addEventListener('change', () => {
        const nfs = protocol.value === 'nfs';
        root.querySelector('#nfs-options').hidden = !nfs;
        root.querySelector('#share-access').closest('.form-field').hidden = nfs;
    });
    renderFolders();
    nameInput.focus();

    modal.ok.onclick = async () => {
        modal.error.textContent = '';
        const name = nameInput.value.trim();
        if (!SHARE_NAME_PATTERN.test(name)) {
            modal.error.textContent = 'Choose a name with letters, numbers, "-" or "_", without spaces.';
            nameInput.focus();
            return;
        }
        const pool = pools.find((p) => String(p.id) === poolSelect.value) || pools[0];
        const body = { name, protocol: protocol.value };
        if (folderSelect.value === 'new') {
            Object.assign(body, { pool_id: pool.id, folder: name, new_folder: true });
        } else if (folderSelect.value === 'pool') {
            Object.assign(body, { pool_id: pool.id });
        } else {
            body.path = folderSelect.value;
        }
        if (protocol.value === 'nfs') {
            body.allowed_hosts = root.querySelector('#share-hosts').value.trim() || '*';
            body.read_only = root.querySelector('#share-nfs-readonly').checked;
        } else {
            Object.assign(body, readAccess());
            if (!body.guest_access && !Object.keys(body.smb_permissions).length) {
                modal.error.textContent = 'Give at least one person access, or allow everyone on your network.';
                return;
            }
        }
        modal.ok.disabled = true;
        modal.ok.textContent = 'Sharing...';
        try {
            const result = await sendShareJson('POST', '/storage/shares', body);
            modal.close();
            showSuccess(result.message);
            loadShares();
        } catch (error) {
            modal.error.textContent = error.message;
            modal.ok.disabled = false;
            modal.ok.textContent = 'Share folder';
        }
    };
}

// ── Access ───────────────────────────────────────────────────────────────────

async function showShareAccessDialog(shareId) {
    const share = sharesCache.find((s) => s.id === shareId);
    if (!share) return;
    try {
        shareUsersCache = (await sharesGet('/users')).users || [];
    } catch {
        shareUsersCache = [];
    }
    const modal = shareModal(`Who can open “${share.name}”`, `<div id="share-access">${accessEditorHtml(share)}</div>`, 'Save');
    const readAccess = bindAccessEditor(modal.overlay.querySelector('#share-access'));
    modal.ok.onclick = async () => {
        modal.error.textContent = '';
        const access = readAccess();
        if (!access.guest_access && !Object.keys(access.smb_permissions).length) {
            modal.error.textContent = 'Give at least one person access, or allow everyone on your network.';
            return;
        }
        modal.ok.disabled = true;
        try {
            const result = await sendShareJson('PUT', '/storage/shares/permissions', { share_id: shareId, ...access });
            modal.close();
            showSuccess(result.message);
            loadShares();
        } catch (error) {
            modal.error.textContent = error.message;
            modal.ok.disabled = false;
        }
    };
}

function showShareLimitDialog(shareId) {
    const share = sharesCache.find((s) => s.id === shareId);
    if (!share) return;
    const currentGb = share.quota_bytes ? Math.round(share.quota_bytes / 1024 ** 3) : '';
    const modal = shareModal(`Space limit for “${share.name}”`, `
        <p style="color: var(--text-secondary); margin-top: 0;">How much this folder may hold. When it is full, saving new files there fails until something is deleted; nothing is lost.</p>
        <label class="choice"><input type="radio" name="limit-kind" value="none" ${currentGb ? '' : 'checked'}><div><strong>No limit</strong><span>It can use all free space in the pool.</span></div></label>
        <label class="choice"><input type="radio" name="limit-kind" value="gb" ${currentGb ? 'checked' : ''}><div><strong>Up to</strong>
            <span><input type="number" id="limit-gb" min="1" step="1" value="${escapeHtml(String(currentGb || 100))}" style="width: 110px;"> GB</span></div></label>
        <p class="field-hint" style="margin-top: 10px;">Limits use Btrfs quotas. The first limit on a pool turns them on, which can make a pool with many restore points a little slower.</p>`, 'Save');
    const gbInput = modal.overlay.querySelector('#limit-gb');
    gbInput.addEventListener('focus', () => { modal.overlay.querySelector('input[value="gb"]').checked = true; });
    modal.ok.onclick = async () => {
        modal.error.textContent = '';
        const none = modal.overlay.querySelector('input[name="limit-kind"]:checked').value === 'none';
        const gb = Number(gbInput.value);
        if (!none && !(gb >= 1)) {
            modal.error.textContent = 'Enter the limit in GB, at least 1.';
            return;
        }
        modal.ok.disabled = true;
        try {
            const result = await sendShareJson('PUT', '/storage/shares/quota', { share_id: shareId, limit_gb: none ? null : gb });
            modal.close();
            showSuccess(result.message);
            loadShares();
        } catch (error) {
            modal.error.textContent = error.message;
            modal.ok.disabled = false;
        }
    };
}

async function deleteShare(shareId, shareName) {
    if (!await showConfirm(`Stop sharing "${shareName}"?\n\nComputers can no longer open it over the network. The folder and its files stay on the NAS.`, {
        confirmLabel: 'Stop sharing'
    })) return;
    try {
        const result = await sendShareJson('DELETE', '/storage/shares', { share_id: shareId });
        showSuccess(result.message);
        loadShares();
    } catch (error) {
        showError(error.message);
    }
}

// ── How to connect ───────────────────────────────────────────────────────────

function connectSection(iconName, title, hint, address) {
    return `
        <div class="connect-section">
            <h4>${icon(iconName)} ${escapeHtml(title)}</h4>
            <p>${escapeHtml(hint)}</p>
            ${addressBox(address)}
        </div>`;
}

function showConnectionInfo(shareId) {
    const share = sharesCache.find((s) => s.id === shareId);
    if (!share) return;
    const host = nasHost();
    const name = share.name;
    const signIn = share.guest_access ? 'No password needed.' : 'Sign in with your name and password from the People tab.';
    const body = share.protocol === 'nfs' ? [
        connectSection('terminal', 'Linux', 'Mount it once:', `sudo mount -t nfs ${host}:${share.path} /mnt/${name}`),
        connectSection('terminal', 'Linux, at every boot', 'Add this line to /etc/fstab:', `${host}:${share.path} /mnt/${name} nfs defaults,_netdev 0 0`),
        connectSection('laptop', 'macOS', 'Finder → Go → Connect to Server (⌘K):', `nfs://${host}${share.path}`)
    ].join('') : [
        connectSection('monitor', 'Windows', `Paste into the File Explorer address bar. ${signIn}`, `\\\\${host}\\${name}`),
        connectSection('laptop', 'macOS', `Finder → Go → Connect to Server (⌘K). ${signIn}`, `smb://${host}/${name}`),
        connectSection('terminal', 'Linux', 'In the file manager, or mount it:', `smb://${host}/${name}`),
        connectSection('globe', 'Phones and tablets', 'In Files (iOS) or a file manager app (Android), add a server:', `smb://${host}/${name}`)
    ].join('');
    const modal = shareModal(`Open “${name}” on your devices`, `
        ${body}
        <div class="field-hint">${escapeHtml(`Where: ${shareLocation(share)}. If the name ${host} does not work on a device, use the NAS's IP address instead.`)}</div>`, 'Done');
    modal.ok.onclick = modal.close;
    modal.overlay.querySelector('[data-act="cancel"]').hidden = true;
}
