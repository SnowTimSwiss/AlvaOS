#!/usr/bin/env python3
"""What uses the space on a pool: folders, apps and restore points.

`btrfs filesystem du` walks every file, so a scan runs in the background, one
pool at a time, and its result stays until the next scan. For restore points
it reports the exclusive size: about what deleting that one would free.
"""

import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from backup_manager import CMD as BACKUP_CMD, FIND_LIST_FORMAT, parse_find_listing

BTRFS = "/usr/bin/btrfs"
DU_BATCH = 50          # paths per btrfs call (the helper allows up to 64)
DU_TIMEOUT = 3 * 3600  # one batch; big media folders take a while


def parse_fs_du(text: str) -> Dict[str, Dict[str, int]]:
    """{path: {total, exclusive, shared}} from `btrfs filesystem du -s --raw`."""
    result: Dict[str, Dict[str, int]] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4 or not parts[0].isdigit():
            continue
        total, exclusive, shared, path = parts
        result[os.path.normpath(path)] = {
            "total": int(total),
            "exclusive": int(exclusive) if exclusive.isdigit() else 0,
            "shared": int(shared) if shared.isdigit() else 0,
        }
    return result


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


class SpaceScanner:
    def __init__(self, run_command: Callable, list_snapshots: Callable[[], List[Dict]]):
        self.run = run_command
        self.list_snapshots = list_snapshots
        self._lock = threading.Lock()
        self._reports: Dict[str, Dict[str, Any]] = {}
        self._running: Optional[str] = None

    def report(self, pool_id: str) -> Dict[str, Any]:
        with self._lock:
            report = dict(self._reports.get(pool_id) or {"state": "never"})
            if self._running == pool_id:
                report["state"] = "running"
            return report

    def start(self, pool_id: str, mount_point: str, background: bool = True) -> Tuple[bool, str]:
        with self._lock:
            if self._running:
                return False, "A space check is already running. Try again when it is done."
            self._running = pool_id
            previous = self._reports.get(pool_id) or {}
            self._reports[pool_id] = {**previous, "started_at": datetime.now(timezone.utc).isoformat()}
        if background:
            threading.Thread(target=self._scan, args=(pool_id, mount_point), daemon=True,
                             name=f"space-{pool_id}").start()
        else:
            self._scan(pool_id, mount_point)
        return True, ""

    # ── Scan ─────────────────────────────────────────────────────────────

    def _list(self, path: str) -> Optional[List[Dict[str, Any]]]:
        res, err = self.run([BACKUP_CMD["FIND"], path, "-mindepth", "1", "-maxdepth", "1",
                             "-printf", FIND_LIST_FORMAT], timeout=60)
        if err or res is None or res.returncode != 0:
            return None
        return parse_find_listing(res.stdout)

    def _du(self, paths: List[str]) -> Dict[str, Dict[str, int]]:
        sizes: Dict[str, Dict[str, int]] = {}
        for i in range(0, len(paths), DU_BATCH):
            batch = paths[i:i + DU_BATCH]
            res, _err = self.run([BTRFS, "filesystem", "du", "-s", "--raw", *batch], timeout=DU_TIMEOUT)
            # A failing path (removed meanwhile) must not hide the others.
            sizes.update(parse_fs_du(res.stdout if res is not None else ""))
        return sizes

    def _scan(self, pool_id: str, mount_point: str) -> None:
        mount = os.path.normpath(mount_point)
        try:
            top = self._list(mount)
            if top is None:
                raise RuntimeError("The pool could not be read.")
            folders = [e for e in top if e["type"] == "folder" and not e["name"].startswith(".") and e["name"] != "apps"]
            loose_bytes = sum(e["size_bytes"] for e in top if e["type"] == "file")
            apps_dir = os.path.join(mount, "apps")
            apps = [e for e in (self._list(apps_dir) or []) if e["type"] == "folder"] \
                if any(e["name"] == "apps" and e["type"] == "folder" for e in top) else []
            snapshots = [s for s in self.list_snapshots()
                         if _within(os.path.normpath(str(s.get("snapshot_path") or "")), mount)]

            folder_paths = [os.path.join(mount, e["name"]) for e in folders]
            app_paths = [os.path.join(apps_dir, e["name"]) for e in apps]
            snap_paths = [os.path.normpath(str(s["snapshot_path"])) for s in snapshots]
            sizes = self._du(folder_paths + app_paths + snap_paths)

            def sized(paths, names):
                rows = [{"name": name, "path": path, "bytes": sizes[path]["total"]}
                        for path, name in zip(paths, names, strict=True) if path in sizes]
                return sorted(rows, key=lambda r: r["bytes"], reverse=True)

            restore_points: List[Dict[str, Any]] = []
            for snap, path in zip(snapshots, snap_paths, strict=True):
                if path not in sizes:
                    continue
                restore_points.append({
                    "snapshot_path": path,
                    "source_path": snap.get("source_path"),
                    "created_at": snap.get("created_at"),
                    "exclusive_bytes": sizes[path]["exclusive"],
                })
            restore_points.sort(key=lambda r: r["exclusive_bytes"], reverse=True)
            result = {
                "folders": sized(folder_paths, [e["name"] for e in folders]),
                "apps": sized(app_paths, [e["name"] for e in apps]),
                "restore_points": {
                    "count": len(restore_points),
                    "exclusive_bytes": sum(r["exclusive_bytes"] for r in restore_points),
                    "items": restore_points,
                },
                "loose_files_bytes": loose_bytes,
            }
            update = {"state": "done", "result": result, "error": ""}
        except Exception as exc:  # noqa: BLE001 - reported to the page, never raised in a thread
            update = {"state": "error", "error": str(exc)}
        with self._lock:
            previous = self._reports.get(pool_id) or {}
            if update["state"] == "error" and previous.get("result"):
                update["result"] = previous["result"]   # keep showing the last good one
            self._reports[pool_id] = {**previous, **update,
                                      "finished_at": datetime.now(timezone.utc).isoformat()}
            self._running = None
