# Buddy Backup Specification

This document describes the Buddy Backup feature - AlvaOS's core NAS-to-NAS backup system.

## Overview

**Buddy Backup** enables two AlvaOS instances to automatically back up to each other over the internet, encrypted end-to-end, with simple pairing and full disaster recovery.

## Design Philosophy

- **Simple to set up** - Pairing via short code, no manual key exchange
- **Always encrypted** - End-to-end encryption, keys never leave devices
- **Incremental** - Only changed data transfers (efficient)
- **Snapshot-based** - Consistent point-in-time backups
- **Automatic** - Set it and forget it
- **Full restore** - Rebuild entire NAS from backup on fresh install

## Use Cases

### Primary: Disaster Recovery

User has two AlvaOS NAS devices:
- **NAS A** - Primary storage at home
- **NAS B** - Backup NAS at friend's/family's house

They pair as buddies. If NAS A fails:
1. User reinstalls AlvaOS on NAS A
2. Pairs with NAS B
3. Restores everything: data, shares, Docker apps, users
4. Back in operation

### Secondary: Geographic Redundancy

- User runs two NAS devices in different locations
- Both sync critical data to each other
- Protection against fire, theft, natural disasters

## What Gets Backed Up

✅ **Included:**
- Storage pool layout and configuration
- User data and shares
- Docker containers (compose files + volumes)
- App configurations
- User accounts and permissions
- System settings (network, shares, etc.)
- Btrfs snapshots

❌ **Not included:**
- The operating system itself (reinstall from ISO)
- Temporary files
- Cache directories
- Docker images (pulled fresh on restore)

## Architecture

### Components

1. **Buddy Daemon** (`alvaos-buddy-backup.service`)
   - Runs on both NAS devices
   - Manages pairing, sync, and restore

2. **Encrypted Tunnel** (WireGuard)
   - Automatic VPN between buddies
   - All traffic encrypted
   - Works over internet (NAT traversal)

3. **Sync Engine** (rsync over SSH or custom protocol)
   - Incremental file transfer
   - Btrfs send/receive for snapshots
   - Bandwidth limiting
   - Resume on connection drop

4. **Pairing Service**
   - Generates unique pairing codes
   - Exchanges public keys
   - Sets up WireGuard tunnel

### Network Architecture

```
┌─────────────────────┐           Internet          ┌─────────────────────┐
│    NAS A (Home)     │◄───────────────────────────►│  NAS B (Remote)     │
│                     │     WireGuard Tunnel        │                     │
│  Buddy Daemon       │     (Encrypted)              │  Buddy Daemon       │
│  10.99.0.1          │                              │  10.99.0.2          │
│                     │                              │                     │
│  Data: /srv/        │  ──rsync/btrfs-send──►      │  Backup: /backup/A/ │
│                     │                              │                     │
└─────────────────────┘                              └─────────────────────┘
```

## Pairing Workflow

### Step 1: Generate Pairing Code (NAS A)

User navigates to **Buddy Backup** → **Pair New Buddy**

NAS A generates:
- **Pairing code**: `ALPHA-BRAVO-12345` (6 chars, easy to type)
- **Public key** (WireGuard)
- **Pairing token** (one-time use)

Code expires after **15 minutes**.

### Step 2: Enter Code (NAS B)

User navigates to **Buddy Backup** → **Connect to Buddy** → enters code

NAS B:
1. Validates code with NAS A (via HTTPS)
2. Exchanges public keys
3. Establishes WireGuard tunnel
4. Runs connection test
5. Pairing complete!

### Step 3: Configure Backup

User configures on both devices:
- **Backup schedule** (continuous, hourly, daily)
- **What to back up** (select shares/datasets)
- **Retention** (how many snapshots to keep)
- **Bandwidth limit** (optional)

### Step 4: Initial Sync

First backup transfers everything (may take hours/days).

Progress shown in Web UI:
```
Initial Backup in Progress
─────────────────────────────────
Progress:    [▓▓▓▓▓▓░░░░░░] 45%
Transferred: 1.2 TB / 2.7 TB
Speed:       85 MB/s
ETA:         ~4 hours

Pause  |  Cancel
```

### Step 5: Incremental Backups

After initial sync, only changes are transferred.

Uses Btrfs snapshots for efficiency:
- Snapshot taken on NAS A
- Only changed blocks sent to NAS B
- Minimal bandwidth usage

## Technical Implementation

### Pairing Service

**API Endpoint:** `POST /api/v1/backup/pairing/generate`

**Response:**
```json
{
  "pairing_code": "ALPHA-BRAVO-12345",
  "public_key": "wg_public_key_here",
  "expires_at": "2026-01-29T15:30:00Z",
  "pairing_url": "https://nas-a.local/pair/abc123"
}
```

**Validation:** `POST /api/v1/backup/pairing/validate`

**Request:**
```json
{
  "pairing_code": "ALPHA-BRAVO-12345",
  "public_key": "requester_wg_public_key"
}
```

**Response:**
```json
{
  "valid": true,
  "peer_name": "NAS-A",
  "peer_public_key": "nas_a_wg_public_key",
  "tunnel_ip": "10.99.0.1",
  "tunnel_peer_ip": "10.99.0.2"
}
```

### WireGuard Configuration

After pairing, both devices configure WireGuard:

**NAS A** (`/etc/wireguard/buddy0.conf`):
```ini
[Interface]
Address = 10.99.0.1/24
PrivateKey = <private_key>
ListenPort = 51820

[Peer]
PublicKey = <nas_b_public_key>
AllowedIPs = 10.99.0.2/32
Endpoint = nas-b-public-ip:51820
PersistentKeepalive = 25
```

