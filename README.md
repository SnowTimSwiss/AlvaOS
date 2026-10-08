# AlvaOS

**AlvaOS** is an ultra-stable, lightweight NAS operating system focused on simplicity, reliability, and effortless backups between trusted peers.

---

## Key Features

* **Flexible Storage Pools**
  * Btrfs-based pools
  * Easy disk expansion (Unraid-like workflow)
  * Snapshots and health monitoring

* **Docker and App Store**
  * Docker and Docker Compose
  * Git-based app templates
  * One-click install and updates

* **Buddy Backup**
  * NAS-to-NAS backups over the internet
  * Encrypted, snapshot-based, incremental
  * Full system restore on a fresh install

* **Clean Web UI**
  * Inspired by UmbrelOS, ZimaOS Unraid and TrueNAS UX (Taking the best of all)
  * Dark and light theme (follows your device, or pick one)
  * API-driven, minimal clicks

* **Ultra Stable Base**
  * Debian Stable
  * Conservative defaults

---

## Project Goals

* Stability over features
* Simplicity over complexity
* Easy backups and restore
* Fully self-hosted
* Lightweight and easily manageable

---

## Architecture Overview

* **Base OS:** Debian (minimal)
* **Storage:** Btrfs
* **Containers:** Docker and Docker Compose
* **Backend:** Python
* **Frontend:** Web UI
* **Networking:** LAN; AlvaOS Link (iroh) for the app and Buddy Backup away from home

The Web UI never executes system commands directly. All actions go through a versioned API layer.

---

## Buddy Backup

AlvaOS introduces **Buddy Backup**: a built-in, peer-to-peer backup system.

* Pair two AlvaOS instances
* Automatic encrypted connection
* Incremental snapshot transfer
* Restore everything on a new machine

---

## Distribution Model

AlvaOS is **not a classic live ISO**.

Instead:

* Minimal installer image
* Post-install setup via scripts
* System assembled deterministically

This keeps builds reproducible, small, and easy to automate.

---

## License

AlvaOS is licensed under the **GPLv3-license**.

You are free to use, modify, and distribute this software, including for commercial purposes.

---

## Project Status

AlvaOS is in active development.

APIs, formats, and behavior may change until the first stable release.
---

## Contributing

Contributions, ideas, and feedback are welcome.

Please open an issue or pull request to get involved.

---

## Philosophy

AlvaOS exists to make self-hosting calm, reliable, and human.

No dashboards full of fear.
No unnecessary complexity.
Just storage that works.
