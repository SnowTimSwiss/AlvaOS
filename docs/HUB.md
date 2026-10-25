# AlvaOS Hub

The Hub is one address for the whole household: everyone signs in once and
finds the apps the admin gave them, side by side. This page is the reference
for how the Hub is built and where its data lives. What is done and what is
next is tracked in `ROADMAP.md` and `BACKLOG.md`.

## What the Hub is

- **Part of AlvaOS, not an app.** It ships with AlvaOS, is updated with it
  (signed, rolls back on its own) and is turned on on the Hub page of the
  admin pages. It is one service: `files_server.py` (port 8090, HTTPS 9443,
  WebDAV 8091/9444), unit `alvaos-files.service`.
- **The admin pages stay separate** (port 8080): the Hub is for everyone at
  home, the admin pages are for the owner.
- **No start page.** The Hub opens the first app in its app bar. The bar
  (left on a computer, along the bottom on a phone) is hidden while someone
  sees only one app.

## Hub apps and store apps

| | Hub apps | Store apps |
|---|---|---|
| Examples | Files, Photos, Calendar, Chat; later Contacts, Notes | Jellyfin, Immich, OpenWebUI, ... |
| Made by | AlvaOS | others |
| Runs as | a module inside the Hub process | its own Docker container |
| Sign-in | the NAS sign-in | the app's own |
| Acts as | the signed-in person, through the privilege helper | the container, isolated |
| Updated | with AlvaOS | per app, from the App Store |
| In the Hub | built in | a tile that opens it (later behind a reverse proxy, `jellyfin.alva.home`) |

- Hub apps are listed in `backend/hub_apps.py` (`APPS`): id, name, icon, what
  it is part of (`needs`) and whether it keeps personal data.
- **No third-party plugins inside the Hub** for now: their code would run with
  the Hub's rights and could read everyone's files. Others' apps stay isolated
  Docker apps.
- No iframes for store apps and no `/apps/<name>` paths: many apps forbid the
  one and break with the other.
- **Store apps as tiles (done):** the Hub page lists every installed store app
  that has a page (a catalog port described as "Web UI", on the port chosen at
  install). Each is off until the admin shows it, then for everyone or only
  some people (`store` in `hub.json`). In the Hub they sit behind "Apps" in
  the bar and open `http://<the NAS>:<port><path>` in a new tab. Apps added as
  a compose file have no known page and are not listed.

## Who sees what

The admin chooses, per Hub app on the Hub page:

- **On or off.** Off means its pages are not there and nothing of it runs.
  An app that is part of another (Photos is part of Files) is only there
  while that one is on.
- **Who sees it:** everyone, or only some people. The admin account sees
  every app that is on.

This is enforced by the server, not only hidden: the Files API answers 403
(`app_off`) to someone who may not use Files, WebDAV refuses them, and share
links do not open while Files is off.

## Where the data lives

### The rule: data is normal files in normal folders

No Hub app hides people's data in a database of its own. What belongs to
someone is visible as files: over the network (SMB), over WebDAV and in
Files. That way restore points, the backup disk, Buddy Backup and space
limits work for every Hub app without anything extra. Only caches are
internal, because they can be made again at any time.

### Personal and shared

- **Personal data** belongs to one person (their phone photos, notes,
  calendar).
- **Shared data** belongs to the household (family photos, shared documents,
  a family calendar). It lives in normal shared folders.

### Where each kind goes

| | Simple (the default) | Powerful when needed |
|---|---|---|
| **Files** | No storage of its own: it shows the folders someone has, their personal folder and the shared folders. Each folder lives on the pool it was made on, so SSD or HDD is chosen per folder. | — |
| **Personal data of Hub apps** | In the person's **personal folder** (a share only they can open, on a pool and with a space limit chosen by the admin). Each app has a fixed subfolder there: `Photos/`, later `Notes/`, and hidden ones for technical data (`.alvaos/calendar`). One limit for everything. | Per Hub app the admin can choose **another place**: a pool. AlvaOS then makes one folder per person there (a share `<person>-photos`, only for that person), optionally with its own limit per person, e.g. photos on the large HDD pool with 500 GB each. |
| **Shared data** | Normal shared folders. The admin marks which of them an app uses, e.g. "Family photos is a photo library". | — |
| **Caches** (thumbnails, later the search index and video previews) | One **Hub cache place** chosen once by the admin: a pool (folder `.alvaos-hub/` at its top, not shared). Without a choice: the system disk, as before. Not backed up, not counted against limits. | — |

The system disk is small: with many photos the thumbnails belong on a pool.
The Hub page says so while the cache is on the system disk.

### Photos

What it should become (backup from the phone, albums, favourites): `PHOTOS.md`.

