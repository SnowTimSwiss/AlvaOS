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
- [ ] Decide whether in-memory sessions are acceptable for beta (every backend
  restart / update logs everyone out) or whether to persist them.
- [ ] Run a focused security review over the auth + API surface (confirm CSRF is
  enforced on every state-changing route).

### 🟠 Buddy Backup correctness (the headline feature)
- [ ] Real WireGuard pairing between two instances (the placeholder keypair path
  must never be hit in production).
- [ ] Full restore onto a fresh machine, including a deliberate data-loss test.

### 🟠 Automated tests
- ✅ Initial pytest suite for pure logic (password hashing, common helpers).
- [ ] Smoke tests for the critical managers (storage / docker / backup) behind a
  Linux/root marker so they are skipped safely elsewhere.
- [ ] Wire `pytest` into CI on push / PR.

---

## Polish (nice before a public beta)

- 🟡 Apply the "No Fear UX" pass done on the Apps page to Storage, Backup, and
  System (no raw error codes, no red buttons without context).
- 🟡 Ensure no app can deploy with a literal `CHANGEME` secret — force the user
  to fill required env fields.
- ✅ Documentation (ARCHITECTURE / README) aligned with the actual stack
  (Python + Flask, vanilla JS, session tokens).

---

## Later

- ZFS as a storage alternative.
- Plugin system for apps and integrations.
- Basic VM support.

Out of scope for now: Kubernetes, desktop environments, cloud/telemetry.
