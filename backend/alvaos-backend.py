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
from flask import Flask, Response, jsonify, redirect, request, send_from_directory

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
import api_ai
import api_files
import api_remote
import api_gpu
import api_vms
import api_shares
import api_storage
import api_system
import api_updates
import api_ups

for _module in (api_auth, api_system, api_updates, api_storage, api_shares, api_backup, api_apps, api_files, api_ai,
                api_remote, api_gpu, api_vms, api_ups):
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


# Never sent to HTTPS: other NAS (pairing and the Buddy Backup peer API come
# over the tunnel by HTTP), the certificate a new device still has to trust,
# and the NAS itself (the assistant and local scripts call the API in-process
# or on localhost).
HTTPS_ONLY_EXEMPT_PATHS = ('/api/v1/system/tls/ca.crt', '/api/v1/backup/pairing/accept',
                           '/api/v1/backup/pairing/remove/accept')


@app.before_request
def send_to_https():
    """While "HTTPS only" is on, plain HTTP is answered with a redirect."""
    path = request.path or ''
    if path.startswith(CSRF_EXEMPT_PREFIXES) or (request.remote_addr or '') in ('127.0.0.1', '::1'):
        return None
    import tls_manager
    target = tls_manager.redirect_to_https(request, tls_manager.PORTS['web'], keep=HTTPS_ONLY_EXEMPT_PATHS)
    return redirect(target, code=308) if target else None


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
        # The screens of virtual machines (vm_console.py) and the admin terminal
        # (admin_terminal.py) are WebSockets on their own ports.
        "connect-src 'self' ws://*:8085 wss://*:9445 ws://*:8086 wss://*:9446; "
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

    # Shares made before network deletes went to the trash get it once.
    def _add_recycle_bins():
        from shares_manager import add_recycle_bins, load_shares_state
        try:
            added = add_recycle_bins(load_shares_state())
            if added:
                print(f"Network deletes now go to the trash for: {', '.join(added)}")
        except Exception as e:
            print(f"Could not add the network trash to shares: {e}")
    threading.Thread(target=_add_recycle_bins, name='smb-recycle', daemon=True).start()

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

    # The signed package of the running version belongs in the update cache:
    # it is the way back when an update fails. After an install from the
    # installer or a USB stick it is fetched from that version's release.
    def _keep_way_back():
        import platform as _platform
        import time as _time
        from app_services import update_manager
        if _platform.system() != 'Linux':
            return
        _time.sleep(120)
        for _ in range(28):           # a week of tries, then the next start
            outcome = update_manager.ensure_way_back()
            if outcome in ('ready', 'fetched', 'unknown version', 'no release for this version',
                           'no signed package in the release', 'the release package has another version'):
                print(f"Way back for updates: {outcome}")
                return
            _time.sleep(6 * 3600)
    threading.Thread(target=_keep_way_back, name='update-way-back', daemon=True).start()

    # A second copy of the restore points on the backup disk, when it is there.
    from app_services import backup_copier
    threading.Thread(target=backup_copier.serve_forever, name='backup-copy', daemon=True).start()

    # Problems by Telegram and email, also when nobody has the web page open.
    import alert_delivery
    threading.Thread(target=alert_delivery.serve_forever, name='alert-delivery', daemon=True).start()

    # Graphics: a driver installed before this start is running now.
    from app_services import gpu
    gpu.clear_after_boot()

    # The screens of virtual machines in the browser (only with a ticket).
    import vm_console
    vm_console.serve_in_background()

    # The admin terminal (Settings › Terminal), only with a ticket, ends with the sign-in.
    import admin_terminal
    import auth_manager
    admin_terminal.setup(auth_manager.admin_signed_in)
    admin_terminal.serve_in_background()

    # Remote access is Tailscale and Cloudflare Tunnel now (containers that
    # restart by themselves); the old WireGuard remote access goes, once.
    def _retire_old_remote_access():
        from app_services import remote
        if remote.retire_wireguard():
            import alerts_manager
            alerts_manager.push_notification(
                'warning', 'Remote access works differently now',
                'The old remote access (WireGuard with a router port) is off. Turn on Tailscale in Settings › '
                'Remote access and install the Tailscale app on your devices; you can close the router port.',
                source='remote', link='system.html#remote', fingerprint='remote-access-wireguard-retired')
    threading.Thread(target=_retire_old_remote_access, name='remote-access', daemon=True).start()

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

    # HTTPS next to HTTP, with this NAS's own certificate (tls_manager.py).
    import tls_manager
    tls_manager.serve_in_background(app, tls_manager.PORTS['web'], 'AlvaOS')

    if waitress is not None:
        waitress.serve(app, host='0.0.0.0', port=8080, threads=8, ident='AlvaOS')
    else:
        print("Warning: waitress is not installed, falling back to the Flask "
              "development server. Install python3-waitress for production use.")
        app.run(host='0.0.0.0', port=8080, debug=False)
