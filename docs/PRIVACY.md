# AlvaOS app: privacy

The AlvaOS app opens the Hub of **your own AlvaOS NAS** and backs up the
photos and videos of your phone to it. It talks to nobody else.

**What the app sends, and where**

- To the NAS whose address you enter, and only there: the photos and videos
  of the albums you choose (with the date they were taken and, if you allow
  it, where), their names and sizes, the name and model of your phone and the
  app's version (so you see it under Phones and devices), and your name and
  password, or the code of the QR code, to sign in.
- The Hub of your NAS opens inside the app, like a web page, from your NAS
  only; links to other places open in your browser.
- Scanning the QR code uses the scanner of Google Play services on the
  phone; the camera picture stays on the phone.
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

Away from home the app reaches your NAS through AlvaOS Link: encrypted from end to
end with keys only your phone and your NAS have. If a direct connection is not
possible, the packets pass a relay server, which cannot read them; it learns that
two keys talk, and when. At home, on your own network, the app can also use plain HTTP.

**Questions**

Open an issue on the AlvaOS repository on GitHub.
