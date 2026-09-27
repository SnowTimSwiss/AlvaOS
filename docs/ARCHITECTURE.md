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
- **UI**: Dark-mode first, responsive, Unraid-inspired. Plain HTML/CSS/vanilla JavaScript — no framework and no build step. Files are served as static assets by the backend. Every runtime asset (including the icon set in `frontend/lucide-icons.js`) is bundled locally, so the UI works on a LAN with no internet access.
- **API**: REST API under `/api/v1/`, implemented in Python with Flask. Uses JSON. Authentication is via opaque session tokens (see Security Model), not JWT. The UI never runs shell commands directly — every privileged action goes through the API, which runs system commands only through the `alvaos-priv` privilege helper.

## Distribution & Updates
- **Installer**: Minimal (<500MB) debootstrap image (BIOS/UEFI).
- **Updates**: Backend delivered as `.deb`. Web-based updates with automatic rollbacks. No mandatory cloud updates.

## Security Model

**Authentication:**
- Single local admin account (`root`), configured during first-run setup — there are no default passwords.
- Web UI login required for all API endpoints once setup is complete.
- Passwords are stored as PBKDF2-HMAC-SHA256 hashes with a per-install random salt (`/var/lib/alvaos/auth.json`).
- Sessions use opaque random tokens with a 24h TTL. Tokens are persisted to `/var/lib/alvaos/sessions.json` (mode `0600`), so a backend restart or an update does not log everyone out. `POST /api/v1/auth/logout` revokes a token server-side.
- Optional TOTP two-factor authentication (RFC 6238, via `pyotp`).
- State-changing requests require a CSRF token bound to the session. This is enforced centrally in a `before_request` hook rather than per route, so a newly added endpoint is protected by default. The only exemptions are the pre-session handshakes (setup, login, 2FA) and buddy peer-to-peer traffic, which authenticates with `X-Buddy-Secret` instead.
- Login attempts are rate-limited per IP (10 attempts / 15 min).
- Security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`) are added to every response.
- Before first-run setup is complete, every API route except `/api/v1/setup/status` and `/api/v1/setup/complete` is refused, so an unconfigured box cannot be inventoried or claimed over the network.
- No CORS headers are sent: the Web UI is served from the same origin, so cross-origin access is never legitimate.
- SSH root login is disabled by default and stays disabled after first-run setup. It is opt-in through System → Security → Remote Access (`/api/v1/system/ssh`).

**Privilege separation:**
- The backend runs as the unprivileged `alvaos` system user.
- Its single sudo rule is the privilege helper `/opt/alvaos/bin/alvaos-priv`. The helper checks every command against `backend/priv_policy.py` before running it: deny by default, no shells, per-argument rules (which paths, devices, users, subcommands), system disks protected.
- Files the backend writes and a root process later interprets (compose files, the WireGuard config, packages, apt sources) are copied into root-owned staging and checked there; config files handed to root daemons (`smb.conf`, the sshd drop-in, `/etc/exports`) are checked for command-executing directives.
- Denied commands exit with status 126 and are logged to `/var/log/alvaos/priv-denied.log`.
- Code under `/opt/alvaos` is root-owned; only state (`/var/lib/alvaos`), logs and `/etc/alvaos` belong to the service user.
- CI (`scripts/ci/check_privileged_commands.py`) fails if the sudoers file grows, if backend code builds its own `sudo`/`bash -c` command lines, or if installers hand `/opt/alvaos` to the service user.

**Updates:**
- AlvaOS packages are signed (Ed25519); the helper verifies the signature against the root-owned public key before installing. See `docs/RELEASE.md`.

**Network:**
- LAN access by default
- Optional WireGuard for remote access
- Buddy Backup uses encrypted tunnels

**Data:**
- Buddy Backup payloads are encrypted before transfer (scrypt + AES-256-GCM, format `ALVAENC2`, see `docs/BUDDY_BACKUP.md`) and sent over a WireGuard tunnel. A fresh install can restore with only the encryption password.
- No telemetry or phone-home.

## Service Architecture

The backend is a single Flask process that serves both the REST API and the static Web UI on port `8080`. It runs under **waitress**, a production WSGI server — not the Flask development server, which is not built for the years of unattended operation AlvaOS targets.

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
| Backend | Python 3 + Flask on waitress | Simple, batteries-included, easy to audit |
| UI | Vanilla HTML/CSS/JS | No framework, no build step, lightweight |
| Auth | Session tokens + TOTP | Local-first, no external IdP |
| Backup | WireGuard + encrypted transfer | Secure, efficient |
| Packaging | Debian `.deb` | Native, predictable upgrades |

## Non-Goals & Future
- **No**: VMs (current), Kubernetes, Desktops, Cloud/Telemetry.
- **Future**: ZFS alternative, plugin system, basic VM support.

**Philosophy**: Boring, stable, reliable. Runs for years without intervention.
