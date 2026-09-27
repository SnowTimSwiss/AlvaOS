"""Tests for the privilege helper policy (backend/priv_policy.py).

Two kinds of cases:
- the command lines the backend really sends must be allowed, otherwise a
  feature breaks at runtime;
- the classic ways to turn "run X as root" into "run anything as root" must
  be denied.
"""

import json
import os

import pytest

import priv_policy as p
from fakes import FakeSystem


SYS = FakeSystem()


def allowed(*argv, system=SYS):
    return p.validate(list(argv), system)


def denied(*argv, system=SYS):
    with pytest.raises(p.PolicyError):
        p.validate(list(argv), system)


# ── Shells and unknown binaries ──────────────────────────────────────────────

@pytest.mark.parametrize('argv', [
    ['/usr/bin/bash', '-lc', 'id'],
    ['/bin/sh', '-c', 'id'],
    ['/usr/bin/python3', '-c', 'print(1)'],
    ['/usr/bin/sed', '-i', 's/a/b/', '/etc/shadow'],
    ['/usr/bin/systemd-run', '/bin/sh'],
    ['/usr/bin/nohup', '/bin/sh'],
    ['/usr/bin/tee', '/etc/sudoers.d/evil'],
    ['btrfs', 'filesystem', 'show'],                 # relative path
    ['/tmp/btrfs', 'filesystem', 'show'],            # not a system dir
    ['/opt/alvaos/bin/btrfs', 'filesystem', 'show'],
])
def test_shells_and_unknown_binaries_are_denied(argv):
    denied(*argv)


def test_newlines_in_arguments_are_denied():
    denied('/usr/bin/hostnamectl', 'set-hostname', 'nas\nevil')


# ── Users and groups ─────────────────────────────────────────────────────────

def test_user_management_commands_used_by_the_backend():
    allowed('/usr/sbin/useradd', '-m', '-s', '/bin/bash', 'newuser')
    allowed('/usr/sbin/userdel', 'tim')
    allowed('/usr/bin/smbpasswd', '-a', '-s', 'tim')
    allowed('/usr/bin/smbpasswd', '-x', 'tim')
    allowed('/usr/sbin/groupadd', '-f', 'share_new')
    allowed('/usr/bin/gpasswd', '-a', 'tim', 'share_media')
    allowed('/usr/bin/gpasswd', '-d', 'nobody', 'share_media')


@pytest.mark.parametrize('argv', [
    ['/usr/sbin/userdel', 'root'],
    ['/usr/sbin/userdel', 'alvaos'],
    ['/usr/sbin/useradd', '-o', '-u', '0', 'evil'],
    ['/usr/sbin/useradd', '-m', '-s', '/bin/bash', 'root'],
    ['/usr/bin/gpasswd', '-a', 'tim', 'sudo'],
    ['/usr/bin/gpasswd', '-a', 'tim', 'docker'],
    ['/usr/sbin/groupdel', 'sudo'],
    ['/usr/bin/smbpasswd', '-a', '-s', 'root'],
])
def test_privileged_accounts_are_protected(argv):
    denied(*argv)


def test_chpasswd_input_is_checked():
    plan = allowed('/usr/sbin/chpasswd')
    plan.stdin_check(b'root:new-secret\n')
    plan.stdin_check(b'tim:abc\n')
    with pytest.raises(p.PolicyError):
        plan.stdin_check(b'alvaos:pw\n')
    with pytest.raises(p.PolicyError):
        plan.stdin_check(b'not a pair\n')


# ── Files and permissions ────────────────────────────────────────────────────

def test_share_permission_commands():
    allowed('/usr/bin/chgrp', '-R', 'share_media', '/mnt/alvaos/main/media')
    allowed('/usr/bin/chmod', '-R', '2770', '/mnt/alvaos/main/media')
    allowed('/usr/bin/chmod', '-R', '0777', '/mnt/alvaos/main/public')
    allowed('/usr/bin/chmod', '-R', 'u+rwX,g+rwX', '/var/lib/alvaos/buddy-streams/abc')
    allowed('/usr/bin/chown', '-R', '998:998', '/mnt/alvaos/main/.alvaos-buddy-streams/x')


