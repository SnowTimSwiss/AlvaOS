#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_FILE="/var/log/alvaos/update-checker.log"

mkdir -p /var/log/alvaos /var/lib/alvaos || true

{
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] update_checker: starting"
  module_path=""

  if [ -f "/opt/alvaos/bin/update_manager.py" ]; then
    module_path="/opt/alvaos/bin"
  elif [ -f "/opt/alvaos/backend/update_manager.py" ]; then
    module_path="/opt/alvaos/backend"
  fi

  if [ -z "$module_path" ]; then
    echo "update_checker: update_manager.py not found"
    exit 1
  fi

  "$PYTHON_BIN" - <<PY
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "${module_path}")
from update_manager import UpdateManager

try:
    from alerts_manager import push_notification
except Exception:
    def push_notification(*args, **kwargs):
        pass

um = UpdateManager()
settings = um.get_settings()
auto_check = bool(settings.get("auto_check", True))
auto_apply = bool(settings.get("auto_apply", False))
auto_apply_debian = bool(settings.get("auto_apply_debian", False))
channel = settings.get("channel", "stable")

if not auto_check:
    um.set_update_state("idle", "Auto-check disabled", {"auto_check": False})
    sys.exit(0)

alvaos = um.check_alvaos_updates(channel)
debian = um.check_debian_updates()

details = {
    "alvaos": alvaos,
    "debian": debian,
    "auto_check": True,
    "channel": channel,
    "last_check": datetime.now(timezone.utc).isoformat()
}

# Auto-apply logic
if auto_apply and alvaos.get("update_available"):
    release = alvaos.get("release")
    assets = release.get("assets", [])
    # Find .deb asset
    deb_url = next((a["browser_download_url"] for a in assets if a["browser_download_url"].endswith(".deb")), None)

    if deb_url:
        print(f"Auto-applying update: {release.get('tag_name')}")
        try:
            # Download
            dl_res = um.download_update(release.get("tag_name"), deb_url)
            pkg_path = dl_res.get("path")

            # Apply
            if pkg_path:
                um.apply_alvaos_update(pkg_path)
                details["auto_applied"] = pkg_path
        except Exception as e:
            print(f"Auto-apply failed: {e}")
            details["auto_apply_error"] = str(e)

debian_updates = debian.get("updates") if isinstance(debian, dict) else None
if isinstance(debian_updates, list) and debian_updates:
    package_names = ", ".join(u.get("package", "") for u in debian_updates[:5] if u.get("package"))
    more = f" and {len(debian_updates) - 5} more" if len(debian_updates) > 5 else ""
    push_notification(
        severity="info",
        title="Debian updates available",
        message=f"{len(debian_updates)} Debian package update(s) available: {package_names}{more}.",
        source="updates",
        dismissible=True,
        link="updates.html",
        fingerprint="debian-updates-available",
    )

    if auto_apply_debian:
        print(f"Auto-applying {len(debian_updates)} Debian update(s)")
        try:
            apply_res = um.apply_debian_updates()
            details["debian_auto_applied"] = bool(apply_res.get("success"))
            if apply_res.get("success"):
                push_notification(
                    severity="success",
                    title="Debian updates applied",
                    message=f"{len(debian_updates)} Debian package update(s) were applied automatically.",
                    source="updates",
                    dismissible=True,
                    link="updates.html",
                )
            else:
                push_notification(
                    severity="critical",
                    title="Debian auto-update failed",
                    message=apply_res.get("error", "Failed to apply Debian updates automatically."),
                    source="updates",
                    dismissible=True,
                    link="updates.html",
                )
        except Exception as e:
            print(f"Debian auto-apply failed: {e}")
            details["debian_auto_apply_error"] = str(e)
            push_notification(
                severity="critical",
                title="Debian auto-update failed",
                message=str(e),
                source="updates",
                dismissible=True,
                link="updates.html",
            )

current_state = um.get_update_state()
if current_state.get("status") in ("installing", "downloading"):
    # Keep active install/download state; avoid overwriting progress with "idle".
    pass
else:
    um.set_update_state("idle", "Auto-check complete", details)
PY

  echo "update_checker: completed"
} >> "$LOG_FILE" 2>&1
