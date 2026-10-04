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

### 2. AlvaOS Hub: one address for everything people use
One place for the whole household: one address, one sign-in, and the apps side by side, instead of one port per app. The admin pages (port 8080) stay separate: the Hub is for everyone, the admin pages are for the owner (admins get a "Manage the NAS" link in the Hub).
- **Part of AlvaOS, not an app.** The Hub ships with AlvaOS, is updated with it (signed, rolls back on its own) and is turned on in the admin pages. Technically it is what the Files server is today (port 8090, HTTPS 9443): the Files server becomes the Hub server, Files its first app.
- **Two kinds of apps, on purpose:**
  - *Hub apps* (ours): Files, Photos, Calendar and Contacts, Chat, Notes. Modules inside the Hub process: the NAS sign-in, the AlvaOS look, they act as the signed-in person through the helper (same folders, same permissions as Files), updated with AlvaOS. Not containers: a container per Hub app would cost memory, need its own logins and get in the way of the permissions.
  - *Store apps* (others): Jellyfin, Immich, OpenWebUI, ... stay Docker containers with their own login and updates, isolated. In the Hub they are tiles that open them; later behind a reverse proxy with names like `jellyfin.alva.home` (certificate from our own authority; name resolution at home is the open part). No iframes and no `/apps/<name>` paths (many apps forbid or break with them).
- **Everything optional.** Each Hub app is a module with a short manifest (name, icon, what it needs). The admin turns each on or off on the Hub page of the admin pages, and chooses per person who sees it. Off means its pages are not there and nothing of it runs. What it needs beyond AlvaOS is installed when it is turned on and can be removed again (Calendar: the Radicale package, run inside the Hub process; Chat: a cloud model key or the Ollama store app). The Hub page lists our apps with their switches; the Apps page keeps the Docker apps.
- **No third-party plugins inside the Hub for now:** their code would run with the Hub's rights, i.e. could read everyone's files. Others' apps stay isolated Docker apps; perhaps later a narrow, defined plugin interface.
- **Standards first, so phones work without our apps:** Calendar and Contacts over CalDAV/CardDAV (Radicale: small, Python, proven), so iPhone, Android and Outlook sync on their own. Chat: a simple page in front of Ollama or a cloud model, reusing the assistant's connection; OpenWebUI stays a store app for power users.
- **Order:** (1) the Hub frame: done (app bar, sign-in, the Hub page in the admin pages with a switch per app and who sees it; Files and Photos as the first apps; a start page and a Hub icon of its own are still open); (2) Calendar and Contacts; (3) Chat; (4) store apps as tiles, later the reverse proxy; (5) native apps.
- **Native apps** for phone, PC and Mac talk to the Hub: one address, one sign-in, every Hub app inside, plus the tunnel (remote access) and file sync. A new Hub app shows up in them by itself; nothing is built per platform twice. The Hub opens directly in the app's window, not on another port.

