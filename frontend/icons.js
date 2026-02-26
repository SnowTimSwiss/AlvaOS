(function () {
    const NS = 'http://www.w3.org/2000/svg';

    const ICONS = {
        'house': ['path d="M3 10.5L12 3l9 7.5"', 'path d="M5 10v10h14V10"'],
        'hard-drive': ['rect x="3" y="6" width="18" height="12" rx="2"', 'path d="M7 14h.01"', 'path d="M11 14h2"'],
        'hard-drive-download': ['rect x="3" y="4" width="18" height="10" rx="2"', 'path d="M12 10v9"', 'path d="M8.5 15.5L12 19l3.5-3.5"'],
        'blocks': ['rect x="3" y="3" width="8" height="8" rx="1.5"', 'rect x="13" y="3" width="8" height="8" rx="1.5"', 'rect x="8" y="13" width="8" height="8" rx="1.5"'],
        'refresh-cw': ['path d="M21 12a9 9 0 0 0-15.5-6.3"', 'path d="M3 4v5h5"', 'path d="M3 12a9 9 0 0 0 15.5 6.3"', 'path d="M21 20v-5h-5"'],
        'rotate-cw': ['path d="M21 12a9 9 0 1 1-2.6-6.4"', 'path d="M21 3v6h-6"'],
        'users': ['path d="M17 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2"', 'circle cx="9" cy="7" r="4"', 'path d="M23 21v-2a4 4 0 0 0-3-3.87"', 'path d="M16 3.13a4 4 0 0 1 0 7.75"'],
        'settings': ['circle cx="12" cy="12" r="3"', 'path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.03.03a2 2 0 0 1-2.83 2.83l-.03-.03A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 1-3 0 1.7 1.7 0 0 0-1-.6 1.7 1.7 0 0 0-1.87.34l-.03.03a2 2 0 0 1-2.83-2.83l.03-.03A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 1 0-3 1.7 1.7 0 0 0 .6-1 1.7 1.7 0 0 0-.34-1.87l-.03-.03A2 2 0 1 1 7.06 5.3l.03.03A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 1 3 0 1.7 1.7 0 0 0 1 .6 1.7 1.7 0 0 0 1.87-.34l.03-.03a2 2 0 1 1 2.83 2.83l-.03.03A1.7 1.7 0 0 0 19.4 9c.27.4.49.67.6 1a1.7 1.7 0 0 0 1 .6 1.7 1.7 0 0 1 0 3 1.7 1.7 0 0 0-1 .6c-.11.33-.33.6-.6 1Z"'],
        'user-round': ['circle cx="12" cy="8" r="5"', 'path d="M4 21a8 8 0 0 1 16 0"'],
        'clipboard-list': ['rect x="7" y="3" width="10" height="4" rx="1"', 'path d="M9 7H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3"', 'path d="M9 12h7"', 'path d="M9 16h7"', 'path d="M7 12h.01"', 'path d="M7 16h.01"'],
        'upload': ['path d="M12 17V4"', 'path d="M7 9l5-5 5 5"', 'path d="M4 20h16"'],
        'package': ['path d="M21 8l-9-5-9 5 9 5 9-5Z"', 'path d="M3 8v8l9 5 9-5V8"', 'path d="M12 13v8"'],
        'scroll-text': ['path d="M8 6h12"', 'path d="M8 10h9"', 'path d="M8 14h8"', 'path d="M4 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H8a4 4 0 0 1 0-8h8"'],
        'disc': ['circle cx="12" cy="12" r="9"', 'circle cx="12" cy="12" r="2"'],
        'disc-3': ['circle cx="12" cy="12" r="9"', 'circle cx="12" cy="12" r="2"', 'path d="M16.5 7.5l-9 9"'],
        'database': ['ellipse cx="12" cy="5" rx="8" ry="3"', 'path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5"', 'path d="M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"'],
        'network': ['circle cx="12" cy="5" r="2"', 'circle cx="5" cy="19" r="2"', 'circle cx="19" cy="19" r="2"', 'path d="M12 7v5"', 'path d="M12 12L6.5 17"', 'path d="M12 12l5.5 5"'],
        'plus': ['path d="M12 5v14"', 'path d="M5 12h14"'],
        'globe': ['circle cx="12" cy="12" r="9"', 'path d="M3 12h18"', 'path d="M12 3a15 15 0 0 1 0 18"', 'path d="M12 3a15 15 0 0 0 0 18"'],
        'clock-3': ['circle cx="12" cy="12" r="9"', 'path d="M12 7v5h4"'],
        'triangle-alert': ['path d="M12 3l9 16H3L12 3Z"', 'path d="M12 9v4"', 'path d="M12 16h.01"'],
        'megaphone': ['path d="M3 11v2"', 'path d="M20 6l-8 4v4l8 4V6Z"', 'path d="M12 10H7a2 2 0 0 0-2 2 2 2 0 0 0 2 2h5"', 'path d="M8 14v5"'],
        'key-round': ['circle cx="8" cy="15" r="4"', 'path d="M12 15h9"', 'path d="M18 12v6"', 'path d="M21 13v4"'],
        'shield-plus': ['path d="M12 3l7 3v6c0 5-3.5 8-7 9-3.5-1-7-4-7-9V6l7-3Z"', 'path d="M12 9v6"', 'path d="M9 12h6"'],
        'stethoscope': ['path d="M6 3v5a4 4 0 0 0 8 0V3"', 'path d="M10 3v5"', 'path d="M14 3v5"', 'path d="M12 12v2a4 4 0 0 0 8 0v-1"', 'circle cx="20" cy="13" r="2"'],
        'cpu': ['rect x="7" y="7" width="10" height="10" rx="2"', 'path d="M9 1v3"', 'path d="M15 1v3"', 'path d="M9 20v3"', 'path d="M15 20v3"', 'path d="M1 9h3"', 'path d="M1 15h3"', 'path d="M20 9h3"', 'path d="M20 15h3"'],
        'memory-stick': ['path d="M6 8h12v8H6z"', 'path d="M4 10h2"', 'path d="M4 14h2"', 'path d="M18 10h2"', 'path d="M18 14h2"', 'path d="M8 8V6"', 'path d="M12 8V6"', 'path d="M16 8V6"'],
        'rocket': ['path d="M5 19c2-6 7-10 14-14-2 7-6 12-12 14l-2 0Z"', 'path d="M9 15l-4 4"', 'circle cx="14" cy="10" r="1.5"'],
        'siren': ['path d="M7 13a5 5 0 0 1 10 0v4H7v-4Z"', 'path d="M12 3v3"', 'path d="M4 8l2 1"', 'path d="M20 8l-2 1"', 'path d="M4 19h16"'],
        'chart-line': ['path d="M3 20h18"', 'path d="M5 16l4-4 3 3 7-7"'],
        'briefcase': ['rect x="3" y="7" width="18" height="13" rx="2"', 'path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"', 'path d="M3 12h18"'],
        'film': ['rect x="3" y="5" width="18" height="14" rx="2"', 'path d="M7 5v14"', 'path d="M17 5v14"', 'path d="M3 9h4"', 'path d="M17 9h4"', 'path d="M3 15h4"', 'path d="M17 15h4"'],
        'code-2': ['path d="M8 8L3 12l5 4"', 'path d="M16 8l5 4-5 4"', 'path d="M14 4l-4 16"'],
        'shield-check': ['path d="M12 3l7 3v6c0 5-3.5 8-7 9-3.5-1-7-4-7-9V6l7-3Z"', 'path d="M9 12l2 2 4-4"'],
        'circle-check-big': ['circle cx="12" cy="12" r="9"', 'path d="M8 12l3 3 5-5"'],
        'circle-x': ['circle cx="12" cy="12" r="9"', 'path d="M9 9l6 6"', 'path d="M15 9l-6 6"'],
        'info': ['circle cx="12" cy="12" r="9"', 'path d="M12 10v6"', 'path d="M12 7h.01"'],
        'x': ['path d="M18 6L6 18"', 'path d="M6 6l12 12"'],
        'plug-zap': ['path d="M8 7v4"', 'path d="M16 7v4"', 'path d="M6 11h12v2a4 4 0 0 1-4 4h-1v4"', 'path d="M10 3l-2 3h3l-2 3"'],
        'folder': ['path d="M3 7h6l2 2h10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"'],
        'folder-open': ['path d="M3 8h6l2 2h10"', 'path d="M3 10h18l-2 8H5l-2-8Z"'],
        'terminal': ['path d="M4 6l4 4-4 4"', 'path d="M10 14h10"'],
        'monitor': ['rect x="3" y="4" width="18" height="12" rx="2"', 'path d="M8 20h8"', 'path d="M12 16v4"'],
        'laptop': ['path d="M3 17h18"', 'rect x="6" y="5" width="12" height="8" rx="1"'],
        'eye': ['path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z"', 'circle cx="12" cy="12" r="2.5"'],
        'loader-circle': ['path d="M12 3a9 9 0 1 0 9 9"'],
        'message-square': ['path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10Z"']
    };

    function parseAttrs(attrs) {
        const result = {};
        const input = String(attrs || '').trim();
        if (!input) return result;
        const regex = /([a-zA-Z_:.-]+)\s*=\s*"([^"]*)"/g;
        let match;
        while ((match = regex.exec(input)) !== null) {
            result[match[1]] = match[2];
        }
        return result;
    }

    function iconSvg(name, className = '', attrs = '') {
        const parts = ICONS[String(name || '').trim()] || ICONS.info;
        const parsedAttrs = parseAttrs(attrs);
        const classAttr = ['lucide', className].filter(Boolean).join(' ');
        const extra = Object.entries(parsedAttrs)
            .map(([key, value]) => ` ${key}="${String(value).replace(/"/g, '&quot;')}"`)
            .join('');
        const body = parts.map((fragment) => `<${fragment}/>`).join('');
        return `<svg xmlns="${NS}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" class="${classAttr}"${extra}>${body}</svg>`;
    }

    function render(root = document) {
        const target = root && root.querySelectorAll ? root : document;
        const nodes = [];
        if (target.matches && target.matches('[data-lucide]')) {
            nodes.push(target);
        }
        nodes.push(...target.querySelectorAll('[data-lucide]'));
        nodes.forEach((node) => {
            const name = node.getAttribute('data-lucide') || 'info';
            const className = node.getAttribute('class') || '';
            const ariaHidden = node.getAttribute('aria-hidden');
            const attrs = ariaHidden !== null ? `aria-hidden=\"${ariaHidden}\"` : '';
            node.outerHTML = iconSvg(name, className, attrs);
        });
    }

    function alvaIcon(name, className = '', attrs = '') {
        return iconSvg(name, className, attrs);
    }

    let iconRenderScheduled = false;
    let queuedRenderRoot = null;

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
                // Different branches changed in same frame: render document once to avoid missed icons.
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
        const observer = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                for (const node of mutation.addedNodes) {
                    if (!node || node.nodeType !== 1) continue;
                    if ((node.matches && node.matches('[data-lucide]')) || (node.querySelector && node.querySelector('[data-lucide]'))) {
                        scheduleRender(node);
                    }
                }
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    window.alvaIcon = alvaIcon;
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
