import hashlib
import json
import os
import platform
import re
import subprocess
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
try:
    from packaging.version import Version, InvalidVersion
except Exception:
    class InvalidVersion(ValueError):
        pass

    class Version:
        PRECEDENCE = {
            "a": 0,
            "alpha": 0,
            "b": 1,
            "beta": 1,
            "pre": 2,
            "rc": 3,
            "": 4,
        }

        def __init__(self, version):
            self.original = str(version or "")
            self._key = self._parse(self.original)

        @classmethod
        def _parse(cls, value):
            v = (value or "").strip().lower()
            m = re.match(r"^(\d+(?:\.\d+)*)(?:(a|alpha|b|beta|pre|rc)(\d*))?$", v)
            if not m:
                raise InvalidVersion(f"Invalid version: {value}")

            release = [int(part) for part in m.group(1).split(".")]
            while len(release) > 1 and release[-1] == 0:
                release.pop()

            pre_tag = m.group(2) or ""
            pre_num = int(m.group(3) or "0")
            return (tuple(release), cls.PRECEDENCE.get(pre_tag, 0), pre_num)

        def __gt__(self, other):
            return self._key > other._key

CMD = {
    'DPKG_DEB': '/usr/bin/dpkg-deb',
    'DPKG': '/usr/bin/dpkg',
    'SYSTEMD_RUN': '/usr/bin/systemd-run',
    'BASH': '/usr/bin/bash',
    'APT': '/usr/bin/apt',
    'APT_GET': '/usr/bin/apt-get',
    'LSBLK': '/usr/bin/lsblk',
    'MOUNT': '/usr/bin/mount',
    'UMOUNT': '/usr/bin/umount',
    'NOHUP': '/usr/bin/nohup'
}


DEFAULT_SETTINGS = {
    "auto_check": True,
    "auto_apply": False,
    "channel": "stable"
}


