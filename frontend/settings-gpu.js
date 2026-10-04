// Settings › Graphics: graphics cards, their driver, and installing what is
// missing (backend/gpu_manager.py).
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const PILLS = {
        ready: ['ok', 'Ready'],
        partial: ['warn', 'Video drivers missing'],
        missing: ['warn', 'Needs a driver'],
        restart: ['warn', 'Restart needed'],
        unknown: ['', 'Unknown'],
    };
    let state = null;
    let polling = 0;

    async function api(path, options = {}) {
        const res = await fetch(`${API_BASE}${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }

    function cardHtml(card, job) {
        const [tone, label] = PILLS[card.state] || PILLS.unknown;
        const busy = job.running && job.vendor === card.vendor;
        const canInstall = card.missing.length && !job.running && card.vendor !== 'other';
        const nvidiaNote = card.vendor === 'nvidia'
            ? `<p class="set-desc gpu-note">NVIDIA's driver is built for the running system and needs a restart.${state.secure_boot
                ? ' <strong>Secure Boot is on:</strong> after the restart the NAS asks once on its screen to confirm a key (MOK). Connect a screen and keyboard, or turn Secure Boot off in the firmware settings first.'
                : ''} Apps in containers also need NVIDIA's container toolkit, which AlvaOS sets up in a later version.</p>`
            : '';
        return `
            <div class="gpu-card">
                <div class="gpu-head">
                    <div>
                        <strong>${esc(card.vendor_name)} ${esc(card.model)}</strong>
                        <span>${card.driver ? `Driver: ${esc(card.driver)}` : 'No driver'}${card.render_node ? ` · ${esc(card.render_node)}` : ''}${card.screen ? ' · shows the console' : ''}</span>
                    </div>
                    <span class="pill ${tone}">${esc(label)}</span>
                </div>
                <p class="set-desc">${esc(card.advice)}</p>
                ${nvidiaNote}
                <div class="gpu-actions">
                    ${busy ? '<span class="gpu-busy" role="status">Installing… this takes a few minutes.</span>' : ''}
                    ${canInstall ? `<button type="button" class="btn-primary" data-install="${esc(card.vendor)}">${card.vendor === 'nvidia' ? 'Install NVIDIA driver' : 'Install firmware and video drivers'}</button>` : ''}
                    ${card.missing.length && !job.running ? `<span class="set-desc">Installs ${esc(card.missing.join(', '))}</span>` : ''}
                </div>
            </div>`;
    }

    function render() {
        const body = $('gpu-body');
        const job = state.job || {};
        if (!state.cards.length) {
            body.innerHTML = '<p class="set-desc">No graphics card was found. Video is then converted by the processor; that works, it is only slower.</p>';
            return;
        }
        body.innerHTML = `
            ${state.restart_needed ? `<div class="gpu-restart" role="status"><span>A new driver is installed. Restart the NAS to start using it.</span>
                <button type="button" class="btn-primary" id="gpu-restart">Restart now</button></div>` : ''}
            ${job.error && !job.running ? `<div class="gpu-error" role="alert">${esc(job.error)}</div>` : ''}
            ${state.cards.map((c) => cardHtml(c, job)).join('')}`;
        body.querySelectorAll('[data-install]').forEach((b) => b.addEventListener('click', () => install(b.dataset.install)));
        $('gpu-restart')?.addEventListener('click', () => (window.sendPowerAction ? window.sendPowerAction('reboot') : null));
    }

    async function install(vendor) {
        const card = state.cards.find((c) => c.vendor === vendor);
        const nvidia = vendor === 'nvidia';
        const ok = await window.showConfirm(
            `${nvidia ? 'Install NVIDIA\'s driver?' : 'Install firmware and video drivers?'}\nAlvaOS downloads ${card.missing.join(', ')} from Debian and installs them. ${nvidia ? 'This takes a few minutes; then the NAS needs a restart.' : 'This takes a minute or two.'}`,
            { confirmLabel: 'Install' });
        if (!ok) return;
        try {
            const data = await api('/system/gpu/install', { method: 'POST', body: JSON.stringify({ vendor }) });
            window.showToast?.(data.message, 'info');
        } catch (e) {
            window.showToast?.(e.message, 'error');
        }
        load();
    }

    async function load() {
        if (!$('gpu-body')) return;
        try {
            state = await api('/system/gpu');
        } catch (e) {
            $('gpu-body').innerHTML = `<p class="set-desc">${esc(e.message)}</p>`;
            return;
        }
        render();
        // Follow an installation until it is done.
        clearTimeout(polling);
        if (state.job && state.job.running) polling = setTimeout(load, 4000);
    }

    load();
})();
