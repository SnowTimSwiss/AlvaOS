# AlvaOS Update Strategy

Safe, simple, and self-hosted system updates with automatic rollbacks.

## Goals
Safe (no breaks), Simple (one-click), Automated (auto-rollback), and User-controlled (no forced updates).

## Update Layers
1. **Base OS (Debian)**: Managed via `apt` through AlvaOS API. Optional auto-security updates.
2. **AlvaOS System**: Backend, Frontend, and Scripts delivered as versioned `.deb` packages via GitHub.
3. **Docker Apps**: Standard Compose-based updates; pulls new images while preserving volumes.

## Process Flow
1. **Check**: Backend queries GitHub Releases API for new versions.
2. **Download**: `.deb` package is downloaded and SHA256 verified.
3. **Prepare**: Snapshot of `/etc/alvaos/` is created for backup.
4. **Install**: `apt install` is executed, and services are restarted.
5. **Verify**: 30s health check runs. **Failure triggers auto-rollback**.

## Rollback & Recovery
- **Automatic**: Restores config snapshot and downgrades package if health checks fail.
- **Manual**: `sudo apt install alvaos-system=PREV_VERSION && sudo systemctl restart alvaos-backend`.
- **Disaster**: Recovery shell via installer USB or restore from Buddy Backup.

## Update Channels
- **stable**: Production-ready releases.
- **beta**: Pre-release testing builds.
- **dev**: Bleeding-edge builds from the `main` branch.

## Security & Packaging
- **Security**: HTTPS only, SHA256 checksums, and admin-only execution. No telemetry.
- **Packaging**: Built via `scripts/package/build-deb.sh`. Contains all system binaries and assets.
- **Offline**: Supports manual `.deb` installation via Web UI or SSH.

## UI & Settings
- **UI**: Clear update notifications, progress tracking, and restart prompts.
- **Settings**: Channel selection, maintenance windows, and event notifications.

**Philosophy**: Updates should be boring. Users should trust that clicking "Update" works.

