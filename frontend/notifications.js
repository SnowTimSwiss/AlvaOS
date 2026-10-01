// Modern Notification System for AlvaOS

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

const toastContainer = document.createElement('div');
toastContainer.className = 'toast-container';
document.body.appendChild(toastContainer);

function getToastIconName(type) {
    if (type === 'success') return 'circle-check-big';
    if (type === 'error') return 'circle-x';
    if (type === 'warning') return 'triangle-alert';
    return 'info';
}

function severityBucket(severity) {
    const s = String(severity || 'info').toLowerCase();
    if (s === 'critical' || s === 'error') return 'danger';
    if (s === 'warning') return 'warning';
    if (s === 'success') return 'success';
    return 'info';
}

function severityIconName(severity) {
    const bucket = severityBucket(severity);
    if (bucket === 'danger') return 'circle-x';
    if (bucket === 'warning') return 'triangle-alert';
    if (bucket === 'success') return 'circle-check-big';
    return 'info';
}

window.showToast = function (message, type = 'info', options = {}) {
    if (typeof options !== 'object' || options === null) options = {};
    const toast = document.createElement('div');
    const sticky = options.sticky === true || type === 'warning' || type === 'error';
    const link = options.link || null;
    toast.className = `toast ${type}${sticky ? ' sticky' : ''}`;

    const iconHtml = window.alvaIcon
        ? window.alvaIcon(getToastIconName(type), 'toast-type-icon', 'aria-hidden="true"')
        : '';

    toast.innerHTML = `
        <div class="toast-body"${link ? ' style="cursor:pointer;"' : ''}>
            <span class="toast-type-icon-wrap">${iconHtml}</span>
            <span class="toast-message">${message}</span>
        </div>
        <button class="toast-close" aria-label="Close notification">
            ${window.alvaIcon ? window.alvaIcon('x', '', 'aria-hidden="true"') : 'x'}
        </button>
    `;

    toastContainer.appendChild(toast);
    if (window.renderAlvaIcons) window.renderAlvaIcons(toast);

    const closeToast = () => {
        toast.style.animation = 'fadeOut 0.3s ease-out forwards';
        setTimeout(() => toast.remove(), 300);
    };

    toast.querySelector('.toast-close').addEventListener('click', (event) => {
        event.stopPropagation();
        closeToast();
    });

    if (link) {
        toast.querySelector('.toast-body').addEventListener('click', () => {
            window.location.href = link;
        });
    }

    if (!sticky) {
        setTimeout(closeToast, 5000);
    }

    // Success and info toasts are feedback on what the user just did; only
    // problems are worth keeping in the notification list.
    const worthKeeping = type === 'warning' || type === 'error' || options.record === true;
    if (window.notificationCenter && options.record !== false && worthKeeping) {
        window.notificationCenter.pushLocal({
            severity: type,
            title: options.title || (String(type).charAt(0).toUpperCase() + String(type).slice(1)),
            message: typeof message === 'string' ? message.replace(/<[^>]+>/g, '') : String(message),
            link,
        });
    }

    return toast;
};

window.attachModalDismiss = function (overlay, closeFn) {
    overlay.addEventListener('click', (event) => {
        if (event.target === overlay) closeFn();
    });
    overlay.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeFn();
        }
    });
};

