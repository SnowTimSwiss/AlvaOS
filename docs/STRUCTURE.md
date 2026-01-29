# AlvaOS Project Structure

This document describes the repository organization.

## Directory Overview

```
AlvaOS/
├── .github/
│   └── workflows/
│       └── build-installer.yml    # CI/CD: Builds installer image
│
├── backend/                       # REST API server
│   └── README.md                  # Backend architecture docs
│
├── frontend/                      # Web UI
│   └── README.md                  # Frontend architecture docs
│
├── installer/                     # Installer image builder
│   ├── build.sh                   # Main build script
│   └── README.md                  # Build documentation
│
├── scripts/                       # System setup scripts
│   └── README.md                  # Scripts documentation
│
├── docs/                          # Documentation
│   └── README.md                  # Docs overview
│
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
Post-install setup scripts. These handle:
- Storage pool initialization (Btrfs)
- Docker installation
- Network configuration
- Buddy Backup setup
- System maintenance

Philosophy: Simple, idempotent, well-logged shell scripts.

### `/docs`
User and developer documentation in Markdown format.

### `/.github/workflows`
GitHub Actions CI/CD pipelines. Currently includes:
- `build-installer.yml` - Builds the installer ISO on every push to main

## Build Process

1. GitHub Actions triggers on push to `main`
2. Runs `installer/build.sh` in a Debian container
3. Outputs `alvaos-installer-<version>.iso`
4. Artifact is uploaded and available for download

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
