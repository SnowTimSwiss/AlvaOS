// AlvaOS Storage: "What uses the space" on a pool's detail page.
//
// One button measures the pool in the background (folders, apps, restore
// points). The answer stays until the next check. The safe cleanup offered
// here is deleting restore points, showing about how much each one frees.
// Uses helpers from storage-pools.js (formatBytes, icon, storagePoolsCache).

const poolSpace = {};
let poolSpaceTimer = null;

function spaceAge(iso) {
    const when = new Date(iso || '');
    if (Number.isNaN(when.getTime())) return '';
    const minutes = Math.round((Date.now() - when.getTime()) / 60000);
    if (minutes < 2) return 'just now';
    if (minutes < 60) return `${minutes} minutes ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 36) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    return when.toLocaleDateString();
}

function spaceRow(label, bytes, max, kind, extra) {
    const width = max > 0 ? Math.max(1, Math.round((bytes / max) * 100)) : 0;
    return `
        <div class="space-row ${kind}">
            <div class="space-row-head"><span class="space-name">${icon(kind === 'app' ? 'package' : kind === 'snap' ? 'clock-3' : 'folder')}${escapeHtml(label)}</span>
                <span class="space-size">${escapeHtml(formatBytes(bytes))}</span></div>
            <div class="space-bar"><span style="width: ${width}%"></span></div>
            ${extra || ''}
        </div>`;
}

function renderPoolSpace(poolId) {
    const report = poolSpace[poolId];
    const id = jsArg(poolId);
    const head = '<h3>What uses the space</h3>';
    if (!report) return `${head}<div class="pool-line">${icon('loader-circle')}<span>Loading...</span></div>`;
    if (report.state === 'running') {
        return `${head}<div class="pool-line">${icon('loader-circle')}<span>Measuring every folder. On a big pool this takes a few minutes; you can leave this page.</span></div>`;
    }
    const result = report.result;
    if (!result) {
        return `${head}
            <div class="pool-offer">${icon('search')}<span>See which folders, apps and restore points take up room${report.state === 'error' ? `. The last check did not work: ${escapeHtml(report.error || 'unknown error')}` : '.'}</span>
                <button type="button" class="btn-secondary" onclick="startPoolSpaceCheck('${id}')">Check what uses space</button></div>`;
    }

    const folders = result.folders || [];
    const apps = result.apps || [];
    const snaps = result.restore_points || { count: 0, exclusive_bytes: 0, items: [] };
    const appsTotal = apps.reduce((sum, a) => sum + a.bytes, 0);
    const max = Math.max(1, ...folders.map((f) => f.bytes), appsTotal, snaps.exclusive_bytes);
    const rows = folders.slice(0, 6).map((f) => spaceRow(f.name, f.bytes, max, 'folder'));
    const restFolders = folders.slice(6);
    if (restFolders.length) {
        rows.push(spaceRow(`${restFolders.length} more folder${restFolders.length === 1 ? '' : 's'}`, restFolders.reduce((s, f) => s + f.bytes, 0), max, 'folder'));
    }
    if (apps.length) {
        rows.push(spaceRow(`Apps (${apps.length})`, appsTotal, max, 'app',
            `<div class="space-sub">${apps.slice(0, 4).map((a) => `${escapeHtml(a.name)} ${escapeHtml(formatBytes(a.bytes))}`).join(' · ')}</div>`));
    }
    if (snaps.count) {
        const biggest = (snaps.items || []).filter((s) => s.exclusive_bytes > 0).slice(0, 5);
        rows.push(spaceRow(`Restore points (${snaps.count})`, snaps.exclusive_bytes, max, 'snap', `
            <div class="space-sub">Only what changed since is kept, so they take less than a copy would.</div>
            ${biggest.length ? `<details class="space-more"><summary>Free space by deleting restore points</summary>
                <p class="space-sub">Deleting one frees about the size shown. Your current files are not touched.</p>
                ${biggest.map((s) => `
                    <div class="space-snap">
                        <span><strong>${escapeHtml(String(s.source_path || '').split('/').pop() || 'Folder')}</strong> · ${escapeHtml(new Date(s.created_at || '').toLocaleString())}</span>
                        <span class="space-size">${escapeHtml(formatBytes(s.exclusive_bytes))}</span>
                        <button type="button" class="btn-secondary" onclick="deleteRestorePointForSpace('${id}', '${jsArg(s.snapshot_path)}')">Delete</button>
                    </div>`).join('')}
            </details>` : ''}`));
    }
    if (!rows.length) rows.push(`<div class="pool-line">${icon('circle-check')}<span>This pool is empty.</span></div>`);
    return `${head}${rows.join('')}
        <div class="space-foot">Measured ${escapeHtml(spaceAge(report.finished_at))}
            <button type="button" class="btn-secondary btn-quiet" onclick="startPoolSpaceCheck('${id}')">Check again</button></div>`;
}

function paintPoolSpace(poolId) {
    const box = document.getElementById('pool-space');
    if (box && box.dataset.pool === String(poolId)) box.innerHTML = renderPoolSpace(poolId);
}

async function loadPoolSpace(poolId, force) {
    clearTimeout(poolSpaceTimer);
    if (!force && poolSpace[poolId] && poolSpace[poolId].state !== 'running') {
        paintPoolSpace(poolId);
        return;
    }
    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await apiFetch(`${API_BASE}/storage/pools/${encodeURIComponent(poolId)}/space`, { headers: { 'Authorization': token || '' } });
        const data = await response.json().catch(() => ({}));
        poolSpace[poolId] = response.ok ? data : { state: 'error', error: data.error || 'The pool could not be measured.' };
    } catch (_e) {
        poolSpace[poolId] = { state: 'error', error: 'The NAS did not answer.' };
    }
    paintPoolSpace(poolId);
    if (poolSpace[poolId].state === 'running' && openedPoolId === String(poolId)) {
        poolSpaceTimer = setTimeout(() => loadPoolSpace(poolId, true), 5000);
    }
}

async function startPoolSpaceCheck(poolId) {
    try {
        poolSpace[poolId] = await poolPost(poolId, 'space', {});
    } catch (error) {
        showError(error.message);
        return;
    }
    paintPoolSpace(poolId);
    loadPoolSpace(poolId, true);
}

async function deleteRestorePointForSpace(poolId, snapshotPath) {
    if (!await showConfirm('Delete this restore point?\nYour current files stay as they are. You can no longer go back to this point in time.', { confirmLabel: 'Delete' })) return;
    try {
        const token = localStorage.getItem('alvaos_token');
        const response = await apiFetch(`${API_BASE}/backup/snapshots`, {
            method: 'DELETE',
            headers: { 'Authorization': token || '', 'Content-Type': 'application/json' },
            body: JSON.stringify({ snapshot_path: snapshotPath }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || 'The restore point was not deleted.');
    } catch (error) {
        showError(error.message);
        return;
    }
    const report = poolSpace[poolId];
    const snaps = report?.result?.restore_points;
    if (snaps) {
        const gone = (snaps.items || []).find((s) => s.snapshot_path === snapshotPath);
        snaps.items = (snaps.items || []).filter((s) => s.snapshot_path !== snapshotPath);
        snaps.count = Math.max(0, snaps.count - 1);
        snaps.exclusive_bytes = Math.max(0, snaps.exclusive_bytes - (gone?.exclusive_bytes || 0));
    }
    showSuccess('Restore point deleted. Btrfs frees the space in the background.');
    paintPoolSpace(poolId);
}
