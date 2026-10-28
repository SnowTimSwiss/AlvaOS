# AlvaOS

**AlvaOS** is a calm NAS operating system for storage, apps and offsite backup: simple by default, powerful when needed, light enough for old hardware. It is in **beta** (see Project Status).

---

## What sets it apart

Three things come built in, with nothing to install, no account and no router setting:

### AlvaOS Link: your NAS from anywhere

Phones, computers and a second NAS reach your NAS from anywhere, even behind a strict router or a mobile network. No port forwarding, no VPN to set up, no account with us. Every device you paired is proven by its own key, the connection is end-to-end encrypted, direct where possible and through a relay (that only passes on encrypted packets) otherwise. On by default, one switch to turn it off. Built on [iroh](https://www.iroh.computer).

### Buddy Backup: a copy that survives a fire

An encrypted, incremental copy of your data on a friend's or family member's AlvaOS, over AlvaOS Link: nothing to open in either router, and only you can read it. If your NAS is lost, a fresh install gets everything back with the recovery kit. No cloud bill, nobody else's servers.

### The Hub: what the household uses every day

One address, one sign-in per person, in the browser, in the Android app and in the desktop app. You choose which apps are on and who sees each one.

* **Files**: shared folders in the browser, links, trash, previous versions, several files as one ZIP, WebDAV.
* **Photos**: backup from the phone, albums, favourites, a timeline.
* **Calendar and Contacts**: week, month and schedule, events over several days, CalDAV/CardDAV so phones sync on their own.
* **Chat**: a simple page in front of a local or cloud model.
* Installed catalog apps show up as tiles.

---

## And the NAS underneath

* **Storage**: Btrfs pools that grow disk by disk, shared folders (SMB, NFS), people with their own folders and limits, restore points, health monitoring.
* **Apps**: Docker and Docker Compose, a catalog with one-click install and updates, graphics cards for apps and VMs.
* **Virtual machines**: QEMU/KVM with the screen in the browser (Windows 11 with UEFI and TPM).
* **Android app**: the whole Hub, photo backup in the background, your shared folders in the system's Files app.
* **Desktop app** (Windows, Linux): the NAS from anywhere on the computer, also in its browser; the Hub apps in a window; a sync folder that only downloads what you open (Windows: experimental).
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
| [docs/DESKTOP.md](docs/DESKTOP.md) | The desktop app: what it does, packages, building |
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

Not yet tested on real hardware, so treat as **experimental**: the Windows desktop app's sync folder and network drive, and the Android app's entry in the system's Files app. [docs/TESTING.md](docs/TESTING.md) says how to check them.

---

## Contributing

Contributions, ideas and feedback are welcome: open an issue or a pull request. Read [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) first; it also says how we treat AI tools (short version: allowed, but a human understands, reads and tests every line).

---

## Philosophy

AlvaOS exists to make self-hosting calm, reliable, and human.

No dashboards full of fear.
No unnecessary complexity.
Just storage that works.