window.confirmModal = function (message, optionsOrOnConfirm, onCancel) {
    const options = (optionsOrOnConfirm && typeof optionsOrOnConfirm === 'object') ? optionsOrOnConfirm : {};
    const onConfirm = (typeof optionsOrOnConfirm === 'function') ? optionsOrOnConfirm : options.onConfirm;
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    let title = 'Confirmation';
    let body = escapeHtml(message);
    if (message.includes('\n')) {
        const parts = message.split('\n');
        title = parts[0];
        body = parts.slice(1).map((part) => escapeHtml(part)).join('<br>');
    }

    const isDanger = options.danger === true
        || message.toLowerCase().includes('delete')
        || message.toLowerCase().includes('erase')
        || message.toLowerCase().includes('wipe')
        || message.toLowerCase().includes('rollback')
        || message.toLowerCase().includes('restore');
    const confirmBtnColor = isDanger ? 'var(--accent-danger)' : 'var(--accent-primary)';
    const requireText = String(options.requireText || '').trim();
    const requireCheckbox = String(options.requireCheckbox || '').trim();
    const confirmLabel = options.confirmLabel || (isDanger ? 'Confirm' : 'Confirm');
    const cancelLabel = options.cancelLabel || 'Cancel';
    const details = Array.isArray(options.details) ? options.details.filter(Boolean) : [];
    const warning = options.warning || '';

    overlay.innerHTML = `
        <div class="modal-content${isDanger ? ' modal-danger' : ''}" role="dialog" aria-modal="true" aria-labelledby="modal-confirm-title">
            <div class="modal-title" id="modal-confirm-title">
                <span>${escapeHtml(title)}</span>
                <button type="button" class="modal-close-x" id="modal-confirm-x" aria-label="Close">&times;</button>
            </div>
            <div class="modal-body">
                <div>${body}</div>
                ${details.length ? `
                    <dl class="modal-detail-list">
                        ${details.map((item) => `
                            <div>
                                <dt>${escapeHtml(item.label || '')}</dt>
                                <dd>${escapeHtml(item.value || '-')}</dd>
                            </div>
                        `).join('')}
                    </dl>
                ` : ''}
                ${warning ? `<div class="modal-warning">${escapeHtml(warning)}</div>` : ''}
                ${requireText ? `
                    <label class="modal-confirm-label" for="modal-confirm-input">
                        Type <strong>${escapeHtml(requireText)}</strong> to continue
                    </label>
                    <input id="modal-confirm-input" class="modal-confirm-input" autocomplete="off" spellcheck="false">
                ` : ''}
                ${requireCheckbox ? `
                    <label class="modal-confirm-label" style="display:flex; align-items:center; gap:10px; cursor:pointer; margin-top: 1rem; text-align: left;">
                        <input type="checkbox" id="modal-confirm-checkbox" style="width:auto; margin:0; accent-color: var(--accent-primary);">
                        <span>${escapeHtml(requireCheckbox)}</span>
                    </label>
                ` : ''}
            </div>
            <div class="modal-actions">
                <button id="modal-cancel" class="btn-secondary">${escapeHtml(cancelLabel)}</button>
                <button id="modal-confirm" class="btn-primary" style="background: ${confirmBtnColor};">${escapeHtml(confirmLabel)}</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    const confirmBtn = overlay.querySelector('#modal-confirm');
    const cancelBtn = overlay.querySelector('#modal-cancel');
    const closeXBtn = overlay.querySelector('#modal-confirm-x');
    const input = overlay.querySelector('#modal-confirm-input');
    const checkboxEl = overlay.querySelector('#modal-confirm-checkbox');

    const close = (value) => {
        overlay.remove();
        resolveOnce(value);
    };

    let resolveOnce = () => {};
    const syncConfirmState = () => {
        if (!confirmBtn) return;
        let isValid = true;
        if (requireText) {
            isValid = isValid && String(input?.value || '').trim() === requireText;
        }
        if (requireCheckbox) {
            isValid = isValid && !!checkboxEl?.checked;
        }
        confirmBtn.disabled = !isValid;
    };

    if (input) {
        input.focus();
        input.addEventListener('input', syncConfirmState);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !confirmBtn.disabled) {
                event.preventDefault();
                confirmBtn.click();
            }
        });
    } else if (checkboxEl) {
        checkboxEl.focus();
    } else {
        confirmBtn.focus();
    }
    
    if (checkboxEl) {
        checkboxEl.addEventListener('change', syncConfirmState);
    }
    
    syncConfirmState();

    return new Promise((resolve) => {
        resolveOnce = resolve;

        cancelBtn.onclick = () => {
            if (onCancel) onCancel();
            close(false);
        };

        if (closeXBtn) closeXBtn.onclick = () => cancelBtn.click();

        confirmBtn.onclick = () => {
            if (requireText && String(input?.value || '').trim() !== requireText) {
                input?.focus();
                return;
            }
            if (onConfirm) onConfirm();
            close(true);
        };

        attachModalDismiss(overlay, () => cancelBtn.click());
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
            <div class="modal-title" id="modal-prompt-title">
                <span>${escapeHtml(title)}</span>
                <button type="button" class="modal-close-x" id="modal-prompt-x" aria-label="Close">&times;</button>
            </div>
            <div class="modal-body">
                ${body}
                ${fieldLabel ? `<div style="margin-top: 1rem; margin-bottom: 0.4rem; font-weight: 600; text-align: left;">${escapeHtml(fieldLabel)}</div>` : ''}
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
                <button id="modal-prompt-cancel" class="btn-secondary">${escapeHtml(cancelLabel)}</button>
                <button id="modal-prompt-confirm" class="btn-primary">${escapeHtml(confirmLabel)}</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    const input = overlay.querySelector('#modal-prompt-input');
    const confirmBtn = overlay.querySelector('#modal-prompt-confirm');
    const cancelBtn = overlay.querySelector('#modal-prompt-cancel');
    const closeXBtn = overlay.querySelector('#modal-prompt-x');

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
        const cancel = () => {
            overlay.remove();
            resolve(null);
        };

        cancelBtn.onclick = cancel;
        if (closeXBtn) closeXBtn.onclick = cancel;

        confirmBtn.onclick = () => {
            const value = String(input?.value || '');
            if (required && !value.trim()) {
                input?.focus();
                return;
            }
            overlay.remove();
            resolve(value);
        };

        attachModalDismiss(overlay, cancel);
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

function renderDashboardUpdatesCard(available, versionLabel) {
    if (window.alvaosDashboardUpdates) window.alvaosDashboardUpdates(!!available, available ? versionLabel : '');
}

function renderUpdateBanner(versionLabel, versionKey) {
    // The dashboard shows updates in its own card and status line.
    if (document.getElementById('card-updates')) return;

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
        renderDashboardUpdatesCard(false, '');
        return;
    }

    const versionLabel = storedVersion || 'Update';
    const versionKey = storedVersion || 'unknown';
    renderDashboardUpdatesCard(true, storedVersion);
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

// ── Notification Center (bell icon, feed, A1-A3) ───────────────────────────────

const NOTIF_LOCAL_KEY = 'alvaos_local_notifications';
const NOTIF_LOCAL_MAX = 50;
const NOTIF_POLL_MS = 60 * 1000;
// Same rule as the backend: read entries go after a week, the rest after a month.
const NOTIF_READ_TTL_MS = 7 * 24 * 3600 * 1000;
const NOTIF_MAX_AGE_MS = 30 * 24 * 3600 * 1000;
const NOTIF_EARLIER_SHOWN = 10;

function notExpired(entry) {
    const age = Date.now() - new Date(entry?.ts).getTime();
    if (!Number.isFinite(age)) return true;
    if (age > NOTIF_MAX_AGE_MS) return false;
    if ((entry.read || entry.dismissed) && age > NOTIF_READ_TTL_MS) return false;
    return true;
}

function getAuthToken() {
    return localStorage.getItem('alvaos_token');
}

function loadLocalNotifications() {
    try {
        const raw = localStorage.getItem(NOTIF_LOCAL_KEY);
        const list = raw ? JSON.parse(raw) : [];
        // Entries from before this rule were every toast, success included.
        return Array.isArray(list)
            ? list.filter((n) => n && notExpired(n) && n.severity !== 'success' && n.severity !== 'info')
            : [];
    } catch (e) {
        return [];
    }
}

function saveLocalNotifications(list) {
    try {
        localStorage.setItem(NOTIF_LOCAL_KEY, JSON.stringify(list.slice(0, NOTIF_LOCAL_MAX)));
    } catch (e) { /* storage unavailable */ }
}

function pushLocalNotification({ severity, title, message, link } = {}) {
    const list = loadLocalNotifications();
    list.unshift({
        id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        ts: new Date().toISOString(),
        severity: severity || 'info',
        title: title || '',
        message: message || '',
        source: 'ui',
        read: false,
        dismissed: false,
        dismissible: true,
        link: link || null,
        local: true,
    });
    saveLocalNotifications(list);
    renderNotificationCenter();
}

function markLocalRead(id) {
    const list = loadLocalNotifications();
    const entry = list.find((n) => n.id === id);
    if (entry) {
        entry.read = true;
        saveLocalNotifications(list);
    }
}

function markLocalDismissed(id) {
    const list = loadLocalNotifications();
    const entry = list.find((n) => n.id === id);
    if (entry) {
        entry.read = true;
        entry.dismissed = true;
        saveLocalNotifications(list);
    }
}

function markAllLocalRead() {
    const list = loadLocalNotifications();
    list.forEach((n) => { n.read = true; });
    saveLocalNotifications(list);
}

let serverNotifications = [];
let notifPanelEl = null;
let notifBadgeEl = null;

function timeAgo(iso) {
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return '';
    const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (diffSec < 60) return 'just now';
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour}h ago`;
    const diffDay = Math.floor(diffHour / 24);
    return `${diffDay}d ago`;
}

