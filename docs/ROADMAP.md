# Roadmap to v1.0

Release strategy: "Iterate fast, stabilize often."

## v0.1.0 - Foundation
- [x] **Core Architecture**: Repository structure and tech stack definition.
- [x] **UI Prototype**: Design system, basic layout, and navigation.
- [x] **Docs**: Initial documentation (README, DESIGN, STRUCTURE).

## v0.1.5 - Foundation Extended
- [x] **Dashboard**: Basic dashboard with system information (OS info, uptime).
- [x] **System Settings**: Hostname, basic network config, and date/time.
- [x] **Logging**: Basic UI for viewing backend logs.

## v0.2.0 - Storage Engine
- [x] **Disk Detection**: Enumerate available drives via Web UI.
- [x] **Btrfs Integration**: Pool creation, subvolume management.
- [x] **Basic Shares**: NFS/SMB export configuration.
- [x] **Disk Health**: SMART monitoring and basic health checks.
- [x] **Pool Expansion**: Add disks to existing pools.
- [will come back later] **File Browser**: Simple web-based file manager to browse and manage files.

## v0.3.0 - Updates and Users
- [x] **Update System**: Update the AlvaOS package via Web UI.
- [x] **Debian Updates**: Update Debian packages via Web UI.
- [x] **Offline Update**: Offline updates via USB stick in the Web UI.
- [x] **Update Checker**: Check for updates on startup and notify the user.
- [x] **User Management**: User management via Web UI.
- [x] **SMB Management**: Assign SMB permissions to different users.

## v0.4.0 - Installer
- [x] **RAID**: Add an option in the installer to install with mirror.
- [x] **User Friendly**: Make the installer more user friendly.
- [x] **Power**: Make the installer more powerful.
- [x] **Welcome Wizard**: Add a welcome wizard for new users and new installs.

## v0.5.0 - Containerization
- [x] **Docker Engine**: Core integration with system services.
- [x] **App Store**: Add an App Store to install and update apps.
- [x] **Container Management**: Start, stop, logs, and basic config via UI.
- [x] **Docker Compose**: Install custom apps with Docker Compose.
- [will come back later] **Custom App Store**: Add support for custom app stores.

## v0.6.0 - Local Backups
- [x] **Backup Engine**: Create snapshots.
- [x] **Restore**: Roll back to previous snapshots.
- [x] **Schedule**: Automatic snapshot creation.

## v0.7.0 - Buddy Backup (Part I: Connection)
- [x] **Identity**: Key generation and peering logic.
- [x] **WireGuard**: Automated tunnel setup between peers.
- [x] **Handshake**: Secure pairing workflow (token).

## v0.8.0 - Buddy Backup (Part II: Transfer)
- [x] **Snapshot Engine**: Btrfs send/receive logic.
- [x] **Encryption**: Client-side encryption before transfer.
- [x] **Restore**: Recovery workflow from remote peer.

## v0.9.0 - Monitoring and Health
- [x] **Disks**: SMART monitoring and basic health checks.
- [x] **Smart Alerts**: Notification system for failures and warnings with clickable destinations.
- [x] **Integrations**: Telegram notifications with bot pairing workflow.
- [x] **Usage and Temperature History**: Show usage and temperature history.
- [x] **Better Dashboard**: Health overview, quick actions, and alert feed.

## v0.10.0 - Security
- [x] **Auto-Healing**: Service recovery.
- [x] **Security**: Security updates and patches.
- [x] **User Management**: User and group management with different permissions (RBAC).
- [x] **2FA**: Two-factor authentication for admin (TOTP).

## v0.11.0 - Polish (Beta)
- [x] **UX Refinement**: Mobile view, animations, and themes.
- [x] **Performance**: Optimization of API and frontend.
- [x] **Bug Hunt**: Excessive testing and edge-case fixing.
- [x] **AlvaOS Branding**: Update AlvaOS branding in the UI.
- [x] **User Friendly**: Make the UI more user friendly.
- [x] **User Feedback**: Add user feedback to the UI.
- [x] **Custom Icons**: Replace emojis with custom icons (Lucide).

## v0.12.0 - Debian 13
- [x] **Debian 13**: Update from Debian 12 to Debian 13.

## v1.0.0 - Stable Release
- [x] **Feature Complete**: All planned features implemented and stable.
- [x] **Production Ready**: No critical bugs, complete documentation.
- [x] **Launch**: Public release.
