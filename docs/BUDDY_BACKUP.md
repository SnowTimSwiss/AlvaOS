# Buddy Backup Specification

Core NAS-to-NAS backup system for AlvaOS. Peer-to-peer, encrypted, and incremental.

## Overview & Philosophy
**Buddy Backup** allows two AlvaOS instances to back up to each other over the internet.
- **Simple**: Pair via short code; no manual key management.
- **Secure**: end-to-end encrypted in transit (AlvaOS Link), LUKS at rest on the buddy; keys stay with the owner.
- **No router work**: it connects from anywhere, with no port forwarding on either side and no account (AlvaOS Link).
- **Efficient**: One full transfer, then only changes, forever.
- **Reliable**: Full disaster recovery (data, shares, Docker, users).
- **Easy restore**: Option for full system backup - everything can be transfered to a new instance just with the paring code.

## Backup Scope
| ✅ Included | ❌ Not Included |
|---|---|
| Storage pool layout & user data | Operating System (reinstall from ISO) |
| Docker configs (compose + volumes) | Temporary files & cache |
| User accounts & system settings | Docker images (redownloaded) |
| Btrfs snapshots | |

## Architecture
- **Daemon**: `alvaos-buddy-backup.service` manages pairing and sync.
- **Link**: AlvaOS Link (`link_daemon.py`, see `docs/LINK.md`) connects the two NAS directly or through a relay that only passes on encrypted packets. Before AlvaOS 0.3 this was an own WireGuard tunnel with a forwarded port.
- **Engine**: Btrfs send/receive into an encrypted vault on the buddy (see below).

