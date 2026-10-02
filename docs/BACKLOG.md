# Work log

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
