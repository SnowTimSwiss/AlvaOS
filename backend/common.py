#!/usr/bin/env python3
"""
AlvaOS Common Utilities
Shared constants, command helpers, and utility functions used across all managers.
"""

import os
import re
import subprocess
import platform
from datetime import datetime, timezone
from pathlib import Path

# System Commands Paths for Sudo (must match sudoers configuration in install-system.sh)
CMD = {
    'GETENT': '/usr/bin/getent',
    'CHPASSWD': '/usr/sbin/chpasswd',
    'USERADD': '/usr/sbin/useradd',
    'USERDEL': '/usr/sbin/userdel',
    'SMBPASSWD': '/usr/bin/smbpasswd',
    'GROUPADD': '/usr/sbin/groupadd',
    'GROUPDEL': '/usr/sbin/groupdel',
    'GPASSWD': '/usr/bin/gpasswd',
    'CHGRP': '/usr/bin/chgrp',
    'CHMOD': '/usr/bin/chmod',
    'SYSTEMCTL': '/usr/bin/systemctl',
    'SYSTEMD_RUN': '/usr/bin/systemd-run',
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
    'LSBLK': '/usr/bin/lsblk',
    'WIPEFS': '/usr/sbin/wipefs',
    'PARTPROBE': '/usr/sbin/partprobe',
    'BTRFS': '/usr/bin/btrfs',
    'MKFS_BTRFS': '/usr/sbin/mkfs.btrfs',
    'MKDIR': '/usr/bin/mkdir',
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


def build_privileged_cmd(cmd):
    """Build a command that runs as root when needed, without requiring sudo if already root."""
    if is_root_user():
        return cmd
    return ['sudo', '-n'] + cmd


def run_sudo_command(cmd, timeout=30, extra_env=None):
    """Helper to run a command with sudo and handle password prompts gracefully"""
    try:
        # Prepare the env with LC_ALL=C to ensure English output
        custom_env = os.environ.copy()
        custom_env['LC_ALL'] = 'C'
        if extra_env:
            custom_env.update(extra_env)

        final_cmd = []
        if cmd and cmd[0] == 'sudo':
            # Remove redundant 'sudo' if present in the cmd list passed to us
            # build_privileged_cmd will add 'sudo -n' if needed
            final_cmd = build_privileged_cmd(cmd[1:])
        else:
            final_cmd = build_privileged_cmd(cmd)

        result = subprocess.run(final_cmd, capture_output=True, text=True, timeout=timeout, env=custom_env)

        if result.returncode != 0:
            stderr_text = (result.stderr or '').strip()
            stdout_text = (result.stdout or '').strip()
            combined_low = f"{stderr_text}\n{stdout_text}".lower()
            if '/etc/sudoers.d/alvaos' in combined_low and (
                'is owned by uid' in combined_low
                or 'is world writable' in combined_low
                or 'bad permissions' in combined_low
            ):
                return result, (
                    "System permission error: /etc/sudoers.d/alvaos has invalid ownership or permissions. "
                    "Run as root: chown root:root /etc/sudoers.d/alvaos && chmod 440 /etc/sudoers.d/alvaos"
                )
            if 'password is required' in combined_low or 'a password is required' in combined_low:
                cmd_str = " ".join(final_cmd)
                return None, f"System permission error: Passwordless sudo is not configured for command: {cmd_str}. Please check the AlvaOS documentation for sudoers setup."
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
