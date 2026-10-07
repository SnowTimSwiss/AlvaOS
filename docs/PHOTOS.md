# Photos: back up, sort into albums, look at them

Photos is the Hub app for the household's pictures. It is meant to be what most
families want from Google Photos or Immich, and no more: every phone backs up
its pictures to the NAS by itself, they show up by date, people put them into
albums and share albums with each other. Faces, maps, object search and
editing are not part of it; whoever wants them installs Immich from the App
Store, on the same folders.

This page is the plan. What is built is marked **done**.

## What people do with it

1. **Back up the phone** (**done** for Android). Open the AlvaOS app once,
   sign in, choose the albums of the phone to back up (Camera, Screenshots,
   WhatsApp Images, …). New pictures and videos go to the NAS on their own,
   every hour (only on Wi-Fi if chosen), also when the app is closed. The
   albums stay albums on the NAS, and deleting stays in sync both ways (see
   below).
2. **Look at them.** Newest first, by month, with the date the picture was
   taken (**done**: from the photo itself, EXIF). A picture opens big; swipe
   to the next one. Videos play.
3. **Albums.** Select pictures › "Add to album". An album belongs to the
   person who made it; they can share it with others in the household (they
   see it, and may add if allowed). Family albums from a shared folder, like
   today's photo libraries, are albums too.
4. **Favourites** with one tap, and a view with only them.
5. **Find** by date ("summer 2023") and by album name. No face or object
   search.
6. **Get them out.** Download one or many (ZIP), share a link to an album
   (the share links Files already has).

## How it is kept

The rule from `HUB.md` stays: everything is normal files in shared folders,
so backups, restore points and space limits just work, and nothing is lost if
Photos is turned off or AlvaOS is gone.

- **The pictures** stay where they are: the person's own `Photos/` folder (or
  the share the admin chose for Photos) and the photo libraries.
- **Phone backups** go to `Photos/<phone name>/<album>/`, with the file's
  own name and date (the date it was taken). A file that is already there
  with the same name and size is taken as backed up, so a reinstalled app
  does not upload everything again. Each phone album shows as an album in
  Photos (**done**).
- **What each phone backed up** is a small list per phone in
  `Photos/.alvaos/phones/<id>.json` (which picture of the phone is which file
  on the NAS), so deleting can be kept in sync.
- **Albums** are small JSON files, one per album, in a hidden folder next to
  the pictures (`Photos/.albums/<id>.json`): a name, the owner, who it is
  shared with, and the list of pictures as paths. Paths, not copies: an album
  costs no space. A picture that was moved or deleted drops out of the album.
  A shared family album lives in the shared folder it is shown from.
- **Favourites** are an album of their own (`favourites.json`).
- **Caches** (thumbnails, the dates read from the photos) are in the Hub cache
  on the pool the admin picked; they can always be thrown away and made again.

No database server, no index that must stay in sync: the folders are the truth,
the JSON files are small and are backed up with the pictures.

## The phone backup, technically (done)

- The app: `android/` (see `android/README.md`). `android/core` is the sync,
  plain Kotlin with tests; `android/app` is the Android app around it. CI
  builds the APK (workflow "Android app": the APK of each push as the run's artifact; every AlvaOS release has `alvaos-android.apk` next to the ISO).
- It signs in like the Hub (name, password, a code if two-step is on) and
  keeps only the session, not the password.
- It uploads with the resumable upload Files already has (`/api/upload/*`,
  pieces of 16 MB, continues after a dropped connection) and the file's own
  date (`files-part-finish-dated`).
- The Hub side is `backend/hub_photos_sync.py`:
  - `POST /api/photos/phones` adds a phone (its folder in `Photos/`).
  - `POST /api/photos/phones/<id>/plan`: the phone says what it has in the
    chosen albums and what was deleted on it; the NAS answers what to upload
    and what was deleted on the NAS.
  - `POST …/done` after uploads, `POST …/deleted` after deleting on the phone.
- **Deleting, both ways, and never by surprise:**
  - Deleted on the phone → the NAS moves it to its trash (30 days to get it
    back). Can be turned off in the app ("Deleting a picture here deletes it
    on the NAS too").
  - Deleted on the NAS (it is in the trash) → the app deletes it on the phone
    too. Without a question when the person allowed "manage media" once
    (the app offers it after choosing the albums, Android 12+): in the
    background where Android lets it, else when the app is opened next.
    Without that permission Android asks once per batch.
  - Moved or renamed on the NAS → nothing is deleted on the phone and it is
    not uploaded again.
  - An album no longer chosen is not a deletion: its pictures stay on the NAS.
  - "Free up space" removes from the phone what is backed up and older than a
    month; it stays on the NAS.

## Built in small steps

1. **The date a picture was taken** (EXIF), sorted by it: **done**.
2. Video thumbnails (a still from the video, made by ffmpeg at low priority,
   only when ffmpeg is installed): **done**. Videos play in the viewer; what the
   browser cannot play (AVI, HEVC from an iPhone) gets a copy made on request
   (H.264, 720 p, kept in the Hub cache); HEIC and TIFF open through a JPEG.
3. Favourites and albums in the browser (`Photos/.albums/`), sharing an album
   with people in the household.
4. Upload into the own photos from the Hub (button and drag and drop), into
   `Photos/<year>/<month>/` by the date in the photo.
5. The phone backup in the Android app, with albums and deleting in sync:
   **done**. iPhone later.
6. Search by date and album name; select many and download as ZIP.

What stays out on purpose: faces, places on a map, "things in the picture",
editing, a timeline of memories. These need a model and a database and are
what Immich does well.
