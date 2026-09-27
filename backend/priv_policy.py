#!/usr/bin/env python3
"""
AlvaOS privileged command policy.

The backend runs as the unprivileged ``alvaos`` user. The only thing it may run
as root is the ``alvaos-priv`` helper, and the helper only executes a command
after this module has approved every single argument of it.

Design rules:
- Deny by default. A binary that is not listed here cannot be run at all.
- No shells. ``bash -c``/``sh -c`` and anything else that interprets a string
  as a program are never allowed.
- Files the backend can write (compose files, WireGuard configs, packages) are
  copied into a root-owned staging directory and checked there, so they cannot
  be swapped between the check and the execution.
- Config files that a root daemon later interprets (smb.conf, sshd drop-in,
  wg-quick config, compose files) are content-checked for directives that
  would execute commands as root.

This module is pure logic (no side effects besides read-only inspection of the
system) so it can be unit tested on any machine.
"""

from __future__ import annotations

import json
import os
import pwd
import grp
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, NoReturn, Optional, Sequence


class PolicyError(Exception):
    """Raised when a command is not allowed."""


# ── Constants ────────────────────────────────────────────────────────────────

# Directories the backend may modify through the helper. Everything else on the
# system is read-only from the backend's point of view.
WRITE_ROOTS = ('/mnt/alvaos', '/srv', '/var/lib/alvaos')

# Mount targets: pools are mounted directly below /mnt/alvaos (a root-owned
# directory, so the backend cannot plant symlinks there), offline-update scans
# below a helper-owned directory in /run.
POOL_MOUNT_BASE = '/mnt/alvaos'
SCAN_MOUNT_BASE = '/run/alvaos-scan'

# Root-owned staging directory for files that are checked before use.
STAGING_DIR = '/run/alvaos-priv'

# The only WireGuard config the helper will bring up or down.
WG_CONFIG_PATH = '/var/lib/alvaos/wireguard/buddy0.conf'

UPDATE_CACHE_DIR = '/var/lib/alvaos/updates'
COMPOSE_DIR = '/var/lib/alvaos/compose'

# Files the helper may read or replace, with the checker for their new content.
CONFIG_FILES = {
    '/etc/exports',
    '/etc/samba/smb.conf',
    '/etc/hosts',
    '/etc/ssh/sshd_config.d/00-alvaos-security.conf',
    '/etc/apt/sources.list',
}
READABLE_FILES = CONFIG_FILES | {'/var/log/syslog'}

# Environment variables a caller may forward to the privileged command.
ALLOWED_ENV = {
    'DEBIAN_FRONTEND': {'noninteractive'},
    'COMPOSE_INTERACTIVE_NO_CLI': {'1'},
}

SYSTEM_BIN_DIRS = ('/usr/bin', '/usr/sbin', '/bin', '/sbin', '/usr/local/bin', '/usr/local/sbin')

USERNAME_RE = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
GROUPNAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.-]{0,31}$')
HOSTNAME_RE = re.compile(r'^(?=.{1,63}$)[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?$')
TIMEZONE_RE = re.compile(r'^[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+){0,2}$')
DEVICE_RE = re.compile(r'^/dev/[a-zA-Z0-9][a-zA-Z0-9._-]*$')
UUID_RE = re.compile(r'^[0-9a-fA-F-]{8,64}$')
POOL_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
DOCKER_REF_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$')
PROJECT_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,80}$')
UNIT_RE = re.compile(r'^[A-Za-z0-9@._-]{1,128}$')
DEB_PACKAGE_RE = re.compile(r'^[a-z0-9][a-z0-9.+-]{0,127}(?:=[A-Za-z0-9.+:~-]{1,64})?$')
RAID_RE = re.compile(r'^(single|dup|raid0|raid1|raid1c3|raid1c4|raid5|raid6|raid10)$')

# Groups that grant root-equivalent access. Never let the backend put users in
# them, delete them, or hand files to them.
PRIVILEGED_GROUPS = {
    'root', 'sudo', 'wheel', 'adm', 'admin', 'disk', 'shadow', 'docker', 'lxd',
    'kvm', 'libvirt', 'systemd-journal', 'staff', 'alvaos',
}
PROTECTED_USERS = {'root', 'alvaos', 'nobody'}


# ── Result object ────────────────────────────────────────────────────────────

@dataclass
class Plan:
    """What the helper should execute after a successful policy check."""
    argv: List[str]
    # When set, stdin is read by the helper, checked, and fed to the command.
    stdin_check: Optional[Callable[[bytes], None]] = None
    # Files to copy into the staging directory before execution: argv index -> kind
    stage: Dict[int, str] = field(default_factory=dict)
    # Directories the helper must create (root-owned) before execution.
    make_dirs: List[str] = field(default_factory=list)


# ── System inspection (overridable for tests) ────────────────────────────────

class System:
    """Read-only view of the system that the policy needs. Tests replace it."""

    def system_disks(self) -> set:
        """Names (e.g. 'sda', 'nvme0n1') of disks that hold the running OS."""
        disks = set()
        try:
            out = subprocess.run(
                ['/usr/bin/lsblk', '-J', '-p', '-o', 'NAME,PKNAME,TYPE,MOUNTPOINT'],
                capture_output=True, text=True, timeout=10, env={'LC_ALL': 'C'},
            ).stdout
            data = json.loads(out or '{}')
        except Exception:
            # If we cannot tell which disk is the system disk, refuse to touch any.
            raise PolicyError('Cannot determine system disks; refusing destructive disk operation') from None

        critical = {'/', '/boot', '/boot/efi', '/usr', '/var', '[SWAP]'}

        def walk(node, top):
            name = os.path.basename(node.get('name') or '')
            top = top or name
            if (node.get('mountpoint') or '') in critical:
                disks.add(top)
            for child in node.get('children') or []:
                walk(child, top)

        for dev in data.get('blockdevices') or []:
            walk(dev, None)
        return disks

    def uid_of(self, user: str) -> Optional[int]:
        try:
            return pwd.getpwnam(user).pw_uid
        except KeyError:
            return None

    def gid_of(self, group: str) -> Optional[int]:
        try:
            return grp.getgrnam(group).gr_gid
        except KeyError:
            return None

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def timezone_exists(self, tz: str) -> bool:
        return os.path.isfile(os.path.join('/usr/share/zoneinfo', tz))

    def read_file(self, path: str) -> bytes:
        try:
            with open(path, 'rb') as f:
                return f.read()
        except FileNotFoundError:
            return b''


