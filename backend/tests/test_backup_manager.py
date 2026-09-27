"""Behaviour of local snapshots, restores and system rollback (backend/backup_manager.py).

Btrfs is simulated by FakeBtrfsRunner, which also runs every command through
the privilege policy: a command the helper would deny fails the test.
"""

import os
import platform

import pytest

import backup_manager as bm_module
from fakes import FakeBtrfsRunner, FakeSystem

pytestmark = pytest.mark.skipif(platform.system() != "Linux", reason="exercises the Linux code paths")

POOL = "/mnt/alvaos/main"
SOURCE = f"{POOL}/media"


@pytest.fixture
def env(tmp_path, monkeypatch):
    # The state dir is /var/lib/alvaos on a NAS; map the test's tmp dir onto it.
    runner = FakeBtrfsRunner(subvolumes={POOL, SOURCE},
                             system=FakeSystem(links={str(tmp_path): "/var/lib/alvaos/test"}))
    monkeypatch.setattr(bm_module.BackupManager, "_resolve_state_dir", lambda self: str(tmp_path))
    monkeypatch.setattr(bm_module.BackupManager, "_schedule_loop", lambda self: None)
    monkeypatch.setattr(bm_module.BackupManager, "_path_on_system_disk", lambda self, p: False)
    monkeypatch.setattr(bm_module.BackupManager, "_path_on_btrfs", lambda self, p: True)
    monkeypatch.setattr(bm_module.BackupManager, "_same_filesystem", lambda self, a, b: True)

    real_exists = os.path.exists

    def exists(path):
        norm = os.path.normpath(str(path))
        if norm.startswith("/mnt/alvaos") or norm.startswith("/.alvaos"):
            return runner.exists(norm)
        return real_exists(path)

    monkeypatch.setattr(bm_module.os.path, "exists", exists)
    manager = bm_module.BackupManager(runner, lambda: {"main": {"mount_point": POOL}})
    return manager, runner


def test_snapshot_of_a_subvolume(env):
    manager, runner = env
    ok, entry = manager.create_snapshot(SOURCE, label="nightly")
    assert ok, entry
    assert entry["snapshot_path"] in runner.subvolumes
    assert entry["snapshot_path"].startswith(POOL + "/")
    assert entry in manager.list_snapshots(source_path=SOURCE)
    assert runner.denied == []


def test_snapshot_commands_are_read_only_snapshots(env):
    manager, runner = env
    manager.create_snapshot(SOURCE)
    snapshots = [c for c in runner.calls if c[1:3] == ["subvolume", "snapshot"]]
    assert snapshots and all("-r" in c for c in snapshots)


def test_pool_root_cannot_be_snapshotted(env):
    manager, _ = env
    ok, payload = manager.create_snapshot(POOL)
    assert not ok and "Pool root" in payload["error"]


def test_non_subvolume_source_is_rejected(env):
    manager, runner = env
    runner.dirs.add(f"{POOL}/plain-dir")
    ok, payload = manager.create_snapshot(f"{POOL}/plain-dir")
    assert not ok and "not a Btrfs subvolume" in payload["error"]


def test_cross_filesystem_snapshot_uses_send_receive_without_a_shell(env, monkeypatch):
    manager, runner = env
    monkeypatch.setattr(bm_module.BackupManager, "_same_filesystem", lambda self, a, b: False)
    ok, entry = manager.create_snapshot(SOURCE, target_path="/mnt/alvaos/backup-disk")
    assert ok, entry
    receive = [c for c in runner.calls if len(c) > 1 and c[1] == "receive"]
    assert receive and receive[0][2] == "-f"
    assert not any(os.path.basename(c[0]) in {"bash", "sh"} for c in runner.calls)
    # the temporary source snapshot is cleaned up again
    assert not any(".alvaos-send-" in s and s.startswith(SOURCE) for s in runner.subvolumes)
    assert runner.denied == []


def test_restore_keeps_the_current_data_aside(env):
    manager, runner = env
    ok, entry = manager.create_snapshot(SOURCE)
    assert ok
    ok, result = manager.restore_snapshot(entry["snapshot_path"])
    assert ok, result
    assert result["restored_to"] == SOURCE
    assert result["previous_backup"] in runner.subvolumes   # old data is not deleted
    assert SOURCE in runner.subvolumes
    assert runner.denied == []


def test_failed_restore_puts_the_original_back(env):
    manager, runner = env
    ok, entry = manager.create_snapshot(SOURCE)
    runner.fail.add(f"snapshot {entry['snapshot_path']} {SOURCE}")
    ok, result = manager.restore_snapshot(entry["snapshot_path"])
    assert not ok
    assert SOURCE in runner.subvolumes
    assert not any(".pre-restore-" in s for s in runner.subvolumes)


def test_restore_refuses_pool_roots_and_system(env):
    manager, _ = env
    ok, payload = manager.restore_snapshot(f"{POOL}/.snapshots/x", source_path=POOL)
    assert not ok and "pool root" in payload["error"].lower()
    ok, payload = manager.restore_snapshot(f"{POOL}/.snapshots/x", source_path="/")
    assert not ok and "system rollback" in payload["error"]


def test_missing_snapshot_is_reported(env):
    manager, _ = env
    ok, payload = manager.restore_snapshot(f"{POOL}/.snapshots/does-not-exist", source_path=SOURCE)
    assert not ok and "not found" in payload["error"]


def test_delete_snapshot_only_removes_that_snapshot(env):
    manager, runner = env
    ok, first = manager.create_snapshot(SOURCE, label="a")
    ok, second = manager.create_snapshot(SOURCE, label="b")
    ok, payload = manager.delete_snapshot(first["snapshot_path"])
    assert ok, payload
    assert first["snapshot_path"] not in runner.subvolumes
    assert second["snapshot_path"] in runner.subvolumes
    assert SOURCE in runner.subvolumes
    assert runner.denied == []
