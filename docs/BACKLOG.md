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