# ── Small validators ─────────────────────────────────────────────────────────

def _fail(msg: str) -> NoReturn:
    raise PolicyError(msg)


def _clean_abs_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
        _fail(f'Invalid path: {path!r}')
    if any(part == '..' for part in path.split('/')):
        _fail(f'Path traversal is not allowed: {path}')
    return os.path.normpath(path)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip('/') + '/')


def _strictly_within(path: str, root: str) -> bool:
    return path != root and path.startswith(root.rstrip('/') + '/')


def is_temp_root_snapshot(path: str) -> bool:
    """Temporary snapshots of the root subvolume live at /.alvaos-*."""
    return os.path.dirname(path) == '/' and os.path.basename(path).startswith('.alvaos-')


def writable_path(system: System, path: str, allow_root_level_temp: bool = False) -> str:
    """Return the resolved path if the backend may modify it, else raise."""
    clean = _clean_abs_path(path)
    resolved = system.realpath(clean)
    if allow_root_level_temp and is_temp_root_snapshot(clean) and resolved == clean:
        return resolved
    for root in WRITE_ROOTS:
        if _strictly_within(resolved, root):
            return resolved
    _fail(f'Path is outside the AlvaOS data directories: {path}')


def readable_path(path: str) -> str:
    """Read-only operations may look at any clean absolute path."""
    return _clean_abs_path(path)


def _device(system: System, dev: str, destructive: bool) -> str:
    if not DEVICE_RE.match(dev or ''):
        _fail(f'Invalid device: {dev!r}')
    resolved = system.realpath(dev)
    if not DEVICE_RE.match(resolved):
        _fail(f'Invalid device: {dev!r}')
    if destructive:
        name = os.path.basename(resolved)
        for disk in system.system_disks():
            # Match the disk itself and any of its partitions (sda1, nvme0n1p2).
            if name == disk or (name.startswith(disk) and name[len(disk):].lstrip('p').isdigit()):
                _fail(f'Refusing to modify the system disk: {dev}')
    return resolved


def _user(system: System, user: str, must_exist: bool = False, deletable: bool = False) -> str:
    if not USERNAME_RE.match(user or ''):
        _fail(f'Invalid username: {user!r}')
    if deletable:
        if user in PROTECTED_USERS:
            _fail(f'User {user} is protected')
        uid = system.uid_of(user)
        if uid is None or uid < 1000 or uid >= 60000:
            _fail(f'Only regular users can be modified: {user}')
    if must_exist and system.uid_of(user) is None:
        _fail(f'Unknown user: {user}')
    return user


def _share_group(system: System, group: str, must_exist: bool = True) -> str:
    if not GROUPNAME_RE.match(group or ''):
        _fail(f'Invalid group name: {group!r}')
    if group in PRIVILEGED_GROUPS:
        _fail(f'Group {group} is protected')
    gid = system.gid_of(group)
    if gid is None:
        if must_exist:
            _fail(f'Unknown group: {group}')
        return group
    if gid < 1000 or gid >= 60000:
        _fail(f'Only regular groups can be modified: {group}')
    return group


def _int_arg(value: str, lo: int = 0, hi: int = 10**9) -> str:
    if not re.fullmatch(r'\d{1,10}', value or '') or not lo <= int(value) <= hi:
        _fail(f'Invalid number: {value!r}')
    return value


def _expect(args: Sequence[str], *patterns) -> None:
    """Match args exactly against literal strings or callables."""
    if len(args) != len(patterns):
        _fail('Unexpected arguments: ' + ' '.join(args))
    for arg, pat in zip(args, patterns, strict=True):
        if isinstance(pat, str):
            if arg != pat:
                _fail(f'Unexpected argument: {arg}')
        elif isinstance(pat, (set, frozenset, tuple, list)):
            if arg not in pat:
                _fail(f'Unexpected argument: {arg}')
        else:
            pat(arg)


# ── Content checks for config files ──────────────────────────────────────────

# Samba parameters that execute programs (as root for the "root ..." variants)
# or load code. A single injected line with one of these is a root shell.
_SMB_EXEC_KEY = re.compile(
    r'(command|script|exec|program|panic action|vfs objects|^include$|config file|'
    r'username map|modules|log file|lock directory|state directory|private dir)',
    re.IGNORECASE,
)
_SMB_ROOT_KEYS = {'force user', 'force group', 'admin users', 'valid users', 'write list', 'guest account'}


def _new_lines(content: bytes, current: bytes) -> List[str]:
    """Lines of the new content that are not already in the root-owned file.

    Existing lines were written by root (the distribution or an admin on the
    console), so only what the backend adds or changes needs checking.
    """
    existing = {line.strip() for line in current.decode('utf-8', 'replace').splitlines()}
    return [line for line in content.decode('utf-8', 'strict').splitlines() if line.strip() not in existing]


