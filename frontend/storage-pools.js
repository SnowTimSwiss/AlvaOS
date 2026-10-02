// AlvaOS Storage: pools overview and pool detail.
//
// The overview answers "is my storage fine and how full is it?" at a glance:
// one card per pool with usage, protection and its disks. Clicking a pool opens
// its detail (storage.html#pool=<id>): usage first, then the disks with Replace
// and Add disk, the data check, folders, and the technical details folded away.
// Uses helpers from app.js (escapeHtml, jsArg, renderLoadFailure) and
// storage.js (apiFetch, showStorageTab, disk helpers, dialogs).

let storagePoolsCache = [];
let openedPoolId = '';
const poolActivity = {};
let poolActivityTimer = null;
let healthSettings = { scrub: 'monthly', start_hour: 3 };

const PROTECTION = {
    single: { failures: 0, text: 'No protection: if a disk fails, the data on it is lost.' },
    raid0: { failures: 0, text: 'Striped for speed, no protection: if any disk fails, the whole pool is lost.' },
    dup: { failures: 0, text: 'Two copies on one disk: protects against bad sectors, not a failed disk.' },
    raid1: { failures: 1, text: 'Mirrored: every file is on two disks, so one disk can fail.' },
    raid1c3: { failures: 2, text: 'Three copies: two disks can fail at the same time.' },
    raid1c4: { failures: 3, text: 'Four copies: three disks can fail at the same time.' },
    raid10: { failures: 1, text: 'Striped mirrors: one disk can fail.' },
    raid5: { failures: 1, text: 'Parity: one disk can fail.' },
    raid6: { failures: 2, text: 'Double parity: two disks can fail at the same time.' }
};

function poolProtection(pool) {
    return PROTECTION[String(pool.raid_level || 'single').toLowerCase()] || { failures: 0, text: `Profile ${pool.raid_level}.` };
}

