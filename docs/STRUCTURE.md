# AlvaOS Project Structure

Repository organization and development workflows.

## Directory Overview
```
AlvaOS/
├── .github/workflows/ (CI: ISO & .deb builders)
├── backend/ (REST API - Go/Python)
├── frontend/ (Web UI - Svelte/Vue)
├── installer/ (ISO builder - live-build)
├── scripts/package/ (.deb builder)
└── docs/ (Project specs & guides)
```

## Directory Details
- **/backend**: Core API server for system management (Storage, Docker, Backups).
- **/frontend**: Dark-mode first Web UI. Unraid-inspired, simple, and reactive.
- **/installer**: Build scripts using Debian `live-build` to create the installer ISO.
- **/scripts**: Idempotent shell scripts for system setup and maintenance.
- **/docs**: Technical documentation in Markdown.

## Development & Build
1. **GitHub Release**: Tagging `vX.X.X` triggers CI to build and attach artifacts.
2. **Build ISO**: `cd installer && sudo ./build.sh`.
3. **Build Package**: `cd scripts/package && sudo ./build-deb.sh`.

## Philosophy
- **Stability first**: Debian Stable base with conservative technology.
- **Transparency**: Clear separation between OS, scripts, and UI. No "magic".
- **Reproducibility**: Deterministic, script-based build process.

## Status
Repository structure is finalized. Implementation of Core API and Web UI is in progress. 🚀
