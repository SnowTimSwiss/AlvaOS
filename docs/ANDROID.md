# The Android app: building, signing, Google Play

The app lives in `android/` (see `android/README.md`). It connects with
the QR code of the Hub (Devices), shows the whole Hub with every
app the person has, and backs up photos (`docs/PHOTOS.md`). GitHub builds
it; nothing needs Android Studio.

- **An AlvaOS release** (the one you make for the NAS, e.g. `beta-v0.2.0`):
  the app is added to it, next to the ISO and the .deb: `alvaos-android.apk`
  to install by hand and, once the upload key is set up,
  `alvaos-android.aab` for Google Play. With Play set up (below) it also
  goes to Play's internal testing by itself. One release, OS and app.
- **Every pull request and every push to `main`** that touches `android/`:
  the tests run and the app is built (the screens of the app are put on the
  `app-screens` branch). Nothing is kept or published.
- **Actions › Android app › Run workflow**: builds the app including the Link
  library and keeps the APK with the run for a day (Actions › the run ›
  Artifacts), to test between releases. Run it once by hand before a release.
  With a track (internal, alpha, beta, production) it also uploads to that
  track on Google Play (the default is none).
  With a release tag in "Add the app to this existing release", the app is
  added to that release afterwards (for a release made before the app).

The app has the version of AlvaOS: the release's name, between releases the
`VERSION` file (`beta-v0.2.0`). The version code Google Play needs is
counted up by CI on its own (minutes since 2026); nobody needs to see it.

## AlvaOS Link (away from home)

The app reaches the NAS from anywhere through AlvaOS Link (`docs/LINK.md`).
The native library comes from iroh-ffi v1.1.0, built in the CI step "Build
the Link library for Android" (cargo-ndk, NDK 27, four CPU types). The step
is best effort: if it fails, the app is built without it and says "not
available in this build" in the settings. A release should be checked for
that line on a phone.

## The demo, and "App access" in the Play Console

Google's review asks how to get into the parts of an app that need a sign-in. The app needs the person's own NAS,
which a reviewer cannot reach, so the app has a **demo**: on the first screen, "Try the demo". It opens the whole
app with sample files, photos (with the time slider), a calendar, contacts and a chat, with no NAS and no account.
Nothing is sent or saved; Settings has "Exit the demo".

How it works: the app carries a copy of the Hub's pages (`frontend/files-app`, copied into the app's assets by the
build) and `demo.js` answers what the NAS would answer, in the page only. The demo is also on in a browser with
`index.html#demo`. The native Backup and Settings tabs show a short explanation in the demo.

In the Play Console › App content › App access choose "All or some functionality is restricted" and add one
group of credentials with the name `Demo mode`, no username and password, and these instructions (English):

> The app connects to the user's own AlvaOS server, which a reviewer cannot reach. Tap "Try the demo" on the
> first screen to use the whole app with sample data. No account or server is needed.

## Once: the upload key (5 minutes)

Google Play needs every app signed with the same key. With Play App Signing,
Google keeps the real key; we only keep an *upload key*. If it is lost, Google
can replace it (Play Console › Setup › App signing), so it is not the end.

1. On your PC (Java is needed, e.g. from Android Studio or `apt install
   openjdk-17-jdk-headless`):

   ```sh
   keytool -genkeypair -v -keystore alvaos-upload.jks -alias upload \
     -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=AlvaOS"
   ```

   It asks for a password twice; use the same one for the key when asked.
   Keep `alvaos-upload.jks` and the password in your password manager. It
   never goes into the repository.

2. Make it one line of text:

   ```sh
   base64 -w0 alvaos-upload.jks > alvaos-upload.b64      # Linux
   base64 -i alvaos-upload.jks -o alvaos-upload.b64      # Mac
   ```

3. GitHub › the repository › Settings › Secrets and variables › Actions ›
   New repository secret, four times:

   | Name | Value |
   | --- | --- |
   | `ALVAOS_KEYSTORE_BASE64` | the content of `alvaos-upload.b64` |
   | `ALVAOS_KEYSTORE_PASSWORD` | the password |
   | `ALVAOS_KEY_ALIAS` | `upload` |
   | `ALVAOS_KEY_PASSWORD` | the password (the same) |

From then on every build is signed with this key and the `.aab` is made
too. A phone with the old debug APK must uninstall it
once (a different signature); after that every new APK installs over the
last one and keeps the sign-in.

## Once: the app in the Play Console

1. Play Console › Create app: name "AlvaOS", app, free.
2. **Package name** is `uk.timserver.alvaos` (the domain timserver.uk
   backwards; `applicationId` in `android/app/build.gradle.kts`, the code
   itself stays in `org.alvaos.app`). It can never change after the first
   upload.
3. Testing › Internal testing › Create new release: upload `alvaos-android.aab` from
   the newest AlvaOS release **by hand**. Google only accepts uploads
   through the API after the first one was made in the browser. Accept Play
   App Signing when asked. Add yourself as tester (a list of e-mails) and
   open the link it gives on the phone.
4. What Google asks before a release (App content):
   - **Privacy policy:** the link to `docs/PRIVACY.md` on GitHub (the
     repository must be public for that, or put the text on any web page).
   - **Data safety:** photos and videos, and the name used to sign in, are
     sent to the NAS the person chose, for the app's function, not shared
     with anyone, sent encrypted when the NAS uses HTTPS. Nothing goes to
     you or to anyone else. No ads, no analytics.
   - **Photo and video permissions** (`READ_MEDIA_IMAGES/VIDEO`): the core
     of the app is backing up the whole gallery; that is an allowed use.
     Explain it in one sentence.
   - **Foreground service** (data sync): "uploads the backup while it runs,
     with a notification"; Google may ask for a short video of the backup.
   - **Manage media** needs no form.
   - Content rating questionnaire, target audience (adults), no ads.
5. Store listing: short and full description, the icon in 512×512 and a few
   screenshots from the phone.

## Uploads through GitHub (after the first one)

1. Play Console › Setup › API access: link a Google Cloud project, create a
   **service account** as shown there, give it a JSON key (Google Cloud ›
   IAM › Service accounts › Keys › Add key › JSON).
2. Play Console › Users and permissions › Invite the service account's e-mail
   with "Release to testing tracks" (and "Release to production" if wanted)
   for this app.
3. GitHub secret `PLAY_SERVICE_ACCOUNT_JSON`: the whole JSON file.
4. From then on every AlvaOS release goes to internal testing by itself;
   Actions › Android app › Run workflow uploads to any track in between.
   While the app was never published on Play, use Run workflow with "As a
   draft" and press "Roll out" in the Play Console yourself.

## Release checklist

- `VERSION` says the version you want to show.
- TESTING.md 12b passes with the APK of the newest run.
- Make the AlvaOS release as always: the app is added to it and goes to
  Play's internal testing. Check it on a phone from Play, then promote it
  in the Play Console (internal › closed › production).
