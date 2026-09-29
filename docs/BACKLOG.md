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
