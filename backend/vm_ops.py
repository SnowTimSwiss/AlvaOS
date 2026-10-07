#!/usr/bin/env python3
"""Virtual machines, the part that touches the system (see docs/VMS.md).

A virtual machine is a QEMU/KVM process run by systemd (`alvaos-vm@ID.service`)
as the unprivileged account `alvaos-vm`. Its description is a small JSON file
(`/var/lib/alvaos/vms/ID.json`) that the admin backend writes. Nothing the
backend writes is ever handed to QEMU as a command line: this module checks
every field again and builds the command itself, so a wrong or hostile file
can at most start a VM that has less than it asked for.

Three kinds of callers:

* the systemd unit, as `alvaos-vm`: `vm_ops.py run ID` starts the VM (and its
  TPM), `vm_ops.py stop ID` asks the guest to shut down and waits for it;
* the same unit, as root (`ExecStartPre=+`): `vm_ops.py ready ID` makes sure
  `alvaos-vm` can reach the folder and the installer image;
* `alvaos-priv` as root: `vm-prepare ID` (folder, disk, UEFI variables),
  `vm-grow ID` (the disk to the size in the description, never smaller),
  `vm-delete ID`, `vm-isos` (installer images to choose from), `vm-setup`;
* the backend, only through the two above (it uses systemctl for the rest).

Disks are normal files (qcow2) in the "VMs" shared folder on a pool, so restore
points, backups and space limits work for them like for any share.
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

STATE_FILE = '/var/lib/alvaos/vms.json'      # {"store": "/mnt/alvaos/main/VMs"}
CONFIG_DIR = '/var/lib/alvaos/vms'           # ID.json, one per virtual machine
RUN_DIR = '/run/alvaos-vm'                   # sockets (QMP, TPM), made by the unit
DATA_ROOT = '/mnt/alvaos'
VM_USER = 'alvaos-vm'
VM_GROUP = 'alvaos'
QEMU = '/usr/bin/qemu-system-x86_64'
QEMU_IMG = '/usr/bin/qemu-img'
SWTPM = '/usr/bin/swtpm'
SYSTEMCTL = '/usr/bin/systemctl'
FIRMWARE_DIRS = ('/usr/share/OVMF', '/usr/share/qemu')
GRACE_SECONDS = 110      # a guest gets this long to shut down before it is switched off
VNC_WEBSOCKET_BASE = 5700

ID_RE = re.compile(r'^[0-9a-f]{8}$')
USB_ID_RE = re.compile(r'^[0-9a-f]{4}$')
PCI_RE = re.compile(r'^0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$')
SYS = '/sys'
DEV = '/dev'
IP = '/usr/bin/ip'
IONICE = '/usr/bin/ionice'
NETWORKS = ('nat', 'bridge')
PRIORITIES = ('normal', 'low')
ISO_FOLDER = 'ISOs'
UNIT_FMT = 'alvaos-vm@{}.service'

# What each kind of guest needs. Windows has no driver for fast virtual disks
# and network cards on its installer, so it gets ones it knows (SATA, Intel);
# Linux gets the fast ones (virtio).
OS_TYPES: Dict[str, Dict[str, Any]] = {
    'linux':     {'name': 'Linux', 'uefi': True, 'tpm': False, 'secure': False, 'disk': 'virtio',
                  'nic': 'virtio-net-pci', 'localtime': False},
    'windows11': {'name': 'Windows 11', 'uefi': True, 'tpm': True, 'secure': True, 'disk': 'sata',
                  'nic': 'e1000e', 'localtime': True},
    'windows10': {'name': 'Windows 10', 'uefi': True, 'tpm': False, 'secure': False, 'disk': 'sata',
                  'nic': 'e1000e', 'localtime': True},
    'other':     {'name': 'Other (older BIOS)', 'uefi': False, 'tpm': False, 'secure': False, 'disk': 'sata',
                  'nic': 'e1000e', 'localtime': False},
}
# Ports the NAS itself uses: a virtual machine cannot take them over.
RESERVED_PORTS = {22, 25, 80, 111, 139, 443, 445, 2049, 5353, 8080, 8085, 8086, 8090, 8091, 8443, 9443, 9444, 9445,
                  9446, 51820}


class VmError(Exception):
    """A sentence for the person."""


# ── The description of a virtual machine ─────────────────────────────────────

def _int(value: Any, low: int, high: int, what: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise VmError(f'{what} has to be a number.') from None
    if not low <= number <= high:
        raise VmError(f'{what} has to be between {low} and {high}.')
    return number


def clean_path(path: str, root: str = DATA_ROOT) -> str:
    """An absolute path below the data root, without tricks."""
    if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
        raise VmError('That is not a place on the storage pools.')
    norm = os.path.normpath(path)
    if norm != path:                      # no "..", "//" or trailing "/"
        raise VmError('That is not a place on the storage pools.')
    if not (norm + '/').startswith(root.rstrip('/') + '/') or norm == root.rstrip('/'):
        raise VmError('That is not a place on the storage pools.')
    return norm


def check_config(raw: Any, root: str = DATA_ROOT, store: Optional[str] = None) -> Dict[str, Any]:
    """The description with every field checked; raises VmError. With `store`,
    the installer image has to be in its "ISOs" folder."""
    if not isinstance(raw, dict):
        raise VmError('The virtual machine is not described properly.')
    vm_id = str(raw.get('id') or '')
    if not ID_RE.match(vm_id):
        raise VmError('That is not a virtual machine.')
    kind = str(raw.get('os') or '')
    if kind not in OS_TYPES:
        raise VmError('Choose what will run in it.')
    name = re.sub(r'\s+', ' ', str(raw.get('name') or '')).strip()
    if not name or len(name) > 60 or any(ord(c) < 32 for c in name):
        raise VmError('Give it a name (up to 60 characters).')
    iso = str(raw.get('iso') or '')
    if iso:
        iso = clean_path(iso, root)
        if not iso.lower().endswith('.iso'):
            raise VmError('The installer has to be an .iso file.')
        if store is not None and not iso.startswith(os.path.join(store, ISO_FOLDER) + '/'):
            raise VmError(f'Put the installer in the folder "{ISO_FOLDER}" of the VMs shared folder.')
    ports = []
    for item in raw.get('ports') or []:
        if not isinstance(item, dict):
            raise VmError('A forwarded port is not described properly.')
        proto = str(item.get('proto') or 'tcp')
        if proto not in ('tcp', 'udp'):
            raise VmError('A forwarded port is TCP or UDP.')
        host = _int(item.get('host'), 1024, 65535, 'The port on the NAS')
        guest = _int(item.get('guest'), 1, 65535, 'The port in the virtual machine')
        if host in RESERVED_PORTS or 5700 <= host <= 5999:
            raise VmError(f'Port {host} is used by the NAS itself. Choose another.')
        ports.append({'proto': proto, 'host': host, 'guest': guest})
    if len(ports) > 16 or len({(p['proto'], p['host']) for p in ports}) != len(ports):
        raise VmError('Each port on the NAS can be forwarded once, 16 at most.')
    iso2 = str(raw.get('iso2') or '')
    if iso2:
        iso2 = clean_path(iso2, root)
        if not iso2.lower().endswith('.iso'):
            raise VmError('The second CD has to be an .iso file.')
        if store is not None and not iso2.startswith(os.path.join(store, ISO_FOLDER) + '/'):
            raise VmError(f'Put the second CD in the folder "{ISO_FOLDER}" of the VMs shared folder.')
    usb = []
    for item in raw.get('usb') or []:
        if not isinstance(item, dict) or not USB_ID_RE.match(str(item.get('vendor') or '')) \
                or not USB_ID_RE.match(str(item.get('product') or '')):
            raise VmError('A USB device is not described properly.')
        usb.append({'vendor': str(item['vendor']), 'product': str(item['product']),
                    'name': re.sub(r'[^\w .,()+-]', '', str(item.get('name') or ''))[:60]})
    if len(usb) > 8 or len({(u['vendor'], u['product']) for u in usb}) != len(usb):
        raise VmError('Up to 8 different USB devices.')
    gpu = str(raw.get('gpu') or '')
    if gpu and not PCI_RE.match(gpu):
        raise VmError('That is not a graphics card of this NAS.')
    network = str(raw.get('network') or 'nat')
    if network not in NETWORKS:
        raise VmError('Choose how the machine is on the network.')
    priority = str(raw.get('priority') or 'normal')
    if priority not in PRIORITIES:
        raise VmError('Choose a priority.')
    return {
        'id': vm_id, 'name': name, 'os': kind,
        'cpus': _int(raw.get('cpus'), 1, 64, 'The number of processor cores'),
        'memory_mb': _int(raw.get('memory_mb'), 256, 1024 * 1024, 'The memory'),
        'disk_gb': _int(raw.get('disk_gb'), 1, 65536, 'The disk size'),
        'iso': iso, 'autostart': bool(raw.get('autostart')),
        'slot': _int(raw.get('slot', 0), 0, 99, 'The screen number'),
        'ports': ports,
        # Since 2026-10-07: a second disk, a second CD (drivers), fast virtio
        # devices for Windows once its drivers are in, the home network,
        # USB devices, a graphics card, and a lower priority.
        'data_gb': _int(raw.get('data_gb', 0), 0, 65536, 'The second disk'),
        'iso2': iso2, 'fast': bool(raw.get('fast')), 'network': network, 'usb': usb, 'gpu': gpu,
        'priority': priority,
    }


def read_store(state_file: str = STATE_FILE, root: str = DATA_ROOT) -> str:
    """The folder the virtual machines live in (the "VMs" shared folder)."""
    try:
        with open(state_file) as f:
            store = json.load(f).get('store')
    except (OSError, ValueError, AttributeError):
        store = None
    if not store:
        raise VmError('Virtual machines are not set up yet.')
    store = clean_path(str(store), root)
    if os.path.islink(store) or not os.path.isdir(store):
        raise VmError('The folder for virtual machines is not there. Check Storage › Shared folders.')
    return store


def load_config(vm_id: str, config_dir: str = CONFIG_DIR, root: str = DATA_ROOT,
                state_file: Optional[str] = STATE_FILE) -> Dict[str, Any]:
    if not ID_RE.match(str(vm_id)):
        raise VmError('That is not a virtual machine.')
    try:
        with open(os.path.join(config_dir, f'{vm_id}.json')) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raise VmError('That virtual machine is not there (any more).') from None
    cfg = check_config(raw, root, read_store(state_file, root) if state_file else None)
    if cfg['id'] != vm_id:
        raise VmError('That virtual machine is not described properly.')
    return cfg


def vm_dir(store: str, vm_id: str) -> str:
    return os.path.join(store, vm_id)


# ── The QEMU command ─────────────────────────────────────────────────────────

def find_firmware(secure: bool, dirs: Optional[Tuple[str, ...]] = None) -> Tuple[str, str]:
    """(code, variables template) of the UEFI firmware (package ovmf)."""
    codes = ['OVMF_CODE_4M.secboot.fd', 'OVMF_CODE.secboot.fd'] if secure else ['OVMF_CODE_4M.fd', 'OVMF_CODE.fd']
    stores = ['OVMF_VARS_4M.ms.fd', 'OVMF_VARS.ms.fd'] if secure else ['OVMF_VARS_4M.fd', 'OVMF_VARS.fd']

    def first(names: List[str]) -> str:
        for d in dirs or FIRMWARE_DIRS:
            for n in names:
                if os.path.isfile(os.path.join(d, n)):
                    return os.path.join(d, n)
        raise VmError('The UEFI firmware is missing. Set up virtual machines again (package ovmf).')
    return first(codes), first(stores)


def qemu_args(cfg: Dict[str, Any], store: str, run_dir: str = RUN_DIR, firmware: Optional[Tuple[str, str]] = None,
              kvm: bool = True, tap_fd: Optional[int] = None, vfio: Optional[List[str]] = None) -> List[str]:
    """The whole QEMU command line for one virtual machine."""
    kind = OS_TYPES[cfg['os']]
    base = vm_dir(store, cfg['id'])
    slot = cfg['slot']
    machine = 'q35' + (',accel=kvm' if kvm else '') + (',smm=on' if kind['secure'] else '')
    args = [QEMU, '-name', f'alvaos-{cfg["id"]},process=alvaos-vm-{cfg["id"]}', '-nodefaults', '-no-user-config',
            '-machine', machine, '-cpu', 'host' if kvm else 'max', '-smp', str(cfg['cpus']),
            '-m', str(cfg['memory_mb']), '-rtc', 'base=localtime' if kind['localtime'] else 'base=utc',
            '-boot', 'menu=off', '-display', 'none', '-monitor', 'none', '-serial', 'none', '-parallel', 'none']
    if kind['uefi']:
        code, _vars = firmware or find_firmware(kind['secure'])
        args += ['-drive', f'if=pflash,format=raw,unit=0,readonly=on,file={code}',
                 '-drive', f'if=pflash,format=raw,unit=1,file={os.path.join(base, "OVMF_VARS.fd")}']
        if kind['secure']:
            args += ['-global', 'driver=cfi.pflash01,property=secure,value=on']
    fast = kind['disk'] == 'virtio' or cfg.get('fast')
    disks = [('disk0', os.path.join(base, 'disk.qcow2'), 2)]
    if cfg.get('data_gb'):
        disks.append(('disk1', os.path.join(base, 'data.qcow2'), 3))
    if not fast:
        args += ['-device', 'ich9-ahci,id=ahci']
    for n, (ident, path, boot) in enumerate(disks):
        args += ['-drive', f'file={path},if=none,id={ident},format=qcow2,cache=none,discard=unmap']
        args += ['-device', f'virtio-blk-pci,drive={ident},bootindex={boot}' if fast
                 else f'ide-hd,drive={ident},bus=ahci.{n},bootindex={boot}']
    cds = [c for c in (cfg['iso'], cfg.get('iso2')) if c]
    if cds:
        args += ['-device', 'ich9-ahci,id=ahci1']
        for n, iso in enumerate(cds):
            args += ['-drive', f'file={iso},if=none,id=cd{n},media=cdrom,readonly=on',
                     '-device', f'ide-cd,drive=cd{n},bus=ahci1.{n}' + (',bootindex=1' if n == 0 else '')]
    nic = 'virtio-net-pci' if fast else kind['nic']
    if cfg.get('network') == 'bridge' and tap_fd is not None:
        # Its own address from the router (macvtap, made by `ready` as root).
        args += ['-netdev', f'tap,id=net0,fd={tap_fd}', '-device', f'{nic},netdev=net0,mac={mac_address(cfg["id"])}']
    else:
        forwards = ''.join(f',hostfwd={p["proto"]}::{p["host"]}-:{p["guest"]}' for p in cfg['ports'])
        args += ['-netdev', f'user,id=net0{forwards}', '-device', f'{nic},netdev=net0']
    args += ['-device', 'virtio-rng-pci', '-vga', 'std',
             '-device', 'qemu-xhci,id=xhci', '-device', 'usb-tablet', '-device', 'usb-kbd',
             '-vnc', f'127.0.0.1:{slot},websocket={VNC_WEBSOCKET_BASE + slot}',
             '-qmp', f'unix:{os.path.join(run_dir, cfg["id"] + ".qmp")},server=on,wait=off']
    for dev in cfg.get('usb') or []:
        args += ['-device', f'usb-host,bus=xhci.0,vendorid=0x{dev["vendor"]},productid=0x{dev["product"]}']
    for addr in vfio or []:
        args += ['-device', f'vfio-pci,host={addr}']
    if kind['tpm']:
        args += ['-chardev', f'socket,id=chrtpm,path={os.path.join(run_dir, cfg["id"] + ".tpm")}',
                 '-tpmdev', 'emulator,id=tpm0,chardev=chrtpm', '-device', 'tpm-tis,tpmdev=tpm0']
    return args


def swtpm_args(cfg: Dict[str, Any], store: str, run_dir: str = RUN_DIR) -> List[str]:
    state = os.path.join(vm_dir(store, cfg['id']), 'tpm')
    return [SWTPM, 'socket', '--tpm2', '--tpmstate', f'dir={state}',
            '--ctrl', f'type=unixio,path={os.path.join(run_dir, cfg["id"] + ".tpm")}']


# ── The NAS's own devices for a machine ──────────────────────────────────────
#
# Reading what there is needs no privileges (the page lists it). Handing a
# device to a machine happens as root right before it starts (`ready`, from
# the unit's ExecStartPre=+) and is undone after it stopped (`cleanup`,
# ExecStopPost=+): a USB device's node goes to `alvaos-vm`; a graphics card
# and everything in its IOMMU group is bound to vfio-pci (and back to its
# own driver afterwards); "own address at home" is a macvtap interface on
# the NAS's network port whose tap device `alvaos-vm` may open.

def _read_sys(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def _write_sys(path: str, value: str) -> None:
    with open(path, 'w') as f:
        f.write(value)


def mac_address(vm_id: str) -> str:
    """A fixed address per machine, so the router gives it the same IP again."""
    return '52:54:00:' + ':'.join(vm_id[i:i + 2] for i in (0, 2, 4))


def tap_name(vm_id: str) -> str:
    return f'mvt{vm_id}'


def default_interface(route_file: str = '/proc/net/route') -> str:
    """The network port the NAS reaches its router through."""
    try:
        with open(route_file) as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == '00000000' and re.match(r'^[A-Za-z0-9_.-]{1,15}$', parts[0]):
                    return parts[0]
    except OSError:
        pass
    raise VmError('The NAS has no network port with a router.')


def usb_devices(sys_root: str = SYS) -> List[Dict[str, Any]]:
    """USB devices plugged into the NAS (without hubs)."""
    out = []
    base = os.path.join(sys_root, 'bus/usb/devices')
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        dev = os.path.join(base, name)
        vendor, product = _read_sys(os.path.join(dev, 'idVendor')), _read_sys(os.path.join(dev, 'idProduct'))
        if not USB_ID_RE.match(vendor) or not USB_ID_RE.match(product):
            continue
        if _read_sys(os.path.join(dev, 'bDeviceClass')) == '09' or vendor == '1d6b':
            continue                                    # hubs and the root hubs
        label = ' '.join(x for x in (_read_sys(os.path.join(dev, 'manufacturer')),
                                     _read_sys(os.path.join(dev, 'product'))) if x)
        out.append({'vendor': vendor, 'product': product, 'name': label[:60] or f'USB device {vendor}:{product}',
                    'bus': _read_sys(os.path.join(dev, 'busnum')), 'dev': _read_sys(os.path.join(dev, 'devnum'))})
    return out


def iommu_on(sys_root: str = SYS) -> bool:
    try:
        return bool(os.listdir(os.path.join(sys_root, 'kernel/iommu_groups')))
    except OSError:
        return False


def group_members(slot: str, sys_root: str = SYS) -> List[str]:
    """The PCI devices that go with this one (its IOMMU group), without bridges."""
    folder = os.path.join(sys_root, 'bus/pci/devices', slot, 'iommu_group/devices')
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [n for n in names if PCI_RE.match(n)
            and not _read_sys(os.path.join(sys_root, 'bus/pci/devices', n, 'class')).startswith('0x0604')]


def graphics_cards(sys_root: str = SYS) -> List[Dict[str, Any]]:
    """Graphics cards with whether a machine can have them, and why not."""
    out = []
    base = os.path.join(sys_root, 'bus/pci/devices')
    on = iommu_on(sys_root)
    for slot in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        dev = os.path.join(base, slot)
        if not PCI_RE.match(slot) or not _read_sys(os.path.join(dev, 'class')).startswith('0x03'):
            continue
        link = os.path.join(dev, 'driver')
        driver = os.path.basename(os.path.realpath(link)) if os.path.islink(link) else ''
        card = {'slot': slot, 'vendor': _read_sys(os.path.join(dev, 'vendor')), 'driver': driver,
                'screen': _read_sys(os.path.join(dev, 'boot_vga')) == '1', 'members': group_members(slot, sys_root)}
        if not on:
            card['why_not'] = ('IOMMU is off: turn on VT-d (Intel) or AMD-Vi/IOMMU in the BIOS. Intel machines may '
                               'also need intel_iommu=on when the NAS starts.')
        elif card['screen']:
            card['why_not'] = 'This is the card the NAS itself shows its screen on.'
        elif not card['members']:
            card['why_not'] = 'This card has no IOMMU group.'
        else:
            card['why_not'] = ''
        out.append(card)
    return out


def bind_vfio(members: List[str], sys_root: str = SYS) -> None:
    subprocess.run(['/usr/sbin/modprobe', 'vfio-pci'], capture_output=True, timeout=30, env={'LC_ALL': 'C'})
    for slot in members:
        dev = os.path.join(sys_root, 'bus/pci/devices', slot)
        _write_sys(os.path.join(dev, 'driver_override'), 'vfio-pci')
        link = os.path.join(dev, 'driver')
        if os.path.islink(link) and os.path.basename(os.path.realpath(link)) != 'vfio-pci':
            _write_sys(os.path.join(link, 'unbind'), slot)
        if not os.path.islink(link):
            _write_sys(os.path.join(sys_root, 'bus/pci/drivers_probe'), slot)


def unbind_vfio(members: List[str], sys_root: str = SYS) -> None:
    """Give the devices back to their own drivers."""
    for slot in members:
        dev = os.path.join(sys_root, 'bus/pci/devices', slot)
        link = os.path.join(dev, 'driver')
        try:
            if os.path.islink(link) and os.path.basename(os.path.realpath(link)) == 'vfio-pci':
                _write_sys(os.path.join(link, 'unbind'), slot)
            _write_sys(os.path.join(dev, 'driver_override'), '\n')
            _write_sys(os.path.join(sys_root, 'bus/pci/drivers_probe'), slot)
        except OSError:
            pass


def _vm_uid() -> int:
    import pwd
    return pwd.getpwnam(VM_USER).pw_uid


def _give(path: str, uid: int) -> None:
    os.chown(path, uid, -1)
    os.chmod(path, 0o600)


def _ip(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([IP, *args], capture_output=True, text=True, timeout=30, env={'LC_ALL': 'C'})


def devices_ready(cfg: Dict[str, Any], sys_root: str = SYS, dev_root: str = DEV,
                  uid: Optional[int] = None) -> None:
    """As root, before a start: hand the machine its devices."""
    if not (cfg.get('usb') or cfg.get('gpu') or cfg.get('network') == 'bridge'):
        return
    uid = _vm_uid() if uid is None else uid
    present = usb_devices(sys_root)
    for want in cfg.get('usb') or []:
        for d in present:
            if (d['vendor'], d['product']) == (want['vendor'], want['product']) and d['bus'].isdigit() \
                    and d['dev'].isdigit():
                _give(os.path.join(dev_root, 'bus/usb', f'{int(d["bus"]):03d}', f'{int(d["dev"]):03d}'), uid)
    if cfg.get('gpu'):
        card = next((c for c in graphics_cards(sys_root) if c['slot'] == cfg['gpu']), None)
        if not card:
            raise VmError('Its graphics card is not in the NAS any more. Choose none in its settings.')
        if card['why_not']:
            raise VmError(card['why_not'])
        bind_vfio(card['members'], sys_root)
        group = os.path.basename(os.path.realpath(os.path.join(sys_root, 'bus/pci/devices', cfg['gpu'],
                                                               'iommu_group')))
        node = os.path.join(dev_root, 'vfio', group)
        for _ in range(30):
            if os.path.exists(node):
                break
            time.sleep(0.1)
        _give(node, uid)
    if cfg.get('network') == 'bridge':
        name = tap_name(cfg['id'])
        _ip('link', 'delete', name)                     # left over from a crash
        res = _ip('link', 'add', 'link', default_interface(), 'name', name, 'type', 'macvtap', 'mode', 'bridge')
        if res.returncode != 0:
            raise VmError(f'Its network port could not be made: {res.stderr.strip()[:200]}')
        _ip('link', 'set', name, 'address', mac_address(cfg['id']), 'up')
        index = _read_sys(os.path.join(sys_root, 'class/net', name, 'ifindex'))
        node = os.path.join(dev_root, f'tap{index}')
        for _ in range(30):
            if os.path.exists(node):
                break
            time.sleep(0.1)
        _give(node, uid)


def devices_back(cfg: Dict[str, Any], sys_root: str = SYS) -> None:
    """As root, after it stopped: the network port goes, the card goes back."""
    if cfg.get('network') == 'bridge':
        _ip('link', 'delete', tap_name(cfg['id']))
    if cfg.get('gpu'):
        unbind_vfio(group_members(cfg['gpu'], sys_root), sys_root)


# ── Talking to a running machine (QMP) ───────────────────────────────────────

def qmp(path: str, command: str, timeout: float = 5.0) -> Dict[str, Any]:
    """One QMP command; returns its answer. Raises VmError when it is not running."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        f = s.makefile('rw', encoding='utf-8')

        def read() -> Dict[str, Any]:
            while True:
                line = f.readline()
                if not line:
                    raise VmError('The virtual machine is not running.')
                msg = json.loads(line)
                if 'event' not in msg:
                    return msg
        read()                                   # greeting
        for cmd in ('qmp_capabilities', command):
            f.write(json.dumps({'execute': cmd}) + '\n')
            f.flush()
            answer = read()
        return answer
    except (OSError, ValueError):
        raise VmError('The virtual machine is not running.') from None
    finally:
        s.close()


