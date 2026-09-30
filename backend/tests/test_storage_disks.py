"""Disk roles and the rules that keep disks with data safe (storage_manager.py, api_storage.py).

Every disk gets one role. Only a disk with leftover data may be erased, only an
empty disk may go into a pool, and removing a pool keeps its data unless the
user asks for the disks to be erased.
"""

import platform

import pytest
from flask import Flask

import api_storage
import auth_manager
import storage_manager as sm


def disk(name, children=(), **fields):
    node = {'name': name, 'type': 'disk', 'size': '4T', 'model': 'WDC WD40EFRX', 'serial': 'WD-1',
            'tran': 'sata', 'rm': False, 'mountpoint': None, 'fstype': None}
    node.update(fields)
    if children:
        node['children'] = list(children)
    return node


def part(name, **fields):
    node = {'name': name, 'type': 'part', 'size': '1T', 'mountpoint': None, 'fstype': None}
    node.update(fields)
    return node


def roles(devices, pools=(), system=()):
    return {d['name']: d['usage'] for d in sm.describe_disks(list(devices), list(pools), set(system))}


# ── Roles ────────────────────────────────────────────────────────────────────

def test_an_empty_disk_is_ready_for_a_pool():
    usage = roles([disk('sdb')])['sdb']
    assert usage['role'] == 'empty'
    assert usage['can_add_to_pool'] and not usage['can_erase']


def test_the_disk_that_runs_the_os_is_the_system_disk():
    usage = roles([disk('sda', [part('sda1', mountpoint='/boot/efi', fstype='vfat'),
                                part('sda2', mountpoint='/', fstype='btrfs')])])['sda']
    assert usage['role'] == 'system'
    assert not usage['can_erase'] and not usage['can_add_to_pool']


def test_the_second_leg_of_a_mirrored_system_is_a_system_disk_too():
    pools = [{'id': 'u1', 'name': 'system', 'devices': ['/dev/sda2', '/dev/sdb2'], 'is_system_pool': True}]
    assert roles([disk('sdb', [part('sdb2', fstype='btrfs')])], pools)['sdb']['role'] == 'system'
    assert roles([disk('nvme1n1')], system={'nvme1n1'})['nvme1n1']['role'] == 'system'


def test_every_member_of_an_alvaos_pool_shows_the_pool():
    pools = [{'id': 'u2', 'name': 'main', 'devices': ['/dev/sdb', '/dev/sdc'], 'is_managed': True,
              'mount_point': '/mnt/alvaos/main'}]
    found = roles([disk('sdb', fstype='btrfs', mountpoint='/mnt/alvaos/main'), disk('sdc', fstype='btrfs')], pools)
    for name in ('sdb', 'sdc'):  # sdc is not in /proc/mounts, but still in the pool
        assert found[name]['role'] == 'pool'
        assert found[name]['pool_name'] == 'main'
        assert not found[name]['can_erase']


def test_a_disk_mounted_outside_alvaos_is_in_use():
    found = roles([disk('sdd', [part('sdd1', fstype='ext4', mountpoint='/media/usb')]),
                   disk('sde', [part('sde1', fstype='swap', mountpoint='[SWAP]')])])
    assert found['sdd']['role'] == 'in_use' and '/media/usb' in found['sdd']['summary']
    assert found['sde']['role'] == 'in_use' and 'swap' in found['sde']['summary']
    assert not found['sdd']['can_erase'] and not found['sdd']['can_add_to_pool']


def test_a_pool_from_another_system_can_be_imported_or_erased():
    pools = [{'id': 'u3', 'name': 'old-nas', 'devices': ['/dev/sdf'], 'is_managed': False}]
    usage = roles([disk('sdf', fstype='btrfs')], pools)['sdf']
    assert usage['role'] == 'other_pool' and usage['pool_name'] == 'old-nas'
    assert usage['can_erase'] and not usage['can_add_to_pool']


def test_a_foreign_pool_mounted_through_another_member_is_in_use():
    pools = [{'id': 'u4', 'name': 'ext', 'devices': ['/dev/sdg', '/dev/sdh'], 'is_managed': False}]
    found = roles([disk('sdg', fstype='btrfs', mountpoint='/srv/ext'), disk('sdh', fstype='btrfs')], pools)
    assert found['sdh']['role'] == 'in_use'
    assert not found['sdh']['can_erase']


