"""Creating and changing network shares (shares_manager.py, api_shares.py).

Share names and NFS client lists end up in smb.conf and /etc/exports, so
anything that could add a line or an option there must be refused.
"""

import platform

import pytest
from flask import Flask

import api_auth
import api_shares
import auth_manager
import shares_manager as shm

POOLS = {'u-main': {'name': 'main', 'mount_point': '/mnt/alvaos/main'},
         'u-sys': {'name': 'system', 'mount_point': '/'}}
USERS = {'anna', 'tim'}


def check(**data):
    data.setdefault('name', 'media')
    data.setdefault('pool_id', 'u-main')
    return shm.validate_share_request(data, POOLS, {}, USERS)


def test_a_new_folder_in_a_pool():
    share, problem = check(folder='media', new_folder=True, smb_permissions={'anna': 'write'})
    assert problem == ''
    assert share['path'] == '/mnt/alvaos/main/media' and share['new_folder'] is True
    assert share['smb_permissions'] == {'anna': 'write'} and share['guest_access'] is False


def test_the_whole_pool_and_an_existing_path():
    share, _ = check(guest_access=True)
    assert share['path'] == '/mnt/alvaos/main' and share['new_folder'] is False
    share, problem = shm.validate_share_request(
        {'name': 'photos', 'path': '/mnt/alvaos/main/photos/', 'guest_access': True}, POOLS, {}, USERS)
    assert problem == '' and share['path'] == '/mnt/alvaos/main/photos'


@pytest.mark.parametrize('name', ['media]\n[global', 'my share', '../x', '', 'a' * 64, 'media\npath = /etc', '-x'])
def test_share_names_cannot_inject_config(name):
    _, problem = check(name=name, guest_access=True)
    assert problem


@pytest.mark.parametrize('data', [
    {'pool_id': 'u-sys', 'guest_access': True},                         # the system pool
    {'pool_id': 'nope', 'guest_access': True},
    {'pool_id': 'u-main', 'folder': '../../etc', 'guest_access': True},
    {'pool_id': None, 'path': '/etc', 'guest_access': True},
    {'pool_id': None, 'path': '/mnt/alvaos/mainly', 'guest_access': True},
    {'pool_id': None, 'path': 'relative', 'guest_access': True},
])
def test_shares_stay_inside_a_pool(data):
    data = dict(data)
    if data.get('pool_id') is None:
        data.pop('pool_id')
    _, problem = shm.validate_share_request({'name': 'x', **data}, POOLS, {}, USERS)
    assert problem


def test_someone_must_be_able_to_open_an_smb_share():
    assert check()[1]
    assert check(smb_permissions={'anna': 'deny'})[1]
    assert check(smb_permissions={'eve': 'write'})[1]          # unknown user
    assert check(smb_permissions={'tim': 'read'})[1] == ''


def test_names_are_unique_ignoring_case():
    existing = {'s1': {'name': 'Media'}}
    _, problem = shm.validate_share_request({'name': 'media', 'pool_id': 'u-main', 'guest_access': True},
                                            POOLS, existing, USERS)
    assert 'already exists' in problem


@pytest.mark.parametrize('hosts', ['*(rw,no_root_squash)', '192.168.1.0/24 *(rw)', 'a\n/ *', ''])
def test_nfs_clients_cannot_inject_options(hosts):
    _, problem = check(protocol='nfs', allowed_hosts=hosts or ' ')
    assert problem


def test_nfs_export_lines():
    share, problem = check(protocol='nfs', allowed_hosts='192.168.1.0/24, laptop.lan', read_only=True)
    assert problem == ''
    text = shm.render_nfs_export(share['name'], share['path'], share['allowed_hosts'], share['read_only'])
    assert text == ('# AlvaOS Share: media\n/mnt/alvaos/main 192.168.1.0/24(ro,sync,no_subtree_check,root_squash) '
                    'laptop.lan(ro,sync,no_subtree_check,root_squash)\n')


# ── API ──────────────────────────────────────────────────────────────────────

class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, timeout=30, **_):
        self.calls.append(list(cmd))
        return None, None


@pytest.fixture
def api(tmp_path, monkeypatch):
    if platform.system() != 'Linux':
        pytest.skip('shares run on the NAS only')
    monkeypatch.setattr(auth_manager, 'SESSIONS_FILE', str(tmp_path / 'sessions.json'))
    monkeypatch.setattr(shm, 'SHARES_STATE_FILE', str(tmp_path / 'shares.json'))
    monkeypatch.setattr(shm, 'ensure_directories', lambda: None)
    pool = tmp_path / 'main'
    pool.mkdir()
    monkeypatch.setattr(api_shares, 'load_pools_state', lambda: {'u-main': {'name': 'main', 'mount_point': str(pool)}})
    monkeypatch.setattr(api_auth, 'load_users_state', lambda: {u: {} for u in USERS})
    runner = Runner()
    written = []
    monkeypatch.setattr(api_shares.subprocess, 'run', lambda cmd, input=None, **kw: written.append((cmd, input)))
    monkeypatch.setattr(api_shares, 'build_privileged_cmd', lambda cmd: cmd)
    for name in ('ensure_samba_conf_exists', 'ensure_samba_global_settings', 'disable_samba_homes_share',
                 'apply_smb_permissions_to_fs', 'reconcile_samba_guest_settings', 'update_samba_share_section'):
        monkeypatch.setattr(api_shares, name, lambda *a, **k: None)
    monkeypatch.setattr(api_shares, 'is_path_on_system_disk', lambda p: False)

    def btrfs_create(cmd, timeout=30, **kw):
        if cmd[1:3] == ['subvolume', 'create']:
            (tmp_path / cmd[3]).mkdir(parents=True)  # absolute path: pathlib keeps it
        return runner(cmd, timeout)

    monkeypatch.setattr(api_shares, 'run_sudo_command', btrfs_create)

    app = Flask(__name__)
    app.register_blueprint(api_shares.bp)
    token = auth_manager._create_session('root', role='admin')
    headers = {'Authorization': token, 'X-CSRF-Token': auth_manager.SESSIONS[token]['csrf_token']}
    yield app.test_client(), headers, runner, written, str(pool)
    auth_manager.SESSIONS.pop(token, None)


