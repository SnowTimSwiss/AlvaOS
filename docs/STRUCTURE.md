# AlvaOS Project Structure

Repository organization and development workflows.

## Directory Overview
```
AlvaOS/
├── .github/workflows/ (CI: ISO & .deb builders)
├── backend/ (REST API - Python/Flask)
├── frontend/ (Web UI - vanilla HTML/CSS/JS)
├── installer/ (ISO builder - live-build)
├── scripts/package/ (.deb builder)
└── docs/ (Project specs & guides)
```

## Directory Details
- **/backend**: Core API server for system management (Storage, Docker, Backups).
  - `alvaos-backend.py` — entry point: creates the Flask app, security hooks (setup guard, CSRF, headers), static UI serving.
  - `api_*.py` — one Flask blueprint per area: `api_auth`, `api_system`, `api_updates`, `api_storage`, `api_shares`, `api_backup`, `api_apps`.
  - `app_services.py` — the shared manager instances all blueprints use.
  - `*_manager.py`, `app_store.py` — business logic.
  - `alvaos-priv`, `priv_policy.py` — the root privilege helper and its policy (see ARCHITECTURE.md).
  - `update_signing.py`, `buddy_crypto.py` — update signatures and Buddy Backup encryption.
  - `tests/` — pytest suite (`pytest` from the repository root).
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
Repository structure is finalized. Implementation of Core API and Web UI is in progress. See [ROADMAP.md](ROADMAP.md) for details. 🚀
