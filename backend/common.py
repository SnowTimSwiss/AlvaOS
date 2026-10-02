#!/usr/bin/env python3
"""
AlvaOS Common Utilities
Shared constants, command helpers, and utility functions used across all managers.
"""

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Absolute paths of the system commands the backend runs. Privileged ones go
# through the alvaos-priv helper, which checks them against priv_policy.py.
CMD = {
    'GETENT': '/usr/bin/getent',
    'CHPASSWD': '/usr/sbin/chpasswd',
    'USERADD': '/usr/sbin/useradd',
    'USERDEL': '/usr/sbin/userdel',
    'USERMOD': '/usr/sbin/usermod',
    'SMBPASSWD': '/usr/bin/smbpasswd',
    'GROUPADD': '/usr/sbin/groupadd',
    'GROUPDEL': '/usr/sbin/groupdel',
    'GPASSWD': '/usr/bin/gpasswd',
    'CHGRP': '/usr/bin/chgrp',
    'CHMOD': '/usr/bin/chmod',
    'SYSTEMCTL': '/usr/bin/systemctl',
    'REBOOT': '/usr/sbin/reboot',
    'POWEROFF': '/usr/sbin/poweroff',
    'TIMEDATECTL': '/usr/bin/timedatectl',
    'HOSTNAMECTL': '/usr/bin/hostnamectl',
    'APT_GET': '/usr/bin/apt-get',
    'APT': '/usr/bin/apt',
    'DPKG': '/usr/bin/dpkg',
    'TAIL': '/usr/bin/tail',
    'JOURNALCTL': '/usr/bin/journalctl',
    'SMARTCTL': '/usr/sbin/smartctl',
    'HDPARM': '/usr/sbin/hdparm',
    'LSBLK': '/usr/bin/lsblk',
    'WIPEFS': '/usr/sbin/wipefs',
    'PARTPROBE': '/usr/sbin/partprobe',
    'BTRFS': '/usr/bin/btrfs',
    'MKFS_BTRFS': '/usr/sbin/mkfs.btrfs',
    'MKDIR': '/usr/bin/mkdir',
    'CP': '/usr/bin/cp',
    'FIND': '/usr/bin/find',
    'MOUNT': '/usr/bin/mount',
    'UMOUNT': '/usr/bin/umount',
    'RMDIR': '/usr/bin/rmdir',
    'BLKID': '/usr/sbin/blkid',
    'EXPORTFS': '/usr/sbin/exportfs',
    'TEE': '/usr/bin/tee',
    'CAT': '/usr/bin/cat',
    'IP': '/usr/sbin/ip',
    'ID': '/usr/bin/id',
    'DF': '/usr/bin/df',
    'MOUNTPOINT': '/usr/bin/mountpoint'
}


def is_root_user():
    try:
        return hasattr(os, 'geteuid') and os.geteuid() == 0
    except Exception:
        return False


# The only command the alvaos user may run through sudo (see scripts/sudoers.alvaos).
PRIV_HELPER = '/opt/alvaos/bin/alvaos-priv'
# Environment variables the helper forwards (everything else is dropped by sudo).
PRIV_FORWARDED_ENV = ('DEBIAN_FRONTEND', 'COMPOSE_INTERACTIVE_NO_CLI')
PRIV_DENIED_EXIT = 126


def build_privileged_cmd(cmd, env=None):
    """Wrap a command so it runs as root through the alvaos-priv helper.

    When the backend already runs as root (development, installer) the command
    is returned unchanged.
    """
    if cmd and cmd[0] == 'sudo':
        cmd = [c for c in cmd[1:] if c != '-n'] if cmd[1:2] == ['-n'] else cmd[1:]
    if is_root_user():
        return list(cmd)
    wrapped = ['sudo', '-n', PRIV_HELPER]
    for key in PRIV_FORWARDED_ENV:
        if env and key in env:
            wrapped += ['--env', f'{key}={env[key]}']
    return wrapped + ['--'] + list(cmd)


