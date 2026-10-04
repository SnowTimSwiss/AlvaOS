#!/usr/bin/env bash
set -euo pipefail

PACKAGE_PATH="${1:-}"
# The signed package of the version that was installed before, staged and
# verified by alvaos-priv. Installed again when this update fails.
WAY_BACK_PATH="${2:-}"
FAIL_MESSAGE="Update failed"
ROLLED_BACK=0
HEALTH_URL="http://127.0.0.1:8080/api/v1/setup/status"
HEALTH_WAIT_SECONDS="${ALVAOS_HEALTH_WAIT:-120}"
LOG_FILE="/var/log/alvaos/update-apply.log"
SERVICES=("alvaos.service" "alvaos-backend.service" "alvaos-ui.service")
BACKUP_DIR="/var/lib/alvaos.bak"
BACKUP_CREATED=0
SETUP_STATUS_FILE="/var/lib/alvaos/setup_complete.json"
AUTH_FILE="/var/lib/alvaos/auth.json"

mkdir -p /var/log/alvaos /var/lib/alvaos || true

log() {
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*" | tee -a "$LOG_FILE"
}

fix_state_permissions() {
  if id -u alvaos >/dev/null 2>&1; then
    chown alvaos:alvaos /var/lib/alvaos/update_state.json /var/lib/alvaos/update_history.json 2>/dev/null || true
  fi
}

fix_alvaos_state_permissions() {
  if id -u alvaos >/dev/null 2>&1; then
    chown -R alvaos:alvaos /var/lib/alvaos /var/log/alvaos 2>/dev/null || true
  fi
}

repair_core_permissions() {
  if [ -e /usr/bin/sudo ]; then
    chown root:root /usr/bin/sudo 2>/dev/null || true
    chmod 4755 /usr/bin/sudo 2>/dev/null || true
  fi
  if [ -e /etc/sudoers.d/alvaos ]; then
    chown root:root /etc/sudoers.d/alvaos 2>/dev/null || true
    chmod 440 /etc/sudoers.d/alvaos 2>/dev/null || true
  fi
}

restore_setup_state_if_missing() {
  if [ "$BACKUP_CREATED" -ne 1 ] || [ ! -d "$BACKUP_DIR" ]; then
    return 0
  fi

  for state_path in "$SETUP_STATUS_FILE" "$AUTH_FILE"; do
    state_file="$(basename "$state_path")"
    if [ ! -e "$state_path" ] && [ -e "${BACKUP_DIR}/${state_file}" ]; then
      log "Restoring missing ${state_file} from pre-update backup"
      cp -a "${BACKUP_DIR}/${state_file}" "$state_path"
    fi
  done

  fix_alvaos_state_permissions
}

update_state() {
  local status="$1"
  local message="$2"
  local percent="${3:-0}"
  python3 - <<PY
import json
from datetime import datetime, timezone
payload = {
    "status": "${status}",
    "message": "${message}",
    "progress": {"percent": int(${percent})},
    "details": {"package": "${PACKAGE_PATH}"},
    "updated_at": datetime.now(timezone.utc).isoformat()
}
with open("/var/lib/alvaos/update_state.json", "w") as f:
    json.dump(payload, f, indent=2)
PY
  fix_state_permissions
}

# Appends to the update history the Updates page shows.
record_history() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
from datetime import datetime, timezone

history_path = "/var/lib/alvaos/update_history.json"
entry = {"type": sys.argv[1], "package": sys.argv[2], "version": sys.argv[3],
         "timestamp": datetime.now(timezone.utc).isoformat()}
try:
    with open(history_path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        data = []
except Exception:
    data = []
data.append(entry)
with open(history_path, "w") as f:
    json.dump(data, f, indent=2)
PY
  fix_state_permissions
}

# Cleanup function for error handling
cleanup() {
  local exit_code=$?
  if [ $exit_code -ne 0 ]; then
    log "ERROR: Update failed with exit code $exit_code"

    # Put the settings back as they were before the update. The update cache
    # (updates/) is kept: it holds the packages to go back to.
    if [ "$BACKUP_CREATED" -eq 1 ] && [ -d "$BACKUP_DIR" ]; then
      log "Attempting to restore from backup..."
      restore_backup || log "WARNING: Restore backup failed"
      log "Backup restoration attempted"
    fi

    # Restart services on failure
    log "Restarting services after failure..."
    repair_core_permissions
    if declare -F start_services >/dev/null 2>&1; then
      start_services
    fi
    # Last, so the restored backup does not bring back an old "installing" state.
    if [ "$ROLLED_BACK" -eq 1 ]; then
      record_history "rollback" "$WAY_BACK_PATH" "$PREVIOUS_VERSION"
    fi
    update_state "error" "$FAIL_MESSAGE" 0
  fi
}

# Set trap for cleanup on error, interrupt, or termination
trap cleanup EXIT ERR INT TERM

restore_backup() {
  if [ -d "$BACKUP_DIR" ]; then
    log "Restoring from backup..."
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete --exclude 'updates/' "$BACKUP_DIR/" /var/lib/alvaos/ || return 1
    else
      find /var/lib/alvaos -mindepth 1 -maxdepth 1 ! -name 'updates' -exec rm -rf {} + || return 1
      cp -r "$BACKUP_DIR"/. /var/lib/alvaos/ || return 1
    fi
    fix_alvaos_state_permissions
    return 0
  fi
  return 1
}

