# Roadmap

AlvaOS is a calm NAS OS for storage, apps, and offsite backup.

This roadmap is intentionally practical: fix the things that made real testing
confusing first, then continue with deeper feature testing.

Its more of a TODO than a ROADMAP in the traditional way.

## Now: Fixes from live testing

### Installer

- [x] Mark default choices clearly in the TUI, for example `DHCP (default)`.
- [x] Make `Cancel`, `Back`, and escape-style exits return one step back instead of throwing an error.
  - Implemented as a step-based wizard loop in `install-system.sh`; cancelling steps back instead of crashing the script.
- [x] After installation, explain exactly when the USB installer medium can be removed.
  - On EFI hardware, the installer now sets a one-shot `efibootmgr --bootnext` to the installed AlvaOS entry, so the next boot goes straight to the installed system regardless of BootOrder; the final message then says the stick can be removed any time and the installer reboots automatically.
  - On legacy BIOS (or if EFI bootnext could not be set), boot order can't be controlled from software, so the installer still shows "Press Enter to shut down, wait until the machine is completely off, then remove the USB installer stick" and powers off (`shutdown -h now`) instead of rebooting.
- [x] Show the final access URL after install for both static IP and DHCP installs.
  - Includes the port explicitly: `http://<ip>:8080`.
  - For DHCP, the installer detects the live network interface's IP and prints it, with a fallback hint if it's not available.
- [x] Review installation duration.
  - Added an explicit "10-15 minutes" expectation to the progress dialog so it's not perceived as stuck on slower hardware/links.

### First-Time Web Setup

- [x] Simplify `setup.html`.
  - Removed the detected-IP / "Ready for Configuration" status noise from the Welcome step (no decision there, just decoration).
  - Kept password configuration and the live password requirements checklist as-is.
  - Kept timezone settings, and made the picker searchable (type-to-filter combobox) instead of a long flat `<select>`.
  - Removed the separate Review & Finish step entirely; the timezone step's "Complete Setup" button finishes setup directly, since review added no real decision (it just re-displayed the password's existence and the just-picked timezone).
- [x] Decide which setup options are actually needed on first boot.
  - Kept to exactly what the backend (`/api/v1/setup/complete`) actually uses: root password and timezone. Everything else (network, storage pools, share users, 2FA, apps) stays out of first-time setup and lives in the main UI after login.
  - Considered also folding storage pool creation into setup, but decided against it: it's a destructive, RAID-dependent decision that's safer to make after authenticating, inside the main UI, where a failure doesn't block getting into AlvaOS at all.

### Authentication and API Reliability

- [x] Fix `Error: CSRF token missing`.
  - Reproduced after reboot and in another browser.
  - This blocks normal use and should be treated as a top-priority bug.
- [x] Clarify user roles.
  - Decided: AlvaOS keeps a single Web UI admin login (root password, optional 2FA). The "Users" feature is actually system/Samba accounts for SMB/NFS share access, unrelated to Web UI login, so the admin/user role field on those accounts was removed (it had no effect on anything). Per-share access is still controlled by the existing read/write/deny permission dropdowns.

### Main UI: Global UX Rules

- [ ] Make every modal and dialog close consistently.
  - `Cancel` must work everywhere.
  - The `X` close button must work everywhere.
  - Closing a dialog should not leave broken state behind.
- [ ] Use one consistent modal/dialog design across the UI.
- [ ] Avoid opening advanced forms by default.
  - Prefer a calm status view with clear action buttons.
  - Advanced or dangerous controls should appear only after the user asks for them.
- [ ] Move validation errors into the form where they belong.
  - Example: pool name errors should appear next to the pool name field, not as a global notification.
- [ ] Replace typed destructive confirmations with explicit checkboxes where appropriate.
  - Example: disk wiping should not require typing `sda`.
  - Example: pool creation should not require typing the pool name again.
- [ ] Refresh the top-left AlvaOS logo treatment.
  - Current version feels too glossy/liquid-glass and does not match the desired direction.

### Dashboard

- [x] Reduce dashboard overwhelm.
- [x] Remove or rethink the `Quick Actions` tile.
  - Removed entirely. Each priority card now links directly to its own page instead of duplicating sidebar navigation.
- [x] Make the dashboard a calm overview, not a control wall.
  - Replaced the CPU/RAM/Storage/Quick-Actions grid + raw alert list + history charts + system-info block with: a one-line health hero, 5 priority cards (Storage, Backup, Alerts, Updates, Apps), a compact CPU/Memory resource strip, and a collapsed "System information" panel.
- [x] Prioritize storage health, backup state, alerts, and updates.
  - Storage card shows a free/total summary plus one usage bar per pool (not just one combined bar). Backup, Alerts, Updates, and Apps each get their own card with a status pill (Healthy/Attention) and a one-line summary; the hero banner reports "N things want your attention" or "Everything looks healthy" based on those cards.
  - Network throughput was left out of the resource strip since the backend doesn't currently expose it; the CPU/Temperature history chart was dropped (the old per-card data is still collected into localStorage but no longer rendered) in favor of the calmer priority-card view.

### Feedback Flow

- [x] Make `Send feedback` feel like sending feedback, not opening a GitHub issue directly.
  - The modal's primary action is now `Send via Email` (mailto: to feedback-alvaos@timserver.uk, prefilled with category/message/page/timestamp). GitHub issue creation is demoted to a small secondary text link below the actions.
