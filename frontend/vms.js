// The "Virtual machines" page: set up once, then make, start and watch
// machines, and open their screen in the browser (noVNC).
// backend/vm_manager.py, vm_ops.py, vm_console.py; docs/VMS.md.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = window.escapeHtml || ((v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));
    const svg = (name) => (window.alvaIcon ? window.alvaIcon(name, '', 'aria-hidden="true"') : '');
    const toast = (text, kind) => window.showToast && window.showToast(text, kind || 'success');
    const OS_ICON = { windows11: 'monitor', windows10: 'monitor', linux: 'terminal', other: 'cpu' };
    const OS_PRESET = { windows11: [4, 8192, 80], windows10: [2, 4096, 64], linux: [2, 2048, 32], other: [1, 1024, 16] };
    const STATE_TEXT = { running: 'Running', starting: 'Starting…', stopping: 'Shutting down…', failed: 'Stopped with a problem', stopped: 'Off' };
    let state = null;
    let isos = null;
    let dialogs = 0;
    let screen = null;

    async function call(path = '', options = {}) {
        const res = await fetch(`${API_BASE}/vms${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }
    const post = (path, body, method = 'POST') => call(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

    const gb = (mb) => (mb % 1024 === 0 ? String(mb / 1024) : (mb / 1024).toFixed(1));
    const bytes = (n) => {
        if (n === null || n === undefined) return '';
        const g = n / 2 ** 30;
        if (g < 0.1) return `${Math.max(1, Math.round(n / 2 ** 20))} MB`;
        return g >= 10 ? `${Math.round(g)} GB` : `${g.toFixed(1)} GB`;
    };
    const fileSize = (n) => (n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : `${Math.round(n / 2 ** 20)} MB`);

    // ── The page ───────────────────────────────────────────────────────────
    function render() {
        $('vm-hero-ic').innerHTML = svg('server');
        const actions = $('vm-actions');
        const ok = state.ready;
        actions.innerHTML = ok ? '<button type="button" class="btn-primary" id="vm-new">New virtual machine</button>' : '';
        if (ok) $('vm-new').onclick = () => newDialog();
        renderSetup();
        renderList();
    }

    function renderSetup() {
        const box = $('vm-setup');
        const st = state;
        if (st.ready) { box.innerHTML = st.problem ? `<div class="vm-note bad">${esc(st.problem)}</div>` : ''; return; }
        const job = st.job || {};
        if (!st.host.supported) {
            box.innerHTML = `<section class="vm-card"><h2>This NAS cannot run virtual machines</h2><p>${esc(st.host.reason)}</p></section>`;
            return;
        }
        if (job.running) {
            box.innerHTML = `<section class="vm-card"><h2>Setting up…</h2><div class="vm-progress"><span class="vm-spin"></span><span>Installing what virtual machines need. This takes a few minutes; you can leave this page.</span></div></section>`;
            return;
        }
        const pools = st.pools || [];
        const missing = st.missing_packages || [];
        box.innerHTML = `
            <section class="vm-card">
                <h2>Set up virtual machines</h2>
                <p>This installs the virtualization software once (a few hundred MB) and makes a shared folder called <strong>VMs</strong> where the disks of your machines live. Only you can see it. It is backed up and limited like any other folder.</p>
                ${job.error ? `<div class="vm-note bad">${esc(job.error)}</div>` : ''}
                ${st.problem ? `<div class="vm-note">${esc(st.problem)}</div>` : ''}
                ${st.store ? `<p>The folder <strong>${esc(st.store)}</strong> is there. Only the software is missing${missing.length ? `: ${esc(missing.join(', '))}` : ''}.</p>` : ''}
                ${pools.length || st.store ? `
                    <div class="vm-row">
                        ${st.store ? '' : `<label>Keep the disks on
                            <select id="vm-pool" aria-label="Pool for the virtual machines">${pools.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select></label>`}
                        <button type="button" class="btn-primary" id="vm-setup-btn">${job.error ? 'Try again' : 'Set up'}</button>
                    </div>` : '<div class="vm-note">Set up storage first: Storage › Pools.</div>'}
            </section>`;
        const btn = $('vm-setup-btn');
        if (btn) btn.onclick = async () => {
            btn.disabled = true;
            try {
                state = await post('/setup', { pool_id: $('vm-pool') ? $('vm-pool').value : '' });
                if (state.message) toast(state.message);
                render();
            } catch (e) {
                toast(e.message, 'error');
                btn.disabled = false;
            }
        };
    }

    function stateHtml(vm) {
        const dot = { running: '<span class="status-dot online"></span>', starting: '<span class="status-dot warning"></span>',
            stopping: '<span class="status-dot warning"></span>', failed: '<span class="status-dot critical"></span>' }[vm.state] || '<span class="idle"></span>';
        return `<span class="vm-state">${dot}${esc(STATE_TEXT[vm.state] || vm.state)}</span>`;
    }

    function renderList() {
        const list = $('vm-list');
        if (!state.ready) { list.innerHTML = ''; return; }
        if (!state.vms.length) {
            list.innerHTML = `<div class="vm-card vm-empty"><strong>No virtual machines yet</strong>Make one for Windows or Linux, start it and use its screen right here.</div>`;
            return;
        }
        list.innerHTML = state.vms.map((vm) => {
            const busy = vm.state === 'starting' || vm.state === 'stopping';
            const buttons = {
                running: `<button type="button" class="btn-primary" data-act="screen">Open screen</button>
                    <button type="button" class="btn-secondary" data-act="stop">Shut down</button>
                    <button type="button" class="btn-secondary btn-quiet" data-act="restart">Restart</button>
                    <button type="button" class="btn-danger-quiet" data-act="force">Switch off</button>`,
                starting: '<button type="button" class="btn-secondary" disabled>Starting…</button>',
                stopping: '<button type="button" class="btn-secondary" disabled>Shutting down…</button><button type="button" class="btn-danger-quiet" data-act="force">Switch off</button>',
            }[vm.state] || `<button type="button" class="btn-primary" data-act="start">Start</button>
                    <button type="button" class="btn-secondary" data-act="settings">Settings</button>
                    <button type="button" class="btn-danger-quiet" data-act="delete">Delete</button>`;
            const meta = [`${vm.os_name}`, `${vm.cpus} core${vm.cpus === 1 ? '' : 's'}`, `${gb(vm.memory_mb)} GB memory`,
                `${vm.disk_gb} GB disk${vm.disk_used_bytes !== null ? ` (${bytes(vm.disk_used_bytes)} used)` : ''}`];
            if (vm.autostart) meta.push('starts with the NAS');
            return `
                <article class="vm is-${esc(vm.state)}" data-id="${esc(vm.id)}">
                    <div class="vm-ic">${svg(OS_ICON[vm.os] || 'cpu')}</div>
                    <div>
                        <div class="vm-name">${esc(vm.name)} ${stateHtml(vm)}</div>
                        <div class="vm-meta">${esc(meta.join(' · '))}</div>
                        ${vm.iso_name ? `<div class="vm-meta">Installer attached: ${esc(vm.iso_name)}${vm.state === 'stopped' ? ' <button type="button" class="vm-link" data-act="eject">Eject</button>' : ''}</div>` : ''}
                        ${vm.problem ? `<div class="vm-problem">${esc(vm.problem)}</div>` : ''}
                    </div>
                    <div class="vm-actions" aria-busy="${busy}">${buttons}</div>
                </article>`;
        }).join('');
    }

    $('vm-list').addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-act]');
        if (!btn) return;
        const vm = state.vms.find((v) => v.id === btn.closest('[data-id]').dataset.id);
        const act = btn.dataset.act;
        if (act === 'screen') return openScreen(vm);
        if (act === 'settings') return settingsDialog(vm);
        if (act === 'eject') return change(vm, { iso: '' }, 'Installer ejected.');
        if (act === 'delete') {
            return window.confirmModal(`Delete "${vm.name}"?\nThe machine and its whole disk are removed for good. Restore points of the VMs folder may still hold an older copy.`,
                { danger: true, confirmLabel: 'Delete', onConfirm: () => run(() => post(`/${vm.id}`, undefined, 'DELETE')) });
        }
        if (act === 'force') {
            return window.confirmModal(`Switch off "${vm.name}"?\nLike pulling the plug: unsaved work in it is lost and its disk may need repairing.`,
                { danger: true, confirmLabel: 'Switch off', onConfirm: () => run(() => post(`/${vm.id}/action`, { action: 'force' })) });
        }
        btn.disabled = true;
        run(() => post(`/${vm.id}/action`, { action: act }));
    });

    async function run(work) {
        try {
            const got = await work();
            state = got;
            if (got.message) toast(got.message);
        } catch (e) {
            toast(e.message, 'error');
        }
        render();
    }
    const change = (vm, values, message) => run(async () => {
        const got = await post(`/${vm.id}`, values);
        got.message = message;
        return got;
    });

    // ── Dialogs ────────────────────────────────────────────────────────────
    function dialog(title, bodyHtml, actionsHtml) {
        dialogs += 1;
        const d = openDialog({ title: esc(title), body: bodyHtml, actions: actionsHtml || ' ',
            className: 'vm-dialog', onClose: () => { dialogs -= 1; } });
        return { el: d.dialog, close: () => d.close() };
    }

    async function loadIsos() {
        try { isos = (await call('/isos')).isos; } catch (_e) { isos = []; }
        return isos;
    }
    function isoOptions(current) {
        const list = isos || [];
        const known = list.some((i) => i.path === current);
        return `<option value=""${current ? '' : ' selected'}>None</option>`
            + (current && !known ? `<option value="${esc(current)}" selected>${esc(current.split('/').pop())}</option>` : '')
            + list.map((i) => `<option value="${esc(i.path)}"${i.path === current ? ' selected' : ''}>${esc(i.where && i.where !== '.' ? `${i.where}/` : '')}${esc(i.name)} (${fileSize(i.size_bytes)})</option>`).join('');
    }
    const isoHelp = '<small>Put installer files (.iso) in the folder <strong>ISOs</strong> of the VMs shared folder: in the Hub, open Files as administrator, or copy them over the network.</small>';

    function portRow(p = { proto: 'tcp', host: '', guest: '' }) {
        return `<div class="vm-port"><select aria-label="Protocol"><option value="tcp"${p.proto === 'tcp' ? ' selected' : ''}>TCP</option><option value="udp"${p.proto === 'udp' ? ' selected' : ''}>UDP</option></select>
            <input type="number" min="1024" max="65535" placeholder="NAS" value="${esc(p.host)}" aria-label="Port on the NAS"><span>→</span>
            <input type="number" min="1" max="65535" placeholder="Machine" value="${esc(p.guest)}" aria-label="Port in the machine">
            <button type="button" class="btn-secondary btn-quiet" data-del-port aria-label="Remove">&times;</button></div>`;
    }
    function portsHtml(ports) {
        return `<div class="vm-field"><span>Forward ports</span>
            <small>To reach something inside the machine from your network, like Remote Desktop (3389) or SSH (22). The machine itself can always reach the internet.</small>
            <div class="vm-ports" id="vm-ports">${(ports || []).map(portRow).join('')}</div>
            <div><button type="button" class="btn-secondary" id="vm-add-port">Forward a port</button></div></div>`;
    }
    function bindPorts(el) {
        const wrap = el.querySelector('#vm-ports');
        el.querySelector('#vm-add-port').onclick = () => wrap.insertAdjacentHTML('beforeend', portRow());
        wrap.addEventListener('click', (e) => { if (e.target.closest('[data-del-port]')) e.target.closest('.vm-port').remove(); });
    }
    function readPorts(el) {
        // Each row: protocol, port on the NAS, an arrow, port in the machine, remove.
        return [...el.querySelectorAll('.vm-port')].map((row) => ({
            proto: row.children[0].value, host: row.children[1].value, guest: row.children[3].value,
        })).filter((p) => p.host || p.guest);
    }

    async function newDialog() {
        const limits = state.limits;
        const d = dialog('New virtual machine', '<div class="vm-summary">Looking for installers…</div>', '');
        await loadIsos();
        const types = state.os_types;
        const clamp = (os) => {
            const [c, m, g] = OS_PRESET[os] || OS_PRESET.other;
            return [Math.min(c, limits.cpus), Math.min(m, limits.memory_mb), g];
        };
        let os = 'windows11';
        let [cpus, mem, disk] = clamp(os);
        d.el.querySelector('.modal-body').innerHTML = `
            <label class="vm-field">Name<input id="vm-name" maxlength="60" placeholder="Windows 11, Work Linux, …" autocomplete="off"></label>
            <div class="vm-field"><span>What will run in it?</span>
                <div class="vm-os" role="radiogroup">${types.map((t) => `
                    <label><input type="radio" name="vm-os" value="${esc(t.id)}"${t.id === os ? ' checked' : ''}>${svg(OS_ICON[t.id] || 'cpu')}${esc(t.name)}</label>`).join('')}</div></div>
            <label class="vm-field">Installer<select id="vm-iso">${isoOptions('')}</select>${isoHelp}</label>
            <div class="vm-summary"><span id="vm-sum"></span></div>
            <details class="vm-more modal-details"><summary>More options</summary>
                <div class="modal-disclosure-panel">
                    <div class="vm-grid" style="margin-top:8px">
                        <label class="vm-field">Processor cores<input type="number" id="vm-cpus" min="1" max="${limits.cpus}"></label>
                        <label class="vm-field">Memory (GB)<input type="number" id="vm-mem" min="0.5" step="0.5" max="${gb(limits.memory_mb)}"></label>
                        <label class="vm-field">Disk (GB)<input type="number" id="vm-disk" min="1"></label>
                    </div>
                    <label class="vm-check" style="margin:12px 0"><input type="checkbox" id="vm-auto"> Start it together with the NAS</label>
                    ${portsHtml([])}
                </div>
            </details>
            <div class="vm-error" id="vm-err" role="alert"></div>`;
        d.el.querySelector('.modal-actions').innerHTML = '<button type="button" class="btn-secondary" data-close>Cancel</button><button type="button" class="btn-primary" id="vm-make">Create</button>';
        const q = (s) => d.el.querySelector(s);
        const paint = () => {
            q('#vm-cpus').value = cpus;
            q('#vm-mem').value = gb(mem);
            q('#vm-disk').value = disk;
            q('#vm-sum').textContent = `${cpus} core${cpus === 1 ? '' : 's'} · ${gb(mem)} GB memory · ${disk} GB disk`;
        };
        paint();
        d.el.addEventListener('change', (e) => {
            if (e.target.name === 'vm-os') {
                os = e.target.value;
                [cpus, mem, disk] = clamp(os);
                paint();
            } else if (e.target.id === 'vm-cpus') { cpus = Math.max(1, Number(e.target.value) || 1); paint(); }
            else if (e.target.id === 'vm-mem') { mem = Math.max(256, Math.round((Number(e.target.value) || 1) * 1024)); paint(); }
            else if (e.target.id === 'vm-disk') { disk = Math.max(1, Number(e.target.value) || 1); paint(); }
        });
        bindPorts(d.el);
        q('#vm-name').focus();
        q('#vm-make').onclick = async () => {
            const btn = q('#vm-make');
            btn.disabled = true;
            btn.textContent = 'Creating…';
            q('#vm-err').textContent = '';
            try {
                state = await post('', { name: q('#vm-name').value, os, cpus, memory_mb: mem, disk_gb: disk, iso: q('#vm-iso').value,
                    autostart: q('#vm-auto').checked, ports: readPorts(d.el) });
                d.close();
                toast(`"${state.vm.name}" is made. Press Start.`);
                render();
            } catch (e) {
                q('#vm-err').textContent = e.message;
                btn.disabled = false;
                btn.textContent = 'Create';
            }
        };
    }

    async function settingsDialog(vm) {
        const limits = state.limits;
        const d = dialog(`Settings of ${vm.name}`, '<div class="vm-summary">Looking for installers…</div>', '');
        await loadIsos();
        d.el.querySelector('.modal-body').innerHTML = `
            <label class="vm-field">Name<input id="vm-name" maxlength="60" value="${esc(vm.name)}" autocomplete="off"></label>
            <label class="vm-field">Installer<select id="vm-iso">${isoOptions(vm.iso)}</select>
                <small>The machine starts from it as long as it is attached and the disk is empty. Eject it after installing.</small>${isoHelp}</label>
            <div class="vm-grid">
                <label class="vm-field">Processor cores<input type="number" id="vm-cpus" min="1" max="${limits.cpus}" value="${vm.cpus}"></label>
                <label class="vm-field">Memory (GB)<input type="number" id="vm-mem" min="0.5" step="0.5" max="${gb(limits.memory_mb)}" value="${gb(vm.memory_mb)}"></label>
                <label class="vm-field">Disk (GB)<input type="number" id="vm-disk" min="${vm.disk_gb}" value="${vm.disk_gb}"></label>
            </div>
            <small class="vm-hint">The disk can only grow. Afterwards, let the system in the machine use the new space (Windows: Disk Management › Extend Volume).</small>
            <label class="vm-check"><input type="checkbox" id="vm-auto"${vm.autostart ? ' checked' : ''}> Start it together with the NAS</label>
            ${portsHtml(vm.ports)}
            <div class="vm-error" id="vm-err" role="alert"></div>`;
        d.el.querySelector('.modal-actions').innerHTML = '<button type="button" class="btn-secondary" data-close>Cancel</button><button type="button" class="btn-primary" id="vm-save">Save</button>';
        bindPorts(d.el);
        const q = (s) => d.el.querySelector(s);
        q('#vm-save').onclick = async () => {
            q('#vm-err').textContent = '';
            try {
                state = await post(`/${vm.id}`, { name: q('#vm-name').value, iso: q('#vm-iso').value, cpus: Number(q('#vm-cpus').value),
                    memory_mb: Math.round(Number(q('#vm-mem').value) * 1024), disk_gb: Number(q('#vm-disk').value),
                    autostart: q('#vm-auto').checked, ports: readPorts(d.el) });
                d.close();
                toast('Saved. It applies the next time the machine starts.');
                render();
            } catch (e) {
                q('#vm-err').textContent = e.message;
            }
        };
    }

    // ── The screen ─────────────────────────────────────────────────────────
    async function openScreen(vm) {
        if (screen) return;
        let ticket;
        try {
            ticket = await post(`/${vm.id}/console`);
        } catch (e) {
            toast(e.message, 'error');
            return;
        }
        const el = document.createElement('div');
        el.className = 'vm-screen';
        el.innerHTML = `
            <div class="vm-screen-bar"><strong>${esc(vm.name)}</strong><span id="vm-screen-state">Connecting…</span>
                <button type="button" id="vm-cad" title="Send Ctrl+Alt+Delete to the machine">Ctrl+Alt+Del</button>
                <button type="button" id="vm-full">Full screen</button>
                <button type="button" id="vm-close">Close</button></div>
            <div class="vm-screen-view" id="vm-view"><div class="vm-screen-msg" id="vm-msg">Connecting…</div></div>`;
        document.body.appendChild(el);
        let rfb = null;
        const close = () => {
            if (rfb) { try { rfb.disconnect(); } catch (_e) { /* gone already */ } }
            el.remove();
            document.removeEventListener('keydown', onKey, true);
            screen = null;
        };
        const onKey = (e) => { if (e.key === 'Escape' && !document.fullscreenElement) { /* the machine gets Escape: only the button closes */ e.stopPropagation(); } };
        document.addEventListener('keydown', onKey, true);
        screen = { close };
        $('vm-close').onclick = close;
        $('vm-full').onclick = () => { if (document.fullscreenElement) document.exitFullscreen(); else el.requestFullscreen().catch(() => {}); };
        const say = (text) => { $('vm-screen-state').textContent = text; };
        try {
            await import('./vendor/novnc/novnc.js');
            const secure = window.location.protocol === 'https:';
            const url = `${secure ? 'wss' : 'ws'}://${window.location.hostname}:${secure ? ticket.tls_port : ticket.port}/?ticket=${encodeURIComponent(ticket.ticket)}`;
            // QEMU's WebSocket speaks the "binary" subprotocol.
            rfb = new window.RFB($('vm-view'), url, { wsProtocols: ['binary'] });
            rfb.scaleViewport = true;
            rfb.resizeSession = false;
            rfb.background = '#000';
            rfb.addEventListener('connect', () => { say('Connected'); $('vm-msg').hidden = true; rfb.focus(); });
            rfb.addEventListener('disconnect', (e) => {
                if (!el.isConnected) return;   // closed by the person
                say('Disconnected');
                $('vm-msg').hidden = false;
                $('vm-msg').textContent = e.detail && e.detail.clean ? 'The virtual machine closed the screen.'
                    : `The screen could not be reached. Is the machine still running?${secure ? ' (An HTTPS page needs the certificate of this NAS trusted for port 9445 too.)' : ''}`;
            });
            $('vm-cad').onclick = () => rfb.sendCtrlAltDel();
        } catch (e) {
            $('vm-msg').textContent = `The screen could not be opened: ${e.message}`;
        }
    }

    // ── Keep the page alive ────────────────────────────────────────────────
    async function refresh() {
        try {
            state = await call();
            render();
        } catch (e) {
            $('vm-setup').innerHTML = `<div class="vm-note bad">${esc(e.message)}</div>`;
        }
    }
    refresh();
    setInterval(() => {
        if (document.hidden || dialogs > 0 || screen) return;
        refresh();
    }, 4000);
})();