function mergedNotificationFeed() {
    const local = loadLocalNotifications().filter((n) => !n.dismissed);
    const server = serverNotifications.filter((n) => !n.dismissed);
    return [...local, ...server].sort((a, b) => new Date(b.ts) - new Date(a.ts));
}

function notifUnreadCount() {
    return mergedNotificationFeed().filter((n) => !n.read).length;
}

function notifMarkRead(id, isLocal) {
    if (isLocal) {
        markLocalRead(id);
        renderNotificationCenter();
        return;
    }
    const token = getAuthToken();
    if (token) {
        fetch(`/api/v1/notifications/${encodeURIComponent(id)}/read`, {
            method: 'POST',
            headers: { Authorization: token }
        }).catch(() => { });
    }
    const entry = serverNotifications.find((n) => n.id === id);
    if (entry) entry.read = true;
    renderNotificationCenter();
}

function notifDismiss(id, isLocal) {
    if (isLocal) {
        markLocalDismissed(id);
        renderNotificationCenter();
        return;
    }
    const token = getAuthToken();
    if (token) {
        fetch(`/api/v1/notifications/${encodeURIComponent(id)}/dismiss`, {
            method: 'POST',
            headers: { Authorization: token }
        }).catch(() => { });
    }
    serverNotifications = serverNotifications.filter((n) => n.id !== id);
    renderNotificationCenter();
}

