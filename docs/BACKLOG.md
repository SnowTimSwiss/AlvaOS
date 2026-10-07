Der NVIDIA-Test läuft wieder. Fehlen die Kernel-Header für den laufenden Kernel, erscheint eine klare Meldung statt endloser Fehlschläge.# Work log

What has been done to AlvaOS, newest first. One short entry per work session
or pull request: what changed, why, and anything the next person should know.

How to add an entry:
- Put it at the top, under the line below, with the date and the PR.
- A few bullet points, not an essay. Name files only when it helps.
- Only work that was done. No wish lists or open to-dos; those live in
  `ROADMAP.md`. The one exception is **Note for next time**: a hint that
  saves the next session time (a trap, a half-finished idea, something that
  needs a real device to verify).
- If a PR is rejected, leave its entry and add "(rejected)" so nobody tries
  the same thing again without a new reason.

---

## 2026-10-06 · Hub: App Store apps as tiles

- Hub page › "Apps from the App Store": every installed app with a page
  (Jellyfin, Immich, Pi-hole, ...) with a switch and "who sees it", off until
  shown. In the Hub an "Apps" button in the bar opens a short list; each
  opens the app on its own port in a new tab, with its own sign-in.
- `hub_apps.store_apps()` reads `apps_state.json` and the catalog (the port
  chosen at install wins over the catalog's); `store_tiles()` for `/api/me`;
  settings under `store` in `hub.json`. Paths are checked before they end up
  in a link.
- Checked in Chromium at desktop and phone width (Hub as a person who sees two
  of three apps, and the Hub page).
- Note for next time: the tiles link to plain http on the app's port. With
  the reverse proxy (roadmap 2, step 4) they become `https://<app>.alva.home`.

## 2026-10-06 · Files: sort by name, date, size or type

- A sort button next to the view switch in Files (Name A–Z/Z–A, newest or
  oldest first, largest or smallest first, type). Folders stay on top. In
  the list the column heads Name, Size and Modified sort too, a second click
  turns the order round. The choice is kept per browser.
- Names sort like people count ("song 9" before "song 10"), as before.
- Checked in Chromium at desktop and phone width against the real Files
  server with a faked folder.

## 2026-10-06 · A UPS on USB (NUT)

- Settings › Power › Battery backup has a **UPS** row next to the laptop
  battery: it finds a UPS on USB (`/sys/bus/usb`, known makers or a device
  that calls itself a UPS), "Set up" installs `nut` and writes the four NUT
  files (standalone: nothing listens on the network), then shows "On mains /
  On battery · 81% · about 22 min". `backend/ups_nut.py`, `api_ups.py`,
  `frontend/settings-ups.js`.
- When to shut down: when the UPS says its battery is low (upsmon does it,
  the default) or after 2, 5, 10 or 20 minutes on battery (the backend asks
  `upsmon -c fsd`). Either way upsmon tells the UPS to switch off, so the NAS
  starts again when the power is back (with the BIOS set to power on).
- The bell notes the power cut, its end and the shutdown; the alerts (and so
  email/Telegram) show "Running on the UPS battery", "The UPS does not
  answer" and "The UPS battery is worn out". The assistant can read
  `/api/v1/system/ups`.
- Privilege helper: content checks for `/etc/nut/{nut.conf,ups.conf,
  upsd.users,upsmon.conf}` that accept only what AlvaOS writes (fixed
  `SHUTDOWNCMD`, no `NOTIFYCMD`, no UPS commands for the monitor account,
  only the usbhid-ups/nutdrv_qx/blazer_usb drivers on `port = auto`), fixed
  `systemctl` lines for the NUT services, and `upsmon -c fsd` only. Tests run
  every file and command through the real policy.
- `.choice` and `.choice-list` moved to `styles.css` (they were only on the
  storage page).
- Checked in Chromium at desktop and phone width with faked answers (no UPS,
  found, on mains, on battery, both dialogs). No real UPS here: `TESTING.md`
  11b.
- Note for next time: the NUT systemd units are from Debian trixie (nut
  2.8.1: `nut-driver-enumerator`, `nut-server`, `nut-monitor`); if a real run
  shows the driver not starting, look there first.

## 2026-10-06 · One dialog for every admin page

- New `openDialog()` in `notifications.js`: frame, title with close button,
  focus moves in and is kept inside, Escape and a click beside it close, focus
  goes back to the button that opened it, the page behind does not scroll.
  `showConfirm`, `showPrompt` and `openSysModal` (Settings) are built on it.
- Moved onto it: password, email, HTTPS, remote-access device, virtual
  machine (new, settings), get files back, replace a disk, share a folder (and
  access, limit, connect), add to which pool, disk health, create pool, add
  disks, remove pool, manage folders, add a person, change a person's
  password, app folders and ports, uninstall an app, restart after a restore,
  feedback. About 460 lines less; most inline styles in the storage dialogs gone.
- Less scrolling: the title and buttons always stay in view, only the fields
  scroll. "More options" and "Mail server" now open in place instead of
  floating: the floating panels covered the Create/Save buttons and ran off a
  phone screen. Short fields side by side (`.modal-row`), buttons in one row
  on a phone.
- Checked in Chromium at desktop and phone width (with faked API answers for
  VMs, email, pools and disks); axe-core finds nothing in the dialogs.
- Note for next time: the app install wizard, container logs and terminal in
  `apps.html` are still static modals in the page; the Hub has its own dialogs.
## 2026-10-06 · Main green again; SSH keys only for root

- NVIDIA install test fixed for the running kernel's headers. When Debian no
  longer offers headers for the running kernel (after a kernel update), the
  install stops before apt with "install the updates, restart, try again"
  instead of failing the same way on every try (`apt-cache policy`, no helper).
- **SSH security fix:** the drop-in pointed `AuthorizedKeysFile` at one admin
  key file for every account, so an admin key signed in as any account with a
  shell, and everyone's own `~/.ssh/authorized_keys` (root's too) stopped
  working. Now `.ssh/authorized_keys .ssh/authorized_keys2
  /etc/ssh/alvaos-authorized-keys-%u`, and the helper only writes the `-root`
  file. Keys in the old file are read once and carried over on the next save.
  The old setting was never in a release.
- Saving SSH settings now runs `sshd -t` (new policy rule: only `-t`) before
  `systemctl reload ssh` and puts the old files back when it fails; turning off
  password sign-in without a key is still refused.
- Policy tests for the sshd drop-in, the admin key file, `sshd -t` and
  `dpkg --configure -a`; `test_priv_flows.py` runs the SSH page's API through
  the real policy (the fake helper now keeps written config files).
- Two ruff E701 errors in `api_system.py`.
- Note for next time: `backend/tests/test_backup_manager.py` needs `btrfs`
  installed (btrfs-progs), otherwise six tests fail locally; CI has it.

## 2026-10-05 · First hardware test follow-ups (1a)

- NVIDIA installs now request headers for the running kernel, retain the apt
  output for the Graphics page, and offer a policy-limited repair for an
  interrupted dpkg configuration.
- Settings › Security now configures SSH port, password sign-in and Ed25519
  admin keys; the helper validates both the sshd drop-in and key file.
- The assistant prompt now describes the actual pages and controls and tells
  it to avoid invented UI. The browser terminal and safe assistant command
  catalog design is in `docs/ADMIN-TERMINAL.md`.
- Dialog width, inner scrolling and primary-button styling have a shared base.
  Migrating all existing hand-built dialogs to one component remains open;
  see `ROADMAP.md`.
- Note for next time: test NVIDIA installation and SSH key/password changes on
  real hardware before relying on these flows. No automated or browser checks
  were run in this session.

## 2026-10-05 · Blockers from the first hardware test: people and shared folders

- **Adding people failed** ("Usage: useradd ..."): the privilege policy checked
  `useradd`, `userdel`, `usermod`, `smbpasswd`, `groupadd`, `groupdel` and
  `gpasswd` and then ran them *without their arguments* (`Plan(argv=[])`).
  Since the first helper; tests only checked that a command is allowed.
- **Making a shared folder failed** (exit 126): the smb.conf check refused
  `vfs objects`, which AlvaOS writes itself for the network trash. Now only
  known modules are allowed (`recycle`, `catia`, `fruit`, `streams_xattr`,
  `btrfs`), never a path.
- New safety net: every allowed command in the policy tests must run exactly
  as checked, and `test_priv_flows.py` runs the real admin API (add a person,
  make a shared folder with rights, remove the person) with the backend as
  on a NAS: each command goes through the real policy, stdin included.
- Setup: disks with old data are listed with *Erase* (a second click erases)
  instead of "no free disk".
- Dashboard: memory also as an amount ("5.4 of 15.6 GB"). App store cards:
  icon, name and category in one line, what the app needs next to Install.
- Tests no longer read the real `/var/lib/alvaos/users.json`.
- The rest of the test round is on the roadmap (1a): dialogs, NVIDIA install,
  assistant, SSH settings, a terminal.

## 2026-10-04 · Virtual machines (docs/VMS.md)

- New admin page **Virtual machines** (`vms.html`, `vms.js`, in the
  navigation): set up once (installs QEMU, UEFI firmware and a TPM on request
  and makes the shared folder `VMs` on a pool), new machine (Windows 11,
  Windows 10, Linux, other; sensible cores/memory/disk per system, details under
  "More options"), start, shut down (power button of the guest), restart,
  switch off, delete, settings (installer, cores, memory, ports, start with
  the NAS) and **the screen in the browser** (noVNC, vendored under
  `frontend/vendor/novnc`, bundled as one file).
- QEMU/KVM without libvirt: `vm_ops.py` builds the command line from a
  description it checks again (the backend writes, never passes a command);
  systemd template unit `alvaos-vm@.service` as the account `alvaos-vm`
  (created by the installer and the package), `ExecStop` presses the
  power button and waits. `alvaos-priv` got `vm-setup|vm-prepare|vm-delete|
  vm-isos`; the policy allows `systemctl` only for `alvaos-vm@<8 hex>.service`.
- `vm_manager.py` (state, setup job, create/update/delete/actions, memory and
  space checks), `api_vms.py` (`/api/v1/vms*`, admin only), `vm_console.py`
  (one-time tickets, a door on 8085/9445 that only copies bytes after the
  check; the web UI's CSP got `connect-src` for those ports).
- Disks are qcow2 files in `VMs/<id>/` (also UEFI variables, TPM state), so
  restore points, backups and limits apply. New packages are listed in
  `scripts/ci/optional-packages.txt`.
- Checked here only with fakes (no KVM in this environment); the page was
  driven in a browser against the real door with a fake VNC server. A real
  run is step 8b of `docs/TESTING.md`.
- Note for next time: `-sandbox` for QEMU, a bridge network and a virtio
  driver ISO are left out on purpose until they can be tried on hardware.
- Roadmap: thought through and written down (not built): UPS (NUT), access
  without a router setting (Tailscale, Cloudflare Tunnel) and a warm
  standby NAS instead of automatic failover (`ROADMAP.md` 2c).

## 2026-10-04 · Hub: Calendar (like Google Calendar) and Chat (like ChatGPT)

- **Calendar** (`hub_calendar.py`, `files-app/calendar.js|css`): day, week
  (3 days on a phone), month and schedule; little month; calendars with
  Google's colours, shown or hidden; tasks with or without a day (in the
  calendar and in a tasks panel); repeating events; click or drag to make
  an event, drag to move, pull to lengthen; detail card and full editor;
  Google's keys. Own calendars in `.alvaos/calendar/calendar.json` of the
  personal folder; family calendars in shared folders the admin marks
  ("Shared family calendars" on the Hub page), changed by whoever may
  change the folder.
- **Chat** (`hub_chat.py`, `files-app/chat.js|css`): chats on the left,
  model top left, "Think" on or off with low/medium/high, the answer
  streamed (NDJSON), thinking folded, Markdown. No tools: it cannot see or
  change the NAS. Uses the AI service of Settings › Assistant (key stays on
  the NAS); on the Hub page the admin turns it on (off at first), chooses
  who sees it and which models (`/api/v1/hub/chat-models` asks the
  service). Chats in `.alvaos/chat/`.
- Hub app data: `hub_data.py` reads and writes small JSON files as the
  person through new helper operations `files-data-read|write|delete`
  (below `.alvaos/`, replaced whole, 16 MB at most). `need_session(app)`
  checks the right app. The Hub shell (`app.js`) opens Calendar and Chat as
  views of their own (`#app=calendar`), each with the person and "Sign out"
  in its sidebar.
