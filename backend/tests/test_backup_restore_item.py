"""Single files from a restore point (backend/backup_manager.py)."""

import os
import subprocess

import pytest

import backup_manager as bm
import priv_policy as p
from fakes import FakeSystem

SOURCE = "/mnt/alvaos/main/media"
SNAP = "/mnt/alvaos/main/.alvaos-snapshots/media/20261001-030000-scheduled"


def record(kind, name, size=10, mtime=1759287600.0):
    return f"{kind}\t{size}\t{mtime}\t{name}\0"


class FakeFiles:
    """Directories as {path: {name: kind}}; runs find and cp through the policy."""

    def __init__(self, tree):
        self.tree = tree
        self.calls = []
        self.system = FakeSystem()

    def __call__(self, cmd, timeout=30, extra_env=None, input=None):
        self.calls.append(cmd)
        p.validate(cmd, self.system)
        name = os.path.basename(cmd[0])
        if name == "find":
            folder = self.tree.get(cmd[1])
            if folder is None:
                return subprocess.CompletedProcess(cmd, 1, "", "No such file"), "No such file"
            out = "".join(record(kind, n) for n, kind in folder.items())
            return subprocess.CompletedProcess(cmd, 0, out, ""), None
        if name == "cp":
            src, dst = cmd[-2], cmd[-1]
            parent, entry = os.path.split(dst)
            src_parent, src_name = os.path.split(src)
            self.tree.setdefault(parent, {})[entry] = self.tree[src_parent][src_name]
            return subprocess.CompletedProcess(cmd, 0, "", ""), None
        raise AssertionError(cmd)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    files = FakeFiles({
        SNAP: {"Holidays": "d", "notes.txt": "f"},
        f"{SNAP}/Holidays": {"beach.jpg": "f", "dinner.jpg": "f"},
        SOURCE: {"Holidays": "d"},
        f"{SOURCE}/Holidays": {"dinner.jpg": "f"},
    })
    monkeypatch.setattr(bm.BackupManager, "_resolve_state_dir", lambda self: str(tmp_path))
    monkeypatch.setattr(bm.BackupManager, "_schedule_loop", lambda self: None)
    manager = bm.BackupManager(files, lambda: {})
    manager._save_snapshots([{"snapshot_path": SNAP, "source_path": SOURCE, "snapshot_class": "data",
                              "created_at": "2026-10-01T03:00:00+00:00"}])
    return manager, files


def test_a_folder_shows_what_is_gone_since(setup):
    manager, _ = setup
    ok, result = manager.browse_snapshot(SNAP, "Holidays")
    assert ok, result
    assert [(e["name"], e["exists_now"]) for e in result["entries"]] == [("beach.jpg", False), ("dinner.jpg", True)]
    ok, top = manager.browse_snapshot(SNAP, "")
    assert [e["name"] for e in top["entries"]] == ["Holidays", "notes.txt"]   # folders first
    assert top["entries"][0]["type"] == "folder"


def test_a_folder_that_exists_comes_back_beside_it(setup):
    manager, files = setup
    ok, result = manager.restore_item(SNAP, "Holidays")
    assert ok and result["restored_as"].startswith("Holidays (restored 2026-10-01")


def test_a_deleted_file_comes_back_under_its_name(setup):
    manager, files = setup
    ok, result = manager.restore_item(SNAP, "Holidays/beach.jpg")
    assert ok and result == {"restored_as": "Holidays/beach.jpg", "renamed": False}
    assert "beach.jpg" in files.tree[f"{SOURCE}/Holidays"]
    assert files.calls[-1][1:5] == ["-a", "--reflink=auto", "--no-clobber", "--"]


def test_an_existing_file_is_never_overwritten(setup):
    manager, files = setup
    ok, result = manager.restore_item(SNAP, "Holidays/dinner.jpg")
    assert ok and result["renamed"]
    assert result["restored_as"].startswith("Holidays/dinner (restored 2026-10-01 ")
    assert result["restored_as"].endswith(").jpg")


def test_unknown_snapshots_and_escapes_are_refused(setup):
    manager, _ = setup
    assert manager.browse_snapshot("/mnt/alvaos/main/media", "")[0] is False
    assert manager.browse_snapshot(SNAP, "../../etc")[0] is False
    assert manager.restore_item(SNAP, "")[0] is False
    ok, result = manager.restore_item(SNAP, "Holidays/never.jpg")
    assert not ok and "not in this restore point" in result["error"]


def test_the_parent_folder_must_still_exist(setup):
    manager, files = setup
    del files.tree[f"{SOURCE}/Holidays"]
    ok, result = manager.restore_item(SNAP, "Holidays/beach.jpg")
    assert not ok and "Restore that folder instead" in result["error"]


def test_names_and_listing_parser():
    assert bm.restored_name("a.tar.gz", __import__("datetime").datetime(2026, 1, 2, 3, 4)) == "a.tar (restored 2026-01-02 0304).gz"
    assert bm.restored_name(".bashrc", __import__("datetime").datetime(2026, 1, 2)).startswith(".bashrc (restored")
    assert bm.restored_name("v1.2", __import__("datetime").datetime(2026, 1, 2), is_folder=True) == "v1.2 (restored 2026-01-02 0000)"
    assert bm.clean_relative_path("/a//b/./c/") == "a/b/c"
    assert bm.restored_name("v1.2", __import__("datetime").datetime(2026, 1, 2), is_folder=True) == "v1.2 (restored 2026-01-02 0000)"
    assert bm.clean_relative_path("a/../b") is None
    entries = bm.parse_find_listing(record("f", "x\ty.txt") + "garbage\0" + record("d", "z"))
    assert [e["name"] for e in entries] == ["z", "x\ty.txt"]
    assert bm.FIND_LIST_FORMAT == p.FIND_LIST_FORMAT