- [x] Add a clear close button to the feedback popup.
  - The modal already had a working `X` close button; additionally, the floating `Feedback` button itself now has its own small close control. Clicking it offers `Hide for now` (reappears next session) or `Don't show again` (persisted via localStorage).
- [x] Decide whether GitHub issue creation should be hidden behind an advanced/developer action.
  - Kept as a small secondary link (`Prefer GitHub? Open an issue instead`) rather than removed entirely, since some users will still want public issue tracking, but it's no longer the default/primary action.

### Storage and Disks

- [x] Keep the disk page direction; testing feedback was positive.
- [x] Change disk wipe confirmation from typed disk name to a checkbox confirmation.
- [x] Improve pool creation.
  - Show only RAID levels possible with the selected disks.
  - Add more RAID level options where supported.
  - Put pool name validation inside the form.
  - Replace typed pool-name confirmation with a checkbox or clear final confirmation step.

### Apps

- [x] Keep the app menu and app store direction; testing feedback was positive.
- [x] Move Docker Compose / custom app install behind a `Custom app` button.
- [x] Keep power-user options available, but hidden until needed.

### Backup and Buddy Backup

- [ ] Simplify the backup page.
  - Show status first.
  - Show setup actions as buttons.
  - Do not expose token generation and internal mechanics by default.
- [ ] Make Buddy Backup setup a clear `Set up Buddy Backup` flow.
- [ ] Keep same-system pairing blocked.
  - Testing confirmed that pairing with the same system does not work, which is good.
- [ ] Improve wording around pairing status, errors, and next steps.

### Users

- [x] Do not show the create-user form open by default.
- [x] Replace it with a `Create user` button.
  - Create-user is now a button that opens a modal (same pattern as password reset), instead of an always-open inline form.
- [x] Clarify what admin and user accounts can actually do.
- [x] Align user management with the real login model.
  - Moved out of its own sidebar page into a `Users` tab inside Storage, next to Disks/Pools/Shares, since these accounts only control SMB/NFS share access.
  - Removed the admin/user role field entirely (it never affected anything: Web UI login is always the single admin session, and per-share access is governed by the separate read/write/deny permission dropdowns).

### Updates

- [x] Keep the current update direction; testing feedback was positive.
- [x] Move offline updates into the AlvaOS updates area as a clear button/action.
  - Removed the separate "Offline Updates" tab. The `AlvaOS Updates` tab now has its own "Offline AlvaOS Update" scan/install button, and offline packages are classified by their actual `.deb` package name so it only lists the real `alvaos-system` package there.
- [x] Keep offline update handling visible but not mixed into unrelated system settings.
  - Added a matching "Offline System Package" scan/install button inside the `System Updates` tab for non-AlvaOS `.deb` files (e.g. installing a Debian security update from USB when offline). It installs via `dpkg`/`apt --fix-broken` only, without touching AlvaOS services, backups, or migrations, since the previous single offline path always ran the AlvaOS-specific install script (service stop/backup/migrate) regardless of which package was selected.
- [x] Fix offline update regression that sent the user back into first-time setup.
  - Test result: after an offline update, AlvaOS opened setup again.
  - Setup then failed with `sudo: /usr/bin/sudo is owned by uid 1001, should be 0` and `sudo: a password is required`.
  - Fix: build `.deb` packages with root-owned metadata, keep `/var/lib/alvaos` out of the package payload, preserve setup/auth state during updates, and repair critical sudo ownership during package install/update.

### System Settings

- [ ] Redesign the system tab.
- [ ] Split mixed settings into clear areas, for example:
  - Alerts
  - Security
  - Time
  - Network
  - Power
  - Logs / Diagnostics
- [ ] Do not show Telegram settings open by default. und allgemein einfach die settings und so nicht offen anzeigen
  - Put alert delivery under `Alerts`.
  - Put 2FA under `Security`.
- [ ] Avoid placing raw settings forms directly in the middle of the page.
  - Use status summaries and action buttons first.


### Other things:
- [ ] add factory reset
## Forward after testing fixes

everything will be tested again and again until we go to stable

## Additional items from recent testing

- [ ] Installed apps are good but ports list is confusing; improve UI for ports under system/apps.
UI and UI by apps is not good. confusing too many options for beginners.
- [ ] Snapshot restore fails: error `Could not statfs: No such file or directory`. Investigate and fix.
- [ ] Backup UI not user-friendly; streamline workflow.
- [ ] Overall design inconsistent; audit UI components.
- [ ] Top-left AlvaOS control panel visual redesign needed.

## Wizards

- [ ] Add an **Installation Wizard** that guides users through partitioning, network setup, and optional features with visual progress bars.
- [ ] Implement a **Backup & Restore Wizard** offering step‑by‑step selection of snapshots, destination pools, and validation of restore paths.
- [ ] Create a **First‑Time Setup Wizard** consolidating the initial web UI configuration (admin credentials, network, storage) into a guided flow.
- [ ] Provide a **App Installation Wizard** for custom Docker‑Compose apps, including form validation and dependency checks.
- [ ] Add a **System Settings Wizard** to help users configure alerts, security, time, network, and power options in a linear, user‑friendly manner.

## Notifications
- sollte man anklicken können (nicht alle aber die bei denen es hilft)
- so wie bei truenas ein symbol wo alle letzten fast wie ein log gespeichert sind
- design verbessern
- verschiedene stufen: die die automatisch weggehen und solche die man dismiss muss befor sie einem in ruhe lassen.