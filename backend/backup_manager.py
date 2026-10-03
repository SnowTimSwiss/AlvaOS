#!/usr/bin/env python3
"""
AlvaOS Local Backup Manager
Provides local Btrfs snapshots, restore, and automatic scheduling.
"""

import copy
import json
import os
import platform
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

CMD: Dict[str, str] = {
    "BTRFS": "/usr/bin/btrfs",
    "MKDIR": "/usr/bin/mkdir",
    "MV": "/usr/bin/mv",
    "CP": "/usr/bin/cp",
    "FIND": "/usr/bin/find",
}

# Must match priv_policy.FIND_LIST_FORMAT exactly.
FIND_LIST_FORMAT = "%y\\t%s\\t%T@\\t%f\\0"
FIND_TYPES = {"d": "folder", "f": "file", "l": "link"}


def parse_find_listing(text: str) -> List[Dict[str, Any]]:
    """Entries from `find DIR -mindepth 1 -maxdepth 1 -printf FIND_LIST_FORMAT`."""
    entries = []
    for record in (text or "").split("\0"):
        parts = record.split("\t", 3)
        if len(parts) != 4 or not parts[3]:
            continue
        kind, size, mtime, name = parts
        try:
            size_bytes = int(size)
            modified = datetime.fromtimestamp(float(mtime), timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            continue
        entries.append({"name": name, "type": FIND_TYPES.get(kind, "other"),
                        "size_bytes": size_bytes, "modified_at": modified})
    entries.sort(key=lambda e: (e["type"] != "folder", str(e["name"]).lower()))
    return entries


def clean_relative_path(path: Optional[str]) -> Optional[str]:
    """A path inside a restore point: relative, no '..'. '' is the top."""
    text = str(path or "").strip().strip("/")
    if "\x00" in text or "\n" in text:
        return None
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def restored_name(name: str, when: datetime, is_folder: bool = False) -> str:
    """'holiday.jpg' -> 'holiday (restored 2026-10-01 0300).jpg'."""
    stem, ext = os.path.splitext(name)
    if not stem or is_folder:
        stem, ext = name, ""
    return f"{stem} (restored {when.strftime('%Y-%m-%d %H%M')}){ext}"

DEFAULT_SETTINGS: Dict[str, Dict[str, Any]] = {
    # Pool Backup Settings
    "pool_backup": {
        "enabled": False,
        "interval_minutes": 1440,
        "keep_last": 30,
        # "smart": everything from the last day, then one per day, week and
        # month (SMART_RETENTION). "count": the newest keep_last snapshots.
        "retention": "smart",
        "sources": [],
        "target_path": "",
    },
    # System Backup Settings
    "system_backup": {
        "enabled": False,
        "interval_minutes": 10080, # Weekly
        "keep_last": 10,
        "target_path": "/var/lib/alvaos/system-snapshots",
    }
}

DEFAULT_STATUS: Dict[str, Any] = {
    "pool_last_run_at": None,
    "pool_next_run_at": None,
    "pool_last_status": "idle",
    "pool_last_error": "",
    
    "system_last_run_at": None,
    "system_next_run_at": None,
    "system_last_status": "idle",
    "system_last_error": "",
    
    "system_last_snapshot_at": None,
    "system_last_rollback_at": None,
    "system_pending_reboot": False,
    "system_pending_snapshot_path": None,
}


RETENTION_MODES = ("smart", "count")

# Smart retention, like Time Machine: every snapshot of the last day, then the
# newest of each day for a month, of each week for 3 months and of each month
# for a year.
SMART_RETENTION = {"all_hours": 24, "daily": 30, "weekly": 12, "monthly": 12}


def _parse_created(value: Any) -> Optional[datetime]:
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def smart_keep(entries: List[Dict], now: datetime) -> List[Dict]:
    """The snapshots smart retention keeps. Always the newest one; entries
    without a readable time are kept too, never deleted by guessing."""
    dated = []
    keep_ids = set()
    for entry in entries:
        stamp = _parse_created(entry.get("created_at"))
        if stamp is None:
            keep_ids.add(id(entry))
        else:
            dated.append((stamp, entry))
    dated.sort(key=lambda item: item[0], reverse=True)
    if dated:
        keep_ids.add(id(dated[0][1]))
    recent = now - timedelta(hours=SMART_RETENTION["all_hours"])
    today = now.date()

    def week_start(day):
        return day - timedelta(days=day.weekday())

    buckets = [
        (SMART_RETENTION["daily"], lambda d: d, lambda d: (today - d).days),
        (SMART_RETENTION["weekly"], week_start, lambda d: (week_start(today) - week_start(d)).days // 7),
        (SMART_RETENTION["monthly"], lambda d: (d.year, d.month),
         lambda d: (today.year - d.year) * 12 + today.month - d.month),
    ]
    for stamp, entry in dated:
        if stamp >= recent:
            keep_ids.add(id(entry))
    for count, bucket_of, age_of in buckets:
        seen = set()
        for stamp, entry in dated:
            day = stamp.date()
            bucket = bucket_of(day)
            if bucket in seen or age_of(day) >= count:
                continue
            seen.add(bucket)
            keep_ids.add(id(entry))
    return [entry for entry in entries if id(entry) in keep_ids]


class BackupManager:
    def __init__(self, run_command: Callable, load_pools_state: Callable):
        self.run_command = run_command
        self.load_pools_state = load_pools_state

        self.state_dir = self._resolve_state_dir()
        self.settings_file = os.path.join(self.state_dir, "backup_settings.json")
        self.status_file = os.path.join(self.state_dir, "backup_status.json")
        self.snapshots_file = os.path.join(self.state_dir, "backup_snapshots.json")

        self._schedule_lock = threading.Lock()
        self._job_lock = threading.RLock()
        self._stop_event = threading.Event()

        self.ensure_dirs()
        self._ensure_defaults()

        self._worker = threading.Thread(target=self._schedule_loop, daemon=True)
        self._worker.start()

    def _resolve_state_dir(self) -> str:
        # ALVAOS_STATE_DIR: the tests keep their state out of /var/lib/alvaos.
        preferred = Path(os.environ.get("ALVAOS_STATE_DIR") or "/var/lib/alvaos")
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

    def _load_json(self, path: str, default):
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _save_json(self, path: str, payload) -> None:
        self.ensure_dirs()
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)

    def _ensure_defaults(self):
        settings = self.get_settings()
        status = self.get_status()
        self._save_json(self.settings_file, settings)
        self._save_json(self.status_file, status)
        snapshots = self._load_json(self.snapshots_file, [])
        if not isinstance(snapshots, list):
            snapshots = []
            self._save_json(self.snapshots_file, snapshots)

    def _normalize_path(self, path: Optional[str]) -> str:
        if not path:
            return ""
        return os.path.normpath(str(path).strip())

    def _slugify(self, text: str) -> str:
        value = (text or "").strip().lower()
        value = re.sub(r"[^a-z0-9._-]+", "-", value)
        value = value.strip("-")
        return value or "snapshot"

    def _parse_iso(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _detect_cmd(self, candidates: List[Optional[str]], which_name: str) -> Optional[str]:
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate
        found = shutil.which(which_name)
        return found if found else None

    def _btrfs_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/btrfs", "/bin/btrfs", "/usr/sbin/btrfs", "/sbin/btrfs", CMD.get("BTRFS")],
            "btrfs",
        )

    def _mkdir_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/mkdir", "/bin/mkdir", CMD.get("MKDIR")],
            "mkdir",
        )

    def _mv_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/mv", "/bin/mv", CMD.get("MV")],
            "mv",
        )

    def _move_path_best_effort(self, source: str, destination: str, timeout: int = 60) -> Tuple[bool, str]:
        src = self._normalize_path(source)
        dst = self._normalize_path(destination)
        if not src or not dst:
            return False, "Invalid move path"

        try:
            os.rename(src, dst)
            return True, ""
        except Exception as exc:
            last_error = str(exc)

        mv_cmd = self._mv_cmd()
        if mv_cmd:
            mv_res, mv_err = self.run_command([mv_cmd, src, dst], timeout=timeout)
            if not mv_err and mv_res and mv_res.returncode == 0:
                return True, ""
            last_error = mv_err or last_error


        return False, last_error or f"Failed to move {src} -> {dst}"

    def _list_pool_mounts(self) -> List[Tuple[str, str]]:
        pools = self.load_pools_state() or {}
        mounts = []
        for pool_id, data in pools.items():
            mount_point = self._normalize_path(data.get("mount_point"))
            if mount_point:
                mounts.append((pool_id, mount_point))
        mounts.sort(key=lambda item: len(item[1]), reverse=True)
        return mounts

    def _find_pool_for_path(self, source_path: str) -> Tuple[Optional[str], Optional[str]]:
        source_norm = self._normalize_path(source_path)
        for pool_id, mount_point in self._list_pool_mounts():
            if source_norm == mount_point or source_norm.startswith(mount_point + os.sep):
                return pool_id, mount_point
        return None, None

    def _list_subvolumes(self, mount_point: str) -> List[Dict]:
        subvolumes: List[Dict] = []
        if platform.system() != "Linux":
            return subvolumes
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return subvolumes

        res, err = self.run_command([btrfs_cmd, "subvolume", "list", mount_point], timeout=10)
        if err or not res or res.returncode != 0:
            return subvolumes

        for raw in (res.stdout or "").splitlines():
            line = raw.strip()
            if not line or " path " not in line:
                continue
            rel = line.split(" path ", 1)[1].strip()
            if not rel:
                continue
            path = os.path.normpath(os.path.join(mount_point, rel))
            subvolumes.append(
                {
                    "name": rel,
                    "path": path,
                }
            )
        return subvolumes

    def get_sources(self) -> List[Dict]:
        sources = []
        seen_paths = set()
        mounts = self._list_pool_mounts()
        pools = self.load_pools_state() or {}

        for pool_id, mount_point in mounts:
            mount_norm = self._normalize_path(mount_point)
            if not mount_norm or mount_norm == "/":
                continue
            if self._path_on_system_disk(mount_norm):
                continue
            pool_name = pools.get(pool_id, {}).get("name") or pool_id

            if platform.system() == "Linux":
                subvolumes = self._list_subvolumes(mount_point)
                for sub in subvolumes:
                    name = sub["name"]
                    if name.startswith("."):
                        continue
                    if name.startswith(".alvaos-snapshots"):
                        continue
                    if name.startswith("system-snapshots"):
                        continue
                    sub_path = self._normalize_path(sub["path"])
                    if not sub_path or sub_path in seen_paths:
                        continue
                    if self._path_on_system_disk(sub_path):
                        continue
                    seen_paths.add(sub_path)
                    sources.append(
                        {
                            "pool_id": pool_id,
                            "pool_name": pool_name,
                            "name": f"{pool_name}: {name}",
                            "path": sub_path,
                            "kind": "subvolume"
                        }
                    )

        return sorted(sources, key=lambda item: item["name"].lower())

    def get_target_locations(self) -> List[Dict]:
        targets = []
        seen = set()
        pools = self.load_pools_state() or {}

        for pool_id, mount_point in self._list_pool_mounts():
            pool_name = pools.get(pool_id, {}).get("name") or pool_id
            norm_mount = self._normalize_path(mount_point)
            if norm_mount and norm_mount not in seen and not self._path_on_system_disk(norm_mount):
                seen.add(norm_mount)
                targets.append({
                    "name": f"Pool: {pool_name}",
                    "path": norm_mount,
                    "kind": "pool"
                })

            if platform.system() == "Linux":
                for sub in self._list_subvolumes(mount_point):
                    rel = sub.get("name", "")
                    if rel.startswith("."):
                        continue
                    path = self._normalize_path(sub.get("path"))
                    if not path or path in seen:
                        continue
                    if rel.startswith(".alvaos-snapshots"):
                        continue
                    if self._path_on_system_disk(path):
                        continue
                    seen.add(path)
                    targets.append({
                        "name": f"{pool_name}: {rel}",
                        "path": path,
                        "kind": "subvolume"
                    })

        settings = self.get_settings()
        system_default = self._normalize_path(
            settings.get("system_backup", {}).get("target_path")
            or DEFAULT_SETTINGS["system_backup"]["target_path"]
        )
        if system_default and system_default not in seen:
            targets.append({
                "name": "System Default: /var/lib/alvaos/system-snapshots",
                "path": system_default,
                "kind": "system"
            })

        return targets

    def get_settings(self) -> Dict:
        # Load raw settings without defaults first to allow migration detection
        settings = self._load_json(self.settings_file, {})
        if not isinstance(settings, dict):
            settings = {}
        return self._normalize_settings(settings)

    def _normalize_settings(self, payload: Dict) -> Dict:
        merged = copy.deepcopy(DEFAULT_SETTINGS)
        
        # Migration from old flat structure if needed
        # Check if payload has old keys and missing new keys
        if "pool_backup" not in payload and "auto_enabled" in payload:
            # Migrate old pool settings
            merged["pool_backup"] = {
                "enabled": bool(payload.get("auto_enabled")),
                "interval_minutes": int(payload.get("interval_minutes", 1440)),
                "keep_last": int(payload.get("keep_last", 30)),
                "retention": "count",
                "sources": payload.get("sources", []),
                "target_path": payload.get("snapshot_target_path", "")
            }
            # Migrate old system settings
            if payload.get("include_system_in_schedule"):
                 merged["system_backup"] = {
                    "enabled": True,
                    "interval_minutes": int(payload.get("interval_minutes", 1440)), # Inherit old interval
                    "keep_last": int(payload.get("keep_system_last", 10)),
                    "target_path": payload.get("system_snapshot_target_path", "/var/lib/alvaos/system-snapshots")
                 }
        else:
            # Standard merge of nested dicts
            if "pool_backup" in payload:
                pool_payload = payload["pool_backup"] if isinstance(payload["pool_backup"], dict) else {}
                merged["pool_backup"].update(pool_payload)
                # Saved before smart retention existed: keep counting, so an
                # update never deletes snapshots someone chose to keep.
                if "retention" not in pool_payload:
                    merged["pool_backup"]["retention"] = "count"
            if "system_backup" in payload:
                merged["system_backup"].update(payload["system_backup"])

        # Validate Pool Backup
        pb = merged["pool_backup"]
        pb["enabled"] = bool(pb.get("enabled", False))
        try:
            pb["interval_minutes"] = max(15, min(43200, int(pb.get("interval_minutes", 1440))))
        except Exception:
            pb["interval_minutes"] = 1440
            
        try:
            pb["keep_last"] = max(1, min(200, int(pb.get("keep_last", 30))))
        except Exception:
            pb["keep_last"] = 30
        if pb.get("retention") not in RETENTION_MODES:
            pb["retention"] = "smart"
            
        raw_sources = pb.get("sources", [])
        if not isinstance(raw_sources, list):
            raw_sources = []
        unique_sources = []
        seen = set()
        for source in raw_sources:
            normalized = self._normalize_path(source)
            if not normalized:
                continue
            if self._path_on_system_disk(normalized):
                continue
            if normalized not in seen:
                seen.add(normalized)
                unique_sources.append(normalized)
        pb["sources"] = unique_sources
        pb["target_path"] = self._normalize_path(pb.get("target_path"))
        if pb["target_path"] and self._path_on_system_disk(pb["target_path"]):
            pb["target_path"] = ""

        # Validate System Backup
        sb = merged["system_backup"]
        sb["enabled"] = bool(sb.get("enabled", False))
        try:
            sb["interval_minutes"] = max(15, min(43200, int(sb.get("interval_minutes", 10080))))
        except Exception:
            sb["interval_minutes"] = 10080
            
        try:
            sb["keep_last"] = max(1, min(100, int(sb.get("keep_last", 10))))
        except Exception:
            sb["keep_last"] = 10
            
        sb["target_path"] = self._normalize_path(sb.get("target_path"))
        if not sb["target_path"]:
            sb["target_path"] = self._normalize_path(DEFAULT_SETTINGS["system_backup"]["target_path"])
        if platform.system() == "Linux" and sb["target_path"] and not self._same_filesystem("/", sb["target_path"]):
            sb["target_path"] = self._normalize_path(DEFAULT_SETTINGS["system_backup"]["target_path"])

        return merged

    def save_settings(self, payload: Dict) -> Dict:
        payload = payload or {}
        current = self.get_settings()
        for key, value in payload.items():
            # A section that is sent keeps the fields it leaves out.
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key] = {**current[key], **value}
            else:
                current[key] = value
        normalized = self._normalize_settings(current)
        self._save_json(self.settings_file, normalized)
        self._refresh_next_run(normalized)
        return normalized

    def get_status(self) -> Dict:
        status = self._load_json(self.status_file, DEFAULT_STATUS.copy())
        if not isinstance(status, dict):
            status = {}
        merged = DEFAULT_STATUS.copy()
        merged.update(status)
        return merged

    def _save_status(self, updates: Dict) -> Dict:
        status = self.get_status()
        status.update(updates or {})
        self._save_json(self.status_file, status)
        return status

    def _refresh_next_run(self, settings: Optional[Dict] = None):
        with self._schedule_lock:
            cfg = settings or self.get_settings()
            updates: Dict[str, Any] = {}
            now = datetime.now(timezone.utc)
            
            # Pool Backup
            pb = cfg.get("pool_backup", {})
            if pb.get("enabled") and pb.get("sources"):
                 next_t = now + timedelta(minutes=pb.get("interval_minutes", 1440))
                 updates["pool_next_run_at"] = next_t.isoformat()
            else:
                 updates["pool_next_run_at"] = None

            # System Backup
            sb = cfg.get("system_backup", {})
            if sb.get("enabled"):
                 next_t = now + timedelta(minutes=sb.get("interval_minutes", 10080))
                 updates["system_next_run_at"] = next_t.isoformat()
            else:
                 updates["system_next_run_at"] = None

            self._save_status(updates)

    def list_snapshots(self, source_path: Optional[str] = None, snapshot_class: Optional[str] = None) -> List[Dict]:
        entries = self._load_json(self.snapshots_file, [])
        if not isinstance(entries, list):
            entries = []
        source_norm = self._normalize_path(source_path) if source_path else None
        if source_norm:
            entries = [e for e in entries if self._normalize_path(e.get("source_path")) == source_norm]
        if snapshot_class:
            entries = [e for e in entries if (e.get("snapshot_class") or "data") == snapshot_class]
        entries.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return entries

    def list_system_snapshots(self) -> List[Dict]:
        return self.list_snapshots(snapshot_class="system")

    def delete_snapshot(self, snapshot_path: str) -> Tuple[bool, Dict]:
        snapshot = self._normalize_path(snapshot_path)
        if not snapshot:
            return False, {"error": "snapshot_path is required"}

        with self._job_lock:
            status = self.get_status()
            pending_snapshot = self._normalize_path(status.get("system_pending_snapshot_path"))
            if status.get("system_pending_reboot") and pending_snapshot and pending_snapshot == snapshot:
                return False, {
                    "error": "Snapshot is currently scheduled for rollback and cannot be deleted"
                }

            entries = self._load_json(self.snapshots_file, [])
            if not isinstance(entries, list):
                entries = []

            matching = [
                item for item in entries
                if self._normalize_path(item.get("snapshot_path")) == snapshot
            ]

            path_exists = self._path_exists_or_is_subvolume(snapshot)

            if not matching and not path_exists:
                return False, {"error": f"Snapshot not found: {snapshot}"}

            if path_exists and platform.system() == "Linux":
                if not self._delete_snapshot_path(snapshot):
                    return False, {"error": f"Failed to delete snapshot path: {snapshot}"}

            if matching:
                filtered = [
                    item for item in entries
                    if self._normalize_path(item.get("snapshot_path")) != snapshot
                ]
                self._save_snapshots(filtered)

            return True, {
                "snapshot_path": snapshot,
                "deleted_path": bool(path_exists),
                "deleted_metadata": bool(matching),
            }

    def _save_snapshots(self, entries: List[Dict]) -> None:
        if len(entries) > 1000:
            entries = entries[-1000:]
        self._save_json(self.snapshots_file, entries)

    def _append_snapshot(self, entry: Dict) -> None:
        with self._job_lock:
            entries = self._load_json(self.snapshots_file, [])
            if not isinstance(entries, list):
                entries = []
            entries.append(entry)
            self._save_snapshots(entries)

    def _get_btrfs_uuid(self, path: str) -> Optional[str]:
        if platform.system() != "Linux":
            return None
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return None
        try:
            res, err = self.run_command([btrfs_cmd, "filesystem", "show", path], timeout=10)
            if res and res.returncode == 0:
                m = re.search(r"uuid:\s+([A-Fa-f0-9-]+)", res.stdout, re.IGNORECASE)
                if m:
                    return m.group(1).lower()
        except Exception:
            pass
        return None

    def _same_filesystem(self, path_a: str, path_b: str) -> bool:
        try:
            # Traditional check
            st_a = os.stat(path_a)
            st_b = os.stat(path_b)
            if st_a.st_dev == st_b.st_dev:
                return True
            
            # Btrfs check: Even if mounted separately (different st_dev), 
            # they could be on the same Btrfs UUID.
            uuid_a = self._get_btrfs_uuid(path_a)
            if uuid_a:
                uuid_b = self._get_btrfs_uuid(path_b)
                return uuid_a == uuid_b
        except Exception:
            pass
        return False

    def _device_id_for_path(self, path: str) -> Optional[int]:
        target = self._normalize_path(path)
        if not target:
            return None
        try:
            return int(os.stat(target).st_dev)
        except Exception:
            return None

    def _best_temp_snapshot_parent(self, source_path: str) -> str:
        source = self._normalize_path(source_path)
        if not source:
            return "/"
        if os.path.exists(source):
            return source
        source_dev = self._device_id_for_path(source)
        parent = self._normalize_path(os.path.dirname(source)) or "/"
        if source_dev is None:
            return parent
        parent_dev = self._device_id_for_path(parent)
        if parent_dev is not None and parent_dev == source_dev:
            return parent
        # Parent is on a different fs (common for /mnt/alvaos vs /mnt/alvaos/<pool>).
        # Use source path as temp parent so snapshot creation stays on the source fs.
        return source

    def _path_is_btrfs_subvolume(self, path: str) -> bool:
        if platform.system() != "Linux":
            return True
        target = self._normalize_path(path)
        if not target:
            return False
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return False
        res, err = self.run_command([btrfs_cmd, "subvolume", "show", target], timeout=20)
        return bool(res and res.returncode == 0 and not err)

    def _path_exists_or_is_subvolume(self, path: str) -> bool:
        target = self._normalize_path(path)
        if not target:
            return False
        if os.path.exists(target):
            return True
        if platform.system() == "Linux":
            return self._path_is_btrfs_subvolume(target)
        return False

    def _decode_mountinfo_path(self, value: str) -> str:
        if not value:
            return value
        return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)

    def _existing_probe_path(self, path: str) -> str:
        probe = self._normalize_path(path)
        if not probe:
            return "/"
        while probe != "/" and not os.path.exists(probe):
            next_probe = os.path.dirname(probe)
            if next_probe == probe:
                break
            probe = next_probe
        return probe if probe else "/"

    def _path_on_system_disk(self, path: str) -> bool:
        if platform.system() != "Linux":
            return False
        target = self._normalize_path(path)
        if not target:
            return False
        try:
            probe = os.path.realpath(self._existing_probe_path(target))
            root = os.path.realpath("/")
            return os.stat(probe).st_dev == os.stat(root).st_dev
        except Exception:
            return False

    def _filesystem_type_for_path(self, path: str) -> Optional[str]:
        probe = self._existing_probe_path(path)
        if not probe:
            return None
        probe_real = os.path.realpath(probe)
        best_fs_type = None
        best_len = -1

        try:
            with open("/proc/self/mountinfo", "r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if " - " not in line:
                        continue
                    left, right = line.split(" - ", 1)
                    left_fields = left.split()
                    right_fields = right.split()
                    if len(left_fields) < 5 or not right_fields:
                        continue

                    mount_point = self._decode_mountinfo_path(left_fields[4])
                    fs_type = right_fields[0].strip().lower()
                    if not mount_point or not fs_type:
                        continue

                    mount_real = os.path.realpath(mount_point)
                    is_match = (
                        probe_real == mount_real
                        or (mount_real == "/" and probe_real.startswith("/"))
                        or (mount_real != "/" and probe_real.startswith(mount_real + os.sep))
                    )
                    if not is_match:
                        continue

                    mount_len = len(mount_real)
                    if mount_len > best_len:
                        best_len = mount_len
                        best_fs_type = fs_type
        except Exception:
            return None

        return best_fs_type

    def _path_on_btrfs(self, path: str) -> bool:
        if platform.system() != "Linux":
            return True
        target = self._normalize_path(path)
        if not target:
            return False

        fs_type = self._filesystem_type_for_path(target)
        if fs_type:
            return fs_type == "btrfs"

        # Fallback for systems where mountinfo parsing is unavailable.
        probe = self._existing_probe_path(target)
        if not os.path.exists(probe):
            return False
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return False
        res, err = self.run_command([btrfs_cmd, "filesystem", "show", probe], timeout=20)
        return bool(res and res.returncode == 0 and not err)

    def _ensure_snapshot_container(self, path: str, force_subvolume: bool = False) -> Tuple[bool, str, str]:
        container = self._normalize_path(path)
        if not container:
            return False, "Invalid snapshot target path", ""

        if platform.system() != "Linux":
            try:
                Path(container).mkdir(parents=True, exist_ok=True)
                return True, "", container
            except Exception as e:
                return False, str(e), ""

        if force_subvolume and os.path.exists(container) and not self._path_is_btrfs_subvolume(container):
            # Existing plain directories cannot be converted in place; use a dedicated child subvolume.
            container = os.path.join(container, "__snapshot-container")

        parent = self._normalize_path(os.path.dirname(container))
        mkdir_cmd = self._mkdir_cmd()
        if not mkdir_cmd:
            return False, "mkdir command not found", ""
        mk_parent_res, mk_parent_err = self.run_command([mkdir_cmd, "-p", parent], timeout=30)
        if mk_parent_err or not mk_parent_res or mk_parent_res.returncode != 0:
            return False, mk_parent_err or "Failed to prepare snapshot target parent path", ""

        if not self._path_on_btrfs(parent):
            return False, f"Target path is not on Btrfs: {parent}", ""

        if os.path.exists(container):
            if force_subvolume and not self._path_is_btrfs_subvolume(container):
                return False, f"Snapshot container is not a Btrfs subvolume: {container}", ""
            return True, "", container

        if force_subvolume:
            btrfs_cmd = self._btrfs_cmd()
            if not btrfs_cmd:
                return False, "btrfs command not found", ""
            create_res, create_err = self.run_command([btrfs_cmd, "subvolume", "create", container], timeout=60)
            if create_err or not create_res or create_res.returncode != 0:
                return False, create_err or "Failed to create snapshot container subvolume", ""
            return True, "", container

        mk_res, mk_err = self.run_command([mkdir_cmd, "-p", container], timeout=30)
        if mk_err or not mk_res or mk_res.returncode != 0:
            return False, mk_err or "Failed to prepare snapshot target path", ""
        return True, "", container

    def _get_subvolume_id(self, path: str) -> Optional[int]:
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return None
        res, err = self.run_command([btrfs_cmd, "subvolume", "show", path], timeout=20)
        if err or not res or res.returncode != 0:
            return None
        for line in (res.stdout or "").splitlines():
            if "Subvolume ID:" in line:
                try:
                    return int(line.split(":", 1)[1].strip())
                except Exception:
                    return None
        return None

    def _get_default_subvolume_id(self, path: str = "/") -> Optional[int]:
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return None
        res, err = self.run_command([btrfs_cmd, "subvolume", "get-default", path], timeout=20)
        if err or not res or res.returncode != 0:
            return None
        m = re.search(r"ID\s+(\d+)", (res.stdout or "").strip())
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def get_system_state(self) -> Dict:
        if platform.system() != "Linux":
            return {"supported": False, "reason": "System snapshots are supported on Linux only"}
        if not self._path_is_btrfs_subvolume("/"):
            return {"supported": False, "reason": "Root filesystem is not a Btrfs subvolume"}
        return {
            "supported": True,
            "root_subvolume_id": self._get_subvolume_id("/"),
            "default_subvolume_id": self._get_default_subvolume_id("/")
        }

    def _build_default_data_target_root(self, source_path: str) -> str:
        source = self._normalize_path(source_path)
        _, mount_point = self._find_pool_for_path(source)
        if not mount_point:
            mount_point = self._normalize_path(os.path.dirname(source))
        rel = os.path.relpath(source, mount_point) if mount_point else os.path.basename(source)
        safe_rel = rel.replace(os.sep, "__")
        if safe_rel in (".", ""):
            safe_rel = "__root__"
        return os.path.join(mount_point, ".alvaos-snapshots", safe_rel)

    def _build_selected_data_target_root(self, source_path: str, target_base_path: str) -> str:
        source = self._normalize_path(source_path)
        base = self._normalize_path(target_base_path)
        safe_rel = source.strip(os.sep).replace(os.sep, "__")
        if not safe_rel:
            safe_rel = "__root__"
        return os.path.join(base, ".alvaos-snapshots", safe_rel)

    def _plan_escape(self, value: Optional[str]) -> str:
        text = str(value or "")
        return (
            text
            .replace("%", "%25")
            .replace("|", "%7C")
            .replace("\n", "%0A")
            .replace("\r", "")
        )

    def _collect_full_backup_sources(self) -> List[Dict]:
        grouped: Dict[str, Dict[str, List[Dict]]] = {}
        for item in self.get_sources():
            pool_id = str(item.get("pool_id") or "")
            source_path = self._normalize_path(item.get("path"))
            kind = str(item.get("kind") or "").strip().lower()
            if not pool_id or not source_path:
                continue
            if kind not in ("pool", "subvolume"):
                continue
            group = grouped.setdefault(pool_id, {"pool": [], "subvolume": []})
            group[kind].append({
                "pool_id": pool_id,
                "pool_name": item.get("pool_name") or item.get("name") or pool_id,
                "kind": kind,
                "path": source_path,
            })

        selected: List[Dict] = []
        for pool_id in sorted(grouped.keys()):
            group = grouped[pool_id]
            candidates = group.get("subvolume") or group.get("pool") or []
            candidates = sorted(candidates, key=lambda entry: entry.get("path", ""))
            selected.extend(candidates)
        return selected

    def _write_full_backup_manifest(
        self,
        system_snapshot_entry: Dict,
        data_snapshot_entries: List[Dict],
        failures: Optional[List[Dict]] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        manifest_dir = os.path.join(self.state_dir, "full-system-manifests")

        snapshot_name = str(system_snapshot_entry.get("snapshot_name") or "").strip()
        if not snapshot_name:
            return None, "Missing system snapshot name"

        pools_state = self.load_pools_state() or {}
        if not isinstance(pools_state, dict):
            pools_state = {}

        primary_manifest_path = os.path.join(manifest_dir, f"{snapshot_name}.plan")
        lines = [
            "# AlvaOS Full Backup Manifest v1",
            "META|format|1",
            f"META|created_at|{self._plan_escape(self._now_iso())}",
            f"META|system_snapshot_name|{self._plan_escape(snapshot_name)}",
            f"META|system_snapshot_path|{self._plan_escape(system_snapshot_entry.get('snapshot_path'))}",
            f"META|system_target_root|{self._plan_escape(system_snapshot_entry.get('target_root'))}",
        ]

        for pool_id in sorted(pools_state.keys()):
            pool = pools_state.get(pool_id, {})
            devices = pool.get("devices", [])
            if not isinstance(devices, list):
                devices = []
            lines.append(
                "POOL|{pool_id}|{name}|{raid}|{mount}|{count}".format(
                    pool_id=self._plan_escape(pool_id),
                    name=self._plan_escape(pool.get("name") or pool_id),
                    raid=self._plan_escape(pool.get("raid_level") or "single"),
                    mount=self._plan_escape(pool.get("mount_point") or ""),
                    count=len(devices),
                )
            )
            for index, dev in enumerate(devices):
                lines.append(
                    "POOL_DEVICE|{pool_id}|{index}|{device}".format(
                        pool_id=self._plan_escape(pool_id),
                        index=index,
                        device=self._plan_escape(dev),
                    )
                )

        for item in data_snapshot_entries:
            source = item.get("source") or {}
            snapshot = item.get("snapshot") or {}
            lines.append(
                "SNAPSHOT|{pool_id}|{kind}|{source_path}|{snapshot_path}|{snapshot_name}".format(
                    pool_id=self._plan_escape(source.get("pool_id") or ""),
                    kind=self._plan_escape(source.get("kind") or "subvolume"),
                    source_path=self._plan_escape(source.get("path") or ""),
                    snapshot_path=self._plan_escape(snapshot.get("snapshot_path") or ""),
                    snapshot_name=self._plan_escape(snapshot.get("snapshot_name") or ""),
                )
            )

        for item in failures or []:
            err = str(item.get("error") or "").strip()
            source_path = self._normalize_path(item.get("source_path"))
            if not err or not source_path:
                continue
            lines.append(
                "FAILURE|{source_path}|{error}".format(
                    source_path=self._plan_escape(source_path),
                    error=self._plan_escape(err),
                )
            )

        fallback_manifest_dir = os.path.join(self.state_dir, "full-system-manifests-local")
        candidate_paths = [primary_manifest_path]
        fallback_manifest_path = os.path.join(fallback_manifest_dir, f"{snapshot_name}.plan")
        if self._normalize_path(fallback_manifest_path) != self._normalize_path(primary_manifest_path):
            candidate_paths.append(fallback_manifest_path)

        last_error = None
        for manifest_path in candidate_paths:
            try:
                Path(os.path.dirname(manifest_path)).mkdir(parents=True, exist_ok=True)
                with open(manifest_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                return manifest_path, None
            except PermissionError as exc:
                last_error = str(exc)
                continue
            except Exception as exc:
                return None, str(exc)

        return None, last_error or "Failed to write full-backup manifest"

    def _snapshot_direct(self, source_path: str, snapshot_path: str) -> Tuple[bool, str]:
        target_parent = os.path.dirname(snapshot_path)
        mkdir_cmd = self._mkdir_cmd()
        btrfs_cmd = self._btrfs_cmd()
        if not mkdir_cmd:
            return False, "mkdir command not found"
        if not btrfs_cmd:
            return False, "btrfs command not found"
        mk_res, mk_err = self.run_command([mkdir_cmd, "-p", target_parent], timeout=30)
        if mk_err or not mk_res or mk_res.returncode != 0:
            return False, mk_err or "Failed to prepare snapshot directory"
        snap_res, snap_err = self.run_command(
            [btrfs_cmd, "subvolume", "snapshot", "-r", source_path, snapshot_path],
            timeout=180,
        )
        if snap_err or not snap_res or snap_res.returncode != 0:
            return False, snap_err or "Failed to create snapshot"
        return True, ""

    def _snapshot_via_send_receive(self, source_path: str, snapshot_path: str) -> Tuple[bool, str]:
        target_parent = os.path.dirname(snapshot_path)
        name = os.path.basename(snapshot_path)
        mkdir_cmd = self._mkdir_cmd()
        btrfs_cmd = self._btrfs_cmd()
        if not mkdir_cmd:
            return False, "mkdir command not found"
        if not btrfs_cmd:
            return False, "btrfs command not found"
        source_parent = self._best_temp_snapshot_parent(source_path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        temp_name = f".alvaos-send-{self._slugify(name)}-{stamp}-{os.getpid()}"
        temp_source_snapshot = os.path.join(source_parent, temp_name)

        mk_res, mk_err = self.run_command([mkdir_cmd, "-p", target_parent], timeout=30)
        if mk_err or not mk_res or mk_res.returncode != 0:
            return False, mk_err or "Failed to prepare target directory"

        ok, err = self._snapshot_direct(source_path, temp_source_snapshot)
        if (not ok) and err and "invalid cross-device link" in str(err).lower():
            # Retry once with source itself as parent.
            fallback_parent = self._normalize_path(source_path)
            fallback_snapshot = os.path.join(fallback_parent, temp_name)
            if fallback_snapshot != temp_source_snapshot:
                temp_source_snapshot = fallback_snapshot
                ok, err = self._snapshot_direct(source_path, temp_source_snapshot)
        if not ok:
            return False, f"Failed to create temporary snapshot for transfer: {err}"

        stream_file = os.path.join(
            self.state_dir,
            f"btrfs-send-{int(datetime.now(timezone.utc).timestamp())}-{os.getpid()}.stream"
        )

        try:
            send_res, send_err = self.run_command(
                [btrfs_cmd, "send", "-f", stream_file, temp_source_snapshot],
                timeout=600,
            )
            if send_err or not send_res or send_res.returncode != 0:
                return False, send_err or "Failed to send snapshot stream"

            recv_res, recv_err = self.run_command(
                [btrfs_cmd, "receive", "-f", stream_file, target_parent], timeout=600
            )
            if recv_err or not recv_res or recv_res.returncode != 0:
                return False, recv_err or "Failed to receive snapshot stream"

            received_snapshot = os.path.join(target_parent, os.path.basename(temp_source_snapshot))
            if self._normalize_path(received_snapshot) != self._normalize_path(snapshot_path):
                move_ok, move_err = self._move_path_best_effort(received_snapshot, snapshot_path, timeout=60)
                if not move_ok:
                    return False, move_err or "Failed to rename received snapshot"

            return True, ""
        finally:
            self.run_command([btrfs_cmd, "subvolume", "delete", temp_source_snapshot], timeout=120)
            try:
                if os.path.exists(stream_file):
                    os.remove(stream_file)
            except Exception:
                pass

    def create_snapshot(
        self,
        source_path: str,
        label: Optional[str] = None,
        trigger: str = "manual",
        target_path: Optional[str] = None,
        snapshot_class: str = "data",
    ) -> Tuple[bool, Dict]:
        source = self._normalize_path(source_path)
        if not source:
            return False, {"error": "source_path is required"}
        if platform.system() == "Linux":
            pool_mount_points = {self._normalize_path(mp) for _, mp in self._list_pool_mounts()}
            if source in pool_mount_points:
                return False, {
                    "error": "Pool root snapshots are not supported for restore. Select a subvolume source."
                }
        if platform.system() == "Linux" and self._path_on_system_disk(source):
            return False, {"error": f"Source path is on the system disk and is not allowed: {source}"}
        if platform.system() == "Linux":
            source_is_subvolume = self._path_is_btrfs_subvolume(source)
            if not source_is_subvolume:
                if not os.path.exists(source):
                    return False, {"error": f"Source path not found: {source}"}
                return False, {"error": f"Source path is not a Btrfs subvolume: {source}"}
        else:
            if not os.path.exists(source):
                return False, {"error": f"Source path not found: {source}"}

        settings = self.get_settings()
        # New Settings Structure
        pb = settings.get("pool_backup", {})
        selected_target = self._normalize_path(target_path or pb.get("target_path"))
        
        if selected_target:
            target_root = self._build_selected_data_target_root(source, selected_target)
        else:
            target_root = self._build_default_data_target_root(source)
        if platform.system() == "Linux" and self._path_on_system_disk(target_root):
            return False, {"error": f"Target path is on the system disk and is not allowed: {target_root}"}

        snapshot_time = datetime.now(timezone.utc)
        name = f"{snapshot_time.strftime('%Y%m%d-%H%M%S')}-{self._slugify(label or trigger or 'snapshot')}"
        snapshot_path = os.path.join(target_root, name)

        if platform.system() == "Linux":
            target_parent = self._normalize_path(os.path.dirname(snapshot_path))
            nested_target = target_parent == source or target_parent.startswith(source + os.sep)
            ok_container, container_err, resolved_parent = self._ensure_snapshot_container(
                target_parent,
                force_subvolume=nested_target
            )
            if not ok_container:
                return False, {"error": container_err or "Failed to prepare target path"}
            if resolved_parent != target_parent:
                target_root = resolved_parent
                snapshot_path = os.path.join(target_root, name)
                target_parent = resolved_parent

            if self._same_filesystem(source, target_parent):
                ok, err = self._snapshot_direct(source, snapshot_path)
                if (not ok) and err and "invalid cross-device link" in str(err).lower():
                    ok, err = self._snapshot_via_send_receive(source, snapshot_path)
            else:
                ok, err = self._snapshot_via_send_receive(source, snapshot_path)

            if not ok:
                return False, {"error": err or "Failed to create snapshot"}

        entry = {
            "id": f"{snapshot_time.strftime('%Y%m%d%H%M%S')}-{abs(hash(snapshot_path)) % 100000}",
            "source_path": source,
            "snapshot_name": name,
            "snapshot_path": snapshot_path,
            "target_root": target_root,
            "target_base_path": selected_target or None,
            "created_at": snapshot_time.isoformat(),
            "trigger": trigger,
            "snapshot_class": snapshot_class,
        }
        self._append_snapshot(entry)
        return True, entry

    def create_system_snapshot(
        self,
        label: Optional[str] = None,
        trigger: str = "manual",
        target_path: Optional[str] = None,
    ) -> Tuple[bool, Dict]:
        settings = self.get_settings()
        sb = settings.get("system_backup", {})
        explicit_target = self._normalize_path(target_path)
        default_target = self._normalize_path(DEFAULT_SETTINGS["system_backup"]["target_path"])
        target_base = self._normalize_path(explicit_target or sb.get("target_path") or default_target)

        if platform.system() == "Linux":
            if not self._path_is_btrfs_subvolume("/"):
                return False, {"error": "Full system snapshots require Btrfs root"}

            def _validate_target(path: str) -> Tuple[bool, str]:
                mkdir_cmd = self._mkdir_cmd()
                if not mkdir_cmd:
                    return False, "mkdir command not found"
                mk_res, mk_err = self.run_command([mkdir_cmd, "-p", path], timeout=30)
                if mk_err or not mk_res or mk_res.returncode != 0:
                    return False, mk_err or "Failed to prepare system snapshot target path"
                if not self._path_on_btrfs(path):
                    return False, f"System snapshot target is not on Btrfs: {path}"
                if not self._same_filesystem("/", path):
                    return False, "System rollback requires snapshots on the root filesystem"
                return True, ""

            target_ok, target_err = _validate_target(target_base)
            if (not target_ok) and default_target and default_target != target_base:
                fallback_ok, fallback_err = _validate_target(default_target)
                if fallback_ok:
                    target_base = default_target
                    target_ok = True
                else:
                    target_err = fallback_err
            if not target_ok:
                return False, {"error": target_err or "Failed to validate system snapshot target path"}

        snapshot_time = datetime.now(timezone.utc)
        name = f"{snapshot_time.strftime('%Y%m%d-%H%M%S')}-{self._slugify(label or trigger or 'system')}"
        snapshot_path = os.path.join(target_base, name)

        if platform.system() == "Linux":
            ok, err = self._snapshot_direct("/", snapshot_path)
            if not ok:
                return False, {"error": err or "Failed to create full system snapshot"}

        entry = {
            "id": f"sys-{snapshot_time.strftime('%Y%m%d%H%M%S')}-{abs(hash(snapshot_path)) % 100000}",
            "source_path": "/",
            "snapshot_name": name,
            "snapshot_path": snapshot_path,
            "target_root": target_base,
            "target_base_path": target_base,
            "created_at": snapshot_time.isoformat(),
            "trigger": trigger,
            "snapshot_class": "system",
        }
        self._append_snapshot(entry)
        self._save_status({"system_last_snapshot_at": snapshot_time.isoformat()})
        return True, entry

    def _delete_snapshot_path(self, path: str) -> bool:
        target = self._normalize_path(path)
        if not target:
            return False
        if platform.system() != "Linux":
            return True
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return False
        res, err = self.run_command([btrfs_cmd, "subvolume", "delete", target], timeout=120)
        return bool(res and res.returncode == 0 and not err)

    def _enforce_retention(self, source_path: str, keep_last: int, snapshot_class: str,
                           mode: str = "count") -> None:
        source = self._normalize_path(source_path)
        entries = self.list_snapshots(source_path=source, snapshot_class=snapshot_class)
        if mode == "smart":
            kept = {id(item) for item in smart_keep(entries, datetime.now(timezone.utc))}
            dropped = [item for item in entries if id(item) not in kept]
        else:
            dropped = entries[max(1, int(keep_last)):]
        if not dropped:
            return
        remove_paths = set()
        for item in dropped:
            remove_paths.add(self._normalize_path(item.get("snapshot_path")))
        all_entries = self._load_json(self.snapshots_file, [])
        if not isinstance(all_entries, list):
            all_entries = []
        filtered = []
        for item in all_entries:
            snap_path = self._normalize_path(item.get("snapshot_path"))
            if snap_path in remove_paths:
                self._delete_snapshot_path(snap_path)
                continue
            filtered.append(item)
        self._save_snapshots(filtered)

    def run_backup_now(
        self,
        backup_type: str = "pool",
        sources: Optional[List[str]] = None,
        trigger: str = "manual",
        target_path: Optional[str] = None,
    ) -> Dict:
        with self._job_lock:
            settings = self.get_settings()
            
            created = []
            failed = []
            
            if backup_type == "pool":
                # Pool Backup Logic
                current_settings = settings.get("pool_backup", {})
                selected = sources if isinstance(sources, list) and sources else current_settings.get("sources", [])
                selected = [self._normalize_path(s) for s in selected if self._normalize_path(s)]
                
                if not selected:
                    error = "No backup sources configured"
                    self._save_status({"pool_last_status": "error", "pool_last_error": error})
                    return {"success": False, "error": error}

                for source in selected:
                     ok, payload = self.create_snapshot(
                         source_path=source,
                         label=trigger,
                         trigger=trigger,
                         target_path=target_path,
                         snapshot_class="data",
                     )
                     if ok:
                         created.append(payload)
                         self._enforce_retention(source, current_settings.get("keep_last", 30), "data",
                                                current_settings.get("retention", "count"))
                     else:
                         failed.append({"source_path": source, "error": payload.get("error", "unknown error")})
                
                status_payload: Dict[str, Any] = {
                    "pool_last_run_at": self._now_iso(),
                    "pool_last_status": "success" if not failed else ("partial" if created else "error"),
                    "pool_last_error": "; ".join([f["error"] for f in failed]) if failed else "",
                }
                # Update next run if scheduled
                if current_settings.get("enabled") and current_settings.get("sources"):
                     next_run = datetime.now(timezone.utc) + timedelta(minutes=current_settings.get("interval_minutes", 1440))
                     status_payload["pool_next_run_at"] = next_run.isoformat()
                else:
                     status_payload["pool_next_run_at"] = None
                self._save_status(status_payload)

            elif backup_type == "system":
                # System Backup Logic
                current_settings = settings.get("system_backup", {})
                full_data_target_path: Optional[str] = self._normalize_path(target_path)
                if not full_data_target_path:
                    full_data_target_path = self._normalize_path(
                        settings.get("pool_backup", {}).get("target_path")
                    )
                if (
                    platform.system() == "Linux"
                    and full_data_target_path
                    and self._path_on_system_disk(full_data_target_path)
                ):
                    full_data_target_path = None

                ok, payload = self.create_system_snapshot(label=trigger, trigger=trigger, target_path=target_path)
                if ok:
                     created.append(payload)
                     self._enforce_retention("/", current_settings.get("keep_last", 10), "system")

                     full_data_created = []
                     for source in self._collect_full_backup_sources():
                         source_path = self._normalize_path(source.get("path"))
                         if not source_path:
                             continue
                         data_ok, data_payload = self.create_snapshot(
                             source_path=source_path,
                             label=f"{trigger}-full",
                             trigger=trigger,
                             target_path=full_data_target_path,
                             snapshot_class="full_data",
                         )
                         if data_ok:
                             created.append(data_payload)
                             full_data_created.append({
                                 "source": source,
                                 "snapshot": data_payload,
                             })
                             self._enforce_retention(
                                 source_path,
                                 current_settings.get("keep_last", 10),
                                 "full_data",
                             )
                         else:
                             failed.append({
                                 "source_path": source_path,
                                 "error": data_payload.get("error", "unknown error"),
                             })

                     manifest_path, manifest_err = self._write_full_backup_manifest(
                         payload,
                         full_data_created,
                         failed,
                     )
                     if manifest_path:
                         payload["full_backup_manifest_path"] = manifest_path
                     if manifest_err:
                         failed.append({
                             "source_path": "/",
                             "error": f"Failed to write full-backup manifest: {manifest_err}",
                         })
                else:
                     failed.append({"source_path": "/", "error": payload.get("error", "unknown error")})

                status_payload = {
                    "system_last_run_at": self._now_iso(),
                    "system_last_status": "success" if not failed else ("partial" if created else "error"),
                    "system_last_error": "; ".join([f["error"] for f in failed]) if failed else "",
                }
                if current_settings.get("enabled"):
                     next_run = datetime.now(timezone.utc) + timedelta(minutes=current_settings.get("interval_minutes", 10080))
                     status_payload["system_next_run_at"] = next_run.isoformat()
                else:
                     status_payload["system_next_run_at"] = None
                self._save_status(status_payload)

            else:
                 return {"success": False, "error": f"Unknown backup_type: {backup_type}"}

            return {
                "success": len(created) > 0 and not failed,
                "created": created,
                "failed": failed,
                "status": "success" if not failed else ("partial" if created else "error"),
            }

    # ── Single files from a restore point ────────────────────────────────

    def _known_data_snapshot(self, snapshot_path: str) -> Optional[Dict]:
        wanted = self._normalize_path(snapshot_path)
        for entry in self.list_snapshots():
            if (entry.get("snapshot_class") or "data") in ("data", "full_data", "copy") \
                    and self._normalize_path(entry.get("snapshot_path")) == wanted:
                return entry
        return None

    def _list_dir(self, path: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
        res, err = self.run_command(
            [CMD["FIND"], path, "-mindepth", "1", "-maxdepth", "1", "-printf", FIND_LIST_FORMAT], timeout=30)
        if err or res is None or res.returncode != 0:
            return None, err or "The folder could not be read."
        return parse_find_listing(res.stdout), ""

    def browse_snapshot(self, snapshot_path: str, rel_path: str = "") -> Tuple[bool, Dict]:
        """What a folder looked like in a restore point, and which of its
        entries are not in the folder any more."""
        entry = self._known_data_snapshot(snapshot_path)
        if not entry:
            return False, {"error": "This restore point is not known."}
        rel = clean_relative_path(rel_path)
        if rel is None:
            return False, {"error": "Invalid path."}
        snap_root = self._normalize_path(entry.get("snapshot_path"))
        then, err = self._list_dir(os.path.join(snap_root, rel) if rel else snap_root)
        if then is None:
            return False, {"error": err}
        source = self._normalize_path(entry.get("source_path"))
        now, _ = self._list_dir(os.path.join(source, rel) if rel else source)
        current = {e["name"] for e in (now or [])}
        for item in then:
            item["exists_now"] = item["name"] in current if now is not None else None
        return True, {"path": rel, "entries": then, "snapshot": entry, "folder_exists_now": now is not None}

    def restore_item(self, snapshot_path: str, rel_path: str) -> Tuple[bool, Dict]:
        """Copy one file or folder from a restore point back to where it was.
        Nothing is overwritten: if the name is taken, the copy gets
        '(restored <date>)' in its name."""
        entry = self._known_data_snapshot(snapshot_path)
        if not entry:
            return False, {"error": "This restore point is not known."}
        rel = clean_relative_path(rel_path)
        if not rel:
            return False, {"error": "Choose a file or folder to restore."}
        parent_rel, name = os.path.split(rel)
        source = self._normalize_path(entry.get("source_path"))
        target_dir = os.path.join(source, parent_rel) if parent_rel else source
        snap_root = self._normalize_path(entry.get("snapshot_path"))
        then, _ = self._list_dir(os.path.join(snap_root, parent_rel) if parent_rel else snap_root)
        item = next((e for e in then or [] if e["name"] == name), None)
        if item is None:
            return False, {"error": f'"{name}" is not in this restore point.'}
        current, _ = self._list_dir(target_dir)
        if current is None:
            folder = parent_rel or os.path.basename(source)
            return False, {"error": f'The folder "{folder}" is not there any more. Restore that folder instead.'}
        taken = {e["name"] for e in current}
        target_name = name
        if target_name in taken:
            when = self._parse_iso(entry.get("created_at")) or datetime.now(timezone.utc)
            target_name = restored_name(name, when.astimezone(), item["type"] == "folder")
            if target_name in taken:
                return False, {"error": f'"{target_name}" is already there.'}
        res, err = self.run_command(
            [CMD["CP"], "-a", "--reflink=auto", "--no-clobber", "--",
             os.path.join(snap_root, rel), os.path.join(target_dir, target_name)], timeout=3600)
        if err or res is None or res.returncode != 0:
            return False, {"error": err or "The copy did not work."}
        return True, {"restored_as": os.path.join(parent_rel, target_name) if parent_rel else target_name,
                      "renamed": target_name != name}

    def restore_snapshot(self, snapshot_path: str, source_path: Optional[str] = None) -> Tuple[bool, Dict]:
        snapshot = self._normalize_path(snapshot_path)
        if not snapshot:
            return False, {"error": "snapshot_path is required"}

        target_source = self._normalize_path(source_path)
        metadata = None
        if not target_source:
            for item in self.list_snapshots():
                if self._normalize_path(item.get("snapshot_path")) == snapshot:
                    target_source = self._normalize_path(item.get("source_path"))
                    metadata = item
                    break
        elif target_source:
            for item in self.list_snapshots(source_path=target_source):
                if self._normalize_path(item.get("snapshot_path")) == snapshot:
                    metadata = item
                    break

        if not target_source:
            return False, {"error": "source_path is required"}

        if target_source == "/" or (metadata and (metadata.get("snapshot_class") == "system")):
            return False, {"error": "Use system rollback endpoint for full system snapshots"}
        if metadata and metadata.get("snapshot_class") == "copy":
            return False, {"error": "This copy is on the backup disk. Use \"Get files\" to bring back what you need."}

        if platform.system() != "Linux":
            return False, {"error": "Restores are only possible on the AlvaOS NAS itself (Linux)"}

        pool_mount_points = {self._normalize_path(mp) for _, mp in self._list_pool_mounts()}
        if target_source in pool_mount_points:
            return False, {
                "error": "Restoring a pool root is not supported. Restore a subvolume snapshot instead."
            }

        if not self._path_exists_or_is_subvolume(snapshot):
            # Fallback resolution: same source + same snapshot name, but different stored path root.
            snapshot_name = os.path.basename(snapshot)
            if snapshot_name:
                for item in self.list_snapshots(source_path=target_source):
                    item_name = str(item.get("snapshot_name") or "").strip() or os.path.basename(
                        self._normalize_path(item.get("snapshot_path"))
                    )
                    if item_name != snapshot_name:
                        continue
                    candidate = self._normalize_path(item.get("snapshot_path"))
                    if candidate and self._path_exists_or_is_subvolume(candidate):
                        snapshot = candidate
                        break
            if metadata and not self._path_exists_or_is_subvolume(snapshot):
                snapshot_name = str(metadata.get("snapshot_name") or "").strip() or os.path.basename(snapshot)
                target_root = self._normalize_path(metadata.get("target_root"))
                legacy_prefix = f".alvaos-send-{self._slugify(snapshot_name)}-"
                if snapshot_name and target_root and os.path.isdir(target_root):
                    try:
                        for entry_name in sorted(os.listdir(target_root), reverse=True):
                            if not entry_name.startswith(legacy_prefix):
                                continue
                            candidate = self._normalize_path(os.path.join(target_root, entry_name))
                            if candidate and self._path_exists_or_is_subvolume(candidate):
                                snapshot = candidate
                                break
                    except Exception:
                        pass

        if not self._path_exists_or_is_subvolume(snapshot):
            return False, {"error": f"Snapshot not found: {snapshot}"}

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        previous_path = f"{target_source}.pre-restore-{stamp}"
        moved_old = False
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return False, {"error": "btrfs command not found"}

        if os.path.exists(target_source):
            move_ok, move_err = self._move_path_best_effort(target_source, previous_path, timeout=60)
            if not move_ok:
                return False, {"error": f"Failed to stage current data for restore: {move_err or 'unknown error'}"}
            moved_old = True

        restore_res, restore_err = self.run_command(
            [btrfs_cmd, "subvolume", "snapshot", snapshot, target_source],
            timeout=180,
        )
        if restore_err or not restore_res or restore_res.returncode != 0:
            if moved_old and not os.path.exists(target_source):
                self._move_path_best_effort(previous_path, target_source, timeout=60)
            return False, {"error": f"Restore failed: {restore_err or 'unknown error'}"}

        return True, {
            "restored_to": target_source,
            "previous_backup": previous_path if moved_old else None,
            "message": "Restore completed",
        }

    def rollback_system_snapshot(self, snapshot_path: str) -> Tuple[bool, Dict]:
        snapshot = self._normalize_path(snapshot_path)
        if not snapshot:
            return False, {"error": "snapshot_path is required"}

        if platform.system() != "Linux":
            return False, {"error": "System rollback is supported on Linux only"}

        if not self._path_exists_or_is_subvolume(snapshot):
            snapshot_name = os.path.basename(snapshot)
            if snapshot_name:
                for item in self.list_system_snapshots():
                    item_name = str(item.get("snapshot_name") or "").strip() or os.path.basename(
                        self._normalize_path(item.get("snapshot_path"))
                    )
                    if item_name != snapshot_name:
                        continue
                    candidate = self._normalize_path(item.get("snapshot_path"))
                    if candidate and self._path_exists_or_is_subvolume(candidate):
                        snapshot = candidate
                        break

        if not self._path_exists_or_is_subvolume(snapshot):
            return False, {"error": f"Snapshot not found: {snapshot}"}

        if not self._same_filesystem("/", snapshot):
            return False, {
                "error": (
                    "Snapshot is not on the root filesystem and cannot be activated by default-subvolume switch. "
                    "Use installer rollback: reboot from the AlvaOS installer media, open Recovery -> Rollback, "
                    "select this snapshot, apply rollback, then reboot from disk."
                )
            }

        snapshot_subvol_id = self._get_subvolume_id(snapshot)
        if snapshot_subvol_id is None:
            return False, {"error": "Could not resolve snapshot subvolume ID"}
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return False, {"error": "btrfs command not found"}

        previous_default = self._get_default_subvolume_id("/")
        set_res, set_err = self.run_command(
            [btrfs_cmd, "subvolume", "set-default", str(snapshot_subvol_id), "/"],
            timeout=60,
        )
        if set_err or not set_res or set_res.returncode != 0:
            return False, {"error": set_err or "Failed to set default subvolume for rollback"}

        now = self._now_iso()
        self._save_status({
            "system_last_rollback_at": now,
            "system_pending_reboot": True,
            "system_pending_snapshot_path": snapshot,
            "system_previous_default_subvolume_id": previous_default,
            "system_target_default_subvolume_id": snapshot_subvol_id,
        })

        return True, {
            "message": "System rollback prepared. Reboot to boot into selected snapshot.",
            "snapshot_path": snapshot,
            "snapshot_subvolume_id": snapshot_subvol_id,
            "previous_default_subvolume_id": previous_default,
            "reboot_required": True,
        }

    def _schedule_loop(self):
        while not self._stop_event.is_set():
            try:
                self._schedule_tick()
            except Exception as e:
                scheduler_error = f"Scheduler error: {e}"
                self._save_status({
                    "pool_last_status": "error",
                    "pool_last_error": scheduler_error,
                    "system_last_status": "error",
                    "system_last_error": scheduler_error,
                })
            self._stop_event.wait(30)

    def _schedule_tick(self):
        settings = self.get_settings()
        now = datetime.now(timezone.utc)
        
        # Check Pool Backup
        pb = settings.get("pool_backup", {})
        if pb.get("enabled") and pb.get("sources"):
             # Check if run needed
             should_run = False
             with self._schedule_lock:
                 status = self.get_status()
                 next_run = self._parse_iso(status.get("pool_next_run_at"))
                 if next_run is None:
                     # Initialize if missing
                     self._refresh_next_run(settings)
                 elif now >= next_run:
                     should_run = True

             if should_run:
                 # Launch in thread to not block tick
                 threading.Thread(target=self.run_backup_now, kwargs={"backup_type": "pool", "trigger": "schedule"}).start()

        # Check System Backup
        sb = settings.get("system_backup", {})
        if sb.get("enabled"):
             should_run = False
             with self._schedule_lock:
                 status = self.get_status()
                 next_run = self._parse_iso(status.get("system_next_run_at"))
                 if next_run is None:
                     self._refresh_next_run(settings)
                 elif now >= next_run:
                     should_run = True
            
             if should_run:
                 threading.Thread(target=self.run_backup_now, kwargs={"backup_type": "system", "trigger": "schedule"}).start()
