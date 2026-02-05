// AlvaOS Users Management
// API_BASE is defined in app.js

document.addEventListener('DOMContentLoaded', () => {
    const createBtn = document.getElementById('create-user-btn');
    if (createBtn) createBtn.addEventListener('click', createUser);
    loadUsers();
});

async function loadUsers() {
    const container = document.getElementById('users-container');
    if (!container) return;

    container.innerHTML = '<p style="color: var(--text-secondary);">Loading users...</p>';

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/users`, {
            headers: { 'Authorization': token || '' }
        });

        if (!response.ok) {
            throw new Error('Failed to load users');
        }

        const data = await response.json();
        renderUsers(data.users || []);
    } catch (error) {
        container.innerHTML = '<p style="color: var(--accent-danger);">Failed to load users.</p>';
    }
}

function renderUsers(users) {
    const container = document.getElementById('users-container');
    if (!container) return;

    if (!users.length) {
        container.innerHTML = '<p style="color: var(--text-secondary);">No users created yet.</p>';
        return;
    }

    container.innerHTML = '';

    users.forEach(user => {
        const row = document.createElement('div');
        row.className = 'card';
        row.style.marginBottom = '0';

        row.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap;">
                <div>
                    <div style="font-weight:600; font-size:1rem;">${user.username}</div>
                    <div style="color: var(--text-secondary); font-size:0.85rem;">
                        ${user.system_exists ? 'System user: yes' : 'System user: missing'}
                    </div>
                </div>
                <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
                    <select data-user="${user.username}" class="role-select">
                        <option value="user" ${user.role === 'user' ? 'selected' : ''}>User</option>
                        <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>Admin</option>
                    </select>
                    <button class="btn-secondary reset-pass-btn" data-user="${user.username}">Reset Password</button>
                    <button class="btn-secondary delete-user-btn" data-user="${user.username}" style="border-color: var(--accent-danger); color: var(--accent-danger);">Delete</button>
                </div>
            </div>
        `;

        container.appendChild(row);
    });

    container.querySelectorAll('.delete-user-btn').forEach(btn => {
        btn.addEventListener('click', () => deleteUser(btn.dataset.user));
    });
    container.querySelectorAll('.reset-pass-btn').forEach(btn => {
        btn.addEventListener('click', () => showPasswordResetModal(btn.dataset.user));
    });
    container.querySelectorAll('.role-select').forEach(select => {
        select.addEventListener('change', () => updateUserRole(select.dataset.user, select.value));
    });
}

async function createUser() {
    const nameInput = document.getElementById('user-name-input');
    const passInput = document.getElementById('user-pass-input');
    const roleSelect = document.getElementById('user-role-select');

    const username = (nameInput.value || '').trim();
    const password = passInput.value || '';
    const role = roleSelect.value || 'user';

    if (!username || !password) {
        window.showToast('Username and password are required', 'warning');
        return;
    }

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/users`, {
            method: 'POST',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password, role })
        });

        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Failed to create user');

        window.showToast(result.message || 'User created', 'success');
        nameInput.value = '';
        passInput.value = '';
        roleSelect.value = 'user';
        loadUsers();
    } catch (error) {
        window.showToast(error.message || 'Failed to create user', 'error');
    }
}

async function deleteUser(username) {
    if (!await showConfirm(`Delete user "${username}"?\n\nThis will remove the user and their SMB access.`)) return;

    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/users`, {
            method: 'DELETE',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Failed to delete user');

        window.showToast(result.message || 'User deleted', 'success');
        loadUsers();
    } catch (error) {
        window.showToast(error.message || 'Failed to delete user', 'error');
    }
}

async function updateUserRole(username, role) {
    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/users/${encodeURIComponent(username)}`, {
            method: 'PATCH',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ role })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Failed to update role');
        window.showToast(`Role updated for ${username}`, 'success');
    } catch (error) {
        window.showToast(error.message || 'Failed to update role', 'error');
        loadUsers();
    }
}

function showPasswordResetModal(username) {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.style.cssText = `
        position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
        z-index: 10000;
    `;

    const panel = document.createElement('div');
    panel.style.cssText = `
        background: var(--bg-surface);
        border: 1px solid var(--border-hover);
        border-radius: 8px;
        padding: 1.5rem;
        max-width: 420px;
        width: 90%;
    `;

    panel.innerHTML = `
        <h3 style="margin-bottom: 0.5rem;">Reset Password</h3>
        <p style="color: var(--text-secondary); margin-bottom: 1rem;">Set a new password for <strong>${username}</strong>.</p>
        <input type="password" id="reset-pass-input" placeholder="Minimum 8 characters" style="width: 100%; margin-bottom: 1rem;" />
        <div style="display:flex; gap:8px;">
            <button id="cancel-reset-btn" class="btn-secondary" style="flex:1;">Cancel</button>
            <button id="confirm-reset-btn" class="btn-primary" style="flex:1;">Update</button>
        </div>
    `;

    modal.appendChild(panel);
    document.body.appendChild(modal);

    panel.querySelector('#cancel-reset-btn').addEventListener('click', () => modal.remove());
    panel.querySelector('#confirm-reset-btn').addEventListener('click', async () => {
        const newPassword = panel.querySelector('#reset-pass-input').value || '';
        if (newPassword.length < 8) {
            window.showToast('Password must be at least 8 characters', 'warning');
            return;
        }
        await updateUserPassword(username, newPassword);
        modal.remove();
    });
}

async function updateUserPassword(username, password) {
    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await fetch(`${API_BASE}/users/${encodeURIComponent(username)}`, {
            method: 'PATCH',
            headers: {
                'Authorization': token || '',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ password })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Failed to update password');
        window.showToast('Password updated', 'success');
    } catch (error) {
        window.showToast(error.message || 'Failed to update password', 'error');
    }
}