def check_smb_conf(content: bytes, current: bytes = b'') -> None:
    for raw in _new_lines(content, current):
        line = raw.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('['):
            if not re.match(r'^\[[A-Za-z0-9 ._$-]{1,80}\]$', line):
                _fail(f'Invalid smb.conf section: {line!r}')
            continue
        if '=' not in line:
            _fail(f'smb.conf line is not a key = value pair: {line!r}')
        key, value = (part.strip() for part in line.split('=', 1))
        key_l = re.sub(r'\s+', ' ', key.lower())
        if _SMB_EXEC_KEY.search(key_l):
            _fail(f'smb.conf parameter "{key}" is not allowed')
        if key_l in _SMB_ROOT_KEYS and re.search(r'(^|[\s,@+&])root\b', value.lower()):
            _fail(f'smb.conf parameter "{key}" cannot grant root')
        if key_l == 'path':
            clean = _clean_abs_path(value)
            if not any(_strictly_within(clean, root) for root in WRITE_ROOTS):
                _fail(f'Samba share path outside the AlvaOS data directories: {value}')


_SSHD_ALLOWED = {
    'permitrootlogin', 'passwordauthentication', 'permitemptypasswords',
    'pubkeyauthentication', 'kbdinteractiveauthentication',
    'challengeresponseauthentication', 'maxauthtries', 'x11forwarding',
}


def check_sshd_dropin(content: bytes, current: bytes = b'') -> None:
    for raw in content.decode('utf-8', 'strict').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key = line.split()[0].lower()
        if key not in _SSHD_ALLOWED:
            _fail(f'sshd option "{key}" is not allowed')


def check_exports(content: bytes, current: bytes = b'') -> None:
    for raw in _new_lines(content, current):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        path = line.split()[0].strip('"')
        clean = _clean_abs_path(path)
        if not any(_strictly_within(clean, root) for root in WRITE_ROOTS):
            _fail(f'NFS export outside the AlvaOS data directories: {path}')


def check_hosts(content: bytes, current: bytes = b'') -> None:
    for raw in _new_lines(content, current):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2 or not re.match(r'^[0-9a-fA-F:.]+$', parts[0]):
            _fail(f'Invalid /etc/hosts line: {raw!r}')
        for name in parts[1:]:
            if not re.match(r'^[A-Za-z0-9.-]{1,253}$', name):
                _fail(f'Invalid hostname in /etc/hosts: {name!r}')


_APT_SOURCE_RE = re.compile(
    r'^deb(-src)? (\[[^\]]*\] )?https?://(deb|security)\.debian\.org/debian(-security)? '
    r'[a-z][a-z0-9-]* [a-z0-9 -]+$'
)


def check_apt_sources(content: bytes, current: bytes = b'') -> None:
    for raw in content.decode('utf-8', 'strict').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '[' in line and 'signed-by' not in line and 'trusted' in line:
            _fail('trusted=yes apt sources are not allowed')
        if not _APT_SOURCE_RE.match(line):
            _fail(f'Only official Debian apt sources are allowed: {line!r}')


CONFIG_CHECKS = {
    '/etc/exports': check_exports,
    '/etc/samba/smb.conf': check_smb_conf,
    '/etc/hosts': check_hosts,
    '/etc/ssh/sshd_config.d/00-alvaos-security.conf': check_sshd_dropin,
    '/etc/apt/sources.list': check_apt_sources,
}


def check_chpasswd_input(content: bytes) -> None:
    lines = [line for line in content.decode('utf-8', 'strict').splitlines() if line]
    if not lines:
        _fail('chpasswd input is empty')
    for line in lines:
        user, sep, password = line.partition(':')
        if not sep or not USERNAME_RE.match(user) or not password:
            _fail('chpasswd input must be user:password lines')
        if user == 'alvaos':
            _fail('The service account password cannot be changed')


def check_wg_config(content: bytes) -> None:
    """wg-quick runs Pre/PostUp/Down as root shell commands; forbid them."""
    for raw in content.decode('utf-8', 'strict').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or line.startswith('['):
            continue
        key = line.split('=', 1)[0].strip().lower()
        if key not in {
            'privatekey', 'listenport', 'address', 'publickey', 'endpoint',
            'allowedips', 'persistentkeepalive', 'presharedkey', 'mtu', 'table',
            'fwmark',
        }:
            _fail(f'WireGuard config key "{key}" is not allowed')


# Compose settings that break container isolation.
_DANGEROUS_HOST_PATHS = (
    '/', '/etc', '/root', '/boot', '/usr', '/bin', '/sbin', '/lib', '/lib64',
    '/proc', '/sys', '/dev', '/var/run', '/run', '/var/lib/docker',
    '/var/lib/alvaos', '/opt/alvaos', '/home',
)
_ALLOWED_CAPS = {'NET_ADMIN', 'NET_RAW', 'NET_BIND_SERVICE', 'CHOWN', 'SETUID', 'SETGID',
                 'DAC_OVERRIDE', 'FOWNER', 'SYS_NICE', 'SYS_TIME', 'KILL', 'MKNOD', 'AUDIT_WRITE'}
_ALLOWED_DEVICE_PREFIXES = ('/dev/dri', '/dev/net/tun', '/dev/nvidia', '/dev/video', '/dev/snd')


def _check_host_path(src: str) -> None:
    if any(part == '..' for part in src.split('/')) or src.startswith('~'):
        _fail(f'Compose bind mount with path traversal: {src}')
    if not src.startswith('/'):
        return  # named volume or relative path inside the project directory
    clean = os.path.normpath(src)
    for bad in _DANGEROUS_HOST_PATHS:
        if clean == bad or (bad != '/' and _within(clean, bad)):
            # /var/lib/alvaos is the backend's own state; /run and /var/run
            # hold the docker socket.
            _fail(f'Compose bind mount of a protected host path: {src}')