function formatBytes(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n < 0) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let value = n;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) {
        value /= 1024;
        i += 1;
    }
    return `${value >= 100 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

function usageBar(percent, big) {
    const p = Math.max(0, Math.min(100, Number(percent) || 0));
    const tone = p >= 90 ? 'bad' : p >= 80 ? 'warn' : '';
    return `<div class="usage-bar${big ? ' big' : ''}" role="progressbar" aria-valuenow="${p}" aria-valuemin="0" aria-valuemax="100"><span class="${tone}" style="width: ${p}%"></span></div>`;
}

function icon(name) {
    return window.alvaIcon ? window.alvaIcon(name, '', 'aria-hidden="true"') : '';
}

// The inventory entry (model, SMART) for a pool member's device path.
function diskForMember(member) {
    const base = String(member.path || '').replace(/^\/dev\//, '').replace(/(nvme\d+n\d+|mmcblk\d+)p\d+$/, '$1').replace(/^([a-z]+)\d+$/, '$1');
    return storageDisksCache.find((d) => d.name === base) || null;
}

function poolMembers(pool) {
    if (Array.isArray(pool.members) && pool.members.length) return pool.members;
    return (pool.devices || []).map((path, i) => ({ devid: i + 1, path, size: '', missing: false }));
}

function memberErrors(pool, member) {
    const stats = (poolActivity[pool.id] || {}).device_stats || {};
    const counters = stats[member.path] || stats[`devid:${member.devid}`] || {};
    return Object.values(counters).reduce((sum, n) => sum + (Number(n) || 0), 0);
}

// One word for the state of the pool, plus what needs doing, if anything.
function poolState(pool) {
    const members = poolMembers(pool);
    const missing = Math.max(members.filter((m) => m.missing).length, Number(pool.missing_count || 0));
    const failing = members.filter((m) => !m.missing && ((diskForMember(m) || {}).smart_status === 'failed' || memberErrors(pool, m) > 0));
    if (pool.is_managed === false && !pool.is_system_pool) {
        return { pill: '<span class="pill warn">Not imported</span>', tone: 'notice', line: { tone: 'warn', text: 'Found on disks connected to this NAS. Import it to use its data.' } };
    }
    if (pool.status === 'degraded' || missing) {
        const redundant = poolProtection(pool).failures > 0;
        return {
            pill: '<span class="pill bad">Degraded</span>',
            tone: 'attention',
            line: {
                tone: 'bad',
                text: redundant
                    ? `${missing || 'A'} disk${missing > 1 ? 's are' : ' is'} missing. The pool still works, but is no longer protected. Replace the disk.`
                    : 'A disk is missing and this pool has no protection. Data on that disk is gone; restore it from a backup.'
            },
            needsReplace: redundant
        };
    }
    if (failing.length) {
        return {
            pill: '<span class="pill warn">Check disks</span>',
            tone: 'notice',
            line: { tone: 'warn', text: `${failing.map((m) => m.path).join(', ')} reports errors. Replace it before it fails.` },
            needsReplace: true
        };
    }
    return { pill: pool.is_system_pool ? '<span class="pill">System</span>' : '<span class="pill ok">Healthy</span>', tone: '', line: null };
}

function usageSummary(pool) {
    if (pool.total_bytes) {
        return {
            percent: pool.used_percent,
            used: formatBytes(pool.used_bytes),
            free: formatBytes(pool.free_bytes),
            total: formatBytes(pool.total_bytes)
        };
    }
    return { percent: null, used: pool.used_size || 'unknown', free: pool.free_size || '', total: pool.total_size || 'unknown' };
}

function activityLine(pool) {
    const act = poolActivity[pool.id];
    if (!act) return '';
    const job = act.replace && act.replace.state === 'running' ? { label: 'Replacing a disk', percent: act.replace.percent }
        : act.balance && ['running', 'paused'].includes(act.balance.state) ? { label: act.balance.state === 'paused' ? 'Spreading data over the disks (paused)' : 'Spreading data over the disks', percent: act.balance.percent }
        : act.scrub && act.scrub.state === 'running' ? { label: 'Checking data', percent: act.scrub.percent }
        : null;
    if (!job) return '';
    const pct = job.percent === null || job.percent === undefined ? '' : ` · ${Math.round(job.percent)}%`;
    return `<div class="progress-line">${escapeHtml(job.label + pct)}${job.percent !== null && job.percent !== undefined ? usageBar(job.percent) : ''}</div>`;
}

function isJobRunning(act) {
    return !!act && ((act.replace && act.replace.state === 'running')
        || (act.balance && ['running', 'paused'].includes(act.balance.state))
        || (act.scrub && act.scrub.state === 'running'));
}

// ── Loading ──────────────────────────────────────────────────────────────────

async function loadPools() {
    const container = document.getElementById('pools-container');
    if (!storagePoolsCache.length) {
        container.innerHTML = '<div style="text-align: center; padding: 2rem;"><div class="spinner"></div><p style="color: var(--text-secondary); margin-top: 1rem;">Reading pools...</p></div>';
    }
    try {
        const token = localStorage.getItem('alvaos_token');
        const headers = { 'Authorization': token || '' };
        const [response, disksResponse] = await Promise.all([
            apiFetch(`${API_BASE}/storage/pools`, { headers }),
            apiFetch(`${API_BASE}/storage/disks`, { headers }).catch(() => null)
        ]);
        if (!response.ok) throw new Error('Failed to load pools');
        const data = await response.json();
        const disksData = disksResponse && disksResponse.ok ? await disksResponse.json() : {};
        if (Array.isArray(disksData.disks)) storageDisksCache = disksData.disks;
        const ignored = getIgnoredDetectedPools();
        storagePoolsCache = (Array.isArray(data.pools) ? data.pools : []).filter((pool) => (
            pool && (pool.is_managed !== false || !ignored.has(String(pool.id || '')))
        ));
        await loadHealthSettings();
        renderPools();
        await loadPoolActivity();
    } catch (error) {
        console.error('Error loading pools:', error);
        showPoolsView();
        renderLoadFailure(container, {
            title: 'Could not read the storage pools',
            detail: 'The storage service did not answer. It may still be starting up after a restart or update. Your data is not affected by this.',
            onRetry: loadPools
        });
    }
}

// Replace, balance and scrub status for every mounted, managed pool. Polls
// while a job runs, so progress moves without a reload.
async function loadPoolActivity() {
    clearTimeout(poolActivityTimer);
    const token = localStorage.getItem('alvaos_token');
    const pools = storagePoolsCache.filter((p) => p.is_managed !== false && !p.is_system_pool && p.total_bytes);
    await Promise.all(pools.map(async (pool) => {
        try {
            const response = await apiFetch(`${API_BASE}/storage/pools/${encodeURIComponent(pool.id)}/activity`, {
                headers: { 'Authorization': token || '' }
            });
            if (response.ok) poolActivity[pool.id] = await response.json();
        } catch {
            // Activity is extra information; the pool view works without it.
        }
    }));
    renderPools();
    if (pools.some((p) => isJobRunning(poolActivity[p.id])) && document.getElementById('tab-pools').classList.contains('active')) {
        poolActivityTimer = setTimeout(loadPoolActivity, 10000);
    }
}

function showPoolsView() {
    document.getElementById('pools-overview').hidden = false;
    document.getElementById('pool-detail').hidden = true;
}

function renderPools() {
    const pool = openedPoolId ? storagePoolsCache.find((p) => String(p.id) === openedPoolId && (p.is_managed !== false || p.is_system_pool)) : null;
    if (pool) {
        document.getElementById('pools-overview').hidden = true;
        const detail = document.getElementById('pool-detail');
        detail.hidden = false;
        detail.innerHTML = renderPoolDetail(pool);
        return;
    }
    showPoolsView();
    displayPools(storagePoolsCache);
}

function openPool(poolId) {
    showStorageTab(`pool=${poolId}`);
    window.scrollTo(0, 0);
}

// ── Overview ─────────────────────────────────────────────────────────────────

function displayPools(pools) {
    const container = document.getElementById('pools-container');
    if (!pools.length) {
        const empty = storageDisksCache.filter((d) => d.usage && d.usage.can_add_to_pool).length;
        container.innerHTML = `
            <div class="pool-section" style="grid-column: 1 / -1; text-align: center; padding: 2.5rem 1.5rem;">
                <div class="pool-name" style="margin-bottom: 6px;">No pool yet</div>
                <p style="color: var(--text-secondary); margin: 0 auto 16px; max-width: 460px;">
                    A pool combines one or more disks into storage for your shares, apps and backups.
                    ${empty ? `${empty} empty disk${empty > 1 ? 's are' : ' is'} ready.` : 'Connect a disk to get started.'}
                </p>
                <button type="button" class="btn-primary" onclick="showCreatePoolDialog()">Create pool</button>
            </div>`;
        return;
    }
    container.innerHTML = pools.map(renderPoolCard).join('');
}

function renderPoolCard(pool) {
    const state = poolState(pool);
    const id = jsArg(pool.id);
    const name = jsArg(pool.name);
    const managed = pool.is_managed !== false || pool.is_system_pool;
    const usage = usageSummary(pool);
    const members = poolMembers(pool);
    const actions = [];
    if (!managed) {
        actions.push(`<button type="button" class="btn-primary" onclick="event.stopPropagation(); importDetectedPool('${id}', '${name}')">Import pool</button>`);
        actions.push(`<button type="button" class="btn-secondary btn-quiet" onclick="event.stopPropagation(); ignoreDetectedPool('${id}')">Ignore</button>`);
    } else {
        if (state.needsReplace && !pool.is_system_pool) {
            actions.push(`<button type="button" class="btn-primary" onclick="event.stopPropagation(); showReplaceDiskDialog('${id}')">Replace disk</button>`);
        }
        if (!pool.is_system_pool || poolProtection(pool).failures > 0) {
            actions.push(`<button type="button" class="btn-secondary" onclick="event.stopPropagation(); showExpandPoolDialog('${id}', '${name}')">${pool.is_system_pool && state.needsReplace ? 'Replace mirror disk' : 'Add disk'}</button>`);
        }
        actions.push(`<button type="button" class="btn-secondary btn-quiet" onclick="event.stopPropagation(); openPool('${id}')">Details ${icon('chevron-right')}</button>`);
    }
    return `
        <div class="pool-card ${state.tone}" role="link" tabindex="0" data-pool="${escapeHtml(pool.id)}"
            onclick="${managed ? `openPool('${id}')` : ''}" onkeydown="if (event.key === 'Enter' && event.target === this) this.click()">
            <div class="pool-head">
                <span class="disk-row-icon" style="grid-row: auto;">${icon(pool.is_system_pool ? 'server' : 'database')}</span>
                <span class="pool-name">${escapeHtml(pool.name)}</span>
                ${state.pill}
            </div>
            ${managed ? `
                <div>
                    ${usage.percent !== null ? usageBar(usage.percent) : ''}
                    <div class="usage-text">
                        <span><strong>${escapeHtml(usage.used)}</strong> of ${escapeHtml(usage.total)} used</span>
                        ${usage.free ? `<span>${escapeHtml(usage.free)} free</span>` : ''}
                    </div>
                </div>` : ''}
            ${state.line ? `<div class="pool-line ${state.line.tone}">${icon('triangle-alert')}<span>${escapeHtml(state.line.text)}</span></div>`
                : `<div class="pool-line">${icon('shield-check')}<span>${escapeHtml(poolProtection(pool).text)}</span></div>`}
            ${activityLine(pool)}
            <div class="member-chips">${members.map((m) => memberChip(pool, m)).join('')}</div>
            <div class="pool-actions">${actions.join('')}</div>
        </div>`;
}

function memberChip(pool, member) {
    if (member.missing) return `<span class="member-chip missing"><span class="dot bad"></span>missing disk</span>`;
    const disk = diskForMember(member) || {};
    const bad = disk.smart_status === 'failed' || memberErrors(pool, member) > 0;
    const dot = bad ? 'bad' : disk.smart_status === 'healthy' ? 'ok' : '';
    return `<span class="member-chip" title="${escapeHtml(disk.model || '')}"><span class="dot ${dot}"></span>${escapeHtml(String(member.path).replace('/dev/', ''))}${disk.size ? ` · ${escapeHtml(disk.size)}` : ''}</span>`;
}

// ── Detail ───────────────────────────────────────────────────────────────────

function renderPoolDetail(pool) {
    const state = poolState(pool);
    const id = jsArg(pool.id);
    const name = jsArg(pool.name);
    const usage = usageSummary(pool);
    const act = poolActivity[pool.id] || {};
    const protection = poolProtection(pool);
    const system = !!pool.is_system_pool;
    const running = isJobRunning(act);

    const headActions = [];
    if (!system) {
        headActions.push(`<button type="button" class="btn-secondary" onclick="showExpandPoolDialog('${id}', '${name}')">${icon('plus')} Add disk</button>`);
        headActions.push(`<button type="button" class="btn-secondary" onclick="startScrub('${id}')" ${running ? 'disabled title="Wait until the current job is done"' : ''}>${icon('shield-check')} Check data</button>`);
    }

    return `
        <button type="button" class="pool-back" onclick="showStorageTab('pools')">← All pools</button>
        <div class="pool-detail-head">
            <h2>${escapeHtml(pool.name)}</h2>
            ${state.pill}
            <div class="pool-actions">${headActions.join('')}</div>
        </div>

        ${state.line ? `<div class="pool-section"><div class="pool-line ${state.line.tone}">${icon('triangle-alert')}<span>${escapeHtml(state.line.text)}</span></div></div>` : ''}

        <section class="pool-section">
            <h3>Usage</h3>
            ${usage.percent !== null ? usageBar(usage.percent, true) : ''}
            <div class="usage-stats">
                <div><div class="k">Used</div><div class="v">${escapeHtml(usage.used)}${usage.percent !== null ? ` <span style="font-size: 0.8rem; color: var(--text-secondary); font-weight: 400;">${Math.round(usage.percent)}%</span>` : ''}</div></div>
                <div><div class="k">Free</div><div class="v">${escapeHtml(usage.free || '–')}</div></div>
                <div><div class="k">Size</div><div class="v">${escapeHtml(usage.total)}</div></div>
            </div>
            <div class="pool-line" style="margin-top: 14px;">${icon('shield-check')}<span>${escapeHtml(protection.text)}${protection.failures === 0 && !system ? ' Add a second disk to mirror it.' : ''}</span></div>
        </section>

        ${system ? '' : renderActivitySection(pool, act)}

        <section class="pool-section">
            <h3>Disks <span style="text-transform: none; letter-spacing: 0; font-weight: 400;">· ${poolMembers(pool).length}</span></h3>
            ${poolMembers(pool).map((m) => renderMemberRow(pool, m, running)).join('')}
        </section>

        ${system ? '' : `
        <section class="pool-section">
            <h3>Folders <span class="pool-actions"><button type="button" class="btn-secondary" onclick="manageSubvolumes('${id}')">${icon('folder-open')} Manage folders</button></span></h3>
            <div class="pool-line">${icon('folder')}<span>Folders (Btrfs subvolumes) can be shared on the network and backed up on their own. Create shares on the Shares tab.</span></div>
        </section>`}

        <details class="sysinfo">
            <summary>${icon('settings-2')} Technical details <span class="chev">${icon('chevron-down')}</span></summary>
            <div class="kv-grid">
                <div><div class="k">Btrfs profile</div><div class="v">${escapeHtml(pool.raid_level || 'single')}</div></div>
                <div><div class="k">Mounted at</div><div class="v">${escapeHtml(pool.mount_point || '–')}</div></div>
                <div><div class="k">UUID</div><div class="v">${escapeHtml(pool.uuid || pool.id)}</div></div>
                <div style="grid-column: 1 / -1;"><div class="k">Members</div><div class="v">${poolMembers(pool).map((m) => escapeHtml(`#${m.devid} ${m.path || 'missing'}${m.size ? ` (${m.size}, ${m.used} used)` : ''}`)).join('<br>')}</div></div>
                ${renderErrorCounters(act)}
            </div>
            ${system ? '' : `
            <div class="danger-zone">
                <span>Take this pool out of AlvaOS. You choose whether its data stays on the disks.</span>
                <button type="button" class="btn-secondary btn-erase" onclick="deletePool('${id}', '${name}')">Remove pool</button>
            </div>`}
        </details>`;
}

function renderActivitySection(pool, act) {
    const id = jsArg(pool.id);
    const rows = [];
    if (act.replace && act.replace.state === 'running') {
        rows.push(progressRow('Replacing a disk', act.replace.percent, 'The pool stays usable. Do not unplug any disk until this is done.'));
    } else if (act.replace && act.replace.state === 'finished' && act.replace.errors) {
        rows.push(`<div class="pool-line warn">${icon('triangle-alert')}<span>The last disk replacement finished with ${act.replace.errors} error(s). Run a data check.</span></div>`);
    }
    if (act.balance && ['running', 'paused'].includes(act.balance.state)) {
        rows.push(progressRow(act.balance.state === 'paused' ? 'Spreading data over the disks (paused)' : 'Spreading data over the disks', act.balance.percent, 'Runs after adding a disk, so the new disk is used and protected.'));
    }
    const scrub = act.scrub || {};
    if (scrub.state === 'running') {
        rows.push(progressRow('Checking data', scrub.percent, scrub.time_left ? `About ${scrub.time_left} left.` : 'Every file is read and compared with its checksum.')
            + `<div class="pool-actions" style="margin-top: 8px;"><button type="button" class="btn-secondary btn-quiet" onclick="cancelScrub('${id}')">Stop check</button></div>`);
    } else if (scrub.state === 'never') {
        rows.push(`<div class="pool-line">${icon('info')}<span>The data has not been checked yet. A check reads every file and repairs damaged copies from the good one where the pool has protection.</span></div>`);
    } else if (scrub.state) {
        const found = scrub.errors ? `${scrub.errors} problem(s) found${scrub.uncorrectable ? `, ${scrub.uncorrectable} could not be repaired. Restore the affected files from a backup.` : ', all repaired.'}` : 'no problems found.';
        const tone = scrub.uncorrectable ? 'bad' : scrub.errors ? 'warn' : '';
        const verb = scrub.state === 'finished' ? 'Last data check' : `Last data check (${scrub.state})`;
        rows.push(`<div class="pool-line ${tone}">${icon(tone ? 'triangle-alert' : 'circle-check')}<span>${escapeHtml(`${verb}: ${scrub.started || 'unknown date'}, ${found}`)}</span></div>`);
    }
    if (!Object.keys(act).length) {
        rows.push(`<div class="pool-line">${icon('info')}<span>Activity is shown once the pool is mounted.</span></div>`);
    }
    rows.push(renderHealthSchedule());
    return `<section class="pool-section"><h3>Activity</h3>${rows.join('<div style="height: 12px;"></div>')}</section>`;
}

// How often every pool gets a data check (one setting for all pools).
async function loadHealthSettings() {
    try {
        const response = await apiFetch(`${API_BASE}/storage/health-checks`, {
            headers: { 'Authorization': localStorage.getItem('alvaos_token') || '' }
        });
        if (response.ok) healthSettings = (await response.json()).settings || healthSettings;
    } catch (_error) {
        // Keep the default; the select still shows it.
    }
}

function renderHealthSchedule() {
    const hour = Number(healthSettings.start_hour ?? 3);
    const when = `${String(hour).padStart(2, '0')}:00`;
    const options = [
        ['monthly', 'Monthly (recommended)'],
        ['weekly', 'Weekly'],
        ['off', 'Off'],
    ].map(([value, label]) => `<option value="${value}"${healthSettings.scrub === value ? ' selected' : ''}>${label}</option>`).join('');
    const hint = healthSettings.scrub === 'off'
        ? 'Damaged data is only found when a file is read. Turn this on unless you check by hand.'
        : `Starts at night around ${when}, one pool at a time, never while a disk is being replaced. Applies to all pools.`;
    return `
        <div class="health-schedule">
            <label for="health-scrub">Check data automatically</label>
            <select id="health-scrub" class="select-input" onchange="saveHealthSchedule(this.value)">${options}</select>
        </div>
        <div class="pool-line" style="font-size: 0.8rem; margin-top: 6px;">${escapeHtml(hint)}</div>`;
}

async function saveHealthSchedule(value) {
    try {
        const response = await apiFetch(`${API_BASE}/storage/health-checks`, {
            method: 'POST',
            headers: { 'Authorization': localStorage.getItem('alvaos_token') || '', 'Content-Type': 'application/json' },
            body: JSON.stringify({ scrub: value })
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || 'The setting was not saved.');
        healthSettings = result.settings || healthSettings;
        showSuccess(value === 'off' ? 'Automatic data checks are off.' : `Data is now checked ${value === 'weekly' ? 'every week' : 'every month'}.`);
    } catch (error) {
        showError(error.message);
    }
    renderPools();
}

function progressRow(label, percent, hint) {
    const pct = percent === null || percent === undefined ? '' : ` · ${Math.round(percent)}%`;
    return `<div class="progress-line">${escapeHtml(label + pct)}${usageBar(percent || 0)}</div><div class="pool-line" style="margin-top: 6px; font-size: 0.8rem;">${escapeHtml(hint)}</div>`;
}

function renderMemberRow(pool, member, running) {
    const id = jsArg(pool.id);
    const system = !!pool.is_system_pool;
    if (member.missing) {
        return `
            <div class="member-row">
                <div>
                    <div class="disk-row-name">Missing disk #${escapeHtml(member.devid)}${member.size ? ` · ${escapeHtml(member.size)}` : ''}</div>
                    <div class="pool-line" style="font-size: 0.82rem;">Disconnected or failed. The pool runs on the remaining disks.</div>
                </div>
                <div class="member-status"><span class="pill bad">Missing</span></div>
                <div class="disk-row-actions">
                    ${system ? `<button type="button" class="btn-primary" onclick="showExpandPoolDialog('${id}', '${jsArg(pool.name)}')">Replace mirror disk</button>`
                        : `<button type="button" class="btn-primary" onclick="showReplaceDiskDialog('${id}', ${Number(member.devid)})" ${running ? 'disabled' : ''}>Replace</button>`}
                </div>
            </div>`;
    }
    const disk = diskForMember(member) || {};
    const errors = memberErrors(pool, member);
    const failing = disk.smart_status === 'failed' || errors > 0;
    const pills = [
        disk.smart_status === 'healthy' ? '<span class="pill ok">Healthy</span>' : disk.smart_status === 'failed' ? '<span class="pill bad">Failing</span>' : '',
        errors ? `<span class="pill warn">${errors} I/O error${errors > 1 ? 's' : ''}</span>` : ''
    ].join('');
    return `
        <div class="member-row">
            <div>
                <div class="disk-row-name">${escapeHtml(disk.name ? diskTitle(disk) : member.path)}</div>
                <div class="disk-row-meta">${escapeHtml(disk.name ? diskMeta(disk) : member.path)}${member.used ? ` · ${escapeHtml(member.used)} used` : ''}</div>
            </div>
            <div class="member-status">${pills}</div>
            <div class="disk-row-actions">
                ${system ? '' : `<button type="button" class="${failing ? 'btn-primary' : 'btn-secondary'}" onclick="showReplaceDiskDialog('${id}', ${Number(member.devid)})" ${running ? 'disabled' : ''}>Replace</button>`}
                ${disk.name ? `<button type="button" class="btn-secondary btn-quiet" onclick="viewDiskDetails('${jsArg(disk.name)}')">Health details</button>` : ''}
            </div>
        </div>`;
}

function renderErrorCounters(act) {
    const stats = act.device_stats || {};
    const devices = Object.keys(stats);
    if (!devices.length) return '';
    return `<div style="grid-column: 1 / -1;"><div class="k">Error counters (btrfs device stats)</div><div class="v">${devices.map((dev) => {
        const counters = Object.entries(stats[dev]).map(([k, v]) => `${k.replace(/_errs$/, '')} ${v}`).join(' · ');
        return escapeHtml(`${dev}: ${counters}`);
    }).join('<br>')}</div></div>`;
}

// ── Actions ──────────────────────────────────────────────────────────────────

async function poolPost(poolId, action, body) {
    const token = localStorage.getItem('alvaos_token');
    const response = await apiFetch(`${API_BASE}/storage/pools/${encodeURIComponent(poolId)}/${action}`, {
        method: 'POST',
        headers: { 'Authorization': token || '', 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {})
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'The request failed.');
    return result;
}

async function startScrub(poolId) {
    const pool = storagePoolsCache.find((p) => String(p.id) === String(poolId)) || {};
    if (!await showConfirm(`Check the data in "${pool.name || poolId}"?\n\nEvery file is read and compared with its checksum. Damaged copies are repaired from the good one where the pool has protection. The pool stays usable, but is slower until the check is done (minutes to hours, depending on size).`, {
        confirmLabel: 'Start check'
    })) return;
    try {
        showSuccess((await poolPost(poolId, 'scrub', {})).message);
    } catch (error) {
        showError(error.message);
    }
    loadPools();
}

async function cancelScrub(poolId) {
    try {
        showSuccess((await poolPost(poolId, 'scrub', { action: 'cancel' })).message);
    } catch (error) {
        showError(error.message);
    }
    loadPools();
}

// Replace one member with an empty disk of at least the same size.
async function showReplaceDiskDialog(poolId, devid) {
    const pool = storagePoolsCache.find((p) => String(p.id) === String(poolId));
    if (!pool) return;
    const members = poolMembers(pool);
    const found = await loadEmptyDisks();
    if (!found) return;

    const failingFirst = members.slice().sort((a, b) => Number(b.missing) - Number(a.missing));
    const initial = devid !== undefined ? devid : (failingFirst[0] || {}).devid;
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="replace-title" style="max-width: 520px;">
            <div class="modal-title" id="replace-title">
                <span>Replace a disk in “${escapeHtml(pool.name)}”</span>
                <button type="button" class="modal-close-x" aria-label="Close">&times;</button>
            </div>
            <div class="modal-body" style="text-align: left;">
                The data is copied onto the new disk while the pool keeps running. A missing disk is rebuilt from its mirror. Afterwards the old disk can be removed.
                <div style="margin-top: 1rem; font-weight: 600;">Disk to replace</div>
                <select id="replace-source" style="width: 100%; margin-top: 6px;">
                    ${members.map((m) => {
                        const disk = diskForMember(m);
                        const label = m.missing ? `Missing disk #${m.devid}${m.size ? ` (${m.size})` : ''}` : `${m.path}${disk ? ` · ${diskTitle(disk)}` : ''}`;
                        return `<option value="${escapeHtml(m.devid)}" data-bytes="${Number(m.size_bytes) || 0}" ${Number(m.devid) === Number(initial) ? 'selected' : ''}>${escapeHtml(label)}</option>`;
                    }).join('')}
                </select>
                <div style="margin-top: 1rem; font-weight: 600;">New disk</div>
                <div id="replace-targets" class="choice-list" style="margin-top: 6px;"></div>
                <div id="replace-hint" style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 8px;"></div>
            </div>
            <div class="modal-actions">
                <button type="button" class="btn-secondary" data-act="cancel">Cancel</button>
                <button type="button" class="btn-primary" data-act="ok">Replace</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    const source = overlay.querySelector('#replace-source');
    const targets = overlay.querySelector('#replace-targets');
    const hint = overlay.querySelector('#replace-hint');
    const okBtn = overlay.querySelector('[data-act="ok"]');
    const renderTargets = () => {
        const need = Number(source.selectedOptions[0]?.dataset.bytes || 0);
        const fits = found.empty.filter((d) => !need || Number(d.size_bytes || 0) >= need);
        const tooSmall = found.empty.length - fits.length;
        targets.innerHTML = fits.map((d, i) => `
            <label class="choice">
                <input type="radio" name="replace-target" value="${escapeHtml(d.path)}" ${i === 0 ? 'checked' : ''}>
                <div><strong>${escapeHtml(diskTitle(d))}</strong><span>${escapeHtml(diskMeta(d))}</span></div>
            </label>`).join('') || '<div class="pool-line warn">No empty disk is large enough. Connect a disk at least as large as the one you replace.</div>';
        hint.textContent = tooSmall ? `${tooSmall} empty disk(s) are smaller than the disk being replaced and are not listed.` : '';
        okBtn.disabled = !fits.length;
    };
    source.addEventListener('change', renderTargets);
    renderTargets();

    const close = () => overlay.remove();
    overlay.querySelector('.modal-close-x').onclick = close;
    overlay.querySelector('[data-act="cancel"]').onclick = close;
    attachModalDismiss(overlay, close);
    okBtn.onclick = async () => {
        const target = overlay.querySelector('input[name="replace-target"]:checked');
        if (!target) return;
        const sourceLabel = source.selectedOptions[0].textContent;
        close();
        if (!await confirmDanger(`Replace ${sourceLabel} with ${target.value}?\n\nEverything on ${target.value} is erased. The copy runs in the background and can take hours.`, null, {
            confirmLabel: 'Replace disk',
            requireCheckbox: `I understand that ${target.value} is erased.`,
            warning: 'Do not unplug any disk of this pool until the replacement is done.',
            details: [{ label: 'Pool', value: pool.name }, { label: 'Replace', value: sourceLabel }, { label: 'With', value: target.value }]
        })) return;
        try {
            showSuccess((await poolPost(poolId, 'replace', { source: Number(source.value), target: target.value })).message);
        } catch (error) {
            showError(error.message);
        }
        loadPools();
    };
}
