// Backup › Data › Backup disk: a second copy of the restore points on a USB
// disk (backend/backup_copy.py). The disk is a pool made in Storage; here it
// is chosen, copied to and safely removed.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let state = null;
    let polling = 0;

    async function api(path, options = {}) {
        const res = await backupApi(path, options);
        const data = res ? await backupReadJson(res) : null;
        if (!res || !res.ok) throw new Error((data && data.error) || 'That did not work.');
        return data;
    }

    function when(iso) {
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? '' : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    }

    function pill(text, kind) {
        $('copy-pill').innerHTML = text ? `<span class="pill ${kind || ''}">${esc(text)}</span>` : '';
    }

    // An empty disk (usually a USB disk just plugged in) can become the backup
    // disk in one step: a pool of its own, then chosen here.
    let newDisks = [];

    async function findNewDisks() {
        try {
            const data = await api('/storage/disks');
            newDisks = (data.disks || []).filter((d) => d.usage && d.usage.can_add_to_pool);
        } catch (_e) {
            newDisks = [];
        }
    }

    function diskLabel(d) {
        const name = d.model && d.model !== 'Unknown' ? d.model : (d.is_removable ? 'USB disk' : 'Disk');
        return `${name} (${d.size || ''}${d.is_removable ? ', USB' : ''})`;
    }

    async function useNewDisk(path) {
        const disk = newDisks.find((d) => d.path === path);
        if (!disk) return;
        const ok = await window.showConfirm(`Erase this disk?\nEverything on ${diskLabel(disk)} (${disk.path}) is erased. It then keeps the copies of your restore points.`,
            { danger: true, confirmLabel: 'Erase and use it', requireText: 'ERASE' });
        if (!ok) return;
        const taken = new Set((state.choices || []).map((c) => String(c.name)));
        let name = 'backup';
        for (let n = 2; taken.has(name); n += 1) name = `backup-${n}`;
        const button = $('copy-new');
        if (button) { button.disabled = true; button.textContent = 'Setting up the disk...'; }
        await act(async () => {
            const pool = await api('/storage/pools', { method: 'POST', json: { name, devices: [path], raid_level: 'single' } });
            await api('/backup/copy', { method: 'POST', json: { pool_id: pool.pool_id } });
            await api('/backup/copy/run', { method: 'POST', json: {} });
            watch();
        }, 'The disk is your backup disk now. The first copy has started.');
    }

    function render() {
        const body = $('copy-body');
        if (!state.enabled) {
            pill('Off', '');
            const usable = (state.choices || []).filter((c) => c.usable);
            body.innerHTML = `
                <p class="metric-sub card-help">Keep a second copy of your restore points on a USB disk, so a broken NAS, a fire or a thief does not take everything. Plug the disk in now and then; AlvaOS copies what is new on its own.</p>
                ${usable.length ? `
                    <div class="copy-choose">
                        <select id="copy-pool" class="select-input" aria-label="Backup disk">
                            ${usable.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}${c.connected ? '' : ' (not connected)'}</option>`).join('')}
                        </select>
                        <button type="button" class="btn-primary" id="copy-on">Use as backup disk</button>
                    </div>
                    <p class="metric-sub">Everything already on that disk stays; the copies go into a hidden folder.</p>`
                : ''}
                ${newDisks.length ? `
                    <div class="copy-choose">
                        <select id="copy-disk" class="select-input" aria-label="New disk">
                            ${newDisks.map((d) => `<option value="${esc(d.path)}">${esc(diskLabel(d))}</option>`).join('')}
                        </select>
                        <button type="button" class="btn-secondary" id="copy-new">Erase and use as backup disk</button>
                    </div>
                    <p class="metric-sub">${usable.length ? 'Or a new, empty disk: it' : 'A new, empty disk was found. It'} is erased and gets the copies of your restore points.</p>`
                : (usable.length ? '' : '<p class="metric-sub"><strong>Connect a USB disk</strong> and press Look again. It is erased and then keeps a copy of your restore points.</p><button type="button" class="btn-secondary" id="copy-look">Look again</button>')}`;
            $('copy-new')?.addEventListener('click', () => useNewDisk($('copy-disk').value));
            $('copy-look')?.addEventListener('click', () => load());
            $('copy-on')?.addEventListener('click', () => act(() => api('/backup/copy', { method: 'POST', json: { pool_id: $('copy-pool').value } }), 'The backup disk is set. Copying starts when it is connected.'));
            return;
        }
        const stale = state.stale;
        pill(state.running ? 'Copying…' : state.ejected ? 'Safe to unplug' : state.connected ? (state.last_error ? 'Last copy failed' : 'Connected') : stale ? 'Not connected for a week' : 'Not connected',
            state.running ? '' : state.ejected ? 'ok' : state.last_error ? 'bad' : stale ? 'warn' : state.connected ? 'ok' : '');
        body.innerHTML = `
            <p class="metric-sub card-help">"${esc(state.pool_name)}" keeps a second copy of your restore points. ${state.ejected ? 'It was safely removed and can be unplugged now.' : state.connected ? 'It is connected; new restore points are copied on their own.' : 'Connect it now and then; copying starts on its own.'}</p>
            <div class="copy-facts">
                <div><span>Last copy</span><strong>${state.last_copy_at ? esc(when(state.last_copy_at)) : 'Not yet'}</strong></div>
                ${state.last_error && !state.running ? `<div class="copy-error">${esc(state.last_error)}</div>` : ''}
            </div>
            <div class="copy-actions">
                ${state.connected && !state.running ? '<button type="button" class="btn-primary" id="copy-run">Copy now</button>' : ''}
                ${state.mounted && !state.running ? '<button type="button" class="btn-secondary" id="copy-eject">Safely remove</button>' : ''}
                <button type="button" class="btn-secondary btn-quiet" id="copy-off">Stop using it</button>
            </div>`;
        $('copy-run')?.addEventListener('click', () => act(async () => { await api('/backup/copy/run', { method: 'POST', json: {} }); watch(); }, 'Copying to the backup disk. This can take a while the first time.'));
        $('copy-eject')?.addEventListener('click', () => act(async () => { const r = await api('/backup/copy/eject', { method: 'POST', json: {} }); backupNotify(r.message, 'success'); }, ''));
        $('copy-off')?.addEventListener('click', () => act(() => api('/backup/copy', { method: 'POST', json: { enabled: false } }), 'The backup disk is no longer used. Its copies stay on it.'));
    }

    async function act(work, message) {
        try {
            await work();
            if (message) backupNotify(message, 'success');
        } catch (e) {
            backupNotify(e.message, 'error');
        }
        load();
    }

    // After "Copy now": look again a few times until the copy is done.
    function watch() {
        clearInterval(polling);
        polling = 0;
        let rounds = 0;
        polling = setInterval(async () => {
            rounds += 1;
            await load();
            if (!state || !state.running || rounds > 720) { clearInterval(polling); polling = 0; }
        }, 5000);
    }

    async function load() {
        if (!$('copy-body')) return;
        try {
            state = await api('/backup/copy');
            if (!state.enabled) await findNewDisks();
        } catch (e) {
            $('copy-body').innerHTML = `<div class="metric-sub">${esc(e.message)}</div>`;
            return;
        }
        render();
        if (state.running && !polling) watch();   // a copy started by itself or in another tab
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
    else load();
})();