- Note for next time: CalDAV (phones syncing) needs the calendars as
  `.ics`; the JSON file is the place to convert from. Changing one repeat
  changes the whole series (no exceptions yet).

## 2026-10-04 · Hub: where apps keep data (docs/HUB.md and the settings)

- `docs/HUB.md`: the reference for the Hub: Hub apps vs store apps, who sees
  what, and where data lives (normal files in shares; personal folder by
  default, a pool per app when wanted; shared libraries; one cache place).
- `hub_apps.py`: per app `location` (personal folder or a pool with an
  optional limit per person) and `libraries`; `storage.cache_pool`.
  `own_folder()` and `cache_dir()` answer where things are.
- Admin Hub page, new section "Where things are kept", simple by default:
  one line per topic saying what it is now, details only when opened.
  Always shown: personal folders ("Make them" right in the line: first
  pool, no limit; opened: pool and limit) and shared photo libraries.
  Under "More options": where each app keeps everyone's own data (with a
  pool: shares `<person>-photos` only for that person, made right away,
  limit each) and where thumbnails go (a "1 tip" badge while they are on
  the system disk). API `/api/v1/hub`
  (`apps.*.location`, `apps.*.libraries`, `storage`, `personal_folders`).
- Hub: Photos shows the person's own `Photos/` (made on first open) plus
  the libraries they may read, merged newest first (`/api/photos/sources`).
  `/api/media` needs Photos. Thumbnails go to `<pool>/.alvaos-hub/thumbs`
  when a cache pool is chosen (made by the helper, owned by the service).
- Note for next time: libraries are stored sorted; uploading into the own
  photos from the Hub is not there yet (Files can upload into `Photos/`).

## 2026-10-04 · AlvaOS Hub: the frame, with Files and Photos as its first apps

- The Files app is now **AlvaOS Hub** (same service and ports, 8090/9443):
  sign-in page, title and the installed app (PWA) say AlvaOS Hub. An app bar
  (left on a computer, along the bottom on a phone) switches between the Hub
  apps; it stays hidden while someone sees only one app.
- `backend/hub_apps.py`: the Hub apps (Files; Photos, which is part of Files)
  and `/var/lib/alvaos/hub.json` with, per app, on or off and who sees it
  (everyone, or a list of people; the admin account sees every app that is
  on). Without the file everything is on for everyone, as before.
- Enforced on the server, not only hidden: every Files API call answers 403
  (`app_off`) for someone who may not use Files; WebDAV refuses them too;
  share links do not open while Files is off. `/api/me` returns the apps the
  person sees.
- Admin pages: the "Files" page is now **Hub** (navigation, `files.html`
  kept as the address): turn the Hub on or off, open it, and "Apps in the
  Hub" with a switch per app and "Who sees it: Everyone / Only some people"
  with a tick per person. API `/api/v1/hub`. The Apps page card and setup
  say AlvaOS Hub; the assistant reads the Hub (`hub`) and links its page.
- Tested against the real Hub server with two people: Anna sees Files and
  Photos, Ben only Files, then nothing (with a plain message).
- The Hub has an icon of its own (`files-app/hub.svg`: four tiles, one
  round) for its sign-in, app bar, tab, installed app and the Hub page;
  Files keeps the folder icon as a Hub app. No start page: the Hub opens the
  first app in the bar.
- The App Store no longer lists AlvaOS Hub/Files as a built-in tile;
  searching it for files, photos, hub, cloud or drive shows a pointer to the
  Hub page instead.
- Photos is no longer a button in the Files sidebar: it is its own Hub app
  and opens from the app bar only.
- **Note for next time:** next Hub apps: Calendar and Contacts (Radicale).

## 2026-10-04 · Roadmap: AlvaOS Hub and the Files plan

- Decided: "AlvaOS Hub": one address and one sign-in for the household.
  Part of AlvaOS (the Files server becomes the Hub server). Hub apps are
  ours, modules inside it (Files, Photos, Calendar and Contacts over
  CalDAV/CardDAV, Chat, Notes); store apps stay Docker containers and show
  up as tiles. The admin turns each Hub app on or off and chooses who sees
  it; what is off does not run and is not installed. No third-party plugins
  inside the Hub for now. The native apps build on it. Admin pages stay
  separate.
- The Files plan written down: what is done, the next simple steps (sort,
  start page with recent and favourites, folder upload, own space, text
  editing, drop-box notice, EXIF, sharing inside) and the powerful ones
  (Office via EuroOffice, search filters, activity, link control, sync).
- Languages: Python for what is tied to the NAS, Go for sync, native app
  core and tunnel, standard projects where they exist. Files stays Python.

## 2026-10-04 · Settings › Graphics: graphics cards and their drivers

- Lists every graphics card (PCI class 03xx in /sys, also those without a
  driver) with maker and model (from pci.ids), the kernel driver, the render
  node and whether it shows the console. States: Ready, Video drivers
  missing, Needs a driver, Restart needed.
- "Install firmware and video drivers" (Intel, AMD) or "Install NVIDIA
  driver": apt-get through the helper, with fixed package lists per maker
  (`gpu_manager.PACKAGES`), in the background; the page follows it. NVIDIA
  sets "restart needed" (cleared after the next start); "Restart now" on the
  page. With Secure Boot on, the page says the NAS asks once for the MOK key
  on its screen.
- Package names checked against Debian trixie in CI: the split firmware
  packages (`firmware-intel-graphics`, `firmware-nvidia-graphics`) do not
  exist in trixie, that firmware is in `firmware-misc-nonfree`;
  `nvidia-container-toolkit` is not in Debian at all. New
  `scripts/ci/optional-packages.txt`; the CI container now uses the same
  components as a NAS (main contrib non-free non-free-firmware).
- `pciutils` installed (model names). The assistant can look at the cards.
- **Note for next time:** apps do not get the card yet: Jellyfin/Immich need
  `/dev/dri` passed in (Intel/AMD); NVIDIA in containers needs NVIDIA's
  container toolkit from NVIDIA's own apt repository. Not tried on real
  hardware.

## 2026-10-04 · Backup disk in one step; an unplugged backup disk is no alarm; missing pools say so

- Backup › Backup disk lists empty disks (a USB disk just plugged in):
  "Erase and use as backup disk" (type ERASE) makes a pool of it, chooses
  it and starts the first copy. No detour through Storage any more.
- An unplugged backup disk (the normal case) raised the critical alert
  "Pool is not mounted" (also by email/Telegram) and made the dashboard say
  "Offline". The backup disk pool is now marked (`is_backup_disk` in
  `/storage/pools` and the system info), skipped by that alert and the
  dashboard storage card, and shown in Storage as "Backup disk · Not
  connected. That is normal". It has its own warning after 7 days.
- Pools whose disks are gone were listed as "Healthy", and with the size of
  the system disk (df on the empty mount folder). Now `status: missing`,
  "Not connected" in Storage, no df on a folder that is not mounted, and a
  `mounted` field for every pool.

## 2026-10-04 · CI checks the installer's package names in Debian trixie

- The installer installs Debian trixie and asks apt for ~35 packages in one
  command; one name that does not exist there stops the installation. New
  CI job `installer-packages` (container `debian:trixie`) runs
  `scripts/ci/check_installer_packages.sh`: every name must be known to
  `apt-cache`.
- The test job installs `qrcode` (the remote access QR codes are tested).

## 2026-10-04 · Remote access problems are alerts

- "Remote access is not running" when it is on but the tunnel is down, and
  "Your home's name is not updated" when DuckDNS failed for a day (or never
  worked). Both link to Settings › Remote access and go out by email or
  Telegram like the other warnings. Otherwise one finds out only when away
  from home and nothing connects.

## 2026-10-04 · HTTPS certificate follows new addresses while running; remote access closes its router port

- The server certificate was checked only when AlvaOS started, and the
  running HTTPS servers kept the one they had. A new address (DHCP, or the
  remote access tunnel 100.96.96.1) gave certificate warnings until a
  restart, and a NAS running past the certificate's end would have served
  an expired one. Now checked hourly (`tls_manager.keep_fresh`); a renewed
  certificate is loaded into the running server for new connections.
  Tested with a real TLS handshake.
- Turning remote access off now removes the port mapping it asked the
  router for (UPnP); turning it on asks again.

## 2026-10-04 · Remote access: DuckDNS built in

- Most homes get a new internet address now and then, and the devices'
  configurations name the address. Remote access › "Your address changes?":
  a DuckDNS name and token; AlvaOS updates it at once and every 10 minutes
  and uses `<name>.duckdns.org` as the public address.
- The token is stored with the other remote access secrets (0600) and never
  shown again or sent to the page or the assistant. Errors ("refused",
  "could not be reached") are shown under the name.

## 2026-10-04 · Remote access: the router opens the port by itself (UPnP), CGNAT is explained

- "Open it automatically" asks the router over UPnP (`upnpc` from
  miniupnpc, runs as the service user, no helper needed) to forward the
  UDP port to the NAS; permanent first, a 7-day lease for routers that
  refuse that; renewed at start and every day. A changed port removes the
  old mapping first. Refusals are shown with the router's reason.
- "Find it" first asks the router for its internet address (no outside
  service), then api.ipify.org.
- When the router's internet address is in 100.64.0.0/10 (the provider
  shares it, CGNAT) or private (a second router in front), the page says
  why nothing from outside can arrive and what to ask for.
- miniupnpc added to the installer and the package dependencies.
- **Note for next time:** only tested against recorded `upnpc` answers.

## 2026-10-04 · The Feedback button no longer covers the last row

- On phones the floating Feedback button sat on the last buttons of a page
  (e.g. "Remove" in Remote access, "Stop using it" on the backup disk).
  While it is shown, `body.has-feedback-fab` gives the page 72 px more room
  at the end; dismissing it takes the room away again.

## 2026-10-04 · Tests no longer write into /var/lib/alvaos

- Importing `app_services` makes the backup and buddy managers, which wrote
  their state files into `/var/lib/alvaos` of the machine running the tests
  (and test notifications into `notifications.json`). A developer machine,
  or a NAS someone runs the tests on, got test buddies and 200 test
  notifications.
- Both managers now honour `ALVAOS_STATE_DIR`; `tests/conftest.py` points it
  at a temporary folder and redirects the alert and notification files.

## 2026-10-04 · Backup page lists the copies on the backup disk; a test checklist

- The copies on the backup disk were registered as restore points but the
  Backup page only asked for class `data`, so they never showed up there.
  Now listed with "On the backup disk", only "Get files" (no "Restore all",
  no Delete: they are thinned out on their own). The API adds `available`
  for copies; "Get files" is greyed out while the disk is not connected.
- `docs/TESTING.md`: a walk through everything by hand, in the order a new
  owner would do it, with what should happen; steps that need a real
  machine are marked.

## 2026-10-04 · First run on the installed layout: three fixes

- Ran the backend and the Files server from `/opt/alvaos` as
  `install-system.sh` lays it out, went through setup and every page with
  the real API: no script errors, every page and file loads.
- Setup, storage step: "Look again" (no free disk) tried to create a pool
  and showed "At least one device is required". `withBusy` changed the
  button text before `submitStorage` checked it; now decided before.
- Renaming the NAS replaced the old name as plain text in `/etc/hosts`
  (also inside other names, or "localhost"). Now whole names only
  (`replace_host_name`, tested).
- Dashboard on a new NAS showed three "next steps" (storage, then backups
  before there is any storage, then "Getting started"). The list under the
  status line now shows only problems; setup steps are in "Getting started".

## 2026-10-04 · Remote access over WireGuard (Settings › Remote access)

- Turn it on, enter the public address of the home (dynamic DNS name or
  address; "Find it" asks api.ipify.org, only on that button), forward the
  shown UDP port (51821) in the router to the shown NAS address.
- "Add a device" makes a key pair for the phone or laptop and shows its
  WireGuard configuration once: QR code (phone app) and a file (computer).
  The device's private key is never stored; the NAS keeps its public key and
  a pre-shared key. Devices list with "Connected now / Last connected" (from
  `wg show remote0 latest-handshakes`) and Remove.
- The tunnel is `remote0`, 100.96.96.0/24, NAS 100.96.96.1; clients get
  `AllowedIPs = 100.96.96.1/32`, so only the NAS is reached, also the apps
  on their ports. Buddy Backup keeps buddy0 / 100.95.95.x / 51820.
- Keys are made with `cryptography` (X25519), no `wg genkey`. Config and
  settings are 0600. The helper runs wg-quick only for buddy0.conf and
  remote0.conf, refuses PostUp and other hooks, and allows `wg show remote0
  latest-handshakes` but not `dump` or `private-key` (they print keys).
