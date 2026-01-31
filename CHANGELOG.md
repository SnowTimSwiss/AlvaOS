# Changelog

All notable changes to AlvaOS will be documented in this file.

## [0.2.0] - 31.1.26

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
