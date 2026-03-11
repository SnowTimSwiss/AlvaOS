(function () {
    const WRAP_CLASS = 'password-toggle-wrap';
    const BTN_CLASS = 'password-toggle-btn';
    const EYE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-eye" aria-hidden="true"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z"></path><circle cx="12" cy="12" r="2.5"></circle></svg>';
    const EYE_OFF_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-eye-off" aria-hidden="true"><path d="M10.58 10.58A2 2 0 0 0 12 14a2 2 0 0 0 1.42-.58"></path><path d="M9.88 4.24A9.86 9.86 0 0 1 12 4c6.5 0 10 8 10 8a17.6 17.6 0 0 1-2.67 3.77"></path><path d="M6.61 6.61A17.4 17.4 0 0 0 2 12s3.5 8 10 8a9.74 9.74 0 0 0 5.39-1.61"></path><line x1="2" x2="22" y1="2" y2="22"></line></svg>';

    function setButtonIcon(button, isShown) {
        if (!button) return;
        button.innerHTML = isShown ? EYE_OFF_ICON : EYE_ICON;
    }

    function enhancePasswordInput(input) {
        if (!input || input.dataset.passwordToggleInit === '1') return;
        if (input.tagName !== 'INPUT') return;
        if (input.type !== 'password') return;
        if (input.disabled) return;

        const parent = input.parentElement;
        if (!parent) return;

        const wrapper = document.createElement('div');
        wrapper.className = WRAP_CLASS;
        wrapper.style.position = 'relative';
        wrapper.style.display = 'flex';
        wrapper.style.alignItems = 'center';
        wrapper.style.width = '100%';

        parent.insertBefore(wrapper, input);
        wrapper.appendChild(input);

        const button = document.createElement('button');
        button.type = 'button';
        button.className = BTN_CLASS;
        setButtonIcon(button, false);
        button.setAttribute('aria-label', 'Show password');
        button.title = 'Show password';
        button.style.position = 'absolute';
        button.style.right = '0.5rem';
        button.style.top = '50%';
        button.style.transform = 'translateY(-50%)';
        button.style.border = 'none';
        button.style.background = 'transparent';
        button.style.cursor = 'pointer';
        button.style.color = 'var(--text-secondary)';
        button.style.fontSize = '1rem';
        button.style.lineHeight = '1';
        button.style.padding = '0.1rem';
        button.style.opacity = '0.85';

        const originalPaddingRight = input.style.paddingRight;
        input.style.paddingRight = '2.2rem';

        button.addEventListener('click', () => {
            const shown = input.type === 'text';
            input.type = shown ? 'password' : 'text';
            setButtonIcon(button, !shown);
            button.setAttribute('aria-label', shown ? 'Show password' : 'Hide password');
            button.title = shown ? 'Show password' : 'Hide password';
            button.style.color = shown ? 'var(--text-secondary)' : 'var(--accent-primary)';
        });

        input.addEventListener('blur', () => {
            // Optional safety: keep revealed state if user wants it, no forced reset.
        });

        wrapper.appendChild(button);
        if (window.renderAlvaIcons) window.renderAlvaIcons(wrapper);
        input.dataset.passwordToggleInit = '1';
        input.dataset.passwordToggleOriginalPaddingRight = originalPaddingRight || '';
    }

    function scan(root) {
        const scope = root && root.querySelectorAll ? root : document;
        const inputs = scope.querySelectorAll('input[type="password"]');
        inputs.forEach(enhancePasswordInput);
    }

    let _passwordObserver = null;

    function initObserver() {
        if (!window.MutationObserver) return;
        _passwordObserver = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (!node || node.nodeType !== 1) return;
                    if (node.matches && node.matches('input[type="password"]')) {
                        enhancePasswordInput(node);
                    } else if (node.querySelectorAll) {
                        scan(node);
                    }
                });
            });
        });
        _passwordObserver.observe(document.body, { childList: true, subtree: true });
    }

    window.disconnectPasswordObserver = function() {
        if (_passwordObserver) {
            _passwordObserver.disconnect();
            _passwordObserver = null;
        }
    };

    function init() {
        scan(document);
        initObserver();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
