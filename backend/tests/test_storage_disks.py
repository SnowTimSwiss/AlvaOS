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


# ── btrfs status output ──────────────────────────────────────────────────────

SHOW_DEGRADED = """Label: 'main'  uuid: 0b6c7d3e-1234-4d5e-8f90-abcdef012345
	Total devices 2 FS bytes used 1.20TiB
	devid    1 size 3.64TiB used 1.21TiB path /dev/sdb
	devid    2 size 3.64TiB used 1.21TiB path <missing disk> MISSING
"""

SHOW_UNMOUNTED_MISSING = """Label: 'tpool'  uuid: a2ec8266-1864-41b3-a23b-8012f6a9bd9b
	Total devices 2 FS bytes used 144.00KiB
	devid    1 size 1.00GiB used 212.75MiB path /dev/loop0
	*** Some devices missing
"""


def test_pool_members_include_a_missing_disk():
    members = sm.parse_show_members(SHOW_DEGRADED)
    assert [(m['devid'], m['path'], m['missing']) for m in members] == [(1, '/dev/sdb', False), (2, '', True)]
    assert members[0]['size_bytes'] == int(3.64 * 1024 ** 4)


def test_pool_detection_counts_missing_disks(monkeypatch):
    class Res:
        returncode = 0
        stdout = SHOW_UNMOUNTED_MISSING

    monkeypatch.setattr(sm.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(sm, 'run_sudo_command', lambda cmd, **kw: (Res(), None) if cmd[-1] == 'show' else (None, 'x'))
    pools, _ = sm.detect_btrfs_pools()
    assert pools[0]['devices'] == ['/dev/loop0']
    assert pools[0]['missing_count'] == 1
    assert pools[0]['status'] == 'degraded'


def test_scrub_status():
    assert sm.parse_scrub_status('UUID: x\n\tno stats available\n') == {'state': 'never'}
    running = sm.parse_scrub_status(
        'UUID:             x\nScrub started:    Tue Sep 30 18:00:00 2026\nStatus:           running\n'
        'Duration:         0:01:05\nTime left:        0:02:00\nTotal to scrub:   100.00GiB\n'
        'Bytes scrubbed:   30.00GiB  (30.00%)\nRate:             409.60MiB/s\nError summary:    no errors found\n')
    assert running['state'] == 'running' and running['percent'] == 30.0 and running['errors'] == 0
    assert running['time_left'] == '0:02:00'
    found = sm.parse_scrub_status(
        'UUID: x\nScrub started:    Tue Sep 30 18:00:00 2026\nStatus:           finished\nDuration: 1:00:00\n'
        'Error summary:    csum=12 verify=1\n  Corrected:      13\n  Uncorrectable:  0\n  Unverified:     0\n')
    assert found['state'] == 'finished' and found['errors'] == 13 and found['uncorrectable'] == 0


def test_replace_status():
    assert sm.parse_replace_status('Never started')['state'] == 'never'
    running = sm.parse_replace_status('12.3% done, 0 write errs, 0 uncorr. read errs')
    assert running['state'] == 'running' and running['percent'] == 12.3
    done = sm.parse_replace_status('Started on 30.Sep 18:00:00, finished on 30.Sep 21:00:00, '
                                   '1 write errs, 2 uncorr. read errs')
    assert done['state'] == 'finished' and done['errors'] == 3
    assert sm.parse_replace_status('Started on 30.Sep, canceled on 30.Sep at 5.0%, '
                                   '0 write errs, 0 uncorr. read errs')['state'] == 'canceled'


def test_balance_status_and_device_stats():
    assert sm.parse_balance_status("No balance found on '/mnt/alvaos/main'") == {'state': 'none'}
    running = sm.parse_balance_status("Balance on '/mnt/alvaos/main' is running\n"
                                      "3 out of about 10 chunks balanced (4 considered),  70% left\n")
    assert running == {'state': 'running', 'percent': 30.0}
    stats = sm.parse_device_stats('[/dev/sdb].write_io_errs    0\n[/dev/sdb].corruption_errs  4\n'
                                  '[devid:2].read_io_errs     0\n')
    assert stats == {'/dev/sdb': {'write_io_errs': 0, 'corruption_errs': 4}, 'devid:2': {'read_io_errs': 0}}


def test_disk_sizes_from_lsblk_bytes():
    found = sm.describe_disks([disk('sdb', size=4000787030016)], [])
    assert found[0]['size'] == '3.6T' and found[0]['size_bytes'] == 4000787030016


# ── Pool maintenance API ─────────────────────────────────────────────────────

@pytest.fixture
def maintenance(api, monkeypatch):
    client, headers, runner = api
    started = []
    status = {'scrub': 'UUID: x\n\tno stats available\n', 'replace': 'Never started',
              'balance': "No balance found on '/mnt/alvaos/main'", 'device': '[/dev/sdb].write_io_errs 0\n'}

    def btrfs(args, timeout=10):
        return status.get(args[0], ''), None

    monkeypatch.setattr(api_storage, '_btrfs_output', btrfs)
    monkeypatch.setattr(api_storage, '_start_background', lambda cmd: started.append(cmd) or '')
    live = [{'id': 'u-main', 'name': 'main', 'devices': ['/dev/sdb'],
             'members': sm.parse_show_members(SHOW_DEGRADED)}]
    monkeypatch.setattr(api_storage, 'detect_btrfs_pools', lambda: (live, None))
    return client, headers, started, status


def test_activity_reports_what_the_pool_is_doing(maintenance):
    client, headers, _, status = maintenance
    status['replace'] = '40.0% done, 0 write errs, 0 uncorr. read errs'
    body = client.get('/api/v1/storage/pools/u-main/activity', headers=headers).get_json()
    assert body['replace'] == {'state': 'running', 'percent': 40.0, 'errors': 0, 'text': status['replace']}
    assert body['scrub'] == {'state': 'never'}
    assert body['device_stats'] == {'/dev/sdb': {'write_io_errs': 0}}


def test_a_missing_disk_is_replaced_by_devid(maintenance, tmp_path):
    client, headers, started, _ = maintenance
    response = client.post('/api/v1/storage/pools/u-main/replace', headers=headers,
                           json={'source': 2, 'target': '/dev/sdc'})
    assert response.status_code == 200, response.get_json()
    assert started[-1][1:] == ['replace', 'start', '-B', '2', '/dev/sdc', '/mnt/alvaos/main']
    assert '/dev/sdc' in sm.load_pools_state()['u-main']['devices']


@pytest.mark.parametrize('body', [
    {'source': 9, 'target': '/dev/sdc'},          # not a member
    {'source': 1, 'target': '/dev/sdd'},          # target has data
    {'source': 1, 'target': '/dev/sdb'},          # target is in the pool
    {'source': 1},
])
def test_replace_needs_a_member_and_an_empty_disk(maintenance, body):
    client, headers, started, _ = maintenance
    response = client.post('/api/v1/storage/pools/u-main/replace', headers=headers, json=body)
    assert response.status_code in (400, 409)
    assert started == []


def test_replace_needs_a_disk_at_least_as_large(maintenance, monkeypatch):
    client, headers, started, _ = maintenance
    small = [disk('sdc', size=1000204886016)]
    monkeypatch.setattr(api_storage, 'disk_inventory', lambda: sm.describe_disks(small, []))
    monkeypatch.setattr(sm, 'disk_inventory', lambda: sm.describe_disks(small, []))
    response = client.post('/api/v1/storage/pools/u-main/replace', headers=headers,
                           json={'source': 1, 'target': '/dev/sdc'})
    assert response.status_code == 409 and 'smaller' in response.get_json()['error']
    assert started == []


def test_one_long_job_at_a_time(maintenance):
    client, headers, started, status = maintenance
    status['balance'] = "Balance on '/mnt/alvaos/main' is running\n1 out of about 9 chunks balanced, 89% left"
    assert client.post('/api/v1/storage/pools/u-main/scrub', headers=headers, json={}).status_code == 409
    assert client.post('/api/v1/storage/pools/u-main/replace', headers=headers,
                       json={'source': 2, 'target': '/dev/sdc'}).status_code == 409
    assert started == []


def test_scrub_starts_in_the_background(maintenance):
    client, headers, started, _ = maintenance
    response = client.post('/api/v1/storage/pools/u-main/scrub', headers=headers, json={})
    assert response.status_code == 200
    assert started[-1][1:] == ['scrub', 'start', '-B', '/mnt/alvaos/main']


def test_maintenance_needs_a_mounted_pool(maintenance, monkeypatch):
    client, headers, started, _ = maintenance
    monkeypatch.setattr(api_storage, '_is_mounted', lambda path: False)
    assert client.post('/api/v1/storage/pools/u-main/scrub', headers=headers, json={}).status_code == 409
    assert client.get('/api/v1/storage/pools/nope/activity', headers=headers).status_code == 404
    assert started == []


def test_background_jobs_report_an_immediate_failure(monkeypatch):
    monkeypatch.setattr(api_storage, 'build_privileged_cmd', lambda cmd: cmd)
    assert api_storage._start_background(['sh', '-c', 'echo "ERROR: target too small" >&2; exit 1']) \
        == 'ERROR: target too small'
    assert api_storage._start_background(['sleep', '5']) == ''  # still running: fine
