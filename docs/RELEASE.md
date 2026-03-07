# AlvaOS Release Process

Predictable, well-tested, and easy-to-upgrade releases.

## Versioning & Channels
- **SemVer**: `vMAJOR.MINOR.PATCH` (e.g., `v1.0.0`).
- **Stable**: Production-ready (default).
- **Beta**: Feature previews (`v1.0.0-beta.1`).

## Artifacts
1. **Installer**: `alvaos-installer-{version}.iso` (~500MB) for fresh installs.
2. **Package**: `alvaos-system_{version}_amd64.deb` (~100MB) for updates.
3. **Hashes**: `checksums.txt` for SHA256 verification.

## Procedures
- **Hotfixes**: Branch from tag → fix bug → release `vX.X.(X+1)` → merge back.
- **Beta Testing**: Create pre-release → 1-2 week test period → Release Candidate (RC) → Stable.
- **Rollback**: Pin previous version in README. Users can downgrade via `sudo apt install alvaos-system=PREV_VERSION`.

## Update Mechanism
Users update via **Web UI → Settings → Updates**. The system restarts automatically. For new installs, download the ISO, write to USB, and boot.

---
**Philosophy**: Users should never fear updating their NAS.