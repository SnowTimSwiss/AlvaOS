#!/usr/bin/env python3
"""The sync folder of the desktop app against a real NAS on this machine.

Starts the real Hub (files_server.py) and WebDAV (files_dav.py) with the real
privileged helper (backend/alvaos-priv) on a share /mnt/alvaos/main/SyncTest
of a person "anna" (both made if missing), then runs the Rust test
desktop/core/tests/sync_nas.rs against them. Needs root (the helper drops to
anna) and a machine you do not mind getting that folder and person: a test
VM or container, not a real NAS.

    sudo python3 scripts/dev/sync_against_nas.py
"""

import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import threading

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BACKEND = os.path.join(REPO, 'backend')
SHARE = '/mnt/alvaos/main/SyncTest'
sys.path.insert(0, BACKEND)


def main() -> int:
    if os.geteuid() != 0:
        print('Needs root (the helper drops to the person).')
        return 2
    try:
        pwd.getpwnam('anna')
    except KeyError:
        subprocess.run(['useradd', '-M', '-s', '/usr/sbin/nologin', 'anna'], check=True)
    shutil.rmtree(SHARE, ignore_errors=True)
    os.makedirs(os.path.join(SHARE, 'Photos', '2024'))
    with open(os.path.join(SHARE, 'Photos', '2024', 'Sommer & Meer ä.txt'), 'w') as f:
        f.write('beach')
    os.utime(os.path.join(SHARE, 'Photos', '2024', 'Sommer & Meer ä.txt'), (1790000000, 1790000000))
    subprocess.run(['chown', '-R', 'anna:anna', SHARE], check=True)
    os.chmod(SHARE, 0o2770)

    work = tempfile.mkdtemp()
    import common
    import files_manager
    import files_server as fs
    from password_utils import hash_password
    common.PRIV_HELPER = files_manager.PRIV_HELPER = os.path.join(BACKEND, 'alvaos-priv')
    for name, data in (('users.json', {'anna': {'files_auth': hash_password('anna-pass')}}),
                       ('shares.json', {'9': {'name': 'SyncTest', 'path': SHARE, 'protocol': 'smb',
                                              'smb_permissions': {'anna': 'write'}}}),
                       ('auth.json', hash_password('admin-pass'))):
        with open(os.path.join(work, name), 'w') as f:
            json.dump(data, f)
    fs.USERS_FILE, fs.SHARES_FILE = os.path.join(work, 'users.json'), os.path.join(work, 'shares.json')
    fs.AUTH_FILE, fs.SESSIONS_FILE, fs.STATE_DIR = os.path.join(work, 'auth.json'), os.path.join(work, 'sessions.json'), work
    import hub_apps
    hub_apps.SETTINGS_FILE = os.path.join(work, 'hub.json')
    import files_dav
    from werkzeug.serving import make_server
    hub = make_server('127.0.0.1', 0, fs.app, threaded=True)
    dav = make_server('127.0.0.1', 0, files_dav.app, threaded=True)
    for server in (hub, dav):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    token = fs._new_session('anna', 'user', device={'id': 'pc1', 'name': 'Laptop', 'platform': 'linux'})
    env = {**os.environ, 'ALVAOS_TEST_HUB': f'http://127.0.0.1:{hub.server_port}',
           'ALVAOS_TEST_DAV': f'http://127.0.0.1:{dav.server_port}', 'ALVAOS_TEST_TOKEN': token,
           'ALVAOS_TEST_SHARE_DIR': SHARE}
    result = subprocess.run(['cargo', 'test', '-p', 'alvaos-core', '--test', 'sync_nas', '--', '--nocapture'],
                            cwd=os.path.join(REPO, 'desktop'), env=env)
    hub.shutdown()
    dav.shutdown()
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(SHARE, ignore_errors=True)
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
