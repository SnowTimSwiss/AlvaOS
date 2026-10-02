// Settings › Power: let hard disks sleep. SSDs and the system disk are not
// affected; the line under the setting says how many disks it applies to.
(function () {
    const select = document.getElementById('disk-sleep-select');
    const desc = document.getElementById('disk-sleep-desc');
    if (!select) return;
    const BASE = 'Saves power and noise when nobody uses the NAS. The first access after a sleep takes a few seconds.';

    function describe(disks) {
        const n = Array.isArray(disks) ? disks.length : 0;
        desc.textContent = n
            ? `${BASE} Applies to ${n} hard disk${n === 1 ? '' : 's'}; SSDs and the system disk are left alone.`
            : `${BASE} This NAS has no hard disks it applies to (SSDs do not need it).`;
    }

    async function call(options) {
        const res = await fetch(`${API_BASE}/storage/disk-power`, {
            ...options,
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'The setting was not saved.');
        return data;
    }

    let current = '0';
    call({}).then((data) => {
        current = String(data.spindown_minutes || 0);
        select.value = current;
        describe(data.disks);
    }).catch(() => {
        document.getElementById('disk-power-group').hidden = true;
    });

    select.addEventListener('change', async () => {
        select.disabled = true;
        try {
            const data = await call({ method: 'POST', body: JSON.stringify({ spindown_minutes: Number(select.value) }) });
            current = select.value;
            describe(data.disks);
            if (data.failed && data.failed.length) {
                window.showToast(`Saved, but ${data.failed.join(', ')} did not accept it.`, 'warning');
            } else {
                window.showToast(select.value === '0' ? 'Hard disks stay awake.' : `Hard disks sleep after ${select.options[select.selectedIndex].text.replace('After ', '')} without use.`, 'success');
            }
        } catch (e) {
            select.value = current;
            window.showToast(e.message, 'error');
        } finally {
            select.disabled = false;
        }
    });
})();