def check_compose(content: bytes) -> None:
    import yaml  # python3-yaml is a hard dependency of AlvaOS

    try:
        doc = yaml.safe_load(content.decode('utf-8', 'strict'))
    except Exception as e:
        _fail(f'Compose file is not valid YAML: {e}')
    if not isinstance(doc, dict) or not isinstance(doc.get('services'), dict):
        _fail('Compose file has no services')

    for name, svc in doc['services'].items():
        if not isinstance(svc, dict):
            _fail(f'Invalid service definition: {name}')
        if svc.get('privileged'):
            _fail(f'Service {name}: privileged containers are not allowed')
        for key in ('pid', 'ipc', 'userns_mode', 'uts', 'cgroup'):
            if str(svc.get(key, '')).lower() == 'host':
                _fail(f'Service {name}: {key}: host is not allowed')
        for opt in svc.get('security_opt') or []:
            if 'unconfined' in str(opt).lower() or 'label:disable' in str(opt).lower():
                _fail(f'Service {name}: security_opt {opt} is not allowed')
        for cap in svc.get('cap_add') or []:
            if str(cap).upper().replace('CAP_', '') not in _ALLOWED_CAPS:
                _fail(f'Service {name}: capability {cap} is not allowed')
        for dev in svc.get('devices') or []:
            host = str(dev).split(':', 1)[0]
            if not host.startswith(_ALLOWED_DEVICE_PREFIXES):
                _fail(f'Service {name}: device {host} is not allowed')
        for vol in svc.get('volumes') or []:
            if isinstance(vol, str):
                _check_host_path(vol.split(':', 1)[0])
            elif isinstance(vol, dict):
                if vol.get('type', 'volume') == 'bind':
                    _check_host_path(str(vol.get('source') or ''))
            else:
                _fail(f'Service {name}: invalid volume entry')

    for vol in (doc.get('volumes') or {}).values():
        opts = (vol or {}).get('driver_opts') or {} if isinstance(vol, dict) else {}
        if 'device' in opts:
            _check_host_path(str(opts['device']))


# ── Per-binary rules ─────────────────────────────────────────────────────────

def _rule_chpasswd(sys_: System, args):
    _expect(args)
    return Plan(argv=[], stdin_check=check_chpasswd_input)


def _rule_useradd(sys_: System, args):
    _expect(args, '-m', '-s', '/bin/bash', lambda u: _user(sys_, u))
    if args[3] in PROTECTED_USERS:
        _fail(f'User {args[3]} is protected')
    return Plan(argv=[])


def _rule_userdel(sys_: System, args):
    _expect(args, lambda u: _user(sys_, u, deletable=True))
    return Plan(argv=[])


def _rule_smbpasswd(sys_: System, args):
    if len(args) == 3:
        _expect(args, '-a', '-s', lambda u: _user(sys_, u, must_exist=True))
    else:
        _expect(args, '-x', lambda u: _user(sys_, u))
    if args[-1] in {'root', 'alvaos'}:
        _fail('Samba passwords for system accounts are not managed by AlvaOS')
    return Plan(argv=[])


def _rule_groupadd(sys_: System, args):
    _expect(args, '-f', lambda g: _share_group(sys_, g, must_exist=False))
    return Plan(argv=[])


def _rule_groupdel(sys_: System, args):
    _expect(args, lambda g: _share_group(sys_, g))
    return Plan(argv=[])


def _rule_gpasswd(sys_: System, args):
    _expect(args, {'-a', '-d'}, lambda u: _user(sys_, u), lambda g: _share_group(sys_, g))
    return Plan(argv=[])


def _rule_chgrp(sys_: System, args):
    _expect(args, '-R', lambda g: _share_group(sys_, g), lambda p: writable_path(sys_, p))
    return Plan(argv=['-R', args[1], writable_path(sys_, args[2])])


_OCTAL_MODE_RE = re.compile(r'^0?[0-2]?[0-7]{3}$')
_SYMBOLIC_MODE_RE = re.compile(r'^[ugoa]*[+=-][rwxX]*(,[ugoa]*[+=-][rwxX]*)*$')


def _mode(mode: str) -> str:
    # setuid is never allowed; setgid (2xxx) is used for shared folders.
    if _OCTAL_MODE_RE.match(mode) or _SYMBOLIC_MODE_RE.match(mode):
        return mode
    _fail(f'File mode not allowed: {mode}')


def _rule_chmod(sys_: System, args):
    _expect(args, '-R', _mode, lambda p: writable_path(sys_, p))
    return Plan(argv=['-R', args[1], writable_path(sys_, args[2])])


def _owner(sys_: System, spec: str) -> str:
    m = re.match(r'^(\d{1,10}|[a-z_][a-z0-9_-]{0,31})(?::(\d{1,10}|[a-zA-Z_][a-zA-Z0-9_.-]{0,31}))?$', spec or '')
    if not m:
        _fail(f'Invalid owner: {spec!r}')
    user, group = m.group(1), m.group(2)
    uid = int(user) if user.isdigit() else sys_.uid_of(user)
    if uid is None or uid == 0:
        _fail('Files cannot be handed to root or an unknown user')
    if group is not None:
        gid = int(group) if group.isdigit() else sys_.gid_of(group)
        if gid is None or gid == 0:
            _fail('Files cannot be handed to the root group or an unknown group')
        if not group.isdigit() and group in PRIVILEGED_GROUPS - {'alvaos'}:
            _fail(f'Group {group} is protected')
    return spec


def _rule_chown(sys_: System, args):
    _expect(args, '-R', lambda o: _owner(sys_, o), lambda p: writable_path(sys_, p))
    return Plan(argv=['-R', args[1], writable_path(sys_, args[2])])


def _rule_mkdir(sys_: System, args):
    _expect(args, '-p', lambda p: None)
    path = _clean_abs_path(args[1])
    if _pool_mountpoint(path, strict=False):
        return Plan(argv=['-p', path])
    return Plan(argv=['-p', writable_path(sys_, path)])


