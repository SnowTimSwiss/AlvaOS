# 🗺️ Roadmap to v1.0

Release strategy: "Iterate fast, stabilize often."

## v0.1.0 - Foundation
- [x] **Core Architecture**: Repository structure and tech stack definition.
- [x] **UI Prototype**: Design system, basic layout, and navigation.
- [x] **Docs**: Initial documentation (README, DESIGN, STRUCTURE).

## v0.1.5 - Foundation Extended
- [x] **Dashboard**: Basic dashboard with system information (OS info, Uptime).
- [x] **System Settings**: Hostname, basic network config, and date/time.
- [x] **Logging**: Basic UI for viewing backend logs.

## v0.2.0 - Storage Engine
- [ ] **Disk Detection**: Enumerate available drives via Web UI.
- [ ] **Btrfs Integration**: Pool creation, subvolume management.
- [ ] **Basic Shares**: NFS/SMB export configuration.
- [ ] **Disk Health**: SMART monitoring and basic health checks.
- [ ] **Pool Expansion**: Add disks to existing pools.

## v0.3.0 - Updates
- [ ] **Update System**: Be able to update the AlvaOS-packages via Web UI.
- [ ] **Debian Updates** Be able to update the Debian-packages via Web UI.

## v0.4.0 - Installer
- [ ] **RAID**: add option in installer to install with mirror
- [ ] **User Friendly**: make installer more user friendly

## v0.5.0 - Containerization
- [ ] **Docker Engine**: Core integration with system services.
- [ ] **App Store**
- [ ] **Container Management**: Start, stop, logs, and basic config via UI.

## v0.6.0 - Local Backups
- [ ] **Backup Engine**: Create Snapshots.
- [ ] **Restore**: Rollback to previous snapshot.
- [ ] **Schedule**: Automatic snapshot creation.

## v0.7.0 - Buddy Backup (Part I: Connection)
- [ ] **Identity**: Key generation and peering logic.
- [ ] **WireGuard**: Automated tunnel setup between peers.
- [ ] **Handshake**: Secure pairing workflow (QR code / token).

## v0.8.0 - Buddy Backup (Part II: Transfer)
- [ ] **Snapshot Engine**: Btrfs send/receive logic.
- [ ] **Encryption**: Client-side encryption before transfer.
- [ ] **Restore**: Recovery workflow from remote peer.

## v0.9.0 - Monitoring & Health
- [ ] **Disks**: S.M.A.R.T. monitoring and basic health checks.
- [ ] **Smart Alerts**: Notification system for failures/warnings.

## v0.10.0 - Security
- [ ] **Hardening**: Firewall rules and permission audits.
- [ ] **Auto-Healing**: Service recovery.
- [ ] **Security**: Security updates and patches.
- [ ] **User Management**: User and group management with different permissions.
- [ ] **2FA**: Two-factor authentication for user accounts.

## v0.11.0 - Polish (Beta)
- [ ] **UX Refinement**: Mobile view, animations, and themes.
- [ ] **Performance**: Optimization of API and Frontend.
- [ ] **Bug Hunt**: Excessive testing and edge-case fixing.

## v1.0.0 - Stable Release 🚀
- [ ] **Feature Complete**: All planned features implemented and stable.
- [ ] **Production Ready**: No critical bugs, complete documentation.
- [ ] **Launch**: Public release.