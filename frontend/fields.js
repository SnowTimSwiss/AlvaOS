// Fields with a button inside: <input data-paste> gets "Paste", <input data-copy> (read-only) gets
// "Copy", <input type="search"> gets a clear button once something is typed. Works for fields that
// are added to the page later too.
(function () {
    const ICON = {
        paste: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/></svg>',
        copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>',
        check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
        x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    };

    function button(kind, label) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'field-action';
        b.innerHTML = `${ICON[kind]}<span>${label}</span>`;
        return b;
    }

    function flash(b, kind, label, done) {
        b.innerHTML = `${ICON.check}<span>${done}</span>`;
        setTimeout(() => { b.innerHTML = `${ICON[kind]}<span>${label}</span>`; }, 1400);
    }

    function wrap(field) {
        const w = document.createElement('div');
        w.className = 'field-wrap';
        field.parentNode.insertBefore(w, field);
        w.appendChild(field);
        const actions = document.createElement('div');
        actions.className = 'field-actions';
        w.appendChild(actions);
        return actions;
    }

    function enhance(field) {
        if (field.dataset.fieldInit) return;
        const isText = field.tagName === 'TEXTAREA' || (field.tagName === 'INPUT' && /^(text|search|url|tel|)$/.test(field.type));
        if (!isText || !field.parentNode) return;
        if ('paste' in field.dataset) {
            field.dataset.fieldInit = '1';
            const b = button('paste', 'Paste');
            b.onclick = async () => {
                try {
                    const text = await navigator.clipboard.readText();
                    field.value = text.trim();
                    field.dispatchEvent(new Event('input', { bubbles: true }));
                    field.focus();
                    flash(b, 'paste', 'Paste', 'Pasted');
                } catch (e) {
                    field.focus(); // the browser may not allow reading: Ctrl+V still works
                    b.querySelector('span').textContent = 'Press Ctrl+V';
                    setTimeout(() => { b.querySelector('span').textContent = 'Paste'; }, 2000);
                }
            };
            wrap(field).appendChild(b);
        } else if ('copy' in field.dataset) {
            field.dataset.fieldInit = '1';
            const b = button('copy', 'Copy');
            b.onclick = async () => {
                try { await navigator.clipboard.writeText(field.value); } catch (e) { field.select(); document.execCommand('copy'); }
                flash(b, 'copy', 'Copy', 'Copied');
            };
            wrap(field).appendChild(b);
        } else if (field.type === 'search') {
            field.dataset.fieldInit = '1';
            const b = button('x', 'Clear');
            const sync = () => { b.hidden = !field.value; };
            b.onclick = () => { field.value = ''; field.dispatchEvent(new Event('input', { bubbles: true })); field.focus(); };
            field.addEventListener('input', sync);
            wrap(field).appendChild(b);
            sync();
        }
    }

    function scan(root) {
        (root.querySelectorAll ? root : document).querySelectorAll('[data-paste], [data-copy], input[type="search"]').forEach(enhance);
    }

    document.addEventListener('DOMContentLoaded', () => {
        scan(document);
        new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => n.nodeType === 1 && scan(n))))
            .observe(document.body, { childList: true, subtree: true });
    });
})();