class UpdateManager:
    def __init__(self, repo="SnowTimSwiss/AlvaOS"):
        self.repo = repo
        self.state_file = "/var/lib/alvaos/update_state.json"
        self.history_file = "/var/lib/alvaos/update_history.json"
        self.settings_file = "/var/lib/alvaos/update_settings.json"
        self.cache_dir = "/var/lib/alvaos/updates"
        self.github_cache_file = "/var/lib/alvaos/github_cache.json"

    def ensure_dirs(self):
        Path("/var/lib/alvaos").mkdir(parents=True, exist_ok=True)
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

    def cleanup_cache(self, keep=3):
        """Keep only the N most recent .deb files in the cache."""
        try:
            if not os.path.exists(self.cache_dir):
                return
            files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir) 
                     if f.endswith(".deb") and os.path.isfile(os.path.join(self.cache_dir, f))]
            if len(files) <= keep:
                return
            
            # Sort by modification time (most recent first)
            files.sort(key=os.path.getmtime, reverse=True)
            for f in files[keep:]:
                try:
                    os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass

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
        if "auto_check" not in settings or "auto_apply" not in settings or "channel" not in settings:
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
        merged["auto_check"] = bool(merged.get("auto_check", True))
        merged["auto_apply"] = bool(merged.get("auto_apply", False))
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
        # Normalize common tag styles like "Release_Candidate_1" to "rc1".
        v = version_str.strip().lstrip("v").lower()
        v = v.replace("_", ".").replace("-", ".")
        v = re.sub(r"pre[.\-]*release", "pre", v)
        v = re.sub(r"preview", "pre", v)
        v = re.sub(r"release[.]*candidate", "rc", v)
        v = re.sub(r"candidate", "rc", v)
        v = re.sub(r"alpha", "a", v)
        v = re.sub(r"beta", "b", v)
        v = re.sub(r"[^0-9a-z.+]", "", v)
        v = re.sub(r"\.(rc|a|b|pre)", r"\1", v)
        v = re.sub(r"(rc|a|b|pre)\.", r"\1", v)
        v = re.sub(r"\.+", ".", v).strip(".")
        return v

    def is_newer(self, current, latest):
        if not current or not latest:
            return False
        try:
            return Version(self.normalize_version(latest)) > Version(self.normalize_version(current))
        except InvalidVersion:
            # If parsing fails, do NOT assume it's an update. 
            # Only return True if we are sure.
            print(f"Version check warning: Could not parse versions '{current}' or '{latest}'")
            return False

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

    def check_alvaos_updates(self, channel="stable", force_refresh=False):
        channel = channel if channel in ("stable", "unstable") else "stable"
        self.set_update_state("checking", f"Checking {channel} channel")
        base_url = f"https://api.github.com/repos/{self.repo}"
        current_version = self.get_current_version()
        
        # 1. Check local cache first
        cache = self.load_json(self.github_cache_file, {})
        cache_key = f"{self.repo}/{channel}"
        cached_entry = cache.get(cache_key)
        
        now = datetime.now(timezone.utc)
        # Cache for 1 hour to stay safe with rate limits.
        # Manual checks may request force_refresh=True to bypass this.
        if cached_entry and not force_refresh:
            try:
                cached_at_str = cached_entry.get("cached_at")
                cached_at = datetime.fromisoformat(cached_at_str) if cached_at_str else None
            except Exception:
                cached_at = None
            if cached_at and (now - cached_at).total_seconds() < 3600:
                print(f"Using cached GitHub release for {cache_key}")
                release = cached_entry.get("release")
                return self._build_check_result(current_version, release, channel)

        release = None
        error = None
        rate_limit_hit = False
        
        try:
            if channel == "stable":
                url = f"{base_url}/releases/latest"
                resp = requests.get(url, headers=self.github_headers(), timeout=15)
                if resp.status_code == 404:
                    release = None
                elif resp.status_code == 403:
                    rate_limit_hit = True
                    error = "GitHub Rate Limit Exceeded"
                else:
                    resp.raise_for_status()
                    release = resp.json()
            else:
                url = f"{base_url}/releases?per_page=20"
                resp = requests.get(url, headers=self.github_headers(), timeout=15)
                if resp.status_code == 403:
                    rate_limit_hit = True
                    error = "GitHub Rate Limit Exceeded"
                else:
                    resp.raise_for_status()
                    releases = [r for r in resp.json() if r.get("prerelease")]
                    releases.sort(key=lambda r: r.get("published_at") or "", reverse=True)
                    release = releases[0] if releases else {}
        except Exception as e:
            error = str(e)
            print(f"Update check error: {error}")

        # 2. If rate limit hit or error, try to fallback to old cache even if expired
        if (rate_limit_hit or error) and cached_entry:
            print(f"Falling back to expired cache due to {error}")
            release = cached_entry.get("release")
            result = self._build_check_result(current_version, release, channel)
            result["warning"] = f"Using cached data: {error}"
            self.set_update_state("idle", "Check complete (cached fallback)", {
                "update_available": result["update_available"],
                "latest_version": result["latest_version"]
            })
            return result

        if error and not rate_limit_hit:
            self.set_update_state("error", "Update check failed", {"error": error})
        
        # 3. Save to cache if successful
        if release is not None:
            cache[cache_key] = {
                "cached_at": now.isoformat(),
                "release": release
            }
            self.save_json(self.github_cache_file, cache)

        return self._build_check_result(current_version, release, channel, error)

    def _build_check_result(self, current_version, release, channel, error=None):
        latest_version = self.normalize_version(release.get("tag_name", "")) if release else ""
        update_available = False
        if latest_version and current_version != "unknown":
            update_available = self.is_newer(current_version, latest_version)

        result = {
            "current_version": current_version,
            "latest_version": release.get("tag_name", "") if release else "",
            "update_available": update_available,
            "channel": channel,
            "release": self.format_release(release) if release else None
        }
        if error:
            result["error"] = error
        else:
            self.set_update_state("idle", "Check complete", {
                "update_available": update_available,
                "latest_version": latest_version
            })
        return result

    def download_update(self, version, url):
        if not url or not url.startswith("https://"):
            raise ValueError("Invalid download URL")
        self.ensure_dirs()
        self.cleanup_cache() 
        self.update_progress("downloading", f"Downloading {version}", 0, {"version": version})

        parsed = urlparse(url)
        filename = os.path.basename(parsed.path) or f"alvaos-{version}.deb"
        if not filename.endswith(".deb"):
            filename = f"{filename}.deb"
        dest_path = os.path.join(self.cache_dir, filename)

        sha256 = hashlib.sha256()
        try:
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
        except Exception as e:
            self.set_update_state("error", "Download failed", {"error": str(e)})
            raise

        checksum = sha256.hexdigest()
        self.set_update_state("idle", "Download complete", {
            "path": dest_path,
            "sha256": checksum
        })
        return {"path": dest_path, "sha256": checksum}

    def run_command(self, cmd, timeout=60, extra_env=None):
        if not cmd:
            return None, "Empty command"
        
        # Prepare final command using sudo if not root
        final_cmd = []
        if cmd[0] == 'sudo':
             # Use absolute path for sudo -n if we're doing it manually or trust build_privileged style
             # But here we stick to the simpler logic of this module for now, just adding -n
             final_cmd = ['sudo', '-n'] + cmd[1:]
        elif platform.system() == "Linux" and not self.is_root_user():
            final_cmd = ["sudo", "-n"] + cmd
        else:
            final_cmd = cmd
            
        try:
            env = {"LC_ALL": "C"}
            if extra_env and isinstance(extra_env, dict):
                env.update(extra_env)
            result = subprocess.run(final_cmd, capture_output=True, text=True, timeout=timeout, env=env)
            if result.returncode != 0:
                stderr_text = (result.stderr or "").strip()
                stdout_text = (result.stdout or "").strip()
                stderr_low = stderr_text.lower()
                if "password is required" in stderr_low or "a password is required" in stderr_low:
                    cmd_str = " ".join(final_cmd)
                    return None, f"System permission error: Passwordless sudo is not configured for command: {cmd_str}"
                cmd_str = " ".join(final_cmd)
                detail = stderr_text or stdout_text or f"exit code {result.returncode}"
                return result, f"Command failed ({result.returncode}): {cmd_str}: {detail}"
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
        res, err = self.run_command([CMD['DPKG_DEB'], "--info", package_path], timeout=30)
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
        
        # Use systemd-run to detach the update process if possible
        use_systemd_run = False
        if os.path.exists(script_path):
            use_systemd_run = shutil.which(CMD['SYSTEMD_RUN']) is not None

        if use_systemd_run and os.path.exists(script_path):
            self.update_progress("installing", "Starting update service", 10, {"package": package_path})
            # Run using systemd-run to decouple from the backend process
            # We construct the command manually to avoid run_command waiting
            try:
                # systemd-run --no-block returns immediately; validate return code first.
                run_prefix = [] if self.is_root_user() else ["sudo", "-n"]
                final_cmd = run_prefix + [
                    CMD['SYSTEMD_RUN'],
                    "--unit=alvaos-updater-" + secrets.token_hex(4),
                    "--description=AlvaOS Updater",
                    "--no-block", # Critical: don't wait for it
                    CMD['BASH'], script_path, package_path
                ]
                start_res = subprocess.run(
                    final_cmd,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env={"LC_ALL": "C"}
                )
                if start_res.returncode != 0:
                    detail = (start_res.stderr or start_res.stdout or "").strip() or f"exit code {start_res.returncode}"
                    raise RuntimeError(detail)
                
                # Return success immediately because the script will handle the rest
                return {"success": True, "message": "Update process started in background"}
            except Exception as e:
                 # Fallback to direct Popen if systemd-run implies errors (though unlikely on Linux with systemd)
                 try:
                    run_prefix = [] if self.is_root_user() else ["sudo", "-n"]
                    fallback_cmd = run_prefix + [CMD['NOHUP'], CMD['BASH'], script_path, package_path]
                    fallback_res = subprocess.run(
                        fallback_cmd,
                        capture_output=True,
                        text=True,
                        timeout=20,
                        start_new_session=True,
                        env={"LC_ALL": "C"}
                    )
                    if fallback_res.returncode != 0:
                        detail = (fallback_res.stderr or fallback_res.stdout or "").strip() or f"exit code {fallback_res.returncode}"
                        raise RuntimeError(detail)
                    return {"success": True, "message": "Update process started in background (nohup)"}
                 except Exception as e2:
                    return {"success": False, "error": f"Failed to launch update script: {e} / {e2}"}

        else:
            # Fallback for systems without script or systemd
            res, run_err = self.run_command([CMD['DPKG'], "-i", package_path], timeout=600)
            if run_err or not res or res.returncode != 0:
                error_msg = run_err or (res.stderr if res else "dpkg failed")
                self.set_update_state("error", "Install failed", {"error": error_msg})
                return {"success": False, "error": error_msg}

        # If we fell back to dpkg -i (synchronous), record history
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
        res, err = self.run_command([CMD['APT'], "list", "--upgradable"], timeout=60)
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

        selected_packages = []
        if packages is not None:
            if isinstance(packages, str):
                packages = [packages]
            if not isinstance(packages, list):
                return {"success": False, "error": "packages must be a list or string"}

            seen = set()
            for item in packages:
                if item is None:
                    continue
                pkg = str(item).strip()
                if not pkg:
                    continue
                # Accept package names and optional package=version pin format.
                if not re.match(r"^[a-zA-Z0-9.+:-]+(?:=\S+)?$", pkg):
                    return {"success": False, "error": f"Invalid package name: {pkg}"}
                if pkg not in seen:
                    seen.add(pkg)
                    selected_packages.append(pkg)

        self.set_update_state("installing", "Applying Debian updates")
        apt_env = {"DEBIAN_FRONTEND": "noninteractive"}
        apt_base = [CMD['APT_GET'], "-o", "Dpkg::Lock::Timeout=120"]

        update_res, update_err = self.run_command(apt_base + ["update"], timeout=180, extra_env=apt_env)
        if update_err or not update_res or update_res.returncode != 0:
            error_msg = update_err or (update_res.stderr if update_res else "apt-get update failed")
            self.set_update_state("error", "Debian updates failed", {"error": error_msg})
            return {"success": False, "error": error_msg}

        cmd = apt_base + ["upgrade", "-y"]
        if selected_packages:
            cmd = apt_base + ["install", "-y"] + selected_packages

        res, err = self.run_command(cmd, timeout=1800, extra_env=apt_env)
        if err or not res or res.returncode != 0:
            error_msg = err or (res.stderr if res else "apt-get failed")
            self.set_update_state("error", "Debian updates failed", {"error": error_msg})
            return {"success": False, "error": error_msg}

        entry = {
            "type": "debian",
            "packages": selected_packages,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stdout": res.stdout[-2000:] if res.stdout else ""
        }
        self.append_history(entry)
        self.set_update_state("idle", "Debian updates complete")
        return {"success": True}

    def scan_offline_packages(self, root_path=None):

      
        packages = []
        
        # 1. Scan explicit path if provided
        if root_path:
            return self._scan_path(root_path)

        # 2. Scan standard mount points
        for base in ["/media", "/mnt"]:
            if os.path.exists(base):
                packages.extend(self._scan_path(base).get("packages", []))

        # 3. Detect and temporarily mount removable devices
        if platform.system() == "Linux":
            try:
                # Get removable devices from lsblk
                result = subprocess.run(
                    [CMD['LSBLK'], '-J', '-o', 'NAME,MOUNTPOINT,RM,TYPE,FSTYPE'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    for device in data.get('blockdevices', []):
                        # We look for partitions on removable disks
                        if device.get('rm'):
                            children = device.get('children', [])
                            if not children:
                                # Sometimes USB sticks have no partition table, just /dev/sdb
                                children = [device]
                                
                            for part in children:
                                # Accept 'part' or 'disk' if it has no mountpoint
                                if not part.get('mountpoint'):
                                    # Candidate for temporary mount
                                    part_name = part.get('name')
                                    # Relax fstype check completely -> let mount auto-detect or fail
                                    # But filter out linux_raid_member etc if needed. 
                                    # For now, just try to mount.
                                    pkgs = self._temp_mount_and_scan(part_name)
                                    packages.extend(pkgs)
            except Exception as e:
                print(f"Error during USB scan: {e}")

        # Remove duplicates by full path
        unique_packages = []
        seen_paths = set()
        for p in packages:
            if p["path"] not in seen_paths:
                unique_packages.append(p)
                seen_paths.add(p["path"])

        return {"packages": unique_packages}

    def _scan_path(self, path):
        packages = []
        if not os.path.exists(path):
            return {"packages": []}
            
        for dirpath, dirnames, filenames in os.walk(path):
            # Limit depth to avoid scanning entire OS if someone points to /
            depth = dirpath.count(os.sep) - path.count(os.sep)
            if depth > 3:
                del dirnames[:] # Don't go deeper
                continue
                
            for name in filenames:
                if name.endswith(".deb"):
                    full_path = os.path.join(dirpath, name)
                    try:
                        packages.append({
                            "path": full_path,
                            "name": name,
                            "size": os.path.getsize(full_path)
                        })
                    except OSError:
                        pass
        return {"packages": packages}

    def _temp_mount_and_scan(self, dev_name):
        """Temporarily mount a device and scan it for packages."""
        dev_path = f"/dev/{dev_name}"
        mount_point = f"/tmp/alvaos_scan_{dev_name}"
        packages = []
        
        try:
            os.makedirs(mount_point, exist_ok=True)
            # Use run_command to handle sudo correctly
            res, err = self.run_command([CMD['MOUNT'], '-o', 'ro', dev_path, mount_point], timeout=10)
            if res and res.returncode == 0:
                try:
                    scan_res = self._scan_path(mount_point)
                    for pkg in scan_res.get("packages", []):
                        cached_path = self._cache_offline_package(pkg.get("path"))
                        if not cached_path:
                            continue
                        cached_pkg = dict(pkg)
                        cached_pkg["source_path"] = pkg.get("path")
                        cached_pkg["path"] = cached_path
                        packages.append(cached_pkg)
                finally:
                    self.run_command([CMD['UMOUNT'], mount_point], timeout=10)
            
            # Cleanup mount point
            if os.path.exists(mount_point):
                os.rmdir(mount_point)
        except Exception as e:
            print(f"Failed to temp mount {dev_path}: {e}")
            
        return packages

    def _cache_offline_package(self, src_path):
        if not src_path or not os.path.exists(src_path):
            return None
        self.ensure_dirs()
        offline_dir = os.path.join(self.cache_dir, "offline")
        os.makedirs(offline_dir, exist_ok=True)
        base = os.path.basename(src_path)
        dest = os.path.join(offline_dir, base)
        try:
            if os.path.exists(dest):
                if os.path.getsize(dest) == os.path.getsize(src_path):
                    return dest
                name, ext = os.path.splitext(base)
                dest = os.path.join(offline_dir, f"{name}-{secrets.token_hex(4)}{ext}")
            shutil.copy2(src_path, dest)
            return dest
        except Exception as e:
            print(f"Failed to cache offline package {src_path}: {e}")
            return None

    def validate_offline_package(self, usb_path):
        return self.validate_deb(usb_path)
