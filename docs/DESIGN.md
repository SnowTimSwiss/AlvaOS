# AlvaOS Design Guidelines

This document defines the visual language, interaction principles, and UX philosophy of **AlvaOS**.

AlvaOS positions itself **between TrueNAS and Unraid**:
- more capable and structured than Unraid
- far less intimidating than TrueNAS
- focused on calm, reliable daily operation

---

## Design Philosophy

AlvaOS follows a **“Stability First, Simplicity Always”** philosophy.

The UI must feel:
- calm
- predictable
- trustworthy
- never rushed or noisy

Users should feel confident leaving AlvaOS running unattended for months.

### Core Principles

1. **Clarity over Power**
   - Powerful features exist
   - They are never forced into view
   - Defaults should be safe and sensible

2. **Unraid-Like Ease, TrueNAS-Level Reliability**
   - Simple workflows for common tasks
   - Strong structure and correctness underneath

3. **Context Over Configuration**
   - Show relevant actions when they matter
   - Hide complexity unless explicitly requested

4. **No Fear UX**
   - No alarming dashboards
   - No red warnings unless action is required
   - Problems are explained calmly, with guidance

5. **Resilient by Design**
   - Long-running tasks must feel normal
   - UI stays responsive during system operations
   - Errors are recoverable and understandable

---

## Visual Style

### General Tone

- Dark, quiet, low-noise interface
- Subtle contrasts
- Minimal animations
- Information density similar to Unraid
- Structural clarity inspired by TrueNAS

---

### Color Palette

Dark mode is the default and primary experience.

- **Background:** `#0d1117`  
- **Surface:** `#161b22`  
- **Border:** `#30363d`  

- **Primary:** `#58a6ff` (Alva Blue – calm, technical)
- **Success:** `#238636`
- **Warning:** `#d29922`
- **Danger:** `#f85149`

### Text Colors (GitHub Dark Dimmed + Fresh)
- **Primary:** `#e6edf3` (Bright/Blueish White)
- **Secondary:** `#8b949e` (Grey)
- **Tertiary:** `#484f58` (Dark Grey)

Color usage rules:
- Red is reserved for **action-required states only**
- Yellow indicates attention, not failure
- Green is informational, not celebratory

### Interaction States
- **Hover:** Subtle border color change (e.g. to `#8b949e`).
- **Forbidden:** No glowing borders, no box-shadow spread, no "neon" effects.

---

### Typography

- **Primary:** `Inter`, system-ui, sans-serif  
- **Monospace:** `JetBrains Mono`, `Fira Code`, monospace  

Typography goals:
- High readability
- No decorative fonts
- Logs and technical data always monospaced

---

## UI Components

### Cards

Cards are the main structural unit.

Use cases:
- Disk status
- Pools
- Containers
- Backup status

Style:
- Border-radius: `6px`
- Background: `Surface`
- Border: `1px solid Border`

Cards should:
- Never feel cramped
- Avoid unnecessary icons
- Clearly separate status from actions

---

### Buttons

- **Primary**
  - Used sparingly
  - Only for main actions
- **Secondary**
  - Default for most interactions
- **Ghost**
  - For contextual or inline actions

No animated or flashy buttons.

---

### Status Indicators

Status must be visible but never stressful.

- **Healthy / Online:** Subtle green indicator
- **Warning:** Amber, steady
- **Critical:** Red, steady (no aggressive blinking)
- **Offline:** Grey

Animations are minimal and slow.

---

## UX Patterns

### 1. Calm System Overview (Dashboard)

The dashboard is:
- a health overview
- not a control panel

Requirements:
- All critical system states visible at a glance
- No scrolling for warnings
- No numbers without context
- “Everything is fine” should feel boring

---

### 2. Progressive Disclosure

AlvaOS assumes:
- 80% of users want simple workflows
- 20% want full control

Rules:
- Advanced settings are hidden by default
- Advanced views must still be readable
- Never punish users for opening advanced options

---

### 3. Safe Actions & Confirmation

Destructive actions always:
- explain what will happen
- explain what will not be affected
- require explicit confirmation

No surprise operations.

---

### 4. Long-Running Operations

Examples:
- Disk rebalance
- Snapshot send
- Backup restore

UI behavior:
- Clear progress indication
- Estimated time if possible
- UI remains usable
- Background tasks survive refreshes

---

### 5. Mobile & Remote Awareness

AlvaOS is often checked remotely.

Mobile support:
- Status overview
- Alerts
- Backup state
- Emergency actions only

No full configuration on mobile required.

---

## Navigation Model

### Sidebar

- Left-aligned
- Collapsible sections
- Stable ordering

Top-level sections:
- Dashboard
- Storage
- Apps
- Backup
- Users
- System

---

### Top Bar

- Breadcrumbs
- System status indicator
- Clock
- Quick search

No notifications spam.

---

## Data Presentation

### Tables

Used for:
- Containers
- Disks
- Backups

Rules:
- Sortable
- Readable without scrolling horizontally
- Actions grouped, not scattered

---

## Identity Summary

AlvaOS UI should feel like:

- **Unraid**, when doing everyday tasks  
- **TrueNAS**, when you need reliability  
- **Neither**, when it comes to complexity

If a user says:
> “This feels calm and obvious.”

Then the design is correct.

---

## Status

This document is a **living guideline**.

It defines direction, not rigidity.  
Actual implementation decisions may evolve during frontend development.