def _rule_rmdir(sys_: System, args):
    _expect(args, lambda p: None)
    path = _clean_abs_path(args[0])
    if _pool_mountpoint(path, strict=False):
        return Plan(argv=[path])
    return Plan(argv=[writable_path(sys_, path)])


def _rule_mv(sys_: System, args):
    _expect(args, lambda p: None, lambda p: None)
    src = writable_path(sys_, args[0], allow_root_level_temp=True)
    dst = writable_path(sys_, args[1], allow_root_level_temp=True)
    return Plan(argv=[src, dst])


_SYSTEMCTL_ALLOWED = {
    ('restart', 'alvaos.service'), ('start', 'alvaos.service'),
    ('stop', 'alvaos.service'), ('status', 'alvaos.service'),
    ('restart', 'ssh'), ('restart', 'smbd'),
    ('restart', 'docker'), ('restart', 'docker.service'), ('status', 'docker.service'),
    ('reload', 'nfs-kernel-server'), ('restart', 'nfs-kernel-server'),
}


def _rule_systemctl(sys_: System, args):
    if len(args) == 2 and args[0] == 'is-active' and UNIT_RE.match(args[1]):
        return Plan(argv=list(args))
    if tuple(args) not in _SYSTEMCTL_ALLOWED:
        _fail('systemctl action not allowed: ' + ' '.join(args))
    return Plan(argv=list(args))


# apt: only fixed actions, only known -o options. Arbitrary -o would allow
# APT::Update::Pre-Invoke and similar hooks, i.e. root command execution.
_APT_OPTIONS = [
    re.compile(r'^Dpkg::Lock::Timeout=\d{1,5}$'),
    re.compile(r'^Dir::Etc::sourcelist=/var/lib/alvaos/updates/debian-upgrade-[0-9a-f]{8}\.list$'),
    re.compile(r'^Dir::Etc::sourceparts=-$'),
]
_APT_FLAGS = {'-y', '-f', '--no-install-recommends', '--allow-releaseinfo-change', '--without-new-pkgs'}
_APT_ACTIONS = {'update', 'upgrade', 'full-upgrade', 'install', 'autoremove'}


def _rule_apt(sys_: System, args):
    action = None
    i = 0
    staged = {}
    while i < len(args):
        arg = args[i]
        if arg == '-o':
            if i + 1 >= len(args) or not any(r.match(args[i + 1]) for r in _APT_OPTIONS):
                _fail(f'apt option not allowed: {args[i + 1] if i + 1 < len(args) else ""}')
            if args[i + 1].startswith('Dir::Etc::sourcelist='):
                staged[i + 1] = 'apt-sources-option'
            i += 2
            continue
        if arg in _APT_FLAGS:
            i += 1
            continue
        if action is None and arg in _APT_ACTIONS:
            action = arg
            i += 1
            continue
        if action == 'install' and DEB_PACKAGE_RE.match(arg):
            i += 1
            continue
        _fail(f'apt argument not allowed: {arg}')
    if action is None:
        _fail('apt action missing')
    return Plan(argv=list(args), stage=staged)


def _rule_dpkg(sys_: System, args):
    _expect(args, '-i', lambda p: None)
    path = _clean_abs_path(args[1])
    resolved = sys_.realpath(path)
    if not _strictly_within(resolved, UPDATE_CACHE_DIR) or not resolved.endswith('.deb'):
        _fail('Only packages from the AlvaOS update cache can be installed')
    # The package is copied to the staging dir and, if it is an AlvaOS package,
    # its signature is verified there (see alvaos-priv).
    return Plan(argv=['-i', resolved], stage={1: 'deb'})


_SMARTCTL_FLAGS = {'-a', '-x', '-H', '-A', '-i', '-j', '-d', 'sat', 'scsi', 'nvme', 'auto'}


def _rule_smartctl(sys_: System, args):
    if not args:
        _fail('smartctl needs a device')
    for arg in args[:-1]:
        if arg not in _SMARTCTL_FLAGS:
            _fail(f'smartctl argument not allowed: {arg}')
    _device(sys_, args[-1], destructive=False)
    return Plan(argv=list(args))


def _rule_readonly_flags(allowed_flag_re: str):
    flag_re = re.compile(allowed_flag_re)

    def rule(sys_: System, args):
        for arg in args:
            if arg.startswith('/dev/'):
                _device(sys_, arg, destructive=False)
            elif arg.startswith('/'):
                readable_path(arg)
            elif not flag_re.match(arg):
                _fail(f'Argument not allowed: {arg}')
        return Plan(argv=list(args))
    return rule


def _rule_wipefs(sys_: System, args):
    flags = [a for a in args if a.startswith('-')]
    devs = [a for a in args if not a.startswith('-')]
    if not set(flags) <= {'-a', '-f'} or len(devs) != 1:
        _fail('wipefs usage: wipefs -a [-f] DEVICE')
    _device(sys_, devs[0], destructive=True)
    return Plan(argv=list(args))


def _rule_partprobe(sys_: System, args):
    _expect(args, lambda d: _device(sys_, d, destructive=True))
    return Plan(argv=list(args))


def _rule_mkfs_btrfs(sys_: System, args):
    i = 0
    devices = []
    while i < len(args):
        arg = args[i]
        if arg == '-f':
            i += 1
        elif arg == '-L':
            if i + 1 >= len(args) or not POOL_NAME_RE.match(args[i + 1]):
                _fail('Invalid filesystem label')
            i += 2
        elif arg in ('-d', '-m'):
            if i + 1 >= len(args) or not RAID_RE.match(args[i + 1]):
                _fail('Invalid RAID profile')
            i += 2
        else:
            devices.append(_device(sys_, arg, destructive=True))
            i += 1
    if not devices:
        _fail('mkfs.btrfs needs at least one device')
    return Plan(argv=list(args))


