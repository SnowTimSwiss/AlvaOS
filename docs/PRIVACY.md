# AlvaOS app: privacy

The AlvaOS app backs up the photos and videos of your phone to **your own
AlvaOS NAS**. It talks to nobody else.

**What the app sends, and where**

- To the NAS whose address you enter, and only there: the photos and videos
  of the albums you choose (with the date they were taken and, if you allow
  it, where), their names and sizes, the name of your phone, and your name and
  password to sign in.
- Nothing is sent to the makers of AlvaOS or to any other company. The app
  has no advertising, no analytics and no crash reporting service.

**What stays on the phone**

- The address of your NAS, your name, and a sign-in session (not your
  password), in the app's private storage.
- Which pictures were backed up, so that nothing is uploaded twice and
  deleting stays in sync.

**Deleting**

- When you delete a backed-up picture on the phone, the app moves it to the
  trash on your NAS (you can turn that off in the app).
- When you delete a backed-up picture on your NAS, the app deletes it on the
  phone too: with a question from Android each time, or without one if you
  allowed the app to manage your media.
- Signing out or uninstalling the app removes everything it keeps on the
  phone. What is on your NAS stays there; you delete it in AlvaOS.

**Security**

The connection is encrypted when your NAS is reached over HTTPS (for example
through Tailscale or your own domain). At home, on your own network, the app
can also use plain HTTP.

**Questions**

Open an issue on the AlvaOS repository on GitHub.
