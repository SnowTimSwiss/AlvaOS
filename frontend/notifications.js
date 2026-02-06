// Modern Notification System for AlvaOS

// Create Toast Container
const toastContainer = document.createElement('div');
toastContainer.className = 'toast-container';
document.body.appendChild(toastContainer);

// Show Toast Function
window.showToast = function (message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    let icon = 'ℹ️';
    if (type === 'success') icon = '✅';
    if (type === 'error') icon = '❌';
    if (type === 'warning') icon = '⚠️';

    toast.innerHTML = `
        <div style="display:flex; align-items:center; gap:12px;">
            <span style="font-size:1.2rem;">${icon}</span>
            <span>${message}</span>
        </div>
        <button class="toast-close" onclick="this.parentElement.remove()">✕</button>
    `;

    toastContainer.appendChild(toast);

    // Auto remove
    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.3s ease-out forwards';
        setTimeout(() => toast.remove(), 300);
    }, 5000);
};

// Override Alert
window.originalAlert = window.alert;
window.alert = function (message) {
    // Determine type based on keywords
    let type = 'info';
    const lowerMsg = String(message).toLowerCase();
    if (lowerMsg.includes('error') || lowerMsg.includes('failed')) type = 'error';
    if (lowerMsg.includes('success') || lowerMsg.includes('created') || lowerMsg.includes('updated') || lowerMsg.includes('deleted')) type = 'success';
    if (lowerMsg.includes('warning')) type = 'warning';

    window.showToast(message, type);
};

// Custom Confirm Modal
window.confirmModal = function (message, onConfirm, onCancel) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    // Check if message has a title (split by newline)
    let title = 'Confirmation';
    let body = message;
    if (message.includes('\n')) {
        const parts = message.split('\n');
        title = parts[0];
        body = parts.slice(1).join('<br>');
    }

    // Danger check
    const isDanger = message.toLowerCase().includes('delete') || message.toLowerCase().includes('erase') || message.toLowerCase().includes('wipe');
    const confirmBtnColor = isDanger ? 'var(--accent-danger)' : 'var(--accent-primary)';

    overlay.innerHTML = `
        <div class="modal-content">
            <div class="modal-title">${title}</div>
            <div class="modal-body">${body}</div>
            <div class="modal-actions">
                <button id="modal-cancel" style="padding: 0.75rem 1.5rem; background: transparent; border: 1px solid var(--bg-border); color: var(--text-primary); border-radius: 6px; cursor: pointer; font-weight: 600;">Cancel</button>
                <button id="modal-confirm" style="padding: 0.75rem 1.5rem; background: ${confirmBtnColor}; border: none; color: white; border-radius: 6px; cursor: pointer; font-weight: 600;">Confirm</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    // Focus confirm
    overlay.querySelector('#modal-confirm').focus();

    return new Promise((resolve) => {
        overlay.querySelector('#modal-cancel').onclick = () => {
            overlay.remove();
            if (onCancel) onCancel();
            resolve(false);
        };

        overlay.querySelector('#modal-confirm').onclick = () => {
            overlay.remove();
            if (onConfirm) onConfirm();
            resolve(true);
        };
    });
};

// Override Confirm (Note: Native confirm is synchronous, this is async.
// We cannot truly override window.confirm to be sync. 
// We must update the calling code to use await confirmModal() or handle async.)
// For now, we provide showConfirm as a utility and I will update usages.
window.showConfirm = window.confirmModal;

// Update Banner & Badge
function triggerUpdateCheck() {
    if (window.__updateCheckRunning) return;
    window.__updateCheckRunning = true;
    const path = window.location.pathname;
    if (path.includes('login.html') || path.includes('setup.html')) return;
    const token = localStorage.getItem('alvaos_token');
    if (!token) return;

    const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

    const lastCheck = Number(localStorage.getItem('alvaos_update_last_check') || 0);
    const cachedAvailable = localStorage.getItem('alvaos_update_available') === 'true';
    const cachedVersion = localStorage.getItem('alvaos_update_version') || '';

    const shouldReuseCache = Date.now() - lastCheck < CHECK_INTERVAL_MS;
    if (shouldReuseCache && cachedAvailable) {
        renderUpdateBanner(cachedVersion);
        setUpdateBadge(true);
        return;
    }

    fetch('/api/v1/updates/settings', {
        headers: { 'Authorization': token }
    })
        .then(res => res.ok ? res.json() : null)
        .then(settings => {
            if (!settings || !settings.auto_check) return null;
            const channel = settings.channel || 'stable';
            return fetch(`/api/v1/updates/alvaos/check?channel=${encodeURIComponent(channel)}`, {
                headers: { 'Authorization': token }
            });
        })
        .then(res => res ? res.json() : null)
        .then(data => {
            if (!data) return;
            if (data.error) return;
            localStorage.setItem('alvaos_update_last_check', String(Date.now()));
            localStorage.setItem('alvaos_update_available', String(!!data.update_available));
            localStorage.setItem('alvaos_update_version', data.latest_version || '');
            if (data.update_available) {
                renderUpdateBanner(data.latest_version || 'Update');
                setUpdateBadge(true);
            } else {
                setUpdateBadge(false);
            }
        })
        .catch(() => { });

    function setUpdateBadge(show) {
        const badge = document.getElementById('updates-nav-badge');
        if (badge) badge.style.display = show ? 'inline-flex' : 'none';
    }

    function renderUpdateBanner(version) {
        const existing = document.getElementById('update-banner');
        if (existing) return;

        const banner = document.createElement('div');
        banner.id = 'update-banner';
        banner.className = 'update-banner';
        banner.innerHTML = `
            <div>
                <div class="banner-title">Update available</div>
                <div class="banner-sub">AlvaOS ${version} is ready to install.</div>
            </div>
            <div class="banner-actions">
                <a class="btn-secondary" href="updates.html">View updates</a>
                <button class="btn-primary" id="update-banner-dismiss">Dismiss</button>
            </div>
        `;

        const slot = document.getElementById('update-banner-slot') || document.querySelector('.content-scroll');
        if (slot) {
            slot.prepend(banner);
            banner.querySelector('#update-banner-dismiss').addEventListener('click', () => {
                banner.remove();
            });
        }
    }
}

window.triggerUpdateCheck = triggerUpdateCheck;
triggerUpdateCheck();
