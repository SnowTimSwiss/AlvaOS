#!/usr/bin/env python3
"""A UPS on USB (Settings › Power › Battery backup), run by NUT.

The most common failure at home is a power cut. With a UPS, AlvaOS shuts
down cleanly while the battery still has charge, and the UPS switches itself
off and on again when power returns, so the NAS starts by itself (when the
BIOS is set to start on power).

How it is put together (NUT, Network UPS Tools, from Debian):
- The driver talks to the UPS over USB, upsd serves its values on
  127.0.0.1:3493, upsmon watches them and shuts the NAS down when the UPS
  reports "on battery, battery low". Standalone mode: nothing listens on
  the network.
- AlvaOS writes the four NUT files through the privilege helper, which
  checks each line (backend/priv_policy.py): no command can be smuggled into
  them, the shutdown command is fixed.
- The backend reads the values with upsc (no privileges) every 30 seconds,
  notes when the power fails and comes back, and, when the person chose
  "after N minutes on battery", asks upsmon to shut down early (upsmon -c fsd).
  upsmon then also tells the UPS to switch off, which is what lets the NAS
  start again when the power returns.
"""

import glob
import json
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from common import CMD

STATE_FILE = '/var/lib/alvaos/ups_nut.json'
UPSC = '/usr/bin/upsc'          # reads values from upsd, runs without the helper
UPS_NAME = 'ups'
MONITOR_USER = 'alvaos-monitor'
PACKAGES = ['nut']
NUT_FILES = {
    'nut': '/etc/nut/nut.conf',
    'ups': '/etc/nut/ups.conf',
    'users': '/etc/nut/upsd.users',
    'upsmon': '/etc/nut/upsmon.conf',
}
SHUTDOWN_CMD = '/sbin/shutdown -h +0'
POWERDOWN_FLAG = '/etc/killpower'
# "After N minutes on battery"; 0 means when the UPS says its battery is low.
SHUTDOWN_CHOICES = (0, 2, 5, 10, 20)

# Makers of UPSs that speak USB HID (the usbhid-ups driver). Cheap UPSs with
# a serial chip behind USB use the Megatec protocol (nutdrv_qx).
VENDORS = {
    '051d': ('APC', 'usbhid-ups'),
    '0463': ('Eaton', 'usbhid-ups'),
    '0764': ('CyberPower', 'usbhid-ups'),
    '09ae': ('Tripp Lite', 'usbhid-ups'),
    '0d9f': ('Powercom', 'usbhid-ups'),
    '050d': ('Belkin', 'usbhid-ups'),
    '10af': ('Liebert', 'usbhid-ups'),
    '06da': ('Phoenixtec', 'usbhid-ups'),
    '2b2d': ('Ecoflow', 'usbhid-ups'),
    '0665': ('UPS', 'nutdrv_qx'),
    '0001': ('UPS', 'nutdrv_qx'),
}
HEX4 = re.compile(r'^[0-9a-f]{4}$')

_lock = threading.Lock()
_job: Dict[str, Any] = {'running': False, 'step': '', 'error': '', 'finished_at': ''}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: str) -> str:
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read().strip()
    except OSError:
        return ''


def _clean_text(text: str, limit: int = 60) -> str:
    """What a USB device calls itself, safe for a quoted NUT value."""
    return re.sub(r'[^A-Za-z0-9 ._()-]', '', text or '').strip()[:limit]


def detect(sys_root: str = '/sys') -> List[Dict[str, str]]:
    """UPSs connected by USB: a known maker, or a device that says it is one."""
    found = []
    for dev in sorted(glob.glob(os.path.join(sys_root, 'bus/usb/devices/*'))):
        vendor = _read(os.path.join(dev, 'idVendor')).lower()
        product = _read(os.path.join(dev, 'idProduct')).lower()
        if not HEX4.match(vendor) or not HEX4.match(product):
            continue
        maker, driver = VENDORS.get(vendor, ('', ''))
        name = _clean_text(_read(os.path.join(dev, 'product')))
        says_ups = bool(re.search(r'\bups\b', name.lower()))
        if not driver:
            if not says_ups:
                continue
            driver = 'usbhid-ups'
        if vendor == '0001' and not says_ups:
            continue   # 0001 is used by other cheap devices too
        manufacturer = _clean_text(_read(os.path.join(dev, 'manufacturer'))) or maker
        found.append({
            'vendorid': vendor, 'productid': product, 'driver': driver,
            'name': ' '.join(p for p in (manufacturer, name) if p) or f'UPS {vendor}:{product}',
        })
    return found


