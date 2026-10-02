#!/usr/bin/env python3
"""
AlvaOS Backend
Thin routing layer — all business logic lives in the manager modules.
"""

# ── Standard library ──────────────────────────────────────────────────────────
import os
import platform
import threading
import secrets
import atexit

# ── Third-party ───────────────────────────────────────────────────────────────
from flask import Flask, Response, jsonify, request, send_from_directory

# ── AlvaOS managers ───────────────────────────────────────────────────────────
from common import (
    ensure_directories, run_sudo_command,
)
from auth_manager import (
    is_setup_complete,
    _get_current_session, _load_sessions,
)
from storage_manager import (
    mount_existing_pools,
)

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=None)
WEBUI_ROOT = '/opt/alvaos/webui'
if not os.path.exists(WEBUI_ROOT):
    WEBUI_ROOT = os.path.join(os.path.dirname(__file__), '..', 'frontend')
app.static_folder = WEBUI_ROOT

# No CORS: the Web UI is served by this very process on the same origin, so
# cross-origin access is never legitimate. A permissive policy here would let any
# website a LAN user visits talk to the NAS API from their browser.

from app_services import VERSION, buddy_backup_manager, power_ups_manager
import api_apps
import api_auth
import api_backup
import api_files
from api_files import UPLOAD_LIMIT_BYTES
import api_shares
import api_storage
import api_system
import api_updates

for _module in (api_auth, api_system, api_updates, api_storage, api_shares, api_backup, api_apps, api_files):
    app.register_blueprint(_module.bp)

# ── Frontend serving ──────────────────────────────────────────────────────────
def serve_frontend(filename):
    try:
        if filename.endswith(('.html', '.css', '.js')):
            with open(os.path.join(WEBUI_ROOT, filename), 'r') as f:
                content = f.read()
            clean_version = VERSION.strip('() ')
            content = content.replace('{{VERSION}}', clean_version)
            if filename.endswith('.html'):
                mimetype = 'text/html'
            elif filename.endswith('.css'):
                mimetype = 'text/css'
            else:
                mimetype = 'application/javascript'
            return Response(content, mimetype=mimetype)
        return send_from_directory(WEBUI_ROOT, filename)
    except Exception as e:
        print(f"Error serving {filename}: {e}")
        return f"File not found: {filename}", 404


@app.route('/')
def index():
    """Serve the Web UI with version replacement"""
    return serve_frontend('index.html')

@app.route('/apps/icons/<path:filename>')
def serve_app_icons(filename):
    """Serve app store icons from production or development catalog directories."""
    prod_icons_dir = '/opt/alvaos/apps/icons'
    dev_icons_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'apps', 'icons'))
    icons_dir = prod_icons_dir if os.path.exists(prod_icons_dir) else dev_icons_dir
    return send_from_directory(icons_dir, filename)


@app.route('/<path:filename>')
def serve_static_files(filename):
    """Catch-all for static files to ensure version replacement"""
    return serve_frontend(filename)


PRE_SETUP_ALLOWLIST = frozenset({
    '/api/v1/setup/status',
    '/api/v1/setup/complete',
})


# State-changing API calls that legitimately carry no browser session, and so
# cannot present a session-bound CSRF token:
#   - the setup/login handshakes, which run before a session exists
#   - buddy peer-to-peer traffic, authenticated by X-Buddy-Secret or a pairing
#     token in the body rather than by an operator session
CSRF_EXEMPT_PATHS = frozenset({
    '/api/v1/setup/complete',
    '/api/v1/auth/login',
    '/api/v1/auth/2fa/complete',
    '/api/v1/backup/pairing/accept',
    '/api/v1/backup/pairing/remove/accept',
})
CSRF_EXEMPT_PREFIXES = ('/api/v1/backup/buddy/peer/',)

SAFE_HTTP_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


@app.before_request
def enforce_csrf_token():
    """Require a session-bound CSRF token on every state-changing API request.

    Enforced centrally rather than per-route so that a newly added endpoint is
    protected by default instead of silently opting out.
    """
    if request.method in SAFE_HTTP_METHODS:
        return None
    path = request.path or ''
    if not path.startswith('/api/'):
        return None

    normalized = path.rstrip('/')
    if normalized in CSRF_EXEMPT_PATHS:
        return None
    if any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PREFIXES):
        return None

    session = _get_current_session()
    if not session:
        # Unauthenticated: let the route's own @require_auth answer with a 401
        # so clients get "log in" rather than a confusing CSRF error.
        return None

    presented = request.headers.get('X-CSRF-Token', '').strip()
    expected = session.get('csrf_token', '')
    if not presented or not expected or not secrets.compare_digest(presented, expected):
        return jsonify({'error': 'Invalid or missing CSRF token'}), 403
    return None


