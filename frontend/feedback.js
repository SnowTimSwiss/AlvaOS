(function () {
    function shouldInit() {
        const path = window.location.pathname || '';
        return !path.includes('login.html') && !path.includes('setup.html');
    }

    function notify(message, type = 'info') {
        if (window.showToast) {
            window.showToast(message, type);
            return;
        }
        if (window.alert) window.alert(message);
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
                    <button id="feedback-github-btn" class="btn-primary">Open GitHub Issue</button>
                </div>
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

        overlay.querySelector('#feedback-github-btn')?.addEventListener('click', () => {
            const payload = buildPayload();
            if (!payload.message) {
                notify('Please add a feedback message first.', 'warning');
                return;
            }
            openGithubIssue(payload);
        });

        overlay.querySelector('#feedback-message')?.focus();
    }

    function init() {
        if (!shouldInit()) return;
        if (document.getElementById('feedback-fab')) return;

        const button = document.createElement('button');
        button.id = 'feedback-fab';
        button.className = 'feedback-fab';
        button.type = 'button';
        button.innerHTML = `
            ${window.alvaIcon ? window.alvaIcon('message-square', '', 'aria-hidden="true"') : ''}
            <span>Feedback</span>
        `;
        button.addEventListener('click', openModal);
        document.body.appendChild(button);
        if (window.renderAlvaIcons) window.renderAlvaIcons(button);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
