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
- All pages follow it (Dashboard, Storage, Files, Apps, Backup, Updates, Settings, sign-in, setup). Keeping the update package of the running version for going back: done.

---

## Next

### 2. AlvaOS Files: a native, lightweight file cloud
Everything people use Nextcloud for at home, built into AlvaOS and working on the same folders as the shares. No separate app, no database server.
- **AlvaOS Files app** (port 8090, HTTPS 9443, built in, turned on under Apps): done.
- **Preview** of images, video, audio, PDF and text; photo thumbnails generated in the background.
- **Share links**: done (read-only or upload-only "drop box", password, expiry, one list).
- **WebDAV** (8091, HTTPS 9444) and **installing Files as an app** (PWA, over HTTPS): done.
- **Trash** per share and **Previous versions** of a file: done in Files.
- **Search**: by name done (folder and below, or all shared folders, as the person). Later: by content.
- **Remote access** without port forwarding, over the existing WireGuard, with a simple "add this device" flow and QR code.
- Lightweight: a few MB of code, no PHP and no extra database, and it does not run when nobody uses it.

### 3. Storage follow-ups
- **Quotas per share and personal folders: done** (Btrfs qgroups, only switched on for a pool once a limit is set there). Warning when a folder is nearly full: done. Next: a check on a real pool with many restore points how much slower qgroups make it.

### 4. AI
- **Read-only assistant: done.** Settings › Assistant (off by default; Ollama at home, Ollama Cloud, OpenAI or any OpenAI-compatible API), chat panel in the top bar of every page, looks at the real state through a fixed list of read-only endpoints, secrets stripped.
- **"Suggests, I confirm" level: done** (backup now, data check, restart an app, check services, disk sleep). Next: more actions (update an app, turn on automatic backups, install an AlvaOS update), then "may do everything" (not recommended, clearly marked).
- An optional step in first setup ("Want an assistant?").
- Answers that stream word by word instead of arriving at once.

---

## Always

### Security
- Keep the privilege helper strict: every new privileged command gets a policy rule and tests.
- Validate everything that ends up in config files (`smb.conf`, `/etc/exports`, WireGuard, compose files).
- Sessions: sign out sessions that were idle for a long time, and say on the login page when the NAS was last signed in to from somewhere else.
- HTTPS on the LAN with the NAS's own authority and instructions: done (next to HTTP). Next: an "HTTPS only" switch that sends http:// to https://.
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
