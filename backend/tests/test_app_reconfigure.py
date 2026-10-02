"""Changing the folders and ports of an installed app (backend/app_store.py)."""

import threading

import pytest

import app_store as store_module
from app_store import normalize_port_mappings

DETAILS = {"name": "Jellyfin", "version": "10.9", "docker_compose": {"services": {"jellyfin": {
    "image": "jellyfin/jellyfin:10.9",
    "ports": ["8096:8096"],
    "volumes": ["${POOL_PATH}/config:/config", "${POOL_PATH}/media:/media"],
}}}}


class FakeDocker:
    def __init__(self):
        self.calls = []

    def update_container_from_compose(self, compose_dict, app_name, pool_path, project_name=None,
                                      callback=None, pull=True):
        self.calls.append({"compose": compose_dict, "pull": pull, "pool_path": pool_path})
        return True, None


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = object.__new__(store_module.AppStore)
    s.docker_manager = FakeDocker()
    s.apps_state_file = str(tmp_path / "apps_state.json")
    s.install_status_file = str(tmp_path / "status.json")
    s.update_cache_file = str(tmp_path / "cache.json")
    s.update_cache_ttl_seconds = 1800
    s._status_lock = threading.RLock()
    s._update_cache_lock = threading.RLock()
    s._active_install_status = None
    s._last_status_write = 0.0
    storage = tmp_path / "apps" / "jellyfin"
    storage.mkdir(parents=True)
    s._save_apps_state({"jellyfin": {
        "app_id": "jellyfin", "name": "Jellyfin", "source": "catalog", "installed_version": "10.9",
        "storage_path": str(storage), "pool_path": "/mnt/alvaos/main",
        "port_mappings": {}, "volume_mappings": {}, "environment_vars": {"TZ": "Europe/Zurich"},
    }})
    monkeypatch.setattr(s, "get_app_details", lambda app_id: (dict(DETAILS), None))
    monkeypatch.setattr(s, "_infer_environment_vars_from_running_containers", lambda *a: {})
    # Run the worker right away instead of in a thread.
    class Inline:
        def __init__(self, target, args=(), kwargs=None):
            self.target, self.args, self.kwargs = target, args, kwargs or {}
            self.daemon = True

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(threading, "Thread", Inline)
    return s


def test_new_folders_and_ports_recreate_the_app_without_pulling(store):
    ok, error = store.reconfigure_app("jellyfin", {8096: 8100}, {"/media": "/mnt/alvaos/main/Media"})
    assert ok, error
    call = store.docker_manager.calls[-1]
    assert call["pull"] is False
    service = call["compose"]["services"]["jellyfin"]
    assert service["ports"] == ["8100:8096"]
    assert "/mnt/alvaos/main/Media:/media" in service["volumes"]
    assert service["environment"]["TZ"] == "Europe/Zurich"
    state = store._load_apps_state()["jellyfin"]
    assert {int(k): v for k, v in state["port_mappings"].items()} == {8096: 8100}
    assert state["volume_mappings"] == {"/media": "/mnt/alvaos/main/Media"}
    assert store.get_install_status()["action"] == "reconfigure"
    assert store.get_install_status()["status"] == "success"


def test_an_outdated_app_is_updated_first(store):
    state = store._load_apps_state()
    state["jellyfin"]["installed_version"] = "10.8"
    store._save_apps_state(state)
    ok, error = store.reconfigure_app("jellyfin", {}, {})
    assert not ok and "Update the app first" in error
    assert store.docker_manager.calls == []


def test_custom_and_unknown_apps_are_refused(store):
    assert store.reconfigure_app("nope", {}, {})[0] is False
    state = store._load_apps_state()
    state["jellyfin"]["source"] = "custom_compose"
    store._save_apps_state(state)
    assert store.reconfigure_app("jellyfin", {}, {})[0] is False


def test_port_mappings_are_numbers():
    assert normalize_port_mappings({"8096": "8100"}) == ({8096: 8100}, None)
    assert normalize_port_mappings(None) == ({}, None)
    assert normalize_port_mappings({"a": 1})[0] is None
    assert normalize_port_mappings({"80": 0})[0] is None
    assert normalize_port_mappings(["80"])[0] is None
