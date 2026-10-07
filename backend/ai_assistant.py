#!/usr/bin/env python3
"""The AlvaOS assistant: questions about this NAS in plain words.

It talks to any OpenAI-compatible chat API (Ollama on the network or in the
cloud, OpenAI, OpenRouter, LM Studio, ...). To answer, it may read the same
API endpoints the web page uses, as the signed-in admin, through a fixed list
of read-only endpoints without secrets. It cannot change anything yet.
"""

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from common import ensure_directories

SETTINGS_FILE = '/var/lib/alvaos/ai.json'
MAX_ROUNDS = 6
MAX_TOOL_CHARS = 8000
MAX_HISTORY = 30

PROVIDERS: Dict[str, Dict[str, Any]] = {
    'ollama-local': {'label': 'Ollama on your network', 'base_url': 'http://localhost:11434/v1', 'needs_key': False,
                     'model': 'llama3.1'},
    'ollama-cloud': {'label': 'Ollama Cloud', 'base_url': 'https://ollama.com/v1', 'needs_key': True,
                     'model': 'gpt-oss:20b'},
    'openai': {'label': 'OpenAI', 'base_url': 'https://api.openai.com/v1', 'needs_key': True, 'model': 'gpt-4o-mini'},
    'other': {'label': 'Another OpenAI-compatible service', 'base_url': '', 'needs_key': False, 'model': ''},
}

# What the assistant may read: (name, endpoint, what it is). Only GET, only
# what the page shows, nothing secret (no tokens, keys, recovery kits, logs).
TOOLS: List[Tuple[str, str, str]] = [
    ('system_overview', '/api/v1/system/info', 'CPU, memory, uptime, network, AlvaOS version and pool usage'),
    ('problems', '/api/v1/alerts', 'Current alerts: what needs attention right now'),
    ('notifications', '/api/v1/notifications', 'Recent notifications (the bell)'),
    ('storage_pools', '/api/v1/storage/pools', 'Storage pools: size, use, RAID level, disks, health'),
    ('disks', '/api/v1/storage/disks', 'All disks with model, size, SMART health and role'),
    ('data_checks', '/api/v1/storage/health-checks', 'Schedule and results of data checks (scrub) and disk tests'),
    ('disk_sleep', '/api/v1/storage/disk-power', 'Whether hard disks sleep when idle'),
    ('shared_folders', '/api/v1/storage/shares', 'Shared folders, who can open them and how'),
    ('people', '/api/v1/users', 'People (share accounts) on this NAS'),
    ('backup_status', '/api/v1/backup/status', 'When backups last ran and if they worked'),
    ('backup_settings', '/api/v1/backup/settings', 'What is backed up, how often, how long restore points are kept'),
    ('restore_points', '/api/v1/backup/snapshots', 'Restore points (snapshots) of folders'),
    ('apps', '/api/v1/apps/installed', 'Installed apps and their versions'),
    ('app_containers', '/api/v1/containers', 'Running and stopped app containers'),
    ('updates', '/api/v1/updates/status', 'Whether an AlvaOS update is running and how the last one went'),
    ('alvaos_update_check', '/api/v1/updates/alvaos/check', 'Whether a newer AlvaOS version is available'),
    ('update_history', '/api/v1/updates/history', 'Past updates'),
    ('network', '/api/v1/system/network', 'Network addresses and interfaces'),
    ('time', '/api/v1/system/time', 'Date, time and time zone'),
    ('services', '/api/v1/watchdog/status', 'Whether file sharing and apps are running'),
    ('virtual_machines', '/api/v1/vms', 'Virtual machines: whether they can run here, and each machine with its state, '
                                       'cores, memory and disk'),
    ('hub', '/api/v1/hub', 'AlvaOS Hub: on or off, its apps (Files, Photos, Calendar, Chat) and who sees each'),
    ('backup_disk', '/api/v1/backup/copy', 'The USB backup disk: chosen or not, connected, last copy, problems'),
    ('buddy_backup', '/api/v1/backup/pairing/status', 'Buddy Backup: whether a second NAS is paired and reachable'),
    ('ups', '/api/v1/system/ups', 'The UPS on USB: set up or not, on mains or on battery, charge, minutes left, '
                                  'when the NAS shuts down'),
    ('https', '/api/v1/system/tls', 'The HTTPS certificate of this NAS and whether "HTTPS only" is on'),
    ('remote_access', '/api/v1/remote-access', 'Remote access: Tailscale (on or off, signed in, the devices in it) '
                                               'and the Cloudflare Tunnel (the Hub at an own domain)'),
    ('graphics_cards', '/api/v1/system/gpu', 'Graphics cards, their driver and what is missing for apps to use them'),
    ('signed_in_devices', '/api/v1/auth/sessions', 'Where AlvaOS is signed in: device, address, last use'),
    ('system_log', '/api/v1/system/logs', 'The last lines of the system log (secrets masked), to find out why '
                                          'something failed'),
]
TOOL_PATHS = {name: path for name, path, _ in TOOLS}