def _pool_mountpoint(path: str, strict: bool = True) -> bool:
    parent, name = os.path.split(path)
    ok = parent == POOL_MOUNT_BASE and bool(POOL_NAME_RE.match(name))
    if strict and not ok:
        _fail(f'Pools can only be mounted directly below {POOL_MOUNT_BASE}')
    return ok


def _scan_mountpoint(path: str) -> bool:
    parent, name = os.path.split(path)
    return parent == SCAN_MOUNT_BASE and bool(re.match(r'^[a-zA-Z0-9]+$', name))


def _rule_mount(sys_: System, args):
    if len(args) == 3 and args[0] == '-U':
        if not UUID_RE.match(args[1]):
            _fail('Invalid filesystem UUID')
        target = _clean_abs_path(args[2])
        _pool_mountpoint(target)
        return Plan(argv=list(args))
    if len(args) == 2:
        _device(sys_, args[0], destructive=True)
        target = _clean_abs_path(args[1])
        _pool_mountpoint(target)
        return Plan(argv=list(args))
    if len(args) == 4 and args[0] == '-o' and args[1] == 'ro':
        # Offline update scan of a USB stick: force it harmless.
        _device(sys_, args[2], destructive=True)
        target = _clean_abs_path(args[3])
        if not _scan_mountpoint(target):
            _fail(f'Scan mounts must live below {SCAN_MOUNT_BASE}')
        return Plan(argv=['-o', 'ro,nosuid,nodev,noexec', args[2], target], make_dirs=[target])
    _fail('mount usage not allowed: ' + ' '.join(args))


def _rule_umount(sys_: System, args):
    rest = [a for a in args if a != '-l']
    if len(rest) != 1 or len(args) - len(rest) > 1:
        _fail('umount usage: umount [-l] TARGET')
    target = rest[0]
    if target.startswith('/dev/'):
        _device(sys_, target, destructive=True)
    else:
        clean = _clean_abs_path(target)
        if not (_pool_mountpoint(clean, strict=False) or _scan_mountpoint(clean)
                or any(_strictly_within(clean, r) for r in ('/media', '/mnt'))):
            _fail(f'Refusing to unmount {target}')
    return Plan(argv=list(args))


def _rule_btrfs(sys_: System, args):
    if len(args) < 2:
        _fail('btrfs usage not allowed')
    group, action, rest = args[0], args[1], list(args[2:])

    # Read-only inspection
    if group == 'filesystem' and action == 'show':
        if len(rest) > 1:
            _fail('btrfs filesystem show takes at most one argument')
        if rest and rest[0].startswith('/'):
            readable_path(rest[0])
        elif rest and not UUID_RE.match(rest[0]):
            _fail('Invalid btrfs filesystem reference')
        return Plan(argv=list(args))
    if group == 'filesystem' and action == 'usage':
        _expect(rest, readable_path)
        return Plan(argv=list(args))
    if group == 'subvolume' and action in ('show', 'get-default'):
        _expect(rest, readable_path)
        return Plan(argv=list(args))
    if group == 'subvolume' and action == 'list':
        paths = [a for a in rest if a != '-o']
        _expect(paths, readable_path)
        return Plan(argv=list(args))

    # Modifications
    if group == 'subvolume' and action == 'create':
        _expect(rest, lambda p: writable_path(sys_, p))
        return Plan(argv=list(args))
    if group == 'subvolume' and action == 'delete':
        _expect(rest, lambda p: writable_path(sys_, p, allow_root_level_temp=True))
        target = _clean_abs_path(rest[0])
        if _pool_mountpoint(target, strict=False):
            _fail('Refusing to delete a pool root')
        return Plan(argv=list(args))
    if group == 'subvolume' and action == 'snapshot':
        srcs = [a for a in rest if a != '-r']
        if len(srcs) != 2:
            _fail('btrfs subvolume snapshot needs SOURCE DEST')
        readable_path(srcs[0])
        writable_path(sys_, srcs[1], allow_root_level_temp=True)
        return Plan(argv=list(args))
    if group == 'subvolume' and action == 'set-default':
        _expect(rest, _int_arg, lambda p: None)
        target = _clean_abs_path(rest[1])
        if target != '/' and not _pool_mountpoint(target, strict=False) \
                and not any(_strictly_within(target, r) for r in WRITE_ROOTS):
            _fail('btrfs set-default target not allowed')
        return Plan(argv=list(args))
    if group == 'send':
        if len(args) == 2:
            # Stream to stdout: the backend pipes it straight into the upload.
            readable_path(args[1])
            return Plan(argv=list(args))
        _expect(args[1:], '-f', lambda p: writable_path(sys_, p), readable_path)
        return Plan(argv=list(args))
    if group == 'receive':
        _expect(args[1:], '-f', lambda p: writable_path(sys_, p), lambda p: None)
        target = _clean_abs_path(args[3])
        if not (_pool_mountpoint(target, strict=False) or any(_strictly_within(target, r) for r in WRITE_ROOTS)):
            _fail('btrfs receive target not allowed')
        return Plan(argv=list(args))
    if group == 'device' and action == 'add':
        if len(rest) < 2:
            _fail('btrfs device add needs devices and a mount point')
        for dev in rest[:-1]:
            if dev == '-f':
                continue
            _device(sys_, dev, destructive=True)
        _pool_mountpoint(_clean_abs_path(rest[-1]))
        return Plan(argv=list(args))
    if group == 'device' and action == 'remove':
        _expect(rest, 'missing', lambda p: _pool_mountpoint(_clean_abs_path(p)))
        return Plan(argv=list(args))
    if group == 'balance' and action == 'start':
        for arg in rest[:-1]:
            if not re.match(r'^-[dm]convert=(single|dup|raid0|raid1|raid1c3|raid1c4|raid5|raid6|raid10)$', arg):
                _fail(f'btrfs balance option not allowed: {arg}')
        _pool_mountpoint(_clean_abs_path(rest[-1]) if rest else '')
        return Plan(argv=list(args))
    _fail('btrfs usage not allowed: ' + ' '.join(args))


