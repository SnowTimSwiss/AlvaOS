"""Virtual machines, the system side (backend/vm_ops.py)."""

import json
import os
import socket
import threading

import pytest

import vm_ops as vm

GOOD = {"id": "0a1b2c3d", "name": "Windows  PC", "os": "windows11", "cpus": 4, "memory_mb": 8192, "disk_gb": 80,
        "iso": "/mnt/alvaos/main/VMs/ISOs/win11.iso", "autostart": True, "slot": 3,
        "ports": [{"proto": "tcp", "host": 3389, "guest": 3389}]}


def test_a_good_description_is_cleaned_up():
    cfg = vm.check_config(GOOD)
    assert cfg["name"] == "Windows PC" and cfg["slot"] == 3 and cfg["autostart"] is True
    assert vm.check_config({**GOOD, "iso": "", "ports": []})["iso"] == ""


@pytest.mark.parametrize("change, message", [
    ({"id": "../etc"}, "not a virtual machine"),
    ({"os": "macos"}, "what will run"),
    ({"name": "  "}, "name"),
    ({"cpus": 0}, "processor"),
    ({"cpus": "many"}, "number"),
    ({"memory_mb": 10}, "memory"),
    ({"disk_gb": 99999}, "disk"),
    ({"slot": 100}, "screen"),
    ({"iso": "/etc/passwd"}, "not a place"),
    ({"iso": "/mnt/alvaos/../etc/x.iso"}, "not a place"),
    ({"iso": "/mnt/alvaos/main/x.img"}, ".iso"),
    ({"iso": "/mnt/alvaos"}, "not a place"),
    ({"ports": [{"proto": "tcp", "host": 8080, "guest": 80}]}, "used by the NAS"),
    ({"ports": [{"proto": "tcp", "host": 5900, "guest": 22}]}, "used by the NAS"),
    ({"ports": [{"proto": "tcp", "host": 80, "guest": 80}]}, "between 1024"),
    ({"ports": [{"proto": "icmp", "host": 3000, "guest": 1}]}, "TCP or UDP"),
    ({"ports": [{"proto": "tcp", "host": 3000, "guest": 1}] * 2}, "once"),
])
def test_bad_descriptions_are_refused(change, message):
    with pytest.raises(vm.VmError, match=message):
        vm.check_config({**GOOD, **change})


def test_the_command_line_is_built_here_and_nothing_else_gets_in():
    fw = ("/usr/share/OVMF/CODE.fd", "/usr/share/OVMF/VARS.fd")
    args = vm.qemu_args(vm.check_config(GOOD), "/mnt/alvaos/main/VMs", "/run/x", firmware=fw)
    text = " ".join(args)
    assert args[0] == vm.QEMU and "accel=kvm,smm=on" in text and "-smp 4" in text and "-m 8192" in text
    assert "file=/mnt/alvaos/main/VMs/0a1b2c3d/disk.qcow2" in text
    assert "if=pflash,format=raw,unit=1,file=/mnt/alvaos/main/VMs/0a1b2c3d/OVMF_VARS.fd" in text
    assert "ide-hd" in text and "e1000e" in text and "base=localtime" in text          # Windows: no extra drivers
    assert "hostfwd=tcp::3389-:3389" in text and "-vnc 127.0.0.1:3,websocket=5703" in text
    assert "tpm-tis" in text and "ide-cd" in text and "win11.iso" in text
    assert "-qmp unix:/run/x/0a1b2c3d.qmp,server=on,wait=off" in text
    linux = vm.qemu_args(vm.check_config({**GOOD, "os": "linux", "iso": "", "ports": []}), "/mnt/alvaos/main/VMs",
                         firmware=fw, kvm=False)
    t = " ".join(linux)
    assert "virtio-blk-pci" in t and "virtio-net-pci" in t and "tpm" not in t and "ide-cd" not in t
    assert "accel=kvm" not in t and "smm" not in t and "base=utc" in t
    bios = " ".join(vm.qemu_args(vm.check_config({**GOOD, "os": "other"}), "/mnt/alvaos/main/VMs"))
    assert "pflash" not in bios


def test_the_tpm_of_windows_11_keeps_its_state_with_the_machine():
    args = vm.swtpm_args(vm.check_config(GOOD), "/mnt/alvaos/main/VMs", "/run/x")
    assert "--tpm2" in args and "dir=/mnt/alvaos/main/VMs/0a1b2c3d/tpm" in args


