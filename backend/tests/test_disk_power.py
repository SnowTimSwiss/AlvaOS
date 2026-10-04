"""Letting hard disks sleep (backend/disk_power.py)."""

import subprocess

import pytest

import disk_power as dp
import priv_policy as p
from fakes import FakeSystem

DISKS = [
    {"path": "/dev/sda", "rotational": True, "is_system_disk": True},
    {"path": "/dev/sdb", "rotational": True, "is_system_disk": False},
    {"path": "/dev/sdc", "rotational": True, "is_system_disk": False, "is_removable": True},
    {"path": "/dev/nvme0n1", "rotational": False, "is_system_disk": False},
    {"path": "/dev/sdd", "rotational": True, "is_system_disk": False},
]


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, timeout=30):
        self.calls.append(cmd)
        p.validate(cmd, FakeSystem())
        return subprocess.CompletedProcess(cmd, 0, "", ""), None


def test_only_spinning_data_disks_are_told_to_sleep():
    runner = Runner()
    results = dp.apply(20, DISKS, runner)
    assert [r["path"] for r in results] == ["/dev/sdb", "/dev/sdd"]
    assert runner.calls[0] == ["/usr/sbin/hdparm", "-S", "240", "/dev/sdb"]
    assert all(r["ok"] for r in results)


@pytest.mark.parametrize("minutes,value", [(0, "0"), (10, "120"), (30, "241"), (60, "242")])
def test_minutes_map_to_hdparm_values(minutes, value):
    runner = Runner()
    dp.apply(minutes, DISKS, runner)
    assert runner.calls[0][2] == value


def test_settings_only_take_known_values(tmp_path):
    path = str(tmp_path / "power.json")
    assert dp.load_settings(path) == {"spindown_minutes": 0}
    dp.save_settings(30, path)
    assert dp.load_settings(path) == {"spindown_minutes": 30}
    with pytest.raises(ValueError):
        dp.save_settings(7, path)
    (tmp_path / "power.json").write_text('{"spindown_minutes": 5}')
    assert dp.load_settings(path) == {"spindown_minutes": 0}


@pytest.mark.parametrize("argv", [
    ["/usr/sbin/hdparm", "-S", "1", "/dev/sdb"],
    ["/usr/sbin/hdparm", "--security-erase", "x", "/dev/sdb"],
    ["/usr/sbin/hdparm", "-W", "0", "/dev/sdb"],
    ["/usr/sbin/hdparm", "-S", "240", "/dev/sda"],          # the system disk
    ["/usr/sbin/hdparm", "-S", "240", "/etc/passwd"],
])
def test_hdparm_can_only_set_the_sleep_timer_of_a_data_disk(argv):
    with pytest.raises(p.PolicyError):
        p.validate(argv, FakeSystem())
