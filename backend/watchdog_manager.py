#!/usr/bin/env python3
"""
AlvaOS Watchdog Manager
Monitors critical services and auto-restarts them on failure.
"""

import subprocess
import os
import json
import platform
from datetime import datetime, timezone

LOG_FILE = '/var/log/alvaos/watchdog.log'
STATE_FILE = '/var/lib/alvaos/watchdog_state.json'

WATCHED_SERVICES = [
    {'name': 'smbd',              'label': 'Samba (File Sharing)'},
    {'name': 'nfs-kernel-server', 'label': 'NFS Server'},
    {'name': 'docker',            'label': 'Docker Engine'},
]

MAX_RECOVERY_LOG_ENTRIES = 50


class WatchdogManager:
    def __init__(self):
        pass

    def _utc_now_iso(self):
        return datetime.now(timezone.utc).isoformat()

    def _load_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return {'last_check': None, 'recoveries': []}

    def _save_state(self, state):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[watchdog] Error saving state: {e}")

    def _log(self, message):
        try:
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            ts = self._utc_now_iso()
            with open(LOG_FILE, 'a') as f:
                f.write(f"[{ts}] {message}\n")
        except Exception:
            pass

    def _is_service_active(self, service_name):
        """Return True if systemd reports the service as active/running."""
        if platform.system() != 'Linux':
            return True  # In dev, assume all OK
        try:
            result = subprocess.run(
                ['/usr/bin/systemctl', 'is-active', service_name],
                capture_output=True, text=True, timeout=5, env={'LC_ALL': 'C'}
            )
            return result.stdout.strip() == 'active'
        except Exception:
            return False

    def _restart_service(self, service_name):
        """Attempt to restart a service. Returns (success, error_message)."""
        if platform.system() != 'Linux':
            return False, 'Non-Linux environment'
        try:
            result = subprocess.run(
                ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'restart', service_name],
                capture_output=True, text=True, timeout=30, env={'LC_ALL': 'C'}
            )
            if result.returncode == 0:
                return True, None
            return False, (result.stderr or result.stdout or f'exit {result.returncode}').strip()
        except subprocess.TimeoutExpired:
            return False, 'Restart timed out'
        except Exception as e:
            return False, str(e)

    def run_check(self):
        """
        Check each watched service and restart if inactive.
        Returns a dict with service statuses and any new recoveries.
        """
        state = self._load_state()
        now_iso = self._utc_now_iso()
        state['last_check'] = now_iso

        if not isinstance(state.get('recoveries'), list):
            state['recoveries'] = []

        service_results = []

        for svc in WATCHED_SERVICES:
            name = svc['name']
            label = svc['label']
            is_active = self._is_service_active(name)

            entry = {
                'name': name,
                'label': label,
                'active': is_active,
                'recovered': False,
                'recover_error': None,
            }

            if not is_active:
                self._log(f"Service '{name}' is not running. Attempting restart...")
                success, err = self._restart_service(name)
                if success:
                    entry['active'] = self._is_service_active(name)
                    entry['recovered'] = entry['active']
                    if entry['recovered']:
                        self._log(f"Service '{name}' restarted successfully.")
                    else:
                        self._log(f"Service '{name}' restart issued but still not active.")
                else:
                    entry['recover_error'] = err
                    self._log(f"Failed to restart '{name}': {err}")

                recovery_entry = {
                    'service': name,
                    'label': label,
                    'recovered': entry['recovered'],
                    'error': entry['recover_error'],
                    'timestamp': now_iso,
                }
                state['recoveries'].insert(0, recovery_entry)

            service_results.append(entry)

        # Trim recovery log
        state['recoveries'] = state['recoveries'][:MAX_RECOVERY_LOG_ENTRIES]
        self._save_state(state)

        return {
            'services': service_results,
            'last_check': now_iso,
        }

    def get_status(self):
        """
        Return current watchdog state with live service status and recovery log.
        """
        state = self._load_state()
        services = []
        for svc in WATCHED_SERVICES:
            services.append({
                'name': svc['name'],
                'label': svc['label'],
                'active': self._is_service_active(svc['name']),
            })

        return {
            'services': services,
            'last_check': state.get('last_check'),
            'recoveries': (state.get('recoveries') or [])[:10],
        }