@app.before_request
def guard_pre_setup_surface():
    """Refuse API access before setup, except for the setup handshake itself."""
    path = request.path or ''
    if not path.startswith('/api/'):
        # Static Web UI assets stay reachable; the setup page has to load.
        return None
    if is_setup_complete():
        return None
    if path.rstrip('/') in PRE_SETUP_ALLOWLIST:
        return None
    return jsonify({
        'error': 'AlvaOS is not set up yet. Finish first-run setup in the Web UI.',
        'setup_required': True,
    }), 403


@app.after_request
def add_security_headers(response):
    """Add security hardening headers to every response."""
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    # The UI ships entirely self-hosted assets (no CDN), so a strict policy holds.
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    )
    return response


if __name__ == '__main__':
    print(f"Starting AlvaOS Backend v{VERSION}...")
    print("Web UI: http://0.0.0.0:8080")
    print("API:    http://0.0.0.0:8080/api/v1/system/info")
    
    # Ensure directories exist
    ensure_directories()

    # Restore sessions so an update or restart does not log everyone out.
    _load_sessions()

    try:
        power_ups_manager.start()
        atexit.register(power_ups_manager.stop)
    except Exception as e:
        print(f"Warning: Battery UPS monitor startup failed: {e}")
    
    # Mount existing pools on startup (persistence)
    mount_existing_pools()

    # Share accounts never get a login shell (older versions gave them bash).
    try:
        from api_auth import load_users_state
        from shares_manager import lock_share_user_shells
        lock_share_user_shells(load_users_state())
    except Exception as e:
        print(f"Warning: could not check share user shells: {e}")

    # Nightly data checks (btrfs scrub) for every pool, monthly by default.
    if platform.system() == 'Linux':
        from api_storage import make_health_scheduler, make_smart_scheduler
        threading.Thread(target=make_health_scheduler().serve_forever,
                         kwargs={'smart': make_smart_scheduler()}, name='health-checks',
                         daemon=True).start()

    # Hard disks forget their sleep timer when they lose power.
    if platform.system() == 'Linux':
        import disk_power
        from storage_manager import disk_inventory
        threading.Thread(target=disk_power.apply_saved, args=(disk_inventory, run_sudo_command),
                         name='disk-power', daemon=True).start()

    # AlvaOS Files: what has been in a share's trash for 30 days goes for good.
    def _purge_trash_daily():
        import time as _time
        import files_manager
        from shares_manager import load_shares_state
        while True:
            _time.sleep(3600)
            try:
                files_manager.purge_all_trash(load_shares_state())
            except Exception as e:
                print(f"Trash cleanup failed: {e}")
            _time.sleep(23 * 3600)
    threading.Thread(target=_purge_trash_daily, name='files-trash', daemon=True).start()

    # Problems by Telegram and email, also when nobody has the web page open.
    import alert_delivery
    threading.Thread(target=alert_delivery.serve_forever, name='alert-delivery', daemon=True).start()

    # wg-quick state does not survive a reboot; bring the buddy tunnel back up.
    threading.Thread(target=buddy_backup_manager.start_tunnel_if_paired, daemon=True).start()
    # Serves the encrypted vaults buddies keep here, on the tunnel address only.
    threading.Thread(target=buddy_backup_manager.vault_store.serve_forever, name='buddy-vaults',
                     daemon=True).start()
    
    if not is_setup_complete():
        print("\n⚠️  SETUP REQUIRED: Access the Web UI to complete initial setup")

    # Serve through waitress: a NAS is expected to run unattended for years, and
    # the Werkzeug development server is explicitly not built for that.
    try:
        import waitress
    except ImportError:
        waitress = None  # type: ignore[assignment]

    if waitress is not None:
        # Uploads in AlvaOS Files: waitress keeps a request body in a temporary
        # file on the system disk until it is complete, so one file is capped at 4 GB.
        waitress.serve(app, host='0.0.0.0', port=8080, threads=8, ident='AlvaOS',
                       max_request_body_size=UPLOAD_LIMIT_BYTES)
    else:
        print("Warning: waitress is not installed, falling back to the Flask "
              "development server. Install python3-waitress for production use.")
        app.run(host='0.0.0.0', port=8080, debug=False)
