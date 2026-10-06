"""A UPS on USB, run by NUT (backend/ups_nut.py).

Every command and file goes through the real privilege policy, as on a NAS.
"""

import json
import os
import subprocess
import time

import pytest

import priv_policy as p
import ups_nut as u
from fakes import FakeSystem

APC = {"vendorid": "051d", "productid": "0002", "driver": "usbhid-ups", "name": "American Power Conversion Back-UPS ES 700G"}


def usb(sys_root, name, vendor, product, product_name="", manufacturer=""):
    dev = sys_root / "bus/usb/devices" / name
    dev.mkdir(parents=True)
    (dev / "idVendor").write_text(vendor + "\n")
    (dev / "idProduct").write_text(product + "\n")
    if product_name:
        (dev / "product").write_text(product_name + "\n")
    if manufacturer:
        (dev / "manufacturer").write_text(manufacturer + "\n")


def machine(tmp_path):
    sys_root = tmp_path / "sys"
    usb(sys_root, "1-1", "051d", "0002", "Back-UPS ES 700G FW:871.O4", "American Power Conversion")
    usb(sys_root, "1-2", "046d", "c52b", "USB Receiver", "Logitech")              # a keyboard
    usb(sys_root, "1-3", "0001", "0000", "MEC0003")                               # not a UPS
    usb(sys_root, "1-4", "0665", "5161", "USB to Serial")                         # Megatec UPS
    usb(sys_root, "usb1", "1d6b", "0002", "xHCI Host Controller", "Linux")         # the hub itself
    return sys_root


class Helper:
    """Answers like run_sudo_command, after the real policy checked the command."""

    def __init__(self):
        self.system = FakeSystem()
        self.ran = []
        self.files = {}

    def __call__(self, cmd, timeout=30, extra_env=None, input=None):
        plan = p.validate(cmd, self.system)
        if plan.stdin_check:
            plan.stdin_check((input or "").encode())
        assert plan.argv == cmd
        self.ran.append(cmd)
        if os.path.basename(cmd[0]) == "tee":
            self.files[cmd[1]] = input
        return subprocess.CompletedProcess(cmd, 0, "", ""), None


def wait_for_job():
    for _ in range(200):
        if not u._job["running"]:
            return
        time.sleep(0.01)
    raise AssertionError("the set-up job did not finish")


def test_finds_ups_on_usb_and_nothing_else(tmp_path):
    found = u.detect(str(machine(tmp_path)))
    assert [(d["vendorid"], d["driver"]) for d in found] == [("051d", "usbhid-ups"), ("0665", "nutdrv_qx")]
    assert found[0]["name"] == "American Power Conversion Back-UPS ES 700G FW871.O4"


def test_the_files_written_pass_the_helper_and_name_the_ups():
    files = u.render_files(APC, "a1" * 16)
    for path, content in files.items():
        assert path in p.CONFIG_FILES
        p.CONFIG_CHECKS[path](content.encode())
    assert "driver = usbhid-ups" in files["/etc/nut/ups.conf"] and "vendorid = 051d" in files["/etc/nut/ups.conf"]
    assert 'SHUTDOWNCMD "/sbin/shutdown -h +0"' in files["/etc/nut/upsmon.conf"]
    p.CONFIG_CHECKS["/etc/nut/nut.conf"](u.off_files()["/etc/nut/nut.conf"].encode())


@pytest.mark.parametrize("path,line", [
    ("/etc/nut/upsmon.conf", 'SHUTDOWNCMD "/bin/sh -c id"'),
    ("/etc/nut/upsmon.conf", 'NOTIFYCMD /tmp/x'),
    ("/etc/nut/upsmon.conf", "RUN_AS_USER root"),
    ("/etc/nut/upsmon.conf", "MONITOR ups@192.168.1.9 1 alvaos-monitor " + "a" * 32 + " primary"),
    ("/etc/nut/upsd.users", "instcmds = ALL"),
    ("/etc/nut/upsd.users", "actions = SET FSD"),
    ("/etc/nut/upsd.users", "password = short"),
    ("/etc/nut/ups.conf", "driver = /tmp/evil"),
    ("/etc/nut/ups.conf", "port = /dev/sda"),
    ("/etc/nut/ups.conf", 'desc = "x"; rm -rf /"'),
    ("/etc/nut/ups.conf", "[second]"),
    ("/etc/nut/nut.conf", "MODE=netserver"),
    ("/etc/nut/nut.conf", "UPSD_OPTIONS=-u root"),
])
def test_nut_files_refuse_what_alvaos_does_not_write(path, line):
    good = u.render_files(APC, "b2" * 16)[path]
    with pytest.raises(p.PolicyError):
        p.CONFIG_CHECKS[path]((good + line + "\n").encode())


def test_upsmon_may_only_force_the_shutdown():
    assert p.validate(["/usr/sbin/upsmon", "-c", "fsd"], FakeSystem()).argv == ["/usr/sbin/upsmon", "-c", "fsd"]
    for args in ([], ["-c", "stop"], ["-D"], ["-c", "fsd", "-u", "root"], ["-K"]):
        with pytest.raises(p.PolicyError):
            p.validate(["/usr/sbin/upsmon", *args], FakeSystem())