# Read tools that need one argument: name -> (description, argument, what it is,
# allowed values, path). The value is checked before it goes into the path.
PARAM_TOOLS: Dict[str, Tuple[str, str, str, 're.Pattern[str]', str]] = {
    'app_log': ('The last lines of one app\'s log (secrets masked), to find out why it misbehaves',
                'container', 'Name or id of the container from app_containers',
                re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'), '/api/v1/containers/{}/logs?lines=60'),
    'disk_health': ('SMART details of one disk: errors, temperature, hours, reallocated sectors',
                    'disk', 'The disk name from disks, like sda or nvme0n1',
                    re.compile(r'^[a-z][a-z0-9]{1,15}$'), '/api/v1/storage/disks/{}/smart'),
}
LOG_TOOLS = {'system_log', 'app_log'}

# Commands the assistant may run by itself, like an admin in a terminal would
# to find out what is wrong (docs/ADMIN-TERMINAL.md): only these, each a fixed
# program with fixed arguments, without a shell, as the AlvaOS service account
# (never root), read-only, with a time limit; the output is masked like logs.
# The chat shows exactly what ran. A few take one value, checked first.
# name -> (what it shows, argv, (value name, what it is, allowed) or None)
SERVICE_RE = re.compile(r'^(smbd|nmbd|nfs-kernel-server|docker|ssh|alvaos|alvaos-files|alvaos-watchdog|nut-server|'
                        r'nut-monitor|NetworkManager|systemd-timesyncd|systemd-resolved|alvaos-vm@[0-9a-f]{8})'
                        r'(\.service)?$')
HOST_RE = re.compile(r'^(?!-)[A-Za-z0-9.-]{1,253}$')
COMMANDS: Dict[str, Tuple[str, List[str], Optional[Tuple[str, str, 're.Pattern[str]']]]] = {
    'disk_space': ('Free space of every mounted file system (df -h)',
                   ['/usr/bin/df', '-h', '-x', 'tmpfs', '-x', 'devtmpfs', '-x', 'overlay', '-x', 'squashfs'], None),
    'memory': ('Memory and swap in use (free -h)', ['/usr/bin/free', '-h'], None),
    'load': ('How long the NAS runs and how busy it is (uptime)', ['/usr/bin/uptime'], None),
    'busiest_processes': ('The processes using the most CPU (ps)',
                          ['/usr/bin/ps', '-eo', 'pid,user,pcpu,pmem,etime,comm', '--sort=-pcpu'], None),
    'biggest_processes': ('The processes using the most memory (ps)',
                          ['/usr/bin/ps', '-eo', 'pid,user,pcpu,pmem,rss,comm', '--sort=-rss'], None),
    'block_devices': ('Disks, partitions and where they are mounted (lsblk)',
                      ['/usr/bin/lsblk', '-o', 'NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL'], None),
    'mounts': ('Mounted storage with its options (findmnt)',
               ['/usr/bin/findmnt', '-rn', '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS', '-t',
                'btrfs,ext4,xfs,vfat,exfat,ntfs3,nfs,nfs4,cifs'], None),
    'addresses': ('Network interfaces and their addresses (ip address)', ['/usr/bin/ip', '-brief', 'address'], None),
    'routes': ('The routes, with the router (ip route)', ['/usr/bin/ip', 'route'], None),
    'listening_ports': ('Which ports the NAS listens on (ss)', ['/usr/bin/ss', '-tulnH'], None),
    'failed_services': ('Services that failed (systemctl --failed)',
                        ['/usr/bin/systemctl', '--failed', '--no-pager', '--plain'], None),
    'service_status': ('The state and last lines of one service (systemctl status)',
                       ['/usr/bin/systemctl', 'status', '--no-pager', '-n', '25', '{}'],
                       ('service', 'One of smbd, nmbd, nfs-kernel-server, docker, ssh, alvaos, alvaos-files, '
                        'nut-server, nut-monitor, NetworkManager, systemd-timesyncd', SERVICE_RE)),
    'time_sync': ('Whether the clock is synchronised (timedatectl)', ['/usr/bin/timedatectl'], None),
    'name_lookup': ('What address a name resolves to (getent ahosts)', ['/usr/bin/getent', 'ahosts', '{}'],
                    ('host', 'A host name like example.com or nas.local', HOST_RE)),
    'ping': ('Whether another device or the internet answers (ping, 4 times)',
             ['/usr/bin/ping', '-c', '4', '-W', '2', '{}'], ('host', 'A host name or IP address', HOST_RE)),
}
# Services the assistant may propose to restart (api_system.py runs it through
# the helper, whose policy allows exactly these): name -> (what, what happens).
RESTARTABLE = {
    'smbd': ('file sharing for Windows and Mac (SMB)',
             'Open files over the network are closed for a moment; computers reconnect by themselves.'),
    'nfs-kernel-server': ('file sharing over NFS', 'NFS clients pause for a moment and continue.'),
    'docker': ('Docker, which runs the apps', 'Every app stops and starts again; they are away for a minute.'),
    'alvaos-files': ('AlvaOS Hub (Files, Photos, Calendar)', 'People in the Hub sign in again; uploads continue.'),
}
COMMAND_LINES = 60
COMMAND_SECONDS = 20


def command_line(name: str, value: str = '') -> Optional[List[str]]:
    """The argv for a catalog command, or None when it or its value is not allowed."""
    entry = COMMANDS.get(name)
    if not entry:
        return None
    _, argv, param = entry
    if param:
        if not param[2].match(value or ''):
            return None
        return [value if a == '{}' else a for a in argv]
    return list(argv)


def _run_command(argv: List[str]) -> Tuple[int, str]:
    import subprocess
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=COMMAND_SECONDS, stdin=subprocess.DEVNULL,
                             env={'LC_ALL': 'C', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'SYSTEMD_COLORS': '0'})
    except FileNotFoundError:
        return 127, f'{argv[0]} is not installed.'
    except subprocess.TimeoutExpired:
        return 124, f'It did not finish within {COMMAND_SECONDS} seconds.'
    return res.returncode, (res.stdout or '') + (res.stderr or '')


