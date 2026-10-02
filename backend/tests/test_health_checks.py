"""Nightly data checks: when they start, and what they report."""

import json
from datetime import datetime

import pytest

import health_checks as hc

NIGHT = datetime(2026, 10, 2, 3, 30)
DAY = datetime(2026, 10, 2, 14, 0)


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    path = tmp_path / "health_checks.json"
    monkeypatch.setattr(hc, "STATE_FILE", str(path))
    return path


def scrub(state, started=""):
    return {"scrub": {"state": state, "started": started, "errors": 0, "uncorrectable": 0}}


def make(pools, activity, busy=lambda act: ""):
    started = []
    scheduler = hc.HealthScheduler(
        pools=lambda: pools,
        activity=lambda mount: activity[mount],
        busy=busy,
        start_scrub=lambda mount: started.append(mount) or "",
        log=lambda msg: None,
    )
    return scheduler, started


def test_btrfs_times_are_read():
    assert hc.parse_btrfs_time("Wed Oct  1 03:00:01 2026") == datetime(2026, 10, 1, 3, 0, 1)
    assert hc.parse_btrfs_time("") is None
    assert hc.parse_btrfs_time("garbage") is None


@pytest.mark.parametrize("hour, expected", [(2, False), (3, True), (5, True), (6, False), (14, False)])
def test_checks_only_start_at_night(hour, expected):
    assert hc.in_window(datetime(2026, 10, 2, hour, 10), 3) is expected


def test_the_window_can_wrap_past_midnight():
    assert hc.in_window(datetime(2026, 10, 2, 0, 30), 23)
    assert not hc.in_window(datetime(2026, 10, 2, 2, 30), 23)


def test_a_check_is_due_after_the_interval():
    now = datetime(2026, 10, 2, 3, 0)
    assert hc.is_due({"state": "never"}, now, "monthly")
    assert hc.is_due({"state": "finished", "started": "Tue Sep  2 03:00:00 2026"}, now, "monthly")
    assert not hc.is_due({"state": "finished", "started": "Mon Sep 22 03:00:00 2026"}, now, "monthly")
    assert hc.is_due({"state": "finished", "started": "Mon Sep 22 03:00:00 2026"}, now, "weekly")
    assert not hc.is_due({"state": "running", "started": "Tue Jan  6 03:00:00 2026"}, now, "monthly")
    assert not hc.is_due({"state": "never"}, now, "off")


def test_one_pool_at_a_time_and_only_at_night():
    pools = {"a": {"name": "main", "mount_point": "/mnt/alvaos/main"},
             "b": {"name": "media", "mount_point": "/mnt/alvaos/media"}}
    activity = {"/mnt/alvaos/main": scrub("never"), "/mnt/alvaos/media": scrub("never")}
    scheduler, started = make(pools, activity)

    assert scheduler.run_once(DAY) == {"a": "waiting", "b": "waiting"}
    assert started == []

    assert scheduler.run_once(NIGHT) == {"a": "started", "b": "waiting"}
    assert started == ["/mnt/alvaos/main"]


def test_a_busy_pool_is_left_alone():
    pools = {"a": {"name": "main", "mount_point": "/mnt/alvaos/main"}}
    activity = {"/mnt/alvaos/main": scrub("never")}
    scheduler, started = make(pools, activity, busy=lambda act: "A disk is being replaced")
    assert scheduler.run_once(NIGHT) == {"a": "busy: A disk is being replaced"}
    assert started == []


def test_switched_off_means_no_checks(state):
    hc.save_settings({"scrub": "off"})
    pools = {"a": {"name": "main", "mount_point": "/mnt/alvaos/main"}}
    scheduler, started = make(pools, {"/mnt/alvaos/main": scrub("never")})
    assert scheduler.run_once(NIGHT) == {"a": "not due"}
    assert started == []


def test_settings_are_validated():
    with pytest.raises(ValueError):
        hc.save_settings({"scrub": "hourly"})
    with pytest.raises(ValueError):
        hc.save_settings({"start_hour": 24})
    with pytest.raises(ValueError):
        hc.save_settings({"start_hour": True})
    assert hc.save_settings({"scrub": "weekly", "start_hour": 1}) == {"scrub": "weekly", "start_hour": 1}
    assert hc.get_settings()["scrub"] == "weekly"


def test_results_are_recorded_and_only_written_when_they_change(state):
    pools = {"a": {"name": "main", "mount_point": "/mnt/alvaos/main"}}
    activity = {"/mnt/alvaos/main": {"scrub": {"state": "finished", "started": "Wed Oct  1 03:00:01 2026",
                                               "errors": 2, "uncorrectable": 0, "duration": "1:02:03"}}}
    scheduler, _ = make(pools, activity)
    scheduler.run_once(DAY)
    result = hc.last_results()["a"]
    assert result["errors"] == 2 and result["started_at"] == "2026-10-01T03:00:01"

    before = state.stat().st_mtime_ns
    scheduler.run_once(DAY)
    assert state.stat().st_mtime_ns == before


def test_unreadable_pools_do_not_stop_the_others():
    pools = {"a": {"name": "main", "mount_point": "/mnt/alvaos/main"},
             "b": {"name": "media", "mount_point": "/mnt/alvaos/media"}}

    def activity(mount):
        if mount.endswith("main"):
            raise OSError("btrfs missing")
        return scrub("never")

    started = []
    scheduler = hc.HealthScheduler(pools=lambda: pools, activity=activity, busy=lambda a: "",
                                   start_scrub=lambda m: started.append(m) or "", log=lambda m: None)
    outcome = scheduler.run_once(NIGHT)
    assert outcome["a"].startswith("unreadable") and outcome["b"] == "started"


def test_alerts_report_damaged_and_repaired_data(state, monkeypatch):
    import alerts_manager
    import storage_manager

    state.write_text(json.dumps({"pools": {
        "u-1": {"state": "finished", "errors": 3, "uncorrectable": 1},
        "u-2": {"state": "finished", "errors": 2, "uncorrectable": 0},
        "u-3": {"state": "finished", "errors": 0, "uncorrectable": 0},
        "gone": {"state": "finished", "errors": 5, "uncorrectable": 5},
    }}))
    monkeypatch.setattr(storage_manager, "load_pools_state", lambda: {
        "u-1": {"name": "main"}, "u-2": {"name": "media"}, "u-3": {"name": "fast"}})
    monkeypatch.setattr(storage_manager, "detect_btrfs_pools", lambda: ([], None))

    alerts = {a["id"]: a for a in alerts_manager._collect_system_alerts()}

    assert alerts["pool-u-1-scrub-damaged"]["severity"] == "critical"
    assert alerts["pool-u-1-scrub-damaged"]["route"] == "storage.html#pool=u-1"
    assert alerts["pool-u-2-scrub-repaired"]["severity"] == "warning"
    assert not any("u-3" in key or "gone" in key for key in alerts)