def stop_vm(vm_id: str, run_dir: str = RUN_DIR, grace: float = GRACE_SECONDS,
            sleep: Callable[[float], None] = time.sleep) -> str:
    """Ask the guest to shut down like a press on the power button; switch it off
    after `grace` seconds. Returns 'shutdown', 'forced' or 'not running'."""
    sock = os.path.join(run_dir, f'{vm_id}.qmp')
    try:
        qmp(sock, 'system_powerdown')
    except VmError:
        return 'not running'
    waited = 0.0
    while waited < grace:
        sleep(1)
        waited += 1
        try:
            qmp(sock, 'query-status', timeout=2)
        except VmError:
            return 'shutdown'
    try:
        qmp(sock, 'quit')
    except VmError:
        return 'shutdown'
    return 'forced'


# ── Run (from the systemd unit) ──────────────────────────────────────────────

def run_vm(vm_id: str, state_file: str = STATE_FILE, config_dir: str = CONFIG_DIR, root: str = DATA_ROOT,
           run_dir: str = RUN_DIR) -> int:
    cfg = load_config(vm_id, config_dir, root, state_file)
    store = read_store(state_file, root)
    base = vm_dir(store, vm_id)
    if not os.path.isfile(os.path.join(base, 'disk.qcow2')):
        raise VmError('The disk of this virtual machine is missing.')
    if not os.path.exists('/dev/kvm'):
        raise VmError('This NAS cannot run virtual machines: no KVM (is virtualization on in the BIOS?).')
    for iso in (cfg['iso'], cfg['iso2']):
        if iso and not os.path.isfile(iso):
            raise VmError(f'The image {os.path.basename(iso)} is not there any more. Choose another or eject it.')
    os.makedirs(run_dir, exist_ok=True)
    for ext in ('qmp', 'tpm'):
        try:
            os.remove(os.path.join(run_dir, f'{vm_id}.{ext}'))   # left over from a crash
        except OSError:
            pass
    tpm = None
    if OS_TYPES[cfg['os']]['tpm']:
        os.makedirs(os.path.join(base, 'tpm'), exist_ok=True)
        tpm = subprocess.Popen(swtpm_args(cfg, store, run_dir))
        for _ in range(50):
            if os.path.exists(os.path.join(run_dir, f'{vm_id}.tpm')):
                break
            time.sleep(0.1)
    tap_fd = None
    if cfg['network'] == 'bridge':
        index = _read_sys(os.path.join(SYS, 'class/net', tap_name(vm_id), 'ifindex'))
        tap_fd = os.open(os.path.join(DEV, f'tap{index}'), os.O_RDWR)
    vfio = group_members(cfg['gpu']) if cfg['gpu'] else []
    argv = qemu_args(cfg, store, run_dir, tap_fd=tap_fd, vfio=vfio)
    if cfg['priority'] == 'low':
        # Lower priority for processor and disks: the NAS's own work comes first.
        argv = [IONICE, '-c', '2', '-n', '7', '--'] + argv
    qemu = subprocess.Popen(argv, pass_fds=(tap_fd,) if tap_fd is not None else (),
                            preexec_fn=(lambda: os.nice(10)) if cfg['priority'] == 'low' else None)
    signal.signal(signal.SIGTERM, lambda *_: qemu.terminate())
    try:
        return qemu.wait()
    finally:
        if tpm:
            tpm.terminate()