def run_command_tool(args: Dict[str, Any], run: Callable[[List[str]], Tuple[int, str]]) -> Tuple[str, str]:
    """(what ran, the answer for the model)."""
    name = str(args.get('name') or '')
    value = str(args.get('value') or '').strip()
    argv = command_line(name, value)
    if argv is None:
        param = (COMMANDS.get(name) or ('', [], None))[2]
        return '', json.dumps({'error': f'Give {param[0]}: {param[1]}.' if param else
                               f'There is no command called {name}. Use one of: {", ".join(COMMANDS)}.'})
    code, output = run(argv)
    lines = str(mask_text(output)).splitlines()
    if len(lines) > COMMAND_LINES:
        lines = lines[:COMMAND_LINES] + [f'…({len(lines) - COMMAND_LINES} more lines)']
    shown = ' '.join([os.path.basename(argv[0])] + argv[1:])
    return shown, json.dumps({'command': shown, 'exit_code': code, 'output': '\n'.join(lines)[:MAX_TOOL_CHARS]})


LEVELS = ('read', 'ask')   # only look / propose changes the person confirms

# What the assistant may propose at the "ask" level. It never runs one
# itself: a proposal is shown in the chat with exactly what will happen, and
# only the person's click runs it, through the normal endpoint with their
# session and CSRF token (api_ai.py). Each builder checks its arguments
# against the real state and returns (title, detail, method, path, body).
ACTION_SPECS: Dict[str, Dict[str, Any]] = {
    'backup_now': {
        'description': 'Make restore points of the shared folders now (a backup on this NAS).',
        'parameters': {},
    },
    'start_data_check': {
        'description': 'Start a data check (scrub) of a storage pool: every block is read and bad copies are '
                       'repaired from the good one. Takes hours; the NAS is slower meanwhile.',
        'parameters': {'pool_id': {'type': 'string', 'description': 'The id of the pool from storage_pools.'}},
    },
    'restart_app': {
        'description': 'Restart one app (container) that hangs or misbehaves.',
        'parameters': {'container': {'type': 'string', 'description': 'Name or id of the container from app_containers.'}},
    },
    'check_services': {
        'description': 'Check file sharing and apps now and restart what has stopped.',
        'parameters': {},
    },
    'restart_service': {
        'description': 'Restart one system service that hangs: file sharing for Windows and Mac (smbd), NFS, '
                       'Docker (all apps restart), or AlvaOS Hub.',
        'parameters': {'service': {'type': 'string', 'enum': ['smbd', 'nfs-kernel-server', 'docker', 'alvaos-files']}},
    },
    'turn_on_automatic_backups': {
        'description': 'Turn on automatic restore points of the shared folders.',
        'parameters': {'every': {'type': 'string', 'enum': ['hour', 'day', 'week']}},
    },
    'update_app': {
        'description': 'Update one installed app to its newest version.',
        'parameters': {'app_id': {'type': 'string', 'description': 'The app_id from apps.'}},
    },
    'install_alvaos_update': {
        'description': 'Install the newest AlvaOS version (from alvaos_update_check).',
        'parameters': {},
    },
    'set_disk_sleep': {
        'description': 'Let hard disks sleep after some minutes without use (0 = never).',
        'parameters': {'minutes': {'type': 'integer', 'enum': [0, 10, 20, 30, 60]}},
    },
    'create_shared_folder': {
        'description': 'Make a new shared folder on a pool, for some people (they can read and change files) '
                       'or for everyone on the home network.',
        'parameters': {
            'name': {'type': 'string', 'description': 'Folder name: letters, numbers, - and _, no spaces.'},
            'pool_id': {'type': 'string', 'description': 'The id or name of the pool from storage_pools.'},
            'people': {'type': 'array', 'items': {'type': 'string'},
                       'description': 'Usernames from people who may open it. Leave empty with everyone=true.'},
            'everyone': {'type': 'boolean', 'description': 'Everyone on the home network may open it.'},
        },
        'required': ['name', 'pool_id'],
    },
    'set_folder_access': {
        'description': 'Give one person access to a shared folder (read, or read and change), or take it away.',
        'parameters': {
            'folder': {'type': 'string', 'description': 'Name of the shared folder from shared_folders.'},
            'person': {'type': 'string', 'description': 'Username from people.'},
            'access': {'type': 'string', 'enum': ['read', 'write', 'none']},
        },
    },
    'set_folder_limit': {
        'description': 'Set how much space a shared folder may use, in GB (0 = no limit).',
        'parameters': {
            'folder': {'type': 'string', 'description': 'Name of the shared folder from shared_folders.'},
            'gigabytes': {'type': 'number', 'description': 'The limit in GB, 0 for no limit.'},
        },
    },
    'turn_on_files_app': {
        'description': 'Turn on AlvaOS Hub (with Files): the people open their folders in the browser and share links.',
        'parameters': {},
    },
    'copy_to_backup_disk': {
        'description': 'Copy the newest restore points to the connected USB backup disk now.',
        'parameters': {},
    },
}