@pytest.mark.parametrize('argv', [
    ['/usr/bin/chmod', '-R', '4755', '/mnt/alvaos/main/x'],       # setuid
    ['/usr/bin/chmod', '-R', 'u+s', '/mnt/alvaos/main/x'],
    ['/usr/bin/chmod', '-R', '0777', '/etc'],
    ['/usr/bin/chmod', '4755', '/usr/bin/find'],
    ['/usr/bin/chown', '-R', '0:0', '/mnt/alvaos/main/x'],       # to root
    ['/usr/bin/chown', '-R', 'tim:tim', '/etc/shadow'],
    ['/usr/bin/chown', '-R', 'tim', '/mnt/alvaos'],              # the root itself
    ['/usr/bin/chgrp', '-R', 'sudo', '/mnt/alvaos/main/x'],
    ['/usr/bin/chown', '-R', '998:998', '/mnt/alvaos/../../etc'],
    ['/usr/bin/mv', '/etc/passwd', '/mnt/alvaos/main/p'],
    ['/usr/bin/mkdir', '-p', '/etc/cron.d/x'],
])
def test_file_ops_outside_data_dirs_are_denied(argv):
    denied(*argv)


def test_symlinks_out_of_the_data_dirs_are_followed_and_denied():
    system = FakeSystem(links={'/mnt/alvaos/main/evil': '/etc'})
    denied('/usr/bin/chown', '-R', '998:998', '/mnt/alvaos/main/evil/sudoers.d', system=system)


def test_resolved_path_is_what_gets_executed():
    system = FakeSystem(links={'/mnt/alvaos/main/link': '/mnt/alvaos/main/real'})
    plan = allowed('/usr/bin/chmod', '-R', '2770', '/mnt/alvaos/main/link', system=system)
    assert plan.argv[-1] == '/mnt/alvaos/main/real'


# ── Config files ─────────────────────────────────────────────────────────────

DEBIAN_SMB_CONF = b"""[global]
   workgroup = WORKGROUP
   log file = /var/log/samba/log.%m
   panic action = /usr/share/samba/panic-action %d
   passwd program = /usr/bin/passwd %u
"""


def smb_plan(current=DEBIAN_SMB_CONF):
    system = FakeSystem(files={'/etc/samba/smb.conf': current})
    return allowed('/usr/bin/tee', '/etc/samba/smb.conf', system=system)


def test_smb_conf_keeps_existing_lines_and_accepts_normal_shares():
    plan = smb_plan()
    plan.stdin_check(DEBIAN_SMB_CONF + b"""
[media]
   path = /mnt/alvaos/main/media
   valid users = @share_media
   read only = no
   force group = share_media
   create mask = 0660
""")


@pytest.mark.parametrize('evil', [
    b'[x]\n   root preexec = /bin/sh -c "id > /tmp/pwn"\n',
    b'[x]\n   preexec = /tmp/evil\n',
    b'[x]\n   magic script = run.sh\n',
    b'[global]\n   add user script = /tmp/x %u\n',
    b'[global]\n   panic action = /tmp/evil\n',                 # changed value
    b'[global]\n   include = /mnt/alvaos/main/evil.conf\n',
    b'[root]\n   path = /\n   force user = root\n',
    b'[etc]\n   path = /etc\n',
    b'[x]\n   path = /mnt/alvaos/main/x\n   admin users = root\n',
])
def test_smb_conf_rejects_command_execution_and_root_shares(evil):
    with pytest.raises(p.PolicyError):
        smb_plan().stdin_check(DEBIAN_SMB_CONF + evil)


def test_tee_only_targets_managed_config_files():
    denied('/usr/bin/tee', '/etc/shadow')
    denied('/usr/bin/tee', '-a', '/etc/crontab')
    denied('/usr/bin/tee', '/etc/exports', '/etc/shadow')


