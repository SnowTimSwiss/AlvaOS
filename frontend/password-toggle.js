(function () {
    const WRAP_CLASS = 'password-toggle-wrap';
    const BTN_CLASS = 'password-toggle-btn';

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
        button.innerHTML = window.alvaIcon ? window.alvaIcon('eye', '', 'aria-hidden="true"') : 'Show';
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

    function initObserver() {
        if (!window.MutationObserver) return;
        const observer = new MutationObserver((mutations) => {
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
        observer.observe(document.body, { childList: true, subtree: true });
    }

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
