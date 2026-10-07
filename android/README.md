# The AlvaOS app for Android

For now it backs up the phone's photos and videos to the NAS (see
`docs/PHOTOS.md` for what it does and the rules for deleting). Android 11 or
newer.

- `core/`: the sync, plain Kotlin without Android: the Hub client
  (`Hub.kt`: sign in, resumable uploads) and the engine (`Sync.kt`: what to
  upload, deletions both ways, free up space). Tested on any computer:
  `./gradlew :core:test`.
- `app/`: the Android app around it: the screens (`MainActivity.kt`), the
  phone's albums through MediaStore (`Gallery.kt`), the backup in the
  background (`SyncWorker.kt`, WorkManager) and what it remembers
  (`Store.kt`).

Signing and Google Play: `docs/ANDROID.md`.

Building the APK needs the Android SDK (`ANDROID_HOME` or `local.properties`);
without it Gradle builds only `core`. GitHub builds it on every change to
`android/` (workflow "Android app": the APK of each push as the run's artifact; every AlvaOS release has `alvaos-android.apk` next to the ISO).
