# Changelog

All notable changes to AlvaOS will be documented in this file.

## [0.10.0] - 20.02.26

### Added - Security & Reliability
- **Two-Factor Authentication (TOTP)**: Users can now enable authenticator-based 2FA (Google Authenticator, Authy, etc.) for login.
- **Auto-Healing Watchdog**: New watchdog service monitors critical components (Samba, NFS, Docker) and automatically attempts recovery if they fail.
- **Role-Based Access Control (RBAC)**: Implemented admin vs. regular user roles. Critical system functions (power, network, user management, storage) now strictly require admin privileges.
- **Login Rate Limiting**: Brute-force protection on the login endpoint (10 attempts per 15 minutes).
- **HTTP Security Headers**: Added modern security headers (HSTS, CSP, X-Frame-Options) to the Web UI backend.

### Changed
- **Session Management**: Sessions now expire after 24 hours and include role-based token validation.
- **Login Flow**: Redesigned as a two-step process to accommodate optional 2FA codes.
- **System Settings**: Added new cards for 2FA management and Auto-Healing service status.
- **User Management**: Users now have role badges and explicit admin/user selectors.

### Fixed
- **Session cleanup**: Expired sessions are now automatically purged from memory.
- **Unauthorized API access**: Hardened various API routes that previously lacked sufficient privilege checks.


## [0.9.0] - 20.02.26

### Added - Monitoring & Health
- **Smart alerts API**: New `/api/v1/alerts` endpoint with severity-based system alerts (critical/warning/info).
- **Alert navigation targets**: Alerts now include contextual destinations so users can jump directly to relevant pages.
- **Telegram critical alerts**: New Telegram integration with bot token storage, pairing flow, test delivery, and unpair controls.

### Changed
- **Dashboard UX**: Added health overview strip, smart alert feed, and quick actions panel.
- **Alert rendering**: Dashboard now shows clickable alerts with severity chips and scan timestamp.
- **System settings UX**: Added Telegram pairing workflow in System Settings.
- **Dashboard history**: Added usage and CPU temperature history charts based on rolling dashboard samples.

## [0.8.0] - 20.02.26

### Added - Buddy Backup (Part II)
- **End-to-end buddy transfer flow**: Snapshot stream upload, remote snapshot listing, and remote restore workflow.
- **Per-buddy policy controls**: Per peer enable/disable, schedule interval, preferred send time, storage limit, and outgoing source selection.
- **Remote snapshot management**: Delete remote buddy snapshots directly from the Backup UI.
- **Local snapshot management**: Delete pool snapshots directly from the Backup UI.
- **Optional transfer encryption**: Password-based stream encryption with password requirement on restore/rollback.
- **Cloudflared app**: Added to the app catalog.

### Changed
- **Buddy pairing UX**: Streamlined token-based workflow for simpler NAS-to-NAS setup with less manual endpoint handling.
- **Backup UI layout**: Pool/System/Buddy tabs were streamlined for more consistent card structure and actions.
- **Buddy restore reliability**: Improved receive-path resolution and collision handling when temporary receive subvolumes already exist.
- **Buddy temporary snapshot handling**: Temporary send snapshots now use hidden naming and improved cleanup behavior.

### Fixed
- **Backup command path portability**: Backup manager now resolves required system commands more robustly across Linux layouts.
- **Snapshot path accessibility edge cases**: Local restore and snapshot operations now handle Btrfs subvolume checks when plain filesystem existence checks fail.
- **Buddy stream write-path fallback**: Better writable path selection and fallback behavior for stream payload storage.
- **Buddy connection testing**: Connection tests now perform active peer API probing instead of relying only on handshake recency.
- **Remote restore errors**: Improved handling for restore stream path resolution failures.

### Known Limitations
- **Advanced restore orchestration** remains intentionally minimal in this release and will continue to evolve in subsequent 0.8.x updates.

## [0.5.0] - TBD

