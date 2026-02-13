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
    "BTRFS": "/usr/bin/btrfs",
    "MKDIR": "/usr/bin/mkdir",
    "MV": "/usr/bin/mv",
    "BASH": "/usr/bin/bash",
}

DEFAULT_SETTINGS = {
    "auto_enabled": False,
    "interval_minutes": 1440,
    "sources": [],
    "keep_last": 30,
    "snapshot_target_path": "",
    "include_system_in_schedule": False,
    "keep_system_last": 10,
    "system_snapshot_target_path": "/var/lib/alvaos/system-snapshots",
}

DEFAULT_STATUS = {
    "last_run_at": None,
    "next_run_at": None,
    "last_status": "idle",
    "last_error": "",
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
        mounts = self._list_pool_mounts()
        pools = self.load_pools_state() or {}

        for pool_id, mount_point in mounts:
            pool_name = pools.get(pool_id, {}).get("name") or pool_id

            if platform.system() == "Linux":
                subvolumes = self._list_subvolumes(mount_point)
                for sub in subvolumes:
                    name = sub["name"]
                    if name.startswith(".alvaos-snapshots"):
                        continue
                    if name.startswith("system-snapshots"):
                        continue
                    sources.append(
                        {
                            "pool_id": pool_id,
                            "pool_name": pool_name,
                            "name": f"{pool_name}: {name}",
                            "path": sub["path"],
                        }
                    )
            else:
                sources.append(
                    {
                        "pool_id": pool_id,
                        "pool_name": pool_name,
                        "name": f"{pool_name}: root",
                        "path": mount_point,
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

        system_default = self._normalize_path(self.get_settings().get("system_snapshot_target_path"))
        if system_default and system_default not in seen:
            targets.append({
                "name": "System Default: /var/lib/alvaos/system-snapshots",
                "path": system_default,
                "kind": "system"
            })

        return targets

    def get_settings(self) -> Dict:
        settings = self._load_json(self.settings_file, DEFAULT_SETTINGS.copy())
        if not isinstance(settings, dict):
            settings = {}
        merged = DEFAULT_SETTINGS.copy()
        merged.update(settings)
        return self._normalize_settings(merged)

    def _normalize_settings(self, payload: Dict) -> Dict:
        merged = DEFAULT_SETTINGS.copy()
        merged.update(payload or {})

        merged["auto_enabled"] = bool(merged.get("auto_enabled", False))
        merged["include_system_in_schedule"] = bool(merged.get("include_system_in_schedule", False))

        try:
            merged["interval_minutes"] = int(merged.get("interval_minutes", 1440))
        except Exception:
            merged["interval_minutes"] = 1440
        merged["interval_minutes"] = max(15, min(10080, merged["interval_minutes"]))

        try:
            merged["keep_last"] = int(merged.get("keep_last", 30))
        except Exception:
            merged["keep_last"] = 30
        merged["keep_last"] = max(1, min(200, merged["keep_last"]))

        try:
            merged["keep_system_last"] = int(merged.get("keep_system_last", 10))
        except Exception:
            merged["keep_system_last"] = 10
        merged["keep_system_last"] = max(1, min(100, merged["keep_system_last"]))

        raw_sources = merged.get("sources", [])
        if not isinstance(raw_sources, list):
            raw_sources = []
        unique_sources = []
        seen = set()
        for source in raw_sources:
            normalized = self._normalize_path(source)
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_sources.append(normalized)
        merged["sources"] = unique_sources

        merged["snapshot_target_path"] = self._normalize_path(merged.get("snapshot_target_path"))
        system_target = self._normalize_path(merged.get("system_snapshot_target_path"))
        if not system_target:
            system_target = self._normalize_path(DEFAULT_SETTINGS["system_snapshot_target_path"])
        merged["system_snapshot_target_path"] = system_target

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
            if not cfg.get("auto_enabled") or (not cfg.get("sources") and not cfg.get("include_system_in_schedule")):
                self._save_status({"next_run_at": None})
                return
            next_run = datetime.now(timezone.utc) + timedelta(minutes=cfg["interval_minutes"])
            self._save_status({"next_run_at": next_run.isoformat()})

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

    def _same_filesystem(self, path_a: str, path_b: str) -> bool:
        try:
            return os.stat(path_a).st_dev == os.stat(path_b).st_dev
        except Exception:
            return False

    def _path_is_btrfs_subvolume(self, path: str) -> bool:
        if platform.system() != "Linux":
            return True
        target = self._normalize_path(path)
        if not target:
            return False
        res, err = self.run_command([CMD["BTRFS"], "subvolume", "show", target], timeout=20)
        return bool(res and res.returncode == 0 and not err)

    def _path_on_btrfs(self, path: str) -> bool:
        if platform.system() != "Linux":
            return True
        target = self._normalize_path(path)
        if not target or not os.path.exists(target):
            return False
        res, err = self.run_command([CMD["BTRFS"], "filesystem", "show", target], timeout=20)
        return bool(res and res.returncode == 0 and not err)

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
        selected_target = self._normalize_path(target_path or settings.get("snapshot_target_path"))
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

            target_parent = os.path.dirname(snapshot_path)
            mk_res, mk_err = self.run_command([CMD["MKDIR"], "-p", target_parent], timeout=30)
            if mk_err or not mk_res or mk_res.returncode != 0:
                return False, {"error": mk_err or "Failed to prepare target path"}
            if not self._path_on_btrfs(target_parent):
                return False, {"error": f"Target path is not on Btrfs: {target_parent}"}

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
        target_base = self._normalize_path(target_path or settings.get("system_snapshot_target_path"))
        if not target_base:
            target_base = self._normalize_path(DEFAULT_SETTINGS["system_snapshot_target_path"])

        if platform.system() == "Linux":
            if not self._path_is_btrfs_subvolume("/"):
                return False, {"error": "Full system snapshots require Btrfs root"}
            mk_res, mk_err = self.run_command([CMD["MKDIR"], "-p", target_base], timeout=30)
            if mk_err or not mk_res or mk_res.returncode != 0:
                return False, {"error": mk_err or "Failed to prepare system snapshot target path"}
            if not self._path_on_btrfs(target_base):
                return False, {"error": f"System snapshot target is not on Btrfs: {target_base}"}
            if not self._same_filesystem("/", target_base):
                return False, {"error": "System rollback requires snapshots on the root filesystem"}

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
        sources: Optional[List[str]] = None,
        trigger: str = "manual",
        include_system: Optional[bool] = None,
        target_path: Optional[str] = None,
    ) -> Dict:
        with self._job_lock:
            settings = self.get_settings()
            selected = sources if isinstance(sources, list) and sources else settings.get("sources", [])
            selected = [self._normalize_path(s) for s in selected if self._normalize_path(s)]
            run_system = include_system if include_system is not None else False

            if not selected and not run_system:
                error = "No backup sources configured"
                self._save_status({"last_status": "error", "last_error": error})
                return {"success": False, "error": error}

            created = []
            failed = []

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
                    self._enforce_retention(source, settings.get("keep_last", 30), "data")
                else:
                    failed.append({"source_path": source, "error": payload.get("error", "unknown error")})

            if run_system:
                ok, payload = self.create_system_snapshot(label=trigger, trigger=trigger)
                if ok:
                    created.append(payload)
                    self._enforce_retention("/", settings.get("keep_system_last", 10), "system")
                else:
                    failed.append({"source_path": "/", "error": payload.get("error", "unknown error")})

            status_payload = {
                "last_run_at": self._now_iso(),
                "last_status": "success" if not failed else ("partial" if created else "error"),
                "last_error": "; ".join([f["error"] for f in failed]) if failed else "",
            }
            if settings.get("auto_enabled") and (settings.get("sources") or settings.get("include_system_in_schedule")):
                next_run = datetime.now(timezone.utc) + timedelta(minutes=settings.get("interval_minutes", 1440))
                status_payload["next_run_at"] = next_run.isoformat()
            else:
                status_payload["next_run_at"] = None
            self._save_status(status_payload)

            return {
                "success": len(created) > 0 and not failed,
                "created": created,
                "failed": failed,
                "status": status_payload["last_status"],
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
                self._save_status({"last_status": "error", "last_error": str(e)})
            self._stop_event.wait(30)

    def _schedule_tick(self):
        settings = self.get_settings()
        has_work = bool(settings.get("sources") or settings.get("include_system_in_schedule"))
        if not settings.get("auto_enabled") or not has_work:
            return

        with self._schedule_lock:
            status = self.get_status()
            next_run = self._parse_iso(status.get("next_run_at"))
            now = datetime.now(timezone.utc)
            if next_run is None:
                next_run = now + timedelta(minutes=settings.get("interval_minutes", 1440))
                self._save_status({"next_run_at": next_run.isoformat()})
                return

            if now < next_run:
                return

        self.run_backup_now(
            trigger="schedule",
            include_system=bool(settings.get("include_system_in_schedule", False)),
        )