- Brought up again at boot. The assistant can look at it (`remote_access`).
- **Note for next time:** not tried with a real tunnel (the build container
  has no WireGuard module). UPnP to open the router port by itself would
  save the router step; an option "also reach the home network" would need
  forwarding and NAT.

## 2026-10-04 · Installer: a fresh install has AlvaOS Files and all packages

- The installer image copied only the files directly in `frontend/`; the
  AlvaOS Files app lives in `frontend/files-app/`, so after a fresh install
  Files had no page. Now the whole folder is copied.
- `alvaos-files.service` was not put into the image either; now it is.
- A fresh install lacked packages the update package depends on:
  `python3-pil` (thumbnails), `python3-pyotp` and `python3-qrcode` (2FA).
  Added, and `samba-vfs-modules` (the network trash) on both sides.
- New test `tests/test_installer.py`: the installer installs every package
  the .deb depends on, and carries the Files app and its service.

## 2026-10-04 · Assistant: sees more, can do more, works with small models

- New things it can look at: the backup disk, Buddy Backup, HTTPS, signed-in
  devices, the system log and the log of one app, SMART details of one disk.
  Logs are masked first (passwords, keys, tokens, user:password@ in
  addresses, long random strings). Tools with an argument (`app_log`,
  `disk_health`) check it against a pattern before it goes into the path.
- New proposals (the person confirms each): make a shared folder for some
  people or everyone, give or take one person's access, set a space limit,
  turn on AlvaOS Files, copy to the backup disk now. Each is checked against
  the real state (pool exists and is not the system, name free, people
  exist, nobody locked out of a folder).
- It knows which page is open and links pages like `[Backup](backup.html)`;
  the chat shows links only to AlvaOS pages, anything else stays text.
- Small local models sometimes write a tool call as JSON text instead of
  making one; such text is now read as a call when it names a known tool.
- Clear messages for: service not reachable (with the Ollama hint
  `OLLAMA_HOST=0.0.0.0`), too slow, wrong API key, model not pulled.
- Test: every proposal and every read tool points at a real route with the
  right method (`test_every_assistant_proposal_points_at_a_real_endpoint`).
- **Note for next time:** not tried with a real model (no model downloads in
  the build container). Worth a run with Ollama llama3.1 / qwen2.5 on a NAS.

## 2026-10-03 · Backup disk: four fixes after a second look

- "Safely remove" was undone by the 10-minute check: if the disk stayed
  plugged in and a new restore point came, it was mounted again, so pulling
  it later was not safe. It now stays unmounted until it was unplugged or
  "Copy now" is pressed; the card says "Safe to unplug".
- Folders backed up in the same run share snapshot names; the check
  compared only names, so a failed copy of one folder was not tried again
  until the next restore point. Now compared per folder.
- "Safely remove" during a copy waited for the copy to end (hours the first
  time). Now it says a copy is running.
- The page stopped following "Copy now" right away when an earlier copy had
  failed. The status now says whether a copy is running ("Copying…"); the
  page follows it until it ends, also one that started by itself.

## 2026-10-03 · Backup disk: a second copy on a USB disk

- Backup › Backup disk: choose a pool (usually a USB disk) that holds no
  shared folders. Every 10 minutes, and on "Copy now", the newest restore
  point of every source is copied there with Btrfs send/receive into
  `.alvaos-copies/<source>/`, incremental against the newest copy both
  sides have. A failed copy removes its half-received snapshot.
- 30 copies per source are kept. The copies are restore points of class
  `copy`: Restore and Files › Previous versions list them, rolling back a
  whole folder to one is refused ("Get files" instead).
- "Safely remove" unmounts the disk; it is mounted again by UUID when it
  comes back. Alert "Connect your backup disk" after 7 days without a copy
  (counted from when the backup disk was set up).
- Code: `backend/backup_copy.py`, `/api/v1/backup/copy*`,
  `frontend/backup-disk.js`.
- **Note for next time:** send/receive and the mount were only tested with
  mocks; the container has no Btrfs. Try it on a real NAS with a USB disk.

## 2026-10-03 · Five bugs from a code review of this PR

- Previous versions failed for a file in 100 restore points: the Files
  server sends the name, the current folder and up to 100 folders, the
  helper took at most 101 arguments. Now 102.
- Files sessions in a browser outlived a password change or the removal of
  the person (and renewed themselves for 14 days). A session now carries a
  fingerprint of the stored password; when it changes or is gone, the
  session ends. Sessions from before this change end once.
- WebDAV counted every uncached sign-in, also correct ones, towards the
  per-address limit it shared with the Files login; Finder or a few
  devices behind one router could be locked out with the right password.
  Now only wrong passwords count, in WebDAV's own counter.
- A writable share where everyone may only read never got the network
  trash lines, but the start-up check expected them, so Samba was
  restarted at every backend start. The check now asks the same function
  that writes the section.
- Email settings saved while the background check was sending (up to ~20
  s) were overwritten by its older copy. The loop now writes back only
  what it owns (sent problems, report time, last error) onto a fresh copy.

## 2026-10-03 · Upload links with a size limit

- An upload link ("drop box") can take up to 1, 5 (preselected), 20 or 100
  GB in all, or as much as the folder has room for. Every piece counts
  (`received` in the link); when the limit is reached, start and piece
  answer 413 with a plain sentence. The guest page says how much room is
  left; the list of links shows "x of y".
- Real test: a 1 GB link counted exactly the bytes of two uploads.

## 2026-10-03 · Deleting over the network goes to the trash

- Writable SMB shares get Samba's `vfs_recycle`: a file deleted in Windows
  Explorer or the macOS Finder is moved to `<share>/.alvaos-trash/smb/<its
  folder>/` (versions as "Copy #2 of …", the time of deleting as the file's
  time; temp files, `~$` Office locks, Thumbs.db and .DS_Store are not
  kept). The trash folder is hidden from computers.
- The Files trash lists these too ("deleted from a computer"), puts them
  back into their folder (name taken: "… (restored)"; folder gone: the
  share), and empties them after 30 days with the rest. Ids are
  `smb:<path>`; every part is checked as a single name and opened without
  following symlinks.
- Shares made before get the setting once when the backend starts
  (`add_recycle_bins`, then one restart of smbd).
- Real test with Samba 4.19 and smbclient: two files deleted over SMB
  landed in `.alvaos-trash/smb/` (keeping their folder), Files listed them
  and put one back with its content; the trash folder shows as hidden (H)
  to SMB clients. `testparm` accepts the generated section.

## 2026-10-03 · Plain words in the last technical messages

- Five messages still spoke Btrfs: "No eligible subvolume sources found",
  "Create Btrfs pools/subvolumes first", "Local incoming data path
  (subvolume)", "Restoring a full system snapshot prepares a rollback" and a
  bare "Subvolume" label. They now say what it means for the person; the
  Btrfs term stays only in the app details, in brackets.

## 2026-10-03 · AlvaOS Files: Photos, and files keep their date

- "Photos" in the Files sidebar: every picture and video of the open
  shared folder, newest first, grouped by month, as square tiles; a click
  opens the viewer, arrows go through all of them. New helper operation
  `files-media DIR` (as the person, like search: no symlinks, skips hidden
  folders, the trash and unfinished uploads; at most 5000, 15 seconds),
  `GET /api/media?share=`.
- Uploads keep the date the file had on the device (`File.lastModified`,
  helper `files-part-finish-dated`), in the app and through drop-box links,
  so photos land in the right month. Dates before 1980 or in the future are
  ignored.
- Bug fix: numbers passed to the helper were limited to 10 digits, so an
  upload past 10 GB (piece offset, final size) and seeking past 10 GB in a
  video failed. Now 16 digits, still with each operation's own bounds.
- Real test: 38 photos and a video over five months shown in order; a photo
  uploaded with a July 2024 date appeared under July 2024.

## 2026-10-03 · Accessibility pass with axe-core

- Every page of the web interface (both themes, also the Shares, Users,
  Security and Assistant tabs, sign-in and setup) and AlvaOS Files (sign-in,
  app, share link, drop box) checked with axe-core: no violations left.
- Fixed: small grey text (`--text-tertiary`) now reaches 4.5:1 in both
  themes; filled buttons in dark mode use a deeper blue (`--accent-solid`,
  white text 4.6:1 instead of 2.5:1); yellow pills in light mode 5.3:1; the
  Getting started progress bar has a name; Files has one `main` landmark and
  a level-one heading, the share page too.
- **Note for next time:** the audit script lives outside the repo (Playwright
  + axe-core from npm in the scratchpad); a CI job for it would need npm in
  CI, which the project avoids so far.

## 2026-10-03 · Security review of today's changes

- WebDAV remembered a correct sign-in for 10 minutes, also after the
  password was changed. The remembered entry is now bound to the stored
  password hash; a new password ends it at once (test).
- HTTPS authority restricted by name constraints (entry below).
- Looked at and fine: WebDAV paths and the Destination header go through
  the same `resolve` as the app (no `..`), names in PROPFIND are escaped;
  assistant actions run with the person's session and CSRF token and only
  as stored by the server; the public certificate download holds no
  secret; HTTPS-only never redirects the Buddy Backup peers.
- An upload link ("drop box") could take as much as the share could hold;
  it now has a size limit (entry above).

## 2026-10-03 · HTTPS authority limited to the home network

- The NAS's certificate authority now carries X.509 name constraints: it may
  only sign for its own host name, home-network domains (`.local`, `.lan`,
  `.home`, `.home.arpa`, `.internal`, `localhost`) and private addresses
  (10/8, 172.16/12, 192.168/16, 127/8, 100.64/10, 169.254/16). Browsers
  enforce this, so even a stolen authority key cannot be used to pose as
  another website to the devices that trust the NAS.
- The server certificate only lists names and addresses the authority may
  sign for (a public address of the NAS is left out instead of making the
  certificate invalid). An authority made before this keeps working as
  before.
- Test: a certificate for `bank.example` signed with the authority key is
  refused by a real TLS client.

## 2026-10-03 · First setup: what can be turned on later

- The last page of the setup wizard lists three extras with links instead
  of an extra step: AlvaOS Files (Apps), the assistant (Settings ›
  Assistant) and the encrypted connection (Settings › Security). Setup
  stays as short as it was.

## 2026-10-03 · Assistant: three more things it can suggest

- Turn on automatic restore points (every hour, day or week), update an
  installed app, install the newest AlvaOS version (only a signed release
  asset; the safety net goes back on its own if it fails). Each is checked
  against the real state before it is shown, and still runs only on "Do it".
- New read tool `alvaos_update_check` so it can tell whether a newer
  version exists.

## 2026-10-03 · "HTTPS only"

- Settings › Security › Set up devices › "HTTPS only": plain HTTP is then
  answered with a 308 redirect to the HTTPS port (web interface 8080 → 8443,
  Files 8090 → 9443); WebDAV on 8091 answers 403 with the HTTPS address,
  because file managers do not follow redirects reliably.
- It can only be turned on from a page opened over HTTPS, so the device
  already trusts the NAS and nobody locks themselves out. Turning it off
  works from HTTPS too (plain HTTP is redirected like everything else).
- Never redirected: the authority certificate (new devices fetch it over
  HTTP), Buddy Backup pairing and the peer API (other NAS over the tunnel),
  and requests from the NAS itself (the assistant runs actions in-process).
- `https.json` holds the switch; `tls_manager.https_only()` rereads it only
  when it changes.

## 2026-10-03 · Background work gives way to people

- Data checks (scrub) start in the idle I/O class (`btrfs scrub start -B -c
  3`); the helper allows exactly that and the old form. Copying files over
  the network goes first (where the disk scheduler honours I/O classes,
  e.g. BFQ).
- Photo thumbnails in Files are made in two worker threads with nice 10
  instead of in the request thread at normal priority.
- Buddy Backup transfers (`btrfs send` and `receive`) run with nice 10 and
  the idle I/O class: the policy marks them `background`, the helper lowers
  itself before it becomes the command (`give_way`, ioprio_set by syscall).

## 2026-10-03 · Sign-in: idle sessions end, the last sign-in is shown

- A session nobody used for 8 hours ends (`IDLE_HOURS`), on top of the 24
  hour limit; an open page keeps itself alive by its polling, so this only
  hits closed browsers and forgotten laptops.
- `signin_log.json` (0600) keeps the last successful sign-in (time, device,
  address) and the wrong passwords and 2FA codes since. Right after signing
  in, a note says when and from where the previous sign-in was; wrong
  passwords since then are a warning with the addresses, linking to
  Settings › Security. Shown once per sign-in, never on the sign-in page
  itself, which anyone on the network can open.