### Added - App Store & Container Management
- **Docker Integration**: Full Docker container management with lifecycle controls (start, stop, restart, delete).
- **App Store**: Curated catalog of 6 self-hosted applications:
  - **Nextcloud**: Self-hosted cloud storage with MariaDB database
  - **Jellyfin**: Free media server for movies, music, and TV shows
  - **Home Assistant**: Open source home automation platform
  - **Pi-hole**: Network-wide ad blocker and DNS server
  - **Vaultwarden**: Self-hosted password manager (Bitwarden compatible)
  - **Immich**: High-performance photo and video backup solution with PostgreSQL
- **Apps Management UI**: New `/apps` page with 3 tabs:
  - **App Store**: Browse and install apps with category badges and descriptions
  - **Installed**: Manage installed apps with storage paths and uninstall options
  - **Containers**: View and control all Docker containers with status indicators
- **Pool-Based Installation**: Apps installed on Btrfs pools with automatic nested subvolume creation (`pool/parent/apps/app-name`)
- **Container Logs**: View container logs directly from the UI
- **Installation Wizard**: Simple app installation with pool selection and configuration

### Added - Backend Modules
- **`docker_manager.py`**: Complete Docker container lifecycle management
  - List containers with status and details
  - Start, stop, restart, and remove containers
  - Retrieve container logs and stats
  - Create containers from Docker Compose configurations
  - Pull Docker images and check daemon status
- **`app_store.py`**: App catalog and installation management
  - Browse available apps from catalog
  - Install apps with custom ports, volumes, and environment variables
  - Uninstall apps with optional data retention
  - Automatic nested Btrfs subvolume creation for app storage
  - Track installed apps with metadata

### Added - API Endpoints
- **App Store APIs**:
  - `GET /api/v1/apps/available` - List available apps
  - `GET /api/v1/apps/available/<app_id>` - Get app details
  - `POST /api/v1/apps/install` - Install an app
  - `DELETE /api/v1/apps/<app_id>` - Uninstall an app
  - `GET /api/v1/apps/installed` - List installed apps
- **Container Management APIs**:
  - `GET /api/v1/containers` - List all containers
  - `GET /api/v1/containers/<id>` - Get container details
  - `POST /api/v1/containers/<id>/start` - Start container
  - `POST /api/v1/containers/<id>/stop` - Stop container
  - `POST /api/v1/containers/<id>/restart` - Restart container
  - `GET /api/v1/containers/<id>/logs` - Get container logs
  - `DELETE /api/v1/containers/<id>` - Delete container
  - `GET /api/v1/docker/status` - Check Docker daemon status

### Changed
- **Dependencies**: Added `python3-yaml` for Docker Compose YAML parsing
- **Sudoers**: Added Docker and Btrfs subvolume management permissions for `alvaos` user
- **Package Build**: Added `docker_manager.py`, `app_store.py`, and app catalog to system package
- **Navigation**: Added "Apps" link to all pages in the web UI

### Technical Details
- **Nested Subvolumes**: Apps use structure `pool/parent/apps/app-name` for isolated storage
- **Docker Compose Support**: Full support for multi-container apps with variable substitution
- **Automatic Cleanup**: Uninstalling apps removes containers and optionally deletes data subvolumes

## [0.4.0] - 08.02.26

### Added - Professional Installer
- **Custom UI Theme**: New dark-themed Whiptail UI matching the AlvaOS design system (Blue accents on Gray/Black).
- **Network Setup**: 
  - Choice between **Automatic (DHCP)** and **Manual (Static IP)** during installation.
  - Interactive dialogs for IP, Netmask, Gateway, and DNS servers.
  - Automatic persistence of network settings to the target system.
- **Enhanced Progress UI**:
  - Silenced console output for `apt`, `debootstrap`, and storage commands to prevent UI flickering.
  - Real-time progress bar with descriptive status updates.
- **Mirror Support Verification**: Reliable Btrfs mirror detection with automatic `initramfs` updates.
- **Dynamic Success Message**: Automatically detects and displays the server's IP address for immediate Web UI access.

