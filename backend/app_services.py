#!/usr/bin/env python3
"""Shared service objects used by the API modules.

Each manager is created once here, so all blueprints talk to the same instance
(and the same locks and caches).
"""

import os

from common import run_sudo_command
from storage_manager import load_pools_state
from update_manager import UpdateManager
from docker_manager import DockerManager
from app_store import AppStore
from backup_manager import BackupManager
from buddy_backup_manager import BuddyBackupManager
from watchdog_manager import WatchdogManager
from power_ups_manager import PowerUpsManager

update_manager = UpdateManager()
docker_manager = DockerManager()
app_store = AppStore()
watchdog_manager = WatchdogManager()
power_ups_manager = PowerUpsManager()
backup_manager = BackupManager(run_sudo_command, load_pools_state)
buddy_backup_manager = BuddyBackupManager(run_sudo_command)

# ── Version ───────────────────────────────────────────────────────────────────
def get_version():
    prod_path = '/etc/alvaos/VERSION'
    dev_path = os.path.join(os.path.dirname(__file__), '..', 'VERSION')
    try:
        if os.path.exists(prod_path):
            with open(prod_path, 'r') as f:
                return f.read().strip()
        elif os.path.exists(dev_path):
            with open(dev_path, 'r') as f:
                return f.read().strip()
    except Exception as e:
        print(f"Error reading version file: {e}")
    return "unknown"

VERSION = get_version()
