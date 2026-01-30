# 🗺️ Roadmap to v1.0

Release strategy: "Iterate fast, stabilize often."

## v0.1.0 - Foundation
- [x] **Core Architecture**: Repository structure and tech stack definition.
- [x] **UI Prototype**: Design system, basic layout, and navigation.
- [x] **Docs**: Initial documentation (README, DESIGN, STRUCTURE).

## v0.1.5 - Foundation Extended
- [ ] **Dashboard**: Basic dashboard with system information (OS info, Uptime).
- [ ] **System Settings**: Hostname, basic network config, and date/time.
- [ ] **Logging**: Basic UI for viewing backend logs.

## v0.2.0 - Storage Engine
- [ ] **Disk Detection**: Enumerate available drives via Web UI.
- [ ] **Btrfs Integration**: Pool creation, subvolume management.
- [ ] **Basic Shares**: NFS/SMB export configuration.
- [ ] **Disk Health**: SMART monitoring and basic health checks.
- [ ] **Pool Expansion**: Add disks to existing pools.

## v0.3.0 - Containerization
- [ ] **Docker Engine**: Core integration with system services.
- [ ] **App Store**
- [ ] **Container Management**: Start, stop, logs, and basic config via UI.

## v0.4.0 - Local Backups
- [ ] **Backup Engine**: Create Snapshots.
- [ ] **Restore**: Rollback to previous snapshot.
- [ ] **Schedule**: Automatic snapshot creation.

## v0.5.0 - Buddy Backup (Part I: Connection)
- [ ] **Identity**: Key generation and peering logic.
- [ ] **WireGuard**: Automated tunnel setup between peers.
- [ ] **Handshake**: Secure pairing workflow (QR code / token).

## v0.6.0 - Buddy Backup (Part II: Transfer)
- [ ] **Snapshot Engine**: Btrfs send/receive logic.
- [ ] **Encryption**: Client-side encryption before transfer.
- [ ] **Restore**: Recovery workflow from remote peer.

## v0.7.0 - Monitoring & Health
- [ ] **Dashboard Pro**: Real-time metrics (CPU, RAM, Net, Disk) & graphs.
- [ ] **Smart Alerts**: Notification system for failures/warnings.

## v0.8.0 - Security & Updates
- [ ] **Update System**: OTA updates for OS and Containers.
- [ ] **Hardening**: Firewall rules and permission audits.
- [ ] **Auto-Healing**: Service recovery.

## v0.9.0 - Polish (Beta)
- [ ] **UX Refinement**: Mobile view, animations, and themes.
- [ ] **Performance**: Optimization of API and Frontend.
- [ ] **Bug Hunt**: Excessive testing and edge-case fixing.

## v1.0.0 - Stable Release 🚀
- [ ] **Feature Complete**: All planned features implemented and stable.
- [ ] **Production Ready**: No critical bugs, complete documentation.
- [ ] **Launch**: Public release.
