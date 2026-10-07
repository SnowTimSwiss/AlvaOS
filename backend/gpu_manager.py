#!/usr/bin/env python3
"""Graphics cards (Settings › Graphics): which there are, which driver runs
them, and the driver or firmware to install when one is missing.

What a graphics card is good for on a NAS: apps that convert video while it
plays (Jellyfin, Immich) and AI models (Ollama). Intel and AMD cards work
with the drivers in the kernel once their firmware is there; NVIDIA cards
need NVIDIA's own driver, which is built for the running kernel (DKMS) and
needs a restart.

Detection reads /sys and needs no privileges. Installing runs apt-get through
the privilege helper, with package names from the fixed lists below only.
"""

import glob
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from common import CMD

VENDORS = {'0x10de': 'nvidia', '0x1002': 'amd', '0x8086': 'intel'}
VENDOR_NAMES = {'nvidia': 'NVIDIA', 'amd': 'AMD', 'intel': 'Intel'}
PCI_IDS = ('/usr/share/misc/pci.ids', '/usr/share/hwdata/pci.ids', '/usr/share/pci.ids')

# What each vendor needs, in Debian trixie (checked in CI against the archive,
# scripts/ci/optional-packages.txt). Firmware first: without it the kernel
# driver starts the card without video acceleration, or not at all.
PACKAGES: Dict[str, List[str]] = {
    # In trixie the Intel and NVIDIA firmware is still in firmware-misc-nonfree.
    'intel': ['firmware-misc-nonfree', 'intel-media-va-driver-non-free', 'vainfo'],
    'amd': ['firmware-amd-graphics', 'mesa-va-drivers', 'vainfo'],
    # The container toolkit lets apps (Docker) use the card (docker --gpus).
    'nvidia': ['linux-headers-amd64', 'nvidia-driver', 'firmware-misc-nonfree', 'nvidia-container-toolkit'],
}
TOOLKIT = 'nvidia-container-toolkit'
# The toolkit is not in Debian: it comes from NVIDIA's own apt source, the
# one the privilege helper allows, signed with the key AlvaOS ships.
NVIDIA_SOURCE_FILE = '/etc/apt/sources.list.d/alvaos-nvidia-container-toolkit.list'
NVIDIA_SOURCE = ('# NVIDIA container toolkit, for apps on NVIDIA cards (AlvaOS Settings › Graphics)\n'
                 'deb [signed-by=/opt/alvaos/keys/nvidia-container-toolkit.asc] '
                 'https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /\n')
# Kernel drivers that make a card usable, per vendor. nouveau runs NVIDIA
# cards for a screen, but apps cannot use it to convert video or run models.
GOOD_DRIVERS = {'intel': {'i915', 'xe'}, 'amd': {'amdgpu'}, 'nvidia': {'nvidia'}}
RESTART_FLAG = '/var/lib/alvaos/gpu_restart_needed'
DPKG_QUERY = '/usr/bin/dpkg-query'   # reads the package list, runs without the helper
APT_CACHE = '/usr/bin/apt-cache'     # asks the package lists, runs without the helper

_lock = threading.Lock()
_job: Dict[str, Any] = {'running': False, 'vendor': '', 'started_at': '', 'finished_at': '', 'error': '',
                        'log': ''}


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def _pci_names(vendor_id: str, device_id: str, sources=PCI_IDS) -> Tuple[str, str]:
    """(vendor, device) names from the PCI database, or ('', '')."""
    want_vendor, want_device = vendor_id.lower().removeprefix('0x'), device_id.lower().removeprefix('0x')
    for path in sources:
        try:
            f = open(path, encoding='utf-8', errors='replace')
        except OSError:
            continue
        with f:
            vendor_name = ''
            for line in f:
                if line.startswith('#') or not line.strip():
                    continue
                if not line.startswith('\t'):
                    if vendor_name:
                        return vendor_name, ''   # the vendor's block ended without the device
                    if line[:4].lower() == want_vendor:
                        vendor_name = line[4:].strip()
                    continue
                if vendor_name and not line.startswith('\t\t') and line[1:5].lower() == want_device:
                    return vendor_name, line[5:].strip()
            if vendor_name:
                return vendor_name, ''
    return '', ''


