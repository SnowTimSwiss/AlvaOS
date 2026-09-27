# Buddy Backup Specification

Core NAS-to-NAS backup system for AlvaOS. Peer-to-peer, encrypted, and incremental.

## Overview & Philosophy
**Buddy Backup** allows two AlvaOS instances to back up to each other over the internet.
- **Simple**: Pair via short code; no manual key management.
- **Secure**: E2E encryption (WireGuard); keys stay on-device.
- **Efficient**: Incremental blocks and Btrfs snapshots.
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
- **Tunnel**: WireGuard for encrypted NAT traversal.
- **Engine**: Btrfs send/receive (native block-level) or rsync over SSH.

## Network and trust
- **All buddy traffic after pairing goes through the WireGuard tunnel** (`buddy0`,
  `100.95.95.0/24`): snapshot upload/download, listing, deletion, health probes.
  WireGuard encrypts it and authenticates the buddy by the public key from the
  pairing code, so no TLS certificates are involved.
- A buddy request is accepted only if it carries this NAS's buddy secret **and**
  arrives from the tunnel address of a paired buddy. The tunnel address is
  what identifies the buddy (WireGuard only accepts it with that buddy's key),
  so one buddy cannot read, delete or overwrite another buddy's snapshots.
- **Pairing** is the only step outside the tunnel: the NAS that enters a code
  calls the other NAS's API once to pair back. That call must present the
  secret from the code, so nobody can register themselves as a buddy just by
  reaching the port.
- **Ports**: the WireGuard UDP port (default `51820`) must be reachable on at
  least one side. The web port (`8080`) only needs to be reachable while
  pairing, never permanently. A buddy behind NAT works as long as the other side
  is reachable.
- The tunnel is brought up again automatically when AlvaOS starts.

## Encryption at rest (on the buddy)
The WireGuard tunnel protects data in transit. With encryption enabled, every
snapshot stream is additionally encrypted *before* it leaves the NAS, so the
buddy stores only ciphertext.

- **Format `ALVAENC2`** (`backend/buddy_crypto.py`): scrypt derives a master key
  from the encryption password, HKDF derives a fresh key per stream, and the
  stream is sealed with AES-256-GCM in 1 MiB chunks. Chunk order and the end of
  the stream are authenticated, so any modification, reordering or truncation
  makes the restore fail instead of restoring damaged data.
- **Disaster recovery**: the key-derivation salt and parameters are stored in
  each stream's header. A freshly installed AlvaOS can restore with **only the
  encryption password** — no settings or key files from the old machine.
  Losing the password means the buddy's copies cannot be decrypted by anyone.
- **Unattended backups**: the derived master key is kept in
  `/var/lib/alvaos/buddy_encryption_key.json` (mode 0600, service user only) so
  scheduled syncs can encrypt without asking for the password.
- **Older snapshots** (`ALVAENC1`, before this format) remain restorable on the
  machine that created them, including after a password change. Installs that
  set their password before `ALVAENC2` pause encrypted syncs, with a
  notification, until the password is entered once in the Buddy Backup
  settings; they never fall back to sending unencrypted data.

## Workflow

### 1. Pairing
1. **NAS A**: Generates 6-char code + public key (expires in 15min).
2. **NAS B**: Enters code; exchanged keys establish WireGuard tunnel.

### 2. Backup & Sync
- **What is sent**: the first sync of a source sends a full read-only
  snapshot. Afterwards only the changes are sent (`btrfs send -p`): the
  snapshot last sent stays on this NAS as a hidden `.alvaos-buddy-…`
  subvolume and serves as the base for the next delta. Keeping it costs only
  the space of data that changed or was deleted since that sync.
- **Chains**: on the buddy every delta records the snapshot it builds on. After
  30 deltas the next sync sends a full snapshot again and starts a new chain.
  If the buddy no longer has the base (deleted or dropped for quota), the
  sync falls back to a full snapshot on its own.
- **Schedule**: User-defined (hourly, daily) via systemd timers.
- **Retention**: when the quota the buddy grants you is exceeded, it drops the
  oldest *chains* as a whole (a delta is useless without its base). The chain
  holding your newest snapshot is always kept. Deleting a snapshot by hand
  also deletes the deltas that build on it; the UI says how many.

#### How a snapshot travels
Snapshots can be far larger than RAM or the system disk, so nothing is
staged locally:

1. The sender pipes `btrfs send` through the `ALVAENC2` encryptor and cuts the
   result into 32 MiB chunks.
2. `POST …/peer/upload/start` opens an upload on the buddy, which creates a
   partial file directly on its incoming pool.
3. `PUT …/peer/upload/<id>?offset=N` carries each chunk. Before writing, the
   buddy checks the chunk's offset, your quota and its free space (it always
   keeps 2 GiB or 2 % of the filesystem free, whichever is larger). A chunk
   whose answer was lost is retried; the offset check makes that safe.
4. `POST …/peer/upload/<id>/finish` sends total size and SHA-256. Only if both
   match does the partial file become a stored snapshot. On any error the
   sender cancels with `DELETE …/peer/upload/<id>`; uploads nobody finishes
   are removed after six hours.

A restore runs the other way without temporary files. For a delta, the full
snapshot and every delta up to the chosen one are received in order and the
intermediate subvolumes are deleted afterwards. For each stream the download is
decrypted chunk by chunk (each chunk is authenticated first) and piped into
`btrfs receive`. The end of the stream is only passed on once everything
checked out, so a cut or altered download never becomes a finished snapshot;
a half-received subvolume is deleted.

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
3. **Restore from a buddy**: pick the buddy and snapshot. Encrypted snapshots
   need the encryption password (not the kit password, unless you chose the
   same one).

## Web UI
- **Pairing**: Quick access to code generation or entry.
- **Monitoring**: Health status, last sync, next scheduled run, and transfer speed.
- **Management**: Easy configuration of retention, schedule, and restore points.

## Extensions (Future)
- Multi-buddy support

**Philosophy**: Offsite backup should be as effortless as pairing a device.