def _rule_exportfs(sys_: System, args):
    _expect(args, '-ra')
    return Plan(argv=list(args))


def _rule_tee(sys_: System, args):
    append = bool(args) and args[0] == '-a'
    targets = args[1:] if append else args
    _expect(targets, CONFIG_FILES)
    target = targets[0]
    check = CONFIG_CHECKS[target]
    current = sys_.read_file(target)
    return Plan(argv=list(args), stdin_check=lambda data: check(data, current))


def _rule_cat(sys_: System, args):
    _expect(args, READABLE_FILES)
    return Plan(argv=list(args))


def _rule_tail(sys_: System, args):
    _expect(args, '-n', lambda n: _int_arg(n, 1, 5000), '/var/log/syslog')
    return Plan(argv=list(args))


def _rule_journalctl(sys_: System, args):
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('-n', '-u'):
            if i + 1 >= len(args):
                _fail(f'{arg} needs a value')
            if arg == '-n':
                _int_arg(args[i + 1], 1, 5000)
            elif not UNIT_RE.match(args[i + 1]):
                _fail('Invalid unit name')
            i += 2
            continue
        if arg in ('--no-pager', '--output=short', '--output=json', '-r'):
            i += 1
            continue
        _fail(f'journalctl argument not allowed: {arg}')
    return Plan(argv=list(args))


def _rule_hostnamectl(sys_: System, args):
    if list(args) == ['hostname']:
        return Plan(argv=list(args))
    _expect(args, 'set-hostname', lambda h: HOSTNAME_RE.match(h) or _fail('Invalid hostname'))
    return Plan(argv=list(args))


def _rule_timedatectl(sys_: System, args):
    if len(args) >= 1 and args[0] == 'show':
        _expect(args[1:], {'--property=Timezone', '--property=NTP'}, '--value')
        return Plan(argv=list(args))
    if len(args) == 2 and args[0] == 'set-timezone':
        if not TIMEZONE_RE.match(args[1]) or not sys_.timezone_exists(args[1]):
            _fail('Unknown timezone')
        return Plan(argv=list(args))
    _expect(args, 'set-ntp', {'true', 'false', 'yes', 'no', '1', '0'})
    return Plan(argv=list(args))


def _rule_noargs(sys_: System, args):
    _expect(args)
    return Plan(argv=[])


# docker: container lifecycle and inspection only. `docker run`/`create` would
# allow mounting the host filesystem, so new containers only come from checked
# compose files.
_DOCKER_SIMPLE = {'start', 'restart', 'logs', 'stats', 'inspect', 'pull', 'rm', 'stop', 'ps', 'info', 'version', 'images'}
_DOCKER_FLAG_RE = re.compile(
    r'^(-a|-f|-t|--tail|--no-stream|--format|--all|-q|--no-trunc|\d{1,6}|\{\{[^}]*\}\}|\{\{json [^}]*\}\})$'
)


def _rule_docker(sys_: System, args):
    if not args:
        _fail('docker needs a subcommand')
    sub, rest = args[0], list(args[1:])
    if sub == 'image' and rest[:1] == ['inspect']:
        sub, rest = 'inspect', rest[1:]
    if sub == 'exec':
        # docker exec [-u USER] [-w DIR] CONTAINER /bin/sh -lc COMMAND
        # Runs inside an existing container; --privileged is never allowed.
        i = 0
        while i < len(rest) and rest[i] in ('-u', '-w'):
            if i + 1 >= len(rest):
                _fail('docker exec option needs a value')
            i += 2
        tail = rest[i:]
        if len(tail) != 4 or tail[1:3] != ['/bin/sh', '-lc'] or not DOCKER_REF_RE.match(tail[0]):
            _fail('docker exec usage not allowed')
        return Plan(argv=list(args))
    if sub not in _DOCKER_SIMPLE:
        _fail(f'docker {sub} is not allowed')
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ('--format', '--tail', '-t'):
            if i + 1 >= len(rest):
                _fail(f'{arg} needs a value')
            if arg == '--format':
                if '{{' not in rest[i + 1] or len(rest[i + 1]) > 200:
                    _fail('Invalid --format value')
            else:
                _int_arg(rest[i + 1], 0, 100000)
            i += 2
            continue
        if arg.startswith('-'):
            if not _DOCKER_FLAG_RE.match(arg):
                _fail(f'docker flag not allowed: {arg}')
        elif not DOCKER_REF_RE.match(arg):
            _fail(f'Invalid container or image reference: {arg}')
        i += 1
    return Plan(argv=list(args))


_COMPOSE_ACTIONS = {
    ('up', '-d'), ('down',), ('down', '--remove-orphans'), ('pull',), ('ps',),
    ('stop',), ('start',), ('restart',), ('config',), ('up', '-d', '--remove-orphans'),
    ('up', '-d', '--force-recreate'), ('up', '-d', '--remove-orphans', '--force-recreate'),
    ('ps', '--format', 'json'), ('ps', '-q'),
}