**NAS B** (`/etc/wireguard/buddy0.conf`):
```ini
[Interface]
Address = 10.99.0.2/24
PrivateKey = <private_key>
ListenPort = 51820

[Peer]
PublicKey = <nas_a_public_key>
AllowedIPs = 10.99.0.1/32
Endpoint = nas-a-public-ip:51820
PersistentKeepalive = 25
```

### Backup Protocol

**Option 1: Btrfs send/receive (recommended)**

Best for Btrfs storage pools:

```bash
# On NAS A (sender)
btrfs subvolume snapshot -r /srv/data /srv/.snapshots/data-2026-01-29
btrfs send /srv/.snapshots/data-2026-01-29 | \
  ssh buddy@10.99.0.2 'btrfs receive /backup/nas-a/'

# Incremental (after first sync)
btrfs send -p /srv/.snapshots/data-2026-01-28 \
           /srv/.snapshots/data-2026-01-29 | \
  ssh buddy@10.99.0.2 'btrfs receive /backup/nas-a/'
```

**Advantages:**
- Extremely efficient (block-level)
- Preserves snapshots
- Native Btrfs feature
- Very fast

**Option 2: rsync over SSH (fallback)**

For non-Btrfs or mixed setups:

```bash
rsync -avz --delete \
  -e "ssh -i /etc/alvaos/buddy-key" \
  /srv/data/ \
  buddy@10.99.0.2:/backup/nas-a/data/
```

### Backup Schedule

Managed by systemd timer: `alvaos-buddy-backup.timer`

```ini
[Unit]
Description=AlvaOS Buddy Backup Timer

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

### Restore Process

**Scenario:** NAS A failed, user reinstalls AlvaOS

**Steps:**

1. **Reinstall AlvaOS**
   - Boot from installer
   - Install to new/repaired disk

2. **Pair with Buddy**
   - In Web UI: Buddy Backup → Restore from Buddy
   - Enter buddy info (NAS B)
   - Authenticate

3. **Select Restore Point**
   - UI shows available snapshots
   - User selects date/time to restore

4. **Restore Data**
   - NAS B sends snapshot back to NAS A
   - Storage pools recreated
   - Data restored

5. **Restore Configuration**
   - Shares rebuilt
   - Docker containers recreated
   - Users and permissions restored

6. **Verify & Resume**
   - User verifies data
   - Buddy sync resumes

**Estimated time:** Hours to days depending on data size

## Security

### Encryption

- **In transit:** WireGuard (ChaCha20-Poly1305)
- **At rest:** Optional (user choice, Btrfs encryption or LUKS)
- **Keys:** Never transmitted, never stored in cloud

### Authentication

- **Pairing:** One-time code, 15 min expiry
- **Ongoing:** WireGuard public key authentication
- **API:** Mutual TLS or shared secret

### Isolation

- Buddy daemon runs with minimal privileges
- Backup data stored separately: `/backup/`
- Cannot access other buddy's data

## Web UI

### Pairing Screen

```
┌─────────────────────────────────────────────────┐
│ Buddy Backup                                    │
├─────────────────────────────────────────────────┤
│                                                 │
│  No buddies connected.                          │
│                                                 │
│  ┌───────────────────────────────────────────┐ │
│  │                                           │ │
│  │  [📱] Pair New Buddy                      │ │
│  │       Generate code to share              │ │
│  │                                           │ │
│  │  [🔗] Connect to Buddy                    │ │
│  │       Enter pairing code                  │ │
│  │                                           │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
└─────────────────────────────────────────────────┘
```

### Generate Code Screen

```
┌─────────────────────────────────────────────────┐
│ Pair New Buddy                                  │
├─────────────────────────────────────────────────┤
│                                                 │
│  Share this code with your buddy:               │
│                                                 │
│       ┌─────────────────────────┐              │
│       │   ALPHA-BRAVO-12345      │              │
│       └─────────────────────────┘              │
│                                                 │
│  Expires in: 12 minutes                         │
│                                                 │
│  [Copy Code]  [Show QR]  [Cancel]               │
│                                                 │
└─────────────────────────────────────────────────┘
```

### Connected Buddies

```
┌─────────────────────────────────────────────────┐
│ Buddy Backup                                    │
├─────────────────────────────────────────────────┤
│                                                 │
│  Connected Buddies (1)                          │
│                                                 │
│  ┌───────────────────────────────────────────┐ │
│  │ 🟢 NAS-Home                               │ │
│  │                                           │ │
│  │ Last backup:  2 hours ago                 │ │
│  │ Status:       Healthy                     │ │
│  │ Backed up:    2.3 TB                      │ │
│  │ Next backup:  in 58 minutes               │ │
│  │                                           │ │
│  │ [Configure] [Restore] [Unpair]            │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
│  [+ Pair Another Buddy]                         │
│                                                 │
└─────────────────────────────────────────────────┘
```

## Future Enhancements

- **Multi-buddy support** - Back up to multiple buddies
- **Selective sync** - Choose specific folders
- **Bandwidth shaping** - Time-based limits
- **Deduplication** - Cross-buddy dedup
- **Compression** - Optional zstd compression
- **Alerts** - Email/notify on backup failure
- **Buddy discovery** - mDNS local network discovery

---

**Philosophy:** Backup should be so simple and reliable that users actually use it. Buddy Backup makes offsite backups as easy as pairing two devices.
