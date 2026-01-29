# AlvaOS

**AlvaOS** is an ultra‑stable, lightweight NAS operating system focused on simplicity, reliability, and effortless backups between trusted peers.

> Simple storage. Simple apps. Simple backups.

---

## ✨ Key Features

* 🗄️ **Flexible Storage Pools**

  * Btrfs‑based pools
  * Easy disk expansion (Unraid‑like workflow)
  * Snapshots & health monitoring

* 🧩 **Docker & App Store**

  * Docker + Docker Compose
  * Git‑based app templates
  * One‑click install & updates

* 🔁 **Buddy Backup (Core Feature)**

  * NAS‑to‑NAS backups over the internet
  * Encrypted, snapshot‑based, incremental
  * Full system restore on a fresh install

* 🖥️ **Clean Web UI**

  * Inspired by Unraid UX
  * Dark mode first
  * API‑driven, minimal clicks

* 🧱 **Ultra Stable Base**

  * Debian Stable
  * No rolling releases
  * Conservative defaults

---

## 🎯 Project Goals

* Stability over features
* Simplicity over complexity
* No cloud dependency
* Fully self‑hosted
* Open source from day one

---

## 🧠 Architecture Overview

* **Base OS:** Debian (minimal)
* **Storage:** Btrfs
* **Containers:** Docker + Docker Compose
* **Backend:** REST API (Go or Python)
* **Frontend:** Web UI (Svelte / Vue)
* **Networking:** LAN + optional WireGuard

The Web UI never executes system commands directly. All actions go through a versioned API layer.

---

## 🔁 Buddy Backup Concept

AlvaOS introduces **Buddy Backup**: a built‑in, peer‑to‑peer backup system.

* Pair two AlvaOS instances
* Automatic encrypted connection
* Incremental snapshot transfer
* Restore everything on a new machine

Backed up data includes:

* Storage layout
* Shares
* Docker containers & app configs
* Users & system settings

The operating system itself is reinstalled from the installer.

---

## 📦 Distribution Model

AlvaOS is **not a classic live ISO**.

Instead:

* Minimal installer image
* Post‑install setup via scripts
* System assembled deterministically

This keeps builds reproducible, small, and easy to automate.

---

## 📜 License

AlvaOS is licensed under the **Apache License 2.0**.

You are free to use, modify, and distribute this software, including for commercial purposes.

---

## 🚧 Project Status

AlvaOS is in early development.

APIs, formats, and behavior may change until the first stable release.

---

## 🤝 Contributing

Contributions, ideas, and feedback are welcome.

Please open an issue or pull request to get involved.

---

## ❤️ Philosophy

AlvaOS exists to make self‑hosting calm, reliable, and human.

No dashboards full of fear.
No unnecessary complexity.
Just storage that works.
