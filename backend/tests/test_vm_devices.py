"""Virtual machines: a second disk, drivers CD, fast devices, home network,
USB devices, a graphics card and priority (backend/vm_ops.py)."""

import json
import os
import subprocess

import pytest

import vm_ops as vm
from test_vm_ops import GOOD, pool  # noqa: F401 - the fixture

FW = ("/usr/share/OVMF/CODE.fd", "/usr/share/OVMF/VARS.fd")
STORE = "/mnt/alvaos/main/VMs"


def args_of(**change):
    return " ".join(vm.qemu_args(vm.check_config({**GOOD, **change}), STORE, "/run/x", firmware=FW, tap_fd=7))


def test_old_descriptions_get_the_defaults():
    cfg = vm.check_config(GOOD)
    assert (cfg["data_gb"], cfg["iso2"], cfg["fast"], cfg["network"], cfg["usb"], cfg["gpu"], cfg["priority"]) == \
        (0, "", False, "nat", [], "", "normal")


@pytest.mark.parametrize("change, message", [
    ({"iso2": "/etc/x.iso"}, "not a place"),
    ({"iso2": "/mnt/alvaos/main/virtio.img"}, ".iso"),
    ({"usb": [{"vendor": "046D", "product": "c52b"}]}, "USB"),
    ({"usb": [{"vendor": "046d", "product": "c52b"}] * 2}, "8 different"),
    ({"gpu": "01:00.0; rm"}, "graphics card"),
    ({"network": "host"}, "network"),
    ({"priority": "realtime"}, "priority"),
    ({"data_gb": -1}, "second disk"),
])
def test_bad_new_fields_are_refused(change, message):
    with pytest.raises(vm.VmError, match=message):
        vm.check_config({**GOOD, **change})


def test_a_second_disk_and_a_drivers_cd():
    text = args_of(data_gb=200, iso2="/mnt/alvaos/main/VMs/ISOs/virtio-win.iso")
    assert "file=/mnt/alvaos/main/VMs/0a1b2c3d/data.qcow2,if=none,id=disk1" in text
    assert "ide-hd,drive=disk1,bus=ahci.1,bootindex=3" in text
    assert "ide-cd,drive=cd0,bus=ahci1.0,bootindex=1" in text and "ide-cd,drive=cd1,bus=ahci1.1" in text
    assert "virtio-win.iso" in text


def test_windows_gets_fast_devices_once_its_drivers_are_in():
    text = args_of(fast=True)
    assert "virtio-blk-pci,drive=disk0" in text and "virtio-net-pci,netdev=net0" in text and "ich9-ahci,id=ahci " not in text


def test_own_address_at_home_uses_the_tap_and_a_fixed_address():
    text = args_of(network="bridge")
    assert "-netdev tap,id=net0,fd=7" in text and "mac=52:54:00:0a:1b:2c" in text and "hostfwd" not in text


def test_usb_devices_and_a_graphics_card():
    cfg = vm.check_config({**GOOD, "usb": [{"vendor": "046d", "product": "c52b", "name": "Logi<script>"}],
                           "gpu": "0000:01:00.0"})
    assert cfg["usb"][0]["name"] == "Logiscript"
    text = " ".join(vm.qemu_args(cfg, STORE, "/run/x", firmware=FW, vfio=["0000:01:00.0", "0000:01:00.1"]))
    assert "usb-host,bus=xhci.0,vendorid=0x046d,productid=0xc52b" in text
    assert "vfio-pci,host=0000:01:00.0" in text and "vfio-pci,host=0000:01:00.1" in text


# ── The NAS's devices, in a fake /sys and /dev ──────────────────────────────

def sysfs(tmp_path, iommu=True, screen=False):
    sys_root, dev_root = tmp_path / "sys", tmp_path / "dev"
    usb = sys_root / "bus/usb/devices"
    for name, vendor, product, cls, label in (("1-1", "046d", "c52b", "00", "Logitech USB Receiver"),
                                              ("usb1", "1d6b", "0002", "09", "root hub"),
                                              ("1-2", "05e3", "0610", "09", "hub")):
        d = usb / name
        d.mkdir(parents=True)
        for k, v in (("idVendor", vendor), ("idProduct", product), ("bDeviceClass", cls), ("product", label),
                     ("busnum", "1"), ("devnum", "4")):
            (d / k).write_text(v + "\n")
    (dev_root / "bus/usb/001").mkdir(parents=True)
    (dev_root / "bus/usb/001/004").write_text("")
    pci = sys_root / "bus/pci/devices"
    group = sys_root / "kernel/iommu_groups/14/devices"
    group.mkdir(parents=True)
    driver = sys_root / "bus/pci/drivers/nvidia"
    driver.mkdir(parents=True)
    (driver / "unbind").write_text("")
    (sys_root / "bus/pci/drivers_probe").write_text("")
    for slot, cls in (("0000:01:00.0", "0x030000"), ("0000:01:00.1", "0x040300")):
        d = pci / slot
        d.mkdir(parents=True)
        (d / "class").write_text(cls + "\n")
        (d / "vendor").write_text("0x10de\n")
        (d / "boot_vga").write_text("1\n" if screen and cls.startswith("0x03") else "0\n")
        (d / "driver_override").write_text("(null)\n")
        os.symlink(driver, d / "driver")
        if iommu:
            os.symlink(sys_root / "kernel/iommu_groups/14", d / "iommu_group")
            os.symlink(d, group / slot)
    if not iommu:
        (sys_root / "kernel/iommu_groups/14/devices/x").mkdir(parents=True)
        os.rmdir(sys_root / "kernel/iommu_groups/14/devices/x")
        os.rmdir(group)
        os.rmdir(sys_root / "kernel/iommu_groups/14")
    (dev_root / "vfio").mkdir()
    (dev_root / "vfio/14").write_text("")
    return str(sys_root), str(dev_root)