def render_files(device: Dict[str, str], password: str) -> Dict[str, str]:
    """The four NUT files for one UPS on USB, in standalone mode."""
    desc = _clean_text(device.get('name', ''))
    return {
        NUT_FILES['nut']: '# Managed by AlvaOS - Settings > Power\nMODE=standalone\n',
        NUT_FILES['ups']: (
            '# Managed by AlvaOS - Settings > Power\n'
            'maxretry = 3\n\n'
            f'[{UPS_NAME}]\n'
            f'\tdriver = {device["driver"]}\n'
            '\tport = auto\n'
            f'\tvendorid = {device["vendorid"]}\n'
            f'\tproductid = {device["productid"]}\n'
            f'\tdesc = "{desc}"\n'
        ),
        NUT_FILES['users']: (
            '# Managed by AlvaOS - Settings > Power\n'
            f'[{MONITOR_USER}]\n'
            f'\tpassword = {password}\n'
            '\tupsmon primary\n'
        ),
        NUT_FILES['upsmon']: (
            '# Managed by AlvaOS - Settings > Power\n'
            f'MONITOR {UPS_NAME}@localhost 1 {MONITOR_USER} {password} primary\n'
            'MINSUPPLIES 1\n'
            f'SHUTDOWNCMD "{SHUTDOWN_CMD}"\n'
            f'POWERDOWNFLAG {POWERDOWN_FLAG}\n'
            'FINALDELAY 5\n'
        ),
    }


def off_files() -> Dict[str, str]:
    """Turning the UPS off: NUT stays installed but does nothing."""
    return {NUT_FILES['nut']: '# Managed by AlvaOS - Settings > Power\nMODE=none\n'}


def parse_upsc(text: str) -> Dict[str, str]:
    values = {}
    for line in (text or '').splitlines():
        key, sep, value = line.partition(':')
        if sep and re.match(r'^[a-z0-9_.]+$', key.strip()):
            values[key.strip()] = value.strip()
    return values


