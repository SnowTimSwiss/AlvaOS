(function () {
    const FEEDBACK_EMAIL = 'feedback-alvaos@timserver.uk';
    const DISMISS_FOREVER_KEY = 'alvaos_feedback_fab_dismissed_forever';
    const DISMISS_SESSION_KEY = 'alvaos_feedback_fab_dismissed_session';

    function shouldInit() {
        const path = window.location.pathname || '';
        if (path.includes('login.html') || path.includes('setup.html')) return false;
        if (localStorage.getItem(DISMISS_FOREVER_KEY) === 'true') return false;
        if (sessionStorage.getItem(DISMISS_SESSION_KEY) === 'true') return false;
        return true;
    }

    function notify(message, type = 'info') {
        if (window.showToast) {
            window.showToast(message, type);
            return;
        }
        if (type === 'error') {
            console.error(message);
        } else {
            console.log(message);
        }
    }

    function buildPayload() {
        const categoryEl = document.getElementById('feedback-category');
        const messageEl = document.getElementById('feedback-message');
        const contactEl = document.getElementById('feedback-contact');

        const category = String(categoryEl?.value || 'general').trim();
        const message = String(messageEl?.value || '').trim();
        const contact = String(contactEl?.value || '').trim();
        const page = window.location.pathname || '/';
        const ts = new Date().toISOString();

        return {
            category,
            message,
            contact,
            page,
            timestamp: ts
        };
    }

    function payloadText(payload) {
        return [
            `Category: ${payload.category}`,
            `Page: ${payload.page}`,
            `Timestamp: ${payload.timestamp}`,
            payload.contact ? `Contact: ${payload.contact}` : 'Contact: (not provided)',
            '',
            'Message:',
            payload.message
        ].join('\n');
    }

    async function copyToClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }

        const temp = document.createElement('textarea');
        temp.value = text;
        temp.setAttribute('readonly', 'readonly');
        temp.style.position = 'fixed';
        temp.style.left = '-9999px';
        document.body.appendChild(temp);
        temp.focus();
        temp.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(temp);
        return ok;
    }

    function openGithubIssue(payload) {
        const title = `[Feedback] ${payload.category}`;
        const body = payloadText(payload);
        const url = `https://github.com/SnowTimSwiss/AlvaOS/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
        window.open(url, '_blank', 'noopener,noreferrer');
    }

    function sendByEmail(payload) {
        const subject = `[AlvaOS Feedback] ${payload.category}`;
        const body = payloadText(payload);
        const url = `mailto:${FEEDBACK_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
        window.location.href = url;
    }

    function closeModal() {
        document.getElementById('feedback-overlay')?.remove();
    }

    function openModal() {
        if (document.getElementById('feedback-overlay')) return;

        const overlay = document.createElement('div');
        overlay.id = 'feedback-overlay';
        overlay.className = 'modal-overlay feedback-overlay';
        overlay.innerHTML = `
            <div class="feedback-modal" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
                <div class="feedback-header">
                    <h2 id="feedback-title">Send Feedback</h2>
                    <button id="feedback-close-btn" class="btn-secondary feedback-close-btn" aria-label="Close">
                        ${window.alvaIcon ? window.alvaIcon('x', '', 'aria-hidden="true"') : 'x'}
                    </button>
                </div>
                <p class="feedback-sub">Share bugs, ideas, or UX pain points for AlvaOS.</p>

                <div class="setting-group">
                    <label class="setting-label" for="feedback-category">Category</label>
                    <select id="feedback-category">
                        <option value="general">General</option>
                        <option value="bug">Bug</option>
                        <option value="ux">UX</option>
                        <option value="performance">Performance</option>
                        <option value="feature">Feature Request</option>
                    </select>
                </div>

                <div class="setting-group">
                    <label class="setting-label" for="feedback-message">Message</label>
                    <textarea id="feedback-message" rows="6" placeholder="What happened, what you expected, and steps to reproduce (if bug)."></textarea>
                </div>

                <div class="setting-group">
                    <label class="setting-label" for="feedback-contact">Contact (optional)</label>
                    <input id="feedback-contact" type="text" placeholder="Email or handle">
                </div>

                <div class="feedback-actions">
                    <button id="feedback-copy-btn" class="btn-secondary">Copy Feedback</button>
                    <button id="feedback-email-btn" class="btn-primary">Send via Email</button>
                </div>
                <button id="feedback-github-link" class="feedback-github-link" type="button">Prefer GitHub? Open an issue instead</button>
            </div>
        `;
        document.body.appendChild(overlay);
        if (window.renderAlvaIcons) window.renderAlvaIcons(overlay);

        overlay.querySelector('#feedback-close-btn')?.addEventListener('click', closeModal);
        overlay.addEventListener('click', (event) => {
            if (event.target?.id === 'feedback-overlay') closeModal();
        });

        overlay.querySelector('#feedback-copy-btn')?.addEventListener('click', async () => {
            const payload = buildPayload();
            if (!payload.message) {
                notify('Please add a feedback message first.', 'warning');
                return;
            }
            try {
                const ok = await copyToClipboard(payloadText(payload));
                if (ok) notify('Feedback copied to clipboard.', 'success');
                else notify('Could not copy feedback text.', 'error');
            } catch {
                notify('Clipboard access failed.', 'error');
            }
        });

        overlay.querySelector('#feedback-email-btn')?.addEventListener('click', () => {
            const payload = buildPayload();
            if (!payload.message) {
                notify('Please add a feedback message first.', 'warning');
                return;
            }
            sendByEmail(payload);
        });

        overlay.querySelector('#feedback-github-link')?.addEventListener('click', () => {
            const payload = buildPayload();
            if (!payload.message) {
                notify('Please add a feedback message first.', 'warning');
                return;
            }
            openGithubIssue(payload);
        });

        overlay.querySelector('#feedback-message')?.focus();
    }

    function closeFabDismissMenu() {
        document.getElementById('feedback-fab-dismiss-menu')?.remove();
    }

    function openFabDismissMenu(wrap) {
        if (document.getElementById('feedback-fab-dismiss-menu')) {
            closeFabDismissMenu();
            return;
        }

        const menu = document.createElement('div');
        menu.id = 'feedback-fab-dismiss-menu';
        menu.className = 'feedback-fab-dismiss-menu';
        menu.innerHTML = `
            <button type="button" class="feedback-fab-dismiss-option" data-action="session">Hide for now</button>
            <button type="button" class="feedback-fab-dismiss-option" data-action="forever">Don't show again</button>
        `;
        wrap.appendChild(menu);

        menu.querySelector('[data-action="session"]')?.addEventListener('click', () => {
            sessionStorage.setItem(DISMISS_SESSION_KEY, 'true');
            wrap.remove();
            document.body.classList.remove('has-feedback-fab');
        });
        menu.querySelector('[data-action="forever"]')?.addEventListener('click', () => {
            localStorage.setItem(DISMISS_FOREVER_KEY, 'true');
            wrap.remove();
            document.body.classList.remove('has-feedback-fab');
        });

        setTimeout(() => {
            document.addEventListener('click', function handleOutside(event) {
                if (!menu.contains(event.target) && event.target.id !== 'feedback-fab-close') {
                    closeFabDismissMenu();
                    document.removeEventListener('click', handleOutside);
                }
            });
        }, 0);
    }

    function init() {
        if (!shouldInit()) return;
        if (document.getElementById('feedback-fab-wrap')) return;

        const wrap = document.createElement('div');
        wrap.id = 'feedback-fab-wrap';
        wrap.className = 'feedback-fab-wrap';
        wrap.innerHTML = `
            <button id="feedback-fab-close" class="feedback-fab-close" type="button" aria-label="Dismiss feedback button">
                ${window.alvaIcon ? window.alvaIcon('x', '', 'aria-hidden="true"') : 'x'}
            </button>
            <button id="feedback-fab" class="feedback-fab" type="button">
                ${window.alvaIcon ? window.alvaIcon('message-square', '', 'aria-hidden="true"') : ''}
                <span>Feedback</span>
            </button>
        `;
        document.body.appendChild(wrap);
        // Room at the end of the page so the button never covers the last row.
        document.body.classList.add('has-feedback-fab');
        if (window.renderAlvaIcons) window.renderAlvaIcons(wrap);

        wrap.querySelector('#feedback-fab')?.addEventListener('click', openModal);
        wrap.querySelector('#feedback-fab-close')?.addEventListener('click', (event) => {
            event.stopPropagation();
            openFabDismissMenu(wrap);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