- Shows the person's own photos (their `Photos/` folder, wherever the admin
  put it) and every **photo library** they may read, together, newest first.
- Uploads from the phone (with the native apps) go to their own photos.

### Calendar

- Like Google Calendar: day, week (3 days on a phone), month and schedule;
  a little month on the left; calendars with Google's colours that can be
  shown or hidden; tasks with or without a day (shown in the calendar on
  their day, and in a tasks panel); events that repeat (every day, weekday,
  week, month, year, optionally until a day). Click or drag in the grid to
  make an event, drag to move it, pull the lower edge to make it longer.
  Keys as in Google: `t` today, `d` `w` `m` `a` views, `j` `k` forward and
  back, `c` new.
- **Own calendars** in `.alvaos/calendar/calendar.json` in the personal
  folder (hidden: it is not meant to be opened by hand), or at the top of
  `<person>-calendar` when the admin gave Calendar a pool of its own. One
  file holds the person's calendars, events and tasks.
- **Family calendars:** each shared folder the admin marks gets a calendar
  (`<folder>/.alvaos/calendar/calendar.json`). Who may open the folder sees
  it, who may change the folder changes it; read-only people only look.
- Times are local times of the NAS, whole days are dates (last day
  included). A repeating event is stored once; changing or moving one
  repeat changes them all (the editor says so).
