// AlvaOS Web UI - shared page code (auth fetch, logout, clock, setup redirect).
// The dashboard itself lives in dashboard.js.
const API_BASE = '/api/v1';
let csrfToken = localStorage.getItem('alvaos_csrf_token') || null;
let csrfTokenPromise = null;

function isApiRequest(resource) {
    const url = typeof resource === 'string' ? resource : resource?.url;
    if (!url) return false;
    try {
        const parsed = new URL(url, window.location.origin);
        return parsed.origin === window.location.origin && parsed.pathname.startsWith('/api/v1/');
    } catch {
        return false;
    }
}

function getRequestMethod(resource, options = {}) {
    return String(options.method || resource?.method || 'GET').toUpperCase();
}

function isStateChangingMethod(method) {
    return ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);
}

function headersToObject(headers) {
    const result = {};
    if (!headers) return result;

    if (headers instanceof Headers) {
        headers.forEach((value, key) => {
            result[key] = value;
        });
        return result;
    }

    if (Array.isArray(headers)) {
        headers.forEach(([key, value]) => {
            result[key] = value;
        });
        return result;
    }

    return { ...headers };
}

function storeCsrfToken(token) {
    csrfToken = token || null;
    if (csrfToken) {
        localStorage.setItem('alvaos_csrf_token', csrfToken);
    } else {
        localStorage.removeItem('alvaos_csrf_token');
    }
}

// Page Loading Overlay
function showPageLoading() {
    const overlay = document.getElementById('page-loading-overlay');
    if (overlay) {
        overlay.style.display = 'flex';
    }
}

function hidePageLoading() {
    const overlay = document.getElementById('page-loading-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

document.addEventListener('DOMContentLoaded', function() {
    const navLinks = document.querySelectorAll('.nav-link');
    navLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            const href = this.getAttribute('href');
            if (href && !href.startsWith('#') && href !== window.location.pathname) {
                showPageLoading();
            }
        });
    });
    
    // Hide loading overlay when page is fully loaded
    window.addEventListener('load', hidePageLoading);
});

// Fetch CSRF token after login
async function fetchCsrfToken() {
    if (csrfToken) return csrfToken;
    if (csrfTokenPromise) return csrfTokenPromise;

    csrfTokenPromise = (async () => {
        const token = localStorage.getItem('alvaos_token');
        if (!token) return null;

        const response = await window.__alvaosNativeFetch(`${API_BASE}/auth/csrf-token`, {
            headers: { 'Authorization': token }
        });
        if (!response.ok) return null;

        const data = await response.json();
        storeCsrfToken(data.csrf_token || null);
        return csrfToken;
    })();

    try {
        return await csrfTokenPromise;
    } catch (error) {
        console.error('Failed to fetch CSRF token:', error);
        return null;
    } finally {
        csrfTokenPromise = null;
    }
}

// Get headers for API requests, including CSRF token for state-changing operations
function getHeaders(includeCsrf = false) {
    const token = localStorage.getItem('alvaos_token');
    const headers = {
        'Content-Type': 'application/json'
    };
    if (token) {
        headers['Authorization'] = token;
    }
    if (includeCsrf && csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
    }
    return headers;
}

function installAuthenticatedFetch() {
    if (window.__alvaosNativeFetch) return;

    window.__alvaosNativeFetch = window.fetch.bind(window);
    window.fetch = async function alvaosFetch(resource, options = {}) {
        if (!isApiRequest(resource)) {
            return window.__alvaosNativeFetch(resource, options);
        }

        const token = localStorage.getItem('alvaos_token') || '';
        const method = getRequestMethod(resource, options);
        const headers = headersToObject(options.headers || resource?.headers);

        if (token && !headers.Authorization && !headers.authorization) {
            headers.Authorization = token;
        }

        if (token && isStateChangingMethod(method) && !headers['X-CSRF-Token'] && !headers['x-csrf-token']) {
            const freshToken = await fetchCsrfToken();
            if (freshToken) headers['X-CSRF-Token'] = freshToken;
        }

        return window.__alvaosNativeFetch(resource, { ...options, headers });
    };
}

