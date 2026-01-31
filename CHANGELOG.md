# Changelog

All notable changes to AlvaOS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-01-31

### Added
- **Storage Management Page**: New dedicated page for managing disks, pools, and network shares
- **Disk Detection**: Automatic detection and listing of all available block devices
  - Shows disk model, size, serial number, and filesystem type
  - Displays partition information
  - SMART health status monitoring
  - System disk identification and protection
- **Storage API Endpoints**: 
  - `/api/v1/storage/disks` - List all available disks with detailed information
  - `/api/v1/storage/pools` - List Btrfs storage pools (foundation for future features)
- **Tabbed Interface**: Clean tab navigation for Disks, Pools, and Shares sections
- **Mock Data Support**: Development mode with sample disk data for testing on non-Linux systems

### Changed
- Updated navigation across all pages to include Storage link
- Enhanced CSS with tab navigation styles
- Version bumped to 0.2.0 across all components

### Technical Details
- Backend uses `lsblk` with JSON output for disk enumeration
- SMART status checked via `smartctl` for each disk
- System disk detection by checking for root partition mounts
- Responsive disk cards with status indicators
- Placeholder functions for pool creation and share management (coming in next phases)

### Notes
- This is Phase 1 of the Storage Engine implementation
- Pool creation, Btrfs integration, and network shares are planned for upcoming phases
- File browser functionality will be added in later iterations

## [0.1.5] - 2026-01-31

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

## [0.1.0] - 2026-01-29

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
