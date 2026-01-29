# AlvaOS Quick Reference

## 📦 Artifacts & Files
- **Artifacts**: `installer.iso` (~500MB), `system.deb` (~100MB), `checksums.txt`.
- **System Paths**: `/opt/alvaos` (Binaries/UI), `/etc/alvaos` (Config), `/var/lib/alvaos` (State), `/srv` (Data), `/backup` (Buddy Backup).
- **API**: `http://nas-ip:8080/api/v1/` (`/storage`, `/docker`, `/apps`, `/backup`, `/system`, `/health`).

## 🔄 Update & Build
- **Update Flow**: Check Release API → Download .deb → Verify SHA256 → `apt install` → Health check → (Success or Auto-rollback).
- **Build ISO**: `cd installer && sudo ./build.sh` (Requires `live-build`).
- **Build Package**: `cd scripts/package && sudo ./build-deb.sh`.
- **Release**: Tag `vX.X.X` on GitHub; CI handles builds.

## 🔐 Core Tech
- **Principles**: Stability over features, Boring tech (Debian, Btrfs, Docker, Go, Svelte).
- **Security**: Local auth (JWT), LAN-first, WireGuard encrypted backups, No telemetry.

## 🎯 Roadmap
- [x] Foundation & Docs
- [ ] Backend API & Storage Mgmt
- [ ] Web UI & Docker Integration
- [ ] Buddy Backup
- [ ] v1.0.0 stable Release

## 🐛 Debug & Troubleshooting
- **Build Error**: `rm -rf build/` and retry.
- **Logs**: `sudo journalctl -u alvaos-backend -f`.
- **Manual Rollback**: `sudo apt install alvaos-system=VERSION`.

## 🔗 Project Links
- **Repo**: [SnowTimSwiss/AlvaOS](https://github.com/SnowTimSwiss/AlvaOS)
- **Downloads**: [Releases](https://github.com/SnowTimSwiss/AlvaOS/releases)
- **Docs**: [ARCH](ARCHITECTURE.md) | [BACKUP](BUDDY_BACKUP.md) | [UPDATE](UPDATE_STRATEGY.md) | [REL](RELEASE.md) | [CONTRIB](CONTRIBUTING.md)

*Philosophy: Simple storage. Simple apps. Simple backups.*