# ── As root, through alvaos-priv ─────────────────────────────────────────────

def _chown_tree(path: str) -> None:
    import grp
    import pwd
    uid, gid = pwd.getpwnam(VM_USER).pw_uid, grp.getgrnam(VM_GROUP).gr_gid
    for here, dirs, files in os.walk(path):
        for name in [''] + dirs + files:
            os.lchown(os.path.join(here, name) if name else here, uid, gid)


def prepare(vm_id: str, state_file: str = STATE_FILE, config_dir: str = CONFIG_DIR, root: str = DATA_ROOT,
            chown: Callable[[str], None] = _chown_tree) -> Dict[str, Any]:
    """The folder, the disk and the UEFI variables of a new virtual machine."""
    cfg = load_config(vm_id, config_dir, root, state_file)
    store = read_store(state_file, root)
    base = vm_dir(store, vm_id)
    _let_in(store)
    if os.path.lexists(base):
        raise VmError('That virtual machine has files already.')
    os.mkdir(base, 0o750)
    try:
        _make_disk(os.path.join(base, 'disk.qcow2'), cfg['disk_gb'])
        if cfg['data_gb']:
            _make_disk(os.path.join(base, 'data.qcow2'), cfg['data_gb'])
        if OS_TYPES[cfg['os']]['uefi']:
            shutil.copyfile(find_firmware(OS_TYPES[cfg['os']]['secure'])[1], os.path.join(base, 'OVMF_VARS.fd'))
        os.chmod(os.path.join(base, 'disk.qcow2'), 0o660)
        chown(base)
    except BaseException:
        shutil.rmtree(base, ignore_errors=True)
        raise
    return {'folder': base}


