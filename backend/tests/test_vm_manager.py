"""Virtual machines: what the page shows and does (backend/vm_manager.py, api_vms.py, vm_console.py)."""

import asyncio
import json
import subprocess

import pytest

import vm_console
import vm_manager as vmm
import vm_ops

HOST = {"arch": "x86_64", "cpus": 8, "memory_mb": 16000, "available_mb": 12000, "virtualization": True,
        "kvm": True, "supported": True, "reason": ""}


class Box:
    """A manager on a temporary state folder with the system faked."""

    def __init__(self, tmp_path):
        self.root = tmp_path / "mnt"
        self.store = self.root / "main" / "VMs"
        (self.store / "ISOs").mkdir(parents=True)
        self.state = tmp_path / "vms.json"
        self.configs = tmp_path / "vms"
        self.ran, self.helped, self.units = [], [], {}
        self.host = dict(HOST)
        self.have = {p: True for p in vmm.PACKAGES}
        self.journal = ""
        self.account = True
        self.manager = vmm.VmManager(self.run, str(self.state), str(self.configs), str(self.root), self.help,
                                     lambda i: self.units.get(i, "inactive"), lambda pkgs: dict(self.have),
                                     lambda: dict(self.host), lambda: self.account, lambda path: 500 * 2**30)

    def run(self, cmd, timeout=30, extra_env=None, input=None):
        self.ran.append(cmd)
        out = self.journal if "journalctl" in cmd[0] else ""
        return subprocess.CompletedProcess(cmd, 0, out, ""), None

    def help(self, args, timeout=300):
        self.helped.append(args)
        if args[0] == "vm-isos":
            return {"isos": [{"path": str(self.store / "ISOs" / "a.iso"), "name": "a.iso", "size_bytes": 5,
                              "where": "."}]}, ""
        return {}, ""

    def set_up(self):
        self.state.write_text(json.dumps({"store": str(self.store)}))


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


NEW = {"name": "Windows 11", "os": "windows11", "cpus": 4, "memory_mb": 8192, "disk_gb": 80}


def test_the_page_says_what_is_missing_before_anything_else(box):
    box.host.update(supported=False, reason="no virtualization")
    assert box.manager.status()["ready"] is False and box.manager.status()["host"]["reason"] == "no virtualization"
    box.host.update(supported=True, reason="")
    box.have["ovmf"] = False
    st = box.manager.status()
    assert st["missing_packages"] == ["ovmf"] and not st["ready"] and st["store"] == ""
    box.have["ovmf"] = True
    box.set_up()
    assert box.manager.status()["ready"] is True
    box.host["kvm"] = False
    assert "BIOS" in box.manager.status()["problem"]


def test_a_machine_is_made_with_its_description_disk_and_autostart(box):
    box.set_up()
    vm, error = box.manager.create({**NEW, "autostart": True, "ports": [{"proto": "tcp", "host": 3389, "guest": 3389}]})
    assert error == "" and vm["state"] == "stopped" and vm["slot"] == 0 and vm["os_name"] == "Windows 11"
    assert box.helped == [["vm-prepare", vm["id"]]]
    assert [vm_ops.UNIT_FMT.format(vm["id"])] == [c[-1] for c in box.ran if c[1] == "enable"]
    saved = json.loads((box.configs / f"{vm['id']}.json").read_text())
    assert saved["memory_mb"] == 8192 and saved["ports"][0]["host"] == 3389
    other, _ = box.manager.create({**NEW, "name": "Linux", "os": "linux", "memory_mb": 2048})
    assert other["slot"] == 1                                              # each machine its own screen number
    assert [v["name"] for v in box.manager.status()["vms"]] == ["Linux", "Windows 11"]


@pytest.mark.parametrize("change, message", [
    ({"name": ""}, "name"),
    ({"cpus": 64}, "8 processor"),
    ({"memory_mb": 16000}, "more memory"),
    ({"disk_gb": 0}, "disk"),
    ({"os": "amiga"}, "what will run"),
    ({"disk_gb": 900}, "500 GB free"),
])
def test_bad_machines_are_refused_with_a_sentence(box, change, message):
    box.set_up()
    vm, error = box.manager.create({**NEW, **change})
    assert vm is None and message in error and box.helped == []


def test_the_installer_has_to_be_in_the_isos_folder(box):
    box.set_up()
    vm, error = box.manager.create({**NEW, "iso": str(box.root / "main" / "Other" / "x.iso")})
    assert vm is None and "ISOs" in error


