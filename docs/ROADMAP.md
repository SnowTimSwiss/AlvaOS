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
- [x] **Disk Detection**: Enumerate available drives via Web UI.
- [x] **Btrfs Integration**: Pool creation, subvolume management.
- [x] **Basic Shares**: NFS/SMB export configuration.
- [x] **Disk Health**: SMART monitoring and basic health checks.
- [x] **Pool Expansion**: Add disks to existing pools.
- [will come back later] **File Browser**: Simple web-based file manager to browse and manage files.

## v0.3.0 - Updates
- [ ] **Update System**: Be able to update the AlvaOS-packages via Web UI.
- [ ] **Debian Updates**: Be able to update the Debian-packages via Web UI.

## v0.4.0 - Installer
- [ ] **RAID**: Add option in installer to install with mirror
- [ ] **User Friendly**: Make installer more user friendly
- [ ] **Power**: Make the Installer more powerfull
- [ ] **Welcome Wizzard**: Add welcome wizzard to the OS for new users and new installs

## v0.5.0 - Containerization
- [ ] **Docker Engine**: Core integration with system services.
- [ ] **App Store**: Add App Store to install and update apps.
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
- [ ] **Integrations**: Webhook support (Discord, Telegram, Email) for alerts.
- [ ] **Usage and Temperature history**: Show usage and temperature history.

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
- [ ] **AlvaOS Branding**: Update AlvaOS branding in the UI.
- [ ] **User friendly**: Make the UI more user friendly.
- [ ] **User feedback**: Add user feedback to the UI.

## v1.0.0 - Stable Release 🚀
- [ ] **Feature Complete**: All planned features implemented and stable.
- [ ] **Production Ready**: No critical bugs, complete documentation.
- [ ] **Launch**: Public release.