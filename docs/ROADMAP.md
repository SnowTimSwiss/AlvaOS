# Roadmap

AlvaOS is a calm NAS OS for storage, apps, and offsite backup.

This is a simple to-do roadmap. Items are checked off when shipped.

## Core

- [ ] Installer & First-Time Setup
  - [ ] Minimal bootable installer image
  - [ ] Guided first install flow
  - [ ] First-time setup for admin access
  - [ ] Clear setup-required state after install

- [ ] Authentication & Security
  - [ ] Login flow
  - [ ] CSRF and session handling
  - [ ] 2FA setup, verify, and disable
  - [ ] Admin-only actions clearly separated

- [ ] Dashboard
  - [ ] Storage, backup, alerts, and updates in one clear overview
  - [ ] Quick access to the most common actions
  - [ ] Calm, low-noise status presentation

- [ ] Storage
  - [ ] Pool overview, disk health, and capacity visibility
  - [ ] Create, import, expand, and inspect pools
  - [ ] Shares and permissions tied cleanly to storage

- [ ] Apps
  - [ ] App catalog with install flow
  - [ ] Compose-based installs for advanced users
  - [ ] Installed app management, updates, and removal

- [ ] Backup
  - [ ] Local snapshots and restore points
  - [ ] Restore flow that is easy to understand
  - [ ] Retention and scheduling controls

- [ ] Buddy Backup
  - [ ] Pair two AlvaOS nodes
  - [ ] Encrypted offsite transfer
  - [ ] Remote snapshots, restore, and peer policies

- [ ] Alerts
  - [ ] Health alerts for disks, pools, memory, CPU, and backup state
  - [ ] Clear links from an alert to the place where it can be resolved
  - [ ] Calm wording, no noisy alarm style
  - [ ] Telegram critical alerts
  - [ ] Telegram pairing, test, and unpair flow
  - [ ] Better alert notifications for remote monitoring

- [ ] Notifications
  - [ ] In-app alert summary
  - [ ] Telegram as a notification channel
  - [ ] Critical alert delivery for remote monitoring
  - [ ] Better notification settings and status feedback

- [ ] Watchdog
  - [ ] Watchdog status overview
  - [ ] Manual watchdog check
  - [ ] Background health supervision
  - [ ] Clear failure reporting when services degrade

## System

- [ ] Users
  - [ ] First-run setup
  - [ ] Admin and user management
  - [ ] Optional 2FA

- [ ] System
  - [ ] Time, hostname, network, logs, and power controls
  - [ ] Better diagnostics and permission checks
  - [ ] UPS / power monitoring and actions
  - [ ] Better system readiness and permission diagnostics

- [ ] Installer & Updates
  - [ ] Deterministic install flow
  - [ ] Safer update handling and rollback thinking
  - [ ] Clear status during long-running operations
  - [ ] Update history and current status
  - [ ] Offline update scan and apply
  - [ ] Debian package updates and OS upgrades
  - [ ] Clear update notifications in the UI

## Polish

- [ ] Better dashboard hierarchy
- [ ] Stronger mobile view for remote checks
- [ ] Better restore selection and snapshot browsing
- [ ] Smoother progress reporting for long jobs
- [ ] Better pairing feedback and connection status
- [ ] More predictable restore behavior
- [ ] Full UI redesign
- [ ] New design system and consistent component styles
- [ ] Redo the dashboard visual language
- [ ] Make the UI feel more modern and less technical
- [ ] Redesign the backup and storage pages
- [ ] Tighten spacing, hierarchy, and component consistency
- [ ] Make destructive actions more clearly explained

## Platform

- [ ] Split the backend into smaller modules
- [ ] Reduce the size of `backend/alvaos-backend.py`
- [ ] Keep route handlers thin and feature-specific
- [ ] Separate API, business logic, and system access more cleanly
- [ ] Make the codebase easier to maintain and extend
- [ ] Improve how frontend and backend contract together
- [ ] Split API routes by domain
- [ ] Move storage, backup, alerts, and updates into smaller services
- [ ] Keep UI state logic out of route handlers

## Apps & Containers

- [ ] App catalog browsing
- [ ] App install flow
- [ ] Compose-based installs
- [ ] Installed app overview
- [ ] App update flow
- [ ] App uninstall flow
- [ ] Container logs
- [ ] Container exec shell
- [ ] Container start, stop, restart, and delete

## Later

- [ ] Plugin system
- [ ] Optional VM support
- [ ] Alternative storage backends such as ZFS
- [ ] Multi-peer Buddy Backup
- [ ] More advanced alert routing
- [ ] Better remote access and mobile-first views