def test_firmware_is_found_in_the_packages_folder(tmp_path):
    for n in ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd", "OVMF_CODE_4M.secboot.fd", "OVMF_VARS_4M.ms.fd"):
        (tmp_path / n).write_text("x")
    assert vm.find_firmware(False, (str(tmp_path),)) == (str(tmp_path / "OVMF_CODE_4M.fd"),
                                                         str(tmp_path / "OVMF_VARS_4M.fd"))
    assert vm.find_firmware(True, (str(tmp_path),))[1].endswith("OVMF_VARS_4M.ms.fd")
    with pytest.raises(vm.VmError, match="firmware"):
        vm.find_firmware(False, (str(tmp_path / "none"),))


class FakeQemu(threading.Thread):
    """A QMP socket that powers down after a number of status questions."""

    def __init__(self, path, polite=True):
        super().__init__(daemon=True)
        self.path, self.polite, self.seen, self.alive = path, polite, [], True
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(path)
        self.server.listen(5)
        self.start()

    def run(self):
        while self.alive:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            f = conn.makefile("rw")
            f.write(json.dumps({"QMP": {"version": {}}}) + "\n")
            f.flush()
            for line in f:
                cmd = json.loads(line)["execute"]
                self.seen.append(cmd)
                f.write(json.dumps({"event": "x"}) + "\n" + json.dumps({"return": {}}) + "\n")
                f.flush()
                if cmd == "quit" or (cmd == "system_powerdown" and self.polite):
                    self.alive = False
            conn.close()
            if not self.alive:
                self.server.close()
                return


def test_stopping_asks_the_guest_first_and_switches_off_when_it_does_not_listen(tmp_path):
    polite = FakeQemu(str(tmp_path / "0a1b2c3d.qmp"))
    assert vm.stop_vm("0a1b2c3d", str(tmp_path), grace=5, sleep=lambda s: None) == "shutdown"
    assert polite.seen[:2] == ["qmp_capabilities", "system_powerdown"]
    os.remove(tmp_path / "0a1b2c3d.qmp")
    rude = FakeQemu(str(tmp_path / "0a1b2c3d.qmp"), polite=False)
    assert vm.stop_vm("0a1b2c3d", str(tmp_path), grace=3, sleep=lambda s: None) == "forced"
    assert "quit" in rude.seen
    assert vm.stop_vm("deadbeef", str(tmp_path)) == "not running"


@pytest.fixture
def pool(tmp_path, monkeypatch):
    root = tmp_path / "mnt"
    store = root / "main" / "VMs"
    store.mkdir(parents=True)
    state = tmp_path / "vms.json"
    state.write_text(json.dumps({"store": str(store)}))
    configs = tmp_path / "vms"
    configs.mkdir()
    (configs / "0a1b2c3d.json").write_text(json.dumps({**GOOD, "iso": "", "os": "linux"}))
    fw = tmp_path / "fw"
    fw.mkdir()
    for n in ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"):
        (fw / n).write_text("vars")
    monkeypatch.setattr(vm, "FIRMWARE_DIRS", (str(fw),))
    made = []

    def fake_img(cmd, **kw):
        import subprocess
        made.append(cmd)
        open(cmd[4], "w").write("qcow2")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(vm.subprocess, "run", fake_img)
    return root, store, str(state), str(configs), made


def test_preparing_makes_the_folder_the_disk_and_the_uefi_variables(pool):
    root, store, state, configs, made = pool
    owned = []
    out = vm.prepare("0a1b2c3d", state, configs, str(root), chown=owned.append)
    base = store / "0a1b2c3d"
    assert out["folder"] == str(base) and made[0][:4] == [vm.QEMU_IMG, "create", "-f", "qcow2"]
    assert made[0][5] == "80G" and (base / "OVMF_VARS.fd").read_text() == "vars" and owned == [str(base)]
    with pytest.raises(vm.VmError, match="files already"):
        vm.prepare("0a1b2c3d", state, configs, str(root), chown=owned.append)


def test_a_failed_preparation_leaves_no_folder_behind(pool, monkeypatch):
    root, store, state, configs, _ = pool
    import subprocess
    monkeypatch.setattr(vm.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "no space"))
    with pytest.raises(vm.VmError, match="no space"):
        vm.prepare("0a1b2c3d", state, configs, str(root), chown=lambda p: None)
    assert not (store / "0a1b2c3d").exists()


