# AlvaOS

**AlvaOS** is a calm NAS operating system for storage, apps and offsite backup: simple by default, powerful when needed, light enough for old hardware. It is in **beta** (see Project Status).

---

## What it does

* **Storage**: Btrfs pools that grow disk by disk, shared folders (SMB, NFS), people with their own folders and limits, restore points, health monitoring.
* **AlvaOS Hub**: one address for what people use every day, in the browser and in the Android app.
  * **Files**: a lightweight file cloud (sharing, links, trash, previous versions, WebDAV).
  * **Photos**: backup from the phone, albums, favourites.
  * **Calendar and Contacts**: with CalDAV/CardDAV, so phones sync on their own; birthdays included.
  * **Chat**: a simple page in front of a local or cloud model.
* **AlvaOS Link**: the app and a second NAS reach your NAS from anywhere, with no router setting and no account. Built on [iroh](https://www.iroh.computer); end-to-end encrypted, direct where possible, through a relay otherwise. On by default, one switch to turn it off.
* **Buddy Backup**: an encrypted, incremental copy of your data on a friend's or family member's AlvaOS, over AlvaOS Link. Only you can read it. Restore everything on a fresh install with the recovery kit.
* **Apps**: Docker and Docker Compose, a catalog with one-click install and updates, graphics cards for apps and VMs.
* **Virtual machines**: QEMU/KVM with the screen in the browser (Windows 11 with UEFI and TPM).
* **Android app**: the whole Hub, photo backup in the background, share to AlvaOS.
* **Clean web UI**: dark and light, works on a phone, API-driven.
* **Stable base**: Debian, conservative defaults, signed updates with rollback.

---

## Project goals

* Stability over features
* Simplicity over complexity
* Easy backups and restore
* Fully self-hosted, no telemetry
* Lightweight and easily manageable

---

## Architecture

* **Base OS:** Debian (minimal), amd64
* **Storage:** Btrfs
* **Containers:** Docker and Docker Compose
* **Backend:** Python (Flask); the web UI never runs system commands itself, everything goes through a versioned API and a narrow privileged helper
* **Frontend:** vanilla HTML, CSS and JS
* **Away from home:** AlvaOS Link (iroh)

More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/STRUCTURE.md](docs/STRUCTURE.md).

---

## Documentation

| | |
|---|---|
| [docs/HUB.md](docs/HUB.md) | The Hub and its apps |
| [docs/LINK.md](docs/LINK.md) | AlvaOS Link: how phones and buddies reach the NAS |
| [docs/BUDDY_BACKUP.md](docs/BUDDY_BACKUP.md) | Buddy Backup |
| [docs/ANDROID.md](docs/ANDROID.md) | The Android app: building, signing, Google Play |
| [docs/PRIVACY.md](docs/PRIVACY.md) | What leaves your NAS and what does not |
| [docs/TESTING.md](docs/TESTING.md) | Step-by-step checks for a real installation |
| [docs/ROADMAP.md](docs/ROADMAP.md) / [docs/BACKLOG.md](docs/BACKLOG.md) | What is next / what was done |
| [docs/RELEASE.md](docs/RELEASE.md) | How a release is made |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | How to help, and our rules on AI-assisted code |

---

## License

AlvaOS is licensed under the **GPLv3**. You are free to use, modify and distribute it, including commercially. Third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

---

## Project status

AlvaOS is in active development and heading for a first public beta. APIs, formats and behavior may still change before a stable release. Please test on a spare machine first and keep a backup.

---

## Contributing

Contributions, ideas and feedback are welcome: open an issue or a pull request. Read [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) first; it also says how we treat AI tools (short version: allowed, but a human understands, reads and tests every line).

---

## Philosophy

AlvaOS exists to make self-hosting calm, reliable, and human.

No dashboards full of fear.
No unnecessary complexity.
Just storage that works.
