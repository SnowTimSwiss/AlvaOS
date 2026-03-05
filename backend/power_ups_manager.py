#!/usr/bin/env python3
"""
AlvaOS Battery UPS Manager
Simple battery monitor for laptop-battery-as-UPS behavior.
"""

import json
import os
import shlex
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SETTINGS = {
    "enabled": False,
    "charge_limit_percent": 80,
    "shutdown_percent": 20,
    "monitor_interval_seconds": 30,
}


class PowerUpsManager:
    def __init__(self, state_file="/var/lib/alvaos/power_ups_state.json", poweroff_cmd="/usr/sbin/poweroff"):
        self.state_file = state_file
        self.poweroff_cmd = poweroff_cmd
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._last_action = {
            "type": "",
            "message": "",
            "timestamp": "",
        }
        self._last_shutdown_ts = 0.0

    def _utc_now_iso(self):
        return datetime.now(timezone.utc).isoformat()

    def _clamp_int(self, value, min_value, max_value, fallback):
        try:
            value = int(value)
        except Exception:
            return fallback
        return max(min_value, min(max_value, value))

    def _settings_defaults(self):
        return dict(DEFAULT_SETTINGS)

    def _normalize_settings(self, payload):
        defaults = self._settings_defaults()
        if not isinstance(payload, dict):
            payload = {}
        merged = defaults
        merged.update(payload)
        merged["enabled"] = bool(merged.get("enabled", defaults["enabled"]))
        merged["charge_limit_percent"] = self._clamp_int(merged.get("charge_limit_percent"), 50, 100, defaults["charge_limit_percent"])
        merged["shutdown_percent"] = self._clamp_int(merged.get("shutdown_percent"), 5, 80, defaults["shutdown_percent"])
        merged["monitor_interval_seconds"] = self._clamp_int(
            merged.get("monitor_interval_seconds"), 10, 300, defaults["monitor_interval_seconds"]
        )
        if merged["shutdown_percent"] >= merged["charge_limit_percent"]:
            merged["shutdown_percent"] = max(5, min(merged["charge_limit_percent"] - 5, 80))
        return merged

    def _ensure_state_dir(self):
        Path(os.path.dirname(self.state_file)).mkdir(parents=True, exist_ok=True)

    def get_settings(self):
        with self._lock:
            settings, had_file = self._load_settings_unlocked()
            if not had_file:
                self._save_settings_unlocked(settings)
            return settings

    def _load_settings_unlocked(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                normalized = self._normalize_settings(loaded)
                if normalized != loaded:
                    self._save_settings_unlocked(normalized)
                return normalized, True
        except Exception:
            pass
        return self._settings_defaults(), False

    def _save_settings_unlocked(self, settings):
        self._ensure_state_dir()
        normalized = self._normalize_settings(settings)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-power-ups-", suffix=".json", dir=os.path.dirname(self.state_file))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(normalized, f, indent=2)
            os.replace(tmp_path, self.state_file)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        return normalized

    def save_settings(self, payload):
        with self._lock:
            current, _ = self._load_settings_unlocked()
            if isinstance(payload, dict):
                current.update(payload)
            saved = self._save_settings_unlocked(current)
        # Apply charge limit immediately if UPS mode is enabled.
        if saved.get("enabled"):
            self.apply_charge_limit(saved.get("charge_limit_percent"))
        return saved

    def _read_file_value(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    def _list_power_supply_paths(self):
        base = "/sys/class/power_supply"
        try:
            names = sorted(os.listdir(base))
            return [os.path.join(base, n) for n in names]
        except Exception:
            return []

    def _detect_battery(self):
        for path in self._list_power_supply_paths():
            name = os.path.basename(path)
            ptype = self._read_file_value(os.path.join(path, "type")).lower()
            if ptype == "battery" or name.upper().startswith("BAT"):
                return path
        return ""

    def _detect_on_ac(self):
        ac_entries = []
        for path in self._list_power_supply_paths():
            ptype = self._read_file_value(os.path.join(path, "type")).lower()
            if ptype in ("mains", "ac", "usb", "usb_c"):
                online_raw = self._read_file_value(os.path.join(path, "online"))
                online = str(online_raw).strip() in ("1", "y", "yes", "true", "on")
                ac_entries.append({"name": os.path.basename(path), "online": online})
        if not ac_entries:
            return None, []
        return any(item["online"] for item in ac_entries), ac_entries

    def _get_threshold_paths(self, battery_path):
        if not battery_path:
            return {"start": "", "end": ""}
        start = os.path.join(battery_path, "charge_control_start_threshold")
        end = os.path.join(battery_path, "charge_control_end_threshold")
        return {
            "start": start if os.path.exists(start) else "",
            "end": end if os.path.exists(end) else "",
        }

    def _build_privileged_cmd(self, cmd):
        try:
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                return cmd
        except Exception:
            pass
        return ["sudo", "-n"] + cmd

    def _run_privileged(self, cmd, timeout=10):
        final_cmd = self._build_privileged_cmd(cmd)
        return subprocess.run(
            final_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"LC_ALL": "C"},
        )

    def _write_sysfs_int(self, path, value):
        if not path:
            return False, "Path missing"
        value_str = str(int(value))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(value_str)
            return True, None
        except Exception:
            pass

        # Fallback when direct write fails (non-root backend user).
        shell_line = f"printf '%s' {shlex.quote(value_str)} > {shlex.quote(path)}"
        try:
            result = self._run_privileged(["/usr/bin/bash", "-lc", shell_line], timeout=10)
            if result.returncode == 0:
                return True, None
            err = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
            return False, err
        except Exception as e:
            return False, str(e)

    def get_live_status(self):
        battery_path = self._detect_battery()
        battery_present = bool(battery_path)
        on_ac, ac_entries = self._detect_on_ac()

        battery_name = os.path.basename(battery_path) if battery_present else ""
        status = self._read_file_value(os.path.join(battery_path, "status")) if battery_present else ""
        capacity_raw = self._read_file_value(os.path.join(battery_path, "capacity")) if battery_present else ""
        capacity = None
        try:
            if capacity_raw:
                capacity = int(float(capacity_raw))
        except Exception:
            capacity = None

        thresholds = self._get_threshold_paths(battery_path)
        end_current = self._read_file_value(thresholds["end"]) if thresholds["end"] else ""
        start_current = self._read_file_value(thresholds["start"]) if thresholds["start"] else ""

        return {
            "battery_present": battery_present,
            "battery_name": battery_name or None,
            "battery_status": status or None,
            "capacity_percent": capacity,
            "on_ac_power": on_ac,
            "ac_supplies": ac_entries,
            "charge_limit_supported": bool(thresholds["end"]),
            "charge_limit_paths": thresholds,
            "charge_limit_current": {
                "start_percent": int(start_current) if str(start_current).isdigit() else None,
                "end_percent": int(end_current) if str(end_current).isdigit() else None,
            },
            "monitor_timestamp": self._utc_now_iso(),
            "last_action": dict(self._last_action),
        }

    def apply_charge_limit(self, target_percent):
        status = self.get_live_status()
        if not status.get("battery_present"):
            return False, "No battery device detected"
        if not status.get("charge_limit_supported"):
            return False, "Charge limit not supported by this battery"

        target = self._clamp_int(target_percent, 50, 100, 80)
        paths = status.get("charge_limit_paths") or {}
        end_path = paths.get("end") or ""
        start_path = paths.get("start") or ""

        ok_end, err_end = self._write_sysfs_int(end_path, target)
        if not ok_end:
            return False, f"Failed writing end threshold: {err_end}"

        # Optional start threshold for smoother charge behavior.
        if start_path:
            start_target = max(5, min(target - 5, target))
            self._write_sysfs_int(start_path, start_target)

        self._last_action = {
            "type": "charge_limit_applied",
            "message": f"Charge limit set to {target}%",
            "timestamp": self._utc_now_iso(),
        }
        return True, None

    def _trigger_shutdown(self, reason):
        now = time.time()
        # Prevent repeated shutdown calls in fast loops.
        if (now - self._last_shutdown_ts) < 600:
            return False, "Shutdown already triggered recently"
        try:
            result = self._run_privileged([self.poweroff_cmd], timeout=10)
            if result.returncode != 0:
                err = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
                return False, err
            self._last_shutdown_ts = now
            self._last_action = {
                "type": "shutdown",
                "message": reason,
                "timestamp": self._utc_now_iso(),
            }
            return True, None
        except Exception as e:
            return False, str(e)

    def run_monitor_check(self):
        settings = self.get_settings()
        status = self.get_live_status()

        if not settings.get("enabled"):
            return status

        target_limit = settings.get("charge_limit_percent", 80)
        current_end = ((status.get("charge_limit_current") or {}).get("end_percent"))
        if status.get("charge_limit_supported") and current_end != int(target_limit):
            self.apply_charge_limit(target_limit)
            status = self.get_live_status()

        capacity = status.get("capacity_percent")
        on_ac = status.get("on_ac_power")
        battery_status = str(status.get("battery_status") or "").strip().lower()
        on_battery = (on_ac is False) or (on_ac is None and battery_status == "discharging")
        if status.get("battery_present") and capacity is not None and on_battery:
            if int(capacity) <= int(settings.get("shutdown_percent", 20)):
                reason = f"Battery level is {capacity}% (threshold {settings.get('shutdown_percent')}%)"
                self._trigger_shutdown(reason)
                status = self.get_live_status()

        return status

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                self.run_monitor_check()
            except Exception:
                pass

            interval = self.get_settings().get("monitor_interval_seconds", 30)
            try:
                interval = int(interval)
            except Exception:
                interval = 30
            interval = max(10, min(300, interval))
            self._stop_event.wait(interval)

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._monitor_loop, name="alvaos-power-ups-monitor", daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
