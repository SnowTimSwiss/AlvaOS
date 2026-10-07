#!/usr/bin/env python3
"""Virtual machines (Virtual machines page): what the page shows and does.

The machines themselves are QEMU/KVM processes run by systemd
(`alvaos-vm@ID.service`, see vm_ops.py and docs/VMS.md). This module keeps
their descriptions (`/var/lib/alvaos/vms/ID.json`), says what the NAS can do
(processor, memory, packages), sets virtual machines up on request and starts,
stops and removes machines. Everything that needs root goes through the
privilege helper: systemctl for a machine's unit, and `vm-prepare`,
`vm-delete`, `vm-isos`, `vm-setup` for its files.

Setting up installs the packages (QEMU, UEFI firmware, a TPM for Windows 11)
the first time and makes the "VMs" shared folder where the disks live, so
restore points, backups and space limits work for them like for any folder.
"""

import json
import os
import platform
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import files_manager
import gpu_manager
import vm_console
import vm_ops
from common import CMD

PACKAGES = ['qemu-system-x86', 'qemu-utils', 'ovmf', 'swtpm', 'swtpm-tools']
STORE_SHARE = 'VMs'
ACCOUNT = vm_ops.VM_USER
STATES = {'active': 'running', 'activating': 'starting', 'deactivating': 'stopping', 'failed': 'failed'}
MEMORY_OVERHEAD_MB = 512      # what QEMU itself needs next to the guest's memory
KEEP_FREE_MB = 1024           # what the NAS keeps for itself