function notifMarkAllRead() {
    markAllLocalRead();
    const token = getAuthToken();
    if (token) {
        fetch('/api/v1/notifications/read-all', {
            method: 'POST',
            headers: { Authorization: token }
        }).catch(() => { });
    }
    serverNotifications.forEach((n) => { n.read = true; });
    renderNotificationCenter();
}

function fetchServerNotifications() {
    const token = getAuthToken();
    if (!token) return;
    fetch('/api/v1/notifications', { headers: { Authorization: token } })
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
            if (!data) return;
            serverNotifications = Array.isArray(data.notifications) ? data.notifications : [];
            renderNotificationCenter();
        })
        .catch(() => { });
}

function notifItemHtml(n) {
    const bucket = severityBucket(n.severity);
    const isLocal = !!n.local;
    const link = n.link || '';
    return `
        <div class="notif-item severity-${bucket}${n.read ? '' : ' unread'}${link ? ' clickable' : ''}" data-id="${escapeHtml(n.id)}" data-local="${isLocal ? '1' : '0'}" data-link="${escapeHtml(link)}">
            <span class="notif-item-icon">${window.alvaIcon ? window.alvaIcon(severityIconName(n.severity), '', 'aria-hidden="true"') : ''}</span>
            <div class="notif-item-body">
                <div class="notif-item-title">${escapeHtml(n.title)}</div>
                ${n.message ? `<div class="notif-item-message">${escapeHtml(n.message)}</div>` : ''}
                <div class="notif-item-time">${timeAgo(n.ts)}</div>
            </div>
            ${n.dismissible !== false ? `<button type="button" class="notif-item-dismiss" aria-label="Dismiss" data-dismiss="${escapeHtml(n.id)}" data-local="${isLocal ? '1' : '0'}">${window.alvaIcon ? window.alvaIcon('x', '', 'aria-hidden="true"') : '&times;'}</button>` : ''}
        </div>`;
}