def test_sshd_dropin_only_allows_known_options():
    plan = allowed('/usr/bin/tee', '/etc/ssh/sshd_config.d/00-alvaos-security.conf')
    plan.stdin_check(b'# AlvaOS\nPermitRootLogin yes\nPasswordAuthentication yes\nPermitEmptyPasswords no\n')
    with pytest.raises(p.PolicyError):
        plan.stdin_check(b'AuthorizedKeysCommand /tmp/x\nAuthorizedKeysCommandUser root\n')
    with pytest.raises(p.PolicyError):
        plan.stdin_check(b'ForceCommand /tmp/x\n')


def test_exports_only_share_data_dirs():
    plan = allowed('/usr/bin/tee', '-a', '/etc/exports')
    plan.stdin_check(b'/mnt/alvaos/main/media *(rw,sync,no_subtree_check)\n')
    with pytest.raises(p.PolicyError):
        plan.stdin_check(b'/ *(rw,no_root_squash)\n')


def test_hosts_file():
    plan = allowed('/usr/bin/tee', '/etc/hosts')
    plan.stdin_check(b'127.0.0.1\tlocalhost\n127.0.1.1\talvaos\n::1 localhost ip6-localhost\n')
    with pytest.raises(p.PolicyError):
        plan.stdin_check(b'127.0.0.1 $(id)\n')


def test_apt_sources_only_official_debian():
    plan = allowed('/usr/bin/tee', '/etc/apt/sources.list')
    plan.stdin_check(
        b'deb http://deb.debian.org/debian trixie main contrib non-free non-free-firmware\n'
        b'deb http://security.debian.org/debian-security trixie-security main\n'
    )
    with pytest.raises(p.PolicyError):
        plan.stdin_check(b'deb [trusted=yes] http://evil.example/debian trixie main\n')


def test_cat_reads_only_managed_files():
    allowed('/usr/bin/cat', '/etc/samba/smb.conf')
    denied('/usr/bin/cat', '/etc/shadow')


# ── Storage ──────────────────────────────────────────────────────────────────

def test_pool_lifecycle_commands():
    allowed('/usr/sbin/mkfs.btrfs', '-f', '-L', 'main', '-d', 'raid1', '-m', 'raid1', '/dev/sdb', '/dev/sdc')
    allowed('/usr/bin/mkdir', '-p', '/mnt/alvaos/main')
    allowed('/usr/bin/mount', '/dev/sdb', '/mnt/alvaos/main')
    allowed('/usr/bin/mount', '-U', '0b6c7d3e-1234-4d5e-8f90-abcdef012345', '/mnt/alvaos/main')
    allowed('/usr/bin/umount', '/mnt/alvaos/main')
    allowed('/usr/bin/umount', '-l', '/dev/sdb1')
    allowed('/usr/bin/rmdir', '/mnt/alvaos/main')
    allowed('/usr/sbin/wipefs', '-a', '-f', '/dev/sdb')
    allowed('/usr/sbin/wipefs', '-a', '/dev/sdb1')
    allowed('/usr/sbin/partprobe', '/dev/sdb')
    allowed('/usr/bin/btrfs', 'device', 'add', '/dev/sdd', '/mnt/alvaos/main')
    allowed('/usr/bin/btrfs', 'device', 'remove', 'missing', '/mnt/alvaos/main')
    allowed('/usr/bin/btrfs', 'balance', 'start', '-dconvert=raid1', '-mconvert=raid1', '/mnt/alvaos/main')
    allowed('/usr/sbin/smartctl', '-H', '-A', '-d', 'sat', '-j', '/dev/sdb')
    allowed('/usr/sbin/blkid', '-s', 'UUID', '-o', 'value', '/dev/sdb')
    allowed('/usr/bin/lsblk', '-nrpo', 'NAME,TYPE,MOUNTPOINT', '/dev/sdb')


