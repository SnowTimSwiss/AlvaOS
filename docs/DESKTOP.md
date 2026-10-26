# AlvaOS for computers (Windows, Linux, macOS)

An icon in the tray that keeps the computer connected to the NAS through **AlvaOS Link**: from anywhere, end to end encrypted, with no router settings and no account (docs/LINK.md). The NAS then has addresses on the computer, for the app's window, the browser and every other program.

## What it does

When it is set up, the app asks once what to use (everything can be changed later under Settings):

| Building block | What it is |
|---|---|
| **Your NAS from anywhere** | Always there. The NAS has addresses on this computer: `http://127.0.0.1:18090` for the Hub (and CalDAV, CardDAV), `http://127.0.0.1:18091` for Files over WebDAV. Every connection to them goes through Link to the NAS: directly on the home network or through the routers, else through a relay that only passes on encrypted packets. |
| **The Hub apps in a window** | Files, Photos, Calendar, Contacts, Chat, one click from the tray, signed in. |
| **Files as a drive** | The shares of the person in the file manager (Linux: GNOME Files and every app using GIO; macOS: Finder). Windows: with the sync folder (0.6), see below. |
| **A sync folder** | Coming in 0.6. |
| **Start with the computer** | Quietly, in the tray. |

The tray shows how the computer reaches the NAS ("Connected to cygnus · direct · 12 ms", "through a relay", "cannot be reached") and has: Open AlvaOS, Open in the browser, Show Files as a drive, the switch **Connected to the NAS**, Settings, Quit.

**Open in the browser** signs the browser in by itself: the app asks the NAS for a code that works once, for a minute (`POST /api/devices/handoff`), and opens `http://127.0.0.1:18090/?handoff=<code>`; the Hub page trades it for a browser session of the same person (`POST /api/handoff`). The Hub's window does the same. The app's own session never reaches a page. Bookmarks keep working: the ports stay the same.

Calendar and contacts programs (Thunderbird, GNOME Calendar, macOS Calendar) use the Hub's address `http://127.0.0.1:18090/` with the person's name and password, as at home.

## Connecting a computer

In the Hub: **Devices › Connect a computer**. It shows a link (`alvaos://pair?c=…&l=…`) to copy and paste into the app, and the code with the address for typing. A computer has no camera, so there is no QR code to scan. Like a phone, it pairs through Link (the `pair` service): at home and away, and the NAS learns its key at once. With Link off on the NAS, it pairs at home over HTTP and stays at home only. **Name and password** work too (at home, with the NAS's address).

Every computer shows under Devices (with a screen icon, Windows/Linux/macOS) and can be signed out there. The app notices it: when the NAS refuses it twice in a row (20 s apart, so a NAS restarting does not count), it forgets its session and asks to be connected again. **Sign out** in the app signs it out on the NAS too.

## What is kept on the computer

- `settings.json` in the app's folder (Windows `%APPDATA%\AlvaOS`, Linux `~/.config/AlvaOS`, macOS `~/Library/Application Support/AlvaOS`): the NAS's name and Link address, the person, the ports, the building blocks.
- The session of this computer and its Link key: in the system's key store (Windows Credential Manager, macOS Keychain, the Secret Service on Linux). Only where there is none (or with `ALVAOS_DESKTOP_DIR`), in `secrets.json` that only the user can read.
- The Hub's window keeps its own sign-in, like a browser.

Signing out removes the session; the Link key stays (it is this computer's; the NAS forgets it when the device is removed there).

## Files as a drive

WebDAV through the computer's own address of the NAS (`dav` service of Link → port 8091 of the NAS). The computer signs in with its own session token as the password (the NAS accepts a device's token there, only as its own person; signing the device out ends it), so the person's password is not stored.

- **Linux:** `gio mount dav://<person>@127.0.0.1:18091/` (needs gvfs, as on GNOME). In GNOME Files under Other Locations.
- **macOS:** Finder's `mount volume`.
- **Windows:** its WebDAV client only signs in over HTTPS with a trusted certificate; the drive comes with the sync folder in 0.6. Until then Files in the app's window.

## Building

The app is in `desktop/`: a Rust workspace with `core` (no window: pairing link, Link client with iroh 1.x, the local proxy, settings and key store, the drive, the Hub calls) and `app` (Tauri 2: the tray, the windows, the commands of the app's own pages in `app/ui/`). Only the app's own pages may call its commands (`app/capabilities/main.json`); the Hub's window may call nothing.

```sh
# Linux: sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libssl-dev libdbus-1-dev
cd desktop
cargo test -p alvaos-core          # also against the real Link daemon, with: pip install iroh==1.1.0
cargo run -p alvaos-desktop        # the app (debug)
cd app && npx @tauri-apps/cli@2.12.4 build --bundles deb,rpm   # packages
```

`.github/workflows/desktop.yml` tests and builds on every change; an AlvaOS release (or a run by hand with a release tag) builds the packages and adds them to the release:

| System | Files |
|---|---|
| Windows | `AlvaOS_<v>_x64_en-US.msi`, `AlvaOS_<v>_x64-setup.exe` |
| macOS (Apple silicon and Intel) | `AlvaOS_<v>_universal.dmg` |
| Linux | `AlvaOS_<v>_amd64.deb`, `AlvaOS-<v>-1.x86_64.rpm`, `AlvaOS_<v>_amd64.AppImage`, `AlvaOS.flatpak` |

The Flatpak is made from the `.deb` (`desktop/flatpak/`, GNOME 48 runtime, the tray library from flathub/shared-modules).

**Not signed yet:** Windows shows a SmartScreen warning ("More info › Run anyway"), macOS asks to open it from System Settings › Privacy & Security. Signing needs a Windows code signing certificate and an Apple Developer account (notarising); both are for later.

Tests against a NAS on the same machine: `ALVAOS_LINK_HINTS=<addresses of the daemon>` makes the app use Link without relays, on the loopback network only; `ALVAOS_DESKTOP_DIR=<folder>` keeps everything there instead of the key store.

## What it sends, and where

To the NAS, through Link: what the person does in the Hub, the computer's name, its system (Windows, Linux, macOS) and the app's version (for Devices). The relays of the iroh project (only when no direct connection is possible) see encrypted packets and which two keys talk. Nothing goes to the makers of AlvaOS; no analytics, no crash reports.
