# Roadmap

AlvaOS is a calm NAS OS for storage, apps and offsite backup: extremely simple by
default, powerful when needed, light enough for old hardware.

This is a prioritized to-do list, not a promise. Top to bottom is the order of work.
Done work moves to `BACKLOG.md`.

Every page follows the same pattern as the reworked Dashboard, Storage and Backup pages:
- The first screen answers "is it fine, and what do I do next?" in plain words.
- Only the actions that make sense right now are shown.
- Details and power options are one click away, never forced into view.
- It works on a phone, and it is checked in a browser at desktop and phone width.

---

## Now

### 1. Remaining pages in the new pattern
- **Apps:** ship real app icons in `apps/icons/` (SVG, made or licensed for AlvaOS).
- **Updates follow-up:** roll back automatically when `dpkg -i` fails or no service comes back after an update (`apply_update.sh` only reports the error today), and keep the package of the installed version in the cache even when it came from the installer or a USB stick, so "Go back" is always offered.

---

## Next

### 2. AlvaOS Files: a native, lightweight file cloud
Everything people use Nextcloud for at home, built into AlvaOS and working on the same folders as the shares. No separate app, no database server.
- **Web file browser** on the shared folders: browse, upload (drag and drop, large files resumable), download, rename, move, delete, and folder downloads as zip.
- Every person from the People tab signs in with the same password and sees exactly the shares they can open, with the same read/edit rights.
- **Preview** of images, video, audio, PDF and text; photo thumbnails generated in the background.
- **Share links** for single files or folders: optional password, expiry date, read-only or upload-only ("drop box"). Visible and revocable in one list.
- **Phone access** through the web UI (installable as a PWA), plus WebDAV so native file apps and desktop clients can connect.
- **Trash** per share with automatic cleanup, and "Previous versions" of a file in Files (the backend for this exists: `browse_snapshot`/`restore_item`, used by "Get files" on the Backup page).
- **Search** by name, later by content.
- **Remote access** without port forwarding, over the existing WireGuard, with a simple "add this device" flow and QR code.
- Lightweight: a few MB of code, no PHP and no extra database, and it does not run when nobody uses it.

### 3. Storage follow-ups
- Mount a redundant pool degraded when a disk is missing at boot, with a clear warning and a Replace button. This needs a decision on safety first.
- Quotas per share.

### 4. AI
- AI chatbot via ollama cloud integration or any other api.
- you can toggle it completely off or on
- new page in setup
- it can do everything that a person can in the ui (permissions can be changed from read only to approve to NOT RECOMMENDED everything)

---

## Always

### Security
- Keep the privilege helper strict: every new privileged command gets a policy rule and tests.
- Validate everything that ends up in config files (`smb.conf`, `/etc/exports`, WireGuard, compose files).
- Sessions: sign out sessions that were idle for a long time, and say on the login page when the NAS was last signed in to from somewhere else.
- HTTPS by default on the LAN, with a local certificate and clear instructions for trusting it.
- Regular dependency updates and a short security review for every PR that touches `priv_policy.py` or auth.

### Lightweight
- Runs well on 2 GB RAM and old CPUs.
- No framework, no build step, no external assets.
- Background work (thumbnails, scrub, backups) runs at low priority.
- Measure idle CPU, memory and disk wake-ups, and keep them low.

### UI and UX
- One visual language across all pages: cards, pills, buttons, dialogs, empty states.
- Plain words instead of technical terms. The technical term appears in the details.
- Every warning links to the page where it is fixed.
- Accessibility: keyboard navigation, focus states, contrast.

### Quality
- Tests for every backend change. Browser checks at desktop and phone width for every UI change.