def test_sharing_a_new_folder_creates_it(api):
    client, headers, runner, written, pool = api
    response = client.post('/api/v1/storage/shares', headers=headers, json={
        'name': 'media', 'pool_id': 'u-main', 'folder': 'media', 'new_folder': True,
        'protocol': 'smb', 'smb_permissions': {'anna': 'write'}})
    assert response.status_code == 200, response.get_json()
    assert ['/usr/bin/btrfs', 'subvolume', 'create', f'{pool}/media'] in runner.calls
    conf = written[-1][1]
    assert '[media]' in conf and f'path = {pool}/media' in conf
    assert shm.load_shares_state()[response.get_json()['share_id']]['smb_permissions'] == {'anna': 'write'}


def test_a_bad_request_touches_nothing(api):
    client, headers, runner, written, pool = api
    response = client.post('/api/v1/storage/shares', headers=headers, json={
        'name': 'x]\n[global', 'pool_id': 'u-main', 'guest_access': True})
    assert response.status_code == 400
    assert runner.calls == [] and written == []


def test_nfs_share_writes_a_squashed_export(api):
    client, headers, _, written, pool = api
    response = client.post('/api/v1/storage/shares', headers=headers, json={
        'name': 'backups', 'pool_id': 'u-main', 'protocol': 'nfs', 'allowed_hosts': '192.168.1.0/24'})
    assert response.status_code == 200, response.get_json()
    assert written[-1][1].endswith(f'{pool} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash)\n')


def test_access_can_switch_between_people_and_everyone(api):
    client, headers, _, _, pool = api
    share_id = client.post('/api/v1/storage/shares', headers=headers, json={
        'name': 'media', 'pool_id': 'u-main', 'smb_permissions': {'anna': 'write'}}).get_json()['share_id']

    everyone = client.put('/api/v1/storage/shares/permissions', headers=headers, json={
        'share_id': share_id, 'smb_permissions': {}, 'guest_access': True, 'read_only': True})
    assert everyone.status_code == 200, everyone.get_json()
    state = shm.load_shares_state()[share_id]
    assert state['guest_access'] is True and state['read_only'] is True and state['smb_permissions'] == {}

    nobody = client.put('/api/v1/storage/shares/permissions', headers=headers, json={
        'share_id': share_id, 'smb_permissions': {}, 'guest_access': False})
    assert nobody.status_code == 400
    stranger = client.put('/api/v1/storage/shares/permissions', headers=headers, json={
        'share_id': share_id, 'smb_permissions': {'eve': 'write'}})
    assert stranger.status_code == 400


def test_old_share_accounts_lose_their_login_shell(monkeypatch):
    import pwd
    shells = {'anna': '/bin/bash', 'tim': '/usr/sbin/nologin'}

    class Entry:
        def __init__(self, shell):
            self.pw_shell = shell

    def getpwnam(name):
        if name not in shells:
            raise KeyError(name)
        return Entry(shells[name])

    calls = []
    monkeypatch.setattr(pwd, 'getpwnam', getpwnam)
    monkeypatch.setattr(shm.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(shm, 'run_sudo_command', lambda cmd, **kw: calls.append(cmd) or (None, None))
    shm.lock_share_user_shells(['anna', 'tim', 'gone'])
    assert calls == [['/usr/sbin/usermod', '-s', '/usr/sbin/nologin', 'anna']]


def test_network_deletes_go_to_the_trash_of_writable_shares():
    import shares_manager as sm
    writable = sm.render_smb_share_config("Family", "/mnt/alvaos/main/Family", False, False, {"anna": "write"})
    assert "recycle:repository = .alvaos-trash/smb" in writable and "hide files = /.alvaos-trash/" in writable
    reading = sm.render_smb_share_config("Media", "/mnt/alvaos/main/Media", True, False, {"anna": "read"})
    assert "recycle" not in reading
    conf = "[global]\n\n# AlvaOS Share: Family\n[Family]\n    path = /x\n\n# AlvaOS Share: New\n[New]\n" + writable.split("[Family]\n", 1)[1]
    state = {"1": {"name": "Family", "protocol": "smb", "smb_permissions": {"anna": "write"}},
             "2": {"name": "New", "protocol": "smb", "smb_permissions": {"anna": "write"}},
             "3": {"name": "Media", "protocol": "smb", "read_only": True, "smb_permissions": {"anna": "read"}}}
    assert sm.missing_recycle_bin(conf, state) == ["Family"]
