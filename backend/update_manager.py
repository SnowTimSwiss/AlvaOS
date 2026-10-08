import hashlib
import json
import os
import platform
import re
import subprocess
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from common import PRIV_HELPER, build_privileged_cmd, privilege_error_message
from update_signing import read_deb_version, signature_path_for

try:
    from packaging.version import Version, InvalidVersion
except Exception:
    class InvalidVersion(ValueError):  # type: ignore[no-redef]
        pass

    class Version:  # type: ignore[no-redef]
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
    'APT': '/usr/bin/apt',
    'APT_GET': '/usr/bin/apt-get',
    'LSBLK': '/usr/bin/lsblk',
    'MOUNT': '/usr/bin/mount',
    'UMOUNT': '/usr/bin/umount',
    'TEE': '/usr/bin/tee',
}

# Offline-update USB sticks are mounted here by the privilege helper.
SCAN_MOUNT_BASE = '/run/alvaos-scan'


DEFAULT_SETTINGS = {
    "auto_check": True,
    "auto_apply": False,
    "auto_apply_debian": False,
    "channel": "stable"
}

ALVAOS_PACKAGE_NAME = "alvaos-system"

DEBIAN_RELEASES = [
    {"version": "11", "codename": "bullseye"},
    {"version": "12", "codename": "bookworm"},
    {"version": "13", "codename": "trixie"},
    {"version": "14", "codename": "forky"},
]
DEBIAN_CODENAME_TO_VERSION = {r["codename"]: r["version"] for r in DEBIAN_RELEASES}
DEBIAN_VERSION_TO_CODENAME = {r["version"]: r["codename"] for r in DEBIAN_RELEASES}
DEFAULT_DEBIAN_TARGET_CODENAME = "trixie"
DEFAULT_DEBIAN_TARGET_VERSION = DEBIAN_CODENAME_TO_VERSION.get(DEFAULT_DEBIAN_TARGET_CODENAME, "13")


