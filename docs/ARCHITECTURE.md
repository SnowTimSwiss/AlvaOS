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

**Disk roles.** Every disk is shown with one role (`storage_manager.describe_disks`), and the role decides what may be done with it, in the UI and again on the server:

| Role | Meaning | Allowed |
|------|---------|---------|
| `system` | Runs AlvaOS (also the second leg of a mirrored system) | nothing destructive |
| `pool` | Member of a pool AlvaOS manages | nothing destructive; remove the pool first |
| `in_use` | Mounted, used as swap, or part of a pool mounted outside AlvaOS | nothing; AlvaOS never unmounts it |
| `other_pool` | Holds a Btrfs pool that is not imported | import, or erase |
| `has_data` | Old partitions or file systems | erase |
| `empty` | Nothing on it | create a pool, add to a pool |

Only empty disks go into a pool, so `mkfs` and `btrfs device add` never destroy data by surprise. Removing a pool keeps its data on the disks (it can be imported again) unless the user explicitly chooses to erase them; a pool that is still shared or busy is not removed. Virtual devices (zram, loop, NBD vaults, optical drives) are not listed.

**Pool maintenance.** The Pools tab shows each pool's usage, protection and disks; a pool's detail (`storage.html#pool=<id>`) adds its activity and per-disk health. Long jobs run in the background as root through the helper and report progress from `btrfs ... status`, so they survive a page reload:
- *Replace* (`btrfs replace start -B`): copies a failing or missing member onto an empty disk at least as large, while the pool stays online.
- *Check data* (`btrfs scrub start -B`): reads every block and repairs bad copies from a good one where the pool is redundant.
- *Add disk* (`btrfs device add` + balance): as before.

Only one of these runs per pool at a time. Disk errors come from SMART and from `btrfs device stats`.

**Shares.** A share is a folder inside a pool (a new subvolume by default, an existing folder, or the whole pool), shared over SMB, or NFS for Linux clients. Share names are letters, digits, `-` and `_` only, because they become `smb.conf` section headers, export comments and folder names. NFS client lists are checked address by address, and exports always use `root_squash`. SMB access is either "only people I choose" (per person: read or edit) or "everyone on my network" (guest, optionally read only). The people are share accounts: they have no home directory and no login shell, so they cannot log in over SSH; accounts created by older versions lose their shell when the backend starts.

### 3. Containers (Docker + Compose)
Git-based template repository for one-click app installs. No Kubernetes or complex orchestration.

### 4. Buddy Backup (Core Feature)
NAS-to-NAS encrypted incremental backup.
- **Setup**: Link devices via pairing codes.
- **Replication**: each NAS keeps an encrypted vault on its buddy (an image the buddy exports over NBD inside the WireGuard tunnel; LUKS2 with a key only the owner has). Btrfs snapshots are replicated into it with `btrfs send -p`, so after the first sync only changes travel, and old snapshots can be deleted freely.
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
- Share accounts are created with `useradd -M -s /usr/sbin/nologin`; the only `usermod` allowed removes a regular account's login shell. NFS exports with `no_root_squash` are refused.
- Commands that erase a disk (`wipefs`, `mkfs.btrfs`, `btrfs device add`) are also refused when the device is in use right now: mounted, swap, a member of a mounted Btrfs pool, or held by LUKS/LVM/md (read from `/proc` and `/sys` as root). This holds even if the backend misjudges a disk.
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
- Buddy Backup data is encrypted on the owner before it reaches the buddy (LUKS2 vault; its key is stored on the buddy sealed with the encryption password, see `docs/BUDDY_BACKUP.md`) and travels only through the WireGuard tunnel. A fresh install can restore with only the encryption password.
- No telemetry or phone-home.

## Service Architecture

The backend is a single Flask process that serves both the REST API and the static Web UI on port `8080`. It runs under **waitress**, a production WSGI server — not the Flask development server, which is not built for the years of unattended operation AlvaOS targets.

**systemd units:**
- `alvaos.service` — main backend: REST API + Web UI (`python3 /opt/alvaos/bin/alvaos-backend.py`).
- `alvaos-update-checker.service` — periodic update check.
- `alvaos-watchdog.timer` / `alvaos-watchdog.service` — health check every ~5 min: restarts Samba, NFS or Docker when they are enabled but not running; services that are switched off are left alone.
- `health_checks.py` — a thread in the backend that starts a data check (`btrfs scrub`) per pool at night, monthly by default (Storage › pool › Activity), one pool at a time and never while the pool is busy. Results feed the dashboard and the alerts.
- `alvaos-update-checker.timer` / `.service` — looks for AlvaOS and Debian updates 15 min after boot and once a day; installs them only if automatic updates are switched on.
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
