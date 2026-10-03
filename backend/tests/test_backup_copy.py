"""A second copy of the restore points on a backup disk (backend/backup_copy.py)."""

import io
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import backup_copy as bc


class FakeBackup:
    def __init__(self, entries):
        self.entries = entries
        self.snapshots_file = "unused"

    def list_snapshots(self, source_path=None, snapshot_class=None):
        out = [e for e in self.entries if (e.get("snapshot_class") or "data") == snapshot_class]
        return sorted(out, key=lambda e: e["created_at"], reverse=True)

    def _append_snapshot(self, entry):
        self.entries.append(entry)

    def _load_json(self, path, default):
        return list(self.entries)

    def _save_snapshots(self, entries):
        self.entries[:] = entries


class Proc:
    def __init__(self, code=0, err=b""):
        self.returncode = code
        self.stdout = io.BytesIO(b"stream")
        self._err = err

    def communicate(self, timeout=None):
        return b"", self._err


def entry(source, name, day):
    return {"id": name, "source_path": source, "snapshot_path": f"/mnt/alvaos/main/.alvaos-snapshots/x/{name}",
            "snapshot_name": name, "created_at": f"2026-10-{day:02d}T03:00:00+00:00", "snapshot_class": "data"}


@pytest.fixture
def setup(tmp_path):
    pools = {"main": {"name": "main", "mount_point": "/mnt/alvaos/main"},
             "usb-uuid-0000000000000000000000": {"name": "usb-backup", "mount_point": str(tmp_path / "usb")}}
    backup = FakeBackup([entry("/mnt/alvaos/main/Family", "20261001-030000-auto", 1),
                         entry("/mnt/alvaos/main/Family", "20261002-030000-auto", 2)])
    ran, pipes = [], []
    (tmp_path / "usb").mkdir()

    def run(cmd, timeout=30):
        ran.append(cmd)
        if cmd[1:2] == ["-p"]:
            os.makedirs(cmd[2], exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, "", ""), None

    def popen(cmd, **kw):
        pipes.append(cmd)
        if cmd[-2] == "receive":                      # the received copy appears
            last_send = pipes[-2]
            os.makedirs(os.path.join(cmd[-1], os.path.basename(last_send[-1])), exist_ok=True)
        return Proc()

    copier = bc.BackupCopier(backup, run, lambda: pools, popen=popen, build_cmd=lambda c: ["sudo"] + c,
                             settings_path=str(tmp_path / "copy.json"), device_present=lambda uuid: True,
                             is_mounted=lambda path: True)
    return copier, backup, ran, pipes, tmp_path


def test_the_backed_up_pool_cannot_be_the_backup_disk(setup):
    copier, *_ = setup
    assert copier.configure({"pool_id": "main"})[1].startswith("This pool holds")
    assert copier.configure({"pool_id": "nope"})[1] == "Choose the backup disk."
    settings, problem = copier.configure({"pool_id": "usb-uuid-0000000000000000000000"})
    assert problem == "" and settings["enabled"] and settings["enabled_at"]


def test_first_copy_is_full_then_incremental(setup):
    copier, backup, ran, pipes, tmp = setup
    copier.configure({"pool_id": "usb-uuid-0000000000000000000000"})
    assert copier.needs_copy()
    out = copier.copy_now()
    assert out == {"copied": ["/mnt/alvaos/main/Family"], "error": ""}
    target = str(tmp / "usb" / ".alvaos-copies" / "main__Family")
    assert pipes[0][1:] == [bc.BTRFS, "send", "/mnt/alvaos/main/.alvaos-snapshots/x/20261002-030000-auto"]
    assert pipes[1][1:] == [bc.BTRFS, "receive", target]
    copy = [e for e in backup.entries if e["snapshot_class"] == "copy"]
    assert copy[0]["snapshot_path"] == os.path.join(target, "20261002-030000-auto")
    assert not copier.needs_copy()
    backup.entries.append(entry("/mnt/alvaos/main/Family", "20261003-030000-auto", 3))
    pipes.clear()
    copier.copy_now()
    assert pipes[0][1:] == [bc.BTRFS, "send", "-p", "/mnt/alvaos/main/.alvaos-snapshots/x/20261002-030000-auto",
                            "/mnt/alvaos/main/.alvaos-snapshots/x/20261003-030000-auto"]
    assert copier.settings()["last_copy_at"]


