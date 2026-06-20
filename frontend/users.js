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
    setUsersContainerMessage('Loading users...', 'users-loading');

    const response = await usersApi('/users');
    const data = await usersReadJson(response);

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

    renderUsers(data.users || []);
}

function renderUsers(users) {
    const container = document.getElementById('users-container');
    if (!container) return;

    if (!users.length) {
        setUsersContainerMessage('No share users created yet.', 'users-empty');
        return;
    }

    container.innerHTML = '';

    users.forEach((user) => {
        const username = String(user?.username || '').trim();
        const createdAt = user?.created_at ? new Date(user.created_at) : null;
        const createdLabel = createdAt && !Number.isNaN(createdAt.getTime())
            ? createdAt.toLocaleDateString()
            : 'Unknown';

        const row = document.createElement('div');
        row.className = 'card user-row';
        row.dataset.user = username;

        row.innerHTML = `
            <div class="user-row-main">
                <div>
                    <div class="user-row-name">${usersEscapeHtml(username)}</div>
                    <div class="user-row-state">
                        ${user.system_exists ? 'System user synchronization: Active' : 'System user: Missing (Manual intervention required)'}
                    </div>
                    <div class="user-row-created">Created: ${usersEscapeHtml(createdLabel)}</div>
                </div>
                <div class="user-row-actions">
                    <button class="btn-secondary reset-pass-btn" data-user="${usersEscapeHtml(username)}">Reset Password</button>
                    <button class="btn-secondary delete-user-btn user-delete-btn" data-user="${usersEscapeHtml(username)}">Delete</button>
                </div>
            </div>
        `;

        container.appendChild(row);

        const deleteBtn = row.querySelector('.delete-user-btn');
        const resetBtn = row.querySelector('.reset-pass-btn');

        if (deleteBtn) {
            deleteBtn.addEventListener('click', () => deleteUser(username, row));
        }

        if (resetBtn) {
            resetBtn.addEventListener('click', () => showPasswordResetModal(username, row));
        }
    });
}

function showCreateUserModal() {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';

    const panel = document.createElement('div');
    panel.className = 'user-modal-panel';

    panel.innerHTML = `
        <h3 class="user-modal-title">Create User</h3>
        <p class="user-modal-text">Creates a share-access account for SMB/NFS. Separate from the AlvaOS admin login.</p>
        <input type="text" id="create-user-name-input" class="user-modal-input" placeholder="Username, e.g. alex" />
        <input type="password" id="create-user-pass-input" class="user-modal-input" placeholder="Minimum 8 characters" />
        <div class="user-modal-actions">
            <button id="cancel-create-btn" class="btn-secondary">Cancel</button>
            <button id="confirm-create-btn" class="btn-primary">Create</button>
        </div>
    `;

    modal.appendChild(panel);
    document.body.appendChild(modal);

    const cancelBtn = panel.querySelector('#cancel-create-btn');
    const confirmBtn = panel.querySelector('#confirm-create-btn');
    const nameInput = panel.querySelector('#create-user-name-input');
    const passInput = panel.querySelector('#create-user-pass-input');

    if (nameInput) nameInput.focus();

    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => modal.remove());
    }

    modal.addEventListener('click', (event) => {
        if (event.target === modal) modal.remove();
    });

    if (confirmBtn) {
        confirmBtn.addEventListener('click', async () => {
            const username = String(nameInput?.value || '').trim();
            const password = String(passInput?.value || '');

            if (!username || !password) {
                usersNotify('Username and password are required', 'warning');
                return;
            }
            if (!USERNAME_PATTERN.test(username)) {
                usersNotify('Invalid username. Use lowercase letters, numbers, _ or -.', 'warning');
                return;
            }
            if (password.length < 8) {
                usersNotify('Password must be at least 8 characters', 'warning');
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

async function deleteUser(username, row) {
    const ok = await usersConfirm(`Delete user "${username}"?\n\nThis will remove the user and their SMB access.`);
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
        <h3 class="user-modal-title">Reset Password</h3>
        <p class="user-modal-text">Set a new password for <strong>${usersEscapeHtml(username)}</strong>.</p>
        <input type="password" id="reset-pass-input" class="user-modal-input" placeholder="Minimum 8 characters" />
        <div class="user-modal-actions">
            <button id="cancel-reset-btn" class="btn-secondary">Cancel</button>
            <button id="confirm-reset-btn" class="btn-primary">Update</button>
        </div>
    `;

    modal.appendChild(panel);
    document.body.appendChild(modal);

    const cancelBtn = panel.querySelector('#cancel-reset-btn');
    const confirmBtn = panel.querySelector('#confirm-reset-btn');
    const passwordInput = panel.querySelector('#reset-pass-input');

    if (passwordInput) passwordInput.focus();

    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => modal.remove());
    }

    modal.addEventListener('click', (event) => {
        if (event.target === modal) modal.remove();
    });

    if (confirmBtn) {
        confirmBtn.addEventListener('click', async () => {
            const newPassword = String(passwordInput?.value || '');
            if (newPassword.length < 8) {
                usersNotify('Password must be at least 8 characters', 'warning');
                return;
            }

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

    usersNotify('Password updated', 'success');
    return true;
}