window.alvaosStoreCsrfToken = storeCsrfToken;
window.alvaosFetchCsrfToken = fetchCsrfToken;
window.alvaosGetHeaders = getHeaders;
installAuthenticatedFetch();

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Escape a value for use inside a quoted JS string in an inline handler, e.g.
// onclick="doThing('${jsArg(name)}')". The browser HTML-decodes the attribute
// first and only then parses it as JS, so the value has to survive both: escape
// for JS, then for HTML. Without this a share or disk name containing a quote
// breaks the handler outright.
function escapeJsString(value) {
    return String(value ?? '')
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/"/g, '\\"')
        .replace(/\n/g, '\\n')
        .replace(/\r/g, '\\r')
        .replace(/\u2028/g, '\\u2028')
        .replace(/\u2029/g, '\\u2029');
}

function jsArg(value) {
    return escapeHtml(escapeJsString(value));
}

window.escapeHtml = escapeHtml;
window.escapeJsString = escapeJsString;
window.jsArg = jsArg;

// Ends the session on the server too. Sessions outlive a backend restart, so
// dropping the local token alone would leave it usable until it expires.
async function alvaosLogout() {
    const token = localStorage.getItem('alvaos_token');
    if (token) {
        try {
            await window.__alvaosNativeFetch(`${API_BASE}/auth/logout`, {
                method: 'POST',
                headers: {
                    'Authorization': token,
                    'X-CSRF-Token': (await fetchCsrfToken()) || ''
                }
            });
        } catch (error) {
            // Even if the call fails, clear locally and send the user to login.
            console.error('Logout request failed:', error);
        }
    }
    localStorage.removeItem('alvaos_token');
    localStorage.removeItem('alvaos_csrf_token');
    csrfToken = null;
    window.location.href = 'login.html?reason=signed-out';
}

window.alvaosLogout = alvaosLogout;

function updateClock() {
    const clockElement = document.getElementById('clock');
    if (!clockElement) return;

    const now = new Date();
    clockElement.textContent = now.toLocaleTimeString();
}

// Show error message (using Toast or overlay for critical)
function showError(message) {
    if (window.showToast) {
        window.showToast(message, 'error');
    } else {
        console.error(message);
    }
}

let isReconnecting = false;

