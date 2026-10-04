"""Smart retention of local snapshots and how it is switched on (backend/backup_manager.py)."""

from datetime import datetime, timedelta, timezone

import pytest

import backup_manager as bm

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def snaps(*ages):
    return [{"snapshot_path": f"/s/{age}", "created_at": (NOW - age).isoformat()} for age in ages]


def kept(entries):
    return {e["snapshot_path"] for e in bm.smart_keep(entries, NOW)}


def test_everything_from_the_last_day_is_kept():
    hourly = snaps(*[timedelta(hours=h) for h in range(24)])
    assert kept(hourly) == {e["snapshot_path"] for e in hourly}


def test_older_snapshots_thin_out_to_one_per_day():
    hourly = snaps(*[timedelta(hours=h) for h in range(24 * 5)])
    result = bm.smart_keep(hourly, NOW)
    older = [e for e in result if NOW - datetime.fromisoformat(e["created_at"]) >= timedelta(hours=24)]
    days = [datetime.fromisoformat(e["created_at"]).date() for e in older]
    assert len(days) == len(set(days))
    assert len(result) < len(hourly)


def test_a_year_of_dailies_keeps_month_weeks_and_months():
    daily = snaps(*[timedelta(days=d) for d in range(400)])
    count = len(kept(daily))
    # 30 days + up to 12 weeks + up to 12 months, with overlaps.
    assert 30 < count <= 30 + 12 + 12
    assert f"/s/{timedelta(days=399)}" not in kept(daily)


def test_the_newest_and_unreadable_entries_are_never_dropped():
    entries = snaps(timedelta(days=900), timedelta(days=1000))
    entries.append({"snapshot_path": "/s/odd", "created_at": "yesterday"})
    assert kept(entries) == {f"/s/{timedelta(days=900)}", "/s/odd"}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(bm.BackupManager, "_resolve_state_dir", lambda self: str(tmp_path))
    monkeypatch.setattr(bm.BackupManager, "_schedule_loop", lambda self: None)
    monkeypatch.setattr(bm.BackupManager, "_path_on_system_disk", lambda self, p: False)
    monkeypatch.setattr(bm.BackupManager, "_same_filesystem", lambda self, a, b: True)
    return bm.BackupManager(lambda *a, **k: (True, ""), lambda: {})


def test_new_installs_use_smart_retention(manager):
    assert manager.get_settings()["pool_backup"]["retention"] == "smart"


def test_settings_saved_before_smart_retention_keep_counting(manager):
    manager._save_json(manager.settings_file, {"pool_backup": {"keep_last": 5}})
    assert manager.get_settings()["pool_backup"]["retention"] == "count"


def test_saving_one_field_keeps_the_others(manager):
    manager.save_settings({"pool_backup": {"retention": "count", "keep_last": 7}})
    manager.save_settings({"pool_backup": {"enabled": True}})
    pool = manager.get_settings()["pool_backup"]
    assert (pool["retention"], pool["keep_last"], pool["enabled"]) == ("count", 7, True)
    assert bm.DEFAULT_SETTINGS["pool_backup"]["retention"] == "smart"
    assert bm.DEFAULT_SETTINGS["pool_backup"]["enabled"] is False


def test_unknown_retention_falls_back_to_smart(manager):
    assert manager.save_settings({"pool_backup": {"retention": "forever"}})["pool_backup"]["retention"] == "smart"
