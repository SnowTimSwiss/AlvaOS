// AlvaOS Share Users (SMB/NFS access accounts, separate from the AlvaOS admin login)
// API_BASE is defined in app.js

const USERNAME_PATTERN = /^[a-z_][a-z0-9_-]{1,31}$/;

function usersNotify(message, type = 'info') {
    if (window.showToast) {
        window.showToast(message, type);
        return;
    }
    if (type === 'error') console.error(message);
    else console.log(message);
}

function usersEscapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

async function usersConfirm(message) {
    if (typeof window.showConfirm === 'function') {
        return await window.showConfirm(message);
    }
    return false;
}

async function usersReadJson(response) {
    if (!response) return null;
    try {
        return await response.json();
    } catch {
        return null;
    }
}

function usersApiError(response, payload, fallback, forbiddenMessage = 'Admin privileges required.') {
    const explicit = String(payload?.error || '').trim();
    if (explicit) return explicit;
    if (response?.status === 403) return forbiddenMessage;
    if (response?.status === 404) return 'User not found';
    return fallback;
}

async function usersApi(path, options = {}) {
    const requestOptions = { ...options };
    const headers = requestOptions.headers ? { ...requestOptions.headers } : {};
    headers.Authorization = localStorage.getItem('alvaos_token') || '';

    if (Object.prototype.hasOwnProperty.call(requestOptions, 'json')) {
        headers['Content-Type'] = 'application/json';
        requestOptions.body = JSON.stringify(requestOptions.json);
        delete requestOptions.json;
    }

    requestOptions.headers = headers;

    let response;
    try {
        response = await fetch(`${API_BASE}${path}`, requestOptions);
    } catch (error) {
        const detail = String(error?.message || '').trim();
        usersNotify(detail ? `Network error: ${detail}` : 'Network error while contacting users API', 'error');
        return null;
    }

    if (response.status === 401) {
        window.location.href = '/login.html';
        return null;
    }

    return response;
}

function setUsersContainerMessage(message, className) {
    const container = document.getElementById('users-container');
    if (!container) return;
    container.innerHTML = `<p class="${className}">${usersEscapeHtml(message)}</p>`;
}

function setUserRowBusy(row, busy) {
    if (!row) return;
    row.classList.toggle('is-busy', !!busy);
    row.querySelectorAll('button, input').forEach((el) => {
        el.disabled = !!busy;
    });
}

document.addEventListener('DOMContentLoaded', () => {
    const createBtn = document.getElementById('create-user-btn');
    if (createBtn) createBtn.addEventListener('click', showCreateUserModal);
});

async function loadUsers() {
    const container = document.getElementById('users-container');
    if (container && !container.querySelector('.disk-row')) setUsersContainerMessage('Loading people...', 'users-loading');

    const [response, sharesResponse] = await Promise.all([usersApi('/users'), usersApi('/storage/shares')]);
    const data = await usersReadJson(response);
    const sharesData = sharesResponse && sharesResponse.ok ? await usersReadJson(sharesResponse) : null;

    if (!response) {
        setUsersContainerMessage('Failed to load users.', 'users-error');
        return;
    }

    if (response.status === 403) {
        setUsersContainerMessage('Access denied: Admin privileges required.', 'users-denied');
        return;
    }

    if (!response.ok || !data) {
        setUsersContainerMessage(usersApiError(response, data, 'Failed to load users.'), 'users-error');
        return;
    }

    renderUsers(data.users || [], (sharesData && sharesData.shares) || []);
}

// What a person can open: [{name, role}] from the shares' SMB permissions.
function sharesOfUser(username, shares) {
    return shares
        .filter((share) => share.protocol === 'smb' && ['read', 'write'].includes((share.smb_permissions || {})[username]))
        .map((share) => ({ name: share.name, role: share.smb_permissions[username] === 'write' && !share.read_only ? 'edit' : 'read' }))
        .sort((a, b) => a.name.localeCompare(b.name));
}

