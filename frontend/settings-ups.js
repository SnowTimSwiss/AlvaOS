// Settings › Power › Battery backup: a UPS on USB, run by NUT
// (backend/ups_nut.py). Find it, set it up, see whether the NAS runs on mains
// or on battery, and choose when it shuts down.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let state = null;
    let polling = 0;

    async function api(options = {}) {
        const res = await fetch(`${API_BASE}/system/ups`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }

    const whenLabel = (m) => (m ? `After ${m} minutes on battery` : 'When the battery runs low (recommended)');

    function liveText(live) {
        if (!live.reachable) return ['warn', 'Does not answer'];
        const parts = [];
        if (live.charge_percent !== null && live.charge_percent !== undefined) parts.push(`${live.charge_percent}%`);
        if (live.runtime_minutes !== null && live.runtime_minutes !== undefined) parts.push(`about ${live.runtime_minutes} min`);
        if (live.on_battery) return ['bad', `On battery${parts.length ? ` · ${parts.join(' · ')}` : ''}`];
        return ['ok', `On mains${parts.length ? ` · ${parts.join(' · ')}` : ''}`];
    }

    function render() {
        const desc = $('nut-desc');
        const value = $('nut-summary');
        const action = $('nut-action');
        if (!desc || !state) return;
        const { settings, detected, live, job } = state;
        action.innerHTML = '';
        value.innerHTML = '';
        if (job.running) {
            desc.textContent = `${job.step || 'Setting up'}… This takes a minute or two.`;
            value.innerHTML = '<span class="pill">Setting up</span>';
            if (!polling) polling = setTimeout(load, 3000);
            return;
        }
        if (settings.enabled) {
            const [tone, text] = liveText(live || {});
            desc.innerHTML = `${esc(settings.name || 'UPS')}. Shuts down ${settings.shutdown_after_minutes ? `after ${esc(settings.shutdown_after_minutes)} minutes on battery` : 'when the battery runs low'}.`
                + (live && live.replace_battery ? ' <strong>The UPS says its battery is worn out.</strong>' : '');
            value.innerHTML = `<span class="pill ${tone}">${esc(text)}</span>`;
            action.innerHTML = '<button type="button" class="btn-secondary" id="nut-change">Change</button>';
            $('nut-change').onclick = changeDialog;
        } else if (detected.length) {
            desc.textContent = `${detected[0].name} is connected. Set it up, and the NAS shuts down cleanly in a power cut.`;
            action.innerHTML = '<button type="button" class="btn-primary" id="nut-setup">Set up</button>';
            $('nut-setup').onclick = setUpDialog;
        } else {
            desc.textContent = 'Connect a UPS with its USB cable. AlvaOS then shuts down cleanly in a power cut and starts again when the power is back.';
            value.innerHTML = '<span class="set-desc">No UPS found</span>';
            action.innerHTML = '<button type="button" class="btn-secondary" id="nut-look">Look again</button>';
            $('nut-look').onclick = load;
        }
        if (job.error) desc.innerHTML += ` <span class="nut-error" role="alert">${esc(job.error)}</span>`;
    }

    function choices(current) {
        return state.shutdown_choices.map((m) => `
            <label class="choice"><input type="radio" name="nut-when" value="${m}"${m === current ? ' checked' : ''}>
                <div><strong>${esc(whenLabel(m))}</strong>${m ? '' : '<span>Uses the battery for as long as is safe. Short cuts do not stop the NAS.</span>'}</div></label>`).join('');
    }
    const chosen = (dlg) => Number(dlg.$('input[name="nut-when"]:checked')?.value || 0);

    function setUpDialog() {
        const list = state.detected;
        const dlg = openDialog({
            title: 'Set up the UPS',
            body: `
                ${list.length > 1 ? `<label class="nut-field">UPS<select id="nut-device">${list.map((d, i) => `<option value="${i}">${esc(d.name)}</option>`).join('')}</select></label>`
        : `<p class="nut-device">${esc(list[0].name)}</p>`}
                <div class="nut-field"><span>Shut the NAS down</span><div class="choice-list">${choices(0)}</div></div>
                <p class="field-hint">AlvaOS installs NUT (Network UPS Tools) for this. For the NAS to start again by itself when the power is back, set "Restore on AC power loss" to "Power on" in its BIOS.</p>
                <div class="nut-error" id="nut-err" role="alert"></div>`,
            actions: `
                <button type="button" class="btn-secondary" data-close>Cancel</button>
                <button type="button" class="btn-primary" id="nut-go">Set up</button>`,
        });
        dlg.$('#nut-go').onclick = async () => {
            const device = list[Number(dlg.$('#nut-device')?.value || 0)];
            dlg.$('#nut-go').disabled = true;
            try {
                state = await api({ method: 'POST', body: JSON.stringify({ action: 'set_up', vendorid: device.vendorid, productid: device.productid, shutdown_after_minutes: chosen(dlg) }) });
                dlg.close();
                render();
            } catch (e) {
                dlg.$('#nut-err').textContent = e.message;
                dlg.$('#nut-go').disabled = false;
            }
        };
    }

    function changeDialog() {
        const s = state.settings;
        const dlg = openDialog({
            title: 'UPS',
            body: `
                <p class="nut-device">${esc(s.name || 'UPS')}</p>
                <div class="nut-field"><span>Shut the NAS down</span><div class="choice-list">${choices(s.shutdown_after_minutes || 0)}</div></div>
                <div class="nut-error" id="nut-err" role="alert"></div>`,
            actions: `
                <button type="button" class="btn-danger-quiet" id="nut-off">Turn off</button>
                <button type="button" class="btn-secondary" data-close>Cancel</button>
                <button type="button" class="btn-primary" id="nut-save">Save</button>`,
        });
        const run = async (body, done) => {
            try {
                state = await api({ method: 'POST', body: JSON.stringify(body) });
                dlg.close();
                render();
                window.showToast?.(done, 'success');
            } catch (e) {
                dlg.$('#nut-err').textContent = e.message;
            }
        };
        dlg.$('#nut-save').onclick = () => run({ action: 'change', shutdown_after_minutes: chosen(dlg) }, 'Saved.');
        dlg.$('#nut-off').onclick = async () => {
            if (!await window.showConfirm('Turn the UPS off?\nThe NAS no longer shuts down by itself in a power cut. NUT stays installed.')) return;
            run({ action: 'off' }, 'The NAS no longer watches the UPS.');
        };
    }

    async function load() {
        polling = 0;
        try {
            state = await api();
            render();
        } catch (e) {
            if ($('nut-desc')) $('nut-desc').textContent = e.message;
        }
    }

    if ($('nut-row')) load();
})();