def test_no_machines_before_setup_and_names_are_unique(box):
    assert "Set up" in box.manager.create(NEW)[1]
    box.set_up()
    assert box.manager.create(NEW)[0]
    assert "called" in box.manager.create({**NEW, "name": "windows 11"})[1]


def test_a_failed_disk_leaves_no_description_behind(box):
    box.set_up()
    box.manager.helper = lambda args, timeout=300: (None, "no space left")
    vm, error = box.manager.create(NEW)
    assert vm is None and "no space" in error and list(box.configs.glob("*.json")) == []


def test_start_stop_and_switching_off_go_through_the_unit(box):
    box.set_up()
    vm, _ = box.manager.create({**NEW, "os": "linux", "memory_mb": 2048})
    unit = vm_ops.UNIT_FMT.format(vm["id"])
    assert box.manager.action(vm["id"], "start") == (True, '"Windows 11" is starting.')
    assert ["/usr/bin/systemctl", "start", unit] in box.ran
    box.units[vm["id"]] = "active"
    assert box.manager.status()["vms"][0]["state"] == "running"
    assert box.manager.action(vm["id"], "start")[0] is False
    assert box.manager.action(vm["id"], "force") == (True, '"Windows 11" is switched off.')
    assert ["/usr/bin/systemctl", "kill", "--signal=SIGKILL", unit] in box.ran
    box.units[vm["id"]] = "inactive"
    assert box.manager.action(vm["id"], "stop")[0] is False and box.manager.action(vm["id"], "fly")[0] is False


def test_a_machine_that_needs_more_memory_than_is_free_does_not_start(box):
    box.set_up()
    vm, _ = box.manager.create({**NEW, "memory_mb": 9000})
    box.host["available_mb"] = 4000
    ok, message = box.manager.action(vm["id"], "start")
    assert not ok and "free memory" in message and not any(c[1] == "start" for c in box.ran if len(c) > 1)


def test_a_failed_machine_shows_why(box):
    box.set_up()
    vm, _ = box.manager.create(NEW)
    box.units[vm["id"]] = "failed"
    box.journal = ("Oct 04 alvaos-vm@x[1]: alvaos-vm: This NAS cannot run virtual machines: no KVM\n"
                   "Oct 04 systemd[1]: alvaos-vm@x.service: Failed with result 'exit-code'.\n")
    got = box.manager.status()["vms"][0]
    assert got["state"] == "failed" and got["problem"] == "This NAS cannot run virtual machines: no KVM"


def test_changing_needs_a_stopped_machine_and_deleting_removes_everything(box):
    box.set_up()
    vm, _ = box.manager.create(NEW)
    changed, error = box.manager.update(vm["id"], {"cpus": 2, "autostart": True, "iso": str(box.store / "ISOs" / "a.iso")})
    assert error == "" and changed["cpus"] == 2 and changed["iso_name"] == "a.iso"
    assert any(c[1:2] == ["enable"] for c in box.ran)
    assert box.manager.update(vm["id"], {"disk_gb": 5})[0]["disk_gb"] == 80      # the disk is not changed here
    box.units[vm["id"]] = "active"
    assert "Shut it down" in box.manager.update(vm["id"], {"cpus": 1})[1]
    assert box.manager.delete(vm["id"]) == (False, "Shut it down first.")
    box.units[vm["id"]] = "inactive"
    ok, message = box.manager.delete(vm["id"])
    assert ok and "deleted" in message and ["vm-delete", vm["id"]] in box.helped
    assert list(box.configs.glob("*.json")) == [] and box.manager.status()["vms"] == []


def test_setup_makes_the_folder_then_installs_in_the_background(box, monkeypatch):
    box.have["qemu-utils"] = False
    made = []
    started = []
    monkeypatch.setattr(vmm.threading, "Thread", lambda target, args, name, daemon: type(
        "T", (), {"start": lambda self: started.append((target, args))})())
    ok, message = box.manager.setup(lambda: (made.append(1) or str(box.store), ""))
    assert ok and "Installing" in message and made == [1] and json.loads(box.state.read_text()) == {"store": str(box.store)}
    assert box.helped == [["vm-setup"]] and started and started[0][1] == (["qemu-utils"],)
    started[0][0](*started[0][1])
    assert any(c[1:3] == ["-y", "install"] and c[-1] == "qemu-utils" for c in box.ran)
    assert vmm._job["running"] is False and vmm._job["error"] == ""
    ok, message = box.manager.setup(lambda: ("", "should not be asked again"))      # the folder is there
    assert ok and made == [1]
    box.host.update(supported=False, reason="no virtualization")
    assert box.manager.setup(lambda: ("", ""))[1] == "no virtualization"