def detect(sys_root: str = '/sys', pci_ids=PCI_IDS) -> List[Dict[str, Any]]:
    """Every graphics card (PCI class 03xx), also those without a driver."""
    cards = []
    for dev in sorted(glob.glob(os.path.join(sys_root, 'bus/pci/devices/*'))):
        if not _read(os.path.join(dev, 'class')).startswith('0x03'):
            continue
        vendor_id = _read(os.path.join(dev, 'vendor'))
        device_id = _read(os.path.join(dev, 'device'))
        vendor = VENDORS.get(vendor_id.lower(), 'other')
        driver_link = os.path.join(dev, 'driver')
        driver = os.path.basename(os.path.realpath(driver_link)) if os.path.islink(driver_link) else ''
        render = sorted(os.path.basename(p) for p in glob.glob(os.path.join(dev, 'drm/renderD*')))
        vendor_name, model = _pci_names(vendor_id, device_id, pci_ids)
        cards.append({
            'slot': os.path.basename(dev),
            'vendor': vendor,
            'vendor_name': VENDOR_NAMES.get(vendor) or vendor_name or 'Unknown maker',
            'model': model or f'Graphics card {device_id.removeprefix("0x")}',
            'driver': driver,
            'render_node': f'/dev/dri/{render[0]}' if render else '',
            'screen': _read(os.path.join(dev, 'boot_vga')) == '1',   # the one the console shows on
        })
    return cards


def secure_boot(sys_root: str = '/sys') -> bool:
    """True when the machine starts with Secure Boot on (NVIDIA's driver
    then needs a key confirmed once at the screen of the NAS)."""
    for path in glob.glob(os.path.join(sys_root, 'firmware/efi/efivars/SecureBoot-*')):
        try:
            with open(path, 'rb') as f:
                data = f.read()
            return bool(data) and data[-1] == 1
        except OSError:
            continue
    return False