def test_deleting_removes_the_folder_but_not_while_it_runs(pool):
    root, store, state, configs, _ = pool
    (store / "0a1b2c3d").mkdir()
    (store / "0a1b2c3d" / "disk.qcow2").write_text("x")
    with pytest.raises(vm.VmError, match="Shut"):
        vm.delete("0a1b2c3d", state, str(root), is_active=lambda i: True)
    vm.delete("0a1b2c3d", state, str(root), is_active=lambda i: False)
    assert not (store / "0a1b2c3d").exists()
    outside = root / "elsewhere"
    outside.mkdir()
    (store / "deadbeef").symlink_to(outside)
    with pytest.raises(vm.VmError, match="not what"):
        vm.delete("deadbeef", state, str(root), is_active=lambda i: False)
    assert outside.exists()
    with pytest.raises(vm.VmError, match="not a virtual machine"):
        vm.delete("../x", state, str(root))


def test_installer_images_are_found_in_the_isos_folder_only(pool):
    root, store, state, _, _ = pool
    (store / "ISOs" / "linux").mkdir(parents=True)
    (store / "ISOs" / "debian.iso").write_text("a")
    (store / "ISOs" / "linux" / "mint.ISO").write_text("bb")
    (store / "ISOs" / "linux" / "notes.txt").write_text("x")
    (store / "ISOs" / ".hidden.iso").write_text("x")
    (root / "main" / "Media").mkdir()
    (root / "main" / "Media" / "elsewhere.iso").write_text("x")
    deep = store / "ISOs" / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "too-deep.iso").write_text("x")
    found = vm.list_isos(state, str(root))["isos"]
    assert sorted((i["name"], i["where"]) for i in found) == [("debian.iso", "."), ("mint.ISO", "linux")]
    assert found[0]["path"].startswith(str(store / "ISOs"))


def test_an_installer_has_to_be_in_the_isos_folder_of_the_store(pool):
    root, store, state, configs, _ = pool
    iso = str(store / "ISOs" / "a.iso")
    assert vm.check_config({**GOOD, "iso": iso}, str(root), str(store))["iso"] == iso
    with pytest.raises(vm.VmError, match="ISOs"):
        vm.check_config({**GOOD, "iso": str(root / "main" / "a.iso")}, str(root), str(store))
    with pytest.raises(vm.VmError, match="not a place"):
        vm.check_config({**GOOD, "iso": str(store / "ISOs" / ".." / "a.iso")}, str(root), str(store))


def test_before_a_start_the_machine_can_reach_its_folder_and_the_installer(pool):
    root, store, state, configs, _ = pool
    (store / "ISOs").mkdir()
    iso = store / "ISOs" / "a.iso"
    iso.write_text("x")
    os.chmod(store, 0o770)
    os.chmod(iso, 0o660)
    cfg = json.loads(open(os.path.join(configs, "0a1b2c3d.json")).read())
    open(os.path.join(configs, "0a1b2c3d.json"), "w").write(json.dumps({**cfg, "iso": str(iso)}))
    vm.ready("0a1b2c3d", state, configs, str(root))
    assert os.stat(store).st_mode & 0o001 and os.stat(iso).st_mode & 0o004
    assert os.stat(store / "ISOs").st_mode & 0o001


def test_the_store_has_to_be_set_up_and_inside_the_pools(tmp_path):
    with pytest.raises(vm.VmError, match="not set up"):
        vm.read_store(str(tmp_path / "none.json"))
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"store": "/etc"}))
    with pytest.raises(vm.VmError, match="not a place"):
        vm.read_store(str(state))


def test_helper_commands_answer_in_json(pool, monkeypatch, capsys):
    root, store, state, configs, _ = pool
    monkeypatch.setattr(vm, "STATE_FILE", state)
    monkeypatch.setattr(vm, "DATA_ROOT", str(root))
    assert vm.helper_main(["vm-isos"]) == 0 and json.loads(capsys.readouterr().out) == {"isos": []}
    assert vm.helper_main(["vm-delete", "nonsense"]) == 1 and "not a virtual machine" in capsys.readouterr().err
    assert vm.helper_main(["vm-delete"]) == 1