## 2026-10-03 · Assistant: suggests changes, the person confirms each one

- Settings › Assistant › "What it may do": Only look (default) or Suggest
  changes, I confirm each one.
- At the second level the model gets five action tools (`ACTION_SPECS`):
  make a backup now, start a data check of a pool, restart an app, check
  services, let disks sleep. Calling one runs nothing: `build_action` checks
  the arguments against the real state (the pool exists and is not the
  system pool, the container exists, the minutes are allowed) and turns it
  into a proposal with a plain description; at most three per answer.
- `api_ai.py` keeps proposals 10 minutes, bound to the session that asked.
  `POST /api/v1/ai/actions/<id>` with "run" sends exactly the stored request
  through the normal endpoint, with that session and its CSRF token; another
  session or an old id gets 404; each proposal runs once.
- The chat shows each proposal as a card with "Do it" and "No"; the outcome
  stays in the card and is passed to the assistant in the next turn.
- The system prompt now differs per level, so "only look" is no longer said
  to a model that may propose.

## 2026-10-03 · Updates: the way back is always there

- The update cache now always keeps the signed package of the version that
  is running; `cleanup_cache` (newest three) skips it. Before, downloading
  three newer versions without installing them could delete it, and a
  failed update then had nothing to go back to.
- An install from the installer (which copies files, it does not install a
  package) or a USB stick left no package of the running version at all.
  Two minutes after the start, the backend now fetches it with its
  signature from that version's GitHub release (`ensure_way_back`), retrying
  every 6 hours for a week while offline. The helper checks the signature
  when it is used, as for every update; a package of another version is
  thrown away.
- Updates › Settings shows "Safety net ready" or what is still missing
  (`way_back_ready` in `GET /api/v1/updates/rollback`).

## 2026-10-03 · HTTPS on the home network, Files installable as an app

- `backend/tls_manager.py`: every NAS makes its own certificate authority
  (EC P-256, 10 years) and a server certificate it signs for the NAS's
  names (`host`, `host.local`, `localhost`) and IPv4 addresses (825 days).
  The server certificate is made again when it ends within 30 days or no
  longer covers the current names and addresses; the authority stays, so
  devices keep trusting it. Keys 0600 in `/var/lib/alvaos/tls`; a file lock
  because the backend and Files start at the same time.
- HTTPS runs next to HTTP, nothing that worked stops: web interface 8443,
  Files 9443, Files WebDAV 9444. Waitress cannot do TLS, so these ports use
  Werkzeug's threaded server with a 120 s connection timeout; the client
  address stays real (login limits keep working).
- Settings › Security: "Encrypted connection (HTTPS)" with the address, a
  download of the authority certificate (`/api/v1/system/tls/ca.crt`,
  public on purpose; Files serves it too at `/alvaos-ca.crt`) and steps for
  Windows, Mac, iPhone/iPad, Android and Linux/Firefox, plus fingerprint and
  covered names.
- Links to Files (Apps, Files page) and the WebDAV address in Files follow
  the page: https opens Files on 9443 and WebDAV on 9444 (`davs://`).
- Files is installable as an app (PWA) over HTTPS: a service worker keeps
  only the app itself (never files or API answers) so it opens fast and
  shows the NAS is away when it is.
- Real test: Files and WebDAV over HTTPS checked with curl against the
  authority (by name and by IP), a 20 MB upload piece over HTTPS arrived
  intact; a client that does not trust the authority is refused.
- **Note for next time:** "HTTPS only" (sending http:// to https://) is the
  next step once people have trusted the authority; not on by default,
  because a device that has not trusted it would see a warning page.

## 2026-10-03 · AlvaOS Files over WebDAV (port 8091)

- `backend/files_dav.py`: the shares in Finder (Connect to Server), GNOME
  Files (`dav://nas:8091/`), Windows and WebDAV file apps on phones. Same
  name and password as for the shares (HTTP Basic, checked like the Files
  sign-in, remembered for 10 minutes so not every request runs PBKDF2; the
  same failed-attempt limit). The admin account is refused there (it would
  act as root, and WebDAV cannot ask for a two-step code).
- PROPFIND (root = one folder per share), GET/HEAD with ranges, PUT, MKCOL,
  DELETE, MOVE, COPY (inside one share), LOCK/UNLOCK (acknowledged, so
  Finder and Windows write), PROPPATCH (acknowledged). Every change runs as
  the person through the existing helper operations. Nothing is
  overwritten in place: PUT writes a hidden part file, the old file goes to
  the trash, then the new one is renamed into place; DELETE and replacing
  MOVE/COPY also go through the trash.
- It runs in the same process as the Files app, but on Werkzeug's threaded
  server: Waitress reads whole request bodies before the app sees them,
  which would park big uploads on the system disk. Werkzeug streams them,
  chunked ones too.
- Files app: "Connect a computer" in the sidebar shows the address and the
  steps for Mac, Linux, Windows and phones.
- Real test with curl: a 30 MB chunked PUT arrived with the same SHA-256,
  overwrite left the old file in the trash, MKCOL/MOVE/COPY/DELETE worked,
  a read-only share refused PUT, a wrong password got 401.
- **Note for next time:** Windows' WebDAV client only does Basic sign-in over
  HTTPS by default, so on Windows SMB stays the way; this changes with HTTPS.
  Try Finder on a real Mac once (it is picky about LOCK and PROPFIND).

## 2026-10-03 · AlvaOS Files: search in all shared folders

- The search results have a switch: "In <this folder>" or "All shared
  folders". All runs `files-search` once per share the person has, as the
  person, up to 200 results in total; every result knows its share, so
  opening, previews, download, versions and "Show in folder" work across
  shares.
- Real test: "beach" found a photo in Family and a document in Work (a
  read-only share); the document opened, "Show in folder" switched to Work.

## 2026-10-03 · AlvaOS Files: upload links ("drop box")

- Sharing a folder you can change offers "Only upload files into it (drop
  box)". Visitors get a page with a drop zone; they see nothing of what is
  in the folder (list, file, thumbnail and ZIP answer 403 for such links),
  and a view link cannot upload. Files land as the link's maker, in pieces
  that continue after a dropped connection; a taken name becomes
  "photo (2).jpg", an unfinished upload of the same name is continued.
- `POST /api/public/<token>/upload/start|piece|finish` (with the
  X-AlvaOS-Files header). Links list shows "Upload only".
- Real test: a guest on a phone uploaded two files into a folder that
  already had photo.jpg; the folder listing stayed refused.

## 2026-10-03 · Personal folders: pool is chosen, warning when nearly full

- "Add a person": the pool for the personal folder is always chosen by hand
  (with its free space); nothing is preselected.
- A share with a space limit raises a warning at 90 % and a critical alert
  at 99 % ("The personal folder of anna is nearly full"), on the dashboard,
  the bell, and by email/Telegram like other problems
  (`share_quota.limit_alerts`).

## 2026-10-03 · Personal folders and space limits

- "Add a person" (Storage › Users) can give them a personal folder, on by
  default when a pool exists: a share named like them, in its own Btrfs
  subvolume `<pool>/<name>`, only they can open and edit it (marked
  `personal_for`). Optional space limit: none, 10 GB ... 1 TB or any number.
  The checks (pool, name not taken as share or folder, limit) run before the
  account is made; if making the folder fails later, the person is still
  created and the page shows why.
- Any share in its own subvolume can get a limit under Storage › Shares ›
  "Space limit" (`PUT /api/v1/storage/shares/quota`). The share card and the
  person's row show "12 GB of 50 GB used" with a bar (warning colour from
  85 %, red from 95 %).
- `backend/share_quota.py`: Btrfs quota groups. The first limit on a pool
  runs `btrfs quota enable`; limits are `btrfs qgroup limit BYTES|none`;
  usage from `btrfs qgroup show -reF --raw`. A folder that is not a
  subvolume (inode 256) is refused, so a limit never lands on a whole pool.
  The helper allows only these forms.
- Removing a person keeps their personal folder and files; the message says
  so.
- `create_share()` in `api_shares.py` is now shared by "Share a folder" and
  personal folders.
- **Note for next time:** not tried on real Btrfs here (no btrfs-progs in
  the test container). On a NAS: create a person with a 1 GB limit, copy
  2 GB into the folder over SMB, expect "disk full" at 1 GB, and check the
  numbers on the share card.

## 2026-10-03 · AlvaOS Files: previous versions of a file

- Right-click a file › "Previous versions…": every different state of that
  file in the restore points (newest first, with its time and size). "Open"
  shows it, "Restore" copies it next to the file as
  "name (restored <date>).ext"; nothing is overwritten.
- The Files server reads `backup_snapshots.json`, works out where the file
  was in each restore point (also for restore points of a whole pool) and
  asks the helper, as the person, which of those hold the file
  (`files-versions NAME DIR...`, at most 100). Same size and time as a newer
  one = the same version, shown once. Restoring is
  `files-restore-version`, also as the person; only with write access.
- Real test with a restore point built by hand: two older versions listed,
  opened, one restored with the right content.

## 2026-10-03 · AlvaOS Files: search

- Typing in the search box filters the open folder at once; half a second
  later (or on Enter) every folder below is searched by name too. All words
  must be in the name, any case. Results show where they are; "Open" and
  "Show in folder" (goes there and selects it). Changes happen in the folder,
  so results are read-only. On a phone a search button opens the box.
- New helper operation `files-search DIR QUERY`, run as the person: walks by
  descriptor without following symlinks, one open descriptor per level,
  skips folders they cannot open, the trash and unfinished uploads; stops at
  200 results, 40 levels or 15 seconds ("type more to narrow it down").
  `GET /api/search?share=&path=&q=` in the Files server.
- Real test: a folder only root can open was left out of the results.
- CI: the test job now installs Pillow (the thumbnail test failed there).

## 2026-10-03 · AI assistant (read-only)

- Settings › Assistant: off by default. Pick a service (Ollama on your
  network, Ollama Cloud, OpenAI, or any OpenAI-compatible API), address,
  model, API key; "Try it" asks for five words before saving. Settings in
  `/var/lib/alvaos/ai.json` (0600); the key is never sent back to the page.
- A chat button in the top bar of every page (`frontend/assistant.js`), only
  when it is on. Replies are escaped, then bold, code and lists are allowed;
  "Looked at: ..." under each answer. The chat lives in the tab's
  sessionStorage.
- `backend/ai_assistant.py` + `api_ai.py`: tool calling over the OpenAI chat
  API, at most 6 rounds. The tools are a fixed list of 20 GET endpoints the
  page already uses (pools, disks, backups, apps, updates, alerts, ...), read
  in-process with the admin's own session (`app.test_client`). Keys that look
  secret (password, token, key, hash, ...) are replaced by "(hidden)" before
  the model sees them; each answer is cut at 8000 characters.
- **Note for next time:** the next level ("asks first") needs a second
  allowlist of POST actions with a confirmation step in the panel; never
  let the model call arbitrary endpoints.

## 2026-10-03 · AlvaOS Files: copy