function renderUsers(users, shares = []) {
    const container = document.getElementById('users-container');
    if (!container) return;

    if (!users.length) {
        container.innerHTML = `
            <div class="pool-section" style="text-align: center; padding: 2.5rem 1.5rem;">
                <div class="pool-name" style="margin-bottom: 6px;">No people yet</div>
                <p style="color: var(--text-secondary); margin: 0 auto 16px; max-width: 460px;">
                    Add the people in your home or team, then choose which shared folders each of them can open.
                </p>
                <button type="button" class="btn-primary" onclick="showCreateUserModal()">Add a person</button>
            </div>`;
        return;
    }

    container.innerHTML = '';

    users.forEach((user) => {
        const username = String(user?.username || '').trim();
        const access = sharesOfUser(username, shares);
        const accessText = access.length
            ? `Can open ${access.map((a) => `${a.name} (${a.role})`).join(', ')}`
            : 'Not in any shared folder yet. Give access from the Shares tab.';

        const row = document.createElement('div');
        row.className = 'disk-row user-row';
        row.dataset.user = username;
        row.innerHTML = `
            <div class="avatar disk-row-icon">${usersEscapeHtml(username.slice(0, 1))}</div>
            <div class="disk-row-name">${usersEscapeHtml(username)}</div>
            ${user.system_exists === false ? '<span class="pill warn" title="The account exists in AlvaOS but not on the system. Remove and add the person again.">Account broken</span>' : ''}
            <div class="disk-row-meta" style="font-family: inherit;">${usersEscapeHtml(accessText)}</div>
            <div class="disk-row-actions">
                <button type="button" class="btn-secondary reset-pass-btn">Change password</button>
                <button type="button" class="btn-secondary btn-quiet delete-user-btn">Remove</button>
            </div>
        `;
        container.appendChild(row);
        row.querySelector('.delete-user-btn').addEventListener('click', () => deleteUser(username, row, access));
        row.querySelector('.reset-pass-btn').addEventListener('click', () => showPasswordResetModal(username, row));
    });
}

function showCreateUserModal() {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';

    const panel = document.createElement('div');
    panel.className = 'user-modal-panel';

    panel.innerHTML = `
        <h3 class="user-modal-title">
            <span>Add a person</span>
            <button type="button" class="modal-close-x" id="close-create-user-x" aria-label="Close">&times;</button>
        </h3>
        <p class="user-modal-text">They sign in with this name and password when they open a shared folder.</p>
        <input type="text" id="create-user-name-input" class="user-modal-input" placeholder="Name, e.g. alex" autocomplete="off" />
        <div id="create-user-name-error" class="user-modal-error" style="display:none;"></div>
        <input type="password" id="create-user-pass-input" class="user-modal-input" placeholder="Password, at least 8 characters" autocomplete="new-password" />
        <div id="create-user-pass-error" class="user-modal-error" style="display:none;"></div>
        <div class="user-modal-actions">
            <button id="cancel-create-btn" class="btn-secondary">Cancel</button>
            <button id="confirm-create-btn" class="btn-primary">Add</button>
        </div>
    `;

    modal.appendChild(panel);
    document.body.appendChild(modal);

    const cancelBtn = panel.querySelector('#cancel-create-btn');
    const confirmBtn = panel.querySelector('#confirm-create-btn');
    const closeXBtn = panel.querySelector('#close-create-user-x');
    const nameInput = panel.querySelector('#create-user-name-input');
    const passInput = panel.querySelector('#create-user-pass-input');
    const nameError = panel.querySelector('#create-user-name-error');
    const passError = panel.querySelector('#create-user-pass-error');

    const setFieldError = (el, message) => {
        if (!el) return;
        el.textContent = message || '';
        el.style.display = message ? 'block' : 'none';
    };

    if (nameInput) nameInput.focus();

    const close = () => modal.remove();
    if (cancelBtn) cancelBtn.addEventListener('click', close);
    if (closeXBtn) closeXBtn.addEventListener('click', close);
    attachModalDismiss(modal, close);

    if (confirmBtn) {
        confirmBtn.addEventListener('click', async () => {
            const username = String(nameInput?.value || '').trim();
            const password = String(passInput?.value || '');
            setFieldError(nameError, '');
            setFieldError(passError, '');

            if (!username) {
                setFieldError(nameError, 'A name is required');
                return;
            }
            if (!USERNAME_PATTERN.test(username)) {
                setFieldError(nameError, 'Use 2 to 32 lowercase letters, numbers, _ or -, starting with a letter.');
                return;
            }
            if (!password || password.length < 8) {
                setFieldError(passError, 'Password must be at least 8 characters');
                return;
            }

            confirmBtn.disabled = true;
            if (!confirmBtn.dataset.defaultLabel) confirmBtn.dataset.defaultLabel = confirmBtn.textContent || 'Create';
            confirmBtn.textContent = 'Creating...';
            const created = await createUser(username, password);
            confirmBtn.disabled = false;
            confirmBtn.textContent = confirmBtn.dataset.defaultLabel;

            if (created) {
                modal.remove();
            }
        });
    }
}

