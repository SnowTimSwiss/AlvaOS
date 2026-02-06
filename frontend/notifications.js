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
const UPDATE_CACHE_KEYS = {
    lastCheck: 'alvaos_update_last_check',
    available: 'alvaos_update_available',
    version: 'alvaos_update_version',
    dismissed: 'alvaos_update_dismissed_version'
};

function setUpdateBadge(show) {
    const badge = document.getElementById('updates-nav-badge');
    if (badge) badge.style.display = show ? 'inline-flex' : 'none';
}

function removeUpdateBanner() {
    const existing = document.getElementById('update-banner');
    if (existing) existing.remove();
}

function renderUpdateBanner(versionLabel, versionKey) {
    const existing = document.getElementById('update-banner');
    if (existing) {
        const sub = existing.querySelector('.banner-sub');
        if (sub) sub.textContent = `AlvaOS ${versionLabel} is ready to install.`;
        existing.dataset.version = versionKey;
        return;
    }

    const banner = document.createElement('div');
    banner.id = 'update-banner';
    banner.className = 'update-banner';
    banner.dataset.version = versionKey;
    banner.innerHTML = `
        <div>
            <div class="banner-title">Update available</div>
            <div class="banner-sub">AlvaOS ${versionLabel} is ready to install.</div>
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
            const dismissedVersion = banner.dataset.version || 'unknown';
            localStorage.setItem(UPDATE_CACHE_KEYS.dismissed, dismissedVersion);
            removeUpdateBanner();
            setUpdateBadge(false);
        });
    }
}

function setUpdateIndicators({ available, version, checkedAt } = {}) {
    if (typeof checkedAt === 'number') {
        localStorage.setItem(UPDATE_CACHE_KEYS.lastCheck, String(checkedAt));
    }
    if (typeof available === 'boolean') {
        localStorage.setItem(UPDATE_CACHE_KEYS.available, String(available));
    }
    if (typeof version === 'string') {
        localStorage.setItem(UPDATE_CACHE_KEYS.version, version);
    }

    const storedAvailable = (typeof available === 'boolean')
        ? available
        : (localStorage.getItem(UPDATE_CACHE_KEYS.available) === 'true');
    const storedVersion = (typeof version === 'string')
        ? version
        : (localStorage.getItem(UPDATE_CACHE_KEYS.version) || '');

    if (!storedAvailable) {
        localStorage.removeItem(UPDATE_CACHE_KEYS.dismissed);
        removeUpdateBanner();
        setUpdateBadge(false);
        return;
    }

    const versionLabel = storedVersion || 'Update';
    const versionKey = storedVersion || 'unknown';
    const dismissedVersion = localStorage.getItem(UPDATE_CACHE_KEYS.dismissed) || '';
    const show = versionKey !== dismissedVersion;
    if (show) {
        renderUpdateBanner(versionLabel, versionKey);
    } else {
        removeUpdateBanner();
    }
    setUpdateBadge(show);
}

window.setUpdateIndicators = setUpdateIndicators;

function triggerUpdateCheck() {
    if (window.__updateCheckRunning) return;
    window.__updateCheckRunning = true;
    const path = window.location.pathname;
    if (path.includes('login.html') || path.includes('setup.html')) return;
    const token = localStorage.getItem('alvaos_token');
    if (!token) return;

    const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

    const lastCheck = Number(localStorage.getItem(UPDATE_CACHE_KEYS.lastCheck) || 0);
    const cachedAvailable = localStorage.getItem(UPDATE_CACHE_KEYS.available) === 'true';
    const cachedVersion = localStorage.getItem(UPDATE_CACHE_KEYS.version) || '';

    const shouldReuseCache = Date.now() - lastCheck < CHECK_INTERVAL_MS;
    if (shouldReuseCache) {
        setUpdateIndicators({ available: cachedAvailable, version: cachedVersion });
        if (cachedAvailable) return;
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
            if (!data || data.error) return;
            setUpdateIndicators({
                available: !!data.update_available,
                version: data.latest_version || '',
                checkedAt: Date.now()
            });
        })
        .catch(() => { });
}

window.triggerUpdateCheck = triggerUpdateCheck;
triggerUpdateCheck();
