import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from packaging.version import Version, InvalidVersion


DEFAULT_SETTINGS = {
    "auto_check": True,
    "channel": "stable"
}


class UpdateManager:
    def __init__(self, repo="SnowTimSwiss/AlvaOS"):
        self.repo = repo
        self.state_file = "/var/lib/alvaos/update_state.json"
        self.history_file = "/var/lib/alvaos/update_history.json"
        self.settings_file = "/var/lib/alvaos/update_settings.json"
        self.cache_dir = "/var/lib/alvaos/updates"

    def ensure_dirs(self):
        Path("/var/lib/alvaos").mkdir(parents=True, exist_ok=True)
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

    def load_json(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def save_json(self, path, payload):
        self.ensure_dirs()
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)

    def get_settings(self):
        settings = self.load_json(self.settings_file, DEFAULT_SETTINGS.copy())
        if "auto_check" not in settings or "channel" not in settings:
            merged = DEFAULT_SETTINGS.copy()
            merged.update(settings or {})
            settings = merged
            self.save_json(self.settings_file, settings)
        return settings

    def save_settings(self, settings):
        merged = DEFAULT_SETTINGS.copy()
        merged.update(settings or {})
        if merged.get("channel") not in ("stable", "unstable"):
            merged["channel"] = "stable"
        self.save_json(self.settings_file, merged)
        return merged

    def get_update_state(self):
        return self.load_json(self.state_file, {"status": "idle"})

    def set_update_state(self, status, message=None, details=None):
        payload = {
            "status": status,
            "message": message or "",
            "details": details or {},
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        self.save_json(self.state_file, payload)
        return payload

    def update_progress(self, status, message, percent, details=None):
        payload = {
            "status": status,
            "message": message or "",
            "progress": {
                "percent": max(0, min(100, int(percent)))
            },
            "details": details or {},
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        self.save_json(self.state_file, payload)
        return payload

    def append_history(self, entry):
        history = self.load_json(self.history_file, [])
        if not isinstance(history, list):
            history = []
        history.append(entry)
        self.save_json(self.history_file, history)
        return history

    def get_update_history(self):
        data = self.load_json(self.history_file, [])
        return data if isinstance(data, list) else []

    def get_current_version(self):
        prod_path = "/etc/alvaos/VERSION"
        dev_path = os.path.join(os.path.dirname(__file__), "..", "VERSION")
        for path in (prod_path, dev_path):
            if os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        return f.read().strip()
                except Exception:
                    continue
        return "unknown"

    def normalize_version(self, version_str):
        if not version_str:
            return ""
        return version_str.strip().lstrip("v")

    def is_newer(self, current, latest):
        try:
            return Version(self.normalize_version(latest)) > Version(self.normalize_version(current))
        except InvalidVersion:
            return self.normalize_version(latest) != self.normalize_version(current)

    def github_headers(self):
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "AlvaOS-Update-Manager"
        }
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def format_release(self, release):
        assets = []
        for asset in release.get("assets", []):
            assets.append({
                "name": asset.get("name"),
                "size": asset.get("size"),
                "content_type": asset.get("content_type"),
                "browser_download_url": asset.get("browser_download_url")
            })
        return {
            "tag_name": release.get("tag_name"),
            "name": release.get("name"),
            "body": release.get("body"),
            "published_at": release.get("published_at"),
            "prerelease": release.get("prerelease", False),
            "assets": assets
        }

    def check_alvaos_updates(self, channel="stable"):
        channel = channel if channel in ("stable", "unstable") else "stable"
        self.set_update_state("checking", f"Checking {channel} channel")
        base_url = f"https://api.github.com/repos/{self.repo}"
        try:
            if channel == "stable":
                url = f"{base_url}/releases/latest"
                resp = requests.get(url, headers=self.github_headers(), timeout=15)
                resp.raise_for_status()
                release = resp.json()
            else:
                url = f"{base_url}/releases?per_page=20"
                resp = requests.get(url, headers=self.github_headers(), timeout=15)
                resp.raise_for_status()
                releases = [r for r in resp.json() if r.get("prerelease")]
                releases.sort(key=lambda r: r.get("published_at") or "", reverse=True)
                release = releases[0] if releases else {}
        except Exception as e:
            self.set_update_state("error", "Update check failed", {"error": str(e)})
            return {"error": str(e)}

        current_version = self.get_current_version()
        latest_version = self.normalize_version(release.get("tag_name", ""))
        update_available = False
        if latest_version and current_version != "unknown":
            update_available = self.is_newer(current_version, latest_version)

        result = {
            "current_version": current_version,
            "latest_version": latest_version,
            "update_available": update_available,
            "channel": channel,
            "release": self.format_release(release) if release else None
        }
        self.set_update_state("idle", "Check complete", {
            "update_available": update_available,
            "latest_version": latest_version
        })
        return result

    def download_update(self, version, url):
        if not url or not url.startswith("https://"):
            raise ValueError("Invalid download URL")
        self.ensure_dirs()
        self.update_progress("downloading", f"Downloading {version}", 0, {"version": version})

        parsed = urlparse(url)
        filename = os.path.basename(parsed.path) or f"alvaos-{version}.deb"
        if not filename.endswith(".deb"):
            filename = f"{filename}.deb"
        dest_path = os.path.join(self.cache_dir, filename)

        sha256 = hashlib.sha256()
        with requests.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        sha256.update(chunk)
                        if total > 0:
                            downloaded += len(chunk)
                            percent = int((downloaded / total) * 100)
                            self.update_progress(
                                "downloading",
                                f"Downloading {version}",
                                percent,
                                {"version": version, "downloaded": downloaded, "total": total}
                            )

        checksum = sha256.hexdigest()
        self.set_update_state("idle", "Download complete", {
            "path": dest_path,
            "sha256": checksum
        })
        return {"path": dest_path, "sha256": checksum}

    def run_command(self, cmd, timeout=60):
        if not cmd:
            return None, "Empty command"
        final_cmd = cmd
        if platform.system() == "Linux" and not self.is_root_user():
            final_cmd = ["sudo", "-n"] + cmd
        try:
            result = subprocess.run(final_cmd, capture_output=True, text=True, timeout=timeout, env={"LC_ALL": "C"})
            if result.returncode != 0 and "password is required" in (result.stderr or ""):
                return None, "System permission error: passwordless sudo not configured."
            return result, None
        except subprocess.TimeoutExpired:
            return None, "Command timed out"
        except Exception as e:
            return None, str(e)

    def is_root_user(self):
        try:
            return hasattr(os, "geteuid") and os.geteuid() == 0
        except Exception:
            return False

    def validate_deb(self, package_path):
        if not package_path or not package_path.endswith(".deb"):
            return False, "Invalid package path"
        if not os.path.exists(package_path):
            return False, "Package not found"
        res, err = self.run_command(["dpkg-deb", "--info", package_path], timeout=30)
        if err or not res or res.returncode != 0:
            return False, err or (res.stderr if res else "dpkg-deb failed")
        return True, None

    def apply_alvaos_update(self, package_path):
        if platform.system() != "Linux":
            return {"success": False, "error": "Updates are only supported on Linux"}
        ok, err = self.validate_deb(package_path)
        if not ok:
            self.set_update_state("error", "Package validation failed", {"error": err})
            return {"success": False, "error": err}

        self.set_update_state("installing", "Installing AlvaOS update", {"package": package_path})
        script_path = "/opt/alvaos/scripts/apply_update.sh"
        if os.path.exists(script_path):
            self.update_progress("installing", "Preparing installation", 55, {"package": package_path})
            res, run_err = self.run_command([script_path, package_path], timeout=1200)
        else:
            res, run_err = self.run_command(["dpkg", "-i", package_path], timeout=600)
        if run_err or not res or res.returncode != 0:
            error_msg = run_err or (res.stderr if res else "dpkg failed")
            self.set_update_state("error", "Install failed", {"error": error_msg})
            return {"success": False, "error": error_msg}

        entry = {
            "type": "alvaos",
            "package": package_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stdout": res.stdout[-2000:] if res.stdout else ""
        }
        self.append_history(entry)
        self.set_update_state("idle", "Install complete", {"package": package_path})
        return {"success": True}

    def check_debian_updates(self):
        if platform.system() != "Linux":
            return {"error": "Debian updates are only supported on Linux"}
        self.set_update_state("checking", "Checking Debian updates")
        res, err = self.run_command(["apt", "list", "--upgradable"], timeout=60)
        if err or not res or res.returncode != 0:
            self.set_update_state("error", "Debian update check failed", {"error": err or res.stderr})
            return {"error": err or (res.stderr if res else "apt failed")}

        updates = []
        for line in (res.stdout or "").splitlines():
            if line.startswith("Listing") or not line.strip():
                continue
            match = re.match(r"^([^/]+)/[^ ]+\s+([^\s]+)", line.strip())
            if match:
                updates.append({
                    "package": match.group(1),
                    "version": match.group(2)
                })

        self.set_update_state("idle", "Debian check complete", {"count": len(updates)})
        return {"updates": updates}

    def apply_debian_updates(self, packages=None):
        if platform.system() != "Linux":
            return {"success": False, "error": "Debian updates are only supported on Linux"}
        self.set_update_state("installing", "Applying Debian updates")
        self.run_command(["apt-get", "update"], timeout=120)

        cmd = ["apt-get", "upgrade", "-y"]
        if packages:
            cmd = ["apt-get", "install", "-y"] + list(packages)

        res, err = self.run_command(cmd, timeout=1200)
        if err or not res or res.returncode != 0:
            error_msg = err or (res.stderr if res else "apt-get failed")
            self.set_update_state("error", "Debian updates failed", {"error": error_msg})
            return {"success": False, "error": error_msg}

        entry = {
            "type": "debian",
            "packages": packages or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stdout": res.stdout[-2000:] if res.stdout else ""
        }
        self.append_history(entry)
        self.set_update_state("idle", "Debian updates complete")
        return {"success": True}

    def scan_offline_packages(self, root_path=None):
        if not root_path:
            root_path = "/media"
        if not os.path.exists(root_path):
            return {"packages": []}

        packages = []
        for dirpath, dirnames, filenames in os.walk(root_path):
            for name in filenames:
                if name.endswith(".deb"):
                    full_path = os.path.join(dirpath, name)
                    packages.append({
                        "path": full_path,
                        "name": name,
                        "size": os.path.getsize(full_path)
                    })
        return {"packages": packages}

    def validate_offline_package(self, usb_path):
        return self.validate_deb(usb_path)
