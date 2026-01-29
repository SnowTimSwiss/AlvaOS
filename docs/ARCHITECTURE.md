# AlvaOS Architecture

This document describes the complete architecture of AlvaOS.

## Design Principles

1. **Stability over features** - Conservative, proven technology
2. **Simple workflows over flexibility** - Opinionated but intuitive
3. **No cloud dependency** - Fully self-hosted
4. **Designed for years of unattended operation** - Ultra-reliable
5. **API-first** - UI never executes system commands directly
6. **Modular design** - Clear separation of concerns

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

### 1. Base OS

**Technology:** Debian Stable (minimal install)

**Characteristics:**
- No desktop environment
- systemd for service management
- Minimal package footprint
- No rolling releases (Debian Stable only)
- Conservative defaults

**Installed via:**
- Custom installer image (debootstrap-based)
- Deterministic, reproducible builds

### 2. Storage Management

**Technology:** Btrfs

**Features:**
- **Storage pools** - Combine multiple disks
- **Easy expansion** - Add disks like Unraid (simple workflow)
- **Snapshots** - Point-in-time backups
- **Background rebalance** - Automatic data distribution
- **SMART monitoring** - Disk health tracking
- **Self-healing** - Checksums and automatic repair

**Management:**
- Web UI for creation and expansion
- Automatic mount management
- Health dashboards

### 3. Containers & Apps

**Technology:** Docker + Docker Compose

**App Store:**
- Git-based template repositories
- One-click install/update/remove
- Pre-configured compose files
- Automatic updates via API

**Explicitly NOT included:**
- Kubernetes
- VMs (not for now)
- Complex orchestration

### 4. Buddy Backup (Core Feature)

**Purpose:** NAS-to-NAS backup over the internet

**Features:**
- **Pairing** - Short code-based device linking
- **Encryption** - End-to-end, automatic key management
- **Incremental** - Only changed data transfers
- **Snapshot-based** - Consistent point-in-time backups
- **Full restore** - Rebuild entire NAS from backup

**What is backed up:**
- Storage layout and pool configuration
- Shares and permissions
- Docker containers and app configurations
- Users and system settings

**What is NOT backed up:**
- The operating system itself (reinstall from installer)

**Protocol:**
- Encrypted tunnel (likely WireGuard-based)
- Rsync or custom incremental transfer
- Automatic verify and health checks

**Workflow:**
1. User generates pairing code on NAS A
2. User enters code on NAS B
3. Automatic encrypted connection established
4. Background incremental backups begin
5. On disaster: reinstall AlvaOS, pair with buddy, restore

### 5. Web UI

**Technology:** Svelte or Vue (lightweight, modern)

**Design:**
- **Dark mode first** - Primary color scheme
- **Unraid-inspired UX** - Simple, clean workflows
- **Minimal clicks** - Common tasks are fast
- **Responsive** - Works on desktop, tablet, mobile
- **API-driven** - NO direct system commands

**Key Views:**
- Dashboard (system status, storage, containers)
- Storage pool management
- Docker app store
- Buddy Backup setup and monitoring
- System settings and users

### 6. REST API (Backend)

**Technology:** Go (recommended) or Python

**Why Go:**
- Single binary deployment
- Great for system tooling
- Fast, reliable, good concurrency
- Cross-compilation for easy distribution

**API Design:**
- **Versioned** - `/api/v1/...`
- **RESTful** - Standard HTTP methods
- **JSON** - Request/response format
- **Authentication** - JWT or session-based
- **Documentation** - OpenAPI/Swagger

**API Modules:**
- `/api/v1/storage` - Pool management, disks
- `/api/v1/docker` - Container management
- `/api/v1/apps` - App store operations
- `/api/v1/backup` - Buddy Backup pairing and status
- `/api/v1/system` - Users, network, updates
- `/api/v1/health` - System health and monitoring

**Security:**
- UI never calls shell commands directly
- API validates and sanitizes all inputs
- Fine-grained permission model
- Audit logging

## Distribution Model

AlvaOS is **NOT a classic live ISO**.

**Build Process:**

1. **Minimal installer image** (ISO or IMG)
   - Created with `debootstrap`
   - Contains just enough to bootstrap Debian
   - Bootable on BIOS and UEFI
   - Target size: < 500 MB

2. **Post-install scripts**
   - Install AlvaOS backend
   - Configure storage
   - Set up Docker
   - Deploy Web UI
   - Initialize services

3. **System assembly**
   - Deterministic and automated
   - Reproducible builds
   - Version-controlled configs

**Advantages:**
- Small, fast builds
- Easy to maintain
- Flexible post-install customization
- No bloated live environment

## Update Strategy

See [UPDATE_STRATEGY.md](UPDATE_STRATEGY.md) for detailed update architecture.

**Key points:**
- AlvaOS backend as a `.deb` package
- System updates via Web UI
- Automatic rollback on failure
- Minimal downtime
- No mandatory cloud updates

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

## Technology Stack Summary

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Base OS | Debian Stable | Ultra-stable, long-term support |
| Init system | systemd | Standard, reliable |
| Storage | Btrfs | Snapshots, pooling, self-healing |
| Containers | Docker + Compose | Simple, proven, widely supported |
| Backend API | Go or Python | Go: single binary; Python: prototyping |
| Frontend UI | Svelte or Vue | Lightweight, modern, reactive |
| Backup transport | WireGuard + rsync | Encrypted, efficient, incremental |
| Package format | .deb | Native Debian packaging |

## Non-Goals

To maintain simplicity, AlvaOS explicitly **does not**:
- Support VMs (for now)
- Include Kubernetes
- Run a desktop environment
- Require cloud services
- Phone home or include telemetry
- Support rolling releases

## Future Considerations

- ZFS as alternative to Btrfs (community request)
- VM support (via KVM/QEMU, low priority)
- Plugin system for extensions
- Multi-node clustering (far future)

---

**Philosophy:** Keep it boring, stable, and reliable. AlvaOS should run for years without intervention.
