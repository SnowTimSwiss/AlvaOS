(function () {
    function getLucideRuntime() {
        return (window && window.lucide && typeof window.lucide.createIcons === 'function')
            ? window.lucide
            : null;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function iconMarkup(name, className = '', attrs = '') {
        const safeName = escapeHtml(String(name || 'info').trim() || 'info');
        const safeClass = escapeHtml(String(className || '').trim());
        const safeAttrs = String(attrs || '').trim();
        const classAttr = safeClass ? ` class="${safeClass}"` : '';
        const extraAttrs = safeAttrs ? ` ${safeAttrs}` : '';
        return `<i data-lucide="${safeName}"${classAttr}${extraAttrs}></i>`;
    }

    function iconSvg(name, className = '', attrs = '') {
        return iconMarkup(name, className, attrs);
    }

    function render(_root = document) {
        const lucide = getLucideRuntime();
        if (!lucide) return;
        lucide.createIcons({
            nameAttr: 'data-lucide',
            attrs: {
                'stroke-width': 1.9
            }
        });
    }

    let iconRenderScheduled = false;
    let queuedRenderRoot = null;
    let _iconObserver = null;

    function scheduleRender(root) {
        const nextRoot = root || document;
        if (!queuedRenderRoot) {
            queuedRenderRoot = nextRoot;
        } else if (
            queuedRenderRoot !== document
            && nextRoot !== document
            && queuedRenderRoot !== nextRoot
        ) {
            const queuedContainsNext = !!(queuedRenderRoot.contains && queuedRenderRoot.contains(nextRoot));
            const nextContainsQueued = !!(nextRoot.contains && nextRoot.contains(queuedRenderRoot));
            if (!queuedContainsNext && !nextContainsQueued) {
                queuedRenderRoot = document;
            } else if (nextContainsQueued) {
                queuedRenderRoot = nextRoot;
            }
        }

        if (iconRenderScheduled) return;
        iconRenderScheduled = true;

        const runner = window.requestAnimationFrame || ((cb) => setTimeout(cb, 16));
        runner(() => {
            iconRenderScheduled = false;
            const rootToRender = queuedRenderRoot || document;
            queuedRenderRoot = null;
            render(rootToRender);
        });
    }

    function initObserver() {
        if (!window.MutationObserver) return;
        _iconObserver = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                for (const node of mutation.addedNodes) {
                    if (!node || node.nodeType !== 1) continue;
                    if ((node.matches && node.matches('[data-lucide]')) || (node.querySelector && node.querySelector('[data-lucide]'))) {
                        scheduleRender(node);
                    }
                }
            }
        });
        _iconObserver.observe(document.body, { childList: true, subtree: true });
    }

    window.disconnectIconObserver = function() {
        if (_iconObserver) {
            _iconObserver.disconnect();
            _iconObserver = null;
        }
    };

    window.alvaIcon = iconSvg;
    window.renderAlvaIcons = render;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            render(document);
            initObserver();
        });
    } else {
        render(document);
        initObserver();
    }
})();