## Network and trust
- **All buddy traffic after pairing goes through AlvaOS Link**: the vault (NBD), listing,
  deletion, health probes. Link gives every buddy an address of its own on this NAS's loopback
  network (`127.95.x.y`, stored as the peer's `tunnel_ip`) and carries what is sent there, end to end
  encrypted, to that buddy. The backup code connects to `127.95.x.y:10809` (the vault) and
  `127.95.x.y:18080` (the buddy's API) as if the buddy were next door.
- A buddy is identified by its **Link key** (iroh: a public key; connecting needs the matching
  private key). A buddy request is accepted only if it carries this NAS's buddy secret **and**
  arrives from the address Link gave that buddy: on the receiving side Link connects to the
  backend and the vault server *from the buddy's own address*, and only for keys on its list, so one
  buddy cannot read, delete or overwrite another buddy's snapshots.
- **Pairing** goes through Link too. The code carries the NAS's Link address (64 hex digits), a name
  and the buddy secret. The NAS that enters a code allows the other in, then sends its own code
  through Link to the other NAS's `pair` service, the one thing Link lets a stranger do. Link
  hands it to the backend (`/api/v1/backup/pairing/accept`) together with the secret from the
  code, so nobody can register as a buddy without it.
- **Ports**: none. Neither side needs a port forwarded, a public address or a domain. Both need
  AlvaOS Link on (Settings › Away from home) and AlvaOS 0.3 or newer.
- Buddies paired before 0.3 (WireGuard) show "Pair again". Their old backups stay on them; pairing
  again with the same node id gives access to them again.
- Link is told the buddies at startup and after every change; the vault server listens on the
  loopback network only.

## Vaults: encrypted replication
Each NAS keeps one **vault** on each of its buddies: an image file on the
buddy's incoming pool, as large as the space the buddy grants (sparse, so it
only uses what is written). Replication then works like ZFS replication in
TrueNAS, and the buddy only ever sees encrypted blocks:

```
Owner NAS                                   Buddy NAS
btrfs send -p <common> <new>                 NBD server (backend, loopback, via Link only)
  | btrfs receive /run/alvaos-vault/<v>/…       ↑ only this owner's image
Btrfs  ─ LUKS2 (key only here) ─ /dev/nbdN ══ AlvaOS Link ══ <owner>/vault.img
```

1. The owner asks the buddy to create (or grow) its image
   (`POST /api/v1/backup/buddy/peer/vault`).
2. As root, the privilege helper attaches the image over NBD (port 10809 on
   the buddy's tunnel address), unlocks it with LUKS2 and mounts the Btrfs
   filesystem inside at `/run/alvaos-vault/<vault>` (`backend/vault_ops.py`).
   Only two fixed operations exist, `vault-open` and `vault-close`; the key is
   passed on stdin, and `create` formats only a device without a LUKS header.
3. Each source lives in its own folder in the vault. The first sync sends a
   full snapshot; every later one sends only the changes since the newest
   snapshot both sides still have (`btrfs send -p`). That snapshot stays on
   the owner as a hidden `.alvaos-buddy-…` subvolume.
4. The vault is unmounted, locked and detached after every operation.

**Never a full resend:** every received snapshot is a complete subvolume that
shares unchanged blocks with the others. Any of them can be deleted without
affecting the rest, and the next sync only needs the newest one. If that one
is gone (or a delta fails), the sync falls back to a full send on its own.

**Retention** (per buddy, in the buddy's policy card): the newest snapshot of
each of the last *N* days, weeks and months is kept (defaults 14 / 8 / 12),
plus always the newest. If the vault still has less than 15 % free space,
the oldest remaining snapshots go first. Deleted blocks are handed back to the
buddy (TRIM becomes holes in the image file).

**The buddy's side:** its NBD server answers only on its tunnel address and
only opens the image of the buddy whose tunnel address is asking. A new
connection from the same owner replaces a stale one. Writes stop with "no
space" before the buddy's own pool drops below 2 GiB or 2 % free. Removing a
buddy also removes its vault.

### Keys
- A random 256-bit **vault key** unlocks the LUKS volume. The owner keeps it in
  `/var/lib/alvaos/buddy_vault_keys.json` (0600) for unattended syncs.
- The same key is stored on the buddy **sealed** (`ALVAVKEY1`, AES-256-GCM)
  with a key derived from the **encryption password** (scrypt, salt and
  parameters in the blob). It is written to the buddy before the vault is
  formatted, so a vault can never exist whose key is lost.
- A **replacement NAS** unlocks the vault with the encryption password once
  (Restore → Refresh snapshots asks for it). After a password change the blob
  is re-sealed at the next sync.
- Losing the encryption password *and* the old NAS means nobody can read the
  vault, the buddy included.
- Buddy backups are always encrypted; without an encryption password there is
  no vault.

### Trade-offs
- Btrfs over the internet is chattier than one stream: large files transfer
  well, very many small changes over a high-latency link are slower. The first
  sync transfers everything, so doing it on the LAN before the buddy moves
  away saves a lot of time.
- A dropped connection mid-sync is like a power cut for the vault's
  filesystem: Btrfs stays consistent and the next sync starts over.
- LUKS without dm-integrity does not authenticate blocks. The buddy cannot
  read or meaningfully change data, but could write garbage; Btrfs checksums
  detect that on the next read.

### Old stream-format snapshots
Snapshots stored before vaults existed (one encrypted stream file per
snapshot, `ALVAENC1`/`ALVAENC2`) stay listed and restorable until they are
deleted. The restore decrypts chunk by chunk into `btrfs receive` and only
passes on the end of the stream once everything checked out.

## Workflow

### 1. Pairing
1. **NAS A**: Generates 6-char code + public key (expires in 15min).
2. **NAS B**: Enters code; exchanged keys establish WireGuard tunnel.

### 2. Backup & Sync
- **Schedule**: per buddy (hourly … weekly, preferred time).
- **What is sent**: see *Vaults* above: a full snapshot once, then only changes.

### 3. Recovery kit (do this once, and again after pairing changes)
Buddies store snapshots under the node id of the NAS that sent them, and they
recognise that NAS by its WireGuard key and buddy secret. A reinstalled NAS has
none of these, so on its own it would look like a stranger.

**Backup → Buddy → Recovery kit → Download recovery kit** saves a small text
file with this NAS's buddy identity (node id, WireGuard keys, buddy secret,
tunnel address) and its buddy list, sealed with a password (scrypt +
AES-256-GCM). Keep it off the NAS. The card shows when the kit is outdated
because buddies were added or removed since.

### 4. Restore after losing the NAS
1. Install AlvaOS on the new hardware and finish setup.
2. **Backup → Buddy → Recovery kit → Setting up a replacement NAS?**: load the
   kit and enter its password. The new NAS takes over the old identity; the
   tunnel to the buddies comes up without re-pairing.
3. **Restore from a buddy**: pick the buddy and click *Refresh snapshots*.
   The first time, enter the encryption password (not the kit password,
   unless you chose the same one) to unlock the vault; then pick a snapshot.

## Web UI
- **Pairing**: Quick access to code generation or entry.
- **Monitoring**: Health status, last sync, next scheduled run, and transfer speed.
- **Management**: Easy configuration of retention, schedule, and restore points.

## Extensions (Future)
- Multi-buddy support

**Philosophy**: Offsite backup should be as effortless as pairing a device.

