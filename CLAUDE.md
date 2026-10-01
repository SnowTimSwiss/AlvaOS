# Notes for AI agents working on AlvaOS

## Commits and pull requests
- No AI attribution anywhere: no `Co-Authored-By: Claude ...`, no
  `Claude-Session: ...`, no "Generated with Claude Code" in commit messages,
  PR titles, PR descriptions or comments. If a tool adds one, remove it.

## Before you start
- Read `docs/ROADMAP.md` (what to do next) and `docs/BACKLOG.md` (the work
  log: what was done, and "Note for next time" hints).
- Principle: easy by default, powerful when needed. See `docs/DESIGN.md`.

## When you finish
- Add an entry at the top of `docs/BACKLOG.md` and move finished items out of
  `docs/ROADMAP.md`.

## Checks
- `pytest` (needs `pip install pytest flask pyyaml cryptography psutil requests`
  and a writable `/var/lib/alvaos`; `test_backup_manager.py` also needs
  `btrfs-progs`).
- `ruff check backend scripts`, `mypy`, and
  `python3 scripts/ci/check_no_external_assets.py`.
- UI changes: check in a browser at desktop and phone width. Playwright and
  Chromium are usually available; mock `/api/v1/*` with `page.route`.
- The sidebar and top bar are identical static HTML on index, storage, apps,
  backup, updates and system.html. Keep them identical.