def _find(items: Any, *keys: str) -> List[Dict[str, Any]]:
    if isinstance(items, dict):
        for key in keys:
            if isinstance(items.get(key), list):
                return [i for i in items[key] if isinstance(i, dict)]
        return []
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def build_action(name: str, args: Dict[str, Any], read: Callable[[str], Tuple[int, Any]]) -> Dict[str, Any]:
    """A proposal the person can confirm, or ValueError with why not."""
    if name == 'backup_now':
        return {'title': 'Make a backup now',
                'detail': 'Restore points of your shared folders are made now. This takes a moment and changes '
                          'no files.', 'method': 'POST', 'path': '/api/v1/backup/run', 'body': {'backup_type': 'pool'}}
    if name == 'start_data_check':
        pool = _pool(args.get('pool_id'), read)
        return {'title': f'Start a data check of "{pool.get("name") or pool.get("id")}"',
                'detail': 'Every block is read and bad copies are repaired from the good one. It takes a few '
                          'hours, the NAS is a little slower meanwhile; you can stop it on the Storage page.',
                'method': 'POST', 'path': f'/api/v1/storage/pools/{pool.get("id")}/scrub', 'body': {}}
    if name == 'restart_app':
        wanted = str(args.get('container') or '').strip()
        _, data = read('/api/v1/containers')
        found = None
        for c in _find(data, 'containers'):
            ident = str(c.get('ID') or c.get('id') or '')
            label = str(c.get('Names') or c.get('name') or '')
            if wanted and (wanted == label or (len(wanted) >= 6 and ident.startswith(wanted))):
                found = (ident, label)
                break
        if not found or not re_container.match(found[0] or found[1]):
            raise ValueError(f'There is no app container "{wanted}".')
        ident, label = found[0] or found[1], found[1]
        return {'title': f'Restart the app "{label or ident}"',
                'detail': 'The app stops and starts again; it is away for a moment. Its data stays.',
                'method': 'POST', 'path': f'/api/v1/containers/{ident}/restart', 'body': {}}
    if name == 'check_services':
        return {'title': 'Check file sharing and apps now',
                'detail': 'AlvaOS checks its services and restarts what has stopped.',
                'method': 'POST', 'path': '/api/v1/watchdog/check', 'body': {}}
    if name == 'restart_service':
        service = str(args.get('service') or '')
        known = RESTARTABLE.get(service)
        if not known:
            raise ValueError(f'"{service}" cannot be restarted from here. Choose one of: {", ".join(RESTARTABLE)}.')
        return {'title': f'Restart {known[0]}', 'detail': known[1], 'method': 'POST',
                'path': '/api/v1/system/services/restart', 'body': {'service': service}}
    if name == 'turn_on_automatic_backups':
        minutes = {'hour': 60, 'day': 1440, 'week': 10080}.get(str(args.get('every') or 'day'))
        if not minutes:
            raise ValueError('Backups can run every hour, day or week.')
        every = {60: 'every hour', 1440: 'every day', 10080: 'every week'}[minutes]
        return {'title': f'Make restore points automatically, {every}',
                'detail': 'AlvaOS keeps restore points of your shared folders and thins out old ones on its own. '
                          'You can change it on the Backup page.',
                'method': 'POST', 'path': '/api/v1/backup/settings',
                'body': {'pool_backup': {'enabled': True, 'interval_minutes': minutes}}}
    if name == 'update_app':
        wanted = str(args.get('app_id') or '').strip()
        _, data = read('/api/v1/apps/installed')
        app = next((a for a in _find(data, 'apps', 'installed') if wanted and wanted in
                    (str(a.get('app_id')), str(a.get('name')))), None)
        if not app or not re_container.match(str(app.get('app_id') or '')):
            raise ValueError(f'There is no installed app "{wanted}".')
        return {'title': f'Update the app "{app.get("name") or app.get("app_id")}"',
                'detail': 'The newest version is downloaded and the app restarts. Its data and settings stay.',
                'method': 'POST', 'path': f'/api/v1/apps/{app.get("app_id")}/update', 'body': {}}
    if name == 'install_alvaos_update':
        _, data = read('/api/v1/updates/alvaos/check')
        data = data if isinstance(data, dict) else {}
        assets = {str(a.get('name')): str(a.get('browser_download_url') or '')
                  for a in _find((data.get('release') or {}), 'assets')}
        deb = next((n for n in assets if n.endswith('.deb') and f'{n}.sig' in assets
                    and assets[n].startswith('https://')), None)
        if not data.get('update_available') or not deb:
            raise ValueError('No newer signed AlvaOS version is available.')
        version = str(data.get('latest_version') or '')
        return {'title': f'Install AlvaOS {version}',
                'detail': 'The update is downloaded, its signature checked and it is installed. The web page is '
                          'away for a minute or two; if anything fails, AlvaOS goes back on its own.',
                'method': 'POST', 'path': '/api/v1/updates/alvaos/apply', 'body': {'url': assets[deb],
                                                                                    'version': version}}
    if name == 'set_disk_sleep':
        try:
            minutes = int(str(args.get('minutes')))
        except (TypeError, ValueError):
            minutes = -1
        if minutes not in (0, 10, 20, 30, 60):
            raise ValueError('Disks can sleep after 10, 20, 30 or 60 minutes, or never (0).')
        return {'title': 'Never let hard disks sleep' if minutes == 0 else
                f'Let hard disks sleep after {minutes} minutes without use',
                'detail': 'Applies to the spinning data disks; SSDs and the system disk are left alone.',
                'method': 'POST', 'path': '/api/v1/storage/disk-power', 'body': {'spindown_minutes': minutes}}
    if name == 'create_shared_folder':
        folder = str(args.get('name') or '').strip()
        if not re.match(r'^[A-Za-z0-9_-]{1,63}$', folder):
            raise ValueError('Folder names use letters, numbers, "-" and "_", no spaces (up to 63 characters).')
        _, shares = read('/api/v1/storage/shares')
        if any(str(s.get('name') or '').lower() == folder.lower() for s in _find(shares, 'shares')):
            raise ValueError(f'There is already a shared folder "{folder}".')
        pool = _pool(args.get('pool_id'), read)
        everyone = args.get('everyone') is True
        people = _people(args.get('people'), read)
        if not everyone and not people:
            raise ValueError('Say who may open the folder: some people, or everyone on the home network.')
        who = 'everyone on your home network' if everyone else \
            (', '.join(people[:-1]) + ' and ' + people[-1] if len(people) > 1 else people[0])
        return {'title': f'Make the shared folder "{folder}" on "{pool.get("name") or pool.get("id")}"',
                'detail': f'A new, empty folder that {who} can open and change from their computers.',
                'method': 'POST', 'path': '/api/v1/storage/shares',
                'body': {'name': folder, 'protocol': 'smb', 'pool_id': pool.get('id'), 'folder': folder,
                         'new_folder': True, 'guest_access': everyone,
                         'smb_permissions': {p: 'write' for p in people}}}
    if name == 'set_folder_access':
        share = _share(args.get('folder'), read)
        person = (_people([args.get('person')], read) or [''])[0]
        access = str(args.get('access') or '')
        if not person:
            raise ValueError(f'There is no person "{args.get("person")}".')
        if access not in ('read', 'write', 'none'):
            raise ValueError('Access is read, write or none.')
        if share.get('protocol') != 'smb':
            raise ValueError('Access per person is only for folders shared with Windows/Mac file sharing (SMB).')
        perms = {str(k): str(v) for k, v in (share.get('smb_permissions') or {}).items()}
        perms.pop(person, None)
        if access != 'none':
            perms[person] = access
        if not share.get('guest_access') and not any(v in ('read', 'write') for v in perms.values()):
            raise ValueError('Then nobody could open the folder any more; give someone else access first.')
        what = {'read': 'may open and read', 'write': 'may open and change', 'none': 'can no longer open'}[access]
        return {'title': f'{person} {what} "{share.get("name")}"',
                'detail': 'Changes who can open the folder over the network and in AlvaOS Files.',
                'method': 'PUT', 'path': '/api/v1/storage/shares/permissions',
                'body': {'share_id': share.get('id'), 'smb_permissions': perms,
                         'guest_access': bool(share.get('guest_access'))}}
    if name == 'set_folder_limit':
        share = _share(args.get('folder'), read)
        try:
            gb = float(str(args.get('gigabytes')))
        except (TypeError, ValueError):
            gb = -1
        if gb < 0 or gb > 1_000_000:
            raise ValueError('Give the limit in GB, or 0 for no limit.')
        size = f'{gb:g} GB'
        return {'title': f'No space limit for "{share.get("name")}"' if gb == 0 else
                f'Limit "{share.get("name")}" to {size}',
                'detail': 'Files already there stay. When the limit is reached, nothing more can be added.'
                          if gb else 'The folder can use all free space of its pool.',
                'method': 'PUT', 'path': '/api/v1/storage/shares/quota',
                'body': {'share_id': share.get('id'), 'limit_gb': gb}}
    if name == 'turn_on_files_app':
        return {'title': 'Turn on AlvaOS Hub',
                'detail': 'The people can open their folders and photos in the browser, upload and share links. '
                          'You can turn it off again on the Hub page.',
                'method': 'POST', 'path': '/api/v1/files-app', 'body': {'enabled': True}}
    if name == 'copy_to_backup_disk':
        _, data = read('/api/v1/backup/copy')
        data = data if isinstance(data, dict) else {}
        if not data.get('enabled'):
            raise ValueError('There is no backup disk yet; choose one on the Backup page.')
        if not data.get('connected'):
            raise ValueError('The backup disk is not connected.')
        return {'title': 'Copy to the backup disk now',
                'detail': 'The newest restore points are copied to the USB disk. Leave it connected until '
                          'the Backup page says the copy is done.',
                'method': 'POST', 'path': '/api/v1/backup/copy/run', 'body': {}}
    raise ValueError(f'There is no action called {name}.')


