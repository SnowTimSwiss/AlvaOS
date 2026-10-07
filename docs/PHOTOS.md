# Photos: back up, sort into albums, look at them

Photos is the Hub app for the household's pictures. It is meant to be what most
families want from Google Photos or Immich, and no more: every phone backs up
its pictures to the NAS by itself, they show up by date, people put them into
albums and share albums with each other. Faces, maps, object search and
editing are not part of it; whoever wants them installs Immich from the App
Store, on the same folders.

This page is the plan. What is built is marked **done**.

## What people do with it

1. **Back up the phone.** Open the AlvaOS app once, sign in, say yes to
   "Back up photos". New pictures and videos go to the NAS on their own, on
   Wi-Fi, also when the app is closed. Nothing is deleted on the phone.
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
- **Phone backups** go to `Photos/<phone name>/<year>/<month>/`, with the
  file's own name and date. A file that is already there with the same size
  and date is skipped, so a reinstalled app does not upload everything again.
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

## The phone backup, technically

- The native apps (see `ROADMAP.md`, Hub step 5) do the uploading; the browser
  cannot run in the background. Until then: "Upload" in Photos and WebDAV.
- They use the resumable upload Files already has (`/api/upload/*`, pieces of
  64 MB, continue after a dropped connection) and the file's own date
  (`files-part-finish-dated`, **done**).
- One new call: "which of these do you have already?" (name, size, date →
  yes/no), so the app does not have to list the whole folder.
- The app remembers what it uploaded; the NAS needs no list of devices.

## Built in small steps

1. **The date a picture was taken** (EXIF), sorted by it: **done**.
2. Video thumbnails (a still from the video, made by ffmpeg in the Hub process
   at low priority, only when ffmpeg is installed).
3. Favourites and albums in the browser (`Photos/.albums/`), sharing an album
   with people in the household.
4. Upload into the own photos from the Hub (button and drag and drop), into
   `Photos/<year>/<month>/` by the date in the photo.
5. "Which do you have already?" for the apps; the phone backup in the native
   apps.
6. Search by date and album name; select many and download as ZIP.

What stays out on purpose: faces, places on a map, "things in the picture",
editing, a timeline of memories. These need a model and a database and are
what Immich does well.
