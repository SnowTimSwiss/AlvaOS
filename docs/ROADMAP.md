# Roadmap

AlvaOS is a calm NAS OS for storage, apps, and offsite backup.

This roadmap is intentionally practical: fix the things that made real testing
confusing first, then continue with deeper feature testing. It is more of a
prioritized TODO than a traditional roadmap.

> Status legend: 🔴 blocker · 🟠 important · 🟡 polish · ✅ done

---

## Before the first beta

### 🔴 Verified end-to-end run on real hardware
Much of the backend has dev-mode "mock" fallbacks for non-Linux machines. The
full path must be exercised once on a real Debian install before beta:
- Installer image → clean system → first login.
- Create pool → install app → start → open Web UI → update → uninstall.
- Buddy Backup → restore (see below).

Track this with a written test checklist so runs are reproducible.

### 🔴 Authentication hardening
- ✅ Password hashing moved from a single SHA-256 to PBKDF2-HMAC-SHA256 with a
  per-install salt; legacy hashes are upgraded transparently on next login.
- ✅ Sessions are persisted to `/var/lib/alvaos/sessions.json` (mode `0600`), so
  a restart or update no longer logs everyone out. `POST /api/v1/auth/logout`
  revokes a token server-side, and the Web UI has a Log out control.
- ✅ CSRF is enforced centrally in a `before_request` hook, so it now covers every
  state-changing route instead of the two that carried the decorator. Exemptions
  are limited to the pre-session handshakes and buddy peer-to-peer traffic.
- ✅ Before setup completes, the API is closed except for the setup handshake
  (it previously served every endpoint unauthenticated), the permissive CORS
  policy is gone, and the backend runs under waitress instead of the Flask
  development server.
- ✅ SSH root login stays off after setup and is opt-in under System → Security.
- [ ] Run a focused external security review over the auth + API surface.

### 🟠 Buddy Backup correctness (the headline feature)
- [ ] Real WireGuard pairing between two instances (the placeholder keypair path
  must never be hit in production).
- [ ] Full restore onto a fresh machine, including a deliberate data-loss test.

### 🟠 Automated tests
- ✅ Initial pytest suite for pure logic (password hashing, common helpers).
- [ ] Smoke tests for the critical managers (storage / docker / backup) behind a
  Linux/root marker so they are skipped safely elsewhere.
- ✅ `pytest` runs in CI on push / PR, alongside three guardrails: the sudoers
  allow-list check (previously release-only), a check that the Web UI loads no
  external assets, and a check that no catalog app can ship a placeholder secret.

---

## Polish (nice before a public beta)

- ✅ Applied the "No Fear UX" pass to Storage, Backup, System and Updates:
  load failures are calm, explain what is unaffected, and offer a retry instead
  of red text.
- ✅ No app can deploy with a literal `CHANGEME` secret. Required fields are
  enforced in the install form, in `AppStore.install_app`, and as a last resort
  in the compose builder; secrets the user never types have a Generate button.
- ✅ Dev-mode mock data (fake pools, fake "pool created" responses) is gone —
  non-Linux systems now report honestly instead of inventing hardware.
- ✅ Accessibility pass: landmarks, skip link, ARIA tab pattern with arrow-key
  navigation, decorative icons hidden from screen readers.
- ✅ Mobile pass: single-column cards, scrollable tabs, sheet-style modals,
  horizontally scrolling tables, `prefers-reduced-motion` support.
- 🟡 A light theme (the palette is already tokenised, so this is a token swap).
- ✅ Documentation (ARCHITECTURE / README) aligned with the actual stack
  (Python + Flask, vanilla JS, session tokens).

---

## Later

- ZFS as a storage alternative.
- Plugin system for apps and integrations.
- Basic VM support.

Out of scope for now: Kubernetes, desktop environments, cloud/telemetry.
