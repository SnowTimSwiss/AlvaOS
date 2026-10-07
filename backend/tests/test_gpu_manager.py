"""Graphics cards and their drivers (backend/gpu_manager.py)."""

import os
import subprocess
import time

import gpu_manager as gm
import priv_policy
from fakes import FakeSystem

PCI_IDS = """# test database
10de  NVIDIA Corporation
\t2484  GA104 [GeForce RTX 3070]
\t\t1043 87b8  a subsystem line
1002  Advanced Micro Devices, Inc. [AMD/ATI]
\t73bf  Navi 21 [Radeon RX 6800/6800 XT / 6900 XT]
8086  Intel Corporation
\t4680  AlderLake-S GT1 [UHD Graphics 770]
"""


def card(sys_root, slot, vendor, device, driver="", render=False, cls="0x030000", boot_vga="0"):
    dev = sys_root / "bus/pci/devices" / slot
    dev.mkdir(parents=True)
    (dev / "class").write_text(cls + "\n")
    (dev / "vendor").write_text(vendor + "\n")
    (dev / "device").write_text(device + "\n")
    (dev / "boot_vga").write_text(boot_vga + "\n")
    if driver:
        target = sys_root / "bus/pci/drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        os.symlink(target, dev / "driver")
    if render:
        (dev / "drm/renderD128").mkdir(parents=True)


def machine(tmp_path):
    sys_root = tmp_path / "sys"
    card(sys_root, "0000:00:02.0", "0x8086", "0x4680", driver="i915", render=True, boot_vga="1")
    card(sys_root, "0000:01:00.0", "0x10de", "0x2484", driver="nouveau", render=True)
    card(sys_root, "0000:03:00.0", "0x1002", "0x73bf")
    card(sys_root, "0000:00:1f.3", "0x8086", "0x7ad0", cls="0x040300")          # sound: not a graphics card
    ids = tmp_path / "pci.ids"
    ids.write_text(PCI_IDS)
    return sys_root, (str(ids),)


def test_cards_are_found_with_their_names_and_drivers(tmp_path):
    sys_root, ids = machine(tmp_path)
    cards = {c["vendor"]: c for c in gm.detect(str(sys_root), ids)}
    assert set(cards) == {"intel", "nvidia", "amd"}
    assert cards["intel"]["model"] == "AlderLake-S GT1 [UHD Graphics 770]" and cards["intel"]["screen"]
    assert cards["intel"]["render_node"] == "/dev/dri/renderD128" and cards["intel"]["driver"] == "i915"
    assert cards["nvidia"]["model"] == "GA104 [GeForce RTX 3070]" and cards["nvidia"]["driver"] == "nouveau"
    assert cards["amd"]["driver"] == "" and cards["amd"]["vendor_name"] == "AMD"
    assert gm.detect(str(sys_root), ("/missing/pci.ids",))[0]["model"].startswith("Graphics card")


def test_each_card_says_what_it_needs(tmp_path):
    sys_root, ids = machine(tmp_path)
    have = {"firmware-misc-nonfree": True, "intel-media-va-driver-non-free": True, "vainfo": True}
    gpu = gm.GpuManager(None, installed=lambda pkgs: {p: have.get(p, False) for p in pkgs},
                        sys_root=str(sys_root), restart_flag=str(tmp_path / "restart"), pci_ids=ids)
    cards = {c["vendor"]: c for c in gpu.status()["cards"]}
    assert cards["intel"]["state"] == "ready" and cards["intel"]["missing"] == []
    assert cards["nvidia"]["state"] == "missing" and "nouveau" in cards["nvidia"]["advice"]
    assert "nvidia-driver" in cards["nvidia"]["missing"]
    assert cards["amd"]["state"] == "missing" and "firmware-amd-graphics" in cards["amd"]["missing"]


