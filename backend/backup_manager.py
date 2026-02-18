#!/usr/bin/env python3
"""
AlvaOS Local Backup Manager
Provides local Btrfs snapshots, restore, and automatic scheduling.
"""

import json
import os
import platform
import re
import shlex
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

CMD = {
    "BTRFS": "/usr/sbin/btrfs",
    "MKDIR": "/usr/bin/mkdir",
    "MV": "/usr/bin/mv",
    "BASH": "/usr/bin/bash",
}

DEFAULT_SETTINGS = {
    # Pool Backup Settings
    "pool_backup": {
        "enabled": False,
        "interval_minutes": 1440,
        "keep_last": 30,
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

DEFAULT_STATUS = {
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
        subvolumes = []
        if platform.system() != "Linux":
            return subvolumes

        res, err = self.run_command([CMD["BTRFS"], "subvolume", "list", mount_point], timeout=10)
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
            pool_name = pools.get(pool_id, {}).get("name") or pool_id

            if mount_norm not in seen_paths:
                seen_paths.add(mount_norm)
                sources.append(
                    {
                        "pool_id": pool_id,
                        "pool_name": pool_name,
                        "name": f"Pool: {pool_name}",
                        "path": mount_norm,
                        "kind": "pool"
                    }
                )

            if platform.system() == "Linux":
                subvolumes = self._list_subvolumes(mount_point)
                for sub in subvolumes:
                    name = sub["name"]
                    if name.startswith(".alvaos-snapshots"):
                        continue
                    if name.startswith("system-snapshots"):
                        continue
                    sub_path = self._normalize_path(sub["path"])
                    if not sub_path or sub_path in seen_paths:
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
            if norm_mount and norm_mount not in seen:
                seen.add(norm_mount)
                targets.append({
                    "name": f"Pool: {pool_name}",
                    "path": norm_mount,
                    "kind": "pool"
                })

            if platform.system() == "Linux":
                for sub in self._list_subvolumes(mount_point):
                    rel = sub.get("name", "")
                    path = self._normalize_path(sub.get("path"))
                    if not path or path in seen:
                        continue
                    if rel.startswith(".alvaos-snapshots"):
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
        merged = DEFAULT_SETTINGS.copy()
        
        # Migration from old flat structure if needed
        # Check if payload has old keys and missing new keys
        if "pool_backup" not in payload and "auto_enabled" in payload:
            # Migrate old pool settings
            merged["pool_backup"] = {
                "enabled": bool(payload.get("auto_enabled")),
                "interval_minutes": int(payload.get("interval_minutes", 1440)),
                "keep_last": int(payload.get("keep_last", 30)),
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
                merged["pool_backup"].update(payload["pool_backup"])
            if "system_backup" in payload:
                merged["system_backup"].update(payload["system_backup"])

        # Validate Pool Backup
        pb = merged["pool_backup"]
        pb["enabled"] = bool(pb.get("enabled", False))
        try:
            pb["interval_minutes"] = max(15, min(43200, int(pb.get("interval_minutes", 1440))))
        except:
            pb["interval_minutes"] = 1440
            
        try:
            pb["keep_last"] = max(1, min(200, int(pb.get("keep_last", 30))))
        except:
            pb["keep_last"] = 30
            
        raw_sources = pb.get("sources", [])
        if not isinstance(raw_sources, list):
            raw_sources = []
        unique_sources = []
        seen = set()
        for source in raw_sources:
            normalized = self._normalize_path(source)
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_sources.append(normalized)
        pb["sources"] = unique_sources
        pb["target_path"] = self._normalize_path(pb.get("target_path"))

        # Validate System Backup
        sb = merged["system_backup"]
        sb["enabled"] = bool(sb.get("enabled", False))
        try:
            sb["interval_minutes"] = max(15, min(43200, int(sb.get("interval_minutes", 10080))))
        except:
            sb["interval_minutes"] = 10080
            
        try:
            sb["keep_last"] = max(1, min(100, int(sb.get("keep_last", 10))))
        except:
            sb["keep_last"] = 10
            
        sb["target_path"] = self._normalize_path(sb.get("target_path"))
        if not sb["target_path"]:
            sb["target_path"] = self._normalize_path(DEFAULT_SETTINGS["system_backup"]["target_path"])

        return merged

    def save_settings(self, payload: Dict) -> Dict:
        payload = payload or {}
        current = self.get_settings()
        current.update(payload)
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
            updates = {}
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
        try:
            res, err = self.run_command([CMD["BTRFS"], "filesystem", "show", path], timeout=10)
            if res and res.returncode == 0:
                m = re.search(r"uuid:\s+([A-Fa-f0-9-]+)", res.stdout, re.IGNORECASE)
                if m:
                    return m.group(1).lower()
        except:
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

    def _path_is_btrfs_subvolume(self, path: str) -> bool:
        if platform.system() != "Linux":
            return True
        target = self._normalize_path(path)
        if not target:
            return False
        res, err = self.run_command([CMD["BTRFS"], "subvolume", "show", target], timeout=20)
        return bool(res and res.returncode == 0 and not err)

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
        res, err = self.run_command([CMD["BTRFS"], "filesystem", "show", probe], timeout=20)
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
        mk_parent_res, mk_parent_err = self.run_command([CMD["MKDIR"], "-p", parent], timeout=30)
        if mk_parent_err or not mk_parent_res or mk_parent_res.returncode != 0:
            return False, mk_parent_err or "Failed to prepare snapshot target parent path", ""

        if not self._path_on_btrfs(parent):
            return False, f"Target path is not on Btrfs: {parent}", ""

        if os.path.exists(container):
            if force_subvolume and not self._path_is_btrfs_subvolume(container):
                return False, f"Snapshot container is not a Btrfs subvolume: {container}", ""
            return True, "", container

        if force_subvolume:
            create_res, create_err = self.run_command([CMD["BTRFS"], "subvolume", "create", container], timeout=60)
            if create_err or not create_res or create_res.returncode != 0:
                return False, create_err or "Failed to create snapshot container subvolume", ""
            return True, "", container

        mk_res, mk_err = self.run_command([CMD["MKDIR"], "-p", container], timeout=30)
        if mk_err or not mk_res or mk_res.returncode != 0:
            return False, mk_err or "Failed to prepare snapshot target path", ""
        return True, "", container

    def _get_subvolume_id(self, path: str) -> Optional[int]:
        res, err = self.run_command([CMD["BTRFS"], "subvolume", "show", path], timeout=20)
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
        res, err = self.run_command([CMD["BTRFS"], "subvolume", "get-default", path], timeout=20)
        if err or not res or res.returncode != 0:
            return None
        m = re.search(r"ID\\s+(\\d+)", (res.stdout or "").strip())
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
        mk_res, mk_err = self.run_command([CMD["MKDIR"], "-p", manifest_dir], timeout=30)
        if mk_err or not mk_res or mk_res.returncode != 0:
            return None, mk_err or "Failed to prepare manifest directory"

        snapshot_name = str(system_snapshot_entry.get("snapshot_name") or "").strip()
        if not snapshot_name:
            return None, "Missing system snapshot name"

        pools_state = self.load_pools_state() or {}
        if not isinstance(pools_state, dict):
            pools_state = {}

        manifest_path = os.path.join(manifest_dir, f"{snapshot_name}.plan")
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

        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return manifest_path, None
        except Exception as exc:
            return None, str(exc)

    def _snapshot_direct(self, source_path: str, snapshot_path: str) -> Tuple[bool, str]:
        target_parent = os.path.dirname(snapshot_path)
        mk_res, mk_err = self.run_command([CMD["MKDIR"], "-p", target_parent], timeout=30)
        if mk_err or not mk_res or mk_res.returncode != 0:
            return False, mk_err or "Failed to prepare snapshot directory"
        snap_res, snap_err = self.run_command(
            [CMD["BTRFS"], "subvolume", "snapshot", "-r", source_path, snapshot_path],
            timeout=180,
        )
        if snap_err or not snap_res or snap_res.returncode != 0:
            return False, snap_err or "Failed to create snapshot"
        return True, ""

    def _snapshot_via_send_receive(self, source_path: str, snapshot_path: str) -> Tuple[bool, str]:
        target_parent = os.path.dirname(snapshot_path)
        name = os.path.basename(snapshot_path)
        source_parent = self._normalize_path(os.path.dirname(source_path)) or "/"
        temp_source_snapshot = os.path.join(source_parent, name)

        mk_res, mk_err = self.run_command([CMD["MKDIR"], "-p", target_parent], timeout=30)
        if mk_err or not mk_res or mk_res.returncode != 0:
            return False, mk_err or "Failed to prepare target directory"

        ok, err = self._snapshot_direct(source_path, temp_source_snapshot)
        if not ok:
            return False, f"Failed to create temporary snapshot for transfer: {err}"

        stream_file = os.path.join(
            self.state_dir,
            f"btrfs-send-{int(datetime.now(timezone.utc).timestamp())}-{os.getpid()}.stream"
        )

        try:
            send_res, send_err = self.run_command(
                [CMD["BTRFS"], "send", "-f", stream_file, temp_source_snapshot],
                timeout=600,
            )
            if send_err or not send_res or send_res.returncode != 0:
                return False, send_err or "Failed to send snapshot stream"

            receive_cmd = (
                f"{shlex.quote(CMD['BTRFS'])} receive {shlex.quote(target_parent)} "
                f"< {shlex.quote(stream_file)}"
            )
            recv_res, recv_err = self.run_command([CMD["BASH"], "-lc", receive_cmd], timeout=600)
            if recv_err or not recv_res or recv_res.returncode != 0:
                return False, recv_err or "Failed to receive snapshot stream"

            return True, ""
        finally:
            self.run_command([CMD["BTRFS"], "subvolume", "delete", temp_source_snapshot], timeout=120)
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

        snapshot_time = datetime.now(timezone.utc)
        name = f"{snapshot_time.strftime('%Y%m%d-%H%M%S')}-{self._slugify(label or trigger or 'snapshot')}"
        snapshot_path = os.path.join(target_root, name)

        if platform.system() == "Linux":
            if not self._path_is_btrfs_subvolume(source):
                return False, {"error": f"Source path is not a Btrfs subvolume: {source}"}

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
                mk_res, mk_err = self.run_command([CMD["MKDIR"], "-p", path], timeout=30)
                if mk_err or not mk_res or mk_res.returncode != 0:
                    return False, mk_err or "Failed to prepare system snapshot target path"
                if not self._path_on_btrfs(path):
                    return False, f"System snapshot target is not on Btrfs: {path}"
                if not self._same_filesystem("/", path):
                    return False, "System rollback requires snapshots on the root filesystem"
                return True, ""

            target_ok, target_err = _validate_target(target_base)
            if (not target_ok) and (not explicit_target) and default_target and default_target != target_base:
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
        res, err = self.run_command([CMD["BTRFS"], "subvolume", "delete", target], timeout=120)
        return bool(res and res.returncode == 0 and not err)

    def _enforce_retention(self, source_path: str, keep_last: int, snapshot_class: str) -> None:
        keep = max(1, int(keep_last))
        source = self._normalize_path(source_path)
        entries = self.list_snapshots(source_path=source, snapshot_class=snapshot_class)
        if len(entries) <= keep:
            return
        remove_paths = set()
        for item in entries[keep:]:
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
                         self._enforce_retention(source, current_settings.get("keep_last", 30), "data")
                     else:
                         failed.append({"source_path": source, "error": payload.get("error", "unknown error")})
                
                status_payload = {
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
                             target_path=target_path,
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

        if not target_source:
            return False, {"error": "source_path is required"}

        if target_source == "/" or (metadata and (metadata.get("snapshot_class") == "system")):
            return False, {"error": "Use system rollback endpoint for full system snapshots"}

        if platform.system() != "Linux":
            return True, {
                "restored_to": target_source,
                "previous_backup": None,
                "message": "Mock restore completed (non-Linux environment)",
            }

        pool_mount_points = {self._normalize_path(mp) for _, mp in self._list_pool_mounts()}
        if target_source in pool_mount_points:
            return False, {
                "error": "Restoring a pool root is not supported. Restore a subvolume snapshot instead."
            }

        if not os.path.exists(snapshot):
            return False, {"error": f"Snapshot not found: {snapshot}"}

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        previous_path = f"{target_source}.pre-restore-{stamp}"
        moved_old = False

        if os.path.exists(target_source):
            mv_res, mv_err = self.run_command([CMD["MV"], target_source, previous_path], timeout=60)
            if mv_err or not mv_res or mv_res.returncode != 0:
                return False, {"error": f"Failed to stage current data for restore: {mv_err or 'unknown error'}"}
            moved_old = True

        restore_res, restore_err = self.run_command(
            [CMD["BTRFS"], "subvolume", "snapshot", snapshot, target_source],
            timeout=180,
        )
        if restore_err or not restore_res or restore_res.returncode != 0:
            if moved_old and not os.path.exists(target_source):
                self.run_command([CMD["MV"], previous_path, target_source], timeout=60)
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

        if not os.path.exists(snapshot):
            return False, {"error": f"Snapshot not found: {snapshot}"}

        if not self._same_filesystem("/", snapshot):
            return False, {"error": "Snapshot is not on root filesystem. Use installer rollback."}

        snapshot_subvol_id = self._get_subvolume_id(snapshot)
        if snapshot_subvol_id is None:
            return False, {"error": "Could not resolve snapshot subvolume ID"}

        previous_default = self._get_default_subvolume_id("/")
        set_res, set_err = self.run_command(
            [CMD["BTRFS"], "subvolume", "set-default", str(snapshot_subvol_id), "/"],
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
                    "pool_last_error": scheduler_error,
                    "system_last_error": scheduler_error,
                    "last_status": "error",
                    "last_error": str(e),
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
