# Testing AlvaOS by hand

A walk through everything a person would do with a new NAS, in the order
they would do it. Each step says what should happen. Write down what does
not, with the step number; that is all a bug report needs.

The automatic tests (`pytest`, the browser checks) cannot do some of this:
they have no real disks, no Btrfs, no WireGuard and no router. Those steps
are marked **(only on a real machine)**.

## 0. Get an installer

- GitHub › Actions › **Build AlvaOS Installer** › the newest run for the
  branch › **Artifacts** › `alvaos-installer-…` (a zip with the ISO and
  `checksums.txt`). Or "Run workflow" on the branch you want to test.
- A virtual machine is easiest (virt-manager, VirtualBox, Proxmox, UTM):
  - 2 CPUs, 2–4 GB RAM, UEFI (also try BIOS once).
  - Disk 1: 16 GB (the system).
  - Disks 2 and 3: 20 GB each (data, mirrored).
  - Disk 4: 20 GB, added **later** while running (plays the USB backup disk).
- Real hardware: an old PC with one small SSD and two data disks, and a USB
  stick or USB disk for the backup disk.

## 1. Install

1. Boot the ISO. The installer starts by itself on the first console.
2. Choose the system disk. It must not offer to install onto a disk that
   already has data without a clear warning.
3. After the reboot the console shows the address, like
   `http://192.168.1.50:8080`.

Expected: no errors on the console; the address opens the setup page.

## 2. First setup (browser)

1. Welcome › Continue.
2. Name and password: a name like `alva-test`, a password of 8+ characters.
3. Storage: disks 2 and 3 are offered (not the system disk). Choose both;
   "Erase and set up storage" makes a mirrored pool.
   - Also try once with no free disk: "Look again" must look again, not
     complain about devices.
4. Shared folder: keep "Files", add a person (e.g. `anna`), keep "Back up
   this folder".
5. Done: shows how to open the folder on Windows, Mac and the phone.

Expected: the dashboard shows "Getting started" with the steps done ticked,
storage healthy, one shared folder.

## 3. Shared folders over the network

1. Windows: `\\alva-test\Files` (or the address), sign in as `anna`.
   Mac: Finder › Go › Connect to Server › `smb://alva-test/Files`.
2. Copy a few files and a folder in, rename one, delete one.
3. AlvaOS Files (step 4) › Trash: the deleted file is there and can be put
   back.

## 4. AlvaOS Files (the app in the browser)

1. Files page › turn on AlvaOS Files › open it (port 8090).
2. Sign in as `anna`. Upload photos (also a big file, > 1 GB, and stop
   and resume it), make a folder, move and copy, download a ZIP.
3. Photos: pictures appear by month, with the date they were taken.
4. A file › Previous versions (needs a restore point, see step 6).
5. Share link: read-only, then a "drop box" with a limit; open it in a
   private window and upload into the drop box.
6. On a phone: open it, "Add to home screen" (needs HTTPS, step 9).
7. WebDAV: Windows "Map network drive" or Finder › `http://alva-test:8091`.

## 5. People and space limits

1. Storage › People › add `ben` with a personal folder on the pool and a
   limit of 1 GB.
2. As `ben`, copy more than 1 GB in: it stops at the limit; the dashboard
   warns at 90 %. **(only on a real machine: needs Btrfs quotas)**
3. Storage › Shared folders › give "Files" a limit, then remove it again.

## 6. Backup

1. Backup › turn on automatic restore points (every hour).
2. "Back up now"; then change a file and restore the old version, also a
   whole folder.
3. **Backup disk (only on a real machine):** plug in disk 4 / a USB disk,
   make it a pool in Storage (only that disk), then Backup › Backup disk ›
   choose it › "Copy now". The card shows "Copying…" and then the time of
   the last copy. "Safely remove" › the card says "Safe to unplug"; unplug
   it; plug it in again: after at most 10 minutes it copies again.
   Restore › the copies are listed ("on the backup disk").
4. Buddy Backup with a second AlvaOS (a second VM) if you have one.

## 7. Remote access (only on a real machine with a router)

1. Settings › Remote access › turn on. "Find it" fills in the public address
   (better: a dynamic DNS name like `xyz.duckdns.org`). Save.
2. In the router, forward the UDP port shown (51821) to the NAS address
   shown.
3. "Add a device" › name it › on the phone install WireGuard › scan the QR
   code. Turn off Wi-Fi on the phone (use mobile data), switch the tunnel
   on, open `http://100.96.96.1:8080`. AlvaOS opens; Files is at
   `http://100.96.96.1:8090`.
4. The device list says "Connected now". Remove the device: the phone can
   no longer connect.

## 8. Apps

1. Apps › install one (e.g. Jellyfin), open it, restart it, update it.
2. Uninstall it; its data folder stays unless you choose otherwise.

## 9. HTTPS

1. Settings › Security › HTTPS › "Set up devices": download the certificate,
   install it as trusted (steps per system on that page).
2. Open `https://alva-test:8443`: no warning. Files on 9443.
3. Turn on "HTTPS only" (only possible from the https page): plain
   `http://…:8080` now goes to https.

## 10. Assistant

1. Settings › Assistant: Ollama on another computer (`ollama pull
   llama3.1`, start it with `OLLAMA_HOST=0.0.0.0`) or a cloud service.
   "Try it" answers.
2. The robot button at the top: "How is my NAS doing?", "Why did the last
   backup fail?", "How full are my disks?". It names what it looked at.
3. Switch to "Suggest changes, I confirm": "Make a shared folder Photos for
   anna" › a card with "Do it" appears; nothing happens before "Do it".
4. Links in answers (like "Storage › Shared folders") open the right page.

## 11. Notifications, disks, power

1. Settings › Notifications: email or Telegram › "Send a test".
2. Pull a data disk out of the mirror (VM: detach disk 3): the dashboard and
   the notification say a disk is missing; files are still there. Put it
   back / replace it in Storage.
3. Settings › Power: disk sleep; restart and shut down from the web page.

## 12. Updates

1. Updates › check. When a newer signed release exists: install it; the
   page is away for a minute and comes back with the new version.
2. If an update fails, AlvaOS goes back to the version before by itself.

## What to send back

For each problem: the step number, what you expected, what happened, and if
possible a screenshot. Settings › Diagnostics has the logs.