def _pool(wanted: Any, read: Callable[[str], Tuple[int, Any]]) -> Dict[str, Any]:
    wanted = str(wanted or '').strip()
    _, data = read('/api/v1/storage/pools')
    pool = next((p for p in _find(data, 'pools') if wanted and wanted in (str(p.get('id')), str(p.get('name')))),
                None)
    if not pool or pool.get('is_system_pool'):
        raise ValueError(f'There is no storage pool "{wanted}".')
    return pool


def _share(wanted: Any, read: Callable[[str], Tuple[int, Any]]) -> Dict[str, Any]:
    wanted = str(wanted or '').strip().lower()
    _, data = read('/api/v1/storage/shares')
    share = next((s for s in _find(data, 'shares') if wanted and wanted in
                  (str(s.get('name') or '').lower(), str(s.get('id') or '').lower())), None)
    if not share:
        raise ValueError(f'There is no shared folder "{wanted}".')
    return share


def _people(wanted: Any, read: Callable[[str], Tuple[int, Any]]) -> List[str]:
    names = [str(n).strip() for n in (wanted if isinstance(wanted, list) else []) if str(n or '').strip()]
    if not names:
        return []
    _, data = read('/api/v1/users')
    known = {str(u.get('username') or u.get('name') or ''): u for u in _find(data, 'users')}
    lower = {k.lower(): k for k in known if k}
    unknown = [n for n in names if n.lower() not in lower]
    if unknown:
        raise ValueError(f'There is no person called {", ".join(unknown)}.')
    return sorted({lower[n.lower()] for n in names})


