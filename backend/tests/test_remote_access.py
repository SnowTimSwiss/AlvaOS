"""Remote access: Tailscale and Cloudflare Tunnel (backend/remote_access.py)."""

import base64
import json
import os
import stat
import subprocess

import pytest
import yaml

import priv_policy
import remote_access as ra

TS_STATUS = {"BackendState": "Running", "AuthURL": "",
             "Self": {"DNSName": "alva.tail1234.ts.net.", "TailscaleIPs": ["100.101.102.103", "fd7a::1"]},
             "CurrentTailnet": {"Name": "anna@example.com"},
             "Peer": {"k1": {"HostName": "Annas-iPhone", "OS": "iOS", "Online": True, "LastSeen": ""},
                      "k2": {"HostName": "laptop", "OS": "windows", "Online": False, "LastSeen": "2026-10-06"}}}
TUNNEL_TOKEN = base64.b64encode(json.dumps({"a": "acc1", "t": "11111111-2222-3333-4444-555555555555",
                                            "s": "c2VjcmV0c2VjcmV0c2VjcmV0"}).encode()).decode()


class FakeCloudflare:
    def __init__(self, token, log):
        self.token, self.log = token, log

    def zones(self):
        if self.token == "bad-token-" + "x" * 30:
            raise ra.CloudflareError("Cloudflare does not accept the API token for this.")
        return [{"id": "a" * 32, "name": "example.com", "account": "acc1"}]

    def make(self, zone, hostname, label):
        self.log.append(("make", zone["name"], hostname))
        return {"tunnel_id": "tid", "dns_record_id": "rid", "tunnel_token": TUNNEL_TOKEN}

    def remove(self, account, tunnel_id, zone_id, record_id):
        self.log.append(("remove", account, tunnel_id, zone_id, record_id))


@pytest.fixture
def remote(tmp_path):
    state = {"running": set(), "status": TS_STATUS}
    ran, ups, downs, cf_log, execs = [], [], [], [], []

    def run(cmd, timeout=30):
        ran.append(cmd)
        if cmd[1] == "inspect":
            return subprocess.CompletedProcess(cmd, 0, "true\n" if cmd[-1] in state["running"] else "false\n", ""), None
        return subprocess.CompletedProcess(cmd, 0, "", ""), None

    def exec_in(container, command, timeout=20):
        execs.append((container, command))
        if command == "tailscale status --json":
            return {"exit_code": 0, "output": json.dumps(state["status"])}, None
        return {"exit_code": 0, "output": ""}, None

    def up(compose, project):
        ups.append((compose, project))
        state["running"].add(next(iter(compose["services"].values()))["container_name"])
        return True, None

    def down(compose, project):
        downs.append(project)
        state["running"].discard(next(iter(compose["services"].values()))["container_name"])
        return True, None

    r = ra.RemoteAccess(run, settings_path=str(tmp_path / "remote.json"), compose_up=up, compose_down=down,
                        cloudflare=lambda token: FakeCloudflare(token, cf_log), hostname=lambda: "Alva", exec_in=exec_in)
    r.state, r.ran, r.ups, r.downs, r.cf_log, r.execs = state, ran, ups, downs, cf_log, execs
    return r


def test_tailscale_starts_as_a_container_and_shows_the_sign_in_link(remote):
    remote.state["status"] = {"BackendState": "NeedsLogin", "AuthURL": "https://login.tailscale.com/a/abc123"}
    ok, message = remote.set_tailscale(True)
    assert ok and "Sign in" in message
    compose, project = remote.ups[-1]
    assert project == "alvaos-tailscale"
    service = compose["services"]["tailscale"]
    assert service["network_mode"] == "host" and service["environment"]["TS_HOSTNAME"] == "alva"
    priv_policy.check_compose(yaml.dump(compose).encode())          # what the helper will accept
    st = remote.tailscale_status()
    assert st["state"] == "login" and st["login_url"] == "https://login.tailscale.com/a/abc123"
    remote.state["status"] = {"BackendState": "NeedsLogin", "AuthURL": "https://evil.example/a"}
    assert remote.tailscale_status()["login_url"] == ""             # only Tailscale's own sign-in page


def test_tailscale_lists_the_devices_once_signed_in(remote):
    remote.set_tailscale(True)
    st = remote.tailscale_status()
    assert st["state"] == "on" and st["name"] == "alva.tail1234.ts.net" and st["addresses"][0] == "100.101.102.103"
    assert [d["name"] for d in st["devices"]] == ["Annas-iPhone", "laptop"] and st["tailnet"] == "anna@example.com"
    assert remote.problems() == []
    remote.state["running"].clear()
    assert remote.problems()[0]["alert_id"] == "remote-access-tailscale"
    ok, _ = remote.set_tailscale(False)
    assert ok and remote.downs == ["alvaos-tailscale"] and remote.problems() == []
    assert remote.tailscale_status() == {"enabled": False, "running": False, "state": "off", "login_url": "",
                                         "name": "", "addresses": [], "tailnet": "", "devices": []}


def test_signing_out_runs_inside_the_container_through_the_helper(remote, monkeypatch):
    remote.set_tailscale(True)
    assert remote.tailscale_logout()[0]
    assert remote.execs[-1] == ("alvaos-tailscale", "tailscale logout")
    cmd = ["/usr/bin/docker", "exec", "alvaos-tailscale", "/bin/sh", "-lc", "tailscale logout"]
    assert priv_policy.validate(cmd, priv_policy.System()).argv[1:] == cmd[1:]   # what docker_manager runs
    import docker_manager
    seen = []
    monkeypatch.setattr(docker_manager.subprocess, "run",
                        lambda cmd, **kw: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "{}", ""))
    for command in ("tailscale status --json", "tailscale logout"):      # its command filter lets them through
        result, error = docker_manager.DockerManager().exec_in_container("alvaos-tailscale", command, timeout=1)
        assert error is None and seen[-1][-4:] == ["alvaos-tailscale", "/bin/sh", "-lc", command]