class GpuManager:
    def __init__(self, run_command: Callable, installed: Optional[Callable[[List[str]], Dict[str, bool]]] = None,
                 sys_root: str = '/sys', restart_flag: Optional[str] = None, pci_ids=PCI_IDS,
                 available: Optional[Callable[[str], bool]] = None):
        self.run = run_command
        self.installed = installed or _installed
        self.available = available or _available
        self.sys_root = sys_root
        self.restart_flag = restart_flag or RESTART_FLAG
        self.pci_ids = pci_ids

    def status(self) -> Dict[str, Any]:
        cards = detect(self.sys_root, self.pci_ids)
        current_headers = f'linux-headers-{os.uname().release}'
        wanted = sorted({p for c in cards for p in PACKAGES.get(c['vendor'], [])}
                        | ({current_headers} if any(c['vendor'] == 'nvidia' for c in cards) else set()))
        have = self.installed(wanted) if wanted else {}
        restart = os.path.exists(self.restart_flag)
        for card in cards:
            needed = PACKAGES.get(card['vendor'], [])
            if card['vendor'] == 'nvidia':
                needed = list(needed) + [current_headers]
            card['missing'] = [p for p in needed if not have.get(p)]
            card['ready'] = card['driver'] in GOOD_DRIVERS.get(card['vendor'], set()) and bool(card['render_node']) \
                if card['vendor'] != 'nvidia' else card['driver'] == 'nvidia'
            card['state'], card['advice'] = _advice(card, restart)
        with _lock:
            job = dict(_job)
        return {'cards': cards, 'secure_boot': secure_boot(self.sys_root), 'restart_needed': restart, 'job': job}

    def clear_after_boot(self, boot_time: Optional[float] = None) -> None:
        """The "restart needed" note is done once the NAS started after it."""
        try:
            if boot_time is None:
                import psutil
                boot_time = psutil.boot_time()
            if os.path.getmtime(self.restart_flag) < boot_time:
                os.remove(self.restart_flag)
        except (OSError, ImportError):
            pass

    def install(self, vendor: str) -> Tuple[bool, str]:
        """Install the driver and firmware for one vendor's cards, in the background."""
        if vendor not in PACKAGES:
            return False, 'There is nothing to install for this card.'
        if not any(c['vendor'] == vendor for c in detect(self.sys_root, self.pci_ids)):
            return False, f'There is no {VENDOR_NAMES[vendor]} graphics card in this NAS.'
        with _lock:
            if _job['running']:
                return False, 'A driver is being installed already.'
            _job.update({'running': True, 'vendor': vendor, 'started_at': _now(), 'finished_at': '', 'error': '',
                         'log': ''})
        threading.Thread(target=self._install, args=(vendor,), name='gpu-install', daemon=True).start()
        return True, f'Installing the {VENDOR_NAMES[vendor]} driver. This takes a few minutes.'

    def repair(self) -> Tuple[bool, str]:
        """Finish package configuration left incomplete by an earlier install."""
        with _lock:
            if _job['running']:
                return False, 'A package operation is already running.'
            _job.update({'running': True, 'vendor': 'repair', 'started_at': _now(), 'finished_at': '',
                         'error': '', 'log': ''})
        threading.Thread(target=self._repair, name='gpu-package-repair', daemon=True).start()
        return True, 'Repairing the package setup. This can take a few minutes.'

    def _repair(self) -> None:
        error, output = '', ''
        try:
            res, err = self.run([CMD['DPKG'], '--configure', '-a'], timeout=1800,
                                extra_env={'DEBIAN_FRONTEND': 'noninteractive'})
            output = ((res.stdout if res else '') or '') + '\n' + ((res.stderr if res else '') or '')
            if err or not res or res.returncode != 0:
                error = _apt_problem(err or output)
        except Exception as e:  # noqa: BLE001
            error = f'The repair stopped: {e}'
        with _lock:
            _job.update({'running': False, 'finished_at': _now(), 'error': error, 'log': output[-4000:]})

    def _install(self, vendor: str) -> None:
        error, log = '', []
        try:
            if TOOLKIT in PACKAGES[vendor]:
                res, err = self.run([CMD['TEE'], NVIDIA_SOURCE_FILE], timeout=30, input=NVIDIA_SOURCE)
                if err or not res or res.returncode != 0:
                    raise RuntimeError('NVIDIA\'s package source could not be added.')
            res, err = self.run([CMD['APT_GET'], 'update'], timeout=600)
            log.append((res.stdout if res else '') or '')
            packages = list(PACKAGES[vendor])
            if vendor == 'nvidia':
                # DKMS must build for the kernel that is actually running.
                release = os.uname().release
                if not release or not all(c.isalnum() or c in '.+-_' for c in release):
                    raise ValueError('Could not safely determine the running kernel version.')
                headers = f'linux-headers-{release}'
                # After a kernel update Debian drops the old kernel's headers from
                # the archive: apt would fail the same way on every try.
                if not self.installed([headers]).get(headers) and not self.available(headers):
                    raise _NoHeaders(release)
                packages.insert(1, headers)
            res, err = self.run([CMD['APT_GET'], '-y', 'install'] + packages, timeout=3600,
                                extra_env={'DEBIAN_FRONTEND': 'noninteractive'})
            log.append(((res.stdout if res else '') or '') + ((res.stderr if res else '') or ''))
            if err or not res or res.returncode != 0:
                error = _apt_problem((err or '') + '\n' + ((res.stdout or '') + '\n' + (res.stderr or '') if res else ''))
            elif vendor == 'nvidia' and any(c['vendor'] == 'nvidia' and c['driver'] != 'nvidia'
                                            for c in detect(self.sys_root, self.pci_ids)):
                _touch(self.restart_flag)   # nouveau stays loaded until the next start
        except _NoHeaders as e:
            error = (f'The NVIDIA driver must be built for the running system (Linux {e}), but its build files '
                     '(kernel headers) are no longer offered. Install the system updates on the Updates page, '
                     'restart the NAS, then try again.')
        except Exception as e:  # noqa: BLE001 - report it on the page
            error = f'The installation stopped: {e}'
        with _lock:
            _job.update({'running': False, 'finished_at': _now(), 'error': error,
                         'log': '\n'.join(log)[-4000:]})