def describe(values: Dict[str, str]) -> Dict[str, Any]:
    """What the page shows, from upsc's values."""
    flags = set(values.get('ups.status', '').split())

    def number(key):
        try:
            return float(values[key])
        except (KeyError, ValueError):
            return None

    runtime = number('battery.runtime')
    charge = number('battery.charge')
    load = number('ups.load')
    return {
        'reachable': bool(values),
        'on_battery': 'OB' in flags,
        'low_battery': 'LB' in flags,
        'replace_battery': 'RB' in flags,
        'charging': 'CHRG' in flags,
        'charge_percent': int(charge) if charge is not None else None,
        'runtime_minutes': int(runtime // 60) if runtime is not None else None,
        'load_percent': int(load) if load is not None else None,
        'model': ' '.join(p for p in (values.get('device.mfr') or values.get('ups.mfr', ''),
                                      values.get('device.model') or values.get('ups.model', '')) if p),
        'status_flags': sorted(flags),
    }


class NutUps:
    def __init__(self, run_command: Callable, state_file: str = STATE_FILE, sys_root: str = '/sys',
                 read_values: Optional[Callable[[], Dict[str, str]]] = None,
                 notify: Optional[Callable[..., Any]] = None, installed: Optional[Callable[[], bool]] = None,
                 clock: Callable[[], float] = time.time):
        self.run = run_command
        self.state_file = state_file
        self.sys_root = sys_root
        self.read_values = read_values or _read_upsc
        self.notify = notify or _notify
        self.installed = installed or _nut_installed
        self.clock = clock
        self.on_battery_since: Optional[float] = None
        self.shutdown_asked = False
        self.last: Dict[str, Any] = {}

    # ── Settings ───────────────────────────────────────────────────────────
    def settings(self) -> Dict[str, Any]:
        try:
            with open(self.state_file) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {'enabled': False}

    def _save(self, data: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        tmp = self.state_file + '.tmp'
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.state_file)

    def status(self) -> Dict[str, Any]:
        settings = self.settings()
        live = describe(self.read_values()) if settings.get('enabled') else {}
        with _lock:
            job = dict(_job)
        public = {k: settings.get(k) for k in ('enabled', 'name', 'shutdown_after_minutes')}
        return {
            'settings': public,
            'detected': detect(self.sys_root),
            'live': live,
            'on_battery_since': datetime.fromtimestamp(self.on_battery_since, timezone.utc).isoformat()
            if self.on_battery_since else None,
            'job': job,
            'shutdown_choices': list(SHUTDOWN_CHOICES),
        }

    # ── Set up, change, turn off ───────────────────────────────────────────
    def set_up(self, vendorid: str, productid: str, shutdown_after_minutes: int = 0) -> Tuple[bool, str]:
        if shutdown_after_minutes not in SHUTDOWN_CHOICES:
            return False, 'Choose when the NAS shuts down.'
        device = next((d for d in detect(self.sys_root)
                       if d['vendorid'] == vendorid and d['productid'] == productid), None)
        if not device:
            return False, 'That UPS is not connected any more. Check its USB cable and look again.'
        with _lock:
            if _job['running']:
                return False, 'The UPS is being set up already.'
            _job.update({'running': True, 'step': 'Installing NUT', 'error': '', 'finished_at': ''})
        threading.Thread(target=self._set_up, args=(device, shutdown_after_minutes), name='ups-setup',
                         daemon=True).start()
        return True, f'Setting up {device["name"]}.'

    def _set_up(self, device: Dict[str, str], minutes: int) -> None:
        error = ''
        try:
            if not self.installed():
                res, err = self.run([CMD['APT_GET'], 'update'], timeout=600)
                res, err = self.run([CMD['APT_GET'], '-y', 'install'] + PACKAGES, timeout=1800,
                                    extra_env={'DEBIAN_FRONTEND': 'noninteractive'})
                if err or not res or res.returncode != 0:
                    raise RuntimeError('NUT could not be installed. Check the updates page, then try again.')
            with _lock:
                _job['step'] = 'Starting'
            password = self.settings().get('password') or secrets.token_hex(16)
            for path, content in render_files(device, password).items():
                self._write(path, content)
            self._restart()
            self._save({'enabled': True, 'name': device['name'], 'vendorid': device['vendorid'],
                        'productid': device['productid'], 'driver': device['driver'], 'password': password,
                        'shutdown_after_minutes': minutes})
        except Exception as e:  # noqa: BLE001 - shown on the page
            error = str(e)
        with _lock:
            _job.update({'running': False, 'step': '', 'error': error, 'finished_at': _now()})

    def change(self, shutdown_after_minutes: int) -> Tuple[bool, str]:
        settings = self.settings()
        if not settings.get('enabled'):
            return False, 'Set up the UPS first.'
        if shutdown_after_minutes not in SHUTDOWN_CHOICES:
            return False, 'Choose when the NAS shuts down.'
        settings['shutdown_after_minutes'] = shutdown_after_minutes
        self._save(settings)
        return True, 'Saved.'

    def turn_off(self) -> Tuple[bool, str]:
        settings = self.settings()
        if not settings.get('enabled'):
            return True, 'The UPS is off.'
        try:
            for path, content in off_files().items():
                self._write(path, content)
            for unit in ('nut-monitor.service', 'nut-server.service'):
                self.run([CMD['SYSTEMCTL'], 'disable', '--now', unit], timeout=60)
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        settings['enabled'] = False
        self._save(settings)
        self.on_battery_since = None
        return True, 'The NAS no longer watches the UPS.'

    def _write(self, path: str, content: str) -> None:
        res, err = self.run([CMD['TEE'], path], timeout=20, input=content)
        if err or not res or res.returncode != 0:
            raise RuntimeError(f'Could not write {path}.')

    def _restart(self) -> None:
        # The enumerator turns ups.conf into a driver service; then the server
        # and upsmon start with the new files.
        self.run([CMD['SYSTEMCTL'], 'restart', 'nut-driver-enumerator.service'], timeout=60)
        for unit in ('nut-server.service', 'nut-monitor.service'):
            self.run([CMD['SYSTEMCTL'], 'enable', '--now', unit], timeout=60)
            res, err = self.run([CMD['SYSTEMCTL'], 'restart', unit], timeout=60)
            if err:
                raise RuntimeError(f'{unit} did not start. Check that the UPS is connected.')

    # ── Watching ───────────────────────────────────────────────────────────
    def check(self) -> None:
        """Called every 30 seconds by the power monitor."""
        settings = self.settings()
        if not settings.get('enabled'):
            return
        live = describe(self.read_values())
        self.last = live
        now = self.clock()
        if live['on_battery'] and self.on_battery_since is None:
            self.on_battery_since = now
            self.shutdown_asked = False
            left = f' ({live["charge_percent"]}%' if live['charge_percent'] is not None else ''
            left += f', about {live["runtime_minutes"]} minutes)' if left and live['runtime_minutes'] is not None \
                else (')' if left else '')
            when = settings.get('shutdown_after_minutes') or 0
            plan = f'It shuts down after {when} minutes on battery.' if when else \
                'It shuts down cleanly when the battery runs low.'
            self.notify('warning', 'The power failed', f'The NAS runs on its UPS battery{left}. {plan}',
                        link='system.html#power', fingerprint=f'ups-on-battery-{int(now)}')
        elif not live['on_battery'] and self.on_battery_since is not None and live['reachable']:
            minutes = max(1, round((now - self.on_battery_since) / 60))
            self.on_battery_since = None
            self.notify('info', 'The power is back',
                        f'The NAS ran on its UPS battery for about {minutes} minute{"s" if minutes != 1 else ""}.',
                        link='system.html#power', fingerprint=f'ups-power-back-{int(now)}')
        when = settings.get('shutdown_after_minutes') or 0
        if (when and self.on_battery_since is not None and not self.shutdown_asked
                and now - self.on_battery_since >= when * 60):
            self.shutdown_asked = True
            self.notify('critical', 'Shutting down', f'The power has been out for {when} minutes. '
                        'The NAS shuts down now and starts again when the power is back.',
                        link='system.html#power', fingerprint=f'ups-shutdown-{int(now)}')
            self.run([CMD['UPSMON'], '-c', 'fsd'], timeout=30)

    def alert(self) -> Optional[Dict[str, str]]:
        """A line for the alerts list while something needs attention."""
        if not self.settings().get('enabled'):
            return None
        live = self.last or {}
        if not live:
            return None
        if not live.get('reachable'):
            return {'id': 'ups-unreachable', 'severity': 'warning', 'title': 'The UPS does not answer',
                    'message': 'Check its USB cable. Until it answers, the NAS cannot shut down cleanly in a power cut.'}
        if live.get('on_battery'):
            return {'id': 'ups-on-battery', 'severity': 'critical', 'title': 'Running on the UPS battery',
                    'message': 'The power failed. The NAS shuts down cleanly before the battery is empty.'}
        if live.get('replace_battery'):
            return {'id': 'ups-replace-battery', 'severity': 'warning', 'title': 'The UPS battery is worn out',
                    'message': 'Replace it soon: the UPS says it would not last long in a power cut.'}
        return None


def _read_upsc() -> Dict[str, str]:
    try:
        res = subprocess.run([UPSC, f'{UPS_NAME}@localhost'], capture_output=True, text=True, timeout=10,
                             env={'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return parse_upsc(res.stdout) if res.returncode == 0 else {}


def _nut_installed() -> bool:
    return os.path.exists('/usr/sbin/upsmon') or os.path.exists('/sbin/upsmon')


def _notify(severity, title, message, link=None, fingerprint=None):
    try:
        from alerts_manager import push_notification
        push_notification(severity, title, message, source='ups', link=link, fingerprint=fingerprint)
    except Exception:  # noqa: BLE001 - a lost note must not stop the watching
        pass