def test_cloudflare_with_an_api_token_makes_tunnel_and_name_for_the_hub_only(remote, tmp_path):
    token = "t" * 40
    zones, problem = remote.cloudflare_zones(token)
    assert problem == "" and zones == [{"id": "a" * 32, "name": "example.com"}]
    assert "Choose a name" in remote.cloudflare_connect({"api_token": token, "zone_id": "a" * 32, "name": "Bad Name"})[1]
    assert "Choose one of your domains" in remote.cloudflare_connect({"api_token": token, "zone_id": "b" * 32,
                                                                      "name": "cloud"})[1]
    ok, message = remote.cloudflare_connect({"api_token": token, "zone_id": "a" * 32, "name": "cloud"})
    assert ok and "https://cloud.example.com" in message
    assert remote.cf_log == [("make", "example.com", "cloud.example.com")]
    compose, project = remote.ups[-1]
    assert project == "alvaos-cloudflared" and compose["services"]["cloudflared"]["environment"]["TUNNEL_TOKEN"] == TUNNEL_TOKEN
    priv_policy.check_compose(yaml.dump(compose).encode())
    assert remote.public_url() == "https://cloud.example.com"
    st = remote.cloudflare_status()
    assert st["running"] and st["url"] == "https://cloud.example.com" and "api_token" not in json.dumps(st).replace("has_api_token", "")
    assert stat.S_IMODE(os.stat(tmp_path / "remote.json").st_mode) == 0o600      # it holds the tokens
    assert "already" in remote.cloudflare_connect({"api_token": token, "zone_id": "a" * 32, "name": "x"})[1]
    ok, _ = remote.cloudflare_disconnect()
    assert ok and remote.cf_log[-1] == ("remove", "acc1", "tid", "a" * 32, "rid") and remote.public_url() == ""


def test_the_hub_is_the_only_thing_a_tunnel_reaches():
    calls = []

    class Res:
        status_code = 200

        def __init__(self, result):
            self.result = result

        def json(self):
            return {"success": True, "result": self.result}

    def request(method, url, **kw):
        calls.append((method, url.replace(ra.CF_API, ""), kw.get("json")))
        if url.endswith("/dns_records") and method == "GET":
            return Res([])
        if url.endswith("/cfd_tunnel") and method == "POST":
            return Res({"id": "tid"})
        if url.endswith("/token"):
            return Res(TUNNEL_TOKEN)
        return Res({"id": "rid"})

    made = ra.Cloudflare("t" * 40, request).make({"id": "z" * 32, "name": "example.com", "account": "acc"},
                                                  "cloud.example.com", "cloud")
    assert made == {"tunnel_id": "tid", "dns_record_id": "rid", "tunnel_token": TUNNEL_TOKEN}
    config = next(c[2] for c in calls if c[1].endswith("/configurations"))
    assert config["config"]["ingress"] == [{"hostname": "cloud.example.com", "service": "http://localhost:8090"},
                                           {"service": "http_status:404"}]
    dns = next(c[2] for c in calls if c[0] == "POST" and c[1].endswith("/dns_records"))
    assert dns["content"] == "tid.cfargotunnel.com" and dns["proxied"] is True


def test_a_name_in_use_is_refused_and_a_bad_token_explained():
    class Res:
        def __init__(self, status, data):
            self.status_code, self.data = status, data

        def json(self):
            return self.data
    taken = ra.Cloudflare("t" * 40, lambda m, u, **kw: Res(200, {"success": True, "result": [{"id": "x"}]}))
    with pytest.raises(ra.CloudflareError, match="used already"):
        taken.make({"id": "z" * 32, "name": "example.com", "account": "acc"}, "cloud.example.com", "cloud")
    bad = ra.Cloudflare("t" * 40, lambda m, u, **kw: Res(403, {"success": False, "errors": [{"code": 10000,
                                                                                            "message": "Auth"}]}))
    with pytest.raises(ra.CloudflareError, match="Tunnel: Edit"):
        bad.zones()


def test_cloudflare_with_a_tunnel_token_from_the_dashboard(remote):
    assert "not a tunnel token" in remote.cloudflare_connect({"tunnel_token": "nope" * 20})[1]
    ok, message = remote.cloudflare_connect({"tunnel_token": TUNNEL_TOKEN, "hostname": "nas.example.org"})
    assert ok and "https://nas.example.org" in message
    ok, message = remote.cloudflare_disconnect()
    assert ok and "Delete it in Cloudflare" in message and remote.cf_log == []


def test_docker_trouble_is_said_in_words(remote):
    remote.compose_up = lambda compose, project: (False, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    ok, message = remote.set_tailscale(True)
    assert not ok and "Docker is not running" in message and remote.load()["tailscale"]["enabled"] is False


def test_the_old_wireguard_remote_access_is_retired_once(remote, tmp_path, monkeypatch):
    (tmp_path / "remote.json").write_text(json.dumps({"enabled": True, "upnp": True, "port": 51821, "devices": []}))
    closed = []
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/upnpc")
    assert remote.retire_wireguard(run_local=closed.append) is True
    assert closed == [["/usr/bin/upnpc", "-d", "51821", "UDP"]]
    assert remote.retire_wireguard(run_local=closed.append) is False and len(closed) == 1
    assert "devices" not in json.loads((tmp_path / "remote.json").read_text())