# ── A graphics card for an app (Apps › an app › Graphics card) ─────────────
#
# The catalog says which service of an app can use a card and which makers'
# cards it can use ("gpu": {"service", "vendors", "images", "kfd"}). Intel
# and AMD cards are handed in as /dev/dri with the groups that may open it;
# NVIDIA cards through Docker's GPU request (needs the container toolkit).

def _group_ids(path: str = '/etc/group') -> Dict[str, int]:
    out = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split(':')
                if len(parts) >= 3 and parts[2].isdigit():
                    out[parts[0]] = int(parts[2])
    except OSError:
        pass
    return out


def _usable(card: Dict[str, Any], have: Dict[str, bool]) -> bool:
    if card['vendor'] == 'nvidia':
        return card['driver'] == 'nvidia' and have.get(TOOLKIT, False)
    return card['driver'] in GOOD_DRIVERS.get(card['vendor'], set()) and bool(card['render_node'])


def app_plan(spec: Any, cards: Optional[List[Dict[str, Any]]] = None, groups: Optional[Dict[str, int]] = None,
             installed: Optional[Callable[[List[str]], Dict[str, bool]]] = None
             ) -> Tuple[Optional[Dict[str, Any]], str]:
    """(how the app gets a card, or None and why not)."""
    if not isinstance(spec, dict) or not isinstance(spec.get('service'), str):
        return None, 'This app cannot use a graphics card.'
    vendors = [v for v in spec.get('vendors') or [] if v in GOOD_DRIVERS]
    cards = detect() if cards is None else cards
    have = (installed or _installed)([TOOLKIT]) if any(c['vendor'] == 'nvidia' for c in cards) else {}
    fitting = [c for c in cards if c['vendor'] in vendors]
    usable = sorted((c for c in fitting if _usable(c, have)), key=lambda c: vendors.index(c['vendor']))
    if not usable:
        names = ', '.join(VENDOR_NAMES[v] for v in vendors)
        if fitting:
            return None, (f'The {VENDOR_NAMES[fitting[0]["vendor"]]} card is not ready for apps yet. '
                          'Settings › Graphics shows what is missing.')
        return None, f'This NAS has no graphics card this app can use ({names}).'
    card = usable[0]
    plan: Dict[str, Any] = {'service': spec['service'], 'vendor': card['vendor'],
                            'card': f"{card['vendor_name']} {card['model']}".strip()}
    image = (spec.get('images') or {}).get(card['vendor'])
    if isinstance(image, str) and image:
        plan['image'] = image
    if card['vendor'] == 'nvidia':
        plan['deploy'] = {'resources': {'reservations': {'devices': [
            {'driver': 'nvidia', 'count': 'all', 'capabilities': ['gpu']}]}}}
        plan['environment'] = {'NVIDIA_VISIBLE_DEVICES': 'all',
                               'NVIDIA_DRIVER_CAPABILITIES': 'compute,video,utility'}
        return plan, ''
    groups = _group_ids() if groups is None else groups
    plan['devices'] = ['/dev/dri:/dev/dri'] + (['/dev/kfd:/dev/kfd'] if card['vendor'] == 'amd' and spec.get('kfd')
                                               else [])
    # Numbers, not names: the group names inside the container differ.
    plan['group_add'] = [str(groups[g]) for g in ('render', 'video') if g in groups]
    return plan, ''


