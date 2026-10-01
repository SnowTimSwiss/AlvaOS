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
