"""Going back to an earlier AlvaOS version from the update cache."""

import os

import pytest

import update_manager as um


@pytest.fixture
def manager(tmp_path, monkeypatch):
    m = um.UpdateManager.__new__(um.UpdateManager)
    m.repo = "x/y"
    m.state_dir = str(tmp_path)
    m.state_file = str(tmp_path / "update_state.json")
    m.history_file = str(tmp_path / "update_history.json")
    m.settings_file = str(tmp_path / "update_settings.json")
    m.cache_dir = str(tmp_path / "updates")
    m.github_cache_file = str(tmp_path / "github_cache.json")
    os.makedirs(m.cache_dir)
    monkeypatch.setattr(m, "get_current_version", lambda: "0.9.0")

    versions = {}

    def add(name, version, signed=True):
        path = os.path.join(m.cache_dir, name)
        with open(path, "wb") as f:
            f.write(b"deb")
        if signed:
            with open(path + ".sig", "wb") as f:
                f.write(b"sig")
        versions[path] = version

    monkeypatch.setattr(um, "read_deb_version", lambda path: versions.get(path))
    m.add = add
    return m


def test_only_older_signed_packages_are_offered_newest_first(manager):
    manager.add("alvaos_0.8.0.deb", "0.8.0")
    manager.add("alvaos_0.8.2.deb", "0.8.2")
    manager.add("alvaos_0.9.0.deb", "0.9.0")          # the current version
    manager.add("alvaos_0.10.0.deb", "0.10.0")        # newer
    manager.add("alvaos_0.7.0.deb", "0.7.0", signed=False)
    manager.add("broken.deb", None)

    versions = [c["version"] for c in manager.list_rollback_candidates()]

    assert versions == ["0.8.2", "0.8.0"]


def test_no_cache_means_no_candidates(manager):
    os.rmdir(manager.cache_dir)
    assert manager.list_rollback_candidates() == []


def test_rollback_installs_the_cached_package(manager, monkeypatch):
    manager.add("alvaos_0.8.2.deb", "0.8.2")
    installed = []
    monkeypatch.setattr(manager, "apply_alvaos_update",
                        lambda path: installed.append(path) or {"success": True})

    result = manager.rollback_to("0.8.2")

    assert result["success"] is True
    assert "0.8.2" in result["message"]
    assert installed == [os.path.join(manager.cache_dir, "alvaos_0.8.2.deb")]


def test_rollback_refuses_versions_that_are_not_cached(manager, monkeypatch):
    manager.add("alvaos_0.8.2.deb", "0.8.2")
    monkeypatch.setattr(manager, "apply_alvaos_update",
                        lambda path: pytest.fail("must not install"))

    for version in ("0.8.0", "0.10.0", "../../etc/passwd", "", None):
        result = manager.rollback_to(version)
        assert result["success"] is False
        assert "not stored" in result["error"]
