// Get single files back from a restore point: browse what a folder looked
// like then, see what is gone since, and copy it back. Nothing is overwritten.
(function () {
    const icon = (name) => (window.alvaIcon ? window.alvaIcon(name, '', 'aria-hidden="true"') : '');

    function folderName(path) {
        const parts = String(path || '').split('/').filter(Boolean);
        return parts[parts.length - 1] || path || 'Folder';
    }

    async function openSnapshotBrowser(snapshotPath, sourcePath, createdAt) {
        let currentPath = '';
        let onlyGone = false;
        let lastEntries = [];

        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal-content snap-browser" role="dialog" aria-modal="true" aria-labelledby="snap-browser-title">
                <div class="modal-title"><span id="snap-browser-title">Get files back</span>
                    <button type="button" class="modal-close-x" aria-label="Close">&times;</button></div>
                <p class="snap-browser-sub">${backupEscapeHtml(folderName(sourcePath))} as it was on ${backupEscapeHtml(backupFormatDate(createdAt))}</p>
                <div class="snap-browser-bar">
                    <nav class="snap-crumbs" aria-label="Folder"></nav>
                    <label class="snap-gone-toggle"><input type="checkbox" id="snap-only-gone"> Only what is gone</label>
                </div>
                <div class="snap-browser-list" aria-live="polite"></div>
                <p class="snap-browser-note">Restored files go back to where they were. If a file with the same name is there now, the old one is added beside it with "(restored ...)" in its name.</p>
            </div>`;
        document.body.appendChild(overlay);
        const close = () => overlay.remove();
        overlay.querySelector('.modal-close-x').onclick = close;
        if (window.attachModalDismiss) window.attachModalDismiss(overlay, close);

        const list = overlay.querySelector('.snap-browser-list');
        const crumbs = overlay.querySelector('.snap-crumbs');

        function renderCrumbs() {
            const parts = currentPath ? currentPath.split('/') : [];
            const links = [`<button type="button" class="snap-crumb" data-path="">${backupEscapeHtml(folderName(sourcePath))}</button>`];
            parts.forEach((part, i) => {
                links.push(`<span aria-hidden="true">&rsaquo;</span><button type="button" class="snap-crumb" data-path="${backupEscapeHtml(parts.slice(0, i + 1).join('/'))}">${backupEscapeHtml(part)}</button>`);
            });
            crumbs.innerHTML = links.join('');
        }

        function renderEntries() {
            const shown = onlyGone ? lastEntries.filter((e) => e.exists_now === false || e.type === 'folder') : lastEntries;
            if (!shown.length) {
                list.innerHTML = `<div class="snap-browser-empty">${onlyGone ? 'Nothing here is gone since then.' : 'This folder was empty.'}</div>`;
                return;
            }
            list.innerHTML = shown.map((e) => {
                const isFolder = e.type === 'folder';
                const meta = [isFolder ? 'Folder' : backupFormatBytes(e.size_bytes), backupFormatDate(e.modified_at)];
                const rel = currentPath ? `${currentPath}/${e.name}` : e.name;
                return `
                    <div class="snap-file${e.exists_now === false ? ' gone' : ''}">
                        <span class="snap-file-ic">${icon(isFolder ? 'folder' : 'file')}</span>
                        <div class="snap-file-text">
                            ${isFolder
                                ? `<button type="button" class="snap-open" data-path="${backupEscapeHtml(rel)}">${backupEscapeHtml(e.name)}</button>`
                                : `<span class="snap-name">${backupEscapeHtml(e.name)}</span>`}
                            <span class="snap-file-meta">${backupEscapeHtml(meta.join(' · '))}${e.exists_now === false ? ' · <strong>gone since</strong>' : ''}</span>
                        </div>
                        <button type="button" class="btn-secondary snap-get" data-path="${backupEscapeHtml(rel)}" data-name="${backupEscapeHtml(e.name)}">Restore</button>
                    </div>`;
            }).join('');
        }

        async function load(path) {
            list.innerHTML = '<div class="snap-browser-empty">Loading...</div>';
            const response = await backupApi(`/backup/snapshots/browse?snapshot_path=${encodeURIComponent(snapshotPath)}&path=${encodeURIComponent(path)}`);
            const data = await backupReadJson(response);
            if (!response || !response.ok || !data) {
                list.innerHTML = `<div class="snap-browser-empty">${backupEscapeHtml(data?.error || 'This restore point could not be read.')}</div>`;
                return;
            }
            currentPath = data.path || '';
            lastEntries = Array.isArray(data.entries) ? data.entries : [];
            renderCrumbs();
            renderEntries();
        }

        overlay.addEventListener('click', async (event) => {
            const nav = event.target.closest('.snap-open, .snap-crumb');
            if (nav) {
                load(nav.dataset.path || '');
                return;
            }
            const get = event.target.closest('.snap-get');
            if (!get) return;
            get.disabled = true;
            get.textContent = 'Restoring...';
            const response = await backupApi('/backup/snapshots/restore-item', {
                method: 'POST',
                json: { snapshot_path: snapshotPath, path: get.dataset.path },
            });
            const data = await backupReadJson(response);
            if (!response || !response.ok || !data?.success) {
                backupNotify(data?.error || 'It was not restored.', 'error');
                get.disabled = false;
                get.textContent = 'Restore';
                return;
            }
            const where = [folderName(sourcePath), ...(currentPath ? currentPath.split('/') : [])].join(' › ');
            const restoredName = String(data.restored_as || '').split('/').pop();
            backupNotify(data.renamed
                ? `Restored beside the current one as "${restoredName}" in ${where}.`
                : `"${get.dataset.name}" is back in ${where}.`, 'success');
            load(currentPath);
        });

        overlay.querySelector('#snap-only-gone').addEventListener('change', (event) => {
            onlyGone = event.target.checked;
            renderEntries();
        });

        load('');
    }

    window.openSnapshotBrowser = openSnapshotBrowser;
})();
