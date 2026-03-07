// Modern Notification System for AlvaOS

const toastContainer = document.createElement('div');
toastContainer.className = 'toast-container';
document.body.appendChild(toastContainer);

function getToastIconName(type) {
    if (type === 'success') return 'circle-check-big';
    if (type === 'error') return 'circle-x';
    if (type === 'warning') return 'triangle-alert';
    return 'info';
}

window.showToast = function (message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    const iconHtml = window.alvaIcon
        ? window.alvaIcon(getToastIconName(type), 'toast-type-icon', 'aria-hidden="true"')
        : '';

    toast.innerHTML = `
        <div style="display:flex; align-items:center; gap:12px;">
            <span style="display:inline-flex; font-size:1.1rem;">${iconHtml}</span>
            <span>${message}</span>
        </div>
        <button class="toast-close" onclick="this.parentElement.remove()" aria-label="Close notification">
            ${window.alvaIcon ? window.alvaIcon('x', '', 'aria-hidden="true"') : 'x'}
        </button>
    `;

    toastContainer.appendChild(toast);
    if (window.renderAlvaIcons) window.renderAlvaIcons(toast);

    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.3s ease-out forwards';
        setTimeout(() => toast.remove(), 300);
    }, 5000);
};

window.originalAlert = window.alert;
window.alert = function (message) {
    let type = 'info';
    const lowerMsg = String(message).toLowerCase();
    if (lowerMsg.includes('error') || lowerMsg.includes('failed')) type = 'error';
    if (lowerMsg.includes('success') || lowerMsg.includes('created') || lowerMsg.includes('updated') || lowerMsg.includes('deleted')) type = 'success';
    if (lowerMsg.includes('warning')) type = 'warning';

    window.showToast(message, type);
};

window.confirmModal = function (message, onConfirm, onCancel) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    let title = 'Confirmation';
    let body = message;
    if (message.includes('\n')) {
        const parts = message.split('\n');
        title = parts[0];
        body = parts.slice(1).join('<br>');
    }

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

window.showConfirm = window.confirmModal;

window.promptModal = function (message, options = {}) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    let title = options.title || 'Input Required';
    let body = String(message || '');
    if (!options.title && body.includes('\n')) {
        const parts = body.split('\n');
        title = parts[0] || title;
        body = parts.slice(1).join('<br>');
    }

    const inputType = options.type || 'text';
    const placeholder = options.placeholder || '';
    const confirmLabel = options.confirmLabel || 'Continue';
    const cancelLabel = options.cancelLabel || 'Cancel';
    const initialValue = options.value || '';
    const fieldLabel = options.label || '';
    const required = options.required !== false;

    overlay.innerHTML = `
        <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="modal-prompt-title">
            <div class="modal-title" id="modal-prompt-title">${title}</div>
            <div class="modal-body">
                ${body}
                ${fieldLabel ? `<div style="margin-top: 1rem; margin-bottom: 0.4rem; font-weight: 600; text-align: left;">${fieldLabel}</div>` : ''}
                <input
                    id="modal-prompt-input"
                    type="${inputType}"
                    value="${String(initialValue)
                        .replace(/&/g, '&amp;')
                        .replace(/"/g, '&quot;')
                        .replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;')}"
                    placeholder="${String(placeholder)
                        .replace(/&/g, '&amp;')
                        .replace(/"/g, '&quot;')
                        .replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;')}"
                    style="width: 100%; margin-top: 1rem; padding: 0.8rem 0.9rem; border-radius: 6px; border: 1px solid var(--bg-border); background: var(--bg-card); color: var(--text-primary);">
            </div>
            <div class="modal-actions">
                <button id="modal-prompt-cancel" class="btn-secondary">${cancelLabel}</button>
                <button id="modal-prompt-confirm" class="btn-primary">${confirmLabel}</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    const input = overlay.querySelector('#modal-prompt-input');
    const confirmBtn = overlay.querySelector('#modal-prompt-confirm');
    const cancelBtn = overlay.querySelector('#modal-prompt-cancel');

    const syncState = () => {
        if (!confirmBtn) return;
        confirmBtn.disabled = required && !String(input?.value || '').trim();
    };

    if (input) {
        input.focus();
        input.select();
        input.addEventListener('input', syncState);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !(required && !String(input.value || '').trim())) {
                event.preventDefault();
                confirmBtn?.click();
            }
        });
    }
    syncState();

    return new Promise((resolve) => {
        cancelBtn.onclick = () => {
            overlay.remove();
            resolve(null);
        };

        confirmBtn.onclick = () => {
            const value = String(input?.value || '');
            if (required && !value.trim()) {
                input?.focus();
                return;
            }
            overlay.remove();
            resolve(value);
        };
    });
};

window.showPrompt = window.promptModal;

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
        headers: { Authorization: token }
    })
        .then(res => res.ok ? res.json() : null)
        .then(settings => {
            if (!settings || !settings.auto_check) return null;
            const channel = settings.channel || 'stable';
            return fetch(`/api/v1/updates/alvaos/check?channel=${encodeURIComponent(channel)}`, {
                headers: { Authorization: token }
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
