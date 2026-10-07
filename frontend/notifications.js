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

// One dialog for every page: the frame, the title with its close button, a body
// that scrolls on its own while the title and buttons stay in view, focus kept
// inside, Escape and a click beside it to close, focus back where it was.
//
//   const dlg = openDialog({ title: 'Change the password', body: '<label>…', actions: '<button …>' });
//   dlg.$('#field').focus(); dlg.close();
//
// body and actions are HTML: escape what comes from outside. Options:
//   size: 'wide' | 'narrow'   danger: true   form: true (body and actions in a <form>)
//   bodyClass, className      dismissable: false (no Escape or click beside it)
//   onClose(value)            called once, with what close(value) got
// Buttons with data-close close the dialog.
let dialogCount = 0;
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

window.openDialog = function (options = {}) {
    const id = `dlg-${++dialogCount}`;
    const opener = document.activeElement;
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    const classes = ['modal-content', options.size ? `modal-${options.size}` : '', options.danger ? 'modal-danger' : '',
        options.className || ''].filter(Boolean).join(' ');
    const bodyClass = ['modal-body', options.bodyClass || ''].filter(Boolean).join(' ');
    const actions = options.actions ? `<div class="modal-actions">${options.actions}</div>` : '';
    const inner = `<div class="${bodyClass}">${options.body || ''}</div>${actions}`;
    overlay.innerHTML = `
        <div class="${classes}" role="dialog" aria-modal="true" aria-labelledby="${id}-title">
            <div class="modal-title"><span id="${id}-title">${options.title || ''}</span>
                <button type="button" class="modal-close-x" aria-label="Close">&times;</button></div>
            ${options.form ? `<form class="modal-form" novalidate>${inner}</form>` : inner}
        </div>`;
    const dialog = overlay.firstElementChild;
    document.body.appendChild(overlay);
    document.documentElement.classList.add('modal-open');

    let closed = false;
    const dlg = {
        overlay,
        dialog,
        body: dialog.querySelector('.modal-body'),
        form: dialog.querySelector('form.modal-form'),
        $: (selector) => dialog.querySelector(selector),
        $$: (selector) => [...dialog.querySelectorAll(selector)],
        get closed() { return closed; },
        close(value) {
            if (closed) return;
            closed = true;
            overlay.remove();
            if (!document.querySelector('.modal-overlay')) document.documentElement.classList.remove('modal-open');
            if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
            if (options.onClose) options.onClose(value);
        },
    };
    const dismiss = () => { if (options.dismissable !== false) dlg.close(); };
    dialog.querySelector('.modal-close-x').addEventListener('click', () => dlg.close());
    dialog.addEventListener('click', (event) => {
        if (event.target.closest('[data-close]')) dlg.close();
    });
    overlay.addEventListener('mousedown', (event) => { overlay.downOnBackdrop = event.target === overlay; });
    overlay.addEventListener('click', (event) => {
        // A drag that starts in a field and ends beside the dialog is not a click beside it.
        if (event.target === overlay && overlay.downOnBackdrop !== false) dismiss();
    });
    overlay.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            dismiss();
        } else if (event.key === 'Tab') {
            const items = [...dialog.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
            if (!items.length) return;
            const first = items[0], last = items[items.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    if (window.renderAlvaIcons) window.renderAlvaIcons(dialog);
    const start = dialog.querySelector('[autofocus]')
        || dialog.querySelector('.modal-body input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"]):not([disabled]), .modal-body select, .modal-body textarea')
        || dialog.querySelector('.modal-body input[type="radio"]:checked, .modal-body input[type="checkbox"]')
        || dialog.querySelector('.modal-actions .btn-primary:not([disabled])')
        || dialog.querySelector('.modal-actions button:not([disabled])')
        || dialog.querySelector('.modal-close-x');
    // After other scripts had their turn: the password eye moves its input once.
    if (options.focus !== false) setTimeout(() => { if (!dialog.contains(document.activeElement)) start?.focus(); }, 0);
    return dlg;
};

window.confirmModal = function (message, optionsOrOnConfirm, onCancel) {
    const options = (optionsOrOnConfirm && typeof optionsOrOnConfirm === 'object') ? optionsOrOnConfirm : {};
    const onConfirm = (typeof optionsOrOnConfirm === 'function') ? optionsOrOnConfirm : options.onConfirm;

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
    const requireText = String(options.requireText || '').trim();
    const requireCheckbox = String(options.requireCheckbox || '').trim();
    const confirmLabel = options.confirmLabel || 'Confirm';
    const cancelLabel = options.cancelLabel || 'Cancel';
    const details = Array.isArray(options.details) ? options.details.filter(Boolean) : [];
    const warning = options.warning || '';

    return new Promise((resolve) => {
        let confirmed = false;
        const dlg = openDialog({
            title: escapeHtml(title),
            danger: isDanger,
            body: `
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
                    <label class="modal-confirm-label modal-check">
                        <input type="checkbox" id="modal-confirm-checkbox">
                        <span>${escapeHtml(requireCheckbox)}</span>
                    </label>
                ` : ''}`,
            actions: `
                <button type="button" id="modal-cancel" class="btn-secondary">${escapeHtml(cancelLabel)}</button>
                <button type="button" id="modal-confirm" class="btn-primary${isDanger ? ' btn-danger' : ''}">${escapeHtml(confirmLabel)}</button>`,
            onClose: () => {
                if (!confirmed && onCancel) onCancel();
                resolve(confirmed);
            },
        });
        const confirmBtn = dlg.$('#modal-confirm');
        const input = dlg.$('#modal-confirm-input');
        const checkboxEl = dlg.$('#modal-confirm-checkbox');
        const valid = () => (!requireText || String(input?.value || '').trim() === requireText)
            && (!requireCheckbox || !!checkboxEl?.checked);
        const sync = () => { confirmBtn.disabled = !valid(); };
        input?.addEventListener('input', sync);
        input?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && valid()) {
                event.preventDefault();
                confirmBtn.click();
            }
        });
        checkboxEl?.addEventListener('change', sync);
        sync();
        if (!input && !checkboxEl) confirmBtn.focus();
        dlg.$('#modal-cancel').onclick = () => dlg.close();
        confirmBtn.onclick = () => {
            if (!valid()) {
                input?.focus();
                return;
            }
            confirmed = true;
            if (onConfirm) onConfirm();
            dlg.close();
        };
    });
};

window.showConfirm = window.confirmModal;

window.promptModal = function (message, options = {}) {
    let title = options.title || 'Input Required';
    let body = String(message || '');
    if (!options.title && body.includes('\n')) {
        const parts = body.split('\n');
        title = parts[0] || title;
        body = parts.slice(1).join('<br>');
    }

    const inputType = options.type || 'text';
    const confirmLabel = options.confirmLabel || 'Continue';
    const cancelLabel = options.cancelLabel || 'Cancel';
    const fieldLabel = options.label || '';
    const required = options.required !== false;

    return new Promise((resolve) => {
        let result = null;
        const dlg = openDialog({
            title: escapeHtml(title),
            form: true,
            body: `
                ${body}
                ${fieldLabel ? `<label class="modal-confirm-label" for="modal-prompt-input">${escapeHtml(fieldLabel)}</label>` : ''}
                <input id="modal-prompt-input" class="modal-prompt-input" type="${escapeHtml(inputType)}"
                    value="${escapeHtml(options.value || '')}" placeholder="${escapeHtml(options.placeholder || '')}"
                    ${fieldLabel ? '' : `aria-label="${escapeHtml(title)}"`}>`,
            actions: `
                <button type="button" id="modal-prompt-cancel" class="btn-secondary" data-close>${escapeHtml(cancelLabel)}</button>
                <button type="submit" id="modal-prompt-confirm" class="btn-primary">${escapeHtml(confirmLabel)}</button>`,
            onClose: () => resolve(result),
        });
        const input = dlg.$('#modal-prompt-input');
        const confirmBtn = dlg.$('#modal-prompt-confirm');
        const sync = () => { confirmBtn.disabled = required && !String(input.value || '').trim(); };
        input.select();
        input.addEventListener('input', sync);
        sync();
        dlg.form.addEventListener('submit', (event) => {
            event.preventDefault();
            const value = String(input.value || '');
            if (required && !value.trim()) {
                input.focus();
                return;
            }
            result = value;
            dlg.close();
        });
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
    if (document.getElementById('card-updates') || document.getElementById('upd-status')) return;

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

function applyUpdateIndicators({ available, version, checkedAt } = {}) {
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

window.setUpdateIndicators = function (state) {
    applyUpdateIndicators(state);
    if (window.alvaosBrandRefresh) window.alvaosBrandRefresh();
};

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
        window.setUpdateIndicators({ available: cachedAvailable, version: cachedVersion });
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
            window.setUpdateIndicators({
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
    const actions = document.querySelector('.topbar .topbar-actions');
    if (!actions || document.getElementById('notif-bell')) return;

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
    actions.prepend(wrap);

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

// Right after signing in: when and from where the previous sign-in was, and
// wrong passwords tried since (auth_manager.record_signin). Shown once.
(function showLastSignin() {
    let info = null;
    try {
        info = JSON.parse(sessionStorage.getItem('alvaos_last_signin') || 'null');
        sessionStorage.removeItem('alvaos_last_signin');
    } catch (_e) {
        return;
    }
    if (!info) return;
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const when = (iso) => {
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? '' : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    };
    const show = () => {
        if (typeof window.showToast !== 'function') return;
        const prev = info.previous;
        const parts = [];
        if (prev) parts.push(`Last sign-in: ${esc(when(prev.at))}, ${esc(prev.device || 'unknown device')}${prev.ip ? ` from ${esc(prev.ip)}` : ''}.`);
        if (info.failed > 0) {
            parts.push(`<strong>${Number(info.failed)} wrong password${info.failed === 1 ? '' : 's'}</strong> since then${(info.failed_from || []).length ? ` (from ${info.failed_from.map(esc).join(', ')})` : ''}. If that was not you, change the password.`);
        }
        if (!parts.length) return;
        window.showToast(parts.join(' '), info.failed > 0 ? 'warning' : 'info',
            info.failed > 0 ? { link: 'system.html#security' } : {});
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(show, 600));
    else setTimeout(show, 600);
})();
