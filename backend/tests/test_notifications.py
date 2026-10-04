"""Notification feed: old entries expire, fresh and unread ones stay."""

import json
from datetime import timedelta

import pytest

import alerts_manager
from common import _utc_now


@pytest.fixture
def feed_file(tmp_path, monkeypatch):
    path = tmp_path / "notifications.json"
    monkeypatch.setattr(alerts_manager, "NOTIFICATIONS_STATE_FILE", str(path))
    monkeypatch.setattr(alerts_manager, "ensure_directories", lambda: None)
    return path


def entry(entry_id, days_old, read=False, dismissed=False):
    return {
        "id": entry_id,
        "ts": (_utc_now() - timedelta(days=days_old)).isoformat(),
        "severity": "warning",
        "title": entry_id,
        "message": "",
        "read": read,
        "dismissed": dismissed,
    }


def test_read_entries_expire_after_a_week_unread_after_a_month(feed_file):
    feed_file.write_text(json.dumps([
        entry("fresh-unread", 1),
        entry("fresh-read", 1, read=True),
        entry("old-read", 8, read=True),
        entry("old-dismissed", 8, dismissed=True),
        entry("old-unread", 20),
        entry("ancient-unread", 31),
    ]))

    ids = [n["id"] for n in alerts_manager.load_notifications()]

    assert ids == ["fresh-unread", "fresh-read", "old-unread"]
    # The pruned list is written back, so the file does not grow forever.
    assert [n["id"] for n in json.loads(feed_file.read_text())] == ids


def test_entries_without_a_timestamp_are_kept(feed_file):
    feed_file.write_text(json.dumps([{"id": "no-ts", "read": True}, "not-a-dict"]))
    assert [n["id"] for n in alerts_manager.load_notifications()] == ["no-ts"]


def test_push_prunes_and_still_dedupes_by_fingerprint(feed_file):
    feed_file.write_text(json.dumps([entry("old-read", 10, read=True)]))

    first = alerts_manager.push_notification("critical", "Pool degraded", "m", fingerprint="pool-x")
    again = alerts_manager.push_notification("critical", "Pool degraded", "m", fingerprint="pool-x")

    assert again["id"] == first["id"]
    stored = json.loads(feed_file.read_text())
    assert [n["id"] for n in stored] == [first["id"]]


def test_feed_counts_unread_only_for_visible_entries(feed_file):
    feed_file.write_text(json.dumps([
        entry("a", 0),
        entry("b", 0, read=True),
        entry("c", 0, dismissed=True),
    ]))
    feed = alerts_manager.get_notifications_feed()
    assert [n["id"] for n in feed["notifications"]] == ["a", "b"]
    assert feed["unread_count"] == 1


def test_io_counters_skip_loopback_and_container_bridges(monkeypatch):
    import api_system

    class Nic:
        def __init__(self, sent, recv):
            self.bytes_sent = sent
            self.bytes_recv = recv

    class Disk:
        read_bytes = 7
        write_bytes = 9

    monkeypatch.setattr(api_system.psutil, "net_io_counters", lambda pernic=False: {
        "lo": Nic(1000, 1000),
        "eth0": Nic(10, 20),
        "docker0": Nic(500, 500),
        "veth12ab": Nic(500, 500),
        "wlan0": Nic(1, 2),
    })
    monkeypatch.setattr(api_system.psutil, "disk_io_counters", lambda: Disk())

    assert api_system._io_counters() == {
        "net_bytes_sent": 11,
        "net_bytes_recv": 22,
        "disk_read_bytes": 7,
        "disk_write_bytes": 9,
    }


def test_io_counters_survive_missing_data(monkeypatch):
    import api_system

    def boom(pernic=False):
        raise OSError("no /proc/net/dev")

    monkeypatch.setattr(api_system.psutil, "net_io_counters", boom)
    monkeypatch.setattr(api_system.psutil, "disk_io_counters", lambda: None)
    assert set(api_system._io_counters().values()) == {None}
