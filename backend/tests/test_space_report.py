"""What uses the space on a pool (backend/space_report.py)."""

import os
import subprocess

import priv_policy as p
import space_report as sr
from fakes import FakeSystem

MOUNT = "/mnt/alvaos/main"
SNAP = f"{MOUNT}/.alvaos-snapshots/Photos/20261001-030000-scheduled"

DU_OUT = """     Total   Exclusive  Set shared  Filename
5000000000  5000000000           0  /mnt/alvaos/main/Media
 800000000   800000000           0  /mnt/alvaos/main/My Photos
 300000000   300000000           0  /mnt/alvaos/main/apps/jellyfin
 790000000    12000000   778000000  /mnt/alvaos/main/.alvaos-snapshots/Photos/20261001-030000-scheduled
"""


def listing(*entries):
    return "".join(f"{kind}\t{size}\t1759287600.0\t{name}\0" for kind, size, name in entries)


class Runner:
    def __init__(self):
        self.calls = []
        self.system = FakeSystem()

    def __call__(self, cmd, timeout=30, extra_env=None, input=None):
        self.calls.append(cmd)
        p.validate(cmd, self.system)
        if os.path.basename(cmd[0]) == "find":
            out = {MOUNT: listing(("d", 0, "Media"), ("d", 0, "My Photos"), ("d", 0, "apps"),
                                  ("d", 0, ".alvaos-snapshots"), ("f", 1234, "readme.txt")),
                   f"{MOUNT}/apps": listing(("d", 0, "jellyfin"))}[cmd[1]]
            return subprocess.CompletedProcess(cmd, 0, out, ""), None
        return subprocess.CompletedProcess(cmd, 0, DU_OUT, ""), None


def test_du_output_is_read_with_spaces_in_names():
    sizes = sr.parse_fs_du(DU_OUT)
    assert sizes["/mnt/alvaos/main/My Photos"]["total"] == 800000000
    assert sizes[SNAP]["exclusive"] == 12000000
    assert sr.parse_fs_du("ERROR: cannot check space\n") == {}


def test_a_scan_sorts_folders_apps_and_restore_points():
    runner = Runner()
    scanner = sr.SpaceScanner(runner, lambda: [
        {"snapshot_path": SNAP, "source_path": f"{MOUNT}/Photos", "created_at": "2026-10-01T03:00:00+00:00"},
        {"snapshot_path": "/mnt/alvaos/other/.alvaos-snapshots/x/y", "source_path": "/mnt/alvaos/other/x"},
    ])
    assert scanner.report("u1") == {"state": "never"}
    ok, _ = scanner.start("u1", MOUNT, background=False)
    report = scanner.report("u1")
    assert ok and report["state"] == "done", report
    result = report["result"]
    assert [f["name"] for f in result["folders"]] == ["Media", "My Photos"]
    assert result["apps"] == [{"name": "jellyfin", "path": f"{MOUNT}/apps/jellyfin", "bytes": 300000000}]
    assert result["restore_points"]["count"] == 1
    assert result["restore_points"]["exclusive_bytes"] == 12000000
    assert result["loose_files_bytes"] == 1234
    du = [c for c in runner.calls if c[1:3] == ["filesystem", "du"]]
    assert du and all("/mnt/alvaos/other" not in " ".join(c) for c in du)


def test_only_one_scan_at_a_time_and_errors_keep_the_last_result():
    runner = Runner()
    scanner = sr.SpaceScanner(runner, lambda: [])
    scanner.start("u1", MOUNT, background=False)
    scanner._running = "u2"
    assert scanner.start("u1", MOUNT, background=False)[0] is False
    assert scanner.report("u2")["state"] == "running"
    scanner._running = None
    scanner.start("u1", "/mnt/alvaos/gone", background=False)   # find has no listing for it
    report = scanner.report("u1")
    assert report["state"] == "error" and report["result"]["folders"]


def test_du_is_limited_to_data_directories():
    sys_ = FakeSystem()
    p.validate(["/usr/bin/btrfs", "filesystem", "du", "-s", "--raw", f"{MOUNT}/Media"], sys_)
    for argv in (["/usr/bin/btrfs", "filesystem", "du", "-s", "--raw", "/etc"],
                 ["/usr/bin/btrfs", "filesystem", "du", f"{MOUNT}/Media"],
                 ["/usr/bin/btrfs", "filesystem", "du", "-s", "--raw"],
                 ["/usr/bin/btrfs", "filesystem", "du", "-s", "--raw"] + [f"{MOUNT}/x{i}" for i in range(65)]):
        try:
            p.validate(argv, sys_)
        except p.PolicyError:
            continue
        raise AssertionError(argv)
