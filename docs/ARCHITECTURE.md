# AlvaOS Architecture

Conservative, proven technology designed for stability and self-hosted, unattended operation.

## Design Principles
- **Stability first**: Proven, conservative tech.
- **Opinionated UX**: Simple, intuitive workflows over complex flexibility.
- **Self-hosted**: No cloud dependencies or telemetry.
- **Reliable**: Designed for years of unattended operation.
- **API-first**: Decoupled UI and system logic.

## System Layers

```
┌─────────────────────────────────────────────────┐
│              Web UI (Frontend)                  │
│         Dark mode, Unraid-inspired UX           │
└─────────────────────────────────────────────────┘
                      ↓ HTTP/REST
┌─────────────────────────────────────────────────┐
│           REST API (Backend)                    │
│      Versioned API, business logic              │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│          System Services Layer                  │
│  Storage │ Docker │ Backup │ Network │ Users   │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│              Base OS Layer                      │
│    Debian Stable + systemd + core tools         │
└─────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│                 Hardware                        │
│          Storage disks, network, etc.           │
└─────────────────────────────────────────────────┘
```

## Core Components

### 1. Base OS (Debian Stable)
Minimal headless install using `systemd`. Deployed via custom debootstrap-based installer for deterministic builds.

### 2. Storage (Btrfs)
Features storage pools (easy disk expansion), snapshots, background rebalancing, and self-healing (checksums). Managed via Web UI with health dashboards.

### 3. Containers (Docker + Compose)
Git-based template repository for one-click app installs. No Kubernetes or complex orchestration.

### 4. Buddy Backup (Core Feature)
NAS-to-NAS encrypted incremental backup. 
- **Setup**: Link devices via pairing codes.
- **Security**: End-to-end encryption (WireGuard based).
- **Scope**: Backs up configs, shares, and app state (not the OS itself).

### 5. Web UI & API
- **UI**: Dark-mode first, responsive, Unraid-inspired. Plain HTML/CSS/vanilla JavaScript — no framework and no build step. Files are served as static assets by the backend.
- **API**: REST API under `/api/v1/`, implemented in Python with Flask. Uses JSON. Authentication is via opaque session tokens (see Security Model), not JWT. The UI never runs shell commands directly — every privileged action goes through the API, which shells out only via an allow-listed sudoers configuration.

## Distribution & Updates
- **Installer**: Minimal (<500MB) debootstrap image (BIOS/UEFI).
- **Updates**: Backend delivered as `.deb`. Web-based updates with automatic rollbacks. No mandatory cloud updates.

## Security Model

**Authentication:**
- Single local admin account (`root`), configured during first-run setup — there are no default passwords.
- Web UI login required for all API endpoints once setup is complete.
- Passwords are stored as PBKDF2-HMAC-SHA256 hashes with a per-install random salt (`/var/lib/alvaos/auth.json`).
- Sessions use opaque random tokens with a 24h TTL. Tokens are held in memory, so restarting the backend (e.g. after an update) logs sessions out.
- Optional TOTP two-factor authentication (RFC 6238, via `pyotp`).
- State-changing requests require a CSRF token bound to the session.
- Login attempts are rate-limited per IP (10 attempts / 15 min).
- Security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`) are added to every response.
- SSH disabled by default (can be enabled).

**Privilege separation:**
- The backend runs as the unprivileged `alvaos` system user.
- System-level operations are executed through an explicit sudoers allow-list (`/etc/sudoers.d/alvaos`); the set of permitted commands is validated in CI (`scripts/ci/check_privileged_commands.py`).

**Network:**
- LAN access by default
- Optional WireGuard for remote access
- Buddy Backup uses encrypted tunnels

**Data:**
- Buddy Backup payloads are encrypted before transfer (passphrase-derived key) and sent over a WireGuard tunnel.
- No telemetry or phone-home.

## Service Architecture

The backend is a single Flask process that serves both the REST API and the static Web UI on port `8080`.

**systemd units:**
- `alvaos.service` — main backend: REST API + Web UI (`python3 /opt/alvaos/bin/alvaos-backend.py`).
- `alvaos-update-checker.service` — periodic update check.
- `alvaos-watchdog.timer` / `alvaos-watchdog.service` — health check with auto-restart of failing services (every ~5 min).
- Standard Docker service for app containers.

**Filesystem layout:**
- `/opt/alvaos/bin/` - Backend Python modules
- `/opt/alvaos/webui/` - Frontend static assets (HTML/CSS/JS)
- `/opt/alvaos/apps/` - App catalog and icons
- `/opt/alvaos/scripts/` - Update and setup helper scripts
- `/etc/alvaos/` - Configuration and version files
- `/var/lib/alvaos/` - State (auth, setup status, pools, backups)
- `/var/log/alvaos/` - Logs
- `/srv/` - User data and shares

## Tech Stack Summary
| Component | Tech | Rationale |
|-----------|-----------|-----------|
| Base OS | Debian Stable | Ultra-stable |
| Storage | Btrfs | Snapshots, pooling |
| Containers| Docker + Compose | Proven, simple |
| Backend | Python 3 + Flask | Simple, batteries-included, easy to audit |
| UI | Vanilla HTML/CSS/JS | No framework, no build step, lightweight |
| Auth | Session tokens + TOTP | Local-first, no external IdP |
| Backup | WireGuard + encrypted transfer | Secure, efficient |
| Packaging | Debian `.deb` | Native, predictable upgrades |

## Non-Goals & Future
- **No**: VMs (current), Kubernetes, Desktops, Cloud/Telemetry.
- **Future**: ZFS alternative, plugin system, basic VM support.

**Philosophy**: Boring, stable, reliable. Runs for years without intervention.