def test_old_partitions_or_filesystems_count_as_data():
    found = roles([disk('sdi', [part('sdi1', fstype='ntfs')]), disk('sdj', [part('sdj1')]),
                   disk('sdk', fstype='crypto_LUKS')])
    assert all(found[n]['role'] == 'has_data' and found[n]['can_erase'] for n in ('sdi', 'sdj', 'sdk'))
    assert 'ntfs' in found['sdi']['summary']
    assert not any(found[n]['can_add_to_pool'] for n in ('sdi', 'sdj', 'sdk'))


def test_virtual_devices_are_not_listed():
    devices = [disk(n) for n in ('zram0', 'loop0', 'sr0', 'nbd3', 'ram0', 'sdb')]
    devices.append({'name': 'md0', 'type': 'raid1'})
    assert list(roles(devices)) == ['sdb']


# ── API ──────────────────────────────────────────────────────────────────────

class Runner:
    """Records privileged commands instead of running them. umount takes a
    mount point off the mounted set unless the pool is busy."""

    def __init__(self, mounted):
        self.calls = []
        self.mounted = mounted
        self.busy = False

    def __call__(self, cmd, timeout=30, **_):
        self.calls.append(list(cmd))
        if cmd[0].endswith('/umount') and not self.busy:
            self.mounted.discard(cmd[-1])
        return None, None

    def ran(self, binary):
        return [c for c in self.calls if c[0].endswith(binary)]


@pytest.fixture
def api(tmp_path, monkeypatch):
    if platform.system() != 'Linux':
        pytest.skip('storage management runs on the NAS only')
    monkeypatch.setattr(auth_manager, 'SESSIONS_FILE', str(tmp_path / 'sessions.json'))
    monkeypatch.setattr(sm, 'POOLS_STATE_FILE', str(tmp_path / 'pools.json'))
    monkeypatch.setattr(sm, 'ensure_directories', lambda: None)
    mounted = {'/mnt/alvaos/main'}
    runner = Runner(mounted)
    monkeypatch.setattr(api_storage, 'run_sudo_command', runner)
    monkeypatch.setattr(api_storage, '_is_mounted', lambda path: path in mounted)
    monkeypatch.setattr(api_storage, 'detect_btrfs_pools', lambda: ([], None))
    monkeypatch.setattr(api_storage, 'load_shares_state', lambda: {})

    inventory = [
        disk('sda', [part('sda2', mountpoint='/', fstype='btrfs')]),
        disk('sdb', fstype='btrfs', mountpoint='/mnt/alvaos/main'),
        disk('sdc'),
        disk('sdd', [part('sdd1', fstype='ntfs')]),
        disk('sde', [part('sde1', fstype='ext4', mountpoint='/media/usb')]),
    ]
    pools = [{'id': 'u-main', 'name': 'main', 'devices': ['/dev/sdb'], 'is_managed': True,
              'mount_point': '/mnt/alvaos/main'}]
    monkeypatch.setattr(api_storage, 'disk_inventory', lambda: sm.describe_disks(inventory, pools))
    monkeypatch.setattr(sm, 'disk_inventory', lambda: sm.describe_disks(inventory, pools))
    sm.save_pools_state({'u-main': {'name': 'main', 'devices': ['/dev/sdb'], 'raid_level': 'single',
                                    'mount_point': '/mnt/alvaos/main'}})

    app = Flask(__name__)
    app.register_blueprint(api_storage.bp)
    token = auth_manager._create_session('root', role='admin')
    headers = {'Authorization': token, 'X-CSRF-Token': auth_manager.SESSIONS[token]['csrf_token']}
    client = app.test_client()
    yield client, headers, runner
    auth_manager.SESSIONS.pop(token, None)


@pytest.mark.parametrize('name, role', [('sda', 'system'), ('sdb', 'pool'), ('sde', 'in_use')])
def test_disks_in_use_are_never_erased(api, name, role):
    client, headers, runner = api
    response = client.post(f'/api/v1/storage/disks/{name}/wipe', headers=headers)
    assert response.status_code == 409
    assert response.get_json()['role'] == role
    assert runner.ran('wipefs') == [] and runner.ran('umount') == []