def _rule_compose(sys_: System, args):
    if len(args) < 5 or args[0] != '-f' or args[2] != '-p':
        _fail('docker-compose usage: -f FILE -p PROJECT ACTION')
    path = _clean_abs_path(args[1])
    resolved = sys_.realpath(path)
    if not _strictly_within(resolved, COMPOSE_DIR) or not resolved.endswith(('.yml', '.yaml')):
        _fail(f'Compose files must live in {COMPOSE_DIR}')
    if not PROJECT_RE.match(args[3]):
        _fail('Invalid compose project name')
    if tuple(args[4:]) not in _COMPOSE_ACTIONS:
        _fail('docker-compose action not allowed: ' + ' '.join(args[4:]))
    return Plan(argv=['-f', resolved] + list(args[2:]), stage={1: 'compose'})


def _rule_ip(sys_: System, args):
    allowed = [
        ['-4', '-o', 'addr', 'show'],
        ['route', 'show', 'default'],
    ]
    if list(args) in allowed:
        return Plan(argv=list(args))
    if list(args[:-1]) == ['-4', '-o', 'addr', 'show', 'dev'] and re.match(r'^[a-zA-Z0-9_.-]{1,15}$', args[-1]):
        return Plan(argv=list(args))
    _fail('ip usage not allowed')


def _rule_wg(sys_: System, args):
    _expect(args, 'show', 'buddy0')
    return Plan(argv=list(args))


def _rule_wg_quick(sys_: System, args):
    _expect(args, {'up', 'down'}, WG_CONFIG_PATH)
    return Plan(argv=list(args), stage={1: 'wg'})


def _rule_readonly_any(sys_: System, args):
    # id, getent, df, mountpoint, blkid: pure queries.
    for arg in args:
        if arg.startswith('/'):
            readable_path(arg)
        elif not re.match(r'^[A-Za-z0-9_.,=:-]{1,64}$', arg):
            _fail(f'Argument not allowed: {arg}')
    return Plan(argv=list(args))


RULES = {
    'chpasswd': _rule_chpasswd,
    'useradd': _rule_useradd,
    'userdel': _rule_userdel,
    'smbpasswd': _rule_smbpasswd,
    'groupadd': _rule_groupadd,
    'groupdel': _rule_groupdel,
    'gpasswd': _rule_gpasswd,
    'chgrp': _rule_chgrp,
    'chmod': _rule_chmod,
    'chown': _rule_chown,
    'mkdir': _rule_mkdir,
    'rmdir': _rule_rmdir,
    'mv': _rule_mv,
    'systemctl': _rule_systemctl,
    'apt-get': _rule_apt,
    'apt': _rule_apt,
    'dpkg': _rule_dpkg,
    'smartctl': _rule_smartctl,
    'lsblk': _rule_readonly_flags(r'^(-[a-zA-Z]{1,6}|[A-Z,-]{1,120})$'),
    'blkid': _rule_readonly_flags(r'^(-[so]|UUID|value|TYPE|LABEL)$'),
    'btrfs': _rule_btrfs,
    'wipefs': _rule_wipefs,
    'partprobe': _rule_partprobe,
    'mkfs.btrfs': _rule_mkfs_btrfs,
    'mount': _rule_mount,
    'umount': _rule_umount,
    'exportfs': _rule_exportfs,
    'tee': _rule_tee,
    'cat': _rule_cat,
    'tail': _rule_tail,
    'journalctl': _rule_journalctl,
    'hostnamectl': _rule_hostnamectl,
    'timedatectl': _rule_timedatectl,
    'reboot': _rule_noargs,
    'poweroff': _rule_noargs,
    'docker': _rule_docker,
    'docker-compose': _rule_compose,
    'ip': _rule_ip,
    'wg': _rule_wg,
    'wg-quick': _rule_wg_quick,
    'id': _rule_readonly_any,
    'getent': _rule_readonly_any,
    'df': _rule_readonly_any,
    'mountpoint': _rule_readonly_any,
}


def resolve_binary(path: str) -> str:
    """Map an absolute binary path to its rule name. Only system dirs count."""
    if not isinstance(path, str) or not path.startswith('/'):
        _fail(f'Commands must be absolute paths: {path!r}')
    directory, name = os.path.split(os.path.normpath(path))
    if directory not in SYSTEM_BIN_DIRS:
        _fail(f'Command outside system directories: {path}')
    if name not in RULES:
        _fail(f'Command not allowed: {name}')
    return name


def validate(argv: Sequence[str], system: Optional[System] = None) -> Plan:
    """Validate a full command line. Returns the Plan to execute."""
    if not argv:
        _fail('Empty command')
    for arg in argv:
        if not isinstance(arg, str) or '\x00' in arg or '\n' in arg:
            _fail('Arguments must be single-line strings')
    system = system or System()
    name = resolve_binary(argv[0])
    plan = RULES[name](system, list(argv[1:]))
    plan.argv = [os.path.normpath(argv[0])] + plan.argv
    return plan


# ── Named operations (not a plain command line) ──────────────────────────────

SYSFS_POWER_RE = re.compile(
    r'^/sys/class/power_supply/[A-Za-z0-9_.-]+/charge_control_(start|end)_threshold$'
)


def validate_sysfs_write(path: str, value: str) -> None:
    if not SYSFS_POWER_RE.fullmatch(path or ''):
        _fail('Only battery charge thresholds can be written')
    _int_arg(value, 0, 100)


def validate_update_package(path: str, system: Optional[System] = None) -> str:
    system = system or System()
    clean = _clean_abs_path(path)
    resolved = system.realpath(clean)
    if not _strictly_within(resolved, UPDATE_CACHE_DIR) or not resolved.endswith('.deb'):
        _fail('Update packages must come from the AlvaOS update cache')
    return resolved


def validate_env(pairs: Iterable[str]) -> Dict[str, str]:
    env = {}
    for pair in pairs:
        key, sep, value = pair.partition('=')
        if not sep or key not in ALLOWED_ENV or value not in ALLOWED_ENV[key]:
            _fail(f'Environment variable not allowed: {pair}')
        env[key] = value
    return env