class UpdateManager:
    def __init__(self, repo="SnowTimSwiss/AlvaOS"):
        self.repo = repo
        self.state_dir = self._resolve_state_dir()
        self.state_file = os.path.join(self.state_dir, "update_state.json")
        self.history_file = os.path.join(self.state_dir, "update_history.json")
        self.settings_file = os.path.join(self.state_dir, "update_settings.json")
        self.cache_dir = os.path.join(self.state_dir, "updates")
        self.github_cache_file = os.path.join(self.state_dir, "github_cache.json")

    def _resolve_state_dir(self):
        preferred = Path("/var/lib/alvaos")
        try:
            preferred.mkdir(parents=True, exist_ok=True)
            probe = preferred / ".alvaos_write_test"
            with open(probe, "w") as f:
                f.write("ok")
            probe.unlink(missing_ok=True)
            return str(preferred)
        except Exception:
            fallback = (Path(__file__).resolve().parent / ".." / ".alvaos_state").resolve()
            fallback.mkdir(parents=True, exist_ok=True)
            return str(fallback)

    def ensure_dirs(self):
        Path(self.state_dir).mkdir(parents=True, exist_ok=True)
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

    def cleanup_cache(self, keep=3):
        """Keep only the N most recent .deb files in the cache, and always the
        package of the version installed now: it is the way back when an
        update fails (apply_update.sh) or is not liked (rollback)."""
        try:
            if not os.path.exists(self.cache_dir):
                return
            files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir)
                     if f.endswith(".deb") and os.path.isfile(os.path.join(self.cache_dir, f))]
            if len(files) <= keep:
                return
            installed = self.normalize_version(self.get_current_version())

            # Sort by modification time (most recent first)
            files.sort(key=os.path.getmtime, reverse=True)
            for f in files[keep:]:
                if installed and self.normalize_version(read_deb_version(f) or "") == installed:
                    continue
                for path in (f, signature_path_for(f)):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
        except Exception:
            pass

    def way_back_ready(self):
        """Whether the signed package of the installed version is in the cache."""
        installed = self.normalize_version(self.get_current_version())
        try:
            names = os.listdir(self.cache_dir)
        except OSError:
            return False
        for name in names:
            path = os.path.join(self.cache_dir, name)
            if name.endswith(".deb") and os.path.isfile(path) and os.path.isfile(signature_path_for(path)) \
                    and installed and self.normalize_version(read_deb_version(path) or "") == installed:
                return True
        return False

    def ensure_way_back(self, get=None):
        """After an install from the installer or a USB stick the package of
        the running version is not in the cache, so a failed update could not
        go back. Fetch it (and its signature) from that version's release.
        The signature is checked by the helper when it is used, as always.
        Returns 'ready', 'fetched' or why not."""
        if self.way_back_ready():
            return "ready"
        version = self.normalize_version(self.get_current_version())
        if not version or version == "unknown":
            return "unknown version"
        get = get or requests.get
        release = None
        for tag in (f"v{version}", version):
            try:
                res = get(f"https://api.github.com/repos/{self.repo}/releases/tags/{tag}",
                          headers=self.github_headers(), timeout=30)
            except Exception as e:  # noqa: BLE001 - offline
                return f"offline: {e}"
            if getattr(res, "status_code", 0) == 200:
                release = res.json()
                break
        if not release:
            return "no release for this version"
        assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
        deb = next((name for name in assets if name and name.endswith(".deb") and f"{name}.sig" in assets), None)
        if not deb or not str(assets[deb]).startswith("https://"):
            return "no signed package in the release"
        self.ensure_dirs()
        dest = os.path.join(self.cache_dir, os.path.basename(deb))
        try:
            for url, path, limit in ((assets[deb], dest, 512 * 1024 * 1024),
                                     (assets[f"{deb}.sig"], signature_path_for(dest), 4096)):
                with get(url, stream=True, timeout=60) as res:
                    res.raise_for_status()
                    size = 0
                    with open(f"{path}.part", "wb") as f:
                        for chunk in res.iter_content(chunk_size=1024 * 1024):
                            size += len(chunk)
                            if size > limit:
                                raise ValueError("too large")
                            f.write(chunk)
                os.replace(f"{path}.part", path)
        except Exception as e:  # noqa: BLE001 - try again at the next start
            for path in (dest, signature_path_for(dest), f"{dest}.part", f"{signature_path_for(dest)}.part"):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return f"download failed: {e}"
        if self.normalize_version(read_deb_version(dest) or "") != version:
            for path in (dest, signature_path_for(dest)):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return "the release package has another version"
        return "fetched"

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
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(parent))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def get_settings(self):
        settings = self.load_json(self.settings_file, DEFAULT_SETTINGS.copy())
        if any(key not in settings for key in DEFAULT_SETTINGS):
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
        merged["auto_apply_debian"] = bool(merged.get("auto_apply_debian", False))
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

    def _read_os_release(self):
        data = {}
        path = "/etc/os-release"
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    value = value.strip().strip('"').strip("'")
                    data[key.strip()] = value
        except Exception:
            return {}
        return data

    def get_debian_os_info(self):
        info = self._read_os_release()
        distro_id = str(info.get("ID", "") or "").strip().lower()
        version = str(info.get("VERSION_ID", "") or "").strip()
        codename = str(info.get("VERSION_CODENAME", "") or info.get("DEBIAN_CODENAME", "") or "").strip().lower()

        # Normalize version like "13.1" -> "13"
        major_match = re.match(r"^(\d+)", version)
        if major_match:
            version = major_match.group(1)

        if not version and codename in DEBIAN_CODENAME_TO_VERSION:
            version = DEBIAN_CODENAME_TO_VERSION[codename]
        if not codename and version in DEBIAN_VERSION_TO_CODENAME:
            codename = DEBIAN_VERSION_TO_CODENAME[version]

        return {
            "id": distro_id,
            "version": version,
            "codename": codename
        }

    def _fetch_debian_stable_codename(self):
        url = "https://deb.debian.org/debian/dists/stable/Release"
        try:
            resp = requests.get(url, timeout=6)
            resp.raise_for_status()
            for line in (resp.text or "").splitlines():
                if line.lower().startswith("codename:"):
                    value = line.split(":", 1)[1].strip().lower()
                    if re.match(r"^[a-z][a-z0-9-]*$", value):
                        return value
        except Exception:
            return None
        return None

    def _release_index(self, codename):
        if not codename:
            return -1
        codename = str(codename).strip().lower()
        for idx, rel in enumerate(DEBIAN_RELEASES):
            if rel["codename"] == codename:
                return idx
        return -1

    def _determine_debian_upgrade_target(self, current):
        current = current or {}
        current_codename = str(current.get("codename", "") or "").strip().lower()
        current_version = str(current.get("version", "") or "").strip()

        if not current_codename and current_version in DEBIAN_VERSION_TO_CODENAME:
            current_codename = DEBIAN_VERSION_TO_CODENAME[current_version]
        if not current_version and current_codename in DEBIAN_CODENAME_TO_VERSION:
            current_version = DEBIAN_CODENAME_TO_VERSION[current_codename]

        stable_codename = self._fetch_debian_stable_codename() or DEFAULT_DEBIAN_TARGET_CODENAME
        stable_version = DEBIAN_CODENAME_TO_VERSION.get(stable_codename, "")

        current_idx = self._release_index(current_codename)
        stable_idx = self._release_index(stable_codename)

        target_codename = ""
        target_version = ""
        stepwise = False
        reason = "up_to_date"

        if current_codename and stable_codename and current_codename == stable_codename:
            reason = "up_to_date"
        elif current_idx >= 0 and stable_idx >= 0 and current_idx < stable_idx:
            if (stable_idx - current_idx) > 1:
                target_rel = DEBIAN_RELEASES[current_idx + 1]
                target_codename = target_rel["codename"]
                target_version = target_rel["version"]
                stepwise = True
                reason = "stepwise_upgrade_required"
            else:
                target_codename = stable_codename
                target_version = stable_version
                reason = "stable_upgrade_available"
        elif current_idx >= 0 and stable_idx >= 0 and current_idx > stable_idx:
            # Current release is newer than known stable (or mixed sources). Do not downgrade automatically.
            reason = "current_newer_than_stable"
        elif stable_codename and current_codename and current_codename != stable_codename:
            target_codename = stable_codename
            target_version = stable_version
            reason = "stable_upgrade_available"
        elif not current_codename and stable_codename:
            target_codename = stable_codename
            target_version = stable_version
            reason = "stable_upgrade_available"

        available = bool(target_codename and target_codename != current_codename)

        return {
            "available": available,
            "reason": reason,
            "stepwise": stepwise,
            "current": {
                "version": current_version,
                "codename": current_codename
            },
            "target": {
                "version": target_version,
                "codename": target_codename
            },
            "stable": {
                "version": stable_version,
                "codename": stable_codename
            }
        }

    def check_debian_os_upgrade(self):
        if platform.system() != "Linux":
            return {"available": False, "error": "Debian OS upgrades are only supported on Linux"}
        current = self.get_debian_os_info()
        distro_id = str(current.get("id", "") or "").strip().lower()
        if distro_id and distro_id != "debian":
            return {"available": False, "error": f"Unsupported distribution for Debian OS upgrade: {distro_id}"}
        result = self._determine_debian_upgrade_target(current)
        return result

    def _build_debian_sources(self, target_codename):
        codename = str(target_codename or "").strip().lower()
        if not re.match(r"^[a-z][a-z0-9-]*$", codename):
            raise ValueError(f"Invalid Debian codename: {target_codename}")
        lines = [
            f"deb http://deb.debian.org/debian {codename} main contrib non-free non-free-firmware",
            f"deb http://deb.debian.org/debian {codename}-updates main contrib non-free non-free-firmware",
            f"deb http://security.debian.org/debian-security {codename}-security main contrib non-free non-free-firmware",
            ""
        ]
        return "\n".join(lines)

    def _set_debian_sources(self, target_codename):
        content = self._build_debian_sources(target_codename)
        res, err = self.run_command([CMD['TEE'], "/etc/apt/sources.list"], timeout=30, input=content)
        if err or not res or res.returncode != 0:
            return False, err or (res.stderr if res else "failed to write /etc/apt/sources.list")
        return True, None

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
        # "beta-v0.1.0": the stage first, then the number -> "0.1.0beta".
        m = re.match(r"^(alpha|beta|rc|pre)[._-]*v?(\d+(?:\.\d+)*)$", v)
        if m:
            v = f"{m.group(2)}{m.group(1)}"
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

    def is_system_release(self, release):
        try:
            Version(self.normalize_version(release.get("tag_name") or ""))
            return True
        except InvalidVersion:
            return False

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
                    # Only AlvaOS versions: a pre-release with another kind of tag
                    # (once the phone app's "android-beta") is not an update for the NAS.
                    releases = [r for r in resp.json() if r.get("prerelease") and self.is_system_release(r)]
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

        # Fetch the detached signature from the same release. It is verified
        # by the privilege helper right before installation.
        sig_path = signature_path_for(dest_path)
        try:
            with requests.get(url + ".sig", timeout=30) as sig_resp:
                sig_resp.raise_for_status()
                if len(sig_resp.content) > 4096:
                    raise ValueError("Signature file is too large")
                with open(sig_path, "wb") as f:
                    f.write(sig_resp.content)
        except Exception as e:
            try:
                os.remove(dest_path)
            except OSError:
                pass
            error = f"The update has no downloadable signature ({e}); it will not be installed"
            self.set_update_state("error", "Download failed", {"error": error})
            raise ValueError(error) from None

        checksum = sha256.hexdigest()
        self.set_update_state("idle", "Download complete", {
            "path": dest_path,
            "sha256": checksum
        })
        return {"path": dest_path, "sha256": checksum}

    def run_command(self, cmd, timeout=60, extra_env=None, use_sudo=True, input=None):
        if not cmd:
            return None, "Empty command"

        if cmd[0] == 'sudo' or (use_sudo and platform.system() == "Linux"):
            final_cmd = build_privileged_cmd(cmd, env=extra_env)
        else:
            final_cmd = cmd

        try:
            env = {"LC_ALL": "C"}
            if extra_env and isinstance(extra_env, dict):
                env.update(extra_env)
            result = subprocess.run(final_cmd, capture_output=True, text=True, timeout=timeout,
                                    env=env, input=input)
            if result.returncode != 0:
                special = privilege_error_message(result, final_cmd)
                if special:
                    return (None if 'password' in special.lower() else result), special
                stderr_text = (result.stderr or "").strip()
                stdout_text = (result.stdout or "").strip()
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

    def _deb_package_name(self, package_path):
        res, err = self.run_command([CMD['DPKG_DEB'], "-f", package_path, "Package"], timeout=10, use_sudo=False)
        if err or not res or res.returncode != 0:
            return None
        return (res.stdout or "").strip()

    def classify_offline_package(self, package_path):
        return "alvaos" if self._deb_package_name(package_path) == ALVAOS_PACKAGE_NAME else "system"

    def validate_deb(self, package_path):
        if not package_path or not package_path.endswith(".deb"):
            return False, "Invalid package path"
        if not os.path.exists(package_path):
            return False, "Package not found"
        # Package metadata inspection does not require root.
        res, err = self.run_command([CMD['DPKG_DEB'], "--info", package_path], timeout=30, use_sudo=False)
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

        if not os.path.exists(signature_path_for(package_path)):
            err = "This update has no signature file (.sig); unsigned updates are not installed"
            self.set_update_state("error", "Package validation failed", {"error": err})
            return {"success": False, "error": err}

        self.set_update_state("installing", "Installing AlvaOS update", {"package": package_path})
        self.update_progress("installing", "Starting update service", 10, {"package": package_path})

        # The privilege helper verifies the package signature and then starts
        # apply_update.sh as a detached systemd unit, so the backend restarting
        # during the update does not kill the update itself.
        cmd = [PRIV_HELPER, "apply-update", package_path]
        if not self.is_root_user():
            cmd = ["sudo", "-n"] + cmd
        try:
            start_res = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                       env={"LC_ALL": "C"})
        except Exception as e:
            self.set_update_state("error", "Install failed", {"error": str(e)})
            return {"success": False, "error": f"Failed to launch the update: {e}"}
        if start_res.returncode != 0:
            detail = (privilege_error_message(start_res, cmd)
                      or (start_res.stderr or start_res.stdout or "").strip()
                      or f"exit code {start_res.returncode}")
            self.set_update_state("error", "Install failed", {"error": detail})
            return {"success": False, "error": detail}
        return {"success": True, "message": "Update process started in background"}

    def list_rollback_candidates(self):
        """Earlier AlvaOS versions that are still in the update cache, signed,
        newest first. Only these can be gone back to: the privilege helper
        verifies the signature exactly as for a normal update."""
        current = self.get_current_version()
        candidates: dict = {}
        try:
            names = os.listdir(self.cache_dir)
        except OSError:
            return []
        for name in names:
            path = os.path.join(self.cache_dir, name)
            if not name.endswith(".deb") or not os.path.isfile(path):
                continue
            if not os.path.isfile(signature_path_for(path)):
                continue
            version = read_deb_version(path)
            if not version or not self.is_newer(version, current):
                continue
            # is_newer(a, b) is "b is newer than a": keep only older versions.
            candidates.setdefault(version, {
                "version": version,
                "file": name,
                "downloaded_at": datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat(),
            })

        def sort_key(item):
            try:
                return Version(self.normalize_version(item["version"]))
            except InvalidVersion:
                return Version("0")

        return sorted(candidates.values(), key=sort_key, reverse=True)

    def rollback_to(self, version):
        """Reinstall an earlier signed AlvaOS package from the cache."""
        wanted = str(version or "").strip()
        match = next((c for c in self.list_rollback_candidates() if c["version"] == wanted), None)
        if not match:
            return {"success": False, "error": f"AlvaOS {wanted or '?'} is not stored on this NAS"}
        result = self.apply_alvaos_update(os.path.join(self.cache_dir, match["file"]))
        if result.get("success"):
            result["message"] = f"Going back to AlvaOS {wanted}"
        return result

    def apply_offline_system_package(self, package_path):
        if platform.system() != "Linux":
            return {"success": False, "error": "Updates are only supported on Linux"}
        ok, err = self.validate_deb(package_path)
        if not ok:
            self.set_update_state("error", "Package validation failed", {"error": err})
            return {"success": False, "error": err}

        if self.classify_offline_package(package_path) == "alvaos":
            return {"success": False, "error": "This is an AlvaOS package; use the AlvaOS update flow instead"}

        self.set_update_state("installing", "Installing offline package", {"package": package_path})
        res, run_err = self.run_command([CMD['DPKG'], "-i", package_path], timeout=600)
        if run_err or not res or res.returncode != 0:
            # Missing dependencies are common for standalone .deb files; try to resolve them.
            fix_res, fix_err = self.run_command(
                [CMD['APT_GET'], "-o", "Dpkg::Lock::Timeout=120", "install", "-f", "-y"],
                timeout=600, extra_env={"DEBIAN_FRONTEND": "noninteractive"}
            )
            if fix_err or not fix_res or fix_res.returncode != 0:
                error_msg = run_err or (res.stderr if res else "dpkg failed")
                self.set_update_state("error", "Install failed", {"error": error_msg})
                return {"success": False, "error": error_msg}

        entry = {
            "type": "offline-system",
            "package": package_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stdout": res.stdout[-2000:] if res and res.stdout else ""
        }
        self.append_history(entry)
        self.set_update_state("idle", "Install complete", {"package": package_path})
        return {"success": True}

    def check_debian_updates(self):
        if platform.system() != "Linux":
            return {"error": "Debian updates are only supported on Linux"}
        self.set_update_state("checking", "Checking Debian updates")
        # Read-only query; avoid sudo dependency for the check itself.
        res, err = self.run_command([CMD['APT'], "list", "--upgradable"], timeout=60, use_sudo=False)
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

        os_upgrade = self.check_debian_os_upgrade()

        self.set_update_state("idle", "Debian check complete", {
            "count": len(updates),
            "os_upgrade_available": bool(os_upgrade.get("available"))
        })
        return {
            "updates": updates,
            "os_upgrade": os_upgrade
        }

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

    def apply_debian_os_upgrade(self, target_codename=None):
        if platform.system() != "Linux":
            return {"success": False, "error": "Debian OS upgrades are only supported on Linux"}

        plan = self.check_debian_os_upgrade()
        if plan.get("error"):
            self.set_update_state("error", "Debian OS upgrade failed", {"error": plan.get("error")})
            return {"success": False, "error": plan.get("error")}
        if not plan.get("available"):
            return {"success": False, "error": "No Debian OS release upgrade available"}

        planned_target = str((plan.get("target") or {}).get("codename") or "").strip().lower()
        requested_target = str(target_codename or "").strip().lower()
        final_target = requested_target or planned_target

        if not final_target:
            return {"success": False, "error": "Could not determine Debian target codename"}
        if requested_target and requested_target != planned_target:
            return {
                "success": False,
                "error": f"Requested target '{requested_target}' does not match planned target '{planned_target}'"
            }
        if not re.match(r"^[a-z][a-z0-9-]*$", final_target):
            return {"success": False, "error": f"Invalid Debian target codename: {final_target}"}

        apt_env = {"DEBIAN_FRONTEND": "noninteractive"}
        apt_base = [
            CMD['APT_GET'],
            "-o", "Dpkg::Lock::Timeout=120",
            "--allow-releaseinfo-change"
        ]
        self.set_update_state("installing", f"Upgrading Debian OS to {final_target}", {
            "from": plan.get("current"),
            "to": {"codename": final_target, "version": DEBIAN_CODENAME_TO_VERSION.get(final_target, "")}
        })
        self.update_progress("installing", "Preparing Debian OS upgrade", 5, {"target": final_target})

        self.ensure_dirs()
        sources_path = os.path.join(self.cache_dir, f"debian-upgrade-{secrets.token_hex(4)}.list")
        sources_content = self._build_debian_sources(final_target)
        try:
            with open(sources_path, "w", encoding="utf-8") as f:
                f.write(sources_content)
        except Exception as e:
            error_msg = f"Failed to prepare temporary apt sources: {e}"
            self.set_update_state("error", "Debian OS upgrade failed", {"error": error_msg})
            return {"success": False, "error": error_msg}

        source_opts = [
            "-o", f"Dir::Etc::sourcelist={sources_path}",
            "-o", "Dir::Etc::sourceparts=-"
        ]

        try:
            self.update_progress("installing", "Refreshing package indexes", 20, {"target": final_target})
            res, err = self.run_command(apt_base + source_opts + ["update"], timeout=300, extra_env=apt_env)
            if err or not res or res.returncode != 0:
                error_msg = err or (res.stderr if res else "apt-get update failed")
                self.set_update_state("error", "Debian OS upgrade failed", {"error": error_msg})
                return {"success": False, "error": error_msg}

            # Follow Debian release notes: first a minimal upgrade, then full-upgrade.
            self.update_progress("installing", "Applying minimal system upgrade", 45, {"target": final_target})
            res, err = self.run_command(
                apt_base + source_opts + ["upgrade", "--without-new-pkgs", "-y"],
                timeout=3600,
                extra_env=apt_env
            )
            if err or not res or res.returncode != 0:
                error_msg = err or (res.stderr if res else "apt-get upgrade failed")
                self.set_update_state("error", "Debian OS upgrade failed", {"error": error_msg})
                return {"success": False, "error": error_msg}

            self.update_progress("installing", "Applying full distribution upgrade", 75, {"target": final_target})
            res, err = self.run_command(
                apt_base + source_opts + ["full-upgrade", "-y"],
                timeout=7200,
                extra_env=apt_env
            )
            if err or not res or res.returncode != 0:
                error_msg = err or (res.stderr if res else "apt-get full-upgrade failed")
                self.set_update_state("error", "Debian OS upgrade failed", {"error": error_msg})
                return {"success": False, "error": error_msg}

            self.update_progress("installing", "Persisting Debian package sources", 90, {"target": final_target})
            ok, source_err = self._set_debian_sources(final_target)
            if not ok:
                self.set_update_state("error", "Debian OS upgrade failed", {"error": source_err})
                return {"success": False, "error": source_err}

            self.update_progress("installing", "Refreshing package indexes", 95, {"target": final_target})
            res, err = self.run_command(apt_base + ["update"], timeout=300, extra_env=apt_env)
            if err or not res or res.returncode != 0:
                error_msg = err or (res.stderr if res else "apt-get update failed after source switch")
                self.set_update_state("error", "Debian OS upgrade failed", {"error": error_msg})
                return {"success": False, "error": error_msg}

            res, err = self.run_command(apt_base + ["autoremove", "-y"], timeout=900, extra_env=apt_env)
            if err or not res or res.returncode != 0:
                # Do not fail whole upgrade on autoremove issues.
                print(f"Debian OS upgrade warning: apt-get autoremove failed: {err}")

            final_info = self.get_debian_os_info()
            entry = {
                "type": "debian-os",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "from": plan.get("current"),
                "to": final_info,
                "target": final_target
            }
            self.append_history(entry)
            self.set_update_state("idle", "Debian OS upgrade complete", {
                "from": plan.get("current"),
                "to": final_info,
                "target": final_target,
                "reboot_required": True
            })
            return {
                "success": True,
                "from": plan.get("current"),
                "to": final_info,
                "target_codename": final_target,
                "reboot_required": True
            }
        finally:
            try:
                if os.path.exists(sources_path):
                    os.remove(sources_path)
            except Exception:
                pass

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
                            "size": os.path.getsize(full_path),
                            "type": self.classify_offline_package(full_path)
                        })
                    except OSError:
                        pass
        return {"packages": packages}

    def _temp_mount_and_scan(self, dev_name):
        """Temporarily mount a device and scan it for packages."""
        dev_path = f"/dev/{dev_name}"
        if not re.match(r"^[a-zA-Z0-9]+$", dev_name or ""):
            return []
        # The helper creates this root-owned mount point and mounts the stick
        # read-only with nosuid,nodev,noexec.
        mount_point = os.path.join(SCAN_MOUNT_BASE, dev_name)
        packages = []

        try:
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
            # Keep the detached signature next to the package; AlvaOS packages
            # are only installed when it verifies.
            src_sig = signature_path_for(src_path)
            if os.path.exists(src_sig):
                shutil.copy2(src_sig, signature_path_for(dest))
            return dest
        except Exception as e:
            print(f"Failed to cache offline package {src_path}: {e}")
            return None

    def validate_offline_package(self, usb_path):
        return self.validate_deb(usb_path)
