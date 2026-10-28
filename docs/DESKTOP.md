# AlvaOS for computers (Windows, Linux, macOS)

An icon in the tray that keeps the computer connected to the NAS through **AlvaOS Link**: from anywhere, end to end encrypted, with no router settings and no account (docs/LINK.md). The NAS then has addresses on the computer, for the app's window, the browser and every other program.

## What it does

When it is set up, the app asks once what to use (everything can be changed later under Settings):

| Building block | What it is |
|---|---|
| **Your NAS from anywhere** | Always there. The NAS has addresses on this computer: `http://127.0.0.1:18090` for the Hub (and CalDAV, CardDAV), `http://127.0.0.1:18091` for Files over WebDAV. Every connection to them goes through Link to the NAS: directly on the home network or through the routers, else through a relay that only passes on encrypted packets. |
| **The Hub apps in a window** | Files, Photos, Calendar, Contacts, Chat, one click from the tray, signed in. |
| **Files as a drive** | The shares of the person in the file manager (Linux: GNOME Files and every app using GIO; macOS: Finder; Windows: a network drive in Explorer, see below). |
| **A sync folder** | Chosen folders of the NAS kept on this computer (by default in `~/AlvaOS/<share>/<folder>`), also offline. See below. |
| **Start with the computer** | Quietly, in the tray. |

The tray shows how the computer reaches the NAS ("Connected to cygnus · direct · 12 ms", "through a relay", "cannot be reached") and has: Show AlvaOS (its small window), Open the Hub apps, Open in the browser, Sync now, Open the sync folder, Show Files as a drive, the switch **Connected to the NAS**, Quit. A click on the icon (Windows, macOS) shows the small window; on Linux the menu opens, with Show AlvaOS on top. The window has the status and the sync folders; the gear opens the settings.

Starting AlvaOS from the launcher (search, start menu, a shortcut) opens the Hub apps' window: right away if the app is already running in the tray, else once it has connected (the small window if the NAS cannot be reached). Starting with the computer stays quiet, in the tray.

**Open in the browser** signs the browser in by itself: the app asks the NAS for a code that works once, for a minute (`POST /api/devices/handoff`), and opens `http://127.0.0.1:18090/?handoff=<code>`; the Hub page trades it for a browser session of the same person (`POST /api/handoff`). The Hub's window does the same. The app's own session never reaches a page. Bookmarks keep working: the ports stay the same.

Apps that open in the Hub's window (docs/HUB.md) get a port of their own on this computer too, the same number as on the NAS (`127.0.0.1:28096` for an app on 8096): the app asks the Hub which apps those are (each minute, with its connection check) and carries every connection through Link as `app:<port>`. A port that is taken here is left out; the app then opens in a new tab.

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
- **Windows:** a network drive (`net use Z:`, the first free letter from Z down). Windows' WebDAV client only signs in over HTTPS with a certificate it trusts, so the app answers HTTPS on `https://127.0.0.1:18092` (handing every connection on to the plain Files address) with a certificate of its own, made for `127.0.0.1` and `localhost` only and not allowed to sign anything (`core/src/tls.rs`). The first time, Windows asks "Do you want to install this certificate?": Yes trusts that one certificate for this user (`certutil -user -addstore Root`); the key stays in the app's folder, readable only by this user. Windows opens files through WebDAV up to 50 MB (a setting of its WebClient service); bigger files are for the sync folder. If mounting fails with "network name not found", the WebClient service is off: start it in Services.

## The sync folder (0.6)

Chosen at setup (after "What do you want on this computer?") or later under **Sync folders › Add a folder**: a share, or a folder in it, kept in a folder of this computer. Changes go both ways.

