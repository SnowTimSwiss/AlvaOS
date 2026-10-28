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

## 4. AlvaOS Hub and Files (in the browser)

1. Hub page › turn on AlvaOS Hub › open it (port 8090). The app bar shows
   Files, Photos and Calendar (Chat after it is turned on). On the Hub page set Photos to "Only some people" and
   untick someone: they no longer see Photos; turn Files off for them: they
   see "No apps for you yet", and WebDAV refuses them.
2. Sign in as `anna`. Upload photos (also a big file, > 1 GB, and stop
   and resume it), make a folder, move and copy, download a ZIP.
   Select: draw a rectangle with the mouse from an empty spot over a few
   files (Ctrl or Shift adds to the selection); the bar on top shows them
   and nothing below it moves. Click beside the files: nothing is selected.
   Shared links, Trash and Devices sit at
   the bottom of the side bar, above your name and "Sign out".
   Delete a file, then Trash: it opens like a folder (not a window), with
   where it was and when; switch between your shared folders at the top,
   search in it, "Put back" brings it back to its folder.
3. Photos: pictures appear by month, with the date they were taken. Copy
   old phone pictures in over the network (the copies get today's file
   date), open Photos, wait a minute and open it again: they move to the
   month they were taken in. On the
   Hub page › Where things are kept: make personal folders for those
   missing; mark "Files" as a photo library; Photos shows anna's own
   `Photos/` folder and the library together. Switch Photos to "A folder
   per person on" a pool with a limit: Storage › Shared folders lists
   `anna-photos` with that limit. Choose a pool for thumbnails: they are
   made in `.alvaos-hub/thumbs` on it.
4. A file › Previous versions (needs a restore point, see step 6).
   Open a .txt file › Edit, change it, Save (or Ctrl+S): it is changed over
   SMB too, and the old one is in the trash.
5. Share link: read-only, then a "drop box" with a limit; open it in a
   private window and upload into the drop box.
   Drag a folder with subfolders (and an empty one) from the computer into
   Files: the same tree appears, with one row while it uploads; New ›
   Upload a folder does the same. As `anna`, right-click a folder of her
   own › Share with people… › tick `ben`, "Look at and download": ben
   sees "<folder> (from anna)" in Files and over WebDAV, can open and
   download but not change; switch to "Also add, change and delete": ben
   adds a file, anna sees it (also over SMB, owned by anna). Stop sharing:
   it is gone for ben.
6. On a phone: open it, "Add to home screen" (needs HTTPS, step 9).
7. WebDAV: Windows "Map network drive" or Finder › `http://alva-test:8091`.
8. Calendar (as `anna`, who has a personal folder): drag in the week to
   make an event, drag it to another day, pull its lower edge; make one
   that repeats every week; add a calendar "Work" in another colour and
   hide it; add a task with a day (it shows in the calendar) and tick it in
   the tasks panel. Hub page › Where things are kept › Shared family
   calendars: tick "Family": anna changes it, someone with read-only
   access only looks. Try month, schedule and the 3 days on a phone.
   On a phone: Calendar › "On your phone and computer" › follow the steps
   (iPhone: install the certificate first, step 9). The calendars and tasks
   show up; an event made on the phone appears in the Hub after a reload,
   one changed in the Hub reaches the phone; the Family calendar is
   read-only on the phone for someone with read-only access. Android: the
   same with DAVx5.
9. Chat: Settings › AI: set up an AI service (Ollama at home is
   enough) with two models. Hub page › Chat: turn it on. In the
   Hub: a new chat, switch the model, "Think" on with "High" (a thinking
   model like qwen3 shows "Thought for … seconds"), stop an answer, rename
   and delete a chat. Ask it about the NAS: it does not know and cannot
   change anything.

## 5. People and space limits

1. Storage › People › add `ben` with a personal folder on the pool and a
   limit of 1 GB.
2. As `ben`, copy more than 1 GB in: it stops at the limit; the dashboard
   warns at 90 %. **(only on a real machine: needs Btrfs quotas)**
3. Storage › Shared folders › give "Files" a limit, then remove it again.
4. In the Hub as `ben`, open the personal folder: the sidebar shows "… of
   1 GB used", the same as Storage › Shared folders, amber above 90 %.

## 6. Backup

1. Backup › turn on automatic restore points (every hour).
2. "Back up now"; then change a file and restore the old version, also a
   whole folder.
