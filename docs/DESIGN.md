# AlvaOS Design Guidelines

This document defines the visual language, interaction principles, and UX philosophy of **AlvaOS**.

AlvaOS positions itself **between TrueNAS, ZimaOS, UmbrelOS and Unraid**:
- more capable and structured than Unraid, ZimaOS and Umbrel
- far less intimidating than TrueNAS
- focused on calm, reliable daily operation
- Perfect for set and forget beginners as well as power users.

---

## Design Philosophy

AlvaOS follows a **“Stability First, Simplicity Always”** philosophy.

The UI must feel:
- calm
- predictable
- trustworthy
- never rushed or noisy

Users should feel confident leaving AlvaOS running unattended for months to years.

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
   - just show things that are needed

4. **No Fear UX**
   - No alarming dashboards
   - No red warnings unless action is required
   - Problems are explained calmly, with guidance
   - no warnings without links to the page where it can be fixed
   - no error codes without clear meaning

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
- looking modern like UmbrelOS or ZimaOS

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
- **Hover:** Subtle border color change.
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

**Do we realy want it this way? is there a better way?**

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
- Advanced settings are hidden by default but accessible
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


**Do we really want it like that? is there a better way?**
---

### Top Bar

- Breadcrumbs
- System status indicator
- Clock
- Quick search

No notifications spam.

**Do we really want it like that? is there a better way?**


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

**Do we really want it like that? is there a better way?**


---

## Identity Summary

AlvaOS UI should feel like:

- **Unraid**, when doing everyday tasks  
- **TrueNAS**, when you need reliability  
- **Neither**, when it comes to complexity
- **ZimaOS/UmbrelOS**, when looking at accessibility

If a user says:
> “yeah it was really easy but my advanced configs worked just fine too.”

Then the design is correct.

---

## Status

This document is a **living guideline**.

It defines direction, not rigidity.  
Actual implementation decisions may evolve during frontend development.
