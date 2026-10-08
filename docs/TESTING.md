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
   Shared links, Trash, Connect a computer and Phones and devices sit at
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
9. Chat: Settings › Assistant: set up an AI service (Ollama at home is
   enough). Hub page › Chat: turn it on, Change › tick two models. In the
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
   with Away from home on and **no router setting on either side**: on the
   first, Backup › Buddy › make a pairing token; on the second paste it.
   Both list each other (status "Connected", then the time last seen).
   Start a backup: it runs through Link (a direct connection when
   possible; slower through a relay). A buddy paired before the update says
   "repair": pair again. Settings › Away from home lists the buddy, too.
   Recovery kit: make one, restore on a fresh NAS: the Link address stays.

## 7. Away from home (AlvaOS Link)

Needs the NAS with the internet and a phone on mobile data (Wi-Fi off).

1. `systemctl status alvaos-link` is active. Settings › Away from home
   shows the switch on, the sentence "Phones and buddies can reach this NAS
   from anywhere" and a Link address (a long code, shown short).
2. Switch off: the sentence says it is off, `systemctl status` still runs
   but nothing connects. On again: running within a few seconds.
3. Hub › Phones and devices › show the QR code: its text under the code says
   the phone also works away. Scan it with the app **on mobile data** (not
   at home): "Connecting…" then the Hub opens. The settings of the app say
   "Through AlvaOS Link". The NAS list under Away from home names the phone.
4. Pair the phone at home, then switch Wi-Fi off: the Hub, Files and the
   photo backup keep working (the first start away can take some seconds).
5. Phones and devices › sign the phone out: it is gone from the list under
   Away from home and can no longer reach the NAS.
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

1. Settings › Assistant: Ollama on another computer (`ollama pull
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

## 12b. The Android app: connect, the Hub, photo backup (Android 11 or newer)

1. On the phone open GitHub › Releases › the newest AlvaOS release and
   download `alvaos-android.apk` (between releases: Actions › "Android app"
   › the newest run › Artifacts › `alvaos-android`, unzip). Install it
   (allow "install unknown apps" for the browser once). Once the upload key
   is set up (`docs/ANDROID.md`), uninstall the old debug build once.
2. On a computer open the Hub, side bar › **Phones and devices** › Connect
   a phone: a QR code and a code appear. In the app tap **Scan the QR
   code** and point it at the code: the app connects without a password
   and shows the Hub; the computer says "<phone> is connected" and lists
   it (last used, model, app version). Also try: scanning the QR code with
   the phone's own camera opens the app and connects; "Type the code
   instead" with the address and the code; "Sign in with name and
   password". An old or used code says so.
3. The **Hub** tab: every app the person has (Files, Photos, Calendar,
   Chat, the store apps) works as in the browser; downloading a file lands
   in Downloads; uploading opens the phone's file picker; there is no
   "Sign out" in the Hub (it is in the app's Settings).
4. In the Hub on the computer › Phones and devices: rename the phone, then
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
   Phones and devices. The phone's back gesture goes up a folder.
2. Photos: the title says Photos, a card shows the backup ("Backing up 16 of
   32" with a bar, or "120 pictures backed up"; Open goes to the Backup
   tab), the albums are chips, an empty library fills the width.
3. Backup and Settings look like the Hub (the same blues, cards, icons)
   in light and dark; the colours do not follow the wallpaper.
4. Settings › Trash opens Files with the trash; Phones and devices opens
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
   Hub opens (see 7). Settings › "Away from home: On …".
2. A build without the library: settings say "not available in this build";
   nothing else breaks.