3. **Backup disk (only on a real machine):** plug in disk 4 / a USB disk.
   Backup › Backup disk offers it: "Erase and use as backup disk", type
   ERASE; the first copy starts by itself. The card shows "Copying…" and then the time of
   the last copy. "Safely remove" › the card says "Safe to unplug"; unplug
   it; plug it in again: after at most 10 minutes it copies again.
   Restore points › the copies are listed ("On the backup disk"); "Get
   files" works while it is connected. Unplugged, Storage shows it as
   "Backup disk · Not connected" and no alarm goes off.
4. Buddy Backup with a second AlvaOS (a second VM) if you have one, both
   with AlvaOS Link on and **no router setting on either side**: on the
   first, Backup › Buddy › make a pairing token; on the second paste it.
   Both list each other (status "Connected", then the time last seen).
   Start a backup: it runs through Link (a direct connection when
   possible; slower through a relay). A buddy paired before the update says
   "repair": pair again. Settings › AlvaOS Link lists the buddy, too.
   Recovery kit: make one, restore on a fresh NAS: the Link address stays.

## 7. AlvaOS Link (away from home)

Needs the NAS with the internet and a phone on mobile data (Wi-Fi off).

1. `systemctl status alvaos-link` is active (not in a restart loop: `journalctl -u alvaos-link -n 20`). Settings › AlvaOS Link
   shows the switch on, the sentence "Phones and buddies can reach this NAS
   from anywhere" and a Link address (a long code, shown short).
2. Switch off: the sentence says it is off, `systemctl status` still runs
   but nothing connects. On again: running within a few seconds.
3. Hub › Devices › show the QR code: its text under the code says
   the phone also works away. Scan it with the app **on mobile data** (not
   at home): "Connecting…" then the Hub opens. The settings of the app say
   "Through AlvaOS Link". The NAS list under AlvaOS Link names the phone.
4. Pair the phone at home, then switch Wi-Fi off: the Hub, Files and the
   photo backup keep working (the first start away can take some seconds).
5. Devices › sign the phone out: it is gone from the list under
   AlvaOS Link and can no longer reach the NAS.
