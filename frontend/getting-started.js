// Dashboard: "Getting started". What a new NAS still needs to keep files safe,
// in order, each with one link. Steps tick themselves off from the real state;
// the list disappears when everything is done or when it is hidden.
(function () {
    const HIDE_KEY = 'alvaos_getting_started_hidden';
    const box = document.getElementById('getting-started');
    if (!box) return;

    async function getJson(path) {
        try {
            const res = await fetch(`/api/v1${path}`);
            return res.ok ? await res.json() : null;
        } catch (_e) {
            return null;
        }
    }

    function icon(name) {
        return window.alvaIcon ? window.alvaIcon(name, '', 'aria-hidden="true"') : '';
    }

    function hiddenByUser() {
        try { return localStorage.getItem(HIDE_KEY) === '1'; } catch (_e) { return false; }
    }

    async function load() {
        if (hiddenByUser()) return;
        const [pools, shares, backup, email, telegram] = await Promise.all([
            getJson('/storage/pools'), getJson('/storage/shares'), getJson('/backup/settings'),
            getJson('/alerts/email'), getJson('/alerts/settings'),
        ]);
        if (!pools) return;   // not an admin, or the NAS is not answering: show nothing

        const own = (pools.pools || []).filter((p) => p.is_managed !== false && !p.is_system_pool && p.mount_point && p.mount_point !== '/');
        const protectedPool = own.some((p) => !['single', 'raid0', ''].includes(String(p.raid_level || '').toLowerCase()));
        const sharesList = shares?.shares || [];
        const pb = backup?.settings?.pool_backup || {};
        const notifies = !!(email?.email?.enabled && email?.email?.configured)
            || !!(telegram?.settings?.telegram?.enabled && telegram?.settings?.telegram?.paired);

        const steps = [
            { done: own.length > 0, title: 'Storage for your files', text: 'Combine one or more disks into a pool.', link: 'storage.html', action: 'Create a pool' },
            { done: sharesList.length > 0, title: 'A shared folder', text: 'Open it from your computers and phones.', link: 'storage.html#shares', action: 'Share a folder' },
            { done: !!(pb.enabled && (pb.sources || []).length), title: 'Automatic restore points', text: 'Get back files you deleted or changed by mistake.', link: 'backup.html', action: 'Turn on' },
            { done: protectedPool, title: 'Protection against a failing disk', text: 'With a second disk, every file is kept twice.', link: own.length ? `storage.html#pool=${encodeURIComponent(own[0].id)}` : 'storage.html', action: 'Add a disk' },
            { done: notifies, title: 'Hear about problems', text: 'Get an email or a Telegram message when a disk fails.', link: 'system.html#alerts', action: 'Set up' },
        ];
        const doneCount = steps.filter((s) => s.done).length;
        if (doneCount === steps.length) {
            box.hidden = true;
            return;
        }
        const next = steps.find((s) => !s.done);
        box.innerHTML = `
            <div class="gs-head">
                <div>
                    <h2>Getting started</h2>
                    <p>${doneCount} of ${steps.length} done. Next: ${next.title.toLowerCase()}.</p>
                </div>
                <button type="button" class="gs-hide" aria-label="Hide getting started">Hide</button>
            </div>
            <div class="gs-bar" role="progressbar" aria-label="Getting started: ${doneCount} of ${steps.length} done" aria-valuemin="0" aria-valuemax="${steps.length}" aria-valuenow="${doneCount}"><span style="width: ${Math.round(doneCount * 100 / steps.length)}%"></span></div>
            <ol class="gs-steps">
                ${steps.map((s) => `
                    <li class="gs-step${s.done ? ' done' : ''}${s === next ? ' next' : ''}">
                        <span class="gs-mark">${s.done ? icon('circle-check') : ''}</span>
                        <span class="gs-text"><strong>${s.title}</strong><span>${s.text}</span></span>
                        ${s.done ? '' : `<a class="${s === next ? 'btn-primary' : 'btn-secondary'}" href="${s.link}">${s.action}</a>`}
                    </li>`).join('')}
            </ol>`;
        box.hidden = false;
        box.querySelector('.gs-hide').addEventListener('click', () => {
            try { localStorage.setItem(HIDE_KEY, '1'); } catch (_e) { /* storage off: hide for now */ }
            box.hidden = true;
            if (window.showToast) window.showToast('Hidden. Everything is still in Storage, Backup and Settings.', 'info');
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
    else load();
})();