def _make_disk(path: str, gb: int) -> None:
    res = subprocess.run([QEMU_IMG, 'create', '-f', 'qcow2', path, f'{gb}G'], capture_output=True, text=True,
                         timeout=120, env={'LC_ALL': 'C'})
    if res.returncode != 0:
        raise VmError(f'The disk could not be made: {(res.stderr or res.stdout).strip()[:200]}')
    os.chmod(path, 0o660)


def disk_size(path: str) -> int:
    """The size a disk file has for the guest, in bytes."""
    res = subprocess.run([QEMU_IMG, 'info', '--output=json', '-f', 'qcow2', path], capture_output=True, text=True,
                         timeout=60, env={'LC_ALL': 'C'})
    try:
        return int(json.loads(res.stdout)['virtual-size'])
    except (ValueError, KeyError, TypeError):
        raise VmError('The disk could not be read.') from None


def grow(vm_id: str, state_file: str = STATE_FILE, config_dir: str = CONFIG_DIR, root: str = DATA_ROOT,
         is_active: Optional[Callable[[str], bool]] = None, size_of: Callable[[str], int] = disk_size,
         chown: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Make a stopped machine's disks as large as its description says, and
    its second disk when it has none yet. Only larger: shrinking would cut
    off what the guest keeps at the end."""
    cfg = load_config(vm_id, config_dir, root, state_file)
    if (is_active or _unit_active)(vm_id):
        raise VmError('Shut the virtual machine down first.')
    base = vm_dir(read_store(state_file, root), vm_id)
    for name, gb in (('disk.qcow2', cfg['disk_gb']), ('data.qcow2', cfg['data_gb'])):
        disk = os.path.join(base, name)
        if name == 'data.qcow2' and not os.path.lexists(disk):
            if gb:
                _make_disk(disk, gb)
                (chown or _chown_tree)(disk)
            continue
        if os.path.islink(disk) or not os.path.isfile(disk):
            raise VmError('The disk of this virtual machine is not there.')
        want = gb * 2**30
        have = size_of(disk)
        if want < have:
            raise VmError('A disk can only grow, not shrink.')
        if want > have:
            res = subprocess.run([QEMU_IMG, 'resize', '-f', 'qcow2', disk, str(want)], capture_output=True,
                                 text=True, timeout=300, env={'LC_ALL': 'C'})
            if res.returncode != 0:
                raise VmError(f'The disk could not grow: {(res.stderr or res.stdout).strip()[:200]}')
    return {'disk_gb': cfg['disk_gb'], 'data_gb': cfg['data_gb']}


def _let_in(path: str) -> None:
    """`alvaos-vm` has to pass through the VMs folder to reach a machine's own."""
    mode = os.stat(path).st_mode
    if not mode & 0o001:
        os.chmod(path, (mode & 0o7777) | 0o001)


def ready(vm_id: str, state_file: str = STATE_FILE, config_dir: str = CONFIG_DIR, root: str = DATA_ROOT) -> None:
    """Before a start, as root: `alvaos-vm` can open the machine's folder and
    read the installer image (files copied in often have no "other" rights)."""
    cfg = load_config(vm_id, config_dir, root, state_file)
    store = read_store(state_file, root)
    _let_in(store)
    isos = os.path.join(store, ISO_FOLDER)
    if os.path.isdir(isos) and not os.path.islink(isos):
        _let_in(isos)
    for iso in (cfg['iso'], cfg['iso2']):
        if iso and os.path.isfile(iso) and not os.path.islink(iso):
            mode = os.stat(iso).st_mode
            if not mode & 0o004:
                os.chmod(iso, (mode & 0o7777) | 0o004)
    devices_ready(cfg)


def cleanup(vm_id: str, state_file: str = STATE_FILE, config_dir: str = CONFIG_DIR, root: str = DATA_ROOT) -> None:
    """After a stop, as root: undo what `ready` did for its devices."""
    devices_back(load_config(vm_id, config_dir, root, state_file))


def setup_store(state_file: str = STATE_FILE, root: str = DATA_ROOT) -> Dict[str, Any]:
    """The VMs folder after it was made: "ISOs" in it, and the way in for `alvaos-vm`."""
    store = read_store(state_file, root)
    _let_in(store)
    isos = os.path.join(store, ISO_FOLDER)
    if not os.path.lexists(isos):
        os.mkdir(isos, 0o755)
    elif os.path.islink(isos) or not os.path.isdir(isos):
        raise VmError('"ISOs" in the VMs folder is not a folder.')
    _let_in(isos)
    return {'store': store}


def delete(vm_id: str, state_file: str = STATE_FILE, root: str = DATA_ROOT,
           is_active: Optional[Callable[[str], bool]] = None) -> Dict[str, Any]:
    """Remove a virtual machine's folder with its disk, for good."""
    if not ID_RE.match(vm_id):
        raise VmError('That is not a virtual machine.')
    store = read_store(state_file, root)
    base = vm_dir(store, vm_id)
    check = is_active or _unit_active
    if check(vm_id):
        raise VmError('Shut the virtual machine down first.')
    if os.path.islink(base):
        raise VmError('That folder is not what it should be.')
    if os.path.isdir(base):
        shutil.rmtree(base)
    return {'removed': vm_id}


def _unit_active(vm_id: str) -> bool:
    res = subprocess.run([SYSTEMCTL, 'is-active', UNIT_FMT.format(vm_id)], capture_output=True, text=True,
                         timeout=15, env={'LC_ALL': 'C'})
    return res.stdout.strip() in ('active', 'activating', 'deactivating')


def list_isos(state_file: str = STATE_FILE, root: str = DATA_ROOT, limit: int = 300) -> Dict[str, Any]:
    """Installer images: the .iso files in the "ISOs" folder of the VMs folder
    (and one level of folders in it)."""
    found: List[Dict[str, Any]] = []
    try:
        isos = os.path.join(read_store(state_file, root), ISO_FOLDER)
    except VmError:
        return {'isos': found}

    def walk(folder: str, depth: int) -> None:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return
        for name in names:
            path = os.path.join(folder, name)
            if name.startswith('.') or os.path.islink(path) or len(found) >= limit:
                continue
            if os.path.isdir(path) and depth < 2:
                walk(path, depth + 1)
            elif name.lower().endswith('.iso') and os.path.isfile(path):
                found.append({'path': path, 'name': name, 'size_bytes': os.path.getsize(path),
                              'where': os.path.relpath(folder, isos)})
    walk(isos, 1)
    return {'isos': found}


HELPER_OPS = {'vm-prepare', 'vm-grow', 'vm-delete', 'vm-isos', 'vm-setup'}


def helper_main(argv: List[str]) -> int:
    """The `vm-*` operations of alvaos-priv; JSON on stdout."""
    try:
        if argv[:1] == ['vm-isos'] and len(argv) == 1:
            result = list_isos()
        elif argv[:1] == ['vm-setup'] and len(argv) == 1:
            result = setup_store()
        elif argv[:1] == ['vm-prepare'] and len(argv) == 2:
            result = prepare(argv[1])
        elif argv[:1] == ['vm-grow'] and len(argv) == 2:
            result = grow(argv[1])
        elif argv[:1] == ['vm-delete'] and len(argv) == 2:
            result = delete(argv[1])
        else:
            raise VmError('usage: vm-setup | vm-prepare ID | vm-grow ID | vm-delete ID | vm-isos')
    except (VmError, OSError) as exc:
        print(f'alvaos-priv: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


def main(argv: List[str]) -> int:
    """The systemd unit: `ready ID` (as root), `run ID` and `stop ID`."""
    try:
        if len(argv) == 2 and argv[0] == 'run':
            return run_vm(argv[1])
        if len(argv) == 2 and argv[0] == 'ready':
            ready(argv[1])
            return 0
        if len(argv) == 2 and argv[0] == 'stop':
            stop_vm(argv[1])
            return 0
        if len(argv) == 2 and argv[0] == 'cleanup':
            cleanup(argv[1])
            return 0
        raise VmError('usage: vm_ops.py ready ID | run ID | stop ID | cleanup ID')
    except VmError as exc:
        print(f'alvaos-vm: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