- **On phones and computers (CalDAV, `backend/hub_caldav.py`):** the same
  calendars and tasks sync with the calendar app people already use:
  iPhone/iPad/Mac (CalDAV account; tasks in Reminders), Android with DAVx5,
  Thunderbird. The address is the Hub's (`<nas>:9443`, found through
  `/.well-known/caldav`); name and password as for the shares; the admin
  account is not offered (as for WebDAV). It works on the same
  `calendar.json` files as the page, as the person through the helper, so
  read-only family calendars are read-only on the phone too. Each calendar
  of a place is one CalDAV calendar, the place's tasks one task list. A
  phone may rename and recolour a calendar (the colour becomes the nearest
  of the page's); calendars are made and deleted on the page. What the page
  cannot show is simplified when a phone saves it: one repeat rule (every 2
  weeks becomes weekly, "5 times" becomes "until" the fifth day), no
  exceptions to a repeat, no reminders. "Calendar › On your phone and
  computer" shows the address and the steps.

### Contacts

- A person's address book: a list with a search and letters on the left, the open contact on the right (a page of its own on a phone). Name, company, job title, nickname, several phone numbers, email addresses and addresses (each with a kind), birthday, website, notes; a heart for favourites. Menu: import a `.vcf` file (cards already there are skipped), export all as vCard, "Sync with your phone".
- **Where it is kept:** one `contacts.json` in `.alvaos/contacts/` of the person's personal folder (or their own pool), written as the person through the helper, like Calendar. Personal only (no shared address books yet).
- **CardDAV** (`hub_carddav.py`, vCards in `hub_vcard.py`) beside CalDAV on the same address (`/.well-known/carddav`, `/dav/<person>/contacts/`), same sign-in. iPhone and Mac (Contacts › Accounts › CardDAV), Android with DAVx5, Thunderbird. Calendar and Contacts are separate switches: a phone can sync one without the other. vCard 3.0 is written; 2.1 and 4.0 are read. A phone's extras (photos, groups, social profiles) are not kept; the contact itself is.

### Chat

- Like ChatGPT: chats on the left (Pinned, Today, Yesterday, ...), the
  model top left (grouped by AI service), a box with "Think" on or off and
  how hard (low, medium, high), the answer streaming in, thinking shown
  folded ("Thought for 8 seconds"). Markdown: headings, bold and italic,
  nested and numbered lists, task lists, tables with alignment, code
  blocks with Copy, quotes, maths blocks. Nothing the model writes becomes
  HTML of its own; pictures stay links.
- **Think only where the model can.** When the admin saves a service, the
  NAS asks the service what each new model can: OpenRouter and others name
  the parameters of each model in their `/models` list (`reasoning`: Think
  with Low/Medium/High; `include_reasoning`: Think on/off); Ollama says
  "thinking" in `/api/show` (levels only for gpt-oss). Only where a service
  says nothing (OpenAI, LM Studio, ...) it goes by the name. Nobody sets it
  by hand. A model that refuses to think anyway loses "Think" until the Hub
  restarts.
- **Private chat** (the ghost top right): kept nowhere, not in the list,
  not on the NAS; the page sends what was said so far with each message.
  Gone with "New chat" or when leaving it. The AI service still sees it.
- **Pinned and kept chats; the rest goes by itself.** Chats nobody used for
  30 days are deleted (the admin can choose 7, 14, 30, 90 days, a year or
  never). Pinned chats are on top and stay; kept chats stay where they are
  in the list (bookmark). Each chat has a flame to delete it (after asking);
  the flame next to "New chat" deletes all others at once, pinned and kept
  ones only when ticked.
- **Only a chat.** It has no tools: it cannot see or change anything on the
  NAS. The assistant in the admin pages (Settings › AI) is a different
  thing for the owner.
- **Where it is set up, and by whom:** the admin only.
  - **Settings › AI › AI services:** as many as needed (Ollama at home,
    Ollama Cloud, OpenAI, OpenRouter or any OpenAI-compatible service),
    each with a name, address, key and its models ("Find models" asks the
    service; or typed). What each model can is asked from the service on
    saving. Keys stay on the NAS and never reach a browser.
    The assistant uses one of these models.
  - On the **Hub page**: Chat on or off (off until the admin turns it on),
    who sees it, which models it offers (all; only these; all but these;
    a whole service or single models, a whole service also covers models
    added later), and for single people a rule on top of that ("of those,
    only these" / "of those, all but these"). The page shows what each
    person ends up with. And how long unused chats stay.
  - People choose only the model (from what is open to them), thinking and
    effort.
- Chats in `.alvaos/chat/` in the personal folder (or `<person>-chat`):
  `chats.json` (the list, with `pinned` and `kept`) and `chat-<id>.json` per
  chat. Only that person can read them, over the network too. Old chats are
  deleted when the person opens Chat.
- Thinking: sent as `reasoning_effort` (low/medium/high; for Ollama "none"
  when off); when the model or service does not know it, Chat asks again
  without and says so. Thinking comes from `reasoning`/`reasoning_content`
  or `<think>…</think>` in the answer.

### Space limits

- **Per person in total:** the limit of their personal folder.
- **Per person per app:** only when that app has its own place.
- **Per shared folder:** as for any share.
- Caches do not count.
- Later the Hub shows "You use 42 of 100 GB" per place.

### Backups

Personal folders, per-app folders and photo libraries are shares, so they are
backed up like every share. Caches are not.

### Who needs a personal folder

Everyone who uses a Hub app that keeps personal data. The Hub page lists the
people who see such an app but have no personal folder, and makes them one on
a pool the admin picks (with an optional limit). People made later get one
when they are created (Storage › People).

## Decisions

- Files has no storage of its own; it shows shares.
- Default for personal data: the personal folder, one limit; per app another
  place only when the admin wants it.
- `Photos/` and later `Notes/` are visible over SMB (they are the person's
  files); only technical data is hidden (`.alvaos/`).
- Caches go to one Hub cache place on a pool; the system disk only until the
  admin chooses one.
- No third-party code inside the Hub.
- Hub app data that is not meant to be opened by hand (calendar, chats)
  goes into a hidden `.alvaos/<app>/` folder, as JSON, written as the person
  through the helper (`files-data-read|write|delete`, the only helper
  operations that replace a file; small files only).
- Chat's AI services are the admin's choice, in one place (Settings › AI);
  people never see keys.

## Settings file

`/var/lib/alvaos/hub.json`, read by the admin backend and the Hub (same
service account):

```json
{
  "apps": {
    "files":  {"enabled": true, "people": null},
    "photos": {"enabled": true, "people": ["anna", "ben"],
               "location": {"mode": "personal"},
               "libraries": ["Family"]},
    "calendar": {"enabled": true, "people": null,
                 "location": {"mode": "personal"}, "libraries": ["Family"]},
    "chat":   {"enabled": true, "people": ["anna", "ben"],
               "location": {"mode": "personal"},
               "access": {"mode": "except", "items": ["cloud/gpt-5"]},
               "people_access": {"ben": {"mode": "only", "items": ["home"]}},
               "keep_days": 30}
  },
  "storage": {"cache_pool": "a1b2-..."}
}
```

- `people: null` means everyone.
- `location`: `{"mode": "personal"}` or `{"mode": "pool", "pool_id": "...",
  "limit_gb": 500}` (limit optional).
- `libraries`: names of shared folders the app reads for everyone who may
  read them (Photos: photo libraries; Calendar: family calendars).
- `access` (Chat): which models it offers: `mode` `all`, `only` or
  `except`, `items` a whole AI service (`home`) or one model of it
  (`home/llama3.1`); the services are in `/var/lib/alvaos/ai.json`.
  `people_access`: the same per person, applied on top. `keep_days`: unused
  chats go after so many days (0: never). Before beta-v0.3.5 it was
  `models: [names]` of the one service; that is read as `only` those models
  of the service `main`.
- `storage.cache_pool`: empty for the system disk.

Without the file every app is on for everyone (Chat stays off until the
admin turns it on), personal data goes to the personal folder and the cache
stays on the system disk.