def test_a_failed_copy_leaves_no_half_copy(setup):
    copier, backup, ran, pipes, tmp = setup
    copier.configure({"pool_id": "usb-uuid-0000000000000000000000"})
    copier.popen = lambda cmd, **kw: Proc(code=1, err=b"No space left on device") if cmd[-2] == "receive" else Proc()
    out = copier.copy_now()
    assert "No space left" in out["error"]
    assert ran[-1][1:3] == ["subvolume", "delete"] and ran[-1][3].endswith("20261002-030000-auto")
    assert not [e for e in backup.entries if e["snapshot_class"] == "copy"]


def test_an_unplugged_disk_is_said_plainly(setup):
    copier, *_ = setup
    copier.configure({"pool_id": "usb-uuid-0000000000000000000000"})
    copier.device_present = lambda uuid: False
    assert copier.copy_now()["error"] == "The backup disk is not connected."
    assert copier.needs_copy() is False


def test_old_copies_are_thinned_out(setup):
    copier, backup, ran, pipes, tmp = setup
    target = tmp / "usb" / ".alvaos-copies" / "main__Family"
    for i in range(bc.KEEP_COPIES + 2):
        (target / f"202609{i:02d}-030000-auto").mkdir(parents=True)
    copier._thin_out("/mnt/alvaos/main/Family", str(target))
    deleted = [c[3] for c in ran if c[1:3] == ["subvolume", "delete"]]
    assert [os.path.basename(d) for d in deleted] == ["20260900-030000-auto", "20260901-030000-auto"]


def test_a_week_without_a_copy_is_noticed(setup):
    copier, *_ = setup
    copier.configure({"pool_id": "usb-uuid-0000000000000000000000"})
    assert copier.status()["stale"] is False
    settings = copier.settings()
    settings["enabled_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    bc.save_settings(settings, copier.settings_path)
    assert copier.status()["stale"] is True


def test_slug_names_a_folder_per_source():
    assert bc.slug("/mnt/alvaos/main/Family") == "main__Family"
    assert bc.slug("/mnt/alvaos/main/a b") == "main__a_b"


def test_a_safely_removed_disk_is_not_mounted_again_until_unplugged(setup):
    copier, backup, ran, pipes, tmp_path = setup
    copier.configure({"pool_id": "usb-uuid-0000000000000000000000"})
    assert copier.needs_copy()
    assert copier.eject()[0] and copier.ejected
    assert not copier.needs_copy()                      # still plugged in: left alone
    copier.device_present = lambda uuid: False          # unplugged ...
    assert not copier.needs_copy() and not copier.ejected
    copier.device_present = lambda uuid: True           # ... and back
    assert copier.needs_copy()


def test_folders_with_the_same_snapshot_name_are_copied_each(setup):
    copier, backup, ran, pipes, tmp_path = setup
    copier.configure({"pool_id": "usb-uuid-0000000000000000000000"})
    backup.entries.append(entry("/mnt/alvaos/main/Photos", "20261002-030000-auto", 2))
    backup.entries.append({**entry("/mnt/alvaos/main/Family", "20261002-030000-auto", 2),
                           "id": "c", "snapshot_class": "copy"})
    assert copier.needs_copy()                          # Photos is not on the disk yet


def test_the_disk_is_not_removed_during_a_copy(setup):
    copier, *_ = setup
    copier.configure({"pool_id": "usb-uuid-0000000000000000000000"})
    with bc._lock:
        ok, message = copier.eject()
    assert not ok and message.startswith("A copy is running")
    assert copier.eject()[0]