function handleConnectionError() {
    if (isReconnecting) return;
    isReconnecting = true;

    const overlay = document.createElement('div');
    overlay.id = 'reconnect-overlay';
    overlay.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: var(--scrim); z-index: 20000;
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        color: white; backdrop-filter: blur(5px);
    `;
    overlay.innerHTML = `
        <div style="font-size: 3rem; margin-bottom: 1rem; animation: spin 1s linear infinite;">${window.alvaIcon ? window.alvaIcon('refresh-cw', '', 'aria-hidden="true"') : '...'}</div>
        <h2 style="margin-bottom: 0.5rem;">Connection Lost</h2>
        <p style="color: var(--text-secondary);">Waiting for AlvaOS to come back online...</p>
        <style>@keyframes spin { 100% { transform: rotate(360deg); } }</style>
    `;
    document.body.appendChild(overlay);
    if (window.renderAlvaIcons) window.renderAlvaIcons(overlay);

    setTimeout(startPolling, 3000);

    function startPolling() {
        const interval = setInterval(async () => {
            try {
                const controller = new AbortController();
                const id = setTimeout(() => controller.abort(), 2000);

                const token = localStorage.getItem('alvaos_token');
                const res = await fetch(`${API_BASE}/system/info`, {
                    signal: controller.signal,
                    headers: { 'Authorization': token || '' }
                });
                clearTimeout(id);

                if (res.ok || res.status === 401) {
                    clearInterval(interval);
                    isReconnecting = false;
                    document.getElementById('reconnect-overlay')?.remove();

                    if (res.status === 401) {
                        window.location.href = '/login.html';
                    } else {
                        if (window.showToast) window.showToast('We are back online!', 'success');
                        if (window.alvaosDashboardRefresh) window.alvaosDashboardRefresh();
                    }
                }
            } catch (e) {
                // Still down, keep waiting
            }
        }, 3000);
    }
}

async function init() {
    try {
        const response = await fetch(`${API_BASE}/setup/status`);
        if (response.ok) {
            const data = await response.json();
            if (!data.setup_complete) {
                window.location.href = '/setup.html';
                return;
            }

            const token = localStorage.getItem('alvaos_token');
            if (!token) {
                window.location.href = '/login.html';
                return;
            }
        }
    } catch (error) {
        console.error('Failed to check setup status:', error);
    }

    updateClock();
    setInterval(updateClock, 1000);

    if (window.triggerUpdateCheck) {
        window.triggerUpdateCheck();
    }

}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

// ── Tab accessibility ────────────────────────────────────────────────────────
// Each page wires its own tab switching. Rather than change five different
// implementations, this decorates whatever is on the page with the ARIA tab
// pattern and adds arrow-key navigation, delegating the actual switch back to
// the page's existing click handler.
function enhanceTabBars() {
    document.querySelectorAll('.tab-bar').forEach((bar) => {
        const tabs = Array.from(bar.querySelectorAll('.tab-btn'));
        if (tabs.length === 0) return;

        bar.setAttribute('role', 'tablist');

        const syncState = () => {
            tabs.forEach((tab) => {
                const isActive = tab.classList.contains('active');
                tab.setAttribute('role', 'tab');
                tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
                // Roving tabindex: one stop for the whole tablist.
                tab.tabIndex = isActive ? 0 : -1;

                const name = tab.getAttribute('data-tab');
                const panel = name && document.getElementById(`tab-${name}`);
                if (panel) {
                    tab.setAttribute('aria-controls', panel.id);
                    panel.setAttribute('role', 'tabpanel');
                    if (!panel.getAttribute('aria-label') && tab.textContent.trim()) {
                        panel.setAttribute('aria-label', tab.textContent.trim());
                    }
                }
            });
        };

        syncState();
        // The page toggles .active on click; re-sync right after it does.
        bar.addEventListener('click', () => setTimeout(syncState, 0));

        bar.addEventListener('keydown', (event) => {
            const currentIndex = tabs.indexOf(document.activeElement);
            if (currentIndex === -1) return;

            let nextIndex = null;
            if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
            else if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
            else if (event.key === 'Home') nextIndex = 0;
            else if (event.key === 'End') nextIndex = tabs.length - 1;
            if (nextIndex === null) return;

            event.preventDefault();
            tabs[nextIndex].focus();
            tabs[nextIndex].click();
        });
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', enhanceTabBars);
} else {
    enhanceTabBars();
}

// ── Calm failure states ──────────────────────────────────────────────────────
// A section that could not load is not an emergency and must not be painted in
// the colour reserved for "you have to act now". It says what did not work, what
// it means, and offers a way forward - per the No Fear UX rules in DESIGN.md.
function renderLoadFailure(container, options = {}) {
    if (!container) return;
    const title = options.title || 'Could not load this section';
    const detail = options.detail
        || 'The NAS did not answer. It may still be starting up after an update.';
    const retryId = `retry-${Math.random().toString(36).slice(2, 9)}`;

    container.innerHTML = `
        <div class="load-failure" style="grid-column: 1/-1;">
            <div class="load-failure-title">${escapeHtml(title)}</div>
            <p class="load-failure-detail">${escapeHtml(detail)}</p>
            ${options.onRetry ? `<button class="btn-secondary" id="${retryId}">Try again</button>` : ''}
        </div>
    `;

    if (options.onRetry) {
        const button = document.getElementById(retryId);
        if (button) {
            button.addEventListener('click', () => {
                button.disabled = true;
                button.textContent = 'Retrying...';
                Promise.resolve(options.onRetry()).catch(() => {
                    button.disabled = false;
                    button.textContent = 'Try again';
                });
            });
        }
    }
}

window.renderLoadFailure = renderLoadFailure;