if [ -z "$PACKAGE_PATH" ]; then
  log "ERROR: package path required"
  exit 1
fi

repair_core_permissions

if [ ! -f "$PACKAGE_PATH" ]; then
  log "ERROR: package not found: $PACKAGE_PATH"
  exit 1
fi

if ! dpkg-deb --info "$PACKAGE_PATH" >/dev/null 2>&1; then
  log "ERROR: invalid .deb package"
  exit 1
fi

update_state "installing" "Backing up state" 50
if [ -d "/var/lib/alvaos" ]; then
    log "Backing up /var/lib/alvaos to /var/lib/alvaos.bak (excluding cache)"
    mkdir -p /var/lib/alvaos.bak
    # Use rsync if available for better performance and exclusion support
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete --exclude 'updates/' /var/lib/alvaos/ /var/lib/alvaos.bak/ && BACKUP_CREATED=1 || log "WARNING: Rsync backup failed"
    else
        # Fallback to cp but exclude updates/ if possible
        rm -rf /var/lib/alvaos.bak/*
        # Simple bash copy excluding updates
        find /var/lib/alvaos -maxdepth 1 ! -name 'updates' ! -name 'alvaos' -exec cp -r {} /var/lib/alvaos.bak/ \; && BACKUP_CREATED=1 || log "WARNING: CP backup failed"
    fi
fi

stop_services() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi
  for svc in "${SERVICES[@]}"; do
    log "Stopping service: $svc"
    systemctl stop "$svc" || true
  done
}

start_services() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi
  systemctl daemon-reload || true
  for svc in "${SERVICES[@]}"; do
    log "Starting service: $svc"
    systemctl start "$svc" || true
  done
}

# Waits until the backend answers on its port (two minutes by default).
backend_answers() {
  python3 - "$HEALTH_URL" "$HEALTH_WAIT_SECONDS" <<'PY'
import sys, time, urllib.request
deadline = time.time() + int(sys.argv[2])
while time.time() < deadline:
    try:
        with urllib.request.urlopen(sys.argv[1], timeout=5) as r:
            if r.status == 200:
                sys.exit(0)
    except Exception:
        pass
    time.sleep(3)
sys.exit(1)
PY
}

# Installs the version from before the update again.
go_back() {
  if [ -z "$WAY_BACK_PATH" ] || [ ! -f "$WAY_BACK_PATH" ]; then
    log "No earlier package to go back to"
    return 1
  fi
  update_state "installing" "The update did not work, going back to ${PREVIOUS_VERSION}" 90
  log "Going back to ${PREVIOUS_VERSION} with ${WAY_BACK_PATH}"
  stop_services
  if ! dpkg -i "$WAY_BACK_PATH" >> "$LOG_FILE" 2>&1; then
    log "ERROR: going back failed too"
    return 1
  fi
  repair_core_permissions
  ROLLED_BACK=1
  log "Went back to ${PREVIOUS_VERSION}"
  return 0
}

PREVIOUS_VERSION="$(dpkg-query -W -f='${Version}' alvaos-system 2>/dev/null || true)"
if [ -n "$WAY_BACK_PATH" ]; then
  log "Way back if this fails: ${PREVIOUS_VERSION} (${WAY_BACK_PATH})"
fi

update_state "installing" "Stopping services" 60
stop_services

install_failed=0
update_state "installing" "Installing package" 75
log "Installing package: $PACKAGE_PATH"
if ! dpkg -i "$PACKAGE_PATH" >> "$LOG_FILE" 2>&1; then
  install_failed=1
  update_state "error" "Install failed" 75
  log "ERROR: dpkg install failed"
fi

repair_core_permissions
restore_setup_state_if_missing

update_state "installing" "Running migrations" 85
log "Running migrations (if any)"
migrations_dir="/opt/alvaos/migrations"
if [ -d "$migrations_dir" ]; then
  set +e
  for script in $(ls -1 "$migrations_dir"/migrate_*.sh 2>/dev/null | sort); do
    if [ -x "$script" ]; then
      log "Executing migration: $script"
      "$script" >> "$LOG_FILE" 2>&1
    else
      log "Skipping non-executable migration: $script"
    fi
  done
  set -e
fi

update_state "installing" "Starting services" 95
start_services
if [ "$install_failed" -eq 0 ] && ! backend_answers; then
  install_failed=1
  log "ERROR: AlvaOS does not answer after the update"
fi

if [ "$install_failed" -ne 0 ]; then
  if go_back; then
    FAIL_MESSAGE="The update did not work. AlvaOS went back to version ${PREVIOUS_VERSION}."
  else
    FAIL_MESSAGE="The update did not work, and there was no earlier version to go back to. See the update log."
  fi
  log "Update completed with errors"
  exit 1
fi

PACKAGE_VERSION="$(dpkg-deb -f "$PACKAGE_PATH" Version 2>/dev/null || true)"
record_history "alvaos" "$PACKAGE_PATH" "$PACKAGE_VERSION"

update_state "idle" "Install complete" 100
log "Update completed successfully"
