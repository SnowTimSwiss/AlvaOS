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
    assert hc.save_settings({"scrub": "weekly", "start_hour": 1}) == {"scrub": "weekly", "start_hour": 1, "smart": "standard"}
    assert hc.save_settings({"smart": "short"})["scrub"] == "weekly"
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


def test_an_unreadable_btrfs_time_does_not_start_a_check_every_night():
    pools = {"a": {"name": "main", "mount_point": "/mnt/alvaos/main"}}
    # A btrfs-progs version that prints the start time in another format.
    activity = {"/mnt/alvaos/main": {"scrub": {"state": "finished", "started": "2026-10-02T03:30:00+0200",
                                               "errors": 0, "uncorrectable": 0}}}
    scheduler, started = make(pools, activity)
    assert scheduler.run_once(NIGHT)["a"] == "started"            # first night: never seen, so due
    next_night = NIGHT.replace(day=3)
    assert scheduler.run_once(next_night)["a"] == "not due"       # remembered: not again tomorrow
    assert started == ["/mnt/alvaos/main"]


# ── SMART self-tests ─────────────────────────────────────────────────────────

ATA_OK = {
    "smart_status": {"passed": True},
    "ata_smart_attributes": {"table": [{"id": 5, "raw": {"value": 0}}, {"id": 197, "raw": {"value": 0}},
                                       {"id": 198, "raw": {"value": 0}}]},
    "ata_smart_self_test_log": {"standard": {"table": [
        {"type": {"string": "Short offline"}, "status": {"string": "Completed without error", "passed": True}}]}},
}


def ata(reallocated=0, pending=0, passed=True, test_passed=True, running=False):
    payload = json.loads(json.dumps(ATA_OK))
    payload["smart_status"]["passed"] = passed
    payload["ata_smart_attributes"]["table"][0]["raw"]["value"] = reallocated
    payload["ata_smart_attributes"]["table"][1]["raw"]["value"] = pending
    entry = payload["ata_smart_self_test_log"]["standard"]["table"][0]
    entry["status"] = {"string": "Completed: read failure" if not test_passed else "Completed without error",
                       "passed": test_passed}
    if running:
        payload["ata_smart_data"] = {"self_test": {"status": {"remaining_percent": 60}}}
    return payload


def test_smart_readings_for_ata_and_nvme():
    reading = hc.parse_smart(ata(reallocated=3))
    assert reading["health"] == "passed" and reading["reallocated"] == 3
    assert reading["last_test"]["passed"] is True and reading["test_running"] is False

    nvme = hc.parse_smart({
        "smart_status": {"passed": True},
        "nvme_smart_health_information_log": {"media_errors": 2},
        "nvme_self_test_log": {"table": [{"self_test_code": {"string": "Short"},
                                          "self_test_result": {"value": 7, "string": "Failed segment"}}],
                               "current_self_test_operation": {"value": 0}},
    })
    assert nvme["media_errors"] == 2 and nvme["last_test"]["passed"] is False

    assert hc.parse_smart({"power_mode": "STANDBY"}) is None
    assert hc.parse_smart(None) is None


@pytest.mark.parametrize("record, severity", [
    ({"reading": hc.parse_smart(ata(passed=False))}, "critical"),
    ({"reading": hc.parse_smart(ata(test_passed=False))}, "critical"),
    ({"reading": hc.parse_smart(ata(pending=4))}, "warning"),
    ({"reading": hc.parse_smart(ata(reallocated=12)), "reallocated_baseline": 8}, "warning"),
    ({"reading": hc.parse_smart(ata(reallocated=8)), "reallocated_baseline": 8}, None),   # old, stable
    ({"reading": hc.parse_smart(ata())}, None),
])
def test_what_counts_as_a_disk_problem(record, severity):
    problem = hc.smart_problems(record)
    assert (problem or {}).get("severity") == severity


def make_smart(disks, payloads):
    started = []
    scheduler = hc.SmartScheduler(
        disks=lambda: disks,
        start_test=lambda path, kind: started.append((path, kind)) or "",
        read=lambda path: payloads.get(path),
        log=lambda msg: None,
    )
    return scheduler, started


DISKS = [{"key": "S1", "name": "sdb", "path": "/dev/sdb", "model": "WD", "pool_id": "p1"},
         {"key": "S2", "name": "sdc", "path": "/dev/sdc", "model": "WD", "pool_id": "p1"}]


def test_self_tests_start_at_night_one_long_test_per_night():
    scheduler, started = make_smart(DISKS, {"/dev/sdb": ata(), "/dev/sdc": ata()})
    assert scheduler.run_once(DAY) == {} and started == []

    assert scheduler.run_once(NIGHT) == {"S1": "long started", "S2": "short started"}
    # The next night the other disk gets its long test; the first is not due.
    second = NIGHT.replace(day=3)
    assert scheduler.run_once(second) == {"S1": "not due", "S2": "long started"}
    assert started == [("/dev/sdb", "long"), ("/dev/sdc", "short"), ("/dev/sdc", "long")]


def test_no_long_test_while_the_pool_gets_a_data_check(state):
    state.write_text(json.dumps({"pools": {"p1": {"state": "running"}}}))
    scheduler, started = make_smart(DISKS[:1], {"/dev/sdb": ata()})
    assert scheduler.run_once(NIGHT) == {"S1": "short started"}


def test_running_tests_and_off_are_respected():
    scheduler, started = make_smart(DISKS[:1], {"/dev/sdb": ata(running=True)})
    assert scheduler.run_once(NIGHT) == {"S1": "test running"}
    hc.save_settings({"smart": "off"})
    assert scheduler.run_once(NIGHT.replace(day=5)) == {}
    assert started == []


def test_readings_set_a_baseline_once():
    scheduler, _ = make_smart(DISKS[:1], {"/dev/sdb": ata(reallocated=8)})
    scheduler.run_once(NIGHT)
    assert hc.smart_records()["S1"]["reallocated_baseline"] == 8
    scheduler.read = lambda path: ata(reallocated=12)
    scheduler.run_once(NIGHT.replace(day=3))
    record = hc.smart_records()["S1"]
    assert record["reallocated_baseline"] == 8 and record["reading"]["reallocated"] == 12
    assert hc.smart_problems(record)["severity"] == "warning"


def test_failing_disks_raise_alerts(state, monkeypatch):
    import alerts_manager
    import storage_manager

    state.write_text(json.dumps({"smart": {
        "S1": {"name": "sdb", "model": "WDC WD40", "reading": hc.parse_smart(ata(passed=False))},
        "S2": {"name": "sdc", "model": "WDC WD40", "reading": hc.parse_smart(ata(pending=2))},
        "S3": {"name": "sdd", "model": "WDC WD40", "reading": hc.parse_smart(ata())},
    }}))
    monkeypatch.setattr(storage_manager, "load_pools_state", lambda: {})
    monkeypatch.setattr(storage_manager, "detect_btrfs_pools", lambda: ([], None))

    alerts = {a["id"]: a for a in alerts_manager._collect_system_alerts()}
    assert alerts["disk-S1-smart"]["severity"] == "critical"
    assert alerts["disk-S1-smart"]["title"] == "Disk WDC WD40 (sdb) is failing"
    assert alerts["disk-S2-smart"]["severity"] == "warning"
    assert "disk-S3-smart" not in alerts