def test_an_empty_disk_has_nothing_to_erase(api):
    client, headers, runner = api
    response = client.post('/api/v1/storage/disks/sdc/wipe', headers=headers)
    assert response.status_code == 409
    assert runner.ran('wipefs') == []


def test_a_disk_with_old_data_is_erased_partitions_first(api):
    client, headers, runner = api
    response = client.post('/api/v1/storage/disks/sdd/wipe', headers=headers)
    assert response.status_code == 200, response.get_json()
    assert [c[-1] for c in runner.ran('wipefs')] == ['/dev/sdd1', '/dev/sdd']
    assert runner.ran('umount') == []


def test_an_unknown_disk_is_not_erased(api):
    client, headers, runner = api
    assert client.post('/api/v1/storage/disks/sdz/wipe', headers=headers).status_code == 404
    assert runner.ran('wipefs') == []


@pytest.mark.parametrize('devices', [['/dev/sdd'], ['/dev/sdb'], ['/dev/sde'], ['/dev/sda'],
                                     ['/dev/sdd1'], ['/dev/sdc', '/dev/sdc'], ['sdc'], []])
def test_pools_are_only_created_on_empty_disks(api, devices):
    client, headers, runner = api
    response = client.post('/api/v1/storage/pools', headers=headers,
                           json={'name': 'media', 'devices': devices, 'raid_level': 'single'})
    assert response.status_code in (400, 409), devices
    assert runner.ran('mkfs.btrfs') == []


def test_a_pool_is_created_on_an_empty_disk(api):
    client, headers, runner = api
    response = client.post('/api/v1/storage/pools', headers=headers,
                           json={'name': 'media', 'devices': ['/dev/sdc'], 'raid_level': 'single'})
    assert response.status_code == 200, response.get_json()
    assert runner.ran('mkfs.btrfs')[0][-1] == '/dev/sdc'


def test_pool_names_are_checked_and_unique(api):
    client, headers, runner = api
    for name in ('Media', '../etc', 'main'):
        response = client.post('/api/v1/storage/pools', headers=headers,
                               json={'name': name, 'devices': ['/dev/sdc'], 'raid_level': 'single'})
        assert response.status_code in (400, 409), name
    assert runner.ran('mkfs.btrfs') == []


def test_expanding_a_pool_needs_an_empty_disk(api):
    client, headers, runner = api
    response = client.post('/api/v1/storage/pools/u-main/expand', headers=headers, json={'devices': ['/dev/sdd']})
    assert response.status_code == 409
    assert not [c for c in runner.calls if 'add' in c]


def test_removing_a_pool_keeps_its_data_by_default(api):
    client, headers, runner = api
    response = client.delete('/api/v1/storage/pools', headers=headers, json={'pool_id': 'u-main'})
    body = response.get_json()
    assert response.status_code == 200, body
    assert body['erased'] is False and 'still on the disks' in body['message']
    assert runner.ran('umount') and runner.ran('wipefs') == []
    assert 'u-main' not in sm.load_pools_state()


def test_removing_a_pool_can_erase_its_disks(api):
    client, headers, runner = api
    response = client.delete('/api/v1/storage/pools', headers=headers, json={'pool_id': 'u-main', 'erase': True})
    assert response.status_code == 200
    assert response.get_json()['erased'] is True
    assert [c[-1] for c in runner.ran('wipefs')] == ['/dev/sdb']


def test_a_busy_pool_is_not_removed_or_erased(api):
    client, headers, runner = api
    runner.busy = True  # an app still has files open: umount does not take effect
    response = client.delete('/api/v1/storage/pools', headers=headers, json={'pool_id': 'u-main', 'erase': True})
    assert response.status_code == 409
    assert runner.ran('wipefs') == []
    assert 'u-main' in sm.load_pools_state()


def test_a_shared_pool_is_not_removed(api, monkeypatch):
    client, headers, runner = api
    monkeypatch.setattr(api_storage, 'load_shares_state',
                        lambda: {'s1': {'name': 'Media', 'path': '/mnt/alvaos/main/media'},
                                 's2': {'name': 'Other', 'path': '/mnt/alvaos/mainly'}})
    response = client.delete('/api/v1/storage/pools', headers=headers, json={'pool_id': 'u-main'})
    assert response.status_code == 409
    assert response.get_json()['shares'] == ['Media']
    assert runner.calls == []
