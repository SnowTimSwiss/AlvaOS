"""AlvaOS Files as a built-in app: turned on and off under Apps.

The app itself is files_server.py (its own service on port 8090). Here the
admin only switches that service on or off, and sees who still needs their
password set once more to sign in there.
"""

import os

from flask import Blueprint, jsonify, request

from auth_manager import require_auth
from common import CMD, run_sudo_command

bp = Blueprint('files', __name__)

UNIT = 'alvaos-files.service'
PORT = 8090
HTTPS_PORT = 9443   # tls_manager.PORTS['files']


def _systemctl(*args):
    res, err = run_sudo_command([CMD['SYSTEMCTL'], *args, UNIT], timeout=30)
    return (res.stdout.strip() if res is not None and res.stdout else ''), err


def files_app_state():
    enabled, _ = _systemctl('is-enabled')
    active, _ = _systemctl('is-active')
    from api_auth import load_users_state
    waiting = sorted(name for name, info in load_users_state().items()
                     if isinstance(info, dict) and not info.get('files_auth'))
    return {'enabled': enabled == 'enabled', 'running': active == 'active', 'port': PORT, 'https_port': HTTPS_PORT,
            'people_without_password': waiting}


@bp.route('/api/v1/files-app', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def files_app():
    if request.method == 'POST':
        on = bool((request.get_json(silent=True) or {}).get('enabled'))
        _, err = _systemctl('enable' if on else 'disable', '--now')
        if err:
            return jsonify({'error': f'AlvaOS Files could not be turned {"on" if on else "off"}: {err}'}), 500
    return jsonify({'success': True, **files_app_state()})


def _data_pools():
    """Pools the Hub may keep things on: mounted data pools, not the system."""
    from storage_manager import load_pools_state
    return {pid: p for pid, p in load_pools_state().items()
            if isinstance(p, dict) and p.get('mount_point') and p.get('mount_point') != '/'}


def _hub_state(people):
    import hub_apps
    from shares_manager import load_shares_state
    settings = hub_apps.load()
    shares = [s for s in load_shares_state().values() if isinstance(s, dict)]
    pools = _data_pools()
    personal = {p: next((s['name'] for s in shares if s.get('personal_for') == p), None) for p in people}
    # People who see an app that keeps their data in the personal folder, but have none.
    needs_personal = sorted({p for a in hub_apps.APPS if a['personal']
                             and settings['apps'][a['id']].get('location', {}).get('mode') == 'personal'
                             for p in people if hub_apps.allowed(a['id'], p, 'user', settings) and not personal[p]})
    cache = hub_apps.cache_dir(pools, settings)
    return {
        'apps': hub_apps.overview(settings), 'people': people, 'storage': settings['storage'],
        'cache_on_system_disk': cache is None,
        'pools': [{'id': pid, 'name': p.get('name') or pid} for pid, p in sorted(pools.items(), key=lambda i: str(i[1].get('name')))],
        'libraries_to_choose': sorted(s['name'] for s in shares
                                      if not s.get('personal_for') and not s.get('hub_for') and s.get('protocol', 'smb') == 'smb'),
        'personal_folders': personal, 'needs_personal_folder': needs_personal,
    }


def _make_app_folders(app_id, people, settings):
    """With a pool of its own, the app keeps each person's data in a share
    only they can open (`<person>-<app>`), with the limit chosen for it."""
    import api_shares
    import hub_apps
    from shares_manager import load_shares_state
    location = settings['apps'][app_id]['location']
    made, problems = [], []
    existing = {s.get('name'): s for s in load_shares_state().values() if isinstance(s, dict)}
    for person in people:
        if not hub_apps.allowed(app_id, person, 'user', settings):
            continue
        name = hub_apps.app_share_name(app_id, person)
        if name in existing:
            if existing[name].get('hub_for') != person:
                problems.append(f'There is already a shared folder "{name}"; rename it to make room.')
            continue
        payload, status = api_shares.create_share(
            {'name': name, 'protocol': 'smb', 'pool_id': location['pool_id'], 'folder': name, 'new_folder': True,
             'smb_permissions': {person: 'write'}}, set(people), extra={'hub_app': app_id, 'hub_for': person})
        if status != 200:
            problems.append(f'{name}: {payload.get("error")}')
            continue
        made.append(name)
        if location.get('limit_gb'):
            limited, limit_status = api_shares.set_share_limit(payload['share_id'], location['limit_gb'])
            if limit_status != 200:
                problems.append(f'{name} has no limit: {limited.get("error")}')
    return made, problems


def _prepare_cache(pool_id):
    """The cache folder on the chosen pool, owned by the Hub's service account."""
    import hub_apps
    pool = _data_pools().get(pool_id) or {}
    folder = os.path.join(str(pool.get('mount_point')), hub_apps.CACHE_FOLDER)
    for cmd in ([CMD['MKDIR'], '-p', os.path.join(folder, 'thumbs')], [CMD['CHOWN'], '-R', 'alvaos:alvaos', folder]):
        res, err = run_sudo_command(cmd, timeout=30)
        if err or not res or res.returncode != 0:
            return f'The cache folder could not be made: {err or (res.stderr if res else "")}'.strip()
    return ''


@bp.route('/api/v1/hub', methods=['GET', 'POST'])
@require_auth(require_admin=True)
def hub():
    """AlvaOS Hub: on or off; per Hub app on or off, who sees it and where it
    keeps data; the cache pool; personal folders for people who need one.

    POST {'enabled': bool}: the Hub (its service) on or off.
    POST {'apps': {id: {'enabled', 'people', 'location', 'libraries'}}}: change apps.
    POST {'storage': {'cache_pool': id or ''}}: where caches go.
    POST {'personal_folders': {'pool_id', 'limit_gb'}}: make the missing personal folders."""
    import api_shares
    import hub_apps
    from api_auth import load_users_state
    from shares_manager import load_shares_state
    people = sorted(load_users_state())
    notes = []
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if 'enabled' in data:
            on = bool(data.get('enabled'))
            _, err = _systemctl('enable' if on else 'disable', '--now')
            if err:
                return jsonify({'error': f'AlvaOS Hub could not be turned {"on" if on else "off"}: {err}'}), 500
        if 'apps' in data or 'storage' in data:
            shares = [str(s.get('name')) for s in load_shares_state().values() if isinstance(s, dict)]
            settings, problem = hub_apps.save({k: data[k] for k in ('apps', 'storage') if k in data}, people,
                                              pools=_data_pools(), shares=shares)
            if problem or settings is None:
                return jsonify({'error': problem}), 400
            for app_id, change in (data.get('apps') or {}).items():
                if isinstance(change, dict) and ('location' in change or 'people' in change or 'enabled' in change) \
                        and settings['apps'][app_id].get('location', {}).get('mode') == 'pool':
                    made, problems = _make_app_folders(app_id, people, settings)
                    if made:
                        notes.append(f'Made {", ".join(made)}.')
                    if problems:
                        return jsonify({'error': ' '.join(problems), **_hub_state(people), **files_app_state()}), 409
            if (data.get('storage') or {}).get('cache_pool'):
                problem = _prepare_cache(settings['storage']['cache_pool'])
                if problem:
                    return jsonify({'error': problem}), 500
        if isinstance(data.get('personal_folders'), dict):
            spec = data['personal_folders']
            made, problems = [], []
            for person in _hub_state(people)['needs_personal_folder']:
                problem = api_shares.check_personal_folder(person, spec)
                if problem:
                    problems.append(f'{person}: {problem}')
                    continue
                folder, status = api_shares.create_personal_folder(person, spec, set(people))
                if status != 200:
                    problems.append(f'{person}: {folder.get("error")}')
                else:
                    made.append(person)
            if problems:
                return jsonify({'error': ' '.join(problems), **_hub_state(people), **files_app_state()}), 409
            notes.append(f'Personal folders made for {", ".join(made)}.' if made else 'Everyone has a personal folder.')
    return jsonify({'success': True, 'name': hub_apps.NAME, **files_app_state(), **_hub_state(people),
                    'message': ' '.join(notes)})
