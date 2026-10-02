// AlvaOS navigation: the brand block (NAS name and status), the mobile menu
// and the account menu. The markup is the same static HTML on every page; this
// only makes it live.
(function () {
    const NAME_KEY = 'alvaos_nas_name';
    const STATUS_POLL_MS = 120000;
    const LEVEL_TEXT = { bad: 'Needs attention', warn: 'Worth a look', info: 'Update available', ok: 'Online' };
    const LEVEL_DOT = { bad: 'critical', warn: 'warning', info: 'info', ok: 'online' };
    let ownStatus = true;  // false once a page (the dashboard) reports a richer status

    function $(id) { return document.getElementById(id); }

    // ── Brand: NAS name and status ───────────────────────────────────────────

    function setName(name) {
        const clean = String(name || '').trim();
        if (!clean || clean === 'unknown') return;
        const el = $('brand-name');
        if (el) el.textContent = clean;
        try { localStorage.setItem(NAME_KEY, clean); } catch (_e) { /* storage off */ }
    }

    function setStatus(level) {
        const key = LEVEL_TEXT[level] ? level : 'ok';
        const dot = document.querySelector('#brand-status .status-dot');
        if (dot) dot.className = `status-dot ${LEVEL_DOT[key]}`;
        const text = $('brand-status-text');
        if (text) text.textContent = LEVEL_TEXT[key];
    }

    // Pages with their own, more complete view of the NAS (the dashboard) call
    // this; the brand then stops guessing from alerts on its own.
    window.alvaosBrandStatus = function (level) {
        ownStatus = false;
        setStatus(level === 'setup' ? 'ok' : level);
    };
    window.alvaosBrandName = setName;
    // notifications.js calls this when it learns whether an update is ready.
    window.alvaosBrandRefresh = () => refreshOwnStatus();

    async function refreshOwnStatus() {
        if (!ownStatus || !localStorage.getItem('alvaos_token')) return;
        if (document.getElementById('status-line')) return; // the dashboard reports itself
        let level = 'ok';
        try {
            const response = await fetch('/api/v1/alerts');
            if (response.ok) {
                const data = await response.json();
                if (Number(data?.summary?.critical) > 0) level = 'bad';
                else if (Number(data?.summary?.warning) > 0) level = 'warn';
            }
        } catch (_e) { /* offline: app.js shows the reconnect overlay */ }
        if (level === 'ok' && localStorage.getItem('alvaos_update_available') === 'true') level = 'info';
        if (ownStatus) setStatus(level);
    }

    async function loadName() {
        let cached = '';
        try { cached = localStorage.getItem(NAME_KEY) || ''; } catch (_e) { /* storage off */ }
        if (cached) {
            setName(cached);
            return;
        }
        // Not known yet in this browser: ask once. The dashboard keeps it fresh.
        if (document.getElementById('status-line') || !localStorage.getItem('alvaos_token')) return;
        try {
            const response = await fetch('/api/v1/system/info');
            if (response.ok) setName((await response.json())?.network?.hostname);
        } catch (_e) { /* keep "AlvaOS" */ }
    }

    // ── Mobile menu ──────────────────────────────────────────────────────────

    function setupDrawer() {
        const button = $('menu-btn');
        const sidebar = $('sidebar');
        const scrim = $('nav-scrim');
        if (!button || !sidebar || !scrim) return;

        const isOpen = () => document.body.classList.contains('nav-open');
        const open = () => {
            document.body.classList.add('nav-open');
            scrim.hidden = false;
            button.setAttribute('aria-expanded', 'true');
            button.setAttribute('aria-label', 'Close menu');
            (sidebar.querySelector('.nav-link.active') || sidebar.querySelector('a'))?.focus();
        };
        const close = (returnFocus) => {
            if (!isOpen()) return;
            document.body.classList.remove('nav-open');
            scrim.hidden = true;
            button.setAttribute('aria-expanded', 'false');
            button.setAttribute('aria-label', 'Open menu');
            if (returnFocus) button.focus();
        };

        button.addEventListener('click', () => (isOpen() ? close(true) : open()));
        scrim.addEventListener('click', () => close(true));
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') close(true);
        });
        // Back to desktop width: never leave the page locked behind the scrim.
        window.matchMedia('(min-width: 981px)').addEventListener('change', (event) => {
            if (event.matches) close(false);
        });
    }

    // ── Account menu ─────────────────────────────────────────────────────────

    function setupAccount() {
        const button = $('account-btn');
        const menu = $('account-menu');
        if (!button || !menu) return;

        const close = (returnFocus) => {
            if (menu.hidden) return;
            menu.hidden = true;
            button.setAttribute('aria-expanded', 'false');
            if (returnFocus) button.focus();
        };
        button.addEventListener('click', (event) => {
            event.stopPropagation();
            if (!menu.hidden) {
                close(false);
                return;
            }
            menu.hidden = false;
            button.setAttribute('aria-expanded', 'true');
            (menu.querySelector('[aria-checked="true"]') || menu.querySelector('[role^="menuitem"]'))?.focus();
        });
        document.addEventListener('click', (event) => {
            if (!menu.contains(event.target)) close(false);
        });
        menu.addEventListener('keydown', (event) => {
            const items = Array.from(menu.querySelectorAll('[role^="menuitem"]'));
            const index = items.indexOf(document.activeElement);
            if (event.key === 'Escape') {
                event.preventDefault();
                close(true);
            } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const step = event.key === 'ArrowDown' ? 1 : -1;
                items[(index + step + items.length) % items.length]?.focus();
            } else if (event.key === 'Tab') {
                close(false);
            }
        });
    }

    // ── Appearance (Auto / Light / Dark) ────────────────────────────────────

    function setupTheme() {
        const buttons = Array.from(document.querySelectorAll('[data-theme-choice]'));
        if (!buttons.length || !window.alvaosTheme) return;
        const sync = () => {
            const choice = window.alvaosTheme.get();
            buttons.forEach((b) => b.setAttribute('aria-checked', String(b.dataset.themeChoice === choice)));
        };
        buttons.forEach((b) => b.addEventListener('click', (event) => {
            event.stopPropagation(); // keep the menu open to see the result
            window.alvaosTheme.set(b.dataset.themeChoice);
        }));
        document.addEventListener('alvaos-theme', sync);
        sync();
    }

    function start() {
        setupTheme();
        setupDrawer();
        setupAccount();
        loadName();
        refreshOwnStatus();
        setInterval(refreshOwnStatus, STATUS_POLL_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