async function createUser(username, password) {
    const response = await usersApi('/users', {
        method: 'POST',
        json: { username, password }
    });
    const result = await usersReadJson(response);

    if (!response) return false;
    if (!response.ok) {
        usersNotify(usersApiError(response, result, 'Failed to create user', 'Permission denied: Only admins can create users.'), 'error');
        return false;
    }

    usersNotify(result?.message || 'User created', 'success');
    await loadUsers();
    return true;
}

async function deleteUser(username, row, access = []) {
    const loses = access.length ? ` They lose access to ${access.map((a) => a.name).join(', ')}.` : '';
    const ok = await usersConfirm(`Remove ${username}?\n\n${username} can no longer open shared folders.${loses} Files in the shares are not deleted.`);
    if (!ok) return;

    setUserRowBusy(row, true);
    const response = await usersApi('/users', {
        method: 'DELETE',
        json: { username }
    });
    const result = await usersReadJson(response);
    setUserRowBusy(row, false);

    if (!response) return;
    if (!response.ok) {
        usersNotify(usersApiError(response, result, 'Failed to delete user', 'Permission denied: Only admins can delete users.'), 'error');
        return;
    }

    usersNotify(result?.message || 'User deleted', 'success');
    await loadUsers();
}

function showPasswordResetModal(username, row) {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';

    const panel = document.createElement('div');
    panel.className = 'user-modal-panel';

    panel.innerHTML = `
        <h3 class="user-modal-title">
            <span>Change password</span>
            <button type="button" class="modal-close-x" id="close-reset-pass-x" aria-label="Close">&times;</button>
        </h3>
        <p class="user-modal-text">New password for <strong>${usersEscapeHtml(username)}</strong>. Their devices ask for it the next time they open a shared folder.</p>
        <input type="password" id="reset-pass-input" class="user-modal-input" placeholder="Minimum 8 characters" />
        <div id="reset-pass-error" class="user-modal-error" style="display:none;"></div>
        <div class="user-modal-actions">
            <button id="cancel-reset-btn" class="btn-secondary">Cancel</button>
            <button id="confirm-reset-btn" class="btn-primary">Update</button>
        </div>
    `;

    modal.appendChild(panel);
    document.body.appendChild(modal);

    const cancelBtn = panel.querySelector('#cancel-reset-btn');
    const confirmBtn = panel.querySelector('#confirm-reset-btn');
    const closeXBtn = panel.querySelector('#close-reset-pass-x');
    const passwordInput = panel.querySelector('#reset-pass-input');
    const passError = panel.querySelector('#reset-pass-error');

    if (passwordInput) passwordInput.focus();

    const close = () => modal.remove();
    if (cancelBtn) cancelBtn.addEventListener('click', close);
    if (closeXBtn) closeXBtn.addEventListener('click', close);
    attachModalDismiss(modal, close);

    if (confirmBtn) {
        confirmBtn.addEventListener('click', async () => {
            const newPassword = String(passwordInput?.value || '');
            if (newPassword.length < 8) {
                passError.textContent = 'Password must be at least 8 characters';
                passError.style.display = 'block';
                return;
            }
            passError.textContent = '';
            passError.style.display = 'none';

            confirmBtn.disabled = true;
            if (!confirmBtn.dataset.defaultLabel) confirmBtn.dataset.defaultLabel = confirmBtn.textContent || 'Update';
            confirmBtn.textContent = 'Updating...';
            const updated = await updateUserPassword(username, newPassword, row);
            confirmBtn.disabled = false;
            confirmBtn.textContent = confirmBtn.dataset.defaultLabel;

            if (updated) {
                modal.remove();
            }
        });
    }
}

async function updateUserPassword(username, password, row) {
    setUserRowBusy(row, true);
    const response = await usersApi(`/users/${encodeURIComponent(username)}`, {
        method: 'PATCH',
        json: { password }
    });
    const result = await usersReadJson(response);
    setUserRowBusy(row, false);

    if (!response) return false;
    if (!response.ok) {
        usersNotify(usersApiError(response, result, 'Failed to update password', 'Permission denied: Only admins can reset passwords.'), 'error');
        return false;
    }

    usersNotify(`Password for ${username} changed.`, 'success');
    return true;
}