### Added - Welcome Wizard
- **Universal Welcome Wizard**:
  - Replaced basic password setup with a professional multi-step onboarding process.
  - **Step 1: Welcome**: Visual greeting with environment detection and IP confirmation.
  - **Step 2: Security**: Secure root password setup with real-time requirement validation.
  - **Step 3: Regional**: Simplified Timezone selection (e.g., Europe/Berlin) integrated into the setup flow.
  - **Step 4: Finish**: Animated completion screen with automatic redirect to the dashboard.

### Changed
- **Backend API**: Enhanced `/api/v1/setup/complete` to support `timezone` configuration via `timedatectl`.
- **Frontend Refactor**: Overhauled `setup.html` with a modern, responsive card design and animated transitions.
- **Installer Cleanup**: Massive cleanup of `install-system.sh` for better readability and logging.

### Technical Details
- **Cleanup**: All installer logs are now redirected to `/tmp/alvaos-install.log` for debugging.
- **Validation**: Added client-side password strength and match validation to the setup wizard.
- **Persistence**: Network interfaces are dynamically detected using `ip link show` for accurate config generation.

## [0.3.0] - 06.02.26

### Added - Update System
- **Comprehensive Update Manager**:
  - **Online Updates**: direct updates from GitHub releases for AlvaOS
  - **Debian Updates**: integrated `apt` package manager interface
  - **Offline Updates**: support for installing .deb packages from USB drives
  - **Update Channels**: switch between Stable and Unstable release channels
- **Update UI**:
  - Dedicated Updates page with tabs for AlvaOS, Debian, and Offline updates
  - Release notes display
  - Update history log
  - Auto-check configuration settings

### Added - User Management
- **User Administration**:
  - Create, edit, and delete system users via Web UI
  - Password management with complexity requirements
  - Role-based permissions (Admin/User)
- **Samba Integration**:
  - Automatic synchronization of system users to Samba user database
  - Management of per-user SMB share permissions

### Fixed
- **Update Permissions**: Resolved issue where update checker ran as root, causing permission errors for the backend service (fixed in `alvaos-update-checker.service`)

## [0.2.0] - 3.2.26

### Added - Phase 1: Disk Detection
- **Storage Management Page**: New dedicated page for managing disks, pools, and network shares
- **Disk Detection**: Automatic detection and listing of all available block devices
  - Shows disk model, size, serial number, and filesystem type
  - Displays partition information
  - SMART health status monitoring
  - System disk identification and protection
- **Storage API Endpoints**: 
  - `/api/v1/storage/disks` - List all available disks with detailed information
- **Tabbed Interface**: Clean tab navigation for Disks, Pools, and Shares sections
- **Mock Data Support**: Development mode with sample disk data for testing on non-Linux systems

### Added - Phase 2: Btrfs Pool Creation & Management
- **Pool Creation**: Full Btrfs pool creation with interactive wizard
  - Multi-disk selection interface
  - RAID level configuration (Single, RAID0, RAID1, RAID10)
  - Real-time validation of RAID requirements
  - Automatic pool mounting at `/mnt/alvaos/{pool_name}`
  - Pool state persistence
- **Pool Management**: 
  - List all existing Btrfs pools with details
  - Show pool capacity, usage, RAID level
  - Display devices in each pool
  - Delete pools (unmount only, data preserved)
- **Subvolume Management**:
  - Create subvolumes within pools
  - List all subvolumes
  - Delete subvolumes with confirmation
  - Interactive subvolume management dialog
- **Enhanced API Endpoints**:
  - `/api/v1/storage/pools` - GET, POST, DELETE for pool management
  - `/api/v1/storage/pools/<pool_id>/subvolumes` - GET, POST, DELETE for subvolume operations
- **Btrfs Command Integration**:
  - `mkfs.btrfs` for pool creation
  - `btrfs filesystem show` for pool enumeration
  - `btrfs filesystem usage` for capacity information
  - `btrfs subvolume` commands for subvolume management