def test_installing_runs_apt_with_the_fixed_list_and_asks_for_a_restart(tmp_path):
    sys_root, ids = machine(tmp_path)
    ran = []

    def run(cmd, timeout=30, extra_env=None):
        ran.append((cmd, extra_env))
        return subprocess.CompletedProcess(cmd, 0, "ok", ""), None

    flag = tmp_path / "restart"
    gpu = gm.GpuManager(run, installed=lambda pkgs: {p: False for p in pkgs}, sys_root=str(sys_root),
                        restart_flag=str(flag), pci_ids=ids, available=lambda p: True)
    assert not gpu.install("matrox")[0]
    ok, message = gpu.install("nvidia")
    assert ok and "NVIDIA" in message
    for _ in range(100):
        if not gpu.status()["job"]["running"]:
            break
        time.sleep(0.02)
    assert ran[0][0][1:] == ["update"]
    headers = f"linux-headers-{os.uname().release}"
    assert ran[1][0][1:] == ["-y", "install", "linux-headers-amd64", headers, "nvidia-driver", "firmware-misc-nonfree"]
    assert priv_policy.validate(ran[1][0], FakeSystem()).argv == ran[1][0]   # the helper runs it as is
    assert ran[1][1] == {"DEBIAN_FRONTEND": "noninteractive"}
    assert flag.exists() and gpu.status()["restart_needed"]
    gpu.clear_after_boot(boot_time=time.time() - 3600)          # started before the install: still needed
    assert flag.exists()
    gpu.clear_after_boot(boot_time=time.time() + 5)               # started after it: done
    assert not flag.exists()


def test_missing_headers_for_the_running_kernel_are_explained_not_retried(tmp_path):
    # After a kernel update Debian no longer offers the old kernel's headers.
    sys_root, ids = machine(tmp_path)
    ran = []

    def run(cmd, timeout=30, extra_env=None):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok", ""), None

    flag = tmp_path / "restart"
    gpu = gm.GpuManager(run, installed=lambda pkgs: {p: False for p in pkgs}, sys_root=str(sys_root),
                        restart_flag=str(flag), pci_ids=ids, available=lambda p: False)
    assert gpu.install("nvidia")[0]
    for _ in range(100):
        if not gpu.status()["job"]["running"]:
            break
        time.sleep(0.02)
    job = gpu.status()["job"]
    assert [c[1:] for c in ran] == [["update"]]          # no apt install that cannot work
    assert "Updates page" in job["error"] and "restart the NAS" in job["error"]
    assert not flag.exists()
    assert "Updates page" in gm._apt_problem("E: Unable to locate package linux-headers-6.1.0-9-amd64")


def test_a_card_that_is_not_there_is_not_installed_for(tmp_path):
    sys_root = tmp_path / "sys"
    card(sys_root, "0000:00:02.0", "0x8086", "0x4680", driver="i915", render=True)
    gpu = gm.GpuManager(None, installed=lambda pkgs: {}, sys_root=str(sys_root), pci_ids=())
    assert gpu.install("nvidia") == (False, "There is no NVIDIA graphics card in this NAS.")


def test_apt_failures_are_explained():
    assert "non-free" in gm._apt_problem("E: Unable to locate package nvidia-driver")
    assert "Another installation" in gm._apt_problem("E: Could not get lock /var/lib/dpkg/lock-frontend")
    assert gm._apt_problem("dpkg: error\nE: Sub-process returned an error code (1)").endswith("error code (1)")


def test_secure_boot_is_read_from_efi(tmp_path):
    efivars = tmp_path / "sys/firmware/efi/efivars"
    efivars.mkdir(parents=True)
    var = efivars / "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    var.write_bytes(b"\x06\x00\x00\x00\x01")
    assert gm.secure_boot(str(tmp_path / "sys"))
    var.write_bytes(b"\x06\x00\x00\x00\x00")
    assert not gm.secure_boot(str(tmp_path / "sys"))


def test_every_package_is_checked_against_debian_in_ci():
    listed = open(os.path.join(os.path.dirname(__file__), "../../scripts/ci/optional-packages.txt")).read().split()
    for packages in gm.PACKAGES.values():
        assert set(packages) <= set(listed)
