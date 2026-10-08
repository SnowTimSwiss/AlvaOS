// The phone's back gesture and swipes for the Hub. Loaded before app.js.
//
// Back: every window that opens over the page (a dialog, the viewer, the side drawer, a small
// calendar window) puts one step into the browser history. The back gesture or button then
// closes the top window first, and only then goes back through folders. In the AlvaOS app the
// Android back gesture is the same thing (the web view goes back).
//
// Swipes (touch only): from the left edge opens the folder drawer, swipe the drawer shut; swipe
// down on a window or the viewer's picture closes it; swipe sideways in the viewer for the
// next picture, and in the calendar's month and schedule for the next or last period.
(function () {
    const $ = (id) => document.getElementById(id);
    const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ── Back closes the top window ─────────────────────────────────────────
    // The windows we know: fixed ones with a hidden flag, and ones the pages add to <body>.
    function openOverlays() {
        const out = [];
        const dialog = $('dialog');
        if (dialog && !dialog.hidden && dialog.firstElementChild) out.push(dialog);
        const viewer = $('viewer');
        if (viewer && !viewer.hidden) out.push(viewer);
        const side = $('side');
        if (side && side.classList.contains('open') && !$('scrim')?.hidden) out.push(side);
        document.querySelectorAll('body > .dialog-wrap, body > .cal-pop').forEach((el) => { if (el.id !== 'dialog' && !el.hidden) out.push(el); });
        return out;
    }
    function closeOverlay(el) {
        if (el.id === 'viewer') { $('viewer-close')?.click(); return; }
        if (el.id === 'side') { $('scrim')?.click(); return; }
        const button = el.querySelector('[data-close], [data-act="close"]');
        if (button) { button.click(); return; }
        if (el.id === 'dialog') { el.hidden = true; el.innerHTML = ''; return; }
        el.remove();
    }

    let depth = 0;          // history steps we added for open windows
    let ignore = 0;         // popstate events we caused ourselves
    let queued = false;
    function sync() {
        queued = false;
        const n = openOverlays().length;
        if (n > depth) {
            for (; depth < n; depth += 1) history.pushState({ overlay: depth + 1 }, '', location.href);
        } else if (n < depth) {
            const back = depth - n;
            depth = n;
            ignore += 1;
            history.go(-back);
        }
    }
    const schedule = () => { if (!queued) { queued = true; requestAnimationFrame(sync); } };
    new MutationObserver(schedule).observe(document.documentElement, {
        subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'class'],
    });
    window.addEventListener('popstate', (e) => {
        if (ignore) { ignore -= 1; e.stopImmediatePropagation(); return; }
        if (depth > 0) {
            const all = openOverlays();
            if (all.length) {
                depth -= 1;
                e.stopImmediatePropagation();
                closeOverlay(all[all.length - 1]);
                return;
            }
            depth = 0;
        }
    });

    // ── Swipes ──────────────────────────────────────────────────────────────
    if (!window.matchMedia('(pointer: coarse)').matches) return;
    let t = null;
    const sheetOf = (el) => el.closest('.cal-pop.sheet, #dialog > .dialog, .cal-dialog');
    document.addEventListener('touchstart', (e) => {
        if (e.touches.length !== 1) { t = null; return; }
        const p = e.touches[0];
        const target = e.target;
        if (target.closest('input, textarea, select, [contenteditable]')) { t = null; return; }
        t = { x: p.clientX, y: p.clientY, at: Date.now(), target, sheet: sheetOf(target), moved: false, edge: p.clientX < 18 };
        if (t.sheet) {
            const r = t.sheet.getBoundingClientRect();
            t.grab = p.clientY - r.top < 64 || t.sheet.scrollTop === 0;   // the top of a window, or a window not scrolled
        }
    }, { passive: true });
    document.addEventListener('touchmove', (e) => {
        if (!t) return;
        const p = e.touches[0];
        const dx = p.clientX - t.x;
        const dy = p.clientY - t.y;
        // A window follows the finger down.
        if (t.sheet && t.grab && dy > 8 && Math.abs(dy) > Math.abs(dx) * 1.5) {
            t.moved = true;
            t.sheet.style.transition = 'none';
            t.sheet.style.transform = `translateY(${dy}px)`;
        }
    }, { passive: true });
    document.addEventListener('touchend', (e) => {
        if (!t) return;
        const p = e.changedTouches[0];
        const dx = p.clientX - t.x;
        const dy = p.clientY - t.y;
        const fast = Date.now() - t.at < 600;
        const start = t;
        t = null;
        const horizontal = Math.abs(dx) > Math.abs(dy) * 1.6;
        const vertical = Math.abs(dy) > Math.abs(dx) * 1.6;
        if (start.sheet && start.moved) {
            const el = start.sheet;
            if (dy > 90 || (fast && dy > 50)) {
                el.style.transition = reduced() ? 'none' : 'transform .16s ease-in';
                el.style.transform = 'translateY(100%)';
                setTimeout(() => { closeOverlay(el.closest('#dialog, .dialog-wrap, .cal-pop') || el); el.style.transform = ''; el.style.transition = ''; }, reduced() ? 0 : 150);
            } else {
                el.style.transition = reduced() ? 'none' : 'transform .18s ease-out';
                el.style.transform = '';
            }
            return;
        }
        const viewer = $('viewer');
        if (viewer && !viewer.hidden && start.target.closest('#viewer')) {
            if (horizontal && Math.abs(dx) > 60) (dx < 0 ? $('viewer-next') : $('viewer-prev'))?.click();
            else if (vertical && dy > 90) $('viewer-close')?.click();
            return;
        }
        const side = $('side');
        if (side && side.classList.contains('open') && horizontal && dx < -60) { $('scrim')?.click(); return; }
        const menu = $('menu-btn');
        if (start.edge && horizontal && dx > 60 && menu && menu.offsetParent !== null && !openOverlays().length) { menu.click(); return; }
        const body = $('cal-body');
        if (body && body.contains(start.target) && start.target.closest('.mo-grid, .mo-week, .sch') && horizontal && Math.abs(dx) > 70) {
            (dx < 0 ? $('cal-next') : $('cal-prev'))?.click();
        }
    }, { passive: true });
}());
