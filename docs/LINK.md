# AlvaOS Link: away from home, without a router setting

Link lets the AlvaOS app and a buddy NAS reach this NAS from anywhere: no port forwarding, no domain, no account, and it works behind carrier-grade NAT. It replaced the old Remote access (Tailscale, Cloudflare Tunnel) and the own WireGuard tunnel of Buddy Backup in **AlvaOS 0.3**.

## How it works

Link is on by default (Settings › AlvaOS Link has the switch; there is no question in the setup). The page says that it uses iroh and that a public relay may carry the encrypted traffic when no direct connection is possible.


- Every NAS has a key pair (Ed25519, kept in `/var/lib/alvaos/link/key`). Its public key (64 hex digits) is its **Link address**. Whoever knows it can *try* to connect; what they may do depends on the key list (below).
- Connections are made with [iroh](https://www.iroh.computer/): QUIC, end to end encrypted, authenticated by the keys. Both sides first try a **direct** connection through the routers (hole punching). When that is not possible, the packets pass a **relay** that only forwards encrypted data. The public relays of the iroh project are used by default.
- The daemon `alvaos-link` (`backend/link_daemon.py`, systemd unit `scripts/alvaos-link.service`) runs on the NAS. Nothing else on the NAS speaks iroh: the Hub, the backup code and the admin pages use a small local control API (`link_client.py`, `127.0.0.1:8095`, password file `link/control_token`, readable by group `alvaos`).

## What a peer may do

One QUIC stream per request; the first line names a **service**:

| service | who | goes to |
|---|---|---|
| `hub` | a paired phone | the Hub (`127.0.0.1:8090`) |
| `api`, `nbd` | a paired buddy | the backend (`:8080`) and the vault server (`:10809`) |
| `pair` | anybody | one narrow request: trade a Hub pairing code for a session (phone) or hand over a buddy pairing code. Slowed down per key (6 per minute). |
| `ping` | a paired peer | answers `pong` |

Everyone else is refused; a stranger cannot even reach the Hub's sign-in page. A paired phone still signs in as its person (the session token from pairing).

**What the NAS sees:** for every allowed peer the daemon has an address on the loopback network (`127.95.x.y`) and connects to the service *from that address*. The Hub then counts failed sign-ins per phone, and the backup code identifies a buddy by it (peer `tunnel_ip`). Removing a phone in the Hub (Devices) or a buddy (Backup › Buddy) takes its key off the list at once. Settings › AlvaOS Link switches Link off for everyone.

## Phones (the Android app)

The QR code of the Hub (Devices) carries the pairing code and, when Link is on, the NAS's Link address (`l=`). The app pairs at home or away; away it reaches the Hub through a local forwarder (`127.0.0.1:<port>` in the app) that opens a `hub` stream per connection. See `docs/ANDROID.md`.

## Buddies

See `docs/BUDDY_BACKUP.md`: Link gives each buddy a loopback address; the vault (NBD) and the buddy API are reached there. Pairing goes through the `pair` service.

## Limits and things to know

- The default relays belong to the iroh project (n0). Their terms and availability are theirs; Link works without a relay only when a direct connection is possible. A relay of its own can be set up (`iroh-relay`); a setting for it is not in the pages yet.
- A relay connection is slower than a direct one. Large first backups work best with a direct connection.
- Share links for people without the app and a custom domain are not part of Link. They are planned separately.
- Install: the daemon needs the Python package `iroh` (a wheel with a native library). AlvaOS is amd64 only for now (package and installer), so the amd64 wheel is all that ships; an arm64 build of AlvaOS would also need the aarch64 wheel. The package and the installer ship it in `/opt/alvaos/vendor`.