@pytest.mark.parametrize('argv', [
    ['/usr/sbin/wipefs', '-a', '-f', '/dev/sda'],
    ['/usr/sbin/wipefs', '-a', '/dev/sda2'],
    ['/usr/sbin/mkfs.btrfs', '-f', '/dev/sdb', '/dev/sda'],
    ['/usr/bin/btrfs', 'device', 'add', '/dev/sda3', '/mnt/alvaos/main'],
    ['/usr/bin/mount', '/dev/sdb', '/etc'],
    ['/usr/bin/mount', '--bind', '/mnt/alvaos/main/x', '/etc'],
    ['/usr/bin/mount', '/dev/sdb', '/mnt/alvaos/main/nested'],
    ['/usr/bin/umount', '/'],
    ['/usr/bin/umount', '/boot'],
    ['/usr/sbin/mkfs.ext4', '/dev/sdb'],
])
def test_system_disk_and_mount_abuse_is_denied(argv):
    denied(*argv)


def test_nvme_system_disk_partitions_are_protected():
    system = FakeSystem(system_disks=('nvme0n1',))
    with pytest.raises(p.PolicyError):
        p.validate(['/usr/sbin/wipefs', '-a', '/dev/nvme0n1p2'], system)
    p.validate(['/usr/sbin/wipefs', '-a', '/dev/nvme1n1'], system)


def test_usb_scan_mount_is_forced_harmless():
    plan = allowed('/usr/bin/mount', '-o', 'ro', '/dev/sdc1', '/run/alvaos-scan/sdc1')
    assert plan.argv[1:3] == ['-o', 'ro,nosuid,nodev,noexec']
    assert plan.make_dirs == ['/run/alvaos-scan/sdc1']
    denied('/usr/bin/mount', '-o', 'ro', '/dev/sdc1', '/tmp/alvaos_scan_sdc1')
    denied('/usr/bin/mount', '-o', 'rw', '/dev/sdc1', '/run/alvaos-scan/sdc1')


def test_btrfs_snapshot_and_backup_commands():
    allowed('/usr/bin/btrfs', 'subvolume', 'create', '/mnt/alvaos/main/apps/jellyfin')
    allowed('/usr/bin/btrfs', 'subvolume', 'delete', '/mnt/alvaos/main/apps/jellyfin')
    allowed('/usr/bin/btrfs', 'subvolume', 'snapshot', '-r', '/', '/.alvaos-send-root-20260101')
    allowed('/usr/bin/btrfs', 'subvolume', 'snapshot', '-r', '/mnt/alvaos/main/media',
            '/mnt/alvaos/main/.snapshots/media-1')
    allowed('/usr/bin/btrfs', 'subvolume', 'delete', '/.alvaos-send-root-20260101')
    allowed('/usr/bin/btrfs', 'send', '-f', '/var/lib/alvaos/buddy-send-x.stream', '/.alvaos-send-root')
    allowed('/usr/bin/btrfs', 'send', '/mnt/alvaos/main/.alvaos-buddy-media-1')   # to stdout
    allowed('/usr/bin/btrfs', 'receive', '-f', '/var/lib/alvaos/x.stream', '/mnt/alvaos/main/restore')
    allowed('/usr/bin/btrfs', 'receive', '/mnt/alvaos/main/restore')   # from stdin
    allowed('/usr/bin/btrfs', 'subvolume', 'set-default', '256', '/')
    allowed('/usr/bin/btrfs', 'subvolume', 'list', '-o', '/mnt/alvaos/main')
    allowed('/usr/bin/btrfs', 'subvolume', 'show', '/')
    allowed('/usr/bin/btrfs', 'filesystem', 'show')
    allowed('/usr/bin/btrfs', 'filesystem', 'usage', '/mnt/alvaos/main')


@pytest.mark.parametrize('argv', [
    ['/usr/bin/btrfs', 'subvolume', 'delete', '/'],
    ['/usr/bin/btrfs', 'subvolume', 'delete', '/mnt/alvaos/main'],   # pool root
    ['/usr/bin/btrfs', 'subvolume', 'delete', '/home'],
    ['/usr/bin/btrfs', 'subvolume', 'snapshot', '/', '/etc/x'],
    ['/usr/bin/btrfs', 'receive', '-f', '/var/lib/alvaos/x', '/etc'],
    ['/usr/bin/btrfs', 'receive', '/etc'],
    ['/usr/bin/btrfs', 'receive', '-e', '/mnt/alvaos/main/x'],
    ['/usr/bin/btrfs', 'send', '-f', '/etc/x', '/'],
    ['/usr/bin/btrfs', 'send', '-p', '/x', '/y'],
    ['/usr/bin/btrfs', 'send', '../x'],
    ['/usr/bin/btrfs', 'property', 'set', '/', 'ro', 'true'],
    ['/usr/bin/btrfs', 'rescue', 'zero-log', '/dev/sda1'],
])
def test_btrfs_abuse_is_denied(argv):
    denied(*argv)