### Added - Phase 3: Network Shares (NFS/SMB)
- **Share Creation**: Full network share creation with interactive wizard
  - Protocol selection (NFS or SMB/Samba)
  - Path selection from pools and subvolumes
  - Custom path support
  - Read-only access toggle
  - NFS: Host access control (wildcards, IP ranges)
  - SMB: Guest access toggle
- **Share Management**:
  - List all configured network shares
  - Show share details (protocol, path, access type)
  - Delete shares with confirmation
  - Connection instructions for all platforms
- **Connection Info Dialog**:
  - Platform-specific mount instructions (Linux, macOS, Windows)
  - Copy-paste ready commands
  - Share details summary
- **Enhanced API Endpoints**:
  - `/api/v1/storage/shares` - GET, POST, DELETE for share management
- **Service Integration**:
  - NFS: Automatic `/etc/exports` configuration
  - SMB: Automatic `/etc/samba/smb.conf` configuration
  - Service restart after configuration changes
  - Share state persistence

### Changed
- Updated navigation across all pages to include Storage link
- Enhanced CSS with tab navigation styles and modal dialogs
- Version bumped to 0.2.0 across all components
- Improved error handling and user feedback

### Technical Details - Phase 1
- Backend uses `lsblk` with JSON output for disk enumeration
- SMART status checked via `smartctl` for each disk
- System disk detection by checking for root partition mounts
- Responsive disk cards with status indicators

### Technical Details - Phase 2
- Btrfs pool creation with configurable RAID levels
- Automatic mount point creation and management
- Pool state stored in `/var/lib/alvaos/pools.json`
- Comprehensive parsing of `btrfs` command outputs
- Modal-based wizards for complex operations
- Real-time disk selection and validation

### Technical Details - Phase 3
- NFS share configuration via `/etc/exports`
- SMB share configuration via `/etc/samba/smb.conf`
- Automatic service management (`exportfs -ra`, `systemctl restart smbd`)
- Share state stored in `/var/lib/alvaos/shares.json`
- Dynamic path selection from pools and subvolumes
- Platform-specific connection instructions

### Notes
- Phase 1 (Disk Detection) ✅ Complete
- Phase 2 (Pool Creation & Subvolumes) ✅ Complete
- Phase 3 (Network Shares - NFS/SMB) ✅ Complete
- Phase 4 (SMART Monitoring, File Browser) - Planned

## [0.1.5] - 31.1.26

### Fixed
- **Network IP Address Display**: Fixed IP address showing as "127.0.0.1" in settings. Now correctly detects and displays the actual network interface IP address using psutil.
- **Power Controls**: Enabled shutdown and reboot functionality. These buttons now properly execute system power commands on Linux systems.
- **Duplicate Script Loading**: Removed duplicate `app.js` script tag in `system.html` that was causing potential conflicts.

### Changed
- Improved network information detection to show real interface names, subnet masks, gateway, and DNS servers (on Linux systems).
- Enhanced error handling for power control actions with proper exception handling.
- Updated all version strings from 0.1.0 to 0.1.5 throughout the codebase.

### Technical Details
- Backend now uses `psutil.net_if_addrs()` to enumerate network interfaces and find the first non-loopback IPv4 address.
- Power control commands now use `subprocess.Popen()` to allow the API response to be sent before system shutdown/reboot.
- Gateway detection uses `ip route show default` command on Linux.
- DNS servers are read from `/etc/resolv.conf` on Linux systems.

## [0.1.0] - 30.1.26

### Added
- Initial release with basic dashboard functionality
- System information display (CPU, Memory, Disk, Network)
- Basic authentication system
- Setup wizard for initial configuration
- System settings page with hostname configuration
- Basic logging viewer

### Features
- Dashboard with real-time system metrics
- User authentication with token-based sessions
- Network configuration display
- System logs viewer
- Responsive web interface with dark theme
