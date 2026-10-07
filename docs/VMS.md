# Virtual machines

Windows, Linux or anything else, running inside the NAS, each with its screen
in the browser. Page: **Virtual machines** in the admin pages. This file says
how it is built and what comes next; done and open items are also in
`ROADMAP.md` and `BACKLOG.md`.

## What the person sees

- **Set up once:** the page installs the software (QEMU, UEFI firmware, a TPM
  for Windows 11; a few hundred MB) when asked, and makes a shared folder
  **VMs** on a pool the admin picks. If the NAS cannot run virtual machines
  (no VT-x/AMD-V, or switched off in the BIOS/UEFI) it says so, in words.
- **New virtual machine:** a name, what runs in it (Windows 11, Windows 10,
  Linux, other), an installer (.iso) from a list. Cores, memory and disk are
  set to sensible values for that system; the details open under *More
  options* (cores, memory, disk size, start with the NAS, forwarded ports).
- **Start, shut down, restart, switch off, delete.** Shutting down presses the
  power button of the guest and waits for it (up to two minutes); *switch off*
  is the plug.
- **Open screen:** the machine's screen and keyboard in the browser (noVNC),
  with Ctrl+Alt+Del and full screen. Works on a phone too.
- **Settings** (while the machine is off): name, installer (eject it after
  installing), cores, memory, ports, start with the NAS.

## How it is built

- **A machine is a QEMU/KVM process run by systemd:** the template unit
  `alvaos-vm@ID.service` (`scripts/alvaos-vm@.service`), as the account
  `alvaos-vm` (no login, group `kvm`). No libvirt: one daemon less, and the
  command line is ours to check.
- **The description is a small file** (`/var/lib/alvaos/vms/ID.json`) written
  by the admin backend (`vm_manager.py`). It is never passed to QEMU as a
  command line: `vm_ops.py` checks every field again (ids, numbers, paths
  inside the pools, the installer inside `VMs/ISOs`, forwarded ports that
  are not the NAS's own) and builds the command itself.
- **Privileges:** the backend starts, stops and enables a machine's unit
  through the privilege helper (`systemctl` rules for exactly
  `alvaos-vm@<8 hex>.service`). Files go through `alvaos-priv vm-setup |
  vm-prepare ID | vm-grow ID | vm-delete ID | vm-isos`. Before a start, `ExecStartPre=+`
  runs `vm_ops.py ready ID` as root so `alvaos-vm` can reach the folder and
  read the installer.
- **Disks are normal files:** `<pool>/VMs/<id>/disk.qcow2`, next to the UEFI
  variables (`OVMF_VARS.fd`) and the TPM state. `VMs` is a shared folder like
  any other (visible to the admin only), so restore points, backups and
  space limits cover the machines. A restore point of a *running* machine is
  like a power cut (crash-consistent); shut it down first for a clean one.
- **Guests:** `windows11` (UEFI, Secure Boot capable, TPM 2 from `swtpm`),
  `windows10` (UEFI), `linux` (UEFI, virtio disk and network), `other`
  (older BIOS). Windows gets SATA and an Intel network card because its
  installer has no virtio drivers; Linux gets virtio.
- **Network:** user-mode NAT: the machine reaches the network and the
  internet, with optional forwarded ports (TCP/UDP) to reach it from outside.
  No bridge yet (see below).
- **The screen:** QEMU serves VNC over WebSocket on the loopback address only
  (`127.0.0.1:5700+n`). The page asks the backend for a one-time ticket (one
  minute, one machine); the browser opens a WebSocket to a small door
  (`vm_console.py`, port 8085, HTTPS 9445 with the NAS's certificate), which
  checks the ticket and that the page is from this NAS, and then only copies
  bytes. noVNC is vendored (`frontend/vendor/novnc`, MPL-2.0) so the page loads
  nothing from outside.
- **Start with the NAS:** the unit is enabled; systemd starts it after the
  pools are mounted and, at shutdown, stops it first (power button, up to
  150 s) before the disks go away.
- **Limits:** a machine cannot have more cores than the NAS, or memory beyond
  what the NAS can spare; it does not start when there is not enough free
  memory; the disk cannot be bigger than the pool's free space.

A disk grows in the settings of a stopped machine (`vm-grow ID`: `qemu-img
resize`, only larger); the person then extends the partition in the guest.

## Disks, network and devices (Settings › "Disks, network and devices")

- **Second disk:** `data.qcow2` next to `disk.qcow2`, made by `vm-grow`
  when the size goes above 0, only grows, is not removed here.
- **Second CD:** any `.iso` from `VMs/ISOs`, for example the virtio drivers
  for Windows (`virtio-win.iso` from fedorapeople.org; Debian does not ship
  it). **Fast disks and network** then switches a Windows machine to
  virtio; only after the drivers are in, or Windows does not boot.
- **Its own address at home:** a macvtap port on the NAS's network port to
  the router (`mvt<ID>`, a fixed MAC 52:54:00:… per machine), made by
  `vm_ops.py ready` as root before the start and removed by `cleanup`
  (`ExecStopPost=+`). QEMU gets its tap device as an open file. The NAS
  itself cannot reach a macvtap guest (devices at home can); port
  forwarding is for the NAT mode only.
- **USB devices:** chosen by vendor and product id; before the start the
  device node goes to `alvaos-vm`; QEMU's `usb-host`.
- **A graphics card (VFIO):** needs IOMMU (VT-d/AMD-Vi in the BIOS); not
  the card the NAS shows its screen on. Before the start the card and
  everything in its IOMMU group is bound to `vfio-pci` (driver_override)
  and `/dev/vfio/<group>` goes to `alvaos-vm`; after the stop it goes back
  to its own driver. One running machine at a time per card; the unit
  locks memory (`LimitMEMLOCK=infinity`). The browser screen stays as a
  second display.
- **Priority low:** QEMU runs with `nice 10` and `ionice -c2 -n7`, so the
  NAS's own work (shares, backups) comes first. Cores and memory stay the
  hard limits.

## Not yet / next

- Snapshots in the page.
- QEMU's own sandbox (`-sandbox`) once tested on real machines; CPU and IO
  limits with cgroups beyond the priority.
- Home Assistant OS and other ready-made images as a one-click choice.
- Warm standby (a second NAS that can take over): see the plan for
  high availability in `ROADMAP.md`.

## Testing needs a real machine

The code is tested with fakes (command line, shutdown over QMP, the
description checks, the door for the screen, the page). A real run needs a
computer with VT-x/AMD-V (in a VM: nested virtualization): see `TESTING.md`.
