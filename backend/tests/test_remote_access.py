"""Remote access over WireGuard (backend/remote_access.py)."""

import os
import stat
import subprocess

import pytest

import priv_policy
import remote_access as ra


@pytest.fixture
def remote(tmp_path, monkeypatch):
    ran, up = [], {"on": False}
    monkeypatch.setattr(ra, "_wg_quick", lambda: "/usr/bin/wg-quick")
    monkeypatch.setattr(ra, "_wg", lambda: "/usr/bin/wg")

    def run(cmd, timeout=30):
        ran.append(cmd)
        if cmd[1:2] == ["up"]:
            up["on"] = True
        if cmd[1:2] == ["down"]:
            up["on"] = False
        out = ""
        if cmd[1:3] == ["show", "remote0"]:
            out = "".join(f"{d['public_key']}\t1759500000\n" for d in r.load()["devices"][:1])
        return subprocess.CompletedProcess(cmd, 0, out, ""), None

    r = ra.RemoteAccess(run, settings_path=str(tmp_path / "remote.json"), config_path=str(tmp_path / "wg" / "remote0.conf"),
                        interface_up=lambda: up["on"], lan_addresses=lambda: ["192.168.1.20"])
    r.ran = ran
    return r


def test_turning_on_writes_a_safe_config_and_starts_the_tunnel(remote, tmp_path):
    ok, message = remote.configure({"enabled": True, "endpoint": "Home.Example.net"})
    assert ok, message
    assert remote.ran[-1] == ["/usr/bin/wg-quick", "up", str(tmp_path / "wg" / "remote0.conf")]
    config = (tmp_path / "wg" / "remote0.conf").read_bytes()
    priv_policy.check_wg_config(config)                       # the helper accepts it
    assert b"ListenPort = 51821" in config and b"Address = 100.96.96.1/24" in config
    for path in (tmp_path / "wg" / "remote0.conf", tmp_path / "remote.json"):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600    # private keys
    status = remote.status()
    assert status["running"] and status["endpoint"] == "home.example.net"
    assert "private_key" not in status and status["open_url"] == "http://100.96.96.1:8080"


def test_bad_settings_are_refused(remote):
    for payload in ({"endpoint": "home;rm -rf"}, {"endpoint": "a b"}, {"port": 80}, {"port": 51820},
                    {"port": "x"}):
        ok, _ = remote.configure(payload)
        assert not ok, payload
    assert remote.configure({"endpoint": "203.0.113.7"})[0]


def test_a_device_gets_its_config_once_and_only_its_public_key_is_kept(remote, tmp_path):
    remote.configure({"enabled": True, "endpoint": "home.example.net"})
    device, problem = remote.add_device("Anna's phone")
    assert problem == ""
    config = device["config"]
    assert "Endpoint = home.example.net:51821" in config and "AllowedIPs = 100.96.96.1/32" in config
    assert device["address"] == "100.96.96.2" and device["qr"].startswith("data:image/svg+xml;base64,")
    assert device["file_name"] == "alvaos-anna-s-phone.conf"
    private = next(line.split(" = ")[1] for line in config.splitlines() if line.startswith("PrivateKey"))
    stored = (tmp_path / "remote.json").read_text() + (tmp_path / "wg" / "remote0.conf").read_text()
    assert private not in stored                               # never kept on the NAS
    assert ra.public_key_of(private) in stored
    server = (tmp_path / "wg" / "remote0.conf").read_text()
    assert "AllowedIPs = 100.96.96.2/32" in server and "PresharedKey" in server
    second, _ = remote.add_device("Laptop")
    assert second["address"] == "100.96.96.3"
    assert remote.add_device("laptop")[1].startswith("There is already")
    assert remote.add_device("x\n[Peer]")[1].startswith("Give the device a name")
    [first, _] = remote.status()["devices"]
    assert first["name"] == "Anna's phone" and first["last_seen"].startswith("2025-10-03")


def test_removing_a_device_shuts_it_out(remote, tmp_path):
    remote.configure({"enabled": True, "endpoint": "home.example.net"})
    device, _ = remote.add_device("Phone")
    assert remote.remove_device(device["id"])[0]
    assert "100.96.96.2" not in (tmp_path / "wg" / "remote0.conf").read_text()
    assert not remote.remove_device(device["id"])[0]


def test_devices_need_remote_access_and_an_address(remote):
    assert remote.add_device("Phone")[1] == "Turn on remote access first."
    remote.configure({"enabled": True})
    assert remote.add_device("Phone")[1].startswith("Enter the public address")


def test_turning_off_stops_the_tunnel(remote):
    remote.configure({"enabled": True, "endpoint": "home.example.net"})
    ok, message = remote.configure({"enabled": False})
    assert ok and message == "Remote access is off." and remote.ran[-1][1] == "down"
    assert not remote.status()["running"]


def test_keys_are_real_wireguard_keys():
    private, public = ra.new_keypair()
    assert ra.KEY_RE.match(private) and ra.KEY_RE.match(public) and ra.public_key_of(private) == public
