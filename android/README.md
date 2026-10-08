# The AlvaOS app for Android

The app of an AlvaOS NAS, Android 11 or newer:

- **Connect** with the QR code of the Hub (Phones and devices › Connect a
  phone): `alvaos://pair?c=<code>&a=<address>…`, scanned in the app
  (Google's scanner) or with the phone's camera. Or the code typed, or name
  and password. The app gets a session of its own, listed in the Hub, and
  tries the addresses at home first; away it goes through **AlvaOS Link**
  (the QR code carries the NAS's Link address `l=`), with no router setting.
- **Hub** tab: the Hub in a web view with that session: every app the
  person has, as on a computer.
- **Backup** tab: the phone's photos and videos, album by album, deleting in
  sync both ways (`docs/PHOTOS.md`).
- **Settings** tab: the account, backup options, sign out.

- `core/`: the sync, plain Kotlin without Android: the Hub client
  (`Hub.kt`: sign in, resumable uploads) and the engine (`Sync.kt`: what to
  upload, deletions both ways, free up space), and the QR code's link
  (`PairLink`). Tested on any computer: `./gradlew :core:test`.
- `app/`: the Android app around it: the screens (`MainActivity.kt`, built
  in code from `Ui.kt`), the Hub in a web view (`HubPage.kt`), the phone's
  albums through MediaStore (`Gallery.kt`), the backup in the background
  (`SyncWorker.kt`, WorkManager) and what it remembers (`Store.kt`).

- **Link** (`core/.../link/Link.kt`, `app/.../LinkService.kt`): the client of
  AlvaOS Link (iroh) and a proxy on `127.0.0.1` that the web view and the
  sync use like any address. `core/.../computer/iroh/iroh_ffi.kt` is the
  generated Kotlin binding of iroh-ffi v1.1.0 (MIT/Apache-2.0, see
  `THIRD_PARTY_NOTICES.md`); the native library `libiroh_ffi.so` is built by
  CI from that tag with cargo-ndk into `app/src/main/jniLibs` (not in the
  repository). Without it the app works at home only. `LinkTest` runs the
  real NAS daemon (needs `python3` with `pip install iroh`).

Signing and Google Play: `docs/ANDROID.md`.

Building the APK needs the Android SDK (`ANDROID_HOME` or `local.properties`);
without it Gradle builds only `core`. GitHub builds it on every change to
`android/` (workflow "Android app": the APK of each push as the run's artifact; every AlvaOS release has `alvaos-android.apk` next to the ISO).