def apply_plan(compose: Dict[str, Any], plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The compose config with the card handed to the plan's service."""
    if not plan:
        return compose
    service = (compose.get('services') or {}).get(plan['service'])
    if not isinstance(service, dict):
        return compose
    if plan.get('image'):
        service['image'] = plan['image']
    for key in ('devices', 'group_add'):
        if plan.get(key):
            service[key] = list(service.get(key) or []) + [v for v in plan[key] if v not in (service.get(key) or [])]
    if plan.get('deploy'):
        reservations = service.setdefault('deploy', {}).setdefault('resources', {}).setdefault('reservations', {})
        reservations['devices'] = list(reservations.get('devices') or []) + \
            plan['deploy']['resources']['reservations']['devices']
    if plan.get('environment'):
        env = service.get('environment') or {}
        if isinstance(env, list):
            env = dict(str(e).split('=', 1) for e in env if '=' in str(e))
        env.update(plan['environment'])
        service['environment'] = env
    return compose


class _NoHeaders(Exception):
    """No headers package for the running kernel in the package lists."""


def _advice(card: Dict[str, Any], restart: bool) -> Tuple[str, str]:
    """(state, what to tell the person) for one card."""
    vendor, missing = card['vendor'], card['missing']
    if vendor == 'other':
        return 'unknown', 'AlvaOS does not know what this card can do for apps.'
    if vendor == 'nvidia':
        if card['driver'] == 'nvidia':
            if TOOLKIT in missing:
                return 'partial', ('NVIDIA\'s driver is running, but apps cannot use the card until NVIDIA\'s '
                                   'container toolkit is installed.')
            return 'ready', 'NVIDIA\'s driver is running. Apps can use this card.'
        if not missing and restart:
            return 'restart', 'The driver is installed. Restart the NAS to start using it.'
        if not missing:
            return 'restart', 'The driver is installed but not running yet. Restart the NAS.'
        return 'missing', ('This card needs NVIDIA\'s own driver before apps can use it'
                           + (' (it runs on the open "nouveau" driver now, which only shows a picture).'
                              if card['driver'] == 'nouveau' else '.'))
    if not card['ready']:
        return 'missing', 'The card has no working driver yet. Installing its firmware usually fixes that; then restart.'
    if missing:
        return 'partial', 'The card works, but some of its firmware or video drivers are missing, so apps cannot use it to convert video yet.'
    return 'ready', 'The card works and apps can use it to convert video.'


def _apt_problem(text: str) -> str:
    text = text.strip()
    if 'Unable to locate package linux-headers-' in text:
        return ('The kernel headers for the running system are not offered any more. Install the system updates '
                'on the Updates page, restart the NAS, then try again.')
    if 'Unable to locate package' in text or 'has no installation candidate' in text:
        return ('A package was not found. Check that "non-free" and "non-free-firmware" are in the package '
                'sources (Updates page), then try again.')
    if 'Could not get lock' in text or 'Unable to acquire the dpkg frontend lock' in text:
        return 'Another installation is running (an update?). Try again in a few minutes.'
    if 'dkms' in text.lower() or 'bad return status' in text.lower():
        return 'The NVIDIA DKMS driver could not build for this kernel. Check the installation log for the compiler or header error.'
    if 'mok' in text.lower() or 'secure boot' in text.lower() or 'key enrollment' in text.lower():
        return 'Secure Boot may be blocking the NVIDIA driver. Check the installation log and the MOK confirmation shown during restart.'
    if 'dpkg was interrupted' in text.lower() or 'configure -a' in text.lower():
        return 'A previous package installation was interrupted. Repair the package setup, then try again.'
    last = [line for line in text.splitlines() if line.strip()][-1:] or ['']
    return f'The installation failed: {last[0][:300]}'


def _available(package: str) -> bool:
    """Whether apt could install the package (apt-cache needs no privileges)."""
    import subprocess
    try:
        res = subprocess.run([APT_CACHE, 'policy', package], capture_output=True, text=True, timeout=30,
                             env={'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired):
        return True   # cannot tell: let apt try and explain
    if res.returncode != 0:
        return True
    for line in res.stdout.splitlines():
        if line.strip().startswith('Candidate:'):
            return line.split(':', 1)[1].strip() not in ('', '(none)')
    return False


def _installed(packages: List[str]) -> Dict[str, bool]:
    """Which packages are installed (dpkg-query needs no privileges)."""
    import subprocess
    out: Dict[str, bool] = {p: False for p in packages}
    try:
        res = subprocess.run([DPKG_QUERY, '-W', '-f', '${Package} ${db:Status-Status}\\n'] + packages,
                             capture_output=True, text=True, timeout=20, env={'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired):
        return out
    for line in res.stdout.splitlines():
        name, _, state = line.partition(' ')
        if name in out:
            out[name] = state.strip() == 'installed'
    return out


def _touch(path: str) -> None:
    try:
        with open(path, 'w') as f:
            f.write(_now())
    except OSError:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
