# Roadmap

AlvaOS is a calm NAS OS for storage, apps and offsite backup: extremely simple by
default, powerful when needed, light enough for old hardware.

This is a plan, not a promise. What is finished is in [BACKLOG.md](BACKLOG.md); how to
check it on a real machine is in [TESTING.md](TESTING.md).

- [x] Done and checked in automated tests
- [~] Done, but still needs a run on real hardware or a real phone
- [ ] Not done yet

Current version: **beta-v0.3.0**

---

## Next

### Files
- [ ] "You use 42 of 100 GB" everywhere (Files has it; Photos and Calendar do not yet)
- [ ] Recent and Starred kept on the NAS, so every device sees the same
- [ ] Search inside documents (text in PDFs and office files)
- [ ] Activity: who changed, deleted or shared what
- [ ] Links: download limit, how often opened, "end all my links"
- [ ] Unpack ZIP files, download several files as one ZIP, folder sizes, rename many files
- [ ] Edit Office documents with the EuroOffice app (WOPI)
- [ ] Share links for people without the app, done differently from the old Remote access

### Photos
- [ ] Share an album with the household
- [ ] Better video thumbnails and playback
- [ ] Photos on contacts

### Calendar and Contacts
- [ ] Reminders
- [ ] Shared address books
- [ ] Sync that works away from home for other apps than ours

### Apps for phones and computers
- [ ] File sync and notifications in the Android app
- [ ] iPhone app (on AlvaOS Link)
- [ ] Desktop apps for PC, Mac & Linux

### AlvaOS Link
- [ ] An own relay as a setting
- [ ] arm64, if AlvaOS ever ships for ARM (needs the aarch64 build of iroh and an arm64 package)

### Buddy Backup and not being without the NAS
- [ ] A *Take over* button not only buddy backup but a hot spare
- [ ] Tell the buddy when the power is out (UPS), so it can hold off a backup

### Virtual machines
- [ ] Home Assistant OS and other ready images in one click
- [ ] Snapshots in the page

### Apps and graphics cards
- [ ] More catalog apps that can use a graphics card (Plex, Frigate, Stable Diffusion)

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
