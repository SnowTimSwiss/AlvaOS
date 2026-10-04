"""Watchdog: restart services that should run, leave switched-off ones alone."""

import pytest

import watchdog_manager as wm


@pytest.fixture
def watchdog(tmp_path, monkeypatch):
    monkeypatch.setattr(wm, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(wm, "LOG_FILE", str(tmp_path / "watchdog.log"))
    monkeypatch.setattr(wm, "WATCHED_SERVICES", [
        {"name": "smbd", "label": "Samba"},
        {"name": "nfs-kernel-server", "label": "NFS"},
        {"name": "docker", "label": "Docker"},
    ])
    m = wm.WatchdogManager()
    active = {"smbd": False, "nfs-kernel-server": False, "docker": True}
    enabled = {"smbd": True, "nfs-kernel-server": False, "docker": True}
    restarted = []

    def restart(name):
        restarted.append(name)
        active[name] = True
        return True, None

    monkeypatch.setattr(m, "_is_service_active", lambda name: active[name])
    monkeypatch.setattr(m, "_is_service_enabled", lambda name: enabled[name])
    monkeypatch.setattr(m, "_restart_service", restart)
    m.restarted = restarted
    return m


def test_only_enabled_services_that_stopped_are_restarted(watchdog):
    result = watchdog.run_check()

    assert watchdog.restarted == ["smbd"]
    by_name = {s["name"]: s for s in result["services"]}
    assert by_name["smbd"]["recovered"] is True
    assert by_name["nfs-kernel-server"]["enabled"] is False
    assert by_name["nfs-kernel-server"]["recovered"] is False
    assert [r["service"] for r in watchdog.get_status().get("recoveries", [])] == ["smbd"]