- **Rounds:** every minute, and a few seconds after a change here (the folder is watched). The NAS gives its whole tree in one answer (`GET /api/sync/tree`); files go through WebDAV (port 18091 here), with the file's date kept on the NAS (`X-OC-Mtime`).
- **Three-way:** each folder remembers how both sides were after its last round (`sync/<id>.json` in the app's folder), so it knows what changed where.
- **Nothing gets lost:** changed on both sides → both kept (`name (conflict <computer> <date>).ext`); replaced or deleted on the NAS → into the share's trash; deleted on the NAS → into this computer's trash. Many deletions at once (20 or more, over half the files) wait for a yes ("Delete them too" / "Bring them back"). A missing local folder (unplugged disk) changes nothing.
- **Read-only shares** only come down.
- **Don't sync:** under a folder's "…" button: names (`*.mov`, `node_modules`) in any folder, or paths from the folder's top (`Videos/`). What is left out is not sent, not fetched and not deleted on either side; what was synced before is just forgotten and stays where it is.
- **What is going on:** the row shows the file and how far the round is (3 of 12) with a bar; the "…" window lists what happened lately (sent, received, deleted, kept twice) and the conflict copies that are still there.
- **Network hiccups:** a file is tried three times (after 2 and 8 seconds) when the NAS is busy or the connection drops; a stalled connection ends after 90 seconds without data.
- The tray shows the state ("Up to date", "Syncing…") and has **Sync now** and **Open the sync folder**; the settings page can pause it. Signing out stops syncing; the files stay.

Tested in memory and against the real Hub, WebDAV and helper (`scripts/dev/sync_against_nas.py`). Not tested yet: Windows and macOS.

## Files on demand (Linux)

When adding a sync folder, **Only download what you open** shows the whole folder of the NAS on this computer at once, but holds only what was opened. It is a folder of its own (a FUSE mount through `fusermount3`, package `fuse3`, which the .deb and .rpm depend on; no root, no kernel module of ours): the folder must be empty, and it shows the NAS's files until the app quits or the sync folder is removed.

- **Open** a file: it is fetched and kept (a file over 64 MB that is not kept here is read in pieces where it is, so a film starts at once). **Save**: the file goes back to the NAS when the program closes it, and an older file of that name goes to the share's trash. **Make, rename, move, delete** work as in any folder and happen on the NAS (delete: into its trash).
- **Always keep on this computer** (the folder's "…" window): patterns like Don't sync (`Documents/`, `*.pdf`); those files are fetched ahead and stay, also without a connection. **Free up space** frees every other copy at once; the oldest copies also go by themselves when the folder holds more than 5 GB.
- What was saved but could not go up yet (no connection) stays marked in the cache, also after a restart, and is sent in the next round. The NAS's folder is looked at again every minute.
- A share you may only read cannot be changed. The Flatpak cannot mount (its sandbox has no FUSE).

### Windows: the same through Explorer's own cloud folders

On Windows a new sync folder is **always** files on demand (there is no switch): it is a cloud folder like OneDrive's (Windows' Cloud Files API, `core/src/cloudfiles.rs`). Every file of the share shows up at once with a cloud icon; opening one makes Windows ask the app, which fetches it (a big file piece by piece, so a film starts at once). Explorer's own menu does the rest: **Always keep on this device**, **Free up space**. The folder must be empty when it is added.

- **Delete, rename, move** a file or folder: done on the NAS first (delete: into its trash). **New and changed files** are found each round (every minute) and sent; they become placeholders in step with the NAS. A file changed here and on the NAS keeps both (`name (conflict …)`). What changed on the NAS comes in as placeholders; what was deleted there is deleted here when nobody changed it here.
- Removing the sync folder in the app ends the cloud folder: what was downloaded stays, files that were not downloaded are gone from it (they are still on the NAS).
- A file deleted while the app was not running comes back at the next round (Windows only tells the app while it runs).
- Sync folders made before this (0.6.0, copies of everything) keep working as before. `ALVAOS_CLASSIC_SYNC=1` in the environment brings the old kind of sync folder back for new ones, if the cloud files do not work on a computer.
- **Files as a drive** (the network drive) stays as it was, for a quick look at everything without a sync folder.

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