# ── System ───────────────────────────────────────────────────────────────────

def test_system_settings_commands():
    allowed('/usr/bin/hostnamectl', 'set-hostname', 'alvaos-nas')
    allowed('/usr/bin/timedatectl', 'set-timezone', 'Europe/Zurich')
    allowed('/usr/bin/timedatectl', 'set-ntp', 'true')
    allowed('/usr/bin/timedatectl', 'show', '--property=Timezone', '--value')
    allowed('/usr/sbin/reboot')
    allowed('/usr/sbin/poweroff')
    allowed('/usr/bin/systemctl', 'restart', 'smbd')
    allowed('/usr/bin/systemctl', 'restart', 'ssh')
    allowed('/usr/bin/tail', '-n', '50', '/var/log/syslog')
    allowed('/usr/bin/journalctl', '-n', '50', '--no-pager', '--output=short')
    allowed('/usr/sbin/exportfs', '-ra')
    allowed('/usr/bin/df', '-h', '/')
    allowed('/usr/bin/mountpoint', '/')


@pytest.mark.parametrize('argv', [
    ['/usr/bin/hostnamectl', 'set-hostname', '$(id)'],
    ['/usr/bin/timedatectl', 'set-timezone', '../../etc/shadow'],
    ['/usr/sbin/reboot', '--force'],
    ['/usr/bin/systemctl', 'start', 'evil.service'],
    ['/usr/bin/systemctl', 'enable', 'ssh'],
    ['/usr/bin/systemctl', 'edit', 'ssh'],
    ['/usr/bin/tail', '-n', '50', '/etc/shadow'],
    ['/usr/bin/journalctl', '--file', '/etc/shadow'],
])
def test_system_abuse_is_denied(argv):
    denied(*argv)


# ── Packages ─────────────────────────────────────────────────────────────────

def test_apt_commands_used_for_updates():
    allowed('/usr/bin/apt-get', '-o', 'Dpkg::Lock::Timeout=120', 'update')
    allowed('/usr/bin/apt-get', '-o', 'Dpkg::Lock::Timeout=120', 'upgrade', '-y')
    allowed('/usr/bin/apt-get', '-o', 'Dpkg::Lock::Timeout=120', 'install', '-y', 'samba', 'python3-pyotp')
    allowed('/usr/bin/apt-get', 'install', '-y', '--no-install-recommends', 'python3-pyotp')
    allowed('/usr/bin/apt-get', '-o', 'Dpkg::Lock::Timeout=120', 'install', '-f', '-y')
    plan = allowed(
        '/usr/bin/apt-get', '-o', 'Dpkg::Lock::Timeout=120', '--allow-releaseinfo-change',
        '-o', 'Dir::Etc::sourcelist=/var/lib/alvaos/updates/debian-upgrade-0123abcd.list',
        '-o', 'Dir::Etc::sourceparts=-', 'full-upgrade', '-y',
    )
    assert plan.stage == {4: 'apt-sources-option'}


@pytest.mark.parametrize('argv', [
    ['/usr/bin/apt-get', '-o', 'APT::Update::Pre-Invoke::=/bin/sh -c id', 'update'],
    ['/usr/bin/apt-get', '-o', 'Dpkg::Pre-Invoke::=id', 'install', 'x'],
    ['/usr/bin/apt-get', 'install', '-y', '/tmp/evil.deb'],
    ['/usr/bin/apt-get', 'install', '-y', './evil.deb'],
    ['/usr/bin/apt-get', 'source', 'bash'],
    ['/usr/bin/apt-get', '-o', 'Dir::Etc::sourcelist=/tmp/x.list', 'update'],
    ['/usr/bin/dpkg', '-i', '/tmp/evil.deb'],
    ['/usr/bin/dpkg', '--force-all', '-i', '/var/lib/alvaos/updates/x.deb'],
    ['/usr/bin/dpkg', '-i', '/var/lib/alvaos/updates/../../../tmp/x.deb'],
])
def test_package_abuse_is_denied(argv):
    denied(*argv)