def test_setting_up_installs_nut_writes_the_files_and_starts_it(tmp_path):
    helper = Helper()
    ups = u.NutUps(helper, state_file=str(tmp_path / "ups.json"), sys_root=str(machine(tmp_path)),
                   read_values=dict, installed=lambda: False)
    assert not ups.set_up("051d", "0002", 7)[0]          # not one of the choices
    assert not ups.set_up("dead", "beef", 0)[0]          # not connected
    ok, message = ups.set_up("051d", "0002", 5)
    assert ok, message
    wait_for_job()
    assert u._job["error"] == ""
    names = [" ".join([os.path.basename(c[0])] + c[1:]) for c in helper.ran]
    assert names[:2] == ["apt-get update", "apt-get -y install nut"]
    assert set(helper.files) == set(u.NUT_FILES.values())
    assert names.index("tee /etc/nut/upsmon.conf") < names.index("systemctl restart nut-monitor.service")
    saved = json.loads((tmp_path / "ups.json").read_text())
    assert saved["enabled"] and saved["shutdown_after_minutes"] == 5 and len(saved["password"]) == 32
    assert oct(os.stat(tmp_path / "ups.json").st_mode & 0o777) == "0o600"
    status = ups.status()
    assert "password" not in json.dumps(status) and status["settings"]["name"].startswith("American")


def test_power_cut_is_noted_shuts_down_after_the_chosen_minutes_and_power_back_too(tmp_path):
    helper = Helper()
    now = [1000.0]
    values = {"ups.status": "OL", "battery.charge": "100", "battery.runtime": "1500"}
    notes = []
    ups = u.NutUps(helper, state_file=str(tmp_path / "ups.json"), sys_root=str(tmp_path),
                   read_values=lambda: dict(values), notify=lambda *a, **k: notes.append(a[:2]), clock=lambda: now[0])
    ups._save({"enabled": True, "name": "APC", "password": "c" * 32, "shutdown_after_minutes": 5})
    ups.check()
    assert notes == []
    values.update({"ups.status": "OB DISCHRG", "battery.charge": "93"})
    ups.check()
    assert notes == [("warning", "The power failed")]
    assert ups.alert()["id"] == "ups-on-battery"
    now[0] += 4 * 60
    ups.check()
    assert not any("upsmon" in c[0] for c in helper.ran)
    now[0] += 61
    ups.check()
    ups.check()
    assert [c for c in helper.ran if "upsmon" in c[0]] == [["/usr/sbin/upsmon", "-c", "fsd"]]   # once
    values.update({"ups.status": "OL CHRG"})
    ups.check()
    assert notes[-1] == ("info", "The power is back") and ups.alert() is None


def test_low_battery_is_left_to_upsmon_and_a_silent_ups_is_an_alert(tmp_path):
    helper = Helper()
    values = {"ups.status": "OB LB", "battery.charge": "9"}
    ups = u.NutUps(helper, state_file=str(tmp_path / "ups.json"), read_values=lambda: dict(values),
                   notify=lambda *a, **k: None, clock=lambda: 5000.0)
    ups._save({"enabled": True, "shutdown_after_minutes": 0})
    ups.check()
    assert helper.ran == []                           # upsmon itself shuts down on "low battery"
    values.clear()
    ups.check()
    assert ups.alert()["id"] == "ups-unreachable"


def test_turning_off_leaves_nut_installed_but_idle(tmp_path):
    helper = Helper()
    ups = u.NutUps(helper, state_file=str(tmp_path / "ups.json"), read_values=dict)
    ups._save({"enabled": True, "password": "d" * 32})
    assert ups.turn_off()[0]
    assert helper.files == {"/etc/nut/nut.conf": "# Managed by AlvaOS - Settings > Power\nMODE=none\n"}
    assert ["/usr/bin/systemctl", "disable", "--now", "nut-monitor.service"] in helper.ran
    assert ups.settings()["enabled"] is False


def test_upsc_values_are_described():
    values = u.parse_upsc("battery.charge: 87\nbattery.runtime: 1260\nups.load: 23\nups.status: OB DISCHRG RB\n"
                          "device.mfr: APC\ndevice.model: Back-UPS ES 700G\nInit SSL without certificate database\n")
    live = u.describe(values)
    assert live == {"reachable": True, "on_battery": True, "low_battery": False, "replace_battery": True,
                    "charging": False, "charge_percent": 87, "runtime_minutes": 21, "load_percent": 23,
                    "model": "APC Back-UPS ES 700G", "status_flags": ["DISCHRG", "OB", "RB"]}


def test_the_page_api_shows_the_ups_and_refuses_odd_requests(tmp_path, monkeypatch):
    import app_services
    import auth_manager
    helper = Helper()
    ups = u.NutUps(helper, state_file=str(tmp_path / "ups.json"), sys_root=str(machine(tmp_path)), read_values=dict)
    monkeypatch.setattr(app_services, "ups", ups)
    import importlib.util
    spec = importlib.util.spec_from_file_location("backend_for_ups", os.path.join(os.path.dirname(__file__), "..", "alvaos-backend.py"))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(auth_manager, "SETUP_STATUS_FILE", str(tmp_path / "setup.json"))
    monkeypatch.setattr(auth_manager, "SESSIONS_FILE", str(tmp_path / "sessions.json"))
    (tmp_path / "setup.json").write_text("{}")
    spec.loader.exec_module(module)
    client = module.app.test_client()
    token = auth_manager._create_session("root", role="admin")
    headers = {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}
    data = client.get("/api/v1/system/ups", headers=headers).get_json()
    assert data["detected"][0]["vendorid"] == "051d" and data["settings"]["enabled"] is False
    assert client.post("/api/v1/system/ups", headers=headers, json={"action": "reboot"}).status_code == 400
    assert client.post("/api/v1/system/ups", headers=headers, json={"action": "change", "shutdown_after_minutes": "5"}).status_code == 400
    assert client.post("/api/v1/system/ups", headers=headers, json={"action": "change", "shutdown_after_minutes": 5}).status_code == 409
    assert client.get("/api/v1/system/ups").status_code == 401
