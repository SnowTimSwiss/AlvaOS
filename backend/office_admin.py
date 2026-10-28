"""Turning Office on in the Hub's admin page: install the editor (EuroOffice's Document Server) by itself.

See hub_office.py for how it is used. The editor is the catalog app `hub-office`, installed through
the same App Store code as every app (pulling its image takes a while: about 2 GB, so the page shows
the progress), with a secret made here for the signatures between the Hub and the editor."""

import secrets
from typing import Any

import hub_office

DONE = ('completed', 'success', 'installed', 'idle')


def state(app_store: Any, enabled: bool, ready: bool | None = None) -> dict[str, Any]:
    """{'state': off | installing | error | starting | ready | missing, 'message', 'progress'} for the admin page."""
    if not enabled:
        return {'state': 'off', 'message': '', 'progress': 0}
    installed = hub_office.DS_APP_ID in (app_store._load_apps_state() or {})
    status = app_store.get_install_status() or {}
    mine = status.get('app_id') == hub_office.DS_APP_ID
    if mine and status.get('status') in ('starting', 'installing', 'updating'):
        return {'state': 'installing', 'message': str(status.get('message') or 'Installing the editor…'),
                'progress': int(status.get('progress') or 0)}
    if not installed:
        if mine and status.get('status') == 'error':
            return {'state': 'error', 'message': str(status.get('message') or 'The editor could not be installed.'), 'progress': 0}
        return {'state': 'missing', 'message': '', 'progress': 0}
    up = hub_office.editor_answers() if ready is None else ready
    return {'state': 'ready' if up else 'starting',
            'message': '' if up else 'The editor is starting. This takes a minute.', 'progress': 100 if up else 90}


def ensure_installed(app_store: Any, pool_path: str) -> tuple[bool, str]:
    """Start installing the editor on this pool unless it is there (or on its way)."""
    if hub_office.DS_APP_ID in (app_store._load_apps_state() or {}):
        return True, ''
    status = app_store.get_install_status() or {}
    if status.get('app_id') == hub_office.DS_APP_ID and status.get('status') in ('starting', 'installing'):
        return True, ''
    ok, error = app_store.install_app(app_id=hub_office.DS_APP_ID, pool_path=pool_path, parent_subvolume=None,
                                      port_mappings=None, volume_mappings=None,
                                      environment_vars={'JWT_SECRET': secrets.token_hex(24)})
    return bool(ok), str(error or '')
