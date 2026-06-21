# Roadmap

AlvaOS is a calm NAS OS for storage, apps, and offsite backup.

This roadmap is intentionally practical: fix the things that made real testing
confusing first, then continue with deeper feature testing.

Its more of a TODO than a ROADMAP in the traditional way.

## Notifications
- sollte man anklicken können (nicht alle aber die bei denen es hilft)
- so wie bei truenas ein symbol wo alle letzten fast wie ein log gespeichert sind
- design verbessern
- verschiedene stufen: die die automatisch weggehen und solche die man dismiss muss befor sie einem in ruhe lassen.


## Usage test-ergebnisse:

- die ip die einem im TUI angezeigt wird, ist dann nicht die gleiche wie man hat nachdem gerestartet wurde
- uhrzeiteinstellung beim installieren sollte wenn man etwas reinschreibt das default direkt gelöscht werden, also utc und nicht dass man das erst löschen muss und dann klicken. // vieleicht eine weltkarte wo man dann anklicken kaann oder nach stadt suchen, so wie bei linux mint?
- ich habe einen usb stick rausgezogen der einen pool hatte und das passierte:
        - Kein alert im dashboard bei storage UND bei alerts
        - nur bei storage/pools kam: pool degraded. aber es kamm so: ja ersetze sie, aber es war eine ohne raid... da müsste man klarer kommunizieren und so
- ich habe einen raid-install gemacht aber nur eine der beiden wurde als system disk erkannt und 1. so bei disks angezeigt und vor wipe geschützt und so.
- system pool zeigt zwar an dass 2 devices drin sind, aber raid level sagt single... und da wäre cool wenn man auch replacen könnte.
- popup-fenster müssen manchmal gescrollt werden. das möchte ich nicht.
- popup fenster haben zum teil ein kreuz oben rechts, das macht aber manchmal nix...
- das installed apps dashboard ist mit der ux nicht so cool. also ich möchte ja running und es easy abstellen können weil dann die sachen zu actions tun ist nicht sooooooo coool
- die punkte/buttons bei actions bei apps scheinen nicht zu funktionieren/machen nix
- buddy backup sollte standartmässig mit send to this buddy OFF sein
- buddy backup sollte wenn es erfolgreich war oder nicht oder so eine benachrichtigung geben
- als ich einen pool senden wollte kam das: Local incoming path is not configured or not allowed
- allgemein sollte ein log von (relevanten) benachrichtigungen im dashboard bei benachrichtigungen/alerts sein
- in den update settings sollte apply debian updates automatically ein checker sein.
- es sollte eine benachrichtigung geben wenn debian updates verfügbar sind.