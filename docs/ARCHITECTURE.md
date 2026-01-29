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
- **UI**: Modern, dark-mode first (Svelte/Vue), responsive, Unraid-inspired.
- **API**: Versioned REST API (Go/Python), uses JSON and JWT. UI never runs shell commands directly.

## Distribution & Updates
- **Installer**: Minimal (<500MB) debootstrap image (BIOS/UEFI).
- **Updates**: Backend delivered as `.deb`. Web-based updates with automatic rollbacks. No mandatory cloud updates.

## Security Model

**Authentication:**
- Local user accounts
- Web UI login required
- SSH disabled by default (can be enabled)
- No default passwords

**Network:**
- LAN access by default
- Optional WireGuard for remote access
- Buddy Backup uses encrypted tunnels

**Data:**
- Encryption at rest (optional, user choice)
- Buddy Backup always encrypted in transit
- No telemetry or phone-home

## Service Architecture

**systemd units:**
- `alvaos-backend.service` - REST API server
- `alvaos-ui.service` - Web UI (nginx or static server)
- `alvaos-buddy-backup.service` - Backup daemon
- Standard Docker service

**Storage:**
- `/etc/alvaos/` - Configuration files
- `/var/lib/alvaos/` - State and databases
- `/opt/alvaos/` - Application binaries
- `/srv/` - User data and shares

## Tech Stack Summary
| Component | Tech | Rationale |
|-----------|-----------|-----------|
| Base OS | Debian Stable | Ultra-stable |
| Storage | Btrfs | Snapshots, pooling |
| Containers| Docker | Proven, simple |
| Backend | Go/Python | Performance/Prototyping |
| UI | Svelte/Vue | Lightweight, reactive |
| Backup | WireGuard+rsync| Secure, efficient |

## Non-Goals & Future
- **No**: VMs (current), Kubernetes, Desktops, Cloud/Telemetry.
- **Future**: ZFS alternative, plugin system, basic VM support.

**Philosophy**: Boring, stable, reliable. Runs for years without intervention.