function renderNotificationCenter() {
    if (!notifBadgeEl || !notifPanelEl) return;
    const feed = mergedNotificationFeed().filter(notExpired);
    const unread = feed.filter((n) => !n.read);
    const count = unread.length;
    notifBadgeEl.textContent = count > 9 ? '9+' : String(count);
    notifBadgeEl.style.display = count > 0 ? 'inline-flex' : 'none';
    // The badge is only red when something unread needs action.
    notifBadgeEl.classList.toggle('calm', !unread.some((n) => severityBucket(n.severity) === 'danger'));
    const bell = notifPanelEl.parentElement?.querySelector('#notif-bell');
    if (bell) bell.setAttribute('aria-label', count ? `Notifications, ${count} new` : 'Notifications');

    const markAll = notifPanelEl.querySelector('#notif-mark-all');
    if (markAll) markAll.style.display = count > 0 ? '' : 'none';

    const listEl = notifPanelEl.querySelector('#notif-panel-list');
    if (!listEl) return;

    if (feed.length === 0) {
        listEl.innerHTML = `<div class="notif-empty">Nothing here. When something needs you, it shows up here and on the dashboard.</div>`;
        return;
    }

    const earlier = feed.filter((n) => n.read);
    const earlierShown = earlier.slice(0, NOTIF_EARLIER_SHOWN);
    listEl.innerHTML = [
        unread.length ? `<div class="notif-group-label">New</div>${unread.map(notifItemHtml).join('')}` : '',
        earlierShown.length ? `<div class="notif-group-label">Earlier</div>${earlierShown.map(notifItemHtml).join('')}` : '',
        earlier.length > earlierShown.length ? `<div class="notif-more">Older ones are cleared automatically.</div>` : '',
    ].join('');

    if (window.renderAlvaIcons) window.renderAlvaIcons(listEl);

    listEl.querySelectorAll('.notif-item-dismiss').forEach((btn) => {
        btn.addEventListener('click', (event) => {
            event.stopPropagation();
            notifDismiss(btn.dataset.dismiss, btn.dataset.local === '1');
        });
    });

    listEl.querySelectorAll('.notif-item.clickable').forEach((item) => {
        item.addEventListener('click', () => {
            notifMarkRead(item.dataset.id, item.dataset.local === '1');
            const link = item.dataset.link;
            if (link) window.location.href = link;
        });
    });

    listEl.querySelectorAll('.notif-item:not(.clickable)').forEach((item) => {
        item.addEventListener('click', () => {
            notifMarkRead(item.dataset.id, item.dataset.local === '1');
        });
    });
}

function injectNotificationCenter() {
    const path = window.location.pathname;
    if (path.includes('login.html') || path.includes('setup.html')) return;
    if (!getAuthToken()) return;
    const statusBadge = document.querySelector('.topbar .status-badge');
    if (!statusBadge || !statusBadge.parentNode || document.getElementById('notif-bell')) return;

    const wrap = document.createElement('div');
    wrap.className = 'notif-bell-wrap';
    wrap.innerHTML = `
        <button type="button" id="notif-bell" class="notif-bell-btn" aria-label="Notifications">
            ${window.alvaIcon ? window.alvaIcon('bell', 'notif-bell-icon', 'aria-hidden="true"') : ''}
            <span id="notif-bell-badge" class="notif-bell-badge" style="display:none;">0</span>
        </button>
        <div id="notif-panel" class="notif-panel" style="display:none;">
            <div class="notif-panel-header">
                <span>Notifications</span>
                <button type="button" id="notif-mark-all" class="notif-mark-all">Mark all read</button>
            </div>
            <div id="notif-panel-list" class="notif-panel-list"></div>
        </div>
    `;
    statusBadge.parentNode.insertBefore(wrap, statusBadge);

    const notifBellEl = wrap.querySelector('#notif-bell');
    notifPanelEl = wrap.querySelector('#notif-panel');
    notifBadgeEl = wrap.querySelector('#notif-bell-badge');

    notifBellEl.addEventListener('click', (event) => {
        event.stopPropagation();
        const isOpen = notifPanelEl.style.display !== 'none';
        notifPanelEl.style.display = isOpen ? 'none' : 'block';
        if (!isOpen) renderNotificationCenter();
    });
    document.addEventListener('click', (event) => {
        if (notifPanelEl && notifPanelEl.style.display !== 'none' && !wrap.contains(event.target)) {
            notifPanelEl.style.display = 'none';
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && notifPanelEl) notifPanelEl.style.display = 'none';
    });
    wrap.querySelector('#notif-mark-all').addEventListener('click', (event) => {
        event.stopPropagation();
        notifMarkAllRead();
    });

    if (window.renderAlvaIcons) window.renderAlvaIcons(wrap);
    renderNotificationCenter();
}

window.notificationCenter = {
    refresh: fetchServerNotifications,
    pushLocal: pushLocalNotification,
};

function initNotificationCenter() {
    injectNotificationCenter();
    fetchServerNotifications();
    setInterval(fetchServerNotifications, NOTIF_POLL_MS);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initNotificationCenter);
} else {
    initNotificationCenter();
}
