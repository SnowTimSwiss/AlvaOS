# AlvaOS Update Strategy

This document describes how AlvaOS handles system updates.

## Design Goals

1. **Safe** - Updates should never break the system
2. **Simple** - One-click updates via Web UI
3. **Automatic rollback** - Failed updates revert automatically
4. **Minimal downtime** - Services restart gracefully
5. **No cloud dependency** - Updates from GitHub or mirrors
6. **User control** - No forced updates

## Update Scope

AlvaOS updates are divided into layers:

### 1. Base OS (Debian)

**What:** Debian Stable packages (kernel, systemd, core tools)

**How:**
- Standard `apt update && apt upgrade`
- Managed via AlvaOS API
- User triggers via Web UI
- Security updates can be automatic (opt-in)

**When:**
- On user request
- Automatically if security updates enabled
- During scheduled maintenance windows

### 2. AlvaOS System

**What:** AlvaOS backend, frontend, scripts, and core services

**How:**
- Distributed as `.deb` packages
- Hosted on GitHub Releases
- Versioned (semver: `v1.2.3`)
- Installed via `apt` from AlvaOS repository

**Package:** `alvaos-system`

**Contents:**
- `/opt/alvaos/bin/alvaos-backend` - API server
- `/opt/alvaos/webui/` - Frontend static files
- `/opt/alvaos/scripts/` - System scripts
- `/etc/alvaos/` - Configuration templates
- Systemd unit files

**Update process:**
1. User clicks "Update" in Web UI
2. Backend checks GitHub Releases for new version
3. Downloads new `.deb` package
4. Verifies signature
5. Installs package via `apt install`
6. Restarts services
7. Verifies health
8. If health check fails → rollback

### 3. Docker Apps

**What:** User-installed containers from App Store

**How:**
- Standard Docker Compose updates
- Pull new images
- Recreate containers
- No data loss (volumes persist)

**When:**
- On user request
- Can be automated per-app

### 4. Buddy Backup Configuration

**What:** Backup daemon and protocol

**How:**
- Part of `alvaos-system` package
- Updated with main system

## Update Architecture

### Version Tracking

**Current version stored in:**
- `/etc/alvaos/version.json`:
  ```json
  {
    "alvaos_version": "1.2.3",
    "debian_version": "12.4",
    "last_update": "2026-01-15T14:23:00Z",
    "update_channel": "stable"
  }
  ```

### Update Channels

**stable** (default)
- Stable releases only
- Recommended for production
- Example: `v1.0.0`, `v1.1.0`

**beta**
- Beta releases for testing
- Example: `v1.2.0-beta.1`

**dev**
- Latest builds from main branch
- Not recommended for production

### Update Check API

**Endpoint:** `GET /api/v1/system/updates/check`

**Response:**
```json
{
  "update_available": true,
  "current_version": "1.0.0",
  "latest_version": "1.1.0",
  "release_notes_url": "https://github.com/SnowTimSwiss/AlvaOS/releases/tag/v1.1.0",
  "package_url": "https://github.com/SnowTimSwiss/AlvaOS/releases/download/v1.1.0/alvaos-system_1.1.0_amd64.deb",
  "package_sha256": "abc123...",
  "release_date": "2026-01-20T10:00:00Z",
  "breaking_changes": false
}
```

### Update Process Flow

```
User clicks "Update" in Web UI
           ↓
Backend calls GitHub Releases API
           ↓
Download .deb package to /tmp/
           ↓
Verify SHA256 checksum
           ↓
Create snapshot of /etc/alvaos/ (backup config)
           ↓
Install package: apt install ./alvaos-system_X.Y.Z_amd64.deb
           ↓
Run post-install script
           ↓
Restart alvaos-backend.service
           ↓
Health check (30 second timeout)
           ↓
    ┌─────┴─────┐
    │           │
 Success     Failure
    │           │
    │      Rollback:
    │      - Stop service
    │      - Restore snapshot
    │      - Downgrade package
    │      - Restart service
    │      - Notify user
    │
Update complete
```

### Rollback Mechanism

**Automatic rollback triggers:**
- Backend API fails to start within 30 seconds
- Health check endpoint returns error
- Critical service crashes

**Rollback process:**
1. Stop `alvaos-backend.service`
2. Restore `/etc/alvaos/` from snapshot
3. Downgrade to previous package version
4. Restart service
5. Log rollback reason
6. Notify user via UI banner

