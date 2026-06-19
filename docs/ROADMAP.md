# Roadmap

AlvaOS is a calm NAS OS for storage, apps, and offsite backup.

This roadmap is intentionally practical: fix the things that made real testing
confusing first, then continue with deeper feature testing.

## Now: Fixes from live testing

### Installer

- [ ] Mark default choices clearly in the TUI, for example `DHCP (default)`.
- [ ] Make `Cancel`, `Back`, and escape-style exits return one step back instead of throwing an error.
- [ ] After installation, explain exactly when the USB installer medium can be removed.
  - Current testing shows it must stay inserted until shutdown is complete.
  - The final message should say something like: "Press Enter to shut down, wait until the machine is off, then remove the USB stick."
- [ ] Show the final access URL after install for both static IP and DHCP installs.
  - Include the port explicitly: `http://<ip>:8080`.
  - For DHCP, detect and print the assigned IP when possible.
- [ ] Review installation duration.
  - Test result: around 10 minutes on NVMe SSD with Intel N100.
  - Decide whether this is expected, or add better progress information for long phases.

### First-Time Web Setup

- [ ] Simplify `setup.html`.
  - Remove low-value status noise such as detected IP and "ready for configuration".
  - Keep password configuration and password requirements.
  - Keep time settings.
  - Remove or heavily simplify the review and finish page if it adds no real decision.
- [ ] Decide which setup options are actually needed on first boot.
  - Remove unnecessary config fields.
  - Add only settings that are needed before the main UI is usable.

### Authentication and API Reliability

- [x] Fix `Error: CSRF token missing`.
  - Reproduced after reboot and in another browser.
  - This blocks normal use and should be treated as a top-priority bug.
- [ ] Clarify user roles.
  - If login uses the root password, the purpose of separate admin/user accounts is unclear.
  - Decide whether AlvaOS should use local Web UI users, system users, or a clear bridge between both.

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

- [ ] Reduce dashboard overwhelm.
- [ ] Remove or rethink the `Quick Actions` tile.
- [ ] Make the dashboard a calm overview, not a control wall.
- [ ] Prioritize storage health, backup state, alerts, and updates.

### Feedback Flow

- [ ] Make `Send feedback` feel like sending feedback, not opening a GitHub issue directly.
- [ ] Add a clear close button to the feedback window.
- [ ] Decide whether GitHub issue creation should be hidden behind an advanced/developer action.

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

- [ ] Do not show the create-user form open by default.
- [ ] Replace it with a `Create user` button.
- [ ] Clarify what admin and user accounts can actually do.
- [ ] Align user management with the real login model.

### Updates

- [ ] Keep the current update direction; testing feedback was positive.
- [ ] Move offline updates into the AlvaOS updates area as a clear button/action.
- [ ] Keep offline update handling visible but not mixed into unrelated system settings.
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
- [ ] Do not show Telegram settings open by default.
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