def privilege_error_message(result, final_cmd):
    """Human-readable error for a failed privileged command, or None."""
    stderr_text = (result.stderr or '').strip() if isinstance(result.stderr, str) else ''
    stdout_text = (result.stdout or '').strip() if isinstance(result.stdout, str) else ''
    combined_low = f"{stderr_text}\n{stdout_text}".lower()
    if result.returncode == PRIV_DENIED_EXIT and 'alvaos-priv: denied' in combined_low:
        return f"Blocked by the AlvaOS privilege policy: {stderr_text.split('denied:', 1)[-1].strip()}"
    if '/etc/sudoers.d/alvaos' in combined_low and (
        'is owned by uid' in combined_low
        or 'is world writable' in combined_low
        or 'bad permissions' in combined_low
    ):
        return (
            "System permission error: /etc/sudoers.d/alvaos has invalid ownership or permissions. "
            "Run as root: chown root:root /etc/sudoers.d/alvaos && chmod 440 /etc/sudoers.d/alvaos"
        )
    if '/usr/bin/sudo' in combined_low and 'owned by uid' in combined_low:
        return (
            "System permission error: /usr/bin/sudo has invalid ownership. "
            "Run as root: chown root:root /usr/bin/sudo && chmod 4755 /usr/bin/sudo"
        )
    if 'password is required' in combined_low:
        return (
            "System permission error: passwordless sudo for the AlvaOS privilege helper is not "
            "configured. Check /etc/sudoers.d/alvaos."
        )
    return None


def run_sudo_command(cmd, timeout=30, extra_env=None, input=None):
    """Run a command as root through the privilege helper.

    Returns (CompletedProcess or None, error message or None).
    """
    try:
        custom_env = os.environ.copy()
        custom_env['LC_ALL'] = 'C'
        if extra_env:
            custom_env.update(extra_env)

        final_cmd = build_privileged_cmd(cmd, env=extra_env)
        result = subprocess.run(final_cmd, capture_output=True, text=True, timeout=timeout,
                                env=custom_env, input=input)

        if result.returncode != 0:
            special = privilege_error_message(result, final_cmd)
            if special and 'password' in special.lower():
                return None, special
            if special:
                return result, special
            stderr_text = (result.stderr or '').strip()
            stdout_text = (result.stdout or '').strip()
            cmd_str = " ".join(final_cmd)
            detail = stderr_text or stdout_text or f"exit code {result.returncode}"
            return result, f"Command failed ({result.returncode}): {cmd_str}: {detail}"

        return result, None
    except subprocess.TimeoutExpired:
        return None, "Command timed out"
    except Exception as e:
        return None, str(e)


def ensure_directories():
    """Ensure necessary directories exist"""
    Path('/var/lib/alvaos').mkdir(parents=True, exist_ok=True)
    Path('/var/log/alvaos').mkdir(parents=True, exist_ok=True)


def parse_size_to_bytes(size_str):
    """Parse size strings like '8.00GiB' into bytes."""
    try:
        s = size_str.strip()
        m = re.match(r'^([\d\.]+)\s*([KMGTP]i?B)$', s)
        if not m:
            return None
        value = float(m.group(1))
        unit = m.group(2)
        multipliers = {
            'KB': 1000, 'MB': 1000**2, 'GB': 1000**3, 'TB': 1000**4, 'PB': 1000**5,
            'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3, 'TiB': 1024**4, 'PiB': 1024**5
        }
        return int(value * multipliers.get(unit, 1))
    except Exception:
        return None


def format_bytes_gib(byte_val):
    try:
        gib = byte_val / (1024**3)
        return f"{gib:.2f}GiB"
    except Exception:
        return "Unknown"


def _utc_now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _utc_now().isoformat()


def _parse_iso(value):
    try:
        raw = str(value or '').strip()
        if not raw:
            return None
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except Exception:
        return fallback


def _read_cpu_temperature_c():
    try:
        import psutil
        temps = psutil.sensors_temperatures(fahrenheit=False)
        if isinstance(temps, dict):
            for group_name in ('coretemp', 'k10temp', 'cpu_thermal', 'cpu-thermal', 'soc_thermal'):
                entries = temps.get(group_name, [])
                for entry in entries:
                    current = getattr(entry, 'current', None)
                    if isinstance(current, (int, float)):
                        return round(float(current), 1)
            for entries in temps.values():
                for entry in entries:
                    current = getattr(entry, 'current', None)
                    if isinstance(current, (int, float)):
                        return round(float(current), 1)
    except Exception as e:
        print(f"Warning: Failed to read CPU temperature: {e}")
    return None
