# Roadmap

AlvaOS is a calm NAS OS for storage, apps and offsite backup: extremely simple by
default, powerful when needed, light enough for old hardware.

This is a plan, not a promise. What is finished is in [BACKLOG.md](BACKLOG.md); how to
check it on a real machine is in [TESTING.md](TESTING.md).

- [x] Done and checked in automated tests
- [~] Done, but still needs a run on real hardware or a real phone
- [ ] Not done yet

Current version: **beta-v0.6.0**

---

## Next

### Files
- [x] "You use 42 of 100 GB" everywhere (Files, Photos, Calendar, Contacts and Chat)
- [ ] Recent and Starred kept on the NAS, so every device sees the same
- [ ] Search inside documents (text in PDFs and office files)
- [ ] Activity: who changed, deleted or shared what
- [ ] Links: download limit, how often opened, "end all my links"
- [x] Unpack ZIP files, download several files as one ZIP, folder sizes
- [ ] Rename many files at once
- [ ] Edit Office documents with the EuroOffice app (WOPI)
- [ ] Share links for people without the app, done differently from the old Remote access

### Photos
- [ ] Share an album with the household
- [ ] Better video thumbnails and playback
- [ ] Photos on contacts

### Calendar and Contacts
- [x] Events over several days: draw them across days, grab the ends of a bar to change its days (month and week)
- [ ] Reminders (needs a way to reach the person: the Android app's notifications first)
- [ ] Shared address books
- [ ] Sync that works away from home for other apps than ours

### Apps for phones and computers
- [ ] File sync and notifications in the Android app
- [ ] iPhone app (on AlvaOS Link)
- [~] Desktop app: 0.6.0 is there (docs/DESKTOP.md), the plan below

#### Desktop app (Windows, Linux): the plan

macOS is not planned for now.

One app in building blocks. After connecting, the app asks once what to use (each can be changed later in its settings):

1. **Remote access** (always there, the base): the encrypted AlvaOS Link connection to the NAS, from anywhere, with no router settings. The app can make the NAS reachable for the **whole computer**, not only for itself, so that the browser opens the Hub away from home like at home, Thunderbird or the system's calendar sync CalDAV/CardDAV through it, and a network drive works from outside. A switch in the tray: connected to the NAS, on or off. Like Tailscale, but with no account and no server of our own.
2. **The Hub apps** (Files, Photos, Calendar, Contacts, Chat) in the app's window, as in the Android app. New Hub apps show up by themselves, no new app build.
3. **A folder on the computer:** first only as a network drive (WebDAV through the connection); later a real sync folder.

How it is built:
- One core in Rust (iroh is Rust already; the same core can later carry the iPhone app) and Tauri for the window, the tray and autostart: a few MB, not Electron.
- Packages: `.msi` (Windows), `.deb` and `.rpm` (own signed repository), Flatpak (Flathub); built in CI for both systems.
- Connecting without a camera: the code or link from the Hub (Devices › Connect a computer), or name and password. The session and the keys go to the system's key store (Credential Manager, libsecret); every computer shows under Devices and can be removed there.

Steps:
- [~] **0.5.0** Remote access for the whole computer, the Hub in the app's window, tray, setup that asks what to use, "Connect a computer" in the Hub, Files as a drive (Linux, macOS), packages for the three systems (tested on Linux; Windows and macOS need a run on a real computer)
- [~] **0.6.0** A real sync folder for chosen folders (also offline); conflicts keep both versions, nothing is deleted without the NAS's trash, tested against a simulated file system
- [~] Files on demand: Linux is there (a FUSE folder, docs/DESKTOP.md); Windows is there too (Explorer's cloud folders, the only kind of sync folder there; written without a Windows machine to run it on, to be tested); macOS is not planned
- [ ] Later: CalDAV/CardDAV set up in the system by itself where it can (macOS, GNOME, Thunderbird)

What it needs from you: Windows code signing (else SmartScreen warns at install).

### AlvaOS Link
- [ ] An own relay as a setting
- [ ] arm64, if AlvaOS ever ships for ARM (needs the aarch64 build of iroh and an arm64 package)

### Buddy Backup and not being without the NAS
- [ ] A *Take over* button: bring up the shares, the Hub and the people from the buddy's copy (a warm standby you start yourself, not automatic failover)
- [ ] Tell the buddy when the power is out (UPS), so it can hold off a backup

### Virtual machines
- [ ] Home Assistant OS and other ready images in one click
- [ ] Snapshots in the page

### Apps and graphics cards
- [ ] More catalog apps that can use a graphics card (Plex, Frigate, Stable Diffusion)

### Chat
- [ ] Pictures and files in a chat (for models that can read them)
- [ ] Making pictures (only with a graphics card or a paid service; maybe a ComfyUI app)
- [ ] Limits per person (messages or tokens per day) and a cost line for paid services

### Assistant
- [ ] Answers that stream word by word
- [ ] An audit line in the notifications for every confirmed action
- [ ] "May do everything" mode (not recommended, clearly marked)

---

## Not planned, on purpose

- **Third-party plugins inside the Hub.** Their code would run with the Hub's rights and could read everyone's files. Other people's apps stay isolated Docker apps.
- **Faces, maps and a full photo manager.** Immich does this well and is in the app catalog.
- **Automatic failover between two NAS.** Two machines cannot tell a dead neighbour from a broken cable, and then both write. A warm standby with a button is the plan instead.
- **Accounts, clouds and telemetry of our own.** AlvaOS Link needs no account; the default relays only pass on encrypted packets.

---

## Always

**Security**
- Every new privileged command gets a policy rule and tests.
- Everything that ends up in a config file or a command is validated.
- A short security review for every change to `priv_policy.py`, sign-in or AlvaOS Link.
- Regular dependency updates.

**Lightweight**
- Runs well on 2 GB RAM and old CPUs. No framework, no build step for the web UI.
- Background work runs at low priority. Idle CPU, memory and disk wake-ups are measured.

**Simple**
- Every page answers "is it fine, and what do I do next?" in plain words first; details are one click away.
- Plain words instead of technical terms; every warning links to where it is fixed.
- Works on a phone; checked in a browser at desktop and phone width; keyboard and contrast checked.

**How we build**
- Python for everything close to the NAS; Kotlin and Swift for phone apps; standard protocols first (CalDAV, CardDAV, WebDAV).
- Tests for every change. AI tools are allowed, a human reads and tests every line (see [CONTRIBUTING.md](CONTRIBUTING.md)).
