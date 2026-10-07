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

AlvaOS has a dark and a light theme. By default it follows the device
(`prefers-color-scheme`); the account menu offers Auto, Light and Dark, stored
per browser. `frontend/theme.js` runs in `<head>` and sets `data-theme` on
`<html>` before the first paint.

Every colour is a CSS custom property on `:root` in `frontend/styles.css`;
`:root[data-theme="light"]` redefines them. Components never use a fixed
colour: bars use `--track`, dialogs `--scrim`, elevation `--shadow-sm/md/lg`,
text on amber or blue badges `--on-accent`. Translucent accent tints
(`rgba(<accent>, 0.1)`) are fine in both themes. Consoles, logs and the
compose editor stay dark in both themes (`--console-bg`, `--console-fg`),
like code blocks.

Dark palette:

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

### Light palette
- **Background:** `#f6f8fa`, **Surface:** `#ffffff`, **Border:** `#d0d7de`
- **Primary:** `#0969da`, **Success:** `#1a7f37`, **Warning:** `#9a6700`, **Danger:** `#cf222e`
- **Text:** `#1f2328` / `#59636e` / `#818b98`

The light accents are darker than the dark ones so they keep their contrast
on white.

Color usage rules:
- Red is reserved for **action-required states only**
- Yellow indicates attention, not failure
- Green is informational, not celebratory

### Interaction States
- **Hover:** Subtle border color change.
- **Forbidden:** No glowing borders, no box-shadow spread, no "neon" effects.
- Drop shadows for elevation (modals, toasts) and the focus ring are not "glow"
  and remain allowed.
- Motion is suppressed entirely for users who set `prefers-reduced-motion`.

---

### Typography

- **Primary:** the platform UI stack — `-apple-system`, `BlinkMacSystemFont`,
  `Segoe UI`, `Helvetica`, `Arial`, sans-serif
- **Monospace:** `SFMono-Regular`, `Consolas`, `Liberation Mono`, `Menlo`, monospace

These are deliberately **local fonts only**. A webfont such as Inter or JetBrains
Mono would have to be downloaded, and AlvaOS is routinely run on a LAN with no
internet access — the UI must never depend on a request that cannot complete.
The same rule applies to every other runtime asset, including icons, and is
enforced in CI by `scripts/ci/check_no_external_assets.py`.

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

### Dialogs

Every dialog of the admin pages is made with `openDialog()` (in
`frontend/notifications.js`); `showConfirm()` and `showPrompt()` are built on it.
It gives each one the same frame, a title with a close button, focus kept inside,
Escape and a click beside it to close, and focus back where it was.

- The title and the buttons stay in view; only the fields in between scroll.
- Details ("More options") open in place, inside the part that scrolls.
- Short fields go side by side with `.modal-row` (one column on a phone).
- On a phone the buttons share one row instead of stacking.
- Dangerous actions: `danger: true` for a red frame and `btn-primary btn-danger`.

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
