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
  set their password before `ALVAENC2` keep writing the old format until the
  password is entered once (restore, rollback or settings).

## Workflow

### 1. Pairing
1. **NAS A**: Generates 6-char code + public key (expires in 15min).
2. **NAS B**: Enters code; exchanged keys establish WireGuard tunnel.

### 2. Backup & Sync
- **First Sync**: Full data transfer (hours/days).
- **Incremental**: Only changed blocks via Btrfs snapshots.
- **Schedule**: User-defined (hourly, daily) via systemd timers.
- **Retention**: User-defined

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

