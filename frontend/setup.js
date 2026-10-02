// AlvaOS first-run wizard: welcome, name and password, storage, a shared
// folder, done. Ends with a NAS that can be used right away. Everything after
// the password can be skipped; the dashboard then shows it as a next step.
//
// This page does not load app.js (that would redirect back here before setup
// is complete), so it carries its own small API helper.
(function () {
    const API = '/api/v1';
    const STEPS = ['welcome', 'account', 'storage', 'share', 'done'];
    const NAME_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
    const USER_RE = /^[a-z_][a-z0-9_-]{1,31}$/;
    const SHARE_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$/;
    const POOL_NAME = 'main';

    const $ = (id) => document.getElementById(id);
    const state = {
        step: 0,
        suggestedName: '',
        token: '',
        csrf: '',
        accountDone: false,
        disks: [],
        pool: null,          // { id, name, mount_point, how: 'created' | 'imported' }
        personCreated: '',
        share: null,         // { name, guest, person }
        backup: false,
        nameNote: '',
    };

    // ── Helpers ─────────────────────────────────────────────────────────────

    function esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function showError(message) {
        $('error').textContent = message || '';
        if (message) $('error').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    async function api(path, { method = 'GET', json } = {}) {
        const headers = {};
        if (state.token) headers.Authorization = state.token;
        if (method !== 'GET' && state.csrf) headers['X-CSRF-Token'] = state.csrf;
        if (json !== undefined) headers['Content-Type'] = 'application/json';
        let response;
        try {
            response = await fetch(`${API}${path}`, { method, headers, body: json !== undefined ? JSON.stringify(json) : undefined });
        } catch (_e) {
            throw new Error('The NAS did not answer. Check that it is still on, then try again.');
        }
        let data = null;
        try { data = await response.json(); } catch (_e) { /* empty body */ }
        if (!response.ok) {
            const error = new Error((data && data.error) || `Something went wrong (${response.status}).`);
            error.status = response.status;
            throw error;
        }
        return data || {};
    }

    function formatBytes(bytes) {
        const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
        let v = Number(bytes) || 0;
        let i = 0;
        while (v >= 1000 && i < units.length - 1) { v /= 1000; i += 1; }
        return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
    }

    // ── Navigation ──────────────────────────────────────────────────────────

    function current() { return STEPS[state.step]; }

    function setButtons({ next = 'Continue', nextEnabled = true, back = false, skip = false, nextHidden = false } = {}) {
        $('btn-next').textContent = next;
        $('btn-next').disabled = !nextEnabled;
        $('btn-next').hidden = nextHidden;
        $('btn-back').hidden = !back;
        $('btn-skip').hidden = !skip;
    }

    function render() {
        document.querySelectorAll('.step').forEach((el) => el.classList.toggle('active', el.dataset.step === current()));
        document.querySelectorAll('#progress span').forEach((el, i) => el.classList.toggle('done', i <= state.step));
        $('step-label').textContent = current() === 'done' ? 'All done' : `Step ${state.step + 1} of ${STEPS.length}`;
        showError('');
        const step = current();
        if (step === 'welcome') setButtons({ next: 'Get started' });
        if (step === 'account') { setButtons({ next: 'Continue', back: true }); validateAccount(); }
        if (step === 'storage') enterStorage();
        if (step === 'share') enterShare();
        if (step === 'done') enterDone();
        const focusable = document.querySelector('.step.active input:not([type="radio"]):not([type="checkbox"])');
        if (focusable && step !== 'done') focusable.focus({ preventScroll: true });
    }

    function go(stepName) {
        state.step = STEPS.indexOf(stepName);
        render();
    }

    async function withBusy(button, label, task) {
        const original = button.textContent;
        button.disabled = true;
        button.textContent = label;
        $('btn-skip').disabled = true;
        try {
            return await task();
        } finally {
            button.disabled = false;
            button.textContent = original;
            $('btn-skip').disabled = false;
        }
    }

    // ── Step 2: name and password ───────────────────────────────────────────

    function cleanName(raw) {
        return String(raw || '').toLowerCase().replace(/[\s_.]+/g, '-').replace(/[^a-z0-9-]/g, '');
    }

    function validateAccount() {
        const name = cleanName($('nas-name').value);
        const pw = $('password').value;
        const confirm = $('password-confirm').value;

        const nameOk = NAME_RE.test(name);
        $('nas-name-preview').textContent = name || '...';
        $('nas-name-hint').className = !name || nameOk ? '' : 'bad';
        if (name && !nameOk) $('nas-name-hint').textContent = 'Use letters, numbers and "-", and do not start or end with "-".';
        else $('nas-name-hint').innerHTML = `Letters, numbers and "-". Your computers will find it as <span id="nas-name-preview">${esc(name || '...')}</span>.`;

        const weak = ['password', '12345678', '123456789', 'qwertyui', 'alvaos12', 'admin123'];
        const pwOk = pw.length >= 8 && !weak.includes(pw.toLowerCase());
        const hint = $('password-hint');
        if (!pw) { hint.className = ''; hint.textContent = 'At least 8 characters. A short sentence is easy to remember and hard to guess.'; }
        else if (!pwOk) { hint.className = 'bad'; hint.textContent = pw.length < 8 ? `${8 - pw.length} more character${8 - pw.length === 1 ? '' : 's'}.` : 'This password is too easy to guess.'; }
        else { hint.className = 'good'; hint.textContent = pw.length >= 14 ? 'Strong password.' : 'Good. Longer is even better.'; }

        const match = pw && confirm && pw === confirm;
        $('confirm-hint').className = confirm ? (match ? 'good' : 'bad') : '';
        $('confirm-hint').innerHTML = confirm ? (match ? 'Matches.' : 'The two passwords are not the same yet.') : '&nbsp;';

        $('btn-next').disabled = !(nameOk && pwOk && match);
    }

    function setupTimezones() {
        let zone = 'UTC';
        try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch (_e) { /* old browser */ }
        let zones = [];
        try { zones = Intl.supportedValuesOf('timeZone'); } catch (_e) { zones = []; }
        if (!zones.includes('UTC')) zones.unshift('UTC');
        if (!zones.includes(zone)) zones.unshift(zone);
        const select = $('tz-select');
        select.innerHTML = zones.map((z) => `<option value="${esc(z)}"${z === zone ? ' selected' : ''}>${esc(z.replace(/_/g, ' '))}</option>`).join('');
        $('tz-name').textContent = zone.replace(/_/g, ' ');
        $('tz-change').addEventListener('click', () => {
            $('tz-summary').hidden = true;
            select.hidden = false;
            select.focus();
        });
    }

    async function submitAccount() {
        const name = cleanName($('nas-name').value);
        const timezone = $('tz-select').value || 'UTC';
        const data = await api('/setup/complete', { method: 'POST', json: { password: $('password').value, timezone } });
        state.token = data.token || '';
        state.csrf = data.csrf_token || '';
        try {
            localStorage.setItem('alvaos_token', state.token);
            if (state.csrf) localStorage.setItem('alvaos_csrf_token', state.csrf);
            else localStorage.removeItem('alvaos_csrf_token');
            localStorage.setItem('alvaos_nas_name', name);
        } catch (_e) { /* storage off: the wizard still works */ }
        state.accountDone = true;
        state.hostname = state.suggestedName || name;
        if (name !== state.suggestedName) {
            try {
                await api('/system/hostname', { method: 'PUT', json: { hostname: name } });
                state.hostname = name;
            } catch (e) {
                state.nameNote = `The name could not be changed (${e.message}). You can try again in System › Network.`;
            }
        }
    }

    // ── Step 3: storage ─────────────────────────────────────────────────────

    async function enterStorage() {
        setButtons({ next: 'Continue', nextEnabled: false, skip: true });
        const body = $('storage-body');
        body.innerHTML = '';
        $('storage-busy').hidden = false;
        let disks = [];
        let pools = [];
        try {
            const [d, p] = await Promise.all([api('/storage/disks'), api('/storage/pools').catch(() => ({}))]);
            disks = Array.isArray(d.disks) ? d.disks : [];
            pools = Array.isArray(p.pools) ? p.pools : [];
        } catch (e) {
            showError(e.message);
        } finally {
            $('storage-busy').hidden = true;
        }
        if (current() !== 'storage') return;
        state.disks = disks;

        const managed = pools.find((p) => p.is_managed && !p.is_system_pool && p.mount_point);
        if (managed) {
            state.pool = { id: managed.id, name: managed.name, mount_point: managed.mount_point, how: 'existing' };
            body.innerHTML = `<div class="note">Storage is already set up: <strong>${esc(managed.name)}</strong>.</div>`;
            setButtons({ next: 'Continue' });
            return;
        }

        const old = new Map();
        disks.filter((d) => d.usage?.role === 'other_pool' && d.usage.pool_id).forEach((d) => old.set(d.usage.pool_id, d.usage.pool_name || 'storage'));
        const empty = disks.filter((d) => d.usage?.can_add_to_pool);

        let html = '';
        if (old.size) {
            html += `<div class="field"><span class="label">Found storage from before</span><div class="choice-list">${
                Array.from(old.entries()).map(([id, name], i) => `
                    <label class="choice">
                        <input type="radio" name="storage-mode" value="import:${esc(id)}"${i === 0 ? ' checked' : ''}>
                        <span><strong>Keep using "${esc(name)}"</strong><small>Your files on it stay as they are.</small></span>
                        <span></span>
                    </label>`).join('')}
                ${empty.length ? `<label class="choice"><input type="radio" name="storage-mode" value="new"><span><strong>Set up new storage instead</strong><small>On the empty disks below.</small></span><span></span></label>` : ''}
            </div></div>`;
        }

        if (empty.length) {
            html += `<div class="field" id="new-storage"><span class="label">${empty.length === 1 ? 'This disk will be used' : 'Disks to use'}</span><div class="choice-list">${
                empty.map((d) => `
                    <label class="choice">
                        <input type="checkbox" class="disk-pick" value="${esc(d.path)}" data-bytes="${Number(d.size_bytes) || 0}" checked>
                        <span><strong>${esc(d.model && d.model !== 'Unknown' ? d.model : (d.is_removable ? 'USB disk' : 'Disk'))}</strong><small>${esc(d.path)}${d.is_removable ? ' · USB' : ''}</small></span>
                        <span class="meta">${esc(d.size || '')}</span>
                    </label>`).join('')}</div></div>
                <div class="field" id="protection-field"></div>
                <div class="note warn" id="erase-note"></div>`;
        } else if (!old.size) {
            html += `<div class="note warn">No free disk was found. AlvaOS never uses the system disk or a disk that still has data on it.<br><br>Connect a disk and press <strong>Look again</strong>, or skip and add storage later in Storage.</div>`;
        }
        body.innerHTML = html;

        body.querySelectorAll('input').forEach((input) => input.addEventListener('change', updateStorageChoice));
        if (!empty.length && !old.size) {
            setButtons({ next: 'Look again', skip: true });
            return;
        }
        updateStorageChoice();
    }

    function storageMode() {
        const checked = document.querySelector('input[name="storage-mode"]:checked');
        return checked ? checked.value : 'new';
    }

    function updateStorageChoice() {
        const mode = storageMode();
        const newBlock = $('new-storage');
        if (mode.startsWith('import:')) {
            if (newBlock) {
                newBlock.hidden = true;
                $('protection-field').hidden = true;
                $('erase-note').hidden = true;
            }
            setButtons({ next: 'Use this storage', skip: true });
            return;
        }
        if (!newBlock) return;
        newBlock.hidden = false;
        $('protection-field').hidden = false;
        $('erase-note').hidden = false;

        const picks = Array.from(document.querySelectorAll('.disk-pick:checked'));
        const sizes = picks.map((p) => Number(p.dataset.bytes) || 0);
        const field = $('protection-field');
        const previous = document.querySelector('input[name="protection"]:checked')?.value;
        if (picks.length >= 2) {
            const total = sizes.reduce((a, b) => a + b, 0);
            const mirrored = total / 2;
            const choice = previous || 'raid1';
            field.innerHTML = `<span class="label">Protection</span><div class="choice-list">
                <label class="choice"><input type="radio" name="protection" value="raid1"${choice === 'raid1' ? ' checked' : ''}><span><strong>Mirrored</strong><small>Recommended. Every file is on two disks, so one disk can fail without losing anything.</small></span><span class="meta">${total ? `${formatBytes(mirrored)} usable` : ''}</span></label>
                <label class="choice"><input type="radio" name="protection" value="single"${choice === 'single' ? ' checked' : ''}><span><strong>Use all space</strong><small>No protection: if one disk fails, files on it are lost.</small></span><span class="meta">${total ? `${formatBytes(total)} usable` : ''}</span></label>
            </div>`;
            field.querySelectorAll('input').forEach((i) => i.addEventListener('change', updateStorageChoice));
        } else if (picks.length === 1) {
            field.innerHTML = `<div class="note">One disk holds one copy of your files. Add a second disk later in Storage to mirror it, and use Buddy Backup for a copy outside your home.</div>`;
        } else {
            field.innerHTML = '';
        }
        $('erase-note').innerHTML = picks.length
            ? `<strong>Everything on ${picks.length === 1 ? 'this disk' : `these ${picks.length} disks`} will be erased.</strong> They look empty, but check that nothing important is on them.`
            : 'Choose at least one disk.';
        setButtons({ next: picks.length ? 'Erase and set up storage' : 'Continue', nextEnabled: picks.length > 0, skip: true });
    }

    async function submitStorage() {
        if ($('btn-next').textContent === 'Look again') {
            await enterStorage();
            return false;
        }
        if (state.pool) return true;
        const mode = storageMode();
        if (mode.startsWith('import:')) {
            const id = mode.slice(7);
            $('storage-busy').textContent = 'Connecting your storage...';
            $('storage-busy').hidden = false;
            try {
                await api('/storage/pools/import', { method: 'POST', json: { pool_id: id } });
            } finally {
                $('storage-busy').hidden = true;
            }
            const pools = (await api('/storage/pools').catch(() => ({}))).pools || [];
            const pool = pools.find((p) => p.id === id) || pools.find((p) => p.is_managed && !p.is_system_pool);
            state.pool = pool
                ? { id: pool.id, name: pool.name, mount_point: pool.mount_point, how: 'imported' }
                : null;
            if (!state.pool) throw new Error('The storage was connected but could not be read yet. Continue in Storage.');
            return true;
        }
        const devices = Array.from(document.querySelectorAll('.disk-pick:checked')).map((p) => p.value);
        const raid = devices.length >= 2 ? (document.querySelector('input[name="protection"]:checked')?.value || 'raid1') : 'single';
        $('storage-busy').textContent = 'Setting up storage. This can take a minute...';
        $('storage-busy').hidden = false;
        try {
            const result = await api('/storage/pools', { method: 'POST', json: { name: POOL_NAME, devices, raid_level: raid } });
            state.pool = { id: result.pool_id, name: POOL_NAME, mount_point: result.mount_point || `/mnt/alvaos/${POOL_NAME}`, how: 'created', raid, disks: devices.length };
        } finally {
            $('storage-busy').hidden = true;
        }
        return true;
    }

    // ── Step 4: shared folder ───────────────────────────────────────────────

    function enterShare() {
        if (!state.pool) {
            go('done');
            return;
        }
        if (state.share) {
            setButtons({ next: 'Continue' });
            return;
        }
        setButtons({ next: 'Create the folder', skip: true });
        validateShare();
    }

    function validateShare() {
        const access = document.querySelector('input[name="share-access"]:checked').value;
        $('person-fields').hidden = access !== 'person';
        $('person-password-field').hidden = $('person-same-password').checked;
        const shareOk = SHARE_RE.test($('share-name').value.trim());
        $('share-name-hint').className = shareOk ? '' : 'bad';
        let ok = shareOk;
        if (access === 'person') {
            const user = $('person-name').value.trim().toLowerCase();
            const userOk = USER_RE.test(user) && !['root', 'alvaos', 'admin'].includes(user);
            $('person-name-hint').className = !user || userOk ? '' : 'bad';
            $('person-name-hint').textContent = !user || userOk
                ? 'Lowercase letters and numbers. This is what you type when your computer asks.'
                : (['root', 'alvaos', 'admin'].includes(user) ? 'This name is taken by the system. Use your own name.' : 'Start with a letter; use lowercase letters, numbers, "-" and "_".');
            ok = ok && userOk && (state.personCreated || $('person-same-password').checked || $('person-password').value.length >= 8);
        }
        $('btn-next').disabled = !ok;
    }

    async function submitShare() {
        if (state.share) return true;
        const name = $('share-name').value.trim();
        const guest = document.querySelector('input[name="share-access"]:checked').value === 'everyone';
        const user = $('person-name').value.trim().toLowerCase();
        $('share-busy').hidden = false;
        try {
            if (!guest && state.personCreated !== user) {
                const password = $('person-same-password').checked ? $('password').value : $('person-password').value;
                await api('/users', { method: 'POST', json: { username: user, password } });
                state.personCreated = user;
            }
            await api('/storage/shares', {
                method: 'POST',
                json: {
                    name,
                    pool_id: state.pool.id,
                    folder: name,
                    new_folder: true,
                    protocol: 'smb',
                    guest_access: guest,
                    smb_permissions: guest ? {} : { [user]: 'write' },
                },
            });
            state.share = { name, guest, person: guest ? '' : user };
            if ($('share-backup').checked) {
                try {
                    await api('/backup/settings', {
                        method: 'POST',
                        json: { pool_backup: { enabled: true, interval_minutes: 1440, keep_last: 30, target_path: '', sources: [`${state.pool.mount_point}/${name}`] } },
                    });
                    state.backup = true;
                } catch (_e) {
                    state.backup = false;
                }
            }
        } finally {
            $('share-busy').hidden = true;
        }
        return true;
    }

    // ── Step 5: done ────────────────────────────────────────────────────────

    function enterDone() {
        setButtons({ next: 'Open the dashboard' });
        const host = state.hostname || 'your-nas';
        const items = [];
        items.push(['ok', `Name and password set`, `This NAS is called ${host}.${state.nameNote ? ` ${state.nameNote}` : ''}`]);
        if (state.pool) {
            const how = { created: 'Storage set up', imported: 'Storage from before connected', existing: 'Storage ready' }[state.pool.how];
            const detail = state.pool.how === 'created'
                ? (state.pool.raid === 'raid1' ? `Mirrored on ${state.pool.disks} disks: one can fail without losing files.` : `On ${state.pool.disks === 1 ? 'one disk' : `${state.pool.disks} disks`}, without protection.`)
                : `"${state.pool.name}"`;
            items.push(['ok', how, detail]);
        } else {
            items.push(['later', 'Storage: later', 'The dashboard reminds you. Open Storage when your disks are connected.']);
        }
        if (state.share) {
            items.push(['ok', `Shared folder "${state.share.name}"`, state.share.guest ? 'Everyone on your home network can open it.' : `Sign in as ${state.share.person} with your password.`]);
            items.push(state.backup
                ? ['ok', 'Daily restore points', 'Older versions of your files are kept for 30 days.']
                : ['later', 'Backups: later', 'Turn them on in Backup.']);
        } else if (state.pool) {
            items.push(['later', 'Shared folder: later', 'Create one in Storage › Shares.']);
        }
        $('done-list').innerHTML = items.map(([kind, title, detail]) => `
            <li><span class="mark${kind === 'later' ? ' later' : ''}">${kind === 'later' ? '–' : '✓'}</span><span><strong>${esc(title)}</strong><small>${esc(detail)}</small></span></li>`).join('');

        $('done-addresses').innerHTML = state.share ? `
            <div class="field"><span class="label">Open "${esc(state.share.name)}" from your devices</span>
                <div class="address"><code>\\\\${esc(host)}\\${esc(state.share.name)}</code><span class="who">Windows: File Explorer</span></div>
                <div class="address"><code>smb://${esc(host)}.local/${esc(state.share.name)}</code><span class="who">Mac: Finder › Go › Connect</span></div>
                <small style="display:block;margin-top:8px;color:var(--text-secondary);font-size:0.8rem;">Phones and Linux: Storage › Shares › How to connect.</small>
            </div>` : '';
    }

    // ── Wiring ──────────────────────────────────────────────────────────────

    $('btn-next').addEventListener('click', async () => {
        const button = $('btn-next');
        const step = current();
        showError('');
        try {
            if (step === 'welcome') return go('account');
            if (step === 'account') {
                if (!state.accountDone) await withBusy(button, 'Saving...', submitAccount);
                return go('storage');
            }
            if (step === 'storage') {
                const label = button.textContent === 'Look again' ? 'Looking...' : 'Working...';
                const advance = await withBusy(button, label, submitStorage);
                if (advance) go('share');
                return undefined;
            }
            if (step === 'share') {
                await withBusy(button, 'Creating...', submitShare);
                return go('done');
            }
            if (step === 'done') window.location.href = '/';
        } catch (e) {
            showError(e.message);
            if (step === 'account' && e.status === 409) {
                // Setup was already finished (another browser): go and sign in.
                window.location.href = '/login.html';
            }
        }
        return undefined;
    });

    $('btn-back').addEventListener('click', () => {
        if (current() === 'account' && !state.accountDone) go('welcome');
    });

    $('btn-skip').addEventListener('click', () => {
        if (current() === 'storage') go('share');
        else if (current() === 'share') go('done');
    });

    ['nas-name', 'password', 'password-confirm'].forEach((id) => $(id).addEventListener('input', validateAccount));
    $('nas-name').addEventListener('blur', () => {
        $('nas-name').value = cleanName($('nas-name').value);
        validateAccount();
    });
    ['share-name', 'person-name', 'person-password'].forEach((id) => $(id).addEventListener('input', validateShare));
    document.querySelectorAll('input[name="share-access"], #person-same-password').forEach((el) => el.addEventListener('change', validateShare));
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && event.target.tagName === 'INPUT' && event.target.type !== 'checkbox' && event.target.type !== 'radio' && !$('btn-next').disabled && !$('btn-next').hidden) {
            event.preventDefault();
            $('btn-next').click();
        }
    });

    async function start() {
        setupTimezones();
        try {
            const status = await api('/setup/status');
            if (status.setup_complete) {
                let token = '';
                try { token = localStorage.getItem('alvaos_token') || ''; } catch (_e) { /* storage off */ }
                window.location.href = token ? '/' : '/login.html';
                return;
            }
            state.suggestedName = cleanName(status.hostname || '');
        } catch (_e) {
            // Show the wizard anyway; the account step reports what fails.
        }
        const suggestion = state.suggestedName && !['localhost', 'debian'].includes(state.suggestedName) ? state.suggestedName : 'alva-home';
        $('nas-name').value = suggestion;
        render();
    }

    start();
})();
