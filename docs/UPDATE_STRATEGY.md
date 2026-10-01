# AlvaOS Update Strategy

Safe, simple, and self-hosted system updates, with a way back when one breaks.

## Goals
Safe (no breaks), Simple (one-click), Recoverable (go back to the previous version), and User-controlled (no forced updates).

## Update Layers
1. **Base OS (Debian)**: Managed via `apt` through AlvaOS API. Optional auto-security updates.
2. **AlvaOS System**: Backend, Frontend, and Scripts delivered as versioned `.deb` packages via GitHub.
3. **Docker Apps**: Standard Compose-based updates; pulls new images while preserving volumes.

## Process Flow (as implemented)
1. **Check**: Once a day (and 15 minutes after boot) `alvaos-update-checker.timer` runs the check; the Updates page can also check now. The backend asks the GitHub Releases API for the newest release on the chosen channel (cached for an hour).
2. **Download**: The `.deb` and its detached `.sig` are downloaded into `/var/lib/alvaos/updates/`. The three newest packages are kept there.
3. **Verify**: The privilege helper checks the Ed25519 signature against the installed key and that the package is `alvaos-system` (`RELEASE.md`).
4. **Install**: `apply_update.sh` runs as a detached systemd unit: it copies `/var/lib/alvaos` to `/var/lib/alvaos.bak`, stops the services, runs `dpkg -i`, runs migrations and starts the services again.
5. **Record**: The installed version is added to the update history.

## Rollback & Recovery
- **From the Updates page**: "Go back to an earlier version" lists earlier signed packages that are still in the update cache and installs one through the same signed path as an update (`GET/POST /api/v1/updates/rollback`). A version that was installed from the installer or a USB stick is not in the cache and cannot be offered.
- **Not yet automatic**: when `dpkg -i` fails or no service comes back, the script reports an error but does not reinstall the previous package by itself. The state copy in `/var/lib/alvaos.bak` is only restored if files went missing.
- **Manual**: `sudo dpkg -i /var/lib/alvaos/updates/<previous>.deb && sudo systemctl restart alvaos-backend`.
- **Disaster**: Recovery shell via installer USB or restore from Buddy Backup.

## Update Channels
- **stable**: Releases (shown as "Stable (recommended)").
- **unstable**: Pre-releases (shown as "Testing").

## Security & Packaging
- **Security**: HTTPS only, Ed25519-signed packages, and admin-only execution. No telemetry.
- **Packaging**: Built via `scripts/package/build-deb.sh`. Contains all system binaries and assets.
- **Offline**: Supports manual `.deb` installation via Web UI or SSH.

## UI & Settings
- **UI**: Clear update notifications, progress tracking, and restart prompts.
- **Settings**: Channel selection, maintenance windows, and event notifications.

**Philosophy**: Updates should be boring. Users should trust that clicking "Update" works.

