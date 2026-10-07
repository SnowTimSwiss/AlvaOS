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
RESERVED_PORTS = {22, 25, 80, 111, 139, 443, 445, 2049, 5353, 8080, 8085, 8090, 8091, 8443, 9443, 9444, 9445,
                  51820}


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
    return {
        'id': vm_id, 'name': name, 'os': kind,
        'cpus': _int(raw.get('cpus'), 1, 64, 'The number of processor cores'),
        'memory_mb': _int(raw.get('memory_mb'), 256, 1024 * 1024, 'The memory'),
        'disk_gb': _int(raw.get('disk_gb'), 1, 65536, 'The disk size'),
        'iso': iso, 'autostart': bool(raw.get('autostart')),
        'slot': _int(raw.get('slot', 0), 0, 99, 'The screen number'),
        'ports': ports,
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
              kvm: bool = True) -> List[str]:
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
    disk = os.path.join(base, 'disk.qcow2')
    args += ['-drive', f'file={disk},if=none,id=disk0,format=qcow2,cache=none,discard=unmap']
    if kind['disk'] == 'virtio':
        args += ['-device', 'virtio-blk-pci,drive=disk0,bootindex=2']
    else:
        args += ['-device', 'ich9-ahci,id=ahci', '-device', 'ide-hd,drive=disk0,bus=ahci.0,bootindex=2']
    if cfg['iso']:
        args += ['-drive', f'file={cfg["iso"]},if=none,id=cd0,media=cdrom,readonly=on',
                 '-device', 'ich9-ahci,id=ahci1', '-device', 'ide-cd,drive=cd0,bus=ahci1.0,bootindex=1']
    forwards = ''.join(f',hostfwd={p["proto"]}::{p["host"]}-:{p["guest"]}' for p in cfg['ports'])
    args += ['-netdev', f'user,id=net0{forwards}', '-device', f'{kind["nic"]},netdev=net0',
             '-device', 'virtio-rng-pci', '-vga', 'std',
             '-device', 'qemu-xhci', '-device', 'usb-tablet', '-device', 'usb-kbd',
             '-vnc', f'127.0.0.1:{slot},websocket={VNC_WEBSOCKET_BASE + slot}',
             '-qmp', f'unix:{os.path.join(run_dir, cfg["id"] + ".qmp")},server=on,wait=off']
    if kind['tpm']:
        args += ['-chardev', f'socket,id=chrtpm,path={os.path.join(run_dir, cfg["id"] + ".tpm")}',
                 '-tpmdev', 'emulator,id=tpm0,chardev=chrtpm', '-device', 'tpm-tis,tpmdev=tpm0']
    return args


def swtpm_args(cfg: Dict[str, Any], store: str, run_dir: str = RUN_DIR) -> List[str]:
    state = os.path.join(vm_dir(store, cfg['id']), 'tpm')
    return [SWTPM, 'socket', '--tpm2', '--tpmstate', f'dir={state}',
            '--ctrl', f'type=unixio,path={os.path.join(run_dir, cfg["id"] + ".tpm")}']


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
    if cfg['iso'] and not os.path.isfile(cfg['iso']):
        raise VmError('The installer image is not there any more. Choose another or eject it.')
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
    qemu = subprocess.Popen(qemu_args(cfg, store, run_dir))
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
        res = subprocess.run([QEMU_IMG, 'create', '-f', 'qcow2', os.path.join(base, 'disk.qcow2'),
                              f'{cfg["disk_gb"]}G'], capture_output=True, text=True, timeout=120,
                             env={'LC_ALL': 'C'})
        if res.returncode != 0:
            raise VmError(f'The disk could not be made: {(res.stderr or res.stdout).strip()[:200]}')
        if OS_TYPES[cfg['os']]['uefi']:
            shutil.copyfile(find_firmware(OS_TYPES[cfg['os']]['secure'])[1], os.path.join(base, 'OVMF_VARS.fd'))
        os.chmod(os.path.join(base, 'disk.qcow2'), 0o660)
        chown(base)
    except BaseException:
        shutil.rmtree(base, ignore_errors=True)
        raise
    return {'folder': base}


def disk_size(path: str) -> int:
    """The size a disk file has for the guest, in bytes."""
    res = subprocess.run([QEMU_IMG, 'info', '--output=json', '-f', 'qcow2', path], capture_output=True, text=True,
                         timeout=60, env={'LC_ALL': 'C'})
    try:
        return int(json.loads(res.stdout)['virtual-size'])
    except (ValueError, KeyError, TypeError):
        raise VmError('The disk could not be read.') from None


def grow(vm_id: str, state_file: str = STATE_FILE, config_dir: str = CONFIG_DIR, root: str = DATA_ROOT,
         is_active: Optional[Callable[[str], bool]] = None, size_of: Callable[[str], int] = disk_size
         ) -> Dict[str, Any]:
    """Make a stopped machine's disk as large as its description says. Only
    larger: shrinking would cut off what the guest keeps at the end."""
    cfg = load_config(vm_id, config_dir, root, state_file)
    if (is_active or _unit_active)(vm_id):
        raise VmError('Shut the virtual machine down first.')
    disk = os.path.join(vm_dir(read_store(state_file, root), vm_id), 'disk.qcow2')
    if os.path.islink(disk) or not os.path.isfile(disk):
        raise VmError('The disk of this virtual machine is not there.')
    want = cfg['disk_gb'] * 2**30
    have = size_of(disk)
    if want < have:
        raise VmError('A disk can only grow, not shrink.')
    if want > have:
        res = subprocess.run([QEMU_IMG, 'resize', '-f', 'qcow2', disk, str(want)], capture_output=True, text=True,
                             timeout=300, env={'LC_ALL': 'C'})
        if res.returncode != 0:
            raise VmError(f'The disk could not grow: {(res.stderr or res.stdout).strip()[:200]}')
    return {'disk_gb': cfg['disk_gb']}


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
    if cfg['iso'] and os.path.isfile(cfg['iso']) and not os.path.islink(cfg['iso']):
        mode = os.stat(cfg['iso']).st_mode
        if not mode & 0o004:
            os.chmod(cfg['iso'], (mode & 0o7777) | 0o004)


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
        raise VmError('usage: vm_ops.py run ID | stop ID')
    except VmError as exc:
        print(f'alvaos-vm: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