- "Copy to…" in the right-click menu (the folder picker of Move, "Copy
  here"). New helper operation `files-copy SRC_DIR NAME DST_DIR`: files and
  whole folders, copied through descriptors, never following symlinks,
  leaving out what the person cannot read, the trash and unfinished uploads;
  modification times are kept. A taken name becomes "name (copy)",
  "name (copy 2)", ... Not into itself.

## 2026-10-03 · AlvaOS Files: big uploads in pieces, continued after a drop

- Uploads go in 16 MB pieces: `GET /api/upload/status`, `POST
  /api/upload/piece?offset=`, `POST /api/upload/finish`, `/abort`. The helper
  (as the person) appends to a hidden `.<name>.alvaos-upload` part file,
  checks the offset is exactly where the part ends, and only renames it to
  the real name at the end, never over an existing file
  (`files-part-size | -write | -finish | -abort`). A symlink in place of the
  part file is refused.
- When the connection drops, the app asks how far the NAS got and goes on
  from there (6 tries with back-off). No more 4 GB limit; waitress now only
  takes 65 MB per request, so it never parks a whole upload on the system
  disk. The old single-request upload is gone.
- Real test: a 40 MB file in 3 pieces with the second piece's connection
  cut on purpose arrived bit for bit (same SHA-256), owned by the person.

## 2026-10-03 · AlvaOS Files: folders as ZIP

- "Download as ZIP" for a folder (right-click, or the selection's Download),
  "Download this folder as ZIP" on empty space, and "Download all" on a
  shared-folder link page.
- New helper operation `files-zip DIR` (`files_ops.zip_folder`): streams the
  ZIP to stdout while it is made (no temporary file, ZIP64 for big files,
  stored without compression since photos and videos are compressed
  already). It walks with `os.fwalk(follow_symlinks=False)`, opens files with
  `O_NOFOLLOW`, leaves out hidden files and the trash, and runs as the
  person: in a real test, a subfolder the person had no access to was left
  out by Linux.

## 2026-10-03 · AlvaOS Files: share links

- "Share link…" in the right-click menu: a link to a file or folder that
  anyone with it can open read-only, for 1, 7 (default), 30 or 90 days or
  until removed, optionally with a password. "Shared links" in the sidebar
  lists them with Copy and Remove (the admin sees all).
- Visitors get `/s/<token>`: the file with a preview and Download, or the
  folder with thumbnails, subfolders, a viewer and per-file download. A
  password link asks first (rate limited; unlocked by an HMAC cookie per
  link, secret in `files_secret`, 0600).
- Everything a visitor sees is read **as the person who made the link**
  (`--as`), inside the linked folder only (`..` refused); if that person
  loses access to the share, the link stops working. Links live in
  `files_links.json` (0600); expired ones are ignored.
- "Copy" works on plain http on the home network too (the clipboard API
  only exists on https; falls back to the old copy command).
- **Note for next time:** links only work where port 8090 is reachable (the
  home network, or the WireGuard tunnel); opening it to the internet needs
  HTTPS first. Upload-only links ("drop box") are still open.

## 2026-10-03 · AlvaOS Files: move, and who needs a password

- **Move:** "Move to…" in the right-click menu opens a folder picker of the
  share; items can also be dragged onto a folder or onto a folder in the path
  bar. New helper operation `files-move SRC_DIR NAME DST_DIR` (both folders
  checked on their descriptors, never over an existing item, not into
  itself; a separate subvolume says so). Only within one share.
- Storage › Users marks people who need their password set once more for
  Files ("Set password for Files"); setting it refreshes the list.

## 2026-10-03 · AlvaOS Files as a built-in app (port 8090)

- **Decision:** Files is its own app, like Nextcloud, but part of the OS:
  a separate service `alvaos-files` (`files_server.py`, waitress, port 8090,
  user alvaos), no database, no container. Off by default; "Turn on" under
  Apps (a "Built into AlvaOS" card at the top of the store) or on the Files
  page in AlvaOS, which also opens it and turns it off
  (`/api/v1/files-app` → `systemctl enable|disable --now alvaos-files.service`,
  the only new systemctl actions the helper allows).
- **Everyone signs in**, not only the admin: people from Storage › Users
  with their share password (a PBKDF2 hash is now stored in `users.json`
  whenever the admin creates a person or sets their password; people from
  before need it set once more, the Files page lists them), the admin as
  "admin" with the admin password and the 2FA code. Cookie session
  (HttpOnly, SameSite=Strict, 14 days), state changes need the page's own
  header, sign-in attempts are rate limited.
- **Same rights as over the network, checked by Linux:** each person sees
  the shares they have in `smb_permissions` (read or write; read-only shares
  stay read-only), and every file operation runs as them through
  `alvaos-priv --as <person>` (step A). The admin acts as root.
- **The app** (`frontend/files-app/`): sidebar with the shares and the
  trash; grid with thumbnails or list; click selects, double-click or Enter
  opens, Ctrl/Shift selection, right-click menu, F2, Delete, Backspace, arrow
  keys; drag and drop upload with progress; viewer with arrows; search;
  phone layout with a drawer; installable (web manifest). Thumbnails are
  made in the Files server (unprivileged; Pillow) from the bytes the helper
  reads as the person, 320 px, cached in `/var/lib/alvaos/thumbs`.
- The admin-only Files page and its API from earlier today are replaced by
  this; the helper operations, trash and range support are reused.
- Checked end to end for real in a container: the real server, the real
  helper as root, a person "anna" in a share group, real photos. It found a
  bug the stand-in tests missed (a finished upload answered 500 because the
  pipe to the helper was closed twice); fixed, with a test on a real process.
  Uploaded files belong to the person and the share's group, like over SMB.
- **Note for next time:** try on a real NAS with two people and a
  read-only share; HTTPS for the app (and for AlvaOS) is still missing;
  share links, resumable uploads and WebDAV are the next Files steps.

## 2026-10-03 · AlvaOS Files: seeking in videos, resuming downloads

- `/api/v1/files/get/<token>` answers one-range `Range` requests with 206,
  `Content-Range` and `Accept-Ranges` (416 when it cannot be served), and
  sends `Content-Length`. Videos can be skipped forward, and interrupted
  downloads can be resumed.
- The helper's `read-file` takes an optional `START LENGTH`; a new
  `file-size` operation reports the size (both checked on the open file).

## 2026-10-03 · AlvaOS Files, step 2: upload, new folder, rename, trash

- **Upload** (button or drag and drop on the list) with a progress panel,
  **New folder**, and a "⋯" menu per row with Download, Rename and Delete.
  **Delete moves to the share's trash**: "Trash" at the bottom of the list
  shows what was deleted, from where and when, with "Put back" and "Empty
  trash". Items older than 30 days are removed daily.
- All changes run as root in `files_ops.py`, called through new helper
  operations `alvaos-priv files-write | files-mkdir | files-rename |
  files-trash | files-trash-list | files-trash-restore | files-trash-purge`.
  Each gets a folder plus a single name (no "/"), opens the folder with
  `O_NOFOLLOW`, checks it on the descriptor to be below `/mnt/alvaos`, and
  works relative to it (`dir_fd`). Nothing is overwritten (`O_EXCL`, checks
  before rename; put back as "name (restored)"). A broken upload removes the
  half file. New files and folders get the group and permissions of the
  folder they are in, so the share's users can use them over SMB.
- The trash is `<share>/.alvaos-trash/<stamp>/<name>` with a `.origin` file;
  a trash that is a symlink is refused. Something in its own Btrfs
  subvolume cannot be moved there and the message says so.
- **Upload limit 4 GB per file:** waitress keeps a request body in a
  temporary file on the system disk until it is complete (default limit was
  1 GB). The page says so and points to the share for bigger files.
- Tested: `files_ops` against symlink tricks in a temporary tree, the real
  helper as root in the container, the API with a stand-in helper, and the
  page in the browser (desktop and phone).
- **Note for next time:** check owner and permissions of uploads on a real
  share from Windows/macOS; resumable uploads for big files; move between
  folders; per-person access.

## 2026-10-03 · AlvaOS Files, first step: browse, look at, download

- New page **Files** (in the navigation after Storage): the shared folders as
  tabs, breadcrumbs, filter and sort (name, newest, largest), folders first,
  size and date, hidden dot-files left out. Photos, videos and audio open in a
  viewer with arrow keys between them, text files as plain text, PDFs in a
  new tab; everything else downloads. Folder and share are in the URL, so
  Back and links work. Admins only for now.
- `api_files.py` / `files_manager.py`: every request names a share and a path
  inside it (`clean_relative_path`, no `..`); never a path on the NAS.
  Listing uses the helper's `find` rule from "Get files".
- Reading goes through a new helper operation `alvaos-priv read-file PATH`:
  it opens the file without following a final symlink and checks, on the
  open file descriptor, that it is a regular file below `/mnt/alvaos`. So a
  share user who swaps a folder for a symlink to `/etc/shadow` gets nothing.
- Downloads and previews use short-lived links (`POST /api/v1/files/link` →
  `/api/v1/files/get/<token>`, 10 minutes, bound to one file), so the browser
  streams big files itself instead of holding them in memory. Responses are
  `nosniff`, sandboxed by CSP (except PDFs, which the browser viewer needs),
  and only images, audio, video, PDF and text are ever shown inline; HTML, SVG
  and scripts always download.
- **Note for next time:** next steps for Files are upload, rename, move,
  delete (with the same symlink-safe checks), folder download as zip, and
  per-person access with the share rights. No Range requests yet, so seeking
  in a long video restarts it from the start.

## 2026-10-03 · Let hard disks sleep

- Settings › Power › Hard disks: "Let hard disks sleep" never (default) or
  after 10, 20, 30 or 60 minutes. Applies to spinning data disks only (lsblk
  `ROTA`, now in the disk list as `rotational`); SSDs, USB disks and the
  system disk are left alone. The line below says how many disks it applies
  to.
- `disk_power.py` sets it with `hdparm -S` when saved and again at every start
  (disks forget it without power). The helper allows only `hdparm -S
  <0|120|240|241|242> <data disk>`; everything else hdparm can do is denied.
  `hdparm` is now a package dependency and installed by the installer.
- SMART checks already use `-n standby`, so they do not wake sleeping disks.
- **Note for next time:** check on real disks that they really sleep (Btrfs
  commits and Docker logs on the pool can keep them awake).

## 2026-10-03 · Apps: own icons

- Every catalog app has its own icon: a colour and a symbol for what it does
  (Jellyfin play, Immich photo, Vaultwarden key, Pi-hole blocked shield, ...),
  drawn with the bundled Lucide set (`APP_MARKS` in `apps.js`). Brand logos
  are not shipped on purpose (their licences). Shown in the store, the
  install dialog and, small, in the installed list.
- Added the Lucide icons cloud, play, image, download, file-text, lock and
  shield-ban to `lucide-icons.js` (same ISC source).
- Apps without a mark (custom compose apps, new catalog entries) keep the
  category icon. A new catalog app needs an entry in `APP_MARKS`.

## 2026-10-03 · Dashboard: Getting started

- A "Getting started" list under the status line with the five things a new
  NAS needs to keep files safe: storage, a shared folder, automatic restore
  points, protection against a failing disk (a redundant pool), and hearing
  about problems (email or Telegram). Each step ticks itself off from the
  real state (`getting-started.js` reads pools, shares, backup settings and
  the notification settings), the next step has the blue button.
- It disappears when all five are done, or for good with "Hide" (stored in
  the browser). Non-admins never see it (the endpoints answer 403).

## 2026-10-03 · Updates: go back by themselves when they fail

- `alvaos-priv apply-update` now also looks in the update cache for the signed
  package of the version installed now (`update_signing.packages_of_version`),
  stages and verifies it exactly like the update, and passes it to
  `apply_update.sh` as the way back.
- `apply_update.sh`: when `dpkg -i` fails, or the backend does not answer on
  `/api/v1/setup/status` within two minutes, it reinstalls that version, puts
  the settings back from before the update, starts the services and says
  "The update did not work. AlvaOS went back to version X." The history shows
  "An update failed; went back to AlvaOS X". Without a way back it says so.
- **Bugs found:** restoring the settings backup after a failed update used
  `rsync --delete` and deleted the whole update cache (the packages to go back
  to); and the error state was written before that restore, so the restore
  brought back "installing" and the Updates page stayed busy forever.
- Checked by running the script with fake `dpkg`/`systemctl`: dpkg fails →
  back to 1.0.0; no answer and no package → clear message; all fine → done.
- **Note for next time:** systems set up by the installer have no package in
  the cache yet (the installer copies files), so their first update has no way
  back. The second item of this roadmap point is still open.

## 2026-10-03 · Problems by email, also when nobody looks; weekly report

- **Bug found:** Telegram alerts were only sent from `GET /api/v1/alerts`,
  i.e. while someone had the web page open. A disk failing at night never
  reached the phone. Now `alert_delivery.py` runs in the background every five
  minutes and sends to every channel that is set up; the API no longer sends.
- A problem is sent when it is still there at the next check (so a short
  spike does not wake anyone), once, and again only if it went away and came
  back. CPU load and memory stay on the dashboard and are never sent.
- **Email** as a channel (Settings › Notifications › Email): send-to address,
  provider (Gmail, Outlook, iCloud, GMX fill in the server) and password;
  "Mail server" is folded away. "Send test email" tries what is typed before
  saving. Login errors say that many providers need an app password. The
  password is never sent back to the page; `alert_delivery.json` is 0600.
- **Weekly report** (on by default, toggle in the same place): Sunday after
  10:00, "all is well" or what needs attention, plus how full each pool is.
- **Note for next time:** try a real Gmail app password and an SMTP server on
  port 465 once.

## 2026-10-02 · Pools: start with a missing disk, then restore protection

- **Decision (safety):** at startup a pool whose normal mount fails is
  mounted with `-o degraded` only when its profile in `pools.json` is
  redundant (raid1, raid1c3, raid1c4, raid10, raid5, raid6) **and** exactly
  one disk is missing (`degraded_mount_allowed`). Anything else stays
  unmounted, as before: no writes to a pool that may be missing data. The
  helper allows exactly `mount -o degraded -U <uuid> <pool mountpoint>`.
- The pool records `mounted_degraded_at`; the page says "AlvaOS started the
  pool without it so your files stay reachable, but it is no longer
  protected. Replace the disk soon." The existing degraded alert and Replace
  button do the rest. The flag is cleared at the next normal mount.
- While degraded, Btrfs writes new data with a single copy. Mixed profiles are
  now detected (`parse_usage_profile_sets`, `unprotected_profiles`;
  `parse_usage_profiles` picks the strongest profile per kind so the pool is
  not shown as "Single"). Once no disk is missing, the pool page offers
  **"Restore protection"** (`POST /api/v1/storage/pools/<id>/restore-protection`
  → `balance start -dconvert=raid1,soft -mconvert=raid1,soft`; the helper now
  accepts `,soft`).
- **Note for next time:** verify on real hardware: pull a disk from a raid1
  pool, reboot, check the page, replace, restore protection.

## 2026-10-02 · Pools: what uses the space

- New section on the pool page, **"What uses the space"**: "Check what uses
  space" measures the pool in the background (`space_report.py`,
  `GET/POST /api/v1/storage/pools/<id>/space`), one pool at a time. It shows
  the biggest folders, the apps (from `apps/`) and the restore points with
  bars, plus "Measured ... ago · Check again".
- Restore points show their **exclusive** size (about what deleting one
  frees, since they share unchanged data). "Free space by deleting restore
  points" lists the biggest with a Delete button: the one cleanup that is
  always safe for the current files.
- Sizes come from `btrfs filesystem du -s --raw` (new helper rule: only these
  flags, 1 to 64 paths, data directories only); the pool's top level is
  listed with the `find` rule from "Get files". The result stays in memory
  until the next check or a backend restart; a failed check keeps the last
  good result.
- **Note for next time:** `btrfs filesystem du` walks every file. On a pool
  with millions of files a check can take long; measure on a real NAS and
  consider running it at night with the health checks.

## 2026-10-02 · Restore points: get single files back

- Each restore point has **"Get files"**: browse the folder as it was, with
  "gone since" on what is not there any more and a filter "Only what is
  gone". "Restore" copies a file or folder back to where it was. Nothing is
  overwritten: if the name is taken, the old one is added beside it as
  "name (restored 2026-10-01 0300).ext". If its parent folder is gone, it says
  to restore that folder instead. "Restore" for the whole folder is now
  "Restore all".
- Backend: `GET /api/v1/backup/snapshots/browse`, `POST
  /api/v1/backup/snapshots/restore-item` (`browse_snapshot`, `restore_item`
  in `backup_manager.py`); only snapshots in the snapshot list are accepted,
  paths are relative without `..`.
- The backend user cannot read share folders (2770), so listing and copying go
  through the helper with two new, exact rules: `find DIR -mindepth 1
  -maxdepth 1 -printf <fixed format>` and `cp -a --reflink=auto --no-clobber
  -- SRC DST`, both only inside the data directories.
- Added the Lucide `file` icon to `lucide-icons.js` by hand (same ISC source).
- **Note for next time:** checked with mocks only; try once on a real NAS that
  ownership and permissions of a restored file are right (`cp -a` keeps them).

## 2026-10-02 · Apps: change folders and ports after installing

- The app inspector menu has **"Folders and ports..."**: the same choices as
  the install dialog (shared folder or a new folder for the app, ports with a
  warning when another app uses one and the next free port named). "Save and
  restart" recreates the containers without pulling
  (`POST /api/v1/apps/<id>/settings` → `AppStore.reconfigure_app`, the update
  worker with `action="reconfigure"`, `pull=False`). Files are not moved; the
  dialog says so.
- An app whose catalog entry is newer than what is installed is told to update
  first: recreating from the newer entry would be an update in disguise.
  Custom compose apps are not offered this.
- Disabled `.btn-primary`/`.btn-secondary` buttons now look disabled
  everywhere (they did not before).

## 2026-10-02 · Restore points: smart retention

- **Smart (recommended)** is the new default for local data snapshots: every
  restore point from the last day, then one per day for a month, one per
  week for 3 months, one per month for a year (`smart_keep` in
  `backup_manager.py`). The newest and entries without a readable time are
  never deleted.
- "Only the newest ones" keeps the old `keep_last` behaviour; the number field
  only shows in that mode. Settings saved before this change keep counting,
  so an update never deletes restore points someone chose to keep.
- **Bugs found:** `_normalize_settings` used a shallow copy of
  `DEFAULT_SETTINGS`, so saving settings changed the defaults in memory; and
  `save_settings` replaced a whole section, so a client that left out a field
  reset it. Now deep copy and a merge per section.

## 2026-10-02 · Pools: use the extra space, mirror the folder structure

- **Use the extra space:** after a disk was replaced with a bigger one, the
  pool page says how much is unused and offers "Use the extra space"
  (`POST /api/v1/storage/pools/<id>/grow` → `btrfs filesystem resize
  <devid>:max` per member that has more than 1 GiB to gain). The privilege
  policy allows only `DEVID:max` on a pool mountpoint.
- **Mirror the folder structure:** pools whose metadata is RAID5/6 (made
  before AlvaOS mirrored metadata) get an offer to convert it
  (`POST .../mirror-metadata` → `balance -mconvert=raid1`, or `raid1c3` for
  RAID6) in the background.
- **Bug found:** the RAID level was guessed by substring over `filesystem
  usage`, so a RAID5 pool with RAID1 metadata could show as RAID1. Now
  `parse_usage_profiles` reads the Data/Metadata/System lines separately;
  the API returns `raid_level` and `metadata_profile`.
- **Note for next time:** both actions are only tested with mocks; check
  them once on a real pool (grow after a real disk swap).

## 2026-10-02 · Installing an app: your folders, free ports, fewer fields

- **Where it keeps its files:** the install dialog sorts an app's folders.
  Folders with your own files (media, photos, downloads, documents) get a
  choice of your shared folders, preselected by name (Jellyfin's media →
  "Media", Immich's uploads → "Photos"), or "A new folder for this app".
  Settings, caches and databases go to `pool › apps › <app>` with one line
  saying so. Pools show their free space; the system disk is not offered.
- **Ports:** "Advanced: ports" lists the app's ports. When one is already used
  by an installed app it opens by itself and suggests the next free one
  ("8081 is used by Vaultwarden, so 8082 is suggested"); sent as
  `port_mappings`.
- **App settings** (environment values) are folded behind "Advanced: app
  settings". It only opens when something must be typed; generated
  passwords are mentioned in one line. `TZ` defaults to the browser's time
  zone instead of UTC.
- **Backend:** `/apps/install` now refuses a `pool_path` that is not a managed
  pool and any `volume_mappings` folder outside the pools, and checks port
  numbers (`validate_install_paths`, tested). The privilege helper already
  refused system paths in compose files; this says no earlier and clearer.
- **Fixed on the way:** the install progress of an earlier app stayed visible
  in the next install dialog and showed "undefined%"; the status poll of an
  old install kept running. The dialog starts clean and an old poll stops.
- Checked in a browser: Jellyfin gets `/media` → the "Media" share, Nextcloud
  moves from 8081 to 8082 because of a clash, at desktop and phone width.
- **Note for next time:** on a NAS, check that a share used by an app keeps
  its permissions (the container writes as its own user; Jellyfin only needs
  to read, Immich needs to write).

## 2026-10-02 · Settings page: state first, one action per row

- **"System" is now "Settings"** in the navigation and on the page (the file
  stays `system.html`, so links keep working). Tabs: Network, Time,
  Security, Notifications (was Alerts), Power, Diagnostics (was Logs); the
  hash names are unchanged (`#security`, `#alerts`, `#logs`).
- **One pattern everywhere:** groups of rows, each with a name and one
  sentence on the left, the current value in the middle and one action on
  the right. Technical details (subnet, gateway, DNS, battery values) are
  under "details"; the system log only opens on request. On a phone the
  value and action go under the text.
- **Network:** Name (Change), "Open AlvaOS" with the address to type
  (`name.local:port`) and Copy, the address from the router.
- **Security:** Admin password (Change), two-step sign-in and SSH with On/Off
  and Turn on/off, then the signed-in devices.
- **Power:** Restart and Shut down as calm buttons with a clear confirm
  ("Shares and apps are away for a minute or two"), no red header. Battery
  backup in one sentence ("Shuts down at 20%").
- **Found:** there was no way to change the admin password after setup; the
  account menu's "Password and security" led nowhere useful. New
  `POST /api/v1/auth/password` checks the current password (rate limited
  like login), sets it with chpasswd and Samba, keeps the 2FA secret in
  `auth.json`, and signs out every other session (this one stays). Dialog
  with live hints in Settings › Security.
- Plain words: On/Off instead of Enabled/Disabled, "Restarted" / "Could not
  restart" instead of FIXED/FAILED; watchdog error text is now escaped.
- Tests: password change (wrong current, too short, success, 2FA kept,
  other sessions signed out) in `test_api.py`. All six tabs and the password
  dialog checked in a browser with mocked data, desktop (dark) and phone
  (light), no sideways scrolling.

## 2026-10-02 · Apps: honest status, Open right away, what an app needs

- **Installed list:** each app says Running, Stopped or "Not fully running"
  (amber, when only some of its containers run; before it said Running), and
  instead of "1/2 containers running" either "Open at nas:8096" with an Open
  button, "Not running" with Start, or "Part of it stopped. Restart it in the
  details." "Update ready" shows on the card. The details panel uses the same
  status words.
- **Store:** every card says what the app needs before installing ("Opens
  on port 8096 · 3 folders on your storage") and warns when a port is already
  used by an installed app. Installed apps show "Installed" (opens it)
  instead of a second Install. "vlatest" is gone; versions only show when
  they are real.
- **Found:** no app icons are shipped (`apps/icons/` does not exist), so
  every store card showed a broken image with its alt text. The category
  icon is now always there; a real icon covers it only once it loads.
- **Backend:** `/apps/available` includes `needs` (ports with protocol and
  description, folder descriptions) from each app's `config_schema`
  (`summarize_app_needs`, tested against the whole catalog).
- Checked in a browser with mocked apps and containers, desktop and phone,
  both themes.

## 2026-10-02 · Sign-in page in the new look

- "Sign in to alva-home" (the name this browser remembers; "AlvaOS"
  otherwise), the AlvaOS mark and the soft background of the setup wizard,
  "Password" instead of "Root Password", one wide Sign in button. Both themes.
- Errors in plain words: wrong password ("Check Caps Lock"), wrong code
  ("Codes change every 30 seconds"), too many attempts, NAS not answering.
  The field is selected again so retyping is quick.
- 2FA: one big code field that sends itself after six digits (typed or
  pasted); "Use a different password" goes back.
- Log out now lands on the sign-in page with "You are signed out."
  (`?reason=signed-out`); `?reason=expired` and `?next=page.html` are
  understood for later use. A hidden username field lets password managers
  save the login.
- Checked in a browser: wrong password, wrong code, auto-submit, success, at
  desktop (dark) and phone (light) width.

## 2026-10-02 · Where am I signed in? Sign out everywhere else

- **System › Security › Signed in:** every session with device ("Safari on
  iPhone"), address, last use and when it signed in; "This browser" marked.
  Sign out one, or "Sign out everywhere else" (confirm dialog).
- **Backend:** sessions now record `created_at`, `last_seen_at`, `ip` and a
  plain device name from the User-Agent (`describe_device`). Last use is
  written at most every 5 minutes, so an open dashboard does not write the
  sessions file on every poll. The list never contains a token: sessions are
  identified by a public id (first 16 hex of SHA-256 of the token).
  `GET /api/v1/auth/sessions`, `DELETE /api/v1/auth/sessions/<id>` (not the
  current one: that is Log out), `POST /api/v1/auth/sessions/revoke-others`;
  admin only, CSRF like every change.
- **Tests no longer write `/var/lib/alvaos/sessions.json`:** an autouse
  fixture in `conftest.py` points the sessions file to a temporary folder
  (verified: the file is not created by a full test run). Removed from the
  roadmap.
- Tests: device names, listing without secrets, revoking one/others/current,
  last-use throttling, API incl. CSRF. Checked in a browser at desktop and
  phone width.

## 2026-10-02 · Disks test themselves, and say when they are failing

- **Found:** SMART was only read when someone opened the Disks tab. A disk
  reporting "failing" raised no alert, and no self-test ever ran.
- **Self-tests:** the health-check thread also starts SMART self-tests on pool
  disks at night: quick weekly and full monthly by default ("Quick weekly
  only" and "Off" in Storage › pool › Activity). One full test per night,
  never on a pool that gets a data check that night. Disks are tracked by
  serial number, since names like sdb can change.
- **Readings** once a night, with `smartctl -n standby`, so a sleeping disk is
  skipped instead of woken. ATA and NVMe are parsed (`parse_smart`).
- **Alerts:** red "Disk … is failing" when the disk says so or a self-test
  failed; amber "Disk … needs attention" for unreadable sectors (pending,
  offline uncorrectable, NVMe media errors) and when reallocated sectors grow
  beyond the count first seen. Old, stable reallocations do not nag.
- Disks tab: "Self-test passed 3 Oct" / "Self-test running" / "Last
  self-test failed" per disk.
- **Privilege helper:** smartctl now also accepts exactly `-t short|long`,
  `-l selftest` and `-n standby`; anything else stays refused (tests).
- **Fixed on the way:** saving the data-check setting would have dropped
  other settings in the same file; settings are merged now.
- Tests: parsing (ATA, NVMe, standby), problem rules, scheduling (night only,
  one long test per night, not during a data check, running tests, off),
  baseline, alerts, policy. Checked in a browser: both settings save.
- **Note for next time:** check on real disks (SATA, USB through a bridge,
  NVMe) that `-t short` starts and that the JSON has the self-test log. A
  USB bridge without SAT support cannot run self-tests; it is then simply
  reported as "did not accept a self-test" in the log.

## 2026-10-02 · Storage that looks after itself: nightly data checks

- **Found:** the welcome screen promises regular checks, but a data check
  (`btrfs scrub`) only ran when someone pressed "Check data".
- **`health_checks.py`:** a backend thread looks every 10 minutes and, between
  03:00 and 06:00, starts a data check for a pool whose last check (as btrfs
  itself records it in `btrfs scrub status`) is older than the interval:
  monthly by default, or weekly, or off. One pool at a time, never while a
  disk is replaced or data is balanced. The decisions are pure functions with
  tests; the btrfs calls are passed in from `api_storage`.
- It records the last result per pool in `/var/lib/alvaos/health_checks.json`
  (written only when something changed, so the system disk is not woken every
  pass). `/system/info` includes it per pool; the alerts read it without
  running btrfs.
- **Alerts:** blocks that could not be repaired → red "The data check found
  damaged files" (restore from backup); repaired blocks → amber "A disk
  returned bad data" (often a disk wearing out). Both link to the pool.
- **Dashboard:** the Storage card says "Data checked 12 days ago: no problems"
  (or what was repaired or damaged, or "Checking data now (45%)"), and turns
  amber or red accordingly.
- **Storage › pool › Activity:** "Check data automatically: Monthly
  (recommended) / Weekly / Off" with when it runs. `GET/POST
  /api/v1/storage/health-checks`.
- **Fixed on the way:** `alvaos-backend.py` used `platform` without importing
  it in the new startup code; mypy caught it before it could crash the
  backend on a NAS.
- Tests: `test_health_checks.py` (time parsing, night window incl. wrap past
  midnight, due rules, one at a time, busy pools, off, settings validation,
  write-only-on-change, unreadable pools, alerts). Checked in a browser with
  mocked data: dashboard card, pool detail and saving the setting.
- **Note for next time:** check on a NAS that a scheduled scrub starts at
  night and that `btrfs scrub status` "Scrub started" parses with the
  installed btrfs-progs version (the format has changed between versions).
  If it does not, the scheduler falls back to the time it started the check
  itself (`started_by_scheduler`), so an unreadable format can never start a
  check every night; that case has a test.

## 2026-10-02 · Setup wizard: "Set up the rest myself"

- Once the admin password is set, the storage and shared-folder steps show a
  quiet "Set up the rest myself" link (bottom left; on a phone below the
  buttons) that goes straight to the dashboard. "Skip for now" still skips
  just the current step. Not shown before the password exists, so no NAS is
  left without one. The dashboard picks up what was skipped as next steps.
- Copy fix: the erase note now speaks of "this disk / it" for a single disk.
- Checked in a browser: hidden on welcome and password, shown on storage,
  leads to `/`, at desktop and phone width.

## 2026-10-02 · A welcome that feels like a start; honest RAID choices

- **Welcome screen** of the setup wizard: the AlvaOS mark builds up layer by
  layer, "Welcome to AlvaOS", one sentence, three promises with icons
  (storage that looks after itself, one folder on every device, backups also
  at a friend's), "About two minutes · Nothing is erased without asking you"
  and one big Get started. A soft colour wash in the background (both
  themes), no progress bar on this first screen. No motion with
  `prefers-reduced-motion`.
- **Wizard storage step:** with three or more disks it also offers "Three
  copies" (RAID1c3: two disks can fail). Usable space is now computed the way
  Btrfs fills disks of different sizes: min(total / copies, total minus the
  copies-1 largest disks). RAID10 and parity stay on the Storage page.
- **Found:** pools with parity were created with parity metadata too
  (`-d raid5 -m raid5`). Btrfs still does not recommend parity for metadata
  (write hole). Now `-m raid1` for RAID5 and `-m raid1c3` for RAID6
  (`mkfs_profile_args`), and unknown profiles are refused by the API before
  the privilege helper. RAID0/RAID10 need at least two disks.
- **Storage page:** layouts in plain words, mirrored first and recommended,
  parity marked experimental with what that means (UPS and backups), no more
  "[WARNING] High Risk" text.
- Tests: profile mapping and unknown profile refusal in
  `test_storage_disks.py`. Wizard walked through with two, three and no free
  disks at desktop and phone width in both themes.
- **Note for next time:** existing RAID5/6 pools keep parity metadata; a
  `btrfs balance start -mconvert=raid1` (policy already allows `-mconvert`)
  could be offered for them on the pool detail page.

## 2026-10-02 · Setup wizard that ends in a usable NAS

- Five steps instead of three, and the NAS can be used at the end:
  1. **Welcome** in two sentences.
  2. **Name and password:** NAS name (suggested from the current hostname,
     cleaned up as you type), admin password with live hints (length, too
     easy, typed twice), time zone taken from the browser with a Change link
     to every IANA zone (`Intl.supportedValuesOf`) instead of 47 hard-coded
     ones. Continue completes setup (`/setup/complete`), then renames the NAS.
  3. **Storage:** only empty disks are offered, all preselected. Two or more:
     Mirrored (recommended, shows usable space) or Use all space. One: a note
     that a second disk can mirror it later. Storage from an earlier install
     can be kept (import). No free disk: Look again or skip. The button says
     "Erase and set up storage" and the note above it says what is erased.
  4. **Shared folder** "Files": "Me, with a name and password" (recommended,
     creates a person, admin password by default) or "Everyone on my home
     network" (guest). "Keep a restore point every day" is on by default and
     turns on daily backups of that folder (30 kept).
  5. **Done:** what was set up, what is left for later, and the addresses to
     type on Windows and Mac.
- Everything after the password can be skipped; the dashboard already shows
  missing storage and backups as blue next steps.
- **Backend:** `/setup/status` suggests the hostname before setup (not after).
  `/setup/complete` now rejects time zones that are not IANA names present in
  `/usr/share/zoneinfo`; before, any string went to `timedatectl`.
- The wizard uses only existing endpoints (disks, pools, import, users,
  shares, backup settings, hostname) with the session it gets from setup.
- Tests: setup status and time zone cases in `test_api.py`. Walked through in a
  browser with a mocked backend (two empty disks, no free disk) at desktop and
  phone width, in both themes; checked the exact requests (token and CSRF on
  each, the right pool, share and backup payloads).
- **Note for next time:** never run on real disks. On a NAS check: pool
  creation from the wizard, the share opening from Windows with the new
  person, the hostname change (Samba and mDNS name), and importing a pool
  from a previous install.

## 2026-10-02 · Light theme

- AlvaOS now has a light theme. **Easy:** it follows the device (light or
  dark, and switches live when the device does). **Powerful:** the account
  menu has Appearance: Auto, Light, Dark, remembered per browser, so a phone
  and a desktop can each look right. Login and setup follow it too.
- `theme.js` is loaded in `<head>` of every page before `styles.css` and sets
  `data-theme` and `color-scheme` on `<html>`, so there is no flash of the
  wrong theme. Other tabs follow a change.
- All fixed colours became tokens: `--track`, `--track-soft`, `--hairline`,
  `--scrim`, `--shadow-sm/md/lg`, `--on-accent`, `--accent-primary-soft`,
  `--accent-danger-soft`, `--accent-primary-ring`. `:root[data-theme="light"]`
  only redefines tokens (GitHub-light palette, darker accents for contrast).
  Consoles, logs and the compose editor stay dark on purpose
  (`--console-bg/-fg`). Translucent accent tints in page styles were left as
  they are; they work on both backgrounds.
- **Fixed on the way:** the phone menu button from the navigation work used
  `.menu-btn`, a class the Apps page already uses for its "..." menus, so the
  hamburger showed on desktop there. It is now `.nav-menu-btn`.
- Checked in a browser at desktop and phone width in both themes (dashboard in
  three states, navigation, Updates, Backup, Apps, System, Login), plus:
  device light, pick Dark, reload keeps Dark, Auto returns to light.
- DESIGN.md and README describe both themes.
- **Note for next time:** new styles must use tokens, never fixed colours;
  check a new page with `colorScheme: 'light'` in Playwright.

## 2026-10-01 · Automatic updates and the watchdog really run

- **Found:** `alvaos-update-checker.service` was a oneshot enabled for boot
  only, with no timer, so "look for updates" and "install automatically" only
  happened after a restart. A new AlvaOS version also raised no
  notification (only Debian packages did). `alvaos-watchdog.service/.timer`
  were never installed by the package or the installer, pointed at
  `/opt/alvaos/backend/` (the code lives in `/opt/alvaos/bin/`) and
  `watchdog_manager.py` had no entry point, so the documented auto-restart of
  Samba, NFS and Docker never ran.
- **Update checker:** new `alvaos-update-checker.timer` (15 minutes after boot,
  then daily with up to 2 h random delay, catches up after downtime). The
  postinst disables the old boot-only service and enables the timer. A new
  AlvaOS version now raises one notification per version ("AlvaOS 0.10.0 is
  ready") unless it is installed automatically.
- **Watchdog:** installed and enabled (every 5 minutes) by the package and the
  installer. Decision: it only restarts a service that is *enabled* but not
  running. A disabled or missing service (no NFS shares, Docker not
  installed) is left alone and shown as "Off" instead of a red "Stopped" on
  the System page.
- Updates > Settings now says what really happens ("Once a day and after a
  restart").
- Tests: `test_watchdog.py`. Unit files checked with `systemd-analyze verify`.
- **Note for next time:** check on a NAS after installing the package that
  `systemctl list-timers` shows both timers, and that the watchdog log
  (`/var/log/alvaos/watchdog.log`) stays quiet when everything runs.

## 2026-10-01 · Updates page: one sentence, what's new, one button, a way back

- **First screen:** "AlvaOS 0.9.0 is up to date" with Check now, or
  "AlvaOS 0.10.0 is ready to install" with Install update and the release
  notes (Markdown headings and lists shown as text, never as HTML). Progress
  and the reconnect after the restart happen on the page itself instead of a
  full-screen overlay; a failed install says so and offers Try again.
- **System packages** in one sentence with Install all; single packages under
  "Choose packages"; a Debian release upgrade only shows when one exists.
- Folded away: **Go back to an earlier version** (only shown when there is
  one), **Install from a USB stick** (AlvaOS and Debian packages in one list),
  **History** in words ("AlvaOS 0.9.0 installed, yesterday") and
  **Settings**, saved on change. The tabs are gone.
- **Rollback backend:** `GET /api/v1/updates/rollback` lists earlier signed
  packages still in `/var/lib/alvaos/updates/`; `POST` installs one through
  the same signed path as an update (the helper checks the signature). Only
  versions from that list are accepted. `apply_update.sh` now writes the
  package version into the history.
- **Found:** `UPDATE_STRATEGY.md` promised an automatic rollback with health
  checks that does not exist. The document now describes what really
  happens; the missing part is in the roadmap.
- **Fixed on the way:** a mypy error in the dashboard I/O counters from the
  first commit of this session (CI would have been red), and a recursion in
  `notifications.js` (`window.setUpdateIndicators` wrapped a top-level
  function of the same name, which is the same global).
- Tests: `test_update_rollback.py`. Checked in a browser with mocked data
  (update ready, up to date with Debian release upgrade, all sections open)
  at desktop and phone width.
- **Note for next time:** never run against a real update. Check on a NAS
  that going back to an older package works with `dpkg -i` (downgrade) and
  that the page reconnects after the restart.

## 2026-10-01 · Navigation: NAS name and status, a real phone menu

- **Brand block** (top left): the AlvaOS mark, the NAS name (hostname,
  remembered in the browser) and its status: Online, Update available, Worth
  a look or Needs attention. On the dashboard it follows the status line; on
  other pages it is read from the alert summary every two minutes. Clicking it
  goes to the dashboard.
- **Sidebar** in a fixed order: Dashboard, Storage, Apps, Backup, Updates,
  and System at the bottom. Backup now has its own icon (shield) instead of
  a second set of arrows next to Updates.
- **Top bar:** the page title, the notification bell and an account button
  (signed in as Administrator, Password and security, Log out). Breadcrumbs,
  the clock and the "Online" pill are gone; Log out moved into the account
  menu.
- **Phone:** the sidebar is a drawer behind a menu button instead of the
  grid of buttons above the page; Escape, the backdrop or a link closes it.
- Toasts no longer cover the top bar: below it on desktop, at the bottom on
  a phone.
- `system.html#security` (and every other System tab) can be linked, like
  the Storage tabs.
- The sidebar and top bar are identical static HTML on all six pages; the
  generator lived in a scratch script, so edit all six by hand (or with a
  script) and keep them identical. `nav.js` only adds behaviour.
- Checked in a browser with mocked data at desktop and phone width on the
  dashboard, Storage, System and Updates pages.
- **Note for next time:** the account menu says "Administrator" because
  login has a single admin password. When people from the People tab can
  sign in (AlvaOS Files), show their name here.

## 2026-10-01 · Dashboard: one status line, four cards, quiet live numbers

- **Status line** at the top: "Everything is fine", or the one most important
  thing with a button to the page where it is fixed. Further items are listed
  below it (at most four, "and N more"). Every card and the alerts feed it a
  list of issues with a level: `bad` (red, action required), `warn` (amber),
  `setup` (blue, "Next step: set up storage / turn on backups" on a new NAS)
  and `info` (blue, e.g. an update is ready). The top bar dot and its label
  ("Online" / "Worth a look" / "Needs attention") follow the status line.
- **Cards** for Storage (free space, one bar per pool), Backup (last backup
  in words, next run, buddies online/offline), Apps (each app running /
  stopped / not fully running) and Updates. Each card is a link to its page.
  The separate Alerts card is gone: alerts now feed the status line.
- **Decisions:** a stopped app is a choice, not a problem, so it is grey and
  does not raise the status. Only an app with some containers stopped is
  amber. A NAS without pools or backups is not "warning" but a blue next
  step. CPU and memory alerts point at the dashboard itself, so their button
  opens the live details instead.
- **Live resources:** one quiet strip with CPU, memory, network and disk
  activity. Clicking it opens a 4-hour history (CPU, memory, network, CPU
  temperature) and the hardware details. `/system/info` now returns
  cumulative network and disk counters (`io`); the page turns two samples into
  a rate. Loopback and Docker bridges are not counted as network traffic.
- **Notifications:** only warnings and errors are kept in the bell list
  (success toasts are feedback, not news). Old entries expire: read ones
  after 7 days, all after 30, in the backend and for local entries. The
  badge shows at most "9+", and is blue unless something unread needs action.
  The list is split into New and Earlier.
- **Code:** dashboard logic moved from the shared `app.js` (loaded on every
  page) into `dashboard.js`; the old hero/focus-card CSS was removed.
- Tests: `test_notifications.py` (expiry, dedupe, feed counts, I/O counters).
  Checked in a browser with mocked API data (all fine, several problems, a
  fresh NAS) at desktop and phone width, no console errors, no sideways
  scrolling.
- **Note for next time:** not yet opened against a real backend. Check on a
  NAS that the network rate looks plausible (bonded or VLAN interfaces are
  counted once each) and that `/containers` failing (Docker off) shows
  "Could not check which apps are running" rather than all apps stopped.
  The degraded-pool state on the Storage card is read from the alert id
  `pool-<id>-degraded`; keep that id stable.

## 2026-09-30 · Shares and people: say who can open what ([#4](https://github.com/SnowTimSwiss/AlvaOS/pull/4))

- **Found:** share names and the NFS "allowed hosts" went unchecked into
  `smb.conf` and `/etc/exports`, so a name like `x]` plus a new line, or hosts like
  `*(rw,no_root_squash)`, added config. Share paths could be any folder
  that was not on the system disk. Share accounts were created with
  `/bin/bash`, so with SSH enabled they could log in to the NAS.
- **Backend:** `validate_share_request` checks name (unique, safe
  characters), place (inside a managed pool), people (must exist) and NFS
  clients; a share can create its own folder (`new_folder`). Exports are
  written per client with `root_squash`; the helper refuses
  `no_root_squash`. The access endpoint also switches between people and
  everyone (guest, optionally read only). Share accounts get no home and no
  shell (`useradd -M -s /usr/sbin/nologin`); existing ones are fixed with
  `usermod -s /usr/sbin/nologin` at startup.
- **Shares tab:** one card per share: where it lives (`main › Media`), who
  can open it in one sentence, the address to type with a Copy button, and
  How to connect / Access / Stop sharing. "Share a folder" asks name, where
  (new folder by default) and who; SMB/NFS and allowed computers sit under
  "More options". A person can be added right inside the dialog. "How to
  connect" gives copyable addresses for Windows, macOS, Linux and phones.
- **Users tab (People):** each person with the shares they can open (read
  or edit), Change password and Remove; removing names the shares they lose.
- Tests: `test_shares.py` (validation, config injection, exports, access
  changes, shell migration) and policy cases. Checked in a browser with
  mocked data at desktop and phone width.
- **Note for next time:** never run against real Samba/NFS here. Check on a
  NAS that a guest share opens without a password from Windows and macOS,
  and that a person with "Can read" really cannot write. Old shares whose
  access list is empty now say "Nobody can open it yet", which matches
  what the file permissions already allowed.

## 2026-09-30 · Storage: pools at a glance, replace and data check ([#4](https://github.com/SnowTimSwiss/AlvaOS/pull/4))

- **Pools tab:** one card per pool with a usage bar (used, free, size), the
  protection in plain words ("Mirrored: one disk can fail"), its disks with a
  health dot, running jobs with progress, and the actions that matter now:
  Add disk, Replace disk (when degraded or a disk reports errors), Import.
- **Pool detail** (`storage.html#pool=<id>`, survives a reload): usage first,
  then activity (replace, balance, data check with progress and the last
  result), the disks with SMART and btrfs error counters and Replace per
  disk, folders, and technical details (profile, mount point, UUID, members,
  error counters, Remove pool) folded away.
- **Backend:** `btrfs filesystem show` is parsed per member (devid, size,
  missing), pools report usage in bytes, and there are new endpoints for
  activity, replace (`btrfs replace start -B`, empty target at least as
  large) and data check (`btrfs scrub start -B`, cancel). One long job per
  pool at a time. The helper allows exactly these commands.
- Disk sizes now come from `lsblk -b`, so sizes can be compared.
- Tests: parsers with real `btrfs` output, the new endpoints and policy rules.
  Checked in a browser with mocked data at desktop and phone width; the
  member parsing was checked against a real degraded Btrfs on loop devices.
- **Note for next time:** a redundant pool with a missing disk does not mount
  at boot (`mount` without `-o degraded`), so Replace is only reachable while
  it stays mounted. Mounting degraded automatically needs a policy rule and a
  decision. Replacing with a larger disk does not grow the pool yet
  (`btrfs filesystem resize <devid>:max`). Tests leave sessions in
  `/var/lib/alvaos/sessions.json` on a dev machine; this predates this PR.

## 2026-09-30 · Storage: disks with data are never erased by surprise ([#4](https://github.com/SnowTimSwiss/AlvaOS/pull/4))

- **Found:** "Wipe" was offered on every non-system disk, lazily unmounted
  whatever was mounted and erased it, including members of an active pool.
  Creating or growing a pool ran `mkfs -f` / `btrfs device add` on any disk
  whose top level had no file system, so a USB disk with partitions was fair
  game. "Delete pool" said the data would stay, then ran `wipefs` on every
  member.
- **Disk roles:** each disk now has one role (system, pool, in use, other
  pool, old data, empty) with a plain sentence. Only disks with old data can
  be erased, only empty disks can go into a pool; the API enforces both and
  answers 409 otherwise. Virtual devices (zram, loop, NBD vaults) are hidden.
- **Privilege helper:** `wipefs`, `mkfs.btrfs` and `btrfs device add` refuse a
  device that is mounted, swap, part of a mounted Btrfs pool or held by
  LUKS/LVM/md, read from `/proc` and `/sys`.
- **Remove pool:** keeps the data by default (the pool can be imported
  again); erasing the disks is an explicit choice with the pool name typed.
  A pool that is still shared or busy is not removed.
- **Storage page:** Pools is the first tab, the tab is kept in the URL
  (`storage.html#disks`). Disks are grouped (available, in use by AlvaOS,
  used elsewhere) and each shows only the actions its role allows: Create
  pool / Add to pool, Import pool, Erase disk. The create and expand dialogs
  list only empty disks and say how many were left out. The repeated "pools
  detected" toast is gone; the Pools tab shows the card.
- Tests: `test_storage_disks.py` (roles, endpoints) and new policy cases.
  Checked in a browser with mocked disk data at desktop and phone width, and
  against the real backend in a Linux container.
- **Note for next time:** needs a check on real disks: a pool across two
  disks (both show "Part of the pool"), a used USB disk (offered for erase,
  not for a pool), and Remove pool with and without erasing. Locally,
  `test_backup_manager.py` fails unless `btrfs-progs` is installed.

## 2026-09-29 · Backup page: simple by default

- **Data and System tabs:** the status card now says in one sentence when the
  last backup ran and what is next, and holds the "Back up automatically"
  toggle and interval directly (saved on change). Retention, location and
  folders stay under "More options". A backup that never ran and is not
  scheduled reads "Not set up" instead of "Healthy".
- **Restore points:** listed by folder and date, newest five visible, the rest
  folded away. System support details only show when something is wrong or a
  rollback is pending.
- **Buddy tab:** each buddy card shows the name, Online or Offline, when it
  sends, and two buttons that are always visible: "Send backup now" and "Test
  connection". "Settings" opens three sections: sending (schedule, folders,
  restore points kept, full system), what the buddy may store here, and the
  connection details, with Save and "Remove this buddy" at the bottom. Without
  a buddy the tab offers a single "Add a buddy" button and hides restore. The
  buddy picker in restore only appears with more than one buddy. Tunnel and
  support details moved to "Technical details". Status pills are centred with
  the action buttons.
- Checked in a browser at desktop and phone width with mocked API responses;
  no backend change.
- **Note for next time:** never opened against a real backend. The
  auto-save toggle sends the whole pool form (folders, retention, location),
  so it needs a check on a NAS where folders were never selected.

## 2026-09-29 · Work log started

- Added this file.

## 2026-09-27 · Security hardening, Buddy Backup rework ([#1](https://github.com/SnowTimSwiss/AlvaOS/pull/1))

- **Privilege helper:** the backend's sudo rights are one rule for
  `alvaos-priv`, which checks every command against `backend/priv_policy.py`
  (deny by default, no shells, staged and content-checked config files).
- **Signed updates:** AlvaOS packages are Ed25519-signed and verified before
  install (`RELEASE.md`).
- **Buddy Backup:** encrypted vaults on the buddy (NBD + LUKS2 + Btrfs),
  replicated with `btrfs send -p`: one full transfer, then only changes
  forever. Retention 14 days / 8 weeks / 12 months. Recovery kit for a
  replacement NAS. All peer traffic through the WireGuard tunnel,
  authenticated per buddy. Details in `BUDDY_BACKUP.md`.
- **Backend:** split into Flask blueprints (`api_*.py`), mock responses
  removed, ruff + mypy in CI, tests from ~20 to 259, including a CI job that
  runs the vault chain on a real kernel as root.
- **Note for next time:** Buddy Backup has never run on two real machines over
  the internet, and the Buddy UI was never checked in a browser. Before the
  next release, a signing key must be generated (`RELEASE.md`), otherwise
  installed systems refuse updates.