re_container = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')

ASK_PROMPT = (
    ' You may also propose changes with the action tools. A proposal does not run: the person sees it in the '
    'chat and has to confirm it. Propose only what they asked for or what clearly fixes a problem you found, one at a '
    'time, and say in a sentence what it will do. Never say it is done.'
)

SYSTEM_PROMPT = (
    'You are the assistant built into AlvaOS, a home NAS. Answer in the language the person writes in, '
    'short and in plain words, for someone who is not a technician. Use the tools to look at the real state '
    'of this NAS before you answer questions about it; do not guess numbers. When something should be changed '
    'by hand, link only to a real page listed below. To find out what is wrong you may run the read-only '
    'diagnostic commands of the run_command tool (disk space, memory, processes, network, service status, ping); '
    'say what you found, not the raw output.  Describe only controls that exist on that page; never invent '
    'buttons, fields, tabs, menu items, or steps. If you do not know the control, say what you know and ask the '
    'person to describe what is on their screen. The actual pages and their controls are: Dashboard: system health, '
    'memory, storage summary, alerts and shortcuts. Storage: Pools (create/import/check/expand/remove pools), Disks '
    '(disk health and actions), Shares (shared folders and access), Users (people and personal folders). Apps: '
    'Installed, App Store, app settings/logs and container terminal. Backup: Data (restore points, schedules, USB '
    'copy), System (system restore points), Buddy (second NAS). Updates: AlvaOS and package updates. Settings: '
    'Network, Time, Security (password, two-step sign-in, HTTPS, SSH, sessions), Notifications, Power, Graphics, '
    'Remote access, Assistant, Diagnostics, Terminal (a shell for the admin). Virtual machines: machine setup, create, settings, start/stop and console. '
    'Hub: Files, Photos, Calendar and Chat as enabled by the owner. If the current assistant mode is read-only, '
    'state that changes are disabled and point to Settings > Assistant to enable confirmed proposals. In confirmed '
    'proposal mode, people and shared folders can be proposed with the available actions; present them as proposals '
    'and never claim they have happened before confirmation. Never ask for passwords.'
)
READ_PROMPT = ' You can only look, not change anything.'


# ── Settings ─────────────────────────────────────────────────────────────────

def load_settings(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or SETTINGS_FILE
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    provider = str(raw.get('provider') or '')
    provider = provider if provider in PROVIDERS else 'ollama-local'
    return {
        'enabled': bool(raw.get('enabled')),
        'provider': provider,
        'base_url': str(raw.get('base_url') or PROVIDERS[provider]['base_url']),
        'model': str(raw.get('model') or PROVIDERS[provider]['model']),
        'api_key': str(raw.get('api_key') or ''),
        'level': raw.get('level') if raw.get('level') in LEVELS else 'read',
    }


def public_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in settings.items() if k != 'api_key'}
    out['key_set'] = bool(settings.get('api_key'))
    out['providers'] = {k: {kk: vv for kk, vv in v.items()} for k, v in PROVIDERS.items()}
    return out


def check_url(url: str) -> bool:
    return bool(re.match(r'^https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~/-]*)?$', url or ''))


def save_settings(current: Dict[str, Any], payload: Dict[str, Any],
                  path: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    path = path or SETTINGS_FILE
    settings = dict(current)
    if 'provider' in payload:
        if payload['provider'] not in PROVIDERS:
            return current, 'Unknown provider.'
        if payload['provider'] != settings['provider']:
            settings['base_url'] = PROVIDERS[payload['provider']]['base_url']
            settings['model'] = PROVIDERS[payload['provider']]['model']
        settings['provider'] = payload['provider']
    for key in ('base_url', 'model'):
        if key in payload:
            settings[key] = str(payload.get(key) or '').strip()
    if payload.get('api_key'):
        settings['api_key'] = str(payload['api_key']).strip()
    if payload.get('clear_key'):
        settings['api_key'] = ''
    if 'enabled' in payload:
        settings['enabled'] = bool(payload['enabled'])
    if 'level' in payload:
        if payload['level'] not in LEVELS:
            return current, 'Choose what the assistant may do.'
        settings['level'] = payload['level']
    if settings['enabled']:
        if not check_url(settings['base_url']):
            return current, 'Enter the address of the service, like http://192.168.1.20:11434/v1.'
        if not re.match(r'^[A-Za-z0-9._:/@+-]{1,120}$', settings['model'] or ''):
            return current, 'Enter the name of the model.'
        if PROVIDERS[settings['provider']]['needs_key'] and not settings['api_key']:
            return current, 'This service needs an API key.'
    ensure_directories()
    tmp = f'{path}.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(settings, f)
    os.replace(tmp, path)
    return settings, ''


# ── Talking to the model ─────────────────────────────────────────────────────

def tool_specs(level: str = 'read') -> List[Dict[str, Any]]:
    specs = [{'type': 'function', 'function': {'name': name, 'description': description,
                                               'parameters': {'type': 'object', 'properties': {}}}}
             for name, _, description in TOOLS]
    specs += [{'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': {arg: {'type': 'string', 'description': arg_desc}},
                       'required': [arg]}}}
        for name, (description, arg, arg_desc, _, _) in PARAM_TOOLS.items()]
    specs.append({'type': 'function', 'function': {
        'name': 'run_command',
        'description': 'Run one read-only diagnostic command on the NAS, like an admin in a terminal, and get its '
                       'output. The person sees which command ran. Commands: ' + '; '.join(
                           f'{n}: {d}' + (f' (value: {p[0]})' if p else '') for n, (d, _, p) in COMMANDS.items()),
        'parameters': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'enum': list(COMMANDS)},
            'value': {'type': 'string', 'description': 'Only for commands that need a value (service or host).'}},
            'required': ['name']}}})
    if level == 'ask':
        specs += [{'type': 'function', 'function': {
            'name': name, 'description': 'PROPOSE (the person confirms): ' + spec['description'],
            'parameters': {'type': 'object', 'properties': spec['parameters'],
                           'required': spec.get('required', list(spec['parameters']))}}}
            for name, spec in ACTION_SPECS.items()]
    return specs


