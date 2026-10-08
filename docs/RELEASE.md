# AlvaOS Release Process

Predictable, well-tested, and easy-to-upgrade releases.

## Versioning & Channels
- **SemVer**: `vMAJOR.MINOR.PATCH` (e.g., `v1.0.0`).
- **Stable**: Production-ready (default).
- **Beta**: Feature previews. The current version is `beta-v0.3.2` (the `VERSION` file, also the release tag). Builds between releases carry the `VERSION` file's version too. The package becomes `0.2.0~beta` (Debian sorts it before `0.2.0`), and updates read it as `0.2.0b`, so `beta-v0.3.0` and `v0.2.0` count as newer.

## Artifacts
1. **Installer**: `alvaos-installer-{version}.iso` (~500MB) for fresh installs.
2. **Package**: `alvaos-system_{version}_amd64.deb` (~100MB) for updates.
3. **Hashes**: `checksums.txt` for SHA256 verification.

## Procedures
- **Hotfixes**: Branch from tag → fix bug → release `vX.X.(X+1)` → merge back.
- **Beta Testing**: Create pre-release → 1-2 week test period → Release Candidate (RC) → Stable.
- **Rollback**: Pin previous version in README. Users can downgrade via `sudo apt install alvaos-system=PREV_VERSION`.

To build a package without publishing a release, open **Actions → Build and
Release AlvaOS Package → Run workflow**. Download the `alvaos-package-0.0.0-dev`
artifact from the completed run. If `ALVAOS_UPDATE_SIGNING_KEY` is configured,
the workflow includes a `.deb.sig` signature. Without the secret, the manual
workflow run still uploads the `.deb` and checksum, but the unsigned package
cannot be installed as an AlvaOS update.

## Update Signing
Every `alvaos-system` package is signed with an Ed25519 key. The release
workflow writes `alvaos-system_{version}_amd64.deb.sig` next to the package, and
installed systems refuse to install an AlvaOS package whose signature does not
verify against `/opt/alvaos/keys/update-signing.pub` (installed from
`keys/update-signing.pub` in this repository).

One-time setup:
1. `python3 scripts/release/sign_update.py keygen`
2. Commit `keys/update-signing.pub`.
3. Store the printed private key as the GitHub secret `ALVAOS_UPDATE_SIGNING_KEY`,
   and keep an offline copy. Losing it means users must reinstall a package with
   a new key by hand; leaking it means anyone can ship "updates".

Offline updates (USB) need the `.deb.sig` file next to the `.deb`.

The check runs in the root-owned privilege helper (`alvaos-priv`), not in the
web backend, so a compromised backend cannot skip it.

## Update Mechanism
Users update via **Web UI → Settings → Updates**. The system restarts automatically. For new installs, download the ISO, write to USB, and boot.

---
**Philosophy**: Users should never fear updating their NAS.
