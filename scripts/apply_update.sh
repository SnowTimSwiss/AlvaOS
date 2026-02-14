#!/usr/bin/env bash
set -euo pipefail

PACKAGE_PATH="${1:-}"
LOG_FILE="/var/log/alvaos/update-apply.log"
SERVICES=("alvaos.service" "alvaos-backend.service" "alvaos-ui.service")

mkdir -p /var/log/alvaos /var/lib/alvaos || true

log() {
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*" | tee -a "$LOG_FILE"
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
}

if [ -z "$PACKAGE_PATH" ]; then
  log "ERROR: package path required"
  exit 1
fi

if [ ! -f "$PACKAGE_PATH" ]; then
  log "ERROR: package not found: $PACKAGE_PATH"
  exit 1
fi

if ! dpkg-deb --info "$PACKAGE_PATH" >/dev/null 2>&1; then
  log "ERROR: invalid .deb package"
  exit 1
fi

update_state "installing" "Backing up user data" 50
if [ -d "/var/lib/alvaos" ]; then
    log "Backing up /var/lib/alvaos to /var/lib/alvaos.bak"
    cp -r /var/lib/alvaos /var/lib/alvaos.bak || log "WARNING: Backup failed"
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

any_service_active() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi
  for svc in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$svc"; then
      return 0
    fi
  done
  return 1
}

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
if ! any_service_active; then
  install_failed=1
  update_state "error" "No AlvaOS service is running after update" 95
  log "ERROR: No known AlvaOS service is active after update"
fi

if [ "$install_failed" -ne 0 ]; then
  log "Update completed with errors"
  exit 1
fi

python3 - <<PY
import json
from datetime import datetime, timezone

history_path = "/var/lib/alvaos/update_history.json"
entry = {
    "type": "alvaos",
    "package": "${PACKAGE_PATH}",
    "timestamp": datetime.now(timezone.utc).isoformat()
}

try:
    with open(history_path, "r") as f:
        data = json.load(f)
    if not isinstance(data, list):
        data = []
except Exception:
    data = []

data.append(entry)

with open(history_path, "w") as f:
    json.dump(data, f, indent=2)
PY

update_state "idle" "Install complete" 100
log "Update completed successfully"