**Manual rollback:**
```bash
sudo apt install alvaos-system=1.0.0  # Specific version
sudo systemctl restart alvaos-backend
```

## Update Packaging

### Building AlvaOS .deb Package

**Build script:** `scripts/package/build-deb.sh`

**Process:**
1. Compile backend binary (Go)
2. Build frontend assets (npm run build)
3. Copy files to package structure
4. Create `.deb` with `dpkg-deb`
5. Sign package with GPG
6. Upload to GitHub Release

**Package metadata (debian/control):**
```
Package: alvaos-system
Version: 1.1.0
Architecture: amd64
Maintainer: AlvaOS Team <dev@alvaos.org>
Depends: docker.io, btrfs-progs, systemd
Description: AlvaOS NAS operating system
 Ultra-stable, lightweight NAS OS with storage management,
 Docker apps, and Buddy Backup.
```

### Hosting Updates

**Primary:** GitHub Releases
- All releases published as GitHub Releases
- `.deb` package attached as asset
- Checksums included
- Release notes in Markdown

**Optional:** APT repository
- Future: host `deb.alvaos.org`
- Standard Debian repository format
- For easier `apt update` integration

## Update UI Workflow

### Dashboard Notification

When update available:
```
┌─────────────────────────────────────────────┐
│ 🔔 Update Available: AlvaOS v1.1.0          │
│                                             │
│ Your version: v1.0.0                        │
│ New version:  v1.1.0 (released 2 days ago)  │
│                                             │
│ [View Release Notes]  [Update Now]          │
└─────────────────────────────────────────────┘
```

### Update Progress

```
┌─────────────────────────────────────────────┐
│ Updating AlvaOS...                          │
│                                             │
│ ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░ 50%                   │
│                                             │
│ Step 3/5: Installing package...             │
│                                             │
│ Est. time remaining: 2 minutes              │
└─────────────────────────────────────────────┘
```

### Update Complete

```
┌─────────────────────────────────────────────┐
│ ✅ Update Complete!                         │
│                                             │
│ AlvaOS has been updated to v1.1.0           │
│                                             │
│ Your system will restart in 10 seconds...  │
│                                             │
│ [Restart Now]  [Release Notes]              │
└─────────────────────────────────────────────┘
```

## Security Considerations

### Package Verification

**All packages must:**
1. Be downloaded from GitHub Releases only
2. Have valid SHA256 checksum
3. Be installed via `apt` (no direct extraction)
4. Match expected version string

### Update Authentication

- No authentication required to **check** for updates
- User must be logged in to **install** updates
- Updates require admin privileges

### Network Security

- HTTPS only for update downloads
- Certificate verification enforced
- No update beacons or telemetry

## Offline Updates

**Manual update process:**

1. Download `.deb` from GitHub Releases on another machine
2. Transfer to AlvaOS via USB or network share
3. Upload via Web UI or copy to `/tmp/`
4. Install via API or SSH:
   ```bash
   sudo apt install ./alvaos-system_1.1.0_amd64.deb
   sudo systemctl restart alvaos-backend
   ```

## Update Scheduling

**Settings in Web UI:**

```
Update Settings
───────────────────────────────────────────

Update Channel:        [Stable ▾]

Automatic Updates:     [ ] Enable
                       (Applies security updates only)

Check for Updates:     [Daily ▾]
                       Daily / Weekly / Monthly / Manual

Maintenance Window:    [02:00 - 04:00 ▾]
                       (Updates install during this window)

Notifications:         [✓] Notify when updates available
                       [✓] Email on update completion
```

## Testing Updates

**Before releasing:**

1. Build `.deb` package
2. Test install on fresh AlvaOS
3. Test upgrade from previous version
4. Test rollback scenario
5. Verify all services start
6. Run integration tests

**Beta testing:**
- Community members opt into beta channel
- Test updates 1-2 weeks before stable release
- Gather feedback and fix issues

## Disaster Recovery

**If update completely breaks system:**

1. Boot from AlvaOS installer USB
2. Access recovery shell
3. Chroot into installed system
4. Downgrade package
5. Or: reinstall AlvaOS and restore from Buddy Backup

## Future Enhancements

- **A/B partition updates** - Dual boot partitions for zero-downtime
- **Staged rollouts** - Gradual release to users
- **Update size optimization** - Delta updates instead of full packages
- **Signed packages** - GPG signature verification
- **Update caching** - Local mirror for multiple AlvaOS instances

---

**Philosophy:** Updates should be boring and reliable. Users should trust that clicking "Update" won't break their NAS.