6. No router setting was needed anywhere, and no account.
7. The app build without the Link library (CI step "Build the Link library
   failed"): the app still works at home; settings say "not available in
   this build".
8. A NAS that had the old Remote access (Tailscale, Cloudflare, WireGuard)
   on: after the update there is no Remote access page, `ip link` shows no
   `remote0`, and old buddies show "repair" (see 6, step 4).

## 8. Apps

1. Apps › install one (e.g. Jellyfin), open it, restart it, update it.
2. Uninstall it; its data folder stays unless you choose otherwise.

## 8b. Virtual machines (a computer with VT-x/AMD-V, on, in the BIOS)

1. Virtual machines › *Set up*, choose a pool: QEMU and the firmware are
   installed, and Storage › Shared folders lists `VMs`. On a machine without
   virtualization the page says why and offers nothing else.
2. Put a Linux installer (.iso) into `VMs/ISOs` (the Hub › Files as
   administrator uploads it). *New virtual machine* › Linux, the installer,
   Create › Start › *Open screen*: the installer shows, the keyboard and mouse
   work. Install, then Settings › Installer: None, start again: it boots from
   its disk.
3. *Shut down* asks the guest to power off (it does, within a minute);
   *Switch off* stops it at once. Restart the NAS with a machine running and
   "start with the NAS" on: it stops cleanly before the disks go, and
   starts again afterwards.
4. A Windows 11 machine (the ISO in `VMs/ISOs`): the installer starts without
   complaining about the TPM or Secure Boot. Forward a port (3389) and
   reach it from another computer.
5. Delete a machine: its folder in `VMs` is gone. Space limits and restore
   points of `VMs` work like for any shared folder.
6. HTTPS: open the admin page over `https://` and the screen: it uses port
   9445 (the NAS's certificate has to be trusted there too).
7. Disk size: shut a machine down › Settings › Disk 80 → 120 › Save. Start
   it; in Windows, Disk Management shows 40 GB unallocated after C:,
   "Extend Volume" takes it. A smaller number is refused.
8. Settings › Disks, network and devices:
   - Second disk 50 GB: Windows shows a new disk to initialise.
   - Second CD `virtio-win.iso`: install the drivers in Windows, then tick
     "Fast disks and network": it still starts, Device Manager shows
     VirtIO devices.
   - "Its own address at home": the router lists the machine with its own
     address; a laptop at home reaches it (RDP) without a forwarded port.
     After stopping it, `ip link` on the NAS shows no `mvt…` left.
   - A USB stick ticked: it shows up in the machine, not on the NAS.
   - A second graphics card (not the one with the NAS's screen, IOMMU on in
     the BIOS): the machine shows it in Device Manager; after stopping,
     Settings › Graphics shows it back on its own driver.
   - Priority low: a big copy to the NAS stays fast while the machine works.

## 9. HTTPS

1. Settings › Security › HTTPS › "Set up devices": download the certificate,
   install it as trusted (steps per system on that page).
2. Open `https://alva-test:8443`: no warning. Files on 9443.
3. Turn on "HTTPS only" (only possible from the https page): plain
   `http://…:8080` now goes to https.

## 9b. SSH (Settings › Security)

1. Turn SSH on. `ssh root@alva-test` signs in with the admin password.
2. Add your Ed25519 public key, turn password sign-in off, save:
   `ssh root@alva-test` signs in with the key, a password is refused.
3. The same key must **not** sign in as any other account with a shell
   (`ssh someone@alva-test`), and a key in an account's own
   `~/.ssh/authorized_keys` (also `/root/.ssh/authorized_keys`) still works.
4. Change the port to 2222 and save: an open SSH session stays, new ones
   need `-p 2222`.
5. Break the main config on the console (add a line `Bogus yes` to
   `/etc/ssh/sshd_config`), then save in the page: it says SSH did not
   accept the settings and nothing changed; remove the line again.

## 10. Assistant

1. Settings › AI: Ollama on another computer (`ollama pull
   llama3.1`, start it with `OLLAMA_HOST=0.0.0.0`) or a cloud service.
   "Try it" answers.
2. The robot button at the top: "How is my NAS doing?", "Why did the last
   backup fail?", "How full are my disks?". It names what it looked at.
3. Switch to "Suggest changes, I confirm": "Make a shared folder Photos for
   anna" › a card with "Do it" appears; nothing happens before "Do it".
4. Links in answers (like "Storage › Shared folders") open the right page.

## 10b. Terminal (Settings › Terminal)

1. Open terminal: a prompt as `alvaos`. `ls /mnt/alvaos`, `df -h`, a long
   `top` (q to quit), resize the window: the terminal follows.
   `sudo -i` is refused (no root shell).
2. Over HTTPS (9443 for the page): the terminal opens on 9446 once the
   certificate is trusted.
3. Sign out in another tab: the terminal closes within half a minute.
   Leave it 30 minutes without typing: it closes and says why.
4. Assistant: ask "why is my NAS slow?" and "is Samba running?": the answer
   shows "Ran: `ps …`" / "Ran: `systemctl status … smbd`". At the "ask"
   level, ask it to restart Samba: it proposes, and runs only after the click.

## 11. Notifications, disks, power

1. Settings › Notifications: email or Telegram › "Send a test".
2. Pull a data disk out of the mirror (VM: detach disk 3): the dashboard and
   the notification say a disk is missing; files are still there. Put it
   back / replace it in Storage.
3. Settings › Power: disk sleep; restart and shut down from the web page.

## 11b. UPS (only with a real UPS on USB)

1. Connect the UPS by USB. Settings › Power › Battery backup › UPS names it
   (for example "American Power Conversion Back-UPS ..."). "Set up", keep
   "When the battery runs low": after a minute or two the row reads
   "On mains · 100% · about N min".
2. Pull the UPS's plug from the wall: within 30 seconds the bell says "The
   power failed" and the row turns red "On battery". Plug it back in: "The
   power is back".
3. Change to "After 2 minutes on battery", pull the plug and wait: the bell
   says "Shutting down", the NAS shuts down cleanly, the UPS switches its
   outlets off a little later. Plug it back in: with "Restore on AC power
   loss: Power on" in the BIOS the NAS starts by itself, and the pools,
   shares and apps come back.
4. Unplug the USB cable: after a minute the alerts say "The UPS does not
   answer". Plug it back.
5. "Change" › "Turn off": the row offers "Set up" again and nothing shuts
   down in a power cut.
6. Note the UPS model and whether it worked; models that need another
   driver than usbhid-ups or nutdrv_qx are worth an issue.

## 12. Graphics card (only with a real card)

1. Settings › Graphics lists the card with its model and driver.
2. Intel/AMD: "Install firmware and video drivers"; afterwards the state is
   "Ready" (after a restart if the card had no driver before).
3. NVIDIA: "Install NVIDIA driver" (a few minutes), then "Restart now";
   after the restart the driver reads "nvidia" and the state is "Ready".
   With Secure Boot on, the NAS asks for the MOK key on its screen once.
4. NVIDIA on an older kernel than the newest offered (install the ISO,
   skip updates): the install stops right away with "Install the system
   updates ... restart the NAS"; after doing that it works. An interrupted
   install (switch off during it) offers "Repair package setup".
5. Apps: install Jellyfin, open it in Apps › Installed › "Graphics card",
   turn it on (the app restarts). In Jellyfin turn on hardware
   acceleration as the line under the switch says and play a video that
   needs converting: the dashboard shows the transcode using the card
   (`intel_gpu_top` / `radeontop` / `nvidia-smi` on the NAS show load).
   Turn it off again: Jellyfin still starts. Ollama with an NVIDIA or AMD
   card: `ollama ps` in the app's terminal shows "GPU". NVIDIA: if
   NVIDIA's driver was installed before this version, Settings › Graphics
   offers "Install what apps need" (the container toolkit) first.

## 12z. The Android app: AlvaOS in the Files app

1. Open the phone's Files app: the side menu has "AlvaOS – <NAS name>". Open it: the shared folders.
2. Open a picture and a PDF from there; open the same one again (fast, from the phone's copy). Change it on the NAS and open it again: the new one.
3. Make a folder, rename it, move a file into it, delete a file: all show in the Hub's Files (the deleted one in the trash).
4. In another app (Gmail, a notes app): "Attach" or "Save to" › AlvaOS › a folder. Saving a file with a name that is there makes "name (1)" or replaces it (the old one in the trash).
5. A read-only share: no "new folder", no delete.
6. Away from home (mobile data): the same, through Link.
7. Sign out in the app: the entry is gone from the Files app.

## 12x. The Android app: the new setup

1. Fresh install: a welcome screen (the logo glows and floats), "Get started" goes to "Connect your phone" (phone and NAS with lights travelling between them).
2. Scan the QR code: the lights speed up, the line closes in green, then "Connected to <NAS>" with a drawn check mark.
3. The cards: "Back up your photos" on, and the options (Wi-Fi only, charging only, deleting) as cards; switch backup off: the options dim. "Continue" asks for access to the pictures, then "Which albums?" (Camera is ticked), "Start the backup".
4. "All set" with rings, what was set up, "Open AlvaOS" opens the bar with the Hub apps.
5. Back button in the album step goes back to the cards. A phone that was signed in before the update shows no setup.
6. Settings › Accessibility › Remove animations: everything stands still, nothing is missing.

## 12b. The Android app: connect, the Hub, photo backup (Android 11 or newer)

1. On the phone open GitHub › Releases › the newest AlvaOS release and
   download `alvaos-android.apk` (between releases: Actions › "Android app"
   › the newest run › Artifacts › `alvaos-android`, unzip). Install it
   (allow "install unknown apps" for the browser once). Once the upload key
   is set up (`docs/ANDROID.md`), uninstall the old debug build once.
2. On a computer open the Hub, side bar › **Devices** › Connect
   a phone: a QR code and a code appear. In the app tap **Scan the QR
   code** and point it at the code: the app connects without a password
   and shows the Hub; the computer says "<phone> is connected" and lists
   it (last used, model, app version). Also try: scanning the QR code with
   the phone's own camera opens the app and connects; "Sign in with
   name and password". An old or used code says so.
3. The **Hub** tab: every app the person has (Files, Photos, Calendar,
   Chat, the store apps) works as in the browser; downloading a file lands
   in Downloads; uploading opens the phone's file picker; there is no
   "Sign out" in the Hub (it is in the app's Settings).
4. In the Hub on the computer › Devices: rename the phone, then
   **Sign out**: opening the app again says it was signed out on the NAS.
   Connect it again.
5. The **Backup** tab › Set up backup: allow pictures. Choose two albums
   (Camera and one more), keep "Only on Wi-Fi", tap "Back up these
   albums"; the tab shows "Backing up 3 of 40" while it runs. A notification shows the progress. In the Hub ›
   Photos the albums appear above the timeline (Camera · <phone>); the files
   are in Files › own folder › Photos › <phone> › <album>, with the date
   they were taken.
6. Take a new photo and **wait**, without tapping anything: within a minute
   or two (Wi-Fi, screen off is fine) it is on the NAS. That is the watcher
   for new pictures; "Back up now" does the same at once. Tap it again:
   nothing is uploaded twice. With "Only while charging" on (Settings) a new
   picture waits until the charger is plugged in; the Backup tab says
   "Waiting for the charger".
6a. A big first backup (hundreds of pictures): the Backup tab counts up
    live ("Backing up 120 of 600", each album "x of y backed up"). Lock the
    phone and wait ten minutes or more: it goes on by itself (Android ends
    a background job after ten minutes; the app starts the next one at
    once). Turn the NAS off in the middle: the tab says "Waiting for the
    NAS", turn it on, and it goes on without anything from you.
7. Delete a backed-up photo **on the phone** (Gallery, and empty the
   phone's own trash if it has one), "Back up now": on the NAS it is in
   the trash of the personal folder, not gone.
8. After choosing the albums the app offers "Keep deleting in sync":
   Allow, turn on AlvaOS, come back. Delete a backed-up photo **in the
   Hub** (Files › Delete), "Back up now" on the phone: it is gone on the
   phone without a question (at the latest when the app is opened next).
   With "Not now" instead, the app says "1 was deleted on your NAS" and
   Android asks once for each batch.
9. **Move** a backed-up photo into another folder in the Hub, "Back up
   now": nothing is deleted on the phone, nothing uploaded again.
10. "Change albums", untick one: its pictures stay on the NAS.
11. "Free up space": photos backed up and older than a month go from the
   phone (Android asks), they stay in Photos on the NAS.
12. Turn the phone's Wi-Fi off with "Only on Wi-Fi" on: no backup on mobile
    data until Wi-Fi is back.

## 12c. The app and the Hub look as one

1. In the app, Files: no side bar, no arrows, no status line at the bottom;
   the title is the folder; the shared folders are chips under the bar (when
   there is more than one); "⋯" has Show as a list, Shared links, Trash,
   Devices. The phone's back gesture goes up a folder.
2. Photos: the title says Photos, a card shows the backup ("Backing up 16 of
   32" with a bar, or "120 pictures backed up"; Open goes to the Backup
   tab), the albums are chips, an empty library fills the width.
3. Backup and Settings look like the Hub (the same blues, cards, icons)
   in light and dark; the colours do not follow the wallpaper.
4. Settings › Trash opens Files with the trash; Devices opens
   the list.

## 12e. Photos: favourites and albums

1. Photos › Select › tap a few pictures › Add to album › New album "Summer":
   the album opens with them; its chip is in the row; Photos in the app too.
2. Open a picture: the heart in the bar makes it a favourite (a small heart
   on its tile); the Favourites chip lists them; Select › "Remove heart".
3. In an album: Rename, Delete album (the pictures stay), Select › Remove
   from album. Add the same picture twice: it is there once.
4. A picture moved or deleted in Files drops out of its albums by itself.
5. Another person does not see these albums (they are in your own Photos
   folder, hidden); in Files they are `.alvaos` under Photos.

## 12d. Videos and more picture formats (browser and app)

1. Put an MP4 from a phone, a MOV, an MKV, an old AVI and a HEIC or TIFF
   picture in a folder: the grid shows a still of each video with a small
   play mark, and the pictures as pictures. Photos shows the videos in the
   timeline too.
2. Open the MP4/MOV/MKV: it plays (the first frames can take a moment on a
   big file); in the app the full-screen button works and the back gesture
   leaves full screen.
3. Open the AVI: "This video cannot be played in the browser as it is". Press
   "Make a copy that plays here": "Converting… 35%" and then it plays. Close
   and open it again: it plays at once (the copy is kept).
4. On a NAS without ffmpeg (`which ffmpeg` in Settings › Terminal) the AVI
   says so and offers Download; MP4/WebM/MOV/MKV still play.
5. The HEIC/TIFF opens as a picture (HEIC needs `libheif-examples`; the
   installer brings it).

## 13. Updates

1. Updates › check. When a newer signed release exists: install it; the
   page is away for a minute and comes back with the new version.
2. If an update fails, AlvaOS goes back to the version before by itself.

## What to send back

For each problem: the step number, what you expected, what happened, and if
possible a screenshot. Settings › Diagnostics has the logs.

## 12f Drop box notice

1. Make an upload link for a folder (Share link › Upload only) and open it in a private window.
2. Upload two files. In the Hub, Shared links shows "2 new" (reload once).
3. Open Shared links: the link says "2 new files"; close and reopen: the mark is gone.

## 12g Recent and Starred

1. Open two files in Files. Side bar › Recent lists them, the newest first.
2. Right-click a folder › Star. Side bar › Starred shows it; open it from there. Right-click again › Remove from Starred.
3. Sign in as another person in the same browser: the lists are different.

## 12h Contacts

1. Hub › Contacts: New contact (name, a phone number, an email, a birthday), Save; Edit, heart, Delete (asks twice).
2. Menu › Import a vCard file: export your contacts from your phone or Google as .vcf and import them. Import it again: "0 added".
3. iPhone: Settings › Contacts › Accounts › Add Account › Other › Add CardDAV Account; server = the Hub address, your AlvaOS name and password. The contacts appear. Change one on the phone, reload the Hub: the change is there. Change one in the Hub, pull to refresh on the phone.
4. Android: DAVx5, URL `https://<nas>:9443/dav/`; tick Contacts. Same checks.
5. Hub › Settings (admin) › Hub: turn Contacts off for someone: their phone says it is not turned on for them.

6. Contacts: give a contact a birthday; Calendar then lists "Birthdays" (read only) with a yearly all-day entry.

## 12i Search filters

Search for a word that matches different kinds of files. The chips above the results narrow them by kind and by when they changed; a kind that was not found has no chip.

## 12j Share to AlvaOS (Android)

1. In the phone's gallery or a file manager: select a picture and a PDF › Share › Save to AlvaOS.
2. Choose a shared folder: "Saving 1 of 2", then "2 files saved". In the Hub, Files › that folder › From phone has both.
3. Share the same files again: they arrive as "name (2).jpg" (nothing is overwritten).
4. Switch the phone to flight mode half-way through a large file: it should say "That did not work" and offer to try again.

## 12k Photos upload

Photos › Upload: choose a few pictures from the computer or phone. They appear in the timeline (newest by their own date) and are in Files › your photos › Uploads.

## 12l Backup warning

Switch off the NAS (or the Hub) for three days with backup on and the phone online: a notification "No backup for 3 days" appears once a day; after the NAS is back and a backup worked, it goes away. (Shortcut: set the phone's clock three days ahead.)

## 12m Time in the title

Calendar › Create: type `20:00 Choir` as the title › Save: the event is at 20:00 and called Choir. Try `19:30-21 Choir`, `Choir um 20 Uhr`, and `5 friends` (stays a title).

## 12n Add a birthday

Calendar › Create › Birthday: type a name and a year; choose "And make a new contact": the contact exists and the Birthdays calendar shows it. Again with "Add to a contact" and with "Only in the calendar" (a yearly entry in your own calendar). Try it on the phone too.

## 12o Quick wins

1. Calendar › Birthdays › the menu next to it: pick a colour. A contact born in 1985 shows "(41)".
2. Files: search, then the size chips narrow the results.
3. Share a folder with another person: in their Files side bar it says New until they open it.

## 12p Link on the phone

1. Install the app from CI, scan a QR code at home, then leave Wi-Fi: the
   Hub opens (see 7). Settings › "AlvaOS Link: On …".
2. A build without the library: settings say "not available in this build";
   nothing else breaks.

## 12q From the first real test

1. Files opens on your own folder, on a computer and on a phone. A folder shared with you that you cannot open is not where it starts.
2. Files: right-click a folder › Pin to side bar: it is under "Pinned"; the ✕ takes it away. Inside a folder, right-click the empty space › Pin this folder.
3. Photos with many pictures: the strip at the right edge shows years; drag it, the month shows next to the handle; tap a place on it to jump.
4. Calendar on a phone: tap a day (Month view): the Create window stays open and sits above the keyboard.
5. Back gesture (phone or app): open Devices, swipe back: it closes, and you are still in the same folder. Open the folder drawer, back: it closes. Open a picture, back: the viewer closes.
6. Swipes: from the left edge opens the folder drawer (not in the app); in the viewer swipe left and right for the next picture, down to close; in the calendar's month view swipe left and right for the next month; drag a window down by its top to close it.
7. Devices, shared links and previous versions are whole screens on a phone and in the app; the app's Settings › Devices opens it the same way. Pinned folders show as chips above the files in the app.
8. With "reduce motion" turned on in the phone's settings nothing slides.
9. In a browser, Devices › Sign out for the phone. On the phone, open the app or do something in the Hub: it shows the app's "Connect to your NAS" screen with the message that it was signed out, not the Hub's sign-in page.



## 12r Photos slider and Files home

1. Photos with a few hundred pictures: the strip at the right edge shows years with dots for the months. Move the mouse over it: the date under the pointer shows. Drag: the page follows; the bar sits on the year you are at.
2. Photos with only a few pictures: no strip.
3. Photos has no side bar and no folder button; Files has them again.
4. Open a deep folder in Files, go to Photos, then click Files: you are in your own folder, not in the deep one.
\n

## 12yb The Windows sync folder (cloud files, desktop app)

Windows 10 1809 or newer, NTFS. Not run on a real Windows yet: please go through this once.

1. Add a sync folder (an empty folder): no switch is shown; the text says files download when opened. The folder appears in Explorer's side bar as "AlvaOS – <share>" and shows every file with a cloud icon within a few seconds.
2. Open a picture and a document: they open (a progress shows for a bigger file); the icon turns to a green check. Open a film: it starts at once.
3. Right-click a file: "Always keep on this device" (green circle) and "Free up space" (back to the cloud). Both work.
4. Make a new file and a new folder in it: after a minute they are on the NAS (Hub › Files); the file shows a green check. Save over an existing file in an editor: the NAS has the new content, the old one is in the trash.
5. Delete a file and rename another: on the NAS the same (deleted: in the trash). Move a file into a folder: moved on the NAS.
6. Change a file on the NAS (Hub): after a minute the placeholder updates (opens with the new content). Delete one on the NAS: gone here.
7. Change the same file here and on the NAS before a round: both are kept (`… (conflict …)`).
8. Quit the app: the folder stops answering (opening a cloud file fails); start it: it works again. Remove the sync folder in the app: only downloaded files stay.
9. If anything does not work: set `ALVAOS_CLASSIC_SYNC=1` (system environment variable), restart the app, and add the folder again: the old kind of sync folder.

## 12ya Files on demand (Linux)

1. Install the .deb (or `sudo apt install fuse3`). Add a sync folder with **Only download what you open**, an empty folder: it shows up with every file of the share at once (a few seconds), tray: "N files · 0 KB here".
2. Open a picture in the folder: it is fetched; the "…" window shows more "here". Open a big film: it starts at once.
3. Save a document in a text editor: it is on the NAS (Hub › Files) and the old version is in the trash. Make, rename, delete a file and a folder.
4. "Always keep on this computer": `Photos/`; go offline: those still open, others do not. "Free up space": the rest is freed.
5. Cut the connection while saving: the file stays, goes up in the next round after it is back. Quit the app: the folder is empty again (not mounted); start it: the files are back.
6. Remove the sync folder: unmounted; nothing on the NAS is deleted.

## 12w The sync folder (desktop app 0.6)

1. Set up a computer: after "Connected", keep "A sync folder" on, pick a share or a folder, "Keep it here". It appears in `~/AlvaOS/<share>/...` within a minute.
2. Make a file in that folder: it is on the NAS a few seconds later, with the same date.
3. Change a file on both sides before a round: both versions stay (`(conflict …)`).
4. Delete a file on the NAS: it lands in this computer's trash. Delete one here: it lands in the share's trash on the NAS.
5. Delete 25 files here at once: the app asks; try both "Bring them back" and "Delete them too".
6. Pause in the settings: nothing moves; switch back: it catches up. Tray: "Sync now", "Open the sync folder".
7. Windows (and Linux): the same, once each.
8. Sync folder "…" › Don't sync: add `*.mov`, save; a .mov in the folder is not sent, one on the NAS is not fetched, one synced before stays on both sides.
9. Copy a big folder in: the row shows "4 of 12 · name" and a bar; the "…" window lists what was sent. Pull the network cable in the middle: the file is tried again, no error shows for a short break.

## 12v The desktop app (Windows, Linux, macOS)

1. Install it from the release (Windows `.msi` or setup `.exe`; Linux `.deb`, `.rpm`, `.AppImage` or the Flatpak; macOS `.dmg`). Not signed yet: Windows "More info › Run anyway", macOS System Settings › Privacy & Security › Open anyway.
2. In the Hub: Devices › Connect a computer › Copy. In the app: Paste a link › it says "A link for anna on cygnus, works from anywhere" › Connect. A wrong or old code says so. Also try "Type the code" (at home) and "Name and password" (with two-step sign-in: it asks for the code).
3. "What do you want to use?": keep the window, tick the drive (Linux, macOS), start with the computer › Done. The Hub opens in a window, already signed in. The Hub's Devices lists the computer with a screen icon and its system.
4. The tray: the status line ("Connected to cygnus · direct · … ms"); Open AlvaOS; Open in the browser (signed in by itself, at `http://127.0.0.1:18090`); Show Files as a drive (Linux: GNOME Files › Other Locations; macOS: Finder); switch "Connected" off and on.
5. Away from home (phone hotspot): everything the same; the status may say "through a relay".
6. A download in the Hub's window lands in Downloads; a link to another site opens in the browser.
7. Thunderbird or GNOME Calendar: CalDAV at `http://127.0.0.1:18090/` with name and password: the calendars come, also away from home.
8. Restart the computer: the app starts in the tray (no window) and connects by itself.
9. In the Hub sign the computer out (Devices): within a minute the app shows "This computer was signed out on the NAS. Connect it again." In the app: Sign out › it is gone from Devices too.

## 12u AI: several services, who may use which, private chats

1. An NAS that had one AI service before: Settings › AI shows it as one
   service with its model; the assistant still answers; Chat still offers
   the models chosen before.
2. Settings › AI › Add a service: Ollama Cloud (or OpenRouter as "Another
   OpenAI-compatible service") with its key. "Find models" lists what it
   has; click two to add them, type a third ("asked when saved"). Save:
   the thinking models (gpt-oss, qwen3, deepseek-r1) show "thinks" (gpt-oss
   and models OpenRouter marks with `reasoning`: "thinks · low/medium/high"),
   asked from the service; nothing to change by hand. "Try it" answers. Edit
   it again: the key stays ("Saved. Type to replace it.").
3. The assistant: choose a model of the new service, Save; the robot answers.
4. Hub page › Chat: "All but these" › untick nothing, tick one model ›
   "People choose from" leaves it out › Save. Models for anna › "Of those,
   only these" › tick a whole service › Save: the line says what anna gets.
   In the Hub as anna: only those models, grouped by service; as ben: all
   but the one.
5. In the Hub: a model that answers at once has no "Think"; qwen3 has
   "Think" without Low/Medium/High; gpt-oss has both.
6. Formatting: ask for "a packing list as a table, a numbered plan with
   sub-points, a code example and a checklist": numbered list with
   sub-points indented, the table with lines, the code block with Copy, the
   checklist with boxes.
7. Private (ghost top right): ask two things in a row (it remembers the
   first), then New chat: it is gone, not in the list, and nothing new in
   `.alvaos/chat/` of the personal folder.
8. Pin one chat (⋯ › Pin: on top under "Pinned"), keep another (⋯ › Keep:
   a bookmark). Hub page: "Chats nobody used for" 7 days. A chat not used
   for 8 days (set the date of `updated` in `chats.json` back by hand)
   is gone the next time Chat opens; the pinned and kept ones stay.
10. Web search: Settings › AI › "Web search in Chat" on, "Try it" shows
    three titles. In the Hub a "Search" pill; ask "Weather in Bern
    tomorrow?": "Searching the web for …", then an answer with small [1]
    links and the sources below (opening in a new tab). Ask "and the day
    after?": it searches again with a sensible query. Hub page › Web search
    › Only some people › only ben: anna has no "Search" any more.
9. The flame on a chat (hover; on a phone always there): "Delete this
   chat?" › Delete: gone. The flame next to "New chat": "Delete all chats?"
   says how many go and how many stay; Delete: the others are gone, no
   animation. Again with "Also delete pinned and kept chats" ticked: the
   list is empty.

## 12t The phone stays signed in

1. Pair the phone, then in the Hub as admin switch two-step sign-in on and off (Settings). The phone stays signed in.
2. Restart the NAS (or the `alvaos-files` service) with the app open, then open the app again: it stays signed in (it may need a few seconds).
3. In the Hub › Devices sign the phone out: the app shows the connect screen within a few seconds of opening it. Changing the admin password does the same.

4. The release build (from the Play .aab or the signed APK) is minified: connect with the QR code, open a Link connection away from home, run a backup, play a video full screen and rotate. Nothing may crash.
5. In the Play Console the 16 KB and edge-to-edge warnings should be gone with the next upload.

## 12s The demo (Android app)

1. On the first screen "Try the demo" opens the app with Files, Photos, Calendar, Contacts and Chat: sample folders and files, 140 pictures with the time slider, events in the calendar, five contacts, a chat that answers. The yellow "Demo · sample data" mark is in the corner.
2. Create an event, a contact, a folder: it appears. Close the app completely and open it again: the changes are gone (nothing is kept) and the demo is still on.
3. Backup tab: only an explanation, no permission asks. Settings: "This is the demo" and "Exit the demo": it returns to the first screen.
4. Airplane mode: the demo works the same.
5. The same in a browser: open `http://<NAS>:8090/#demo`.