def call_model(settings: Dict[str, Any], messages: List[Dict[str, Any]], post: Optional[Callable] = None,
               use_tools: bool = True) -> Dict[str, Any]:
    """One chat completion. Returns the assistant message."""
    if post is None:
        import requests
        post = requests.post
    headers = {'Content-Type': 'application/json'}
    if settings.get('api_key'):
        headers['Authorization'] = f"Bearer {settings['api_key']}"
    body: Dict[str, Any] = {'model': settings['model'], 'messages': messages, 'temperature': 0.2}
    if use_tools:
        body['tools'] = tool_specs(settings.get('level', 'read'))
    url = settings['base_url'].rstrip('/') + '/chat/completions'
    try:
        res = post(url, headers=headers, json=body, timeout=180)
    except Exception as e:  # noqa: BLE001 - requests raises many kinds
        kind = type(e).__name__
        if 'Timeout' in kind:
            raise RuntimeError('The AI service took too long to answer. A smaller model answers faster.') from e
        if 'Connection' in kind:
            raise RuntimeError(f'AlvaOS cannot reach the AI service at {settings["base_url"]}. Is it running, and '
                               'reachable from the NAS? (For Ollama on another computer: OLLAMA_HOST=0.0.0.0)') from e
        raise
    if getattr(res, 'status_code', 200) >= 400:
        detail = ''
        try:
            detail = str((res.json().get('error') or {}).get('message') or '')
        except Exception:  # noqa: BLE001 - any body
            detail = str(getattr(res, 'text', ''))[:200]
        if res.status_code == 401:
            raise RuntimeError('The AI service does not accept the API key. Check it in Settings › Assistant.')
        if res.status_code == 404 and 'model' in detail.lower():
            raise RuntimeError(f'The AI service does not know the model "{settings["model"]}". '
                               f'(For Ollama: ollama pull {settings["model"]})')
        raise RuntimeError(f'The AI service answered {res.status_code}{": " + detail if detail else ""}')
    data = res.json()
    message = ((data.get('choices') or [{}])[0] or {}).get('message') or {}
    if not isinstance(message, dict):
        raise RuntimeError('The AI service gave an answer AlvaOS does not understand.')
    return message


SECRET_KEY = re.compile(r'pass|secret|token|api_?key|private|otp|hash|salt|csrf|cookie', re.I)