def test_the_page_lists_usb_devices_without_hubs(tmp_path):
    sys_root, _ = sysfs(tmp_path)
    assert vm.usb_devices(sys_root) == [{"vendor": "046d", "product": "c52b", "name": "Logitech USB Receiver",
                                         "bus": "1", "dev": "4"}]


def test_a_graphics_card_says_why_a_machine_cannot_have_it(tmp_path):
    sys_root, _ = sysfs(tmp_path / "a")
    [card] = vm.graphics_cards(sys_root)
    assert card["why_not"] == "" and card["members"] == ["0000:01:00.0", "0000:01:00.1"]
    assert "IOMMU is off" in vm.graphics_cards(sysfs(tmp_path / "b", iommu=False)[0])[0]["why_not"]
    assert "shows its screen" in vm.graphics_cards(sysfs(tmp_path / "c", screen=True)[0])[0]["why_not"]


def test_before_a_start_the_devices_are_handed_over_and_after_it_given_back(tmp_path, monkeypatch):
    sys_root, dev_root = sysfs(tmp_path)
    ran = []
    monkeypatch.setattr(vm.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    written = []
    monkeypatch.setattr(vm, "_write_sys", lambda path, value: written.append((path.replace(sys_root, ""), value)))
    cfg = vm.check_config({**GOOD, "usb": [{"vendor": "046d", "product": "c52b"}], "gpu": "0000:01:00.0"})
    vm.devices_ready(cfg, sys_root, dev_root, uid=os.getuid())
    assert oct(os.stat(os.path.join(dev_root, "bus/usb/001/004")).st_mode & 0o777) == "0o600"
    assert ["/usr/sbin/modprobe", "vfio-pci"] in ran
    assert ("/bus/pci/devices/0000:01:00.0/driver_override", "vfio-pci") in written
    assert ("/bus/pci/devices/0000:01:00.1/driver/unbind", "0000:01:00.1") in written
    written.clear()
    vm.devices_back(cfg, sys_root)
    assert ("/bus/pci/devices/0000:01:00.0/driver_override", "\n") in written
    assert ("/bus/pci/drivers_probe", "0000:01:00.1") in written


def test_the_screen_card_is_never_taken(tmp_path):
    sys_root, dev_root = sysfs(tmp_path, screen=True)
    with pytest.raises(vm.VmError, match="screen"):
        vm.devices_ready(vm.check_config({**GOOD, "gpu": "0000:01:00.0"}), sys_root, dev_root, uid=os.getuid())


def test_own_address_at_home_makes_a_macvtap_port(tmp_path, monkeypatch):
    sys_root, dev_root = sysfs(tmp_path)
    ips = []
    monkeypatch.setattr(vm, "_ip", lambda *a: ips.append(list(a)) or subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(vm, "default_interface", lambda: "enp3s0")
    os.makedirs(os.path.join(sys_root, "class/net/mvt0a1b2c3d"))
    open(os.path.join(sys_root, "class/net/mvt0a1b2c3d/ifindex"), "w").write("9\n")
    open(os.path.join(dev_root, "tap9"), "w").write("")
    cfg = vm.check_config({**GOOD, "network": "bridge"})
    vm.devices_ready(cfg, sys_root, dev_root, uid=os.getuid())
    assert ["link", "add", "link", "enp3s0", "name", "mvt0a1b2c3d", "type", "macvtap", "mode", "bridge"] in ips
    assert ["link", "set", "mvt0a1b2c3d", "address", "52:54:00:0a:1b:2c", "up"] in ips
    vm.devices_back(cfg, sys_root)
    assert ips[-1] == ["link", "delete", "mvt0a1b2c3d"]


def test_the_router_port_is_found(tmp_path):
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\nenp3s0\t0001A8C0\t00000000\nenp3s0\t00000000\t0101A8C0\n")
    assert vm.default_interface(str(route)) == "enp3s0"
    route.write_text("Iface\tDestination\n")
    with pytest.raises(vm.VmError):
        vm.default_interface(str(route))


def test_a_second_disk_is_made_later_and_grows(pool, monkeypatch):  # noqa: F811
    root, store, state, configs, made = pool
    (store / "0a1b2c3d").mkdir()
    (store / "0a1b2c3d" / "disk.qcow2").write_text("x")
    cfg = json.loads(open(os.path.join(configs, "0a1b2c3d.json")).read())
    open(os.path.join(configs, "0a1b2c3d.json"), "w").write(json.dumps({**cfg, "data_gb": 500}))
    owned = []
    vm.grow("0a1b2c3d", state, configs, str(root), is_active=lambda i: False, size_of=lambda p: 80 * 2**30,
            chown=owned.append)
    assert made[-1][:4] == [vm.QEMU_IMG, "create", "-f", "qcow2"] and made[-1][4].endswith("data.qcow2")
    assert made[-1][5] == "500G" and owned == [made[-1][4]]
