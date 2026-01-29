# AlvaOS Project Structure

This document describes the repository organization.

## Directory Overview

```
AlvaOS/
├── .github/
│   └── workflows/
│       ├── build-installer.yml    # CI: Builds installer ISO
│       └── build-package.yml      # CI: Builds .deb package
│
├── backend/                       # REST API server
│   └── README.md                  # Backend architecture docs
│
├── frontend/                      # Web UI
│   └── README.md                  # Frontend architecture docs
│
├── installer/                     # Installer image builder
│   ├── build.sh                   # Main build script (debootstrap)
│   └── README.md                  # Build documentation
│
├── scripts/                       # System setup and maintenance
│   ├── package/
│   │   └── build-deb.sh           # .deb package builder
│   └── README.md                  # Scripts documentation
│
├── docs/                          # Documentation
│   ├── ARCHITECTURE.md            # System design
│   ├── BUDDY_BACKUP.md            # Backup specification
│   ├── CONTRIBUTING.md            # Developer guide
│   ├── README.md                  # Docs index
│   ├── STRUCTURE.md               # This file
│   └── UPDATE_STRATEGY.md         # Update mechanism
│
├── .gitignore                     # Ignore build artifacts
├── LICENSE                        # Apache License 2.0
└── README.md                      # Main project README
```

## Directory Details

### `/backend`
Contains the AlvaOS REST API server that the Web UI consumes. This is the bridge between the UI and system operations (storage management, Docker, networking, backups).

**Recommended tech:** Go (single binary, good for system tools) or Python

### `/frontend`
The AlvaOS Web UI. Inspired by Unraid's UX, dark mode first, simple and intuitive.

**Recommended tech:** Svelte or Vue (lightweight, modern)

### `/installer`
Build system for the AlvaOS installer image. Uses `debootstrap` to create a minimal Debian-based bootable ISO.

**Not a live desktop environment** - just a simple installer that bootstraps the system.

### `/scripts`
Post-install setup scripts and build tools:
- **Post-install scripts** - System initialization after fresh install
- **Package scripts** (`scripts/package/`) - Build .deb packages for distribution

Handles:
- Storage pool initialization (Btrfs)
- Docker installation
- Network configuration
- Buddy Backup setup
- System maintenance
- Release packaging

Philosophy: Simple, idempotent, well-logged shell scripts.

### `/docs`
User and developer documentation in Markdown format. See [docs/README.md](README.md) for full index.

### `/.github/workflows`
GitHub Actions CI/CD pipelines:
- **`build-installer.yml`** - Builds installer ISO on new releases
- **`build-package.yml`** - Builds `.deb` system package on new releases

Both workflows also support manual triggering via `workflow_dispatch`.

## Build & Release Process

### Release Workflow

1. **Create GitHub Release** (tag: `v1.0.0`)
2. **Automated builds trigger:**
   - `build-installer.yml` → creates `alvaos-installer-1.0.0.iso`
   - `build-package.yml` → creates `alvaos-system_1.0.0_amd64.deb`
3. **Artifacts auto-attached to release**
4. **Users download from GitHub Releases**

### Local Development Builds

**Build installer ISO:**
```bash
cd installer
sudo ./build.sh
```

**Build system .deb package:**
```bash
cd scripts/package
sudo ./build-deb.sh
```

## Philosophy

- **Simplicity over complexity** - No overengineering
- **Stability over features** - Debian Stable base, conservative choices
- **Transparency** - Everything is a script or config file, easy to inspect
- **Reproducibility** - Builds are deterministic
- **No magic** - Clear separation between OS, scripts, and UI

## Getting Started

1. **To build the installer locally:**
   ```bash
   cd installer
   sudo ./build.sh
   ```

2. **To develop the backend:**
   ```bash
   cd backend
   # Documentation TBD
   ```

3. **To develop the frontend:**
   ```bash
   cd frontend
   # Documentation TBD
   ```

## Next Steps

- Implement backend API skeleton
- Create frontend prototype
- Flesh out installation scripts
- Document Buddy Backup protocol
- Create user documentation

---

**Status:** Early development - repo structure is ready, implementation in progress.