def redact(data: Any) -> Any:
    """Drops anything that looks secret, in case an endpoint ever grows one."""
    if isinstance(data, dict):
        return {k: ('(hidden)' if SECRET_KEY.search(str(k)) else redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


SECRET_TEXT = [
    (re.compile(r'(?i)\bbearer\s+\S+'), 'Bearer (hidden)'),
    (re.compile(r'(?i)\b(pass(?:word|wd)?|secret|token|api[_-]?key|auth(?:orization)?|cookie|session)'
                r'(\s*[=:]\s*|\s+)("[^"]*"|\'[^\']*\'|\S+)'), r'\1\2(hidden)'),
    (re.compile(r'[A-Za-z0-9+/_-]{32,}={0,2}'), '(hidden)'),     # long keys and tokens
    (re.compile(r'://[^/\s:@]+:[^/\s@]+@'), '://(hidden)@'),     # user:password@ in addresses
]


def mask_text(data: Any) -> Any:
    """Masks what looks like a password, key or token inside log text."""
    if isinstance(data, str):
        for pattern, replacement in SECRET_TEXT:
            data = pattern.sub(replacement, data)
        return data
    if isinstance(data, dict):
        return {k: mask_text(v) for k, v in data.items()}
    if isinstance(data, list):
        return [mask_text(v) for v in data]
    return data


def run_tool(name: str, read: Callable[[str], Tuple[int, Any]], args: Optional[Dict[str, Any]] = None) -> str:
    path = TOOL_PATHS.get(name)
    if name in PARAM_TOOLS:
        _, arg, _, allowed, template = PARAM_TOOLS[name]
        value = str((args or {}).get(arg) or '').strip()
        if not allowed.match(value):
            return json.dumps({'error': f'Give {arg} as it appears in the list.'})
        path = template.format(value)
    if not path:
        return json.dumps({'error': f'There is no tool called {name}.'})
    status, data = read(path)
    data = redact(data)
    if name in LOG_TOOLS:
        data = mask_text(data)
    text = json.dumps(data, default=str, ensure_ascii=False)
    if status >= 400:
        return json.dumps({'error': f'{path} answered {status}', 'detail': text[:500]})
    return text if len(text) <= MAX_TOOL_CHARS else text[:MAX_TOOL_CHARS] + ' …(cut)'


def clean_history(history: Any) -> List[Dict[str, str]]:
    out = []
    for item in history if isinstance(history, list) else []:
        if isinstance(item, dict) and item.get('role') in ('user', 'assistant') and isinstance(item.get('content'), str):
            out.append({'role': item['role'], 'content': item['content'][:8000]})
    return out[-MAX_HISTORY:]


def _args(function: Dict[str, Any]) -> Dict[str, Any]:
    raw = function.get('arguments') or '{}'
    try:
        args = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return {}
    return args if isinstance(args, dict) else {}


def calls_in_text(content: Any) -> List[Dict[str, Any]]:
    """Small local models sometimes write a tool call as JSON text instead of
    making one. Read it as a call when it names one of the tools."""
    text = str(content or '').strip()
    fenced = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.S)
    text = fenced.group(1) if fenced else text
    if not text.startswith(('{', '[')):
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    known = set(TOOL_PATHS) | set(PARAM_TOOLS) | set(ACTION_SPECS) | {'run_command'}
    calls = []
    for i, item in enumerate(data if isinstance(data, list) else [data]):
        if not isinstance(item, dict):
            continue
        inner = item.get('function')
        call: Dict[str, Any] = inner if isinstance(inner, dict) else item
        name = str(call.get('name') or '')
        args = call.get('arguments', call.get('parameters', {}))
        if name in known:
            calls.append({'id': f'text-{i}', 'type': 'function',
                          'function': {'name': name, 'arguments': json.dumps(args if isinstance(args, dict) else {})}})
    return calls


# The pages the assistant can point to; links to anything else are not shown.
PAGES = {
    'index.html': 'Dashboard', 'storage.html': 'Storage', 'files.html': 'Hub', 'apps.html': 'Apps',
    'backup.html': 'Backup', 'updates.html': 'Updates', 'system.html': 'Settings', 'vms.html': 'Virtual machines',
}
LINK_PROMPT = (
    ' When the person should do something by hand, link the page like [Backup](backup.html). Pages: '
    'index.html (Dashboard), storage.html#pools, storage.html#disks, storage.html#shares (shared folders), '
    'storage.html#users (people), files.html (AlvaOS Hub: Files, Photos, Calendar, Chat), apps.html, vms.html (virtual machines), backup.html, updates.html, system.html#network, '
    'system.html#remote (remote access), system.html#graphics (graphics cards and drivers), '
    'system.html#security, system.html#alerts, system.html#power, system.html#assistant, system.html#logs, '
    'system.html#terminal (a command line on the NAS).'
)


def chat(settings: Dict[str, Any], history: Any, read: Callable[[str], Tuple[int, Any]],
         post: Optional[Callable] = None, page: str = '',
         run: Optional[Callable[[List[str]], Tuple[int, str]]] = None) -> Dict[str, Any]:
    """Answer the last question. Returns {'reply', 'looked_at', 'proposals'};
    proposals (at the "ask" level) are changes the person still has to confirm."""
    level = settings.get('level', 'read')
    prompt = SYSTEM_PROMPT + (ASK_PROMPT if level == 'ask' else READ_PROMPT) + LINK_PROMPT
    if page in PAGES:
        prompt += f' The person has the {PAGES[page]} page open.'
    messages: List[Dict[str, Any]] = [{'role': 'system', 'content': prompt}] + clean_history(history)
    if len(messages) < 2 or messages[-1]['role'] != 'user':
        raise ValueError('Ask something first.')
    looked_at: List[str] = []
    ran: List[str] = []
    proposals: List[Dict[str, Any]] = []

    def answer(message: Dict[str, Any]) -> Dict[str, Any]:
        return {'reply': str(message.get('content') or '').strip() or '(No answer.)', 'looked_at': looked_at,
                'ran': ran, 'proposals': proposals}

    for _ in range(MAX_ROUNDS):
        message = call_model(settings, messages, post)
        calls = message.get('tool_calls') or calls_in_text(message.get('content'))
        if not calls:
            return answer(message)
        messages.append({'role': 'assistant', 'content': message.get('content') or '', 'tool_calls': calls})
        for call in calls[:8]:
            function = (call or {}).get('function') or {}
            name = str(function.get('name') or '')
            call_id = str((call or {}).get('id') or name)
            if name in ACTION_SPECS:
                if level != 'ask':
                    result = {'error': 'You can only look, not change anything.'}
                elif len(proposals) >= 3:
                    result = {'error': 'Enough proposals for now; let the person decide first.'}
                else:
                    try:
                        proposals.append(build_action(name, _args(function), read))
                        result = {'proposed': proposals[-1]['title'],
                                  'note': 'Shown to the person; it runs only if they confirm.'}
                    except (ValueError, TypeError) as e:
                        result = {'error': str(e)}
                messages.append({'role': 'tool', 'tool_call_id': call_id, 'content': json.dumps(result)})
                continue
            if name == 'run_command':
                shown, result_text = run_command_tool(_args(function), run or _run_command)
                if shown:
                    ran.append(shown)
                messages.append({'role': 'tool', 'tool_call_id': call_id, 'content': result_text})
                continue
            looked_at.append(name)
            messages.append({'role': 'tool', 'tool_call_id': call_id,
                             'content': run_tool(name, read, _args(function))})
    message = call_model(settings, messages + [{'role': 'user', 'content': 'Answer now with what you found.'}],
                         post, use_tools=False)
    return answer(message)