def test_dpkg_only_installs_from_the_update_cache_and_is_staged():
    plan = allowed('/usr/bin/dpkg', '-i', '/var/lib/alvaos/updates/offline/tool.deb')
    assert plan.stage == {1: 'deb'}


# ── Docker ───────────────────────────────────────────────────────────────────

def test_docker_commands_used_by_the_backend():
    allowed('/usr/bin/docker', 'ps', '--format', '{{json .}}', '-a')
    allowed('/usr/bin/docker', 'inspect', 'abc123')
    allowed('/usr/bin/docker', 'image', 'inspect', 'nginx:latest', '--format', '{{json .RepoDigests}}')
    allowed('/usr/bin/docker', 'start', 'jellyfin')
    allowed('/usr/bin/docker', 'stop', '-t', '10', 'jellyfin')
    allowed('/usr/bin/docker', 'rm', '-f', 'jellyfin')
    allowed('/usr/bin/docker', 'logs', '--tail', '100', 'jellyfin')
    allowed('/usr/bin/docker', 'stats', '--no-stream', '--format', '{{json .}}', 'jellyfin')
    allowed('/usr/bin/docker', 'pull', 'ghcr.io/immich-app/immich-server:release')
    allowed('/usr/bin/docker', 'exec', '-u', 'www-data', 'nextcloud', '/bin/sh', '-lc', 'php occ status')


@pytest.mark.parametrize('argv', [
    ['/usr/bin/docker', 'run', '-v', '/:/host', 'alpine'],
    ['/usr/bin/docker', 'run', '--privileged', 'alpine'],
    ['/usr/bin/docker', 'create', 'alpine'],
    ['/usr/bin/docker', 'exec', '--privileged', 'x', '/bin/sh', '-lc', 'id'],
    ['/usr/bin/docker', 'cp', 'x:/etc/shadow', '/etc/shadow'],
    ['/usr/bin/docker', '-H', 'tcp://evil:2375', 'ps'],
    ['/usr/bin/docker', 'plugin', 'install', 'evil'],
])
def test_docker_abuse_is_denied(argv):
    denied(*argv)


def test_compose_only_from_compose_dir():
    plan = allowed('/usr/bin/docker-compose', '-f', '/var/lib/alvaos/compose/alvaos-jellyfin-x.yml',
                   '-p', 'alvaos-jellyfin', 'up', '-d')
    assert plan.stage == {1: 'compose'}
    allowed('/usr/bin/docker-compose', '-f', '/var/lib/alvaos/compose/a.yml', '-p', 'alvaos-a', 'pull')
    denied('/usr/bin/docker-compose', '-f', '/tmp/tmpabc.yml', '-p', 'x', 'up', '-d')
    denied('/usr/bin/docker-compose', '-f', '/var/lib/alvaos/compose/a.yml', '-p', 'x', 'run', 'svc', 'sh')
    denied('/usr/bin/docker-compose', '-f', '/var/lib/alvaos/compose/a.yml', '-p', 'x', 'exec', 'svc', 'sh')


def test_catalog_apps_pass_the_compose_check():
    """Every app in the store must still be installable under the policy."""
    import yaml

    catalog_path = os.path.join(os.path.dirname(__file__), '..', '..', 'apps', 'catalog.json')
    with open(catalog_path, encoding='utf-8') as f:
        catalog = json.load(f)
    for app in catalog.values():
        compose = json.loads(json.dumps(app['docker_compose']).replace('${POOL_PATH}', '/mnt/alvaos/main/apps/x'))
        p.check_compose(yaml.safe_dump(compose).encode())


