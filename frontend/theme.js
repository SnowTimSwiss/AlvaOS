// AlvaOS theme: follows the device (light or dark) unless the person picked one.
// Loaded in <head> before the stylesheet paints, so a page never flashes in
// the wrong colours. The choice is per browser: a phone in dark mode and a
// desktop in light mode can each look right.
(function () {
    const KEY = 'alvaos_theme';           // 'auto' (default) | 'light' | 'dark'
    const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

    function stored() {
        try {
            const value = localStorage.getItem(KEY);
            return value === 'light' || value === 'dark' ? value : 'auto';
        } catch (_e) {
            return 'auto';
        }
    }

    function resolved(choice) {
        if (choice === 'light' || choice === 'dark') return choice;
        return media && media.matches ? 'light' : 'dark';
    }

    function apply() {
        const choice = stored();
        const theme = resolved(choice);
        const root = document.documentElement;
        root.dataset.theme = theme;
        root.style.colorScheme = theme;
        document.dispatchEvent(new CustomEvent('alvaos-theme', { detail: { choice, theme } }));
    }

    window.alvaosTheme = {
        get: stored,
        set(choice) {
            try {
                if (choice === 'light' || choice === 'dark') localStorage.setItem(KEY, choice);
                else localStorage.removeItem(KEY);
            } catch (_e) { /* storage off: still switch for this page */ }
            apply();
        },
    };

    if (media) {
        const onChange = () => { if (stored() === 'auto') apply(); };
        if (media.addEventListener) media.addEventListener('change', onChange);
        else if (media.addListener) media.addListener(onChange);
    }
    // Another tab changed the choice.
    window.addEventListener('storage', (event) => { if (event.key === KEY) apply(); });

    apply();
})();