### 2a. AlvaOS Files: a native, lightweight file cloud
Everything people use Nextcloud for at home, built into AlvaOS and working on the same folders as the shares. No separate app, no database server. Lightweight: a few MB of code, no PHP and no extra database, and it does not run when nobody uses it. Stays in Python + plain JavaScript (same helper, tests and packaging as the rest; the disk and network are the limit, not the language); a part that turns out too slow in real use is replaced on its own.
- **Done:** the Files app (port 8090, HTTPS 9443, turned on under Apps); grid and list, thumbnails, viewer for images, video, audio, PDF and text; Photos timeline by month; search by name (this folder and below, or all shared folders); copy and move (also by dragging), ZIP download, resumable uploads of any size; trash per share (also for files deleted over SMB) and previous versions of a file; share links (read-only or upload-only drop box, password, expiry, limits); WebDAV (8091, HTTPS 9444); installable as an app (PWA); keyboard shortcuts.
- **Simple, noticed by everyone (next, in this order):**
  1. Sort by name, date, size and type (today only by name, folders first).
  2. A start page with "Recent" and favourites.
  3. Upload whole folders by drag and drop (check what works today).
  4. "You use 42 of 100 GB" in Files (the person's own space limit).
  5. Edit text files (notes, lists, .txt/.md) in the browser.
  6. A notification when someone uploads into a drop box.
  7. Photos: the date from the photo itself (EXIF) instead of the file date; video thumbnails.
  8. Share a folder with another person on the NAS without a public link.
- **Powerful when needed:**
  9. Open and edit Office documents with the EuroOffice app from the catalog (WOPI).
  10. Search filters (type, date, size); later search by content (text in PDFs and documents).
  11. Activity: who changed, deleted or shared what, and when.
  12. More control over links: download limit, how often opened, all my links with "end all".
  13. Unpack ZIP files, folder sizes, rename many files at once, properties (checksum, exact dates).
  14. File sync for PC and phone (camera upload, folders kept in sync): with the native apps; WebDAV is the stopgap.
- **Not in Files on purpose:** faces, maps and albums (Immich does that as an app); chat, calendar and contacts are Hub apps of their own (see 2), not part of Files.
- **Remote access: first step done.** Settings › Remote access: WireGuard with "add this device", QR code for phones, a file for computers, last connected, remove. Needs one forwarded UDP port. UPnP (the router opens the port by itself), a CGNAT warning and DuckDNS: done. Next: optional "also reach the home network"; without any port only with a relay (Tailscale/Headscale or an own one).

### 3. Storage follow-ups
- **Quotas per share and personal folders: done** (Btrfs qgroups, only switched on for a pool once a limit is set there). Warning when a folder is nearly full: done. Next: a check on a real pool with many restore points how much slower qgroups make it.

### 3a. Graphics cards
- **Settings › Graphics: done** (detection, driver and firmware install, restart, Secure Boot note). Next: give apps the card with one switch (Jellyfin, Immich: `/dev/dri`; Ollama), and NVIDIA's container toolkit for NVIDIA cards in apps.

### 3b. Backup to a USB disk
- **Done (needs a test on real Btrfs).** Backup › Backup disk: pick a pool on a USB disk, AlvaOS copies the newest restore point of every source there (Btrfs send/receive, incremental) whenever the disk is connected, keeps 30 per source, "Safely remove" unmounts it. Copies show up in the restore points ("On the backup disk") and Files › Previous versions ("Get files"); a warning appears after a week without a copy. A fresh USB disk becomes the backup disk in one step (erase after typing ERASE): done.

### 4. AI
- **Read-only assistant: done.** Settings › Assistant (off by default; Ollama at home, Ollama Cloud, OpenAI or any OpenAI-compatible API), chat panel in the top bar of every page, looks at the real state through a fixed list of read-only endpoints, secrets stripped.
- **"Suggests, I confirm" level: done** (backup now, data check, restart an app, check services, disk sleep, automatic backups, update an app, install an AlvaOS update, make a shared folder, give/take access, space limits, turn on Files, copy to the backup disk). Reads logs (masked) and SMART details; links pages; understands tool calls written as text by small local models. Next: "may do everything" (not recommended, clearly marked); add people (needs a safe way to hand over a first password).
- Mentioned at the end of first setup, with Files and HTTPS: done (no extra step).
- Answers that stream word by word instead of arriving at once.

---

## Always

### Security
- Keep the privilege helper strict: every new privileged command gets a policy rule and tests.
- Validate everything that ends up in config files (`smb.conf`, `/etc/exports`, WireGuard, compose files).
- Sessions: idle sign-out (8 h) and "last sign-in / wrong passwords since" after signing in: done.
- HTTPS on the LAN with the NAS's own authority and instructions: done (next to HTTP). "HTTPS only" switch: done.
- Regular dependency updates and a short security review for every PR that touches `priv_policy.py` or auth.

### Languages
- Python for everything tied closely to the NAS (privilege helper, storage, shares, sign-in): one base, one set of tests and packages.
- Go for parts that run on their own, for long, and must be fast, or run on other devices: file sync, the native apps' core, the tunnel. One binary, no dependencies. Phone UIs in Kotlin/Swift or Flutter.
- Where a proven standard project exists (calendar, contacts, AI models), use it instead of writing our own.
- Decided per new system, not by rewriting what works.

### Lightweight
- Runs well on 2 GB RAM and old CPUs.
- No framework, no build step, no external assets.
- Background work runs at low priority: scrub, thumbnails and Buddy Backup transfers: done.
- Measure idle CPU, memory and disk wake-ups, and keep them low.

### UI and UX
- One visual language across all pages: cards, pills, buttons, dialogs, empty states.
- Plain words instead of technical terms. The technical term appears in the details.
- Every warning links to the page where it is fixed.
- Accessibility: keyboard navigation, focus states, contrast. axe-core pass over all pages: done, no violations.

### Quality
- Tests for every backend change. Browser checks at desktop and phone width for every UI change.