@pytest.mark.parametrize('service', [
    {'image': 'alpine', 'privileged': True},
    {'image': 'alpine', 'volumes': ['/:/host']},
    {'image': 'alpine', 'volumes': ['/etc:/x:ro']},
    {'image': 'alpine', 'volumes': ['/var/run/docker.sock:/var/run/docker.sock']},
    {'image': 'alpine', 'volumes': ['/var/lib/alvaos:/state']},
    {'image': 'alpine', 'volumes': ['../../../../etc:/x']},
    {'image': 'alpine', 'volumes': [{'type': 'bind', 'source': '/root', 'target': '/x'}]},
    {'image': 'alpine', 'pid': 'host'},
    {'image': 'alpine', 'cap_add': ['SYS_ADMIN']},
    {'image': 'alpine', 'cap_add': ['ALL']},
    {'image': 'alpine', 'devices': ['/dev/sda:/dev/sda']},
    {'image': 'alpine', 'security_opt': ['apparmor:unconfined']},
])
def test_compose_escapes_are_denied(service):
    import yaml

    with pytest.raises(p.PolicyError):
        p.check_compose(yaml.safe_dump({'services': {'x': service}}).encode())


def test_compose_allows_normal_app_settings():
    import yaml

    p.check_compose(yaml.safe_dump({'services': {'jellyfin': {
        'image': 'jellyfin/jellyfin',
        'volumes': ['/mnt/alvaos/main/apps/jellyfin/config:/config', '/srv/media:/media:ro', 'cache:/cache'],
        'devices': ['/dev/dri:/dev/dri'],
        'cap_add': ['NET_ADMIN'],
    }}}).encode())


# ── WireGuard ────────────────────────────────────────────────────────────────

def test_wireguard_commands():
    plan = allowed('/usr/bin/wg-quick', 'up', '/var/lib/alvaos/wireguard/buddy0.conf')
    assert plan.stage == {1: 'wg'}
    allowed('/usr/bin/wg', 'show', 'buddy0')
    denied('/usr/bin/wg', 'genkey')   # keys are generated in-process now
    denied('/usr/bin/wg-quick', 'up', '/tmp/evil.conf')
    denied('/usr/bin/wg', 'set', 'buddy0', 'private-key', '/etc/shadow')


def test_wg_config_rejects_hooks():
    good = (b'[Interface]\nPrivateKey = abc=\nAddress = 10.0.0.1/24\nListenPort = 51820\n\n'
            b'[Peer]\nPublicKey = def=\nAllowedIPs = 10.0.0.2/32\nEndpoint = 1.2.3.4:51820\n'
            b'PersistentKeepalive = 25\n')
    p.check_wg_config(good)
    for hook in (b'PostUp = id > /tmp/x\n', b'PreUp = id\n', b'PostDown = id\n', b'SaveConfig = true\n'):
        with pytest.raises(p.PolicyError):
            p.check_wg_config(good + hook)


# ── Named operations and environment ─────────────────────────────────────────

def test_sysfs_writes_only_battery_thresholds():
    p.validate_sysfs_write('/sys/class/power_supply/BAT0/charge_control_end_threshold', '80')
    with pytest.raises(p.PolicyError):
        p.validate_sysfs_write('/sys/kernel/uevent_helper', '/tmp/x')
    with pytest.raises(p.PolicyError):
        p.validate_sysfs_write('/sys/class/power_supply/BAT0/charge_control_end_threshold', '80\n')


def test_update_package_must_come_from_cache():
    assert p.validate_update_package('/var/lib/alvaos/updates/alvaos.deb', SYS)
    with pytest.raises(p.PolicyError):
        p.validate_update_package('/tmp/alvaos.deb', SYS)


def test_only_known_environment_is_forwarded():
    assert p.validate_env(['DEBIAN_FRONTEND=noninteractive']) == {'DEBIAN_FRONTEND': 'noninteractive'}
    for bad in ('LD_PRELOAD=/tmp/x.so', 'PATH=/tmp', 'DEBIAN_FRONTEND=readline', 'PYTHONPATH=/tmp'):
        with pytest.raises(p.PolicyError):
            p.validate_env([bad])