_lock = threading.Lock()
_job: Dict[str, Any] = {'running': False, 'started_at': '', 'finished_at': '', 'error': ''}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def host_info(cpuinfo: str = '/proc/cpuinfo', kvm: str = '/dev/kvm') -> Dict[str, Any]:
    """What this machine can do for virtual machines."""
    flags = ''
    try:
        with open(cpuinfo) as f:
            for line in f:
                if line.startswith('flags'):
                    flags = line
                    break
    except OSError:
        pass
    try:
        import psutil
        memory = psutil.virtual_memory()
        total, available = int(memory.total // 2**20), int(memory.available // 2**20)
    except Exception:  # noqa: BLE001 - psutil missing or broken
        total = available = 0
    arch = platform.machine()
    cpu_ok = bool(re.search(r'\b(vmx|svm)\b', flags))
    has_kvm = os.path.exists(kvm)
    reason = ''
    if arch not in ('x86_64', 'AMD64'):
        reason = 'Virtual machines need an Intel or AMD processor (64 bit).'
    elif not cpu_ok and not has_kvm:
        reason = ('This processor cannot run virtual machines, or "virtualization" (VT-x, AMD-V) is switched off '
                  'in the BIOS/UEFI of the NAS.')
    return {'arch': arch, 'cpus': os.cpu_count() or 1, 'memory_mb': total, 'available_mb': available,
            'virtualization': cpu_ok, 'kvm': has_kvm, 'supported': not reason, 'reason': reason}


def account_exists(name: str = ACCOUNT) -> bool:
    try:
        import pwd
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _unit(vm_id: str) -> str:
    return vm_ops.UNIT_FMT.format(vm_id)


def _systemctl_state(vm_id: str) -> str:
    """`systemctl is-active`, which needs no rights."""
    import subprocess
    try:
        res = subprocess.run([CMD['SYSTEMCTL'], 'is-active', _unit(vm_id)], capture_output=True, text=True,
                             timeout=10, env={'LC_ALL': 'C'})
    except (OSError, subprocess.SubprocessError):
        return ''
    return res.stdout.strip()


def _blocker(host: Dict[str, Any], account: bool) -> str:
    """What stops machines from running although everything is installed."""
    if not account:
        return 'The account for virtual machines is missing. Update AlvaOS and try again.'
    if not host['kvm']:
        return 'KVM is not available: switch "virtualization" on in the BIOS/UEFI of the NAS and restart it.'
    return ''


class VmManager:
    def __init__(self, run_command: Callable, state_file: str = vm_ops.STATE_FILE,
                 config_dir: str = vm_ops.CONFIG_DIR, root: str = vm_ops.DATA_ROOT,
                 helper: Optional[Callable] = None, unit_state: Optional[Callable[[str], str]] = None,
                 installed: Optional[Callable[[List[str]], Dict[str, bool]]] = None,
                 host: Optional[Callable[[], Dict[str, Any]]] = None, account: Optional[Callable[[], bool]] = None,
                 free_bytes: Optional[Callable[[str], int]] = None):
        self.run = run_command
        self.state_file = state_file
        self.config_dir = config_dir
        self.root = root
        self.helper = helper or (lambda args, timeout=300: files_manager.run_helper(args, timeout=timeout))
        self.unit_state = unit_state or _systemctl_state
        self.installed = installed or gpu_manager._installed
        self.host = host or host_info
        self.account = account or account_exists
        self.free_bytes = free_bytes or (lambda path: shutil.disk_usage(path).free)

    # ── Files ────────────────────────────────────────────────────────────────

    def store(self) -> str:
        try:
            return vm_ops.read_store(self.state_file, self.root)
        except vm_ops.VmError:
            return ''

    def _read_configs(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            names = sorted(os.listdir(self.config_dir))
        except OSError:
            return out
        store = self.store() or None
        for name in names:
            if not name.endswith('.json'):
                continue
            try:
                with open(os.path.join(self.config_dir, name)) as f:
                    out.append(vm_ops.check_config(json.load(f), self.root, store))
            except (OSError, ValueError, vm_ops.VmError):
                continue   # a damaged description is left alone, not shown
        return sorted(out, key=lambda c: (c['name'].lower(), c['id']))

    def _write_config(self, cfg: Dict[str, Any]) -> None:
        os.makedirs(self.config_dir, mode=0o750, exist_ok=True)
        path = os.path.join(self.config_dir, f'{cfg["id"]}.json')
        tmp = f'{path}.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, 'w') as f:
            json.dump(cfg, f, indent=1)
        os.replace(tmp, path)

    def _find(self, vm_id: str) -> Optional[Dict[str, Any]]:
        return next((c for c in self._read_configs() if c['id'] == vm_id), None)

    def _write_state(self, store: str) -> None:
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        tmp = f'{self.state_file}.tmp'
        with open(tmp, 'w') as f:
            json.dump({'store': store}, f)
        os.replace(tmp, self.state_file)

    # ── What the page shows ──────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        host = self.host()
        have = self.installed(PACKAGES)
        missing = [p for p in PACKAGES if not have.get(p)]
        store = self.store()
        account = self.account()
        with _lock:
            job = dict(_job)
        ready = host['supported'] and not missing and account and bool(store)
        problem = '' if not host['supported'] or missing or not store else _blocker(host, account)
        return {
            'host': host, 'missing_packages': missing, 'account': account, 'store': store, 'ready': ready,
            'problem': problem, 'job': job, 'os_types': [{'id': k, **{x: v[x] for x in ('name', 'tpm')}}
                                                         for k, v in vm_ops.OS_TYPES.items()],
            'limits': {'cpus': host['cpus'], 'memory_mb': max(256, host['memory_mb'] - KEEP_FREE_MB)},
            'vms': [self._describe(c) for c in self._read_configs()] if store else [],
        }

    def _describe(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        state = STATES.get(self.unit_state(cfg['id']), 'stopped')
        out = {**cfg, 'os_name': vm_ops.OS_TYPES[cfg['os']]['name'], 'state': state,
               'iso_name': os.path.basename(cfg['iso']) if cfg['iso'] else '',
               'iso2_name': os.path.basename(cfg['iso2']) if cfg['iso2'] else '', 'problem': '',
               'disk_used_bytes': None}
        if state == 'failed':
            out['problem'] = self._problem(cfg['id'])
        try:
            st = os.stat(os.path.join(vm_ops.vm_dir(self.store(), cfg['id']), 'disk.qcow2'))
            out['disk_used_bytes'] = st.st_blocks * 512
        except OSError:
            pass
        return out

    def _problem(self, vm_id: str) -> str:
        """Why it stopped: the last complaint in the log."""
        res, _err = self.run([CMD['JOURNALCTL'], '-u', _unit(vm_id), '-n', '30', '--no-pager'], timeout=20)
        text = (res.stdout if res is not None else '') or ''
        lines = [ln for ln in text.splitlines() if 'alvaos-vm:' in ln or 'qemu-system' in ln]
        if not lines:
            return 'It stopped. Look at the log under Settings › Diagnostics.'
        last = lines[-1]
        return re.sub(r'^.*?(alvaos-vm:|qemu-system-x86_64:)\s*', '', last)[:300]

    # ── Setting up ───────────────────────────────────────────────────────────

    def setup(self, make_store: Callable[[], Tuple[str, str]]) -> Tuple[bool, str]:
        """The VMs folder first (quick), then the packages in the background."""
        host = self.host()
        if not host['supported']:
            return False, host['reason']
        if not self.account():
            return False, 'The account for virtual machines is missing. Update AlvaOS and try again.'
        with _lock:
            if _job['running']:
                return False, 'Virtual machines are being set up already.'
        if not self.store():
            path, error = make_store()
            if error:
                return False, error
            self._write_state(path)
        _result, error = self.helper(['vm-setup'], 60)
        if error:
            return False, error
        missing = [p for p in PACKAGES if not self.installed(PACKAGES).get(p)]
        if not missing:
            return True, 'Virtual machines are ready.'
        with _lock:
            _job.update({'running': True, 'started_at': _now(), 'finished_at': '', 'error': ''})
        threading.Thread(target=self._install, args=(missing,), name='vm-setup', daemon=True).start()
        return True, 'Installing what virtual machines need. This takes a few minutes.'

    def _install(self, packages: List[str]) -> None:
        error = ''
        try:
            _res, _err = self.run([CMD['APT_GET'], 'update'], timeout=600)
            res, err = self.run([CMD['APT_GET'], '-y', 'install', '--no-install-recommends'] + packages,
                                timeout=3600, extra_env={'DEBIAN_FRONTEND': 'noninteractive'})
            if err or not res or res.returncode != 0:
                error = gpu_manager._apt_problem(err or (res.stderr if res else '') or '')
        except Exception as e:  # noqa: BLE001 - report it on the page
            error = f'The installation stopped: {e}'
        with _lock:
            _job.update({'running': False, 'finished_at': _now(), 'error': error})

    # ── Machines ─────────────────────────────────────────────────────────────

    def _checked(self) -> str:
        """'' when machines can be made and run, else what is missing."""
        host = self.host()
        if not host['supported']:
            return host['reason']
        if any(not v for v in self.installed(PACKAGES).values()) or not self.store():
            return 'Set up virtual machines first.'
        return _blocker(host, self.account())

    def _free_slot(self, taken: List[int]) -> int:
        return next(i for i in range(100) if i not in taken)

    def create(self, data: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
        problem = self._checked()
        if problem:
            return None, problem
        store = self.store()
        configs = self._read_configs()
        if len(configs) >= 99:
            return None, 'That is a lot of virtual machines already.'
        host = self.host()
        raw = {**data, 'id': secrets.token_hex(4), 'slot': self._free_slot([c['slot'] for c in configs])}
        try:
            cfg = vm_ops.check_config(raw, self.root, store)
        except vm_ops.VmError as e:
            return None, str(e)
        if any(c['name'].lower() == cfg['name'].lower() for c in configs):
            return None, f'There is a virtual machine called "{cfg["name"]}" already.'
        if cfg['cpus'] > host['cpus']:
            return None, f'This NAS has {host["cpus"]} processor cores.'
        if cfg['memory_mb'] > max(256, host['memory_mb'] - KEEP_FREE_MB):
            return None, 'That is more memory than this NAS can give: it needs some for itself.'
        problem = self._device_problem(cfg)
        if problem:
            return None, problem
        try:
            free_gb = self.free_bytes(store) / 2**30
            if cfg['disk_gb'] + cfg['data_gb'] > free_gb:
                return None, f'The pool has {free_gb:.0f} GB free: a smaller disk, or free some space.'
        except OSError:
            pass
        self._write_config(cfg)
        _result, error = self.helper(['vm-prepare', cfg['id']], 300)
        if error:
            self._remove_config(cfg['id'])
            return None, error
        if cfg['autostart']:
            self.run([CMD['SYSTEMCTL'], 'enable', _unit(cfg['id'])], timeout=30)
        return self._describe(cfg), ''

    def update(self, vm_id: str, data: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
        """Change a stopped machine: name, cores, memory, disk size (only larger),
        installer image, ports, start with the NAS."""
        cfg = self._find(vm_id)
        if not cfg:
            return None, 'That virtual machine is not there.'
        if STATES.get(self.unit_state(vm_id), 'stopped') in ('running', 'starting', 'stopping'):
            return None, 'Shut it down first, then change it.'
        merged: Dict[str, Any] = dict(cfg)
        merged.update({k: data[k] for k in ('name', 'cpus', 'memory_mb', 'disk_gb', 'iso', 'ports', 'autostart',
                                            'data_gb', 'iso2', 'fast', 'network', 'usb', 'gpu', 'priority')
                       if k in data})
        try:
            new = vm_ops.check_config(merged, self.root, self.store())
        except vm_ops.VmError as e:
            return None, str(e)
        host = self.host()
        if new['cpus'] > host['cpus']:
            return None, f'This NAS has {host["cpus"]} processor cores.'
        if new['memory_mb'] > max(256, host['memory_mb'] - KEEP_FREE_MB):
            return None, 'That is more memory than this NAS can give: it needs some for itself.'
        if any(c['id'] != vm_id and c['name'].lower() == new['name'].lower() for c in self._read_configs()):
            return None, f'There is a virtual machine called "{new["name"]}" already.'
        if new['disk_gb'] < cfg['disk_gb']:
            return None, f'A disk can only grow. It has {cfg["disk_gb"]} GB.'
        if cfg['data_gb'] and new['data_gb'] < cfg['data_gb']:
            return None, (f'The second disk can only grow. It has {cfg["data_gb"]} GB.' if new['data_gb'] else
                          'A second disk is not removed here: its files would be gone.')
        problem = self._device_problem(new)
        if problem:
            return None, problem
        grown = (new['disk_gb'] - cfg['disk_gb']) + (new['data_gb'] - cfg['data_gb'])
        if grown > 0:
            try:
                free_gb = self.free_bytes(self.store()) / 2**30
                if grown > free_gb:
                    return None, f'The pool has {free_gb:.0f} GB free: grow it by less, or free some space.'
            except OSError:
                pass
        self._write_config(new)
        if grown > 0:
            _result, error = self.helper(['vm-grow', vm_id], 300)
            if error:
                self._write_config(cfg)
                return None, error
        if new['autostart'] != cfg['autostart']:
            self.run([CMD['SYSTEMCTL'], 'enable' if new['autostart'] else 'disable', _unit(vm_id)], timeout=30)
        return self._describe(new), ''

    def _device_problem(self, cfg: Dict[str, Any]) -> str:
        """'' when the NAS can give the machine the graphics card it asks for."""
        if not cfg['gpu']:
            return ''
        card = next((c for c in self.cards() if c['slot'] == cfg['gpu']), None)
        if not card:
            return 'That graphics card is not in this NAS.'
        return card['why_not']

    def devices(self) -> Dict[str, Any]:
        """What can be handed to a machine: USB devices and graphics cards."""
        return {'usb': vm_ops.usb_devices(), 'gpus': self.cards(), 'iommu': vm_ops.iommu_on()}

    def cards(self) -> List[Dict[str, Any]]:
        names = {c['slot']: f"{c['vendor_name']} {c['model']}" for c in gpu_manager.detect()}
        return [{**c, 'name': names.get(c['slot'], c['slot'])} for c in vm_ops.graphics_cards()]

    def _remove_config(self, vm_id: str) -> None:
        try:
            os.remove(os.path.join(self.config_dir, f'{vm_id}.json'))
        except OSError:
            pass

    def delete(self, vm_id: str) -> Tuple[bool, str]:
        cfg = self._find(vm_id)
        if not cfg:
            return False, 'That virtual machine is not there.'
        if STATES.get(self.unit_state(vm_id), 'stopped') in ('running', 'starting', 'stopping'):
            return False, 'Shut it down first.'
        _result, error = self.helper(['vm-delete', vm_id], 300)
        if error:
            return False, error
        self.run([CMD['SYSTEMCTL'], 'disable', _unit(vm_id)], timeout=30)
        self._remove_config(vm_id)
        return True, f'"{cfg["name"]}" and its disk are deleted.'

    def action(self, vm_id: str, what: str) -> Tuple[bool, str]:
        cfg = self._find(vm_id)
        if not cfg:
            return False, 'That virtual machine is not there.'
        state = STATES.get(self.unit_state(vm_id), 'stopped')
        unit = _unit(vm_id)
        if what == 'start':
            if state in ('running', 'starting'):
                return False, 'It is running already.'
            problem = self._checked() or self._memory_problem(cfg) or self._device_problem(cfg)
            if not problem and cfg['gpu']:
                other = next((c for c in self._read_configs() if c['id'] != vm_id and c['gpu'] == cfg['gpu']
                              and STATES.get(self.unit_state(c['id']), 'stopped') in ('running', 'starting')), None)
                if other:
                    problem = f'"{other["name"]}" has the graphics card now. Shut it down first.'
            if problem:
                return False, problem
            _res, error = self.run([CMD['SYSTEMCTL'], 'start', unit], timeout=60)
            return (False, error or 'It did not start.') if error else (True, f'"{cfg["name"]}" is starting.')
        if what in ('stop', 'restart'):
            if state not in ('running', 'starting'):
                return False, 'It is not running.'
            verb = 'stop' if what == 'stop' else 'restart'
            # Waits for the guest to shut down (up to two minutes): on the side.
            threading.Thread(target=self.run, args=([CMD['SYSTEMCTL'], verb, unit],), kwargs={'timeout': 240},
                             name=f'vm-{verb}', daemon=True).start()
            return True, (f'Asked "{cfg["name"]}" to shut down.' if what == 'stop'
                          else f'Restarting "{cfg["name"]}".')
        if what == 'force':
            if state not in ('running', 'starting', 'stopping'):
                return False, 'It is not running.'
            _res, error = self.run([CMD['SYSTEMCTL'], 'kill', '--signal=SIGKILL', unit], timeout=30)
            return (False, error) if error else (True, f'"{cfg["name"]}" is switched off.')
        return False, 'That is not something a virtual machine can do.'

    def _memory_problem(self, cfg: Dict[str, Any]) -> str:
        need = cfg['memory_mb'] + MEMORY_OVERHEAD_MB
        free = self.host()['available_mb']
        if free and need > free:
            return (f'Not enough free memory to start it now: it needs about {need} MB and {free} MB are free. '
                    'Shut another machine down or give it less memory.')
        return ''

    def isos(self) -> Tuple[List[Dict[str, Any]], str]:
        result, error = self.helper(['vm-isos'], 60)
        if result is None:
            return [], error or 'The installer images could not be listed.'
        return list(result.get('isos') or []), ''

    def console(self, vm_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """A ticket for the screen; the machine has to be running."""
        cfg = self._find(vm_id)
        if not cfg:
            return None, 'That virtual machine is not there.'
        if STATES.get(self.unit_state(vm_id), 'stopped') != 'running':
            return None, 'Start it first.'
        return {'ticket': vm_console.issue(vm_id, cfg['slot']), 'port': vm_console.PORT,
                'tls_port': vm_console.TLS_PORT, 'name': cfg['name']}, ''