def test_the_screen_needs_a_running_machine_and_a_ticket_works_once(box):
    box.set_up()
    vm, _ = box.manager.create(NEW)
    assert box.manager.console(vm["id"]) == (None, "Start it first.")
    box.units[vm["id"]] = "active"
    answer, _ = box.manager.console(vm["id"])
    assert answer["port"] == 8085 and answer["tls_port"] == 9445
    assert vm_console.redeem(answer["ticket"]) == (vm["id"], 0)
    assert vm_console.redeem(answer["ticket"]) is None
    assert vm_console.redeem("nonsense") is None


# ── The door for the screen ──────────────────────────────────────────────────

def head(ticket, origin="http://nas:8080", host="nas:8085", upgrade="websocket", method="GET"):
    return (f"{method} /?ticket={ticket} HTTP/1.1\r\nHost: {host}\r\nUpgrade: {upgrade}\r\n"
            f"Connection: Upgrade\r\nOrigin: {origin}\r\n\r\n").encode()


def test_only_a_page_of_this_nas_with_a_fresh_ticket_gets_through():
    ticket = vm_console.issue("0a1b2c3d", 7)
    assert vm_console.check_request(head(ticket, origin="http://evil.example")) == (None, "403 Forbidden")
    assert vm_console.check_request(head(ticket, origin="")) == (None, "403 Forbidden")
    assert vm_console.check_request(head(ticket, upgrade="")) == (None, "400 Bad Request")
    assert vm_console.check_request(head(ticket, method="POST")) == (None, "400 Bad Request")
    assert vm_console.check_request(b"nonsense\r\n\r\n") == (None, "400 Bad Request")
    assert vm_console.check_request(head(ticket)) == (7, "")                 # the failed tries used nothing up
    assert vm_console.check_request(head(ticket)) == (None, "403 Forbidden")  # once only


def test_expired_tickets_are_no_good(monkeypatch):
    ticket = vm_console.issue("0a1b2c3d", 1)
    real = vm_console.time.monotonic
    monkeypatch.setattr(vm_console.time, "monotonic", lambda: real() + vm_console.TICKET_SECONDS + 1)
    assert vm_console.redeem(ticket) is None


def test_the_door_copies_bytes_between_the_browser_and_the_machine_after_the_check():
    async def scenario():
        got = {}

        async def machine(reader, writer):          # stands in for QEMU's WebSocket on the loopback address
            got["head"] = await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 101 Switching Protocols\r\n\r\nscreen")
            await writer.drain()
            got["more"] = await reader.read(5)
            writer.close()
        qemu = await asyncio.start_server(machine, "127.0.0.1", 0)
        qemu_port = qemu.sockets[0].getsockname()[1]
        door = await asyncio.start_server(lambda r, w: vm_console.handle(r, w, lambda slot: ("127.0.0.1", qemu_port)),
                                          "127.0.0.1", 0)
        door_port = door.sockets[0].getsockname()[1]
        ticket = vm_console.issue("0a1b2c3d", 2)
        reader, writer = await asyncio.open_connection("127.0.0.1", door_port)
        writer.write(head(ticket))
        await writer.drain()
        first = await asyncio.wait_for(reader.read(200), 5)
        writer.write(b"hello")
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()
        # no ticket, no way in
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", door_port)
        writer2.write(head("bad"))
        await writer2.drain()
        refused = await asyncio.wait_for(reader2.read(200), 5)
        writer2.close()
        qemu.close()
        door.close()
        return got, first, refused
    got, first, refused = asyncio.run(scenario())
    assert got["head"].startswith(b"GET /?ticket=") and got["more"] == b"hello"
    assert first.endswith(b"screen") and refused.startswith(b"HTTP/1.1 403")


def test_a_screen_that_is_not_there_answers_bad_gateway():
    async def scenario():
        door = await asyncio.start_server(lambda r, w: vm_console.handle(r, w, lambda slot: ("127.0.0.1", 1)),
                                          "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", door.sockets[0].getsockname()[1])
        writer.write(head(vm_console.issue("0a1b2c3d", 0)))
        await writer.drain()
        answer = await asyncio.wait_for(reader.read(200), 5)
        writer.close()
        door.close()
        return answer
    assert asyncio.run(scenario()).startswith(b"HTTP/1.1 502")
