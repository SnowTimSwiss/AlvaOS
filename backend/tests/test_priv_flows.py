"""The admin page's real actions, through the privilege helper's policy.

On a NAS the backend is not root: every command goes through alvaos-priv,
which checks it and runs exactly what it checked. Two such checks once broke
adding people (useradd ran without its arguments) and making shared folders
(the trash line in smb.conf was refused), while every unit test passed,
because tests run as root or fake the command runner. Here the backend runs
as on a NAS: commands are wrapped for the helper, and each one is checked by
the real policy, with what it sends on stdin, before it is pretended to run.
"""

import os
import subprocess

import pytest

import auth_manager
import common
import priv_policy as p
from fakes import FakeSystem
from test_api import backend  # noqa: F401 - the admin backend fixture


class Box:
    def __init__(self, tmp_path):
        self.system = FakeSystem()
        self.denied = []
        self.ran = []
        self.root = tmp_path / "mnt" / "alvaos"
        self.fail = {}             # command name -> (returncode, stderr) to answer instead

    def check(self, argv, stdin=b""):
        """(returncode, stdout, stderr) as the helper would answer."""
        if argv[:3] != ["sudo", "-n", common.PRIV_HELPER]:
            return 0, "", ""            # not privileged: lsblk, getent, ... answer nothing
        args = argv[3:]
        while args[:1] == ["--env"]:
            args = args[2:]
        if args[:1] == ["--"]:
            args = args[1:]
        try:
            plan = p.validate(args, self.system)
            if plan.stdin_check:
                plan.stdin_check(stdin if isinstance(stdin, bytes) else str(stdin or "").encode())
        except p.PolicyError as e:
            self.denied.append((args, str(e)))
            return 126, "", f"alvaos-priv: denied: {e}"
        assert plan.argv[1:] == args[1:] or plan.stage, ("the helper would run something else", args, plan.argv)
        self.ran.append(args)
        name, rest = os.path.basename(args[0]), args[1:]
        if name in self.fail:
            return self.fail[name][0], "", self.fail[name][1]
        if name == "tee" and rest[:1] != ["-a"]:
            self.system.files[rest[0]] = stdin if isinstance(stdin, bytes) else str(stdin).encode()
        elif name == "cat":
            if rest[0] not in self.system.files:
                return 1, "", "No such file or directory"
            return 0, self.system.files[rest[0]].decode(), ""
        if name == "useradd":
            self.system.users[rest[-1]] = 2000 + len(self.system.users)
        elif name == "groupadd":
            self.system.groups[rest[-1]] = 3000 + len(self.system.groups)
        elif name == "userdel":
            self.system.users.pop(rest[-1], None)
        elif name == "btrfs" and rest[:2] == ["subvolume", "create"]:
            os.makedirs(rest[2], exist_ok=True)
        return 0, "", ""


@pytest.fixture
def box(tmp_path, monkeypatch):
    b = Box(tmp_path)
    real_run, real_popen = subprocess.run, subprocess.Popen

    def run(cmd, *a, **kw):
        if not isinstance(cmd, list) or cmd[:1] != ["sudo"]:
            return real_run(cmd, *a, **kw)
        code, out, err = b.check(cmd, kw.get("input") or b"")
        return subprocess.CompletedProcess(cmd, code, out, err)

    class Popen:
        def __init__(self, cmd, *a, **kw):
            if not isinstance(cmd, list) or cmd[:1] != ["sudo"]:
                self.real = real_popen(cmd, *a, **kw)
            self.cmd, self.returncode, self.text = cmd, None, kw.get("text")

        def communicate(self, input=None, timeout=None):
            if hasattr(self, "real"):
                return self.real.communicate(input, timeout)
            code, out, err = b.check(self.cmd, input or b"")
            self.returncode = code
            return out, err

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    monkeypatch.setattr(common, "is_root_user", lambda: False)
    import api_auth
    import shares_manager
    import storage_manager
    monkeypatch.setattr(api_auth, "USERS_STATE_FILE", str(tmp_path / "users.json"))
    monkeypatch.setattr(api_auth, "system_user_exists", lambda name: name in b.system.users)
    monkeypatch.setattr(shares_manager, "SHARES_STATE_FILE", str(tmp_path / "shares.json"))
    pool = str(b.root / "main")
    # The pools live in a temporary folder here: the policy accepts it as a data root.
    monkeypatch.setattr(p, "WRITE_ROOTS", p.WRITE_ROOTS + (str(b.root),))
    pools = {"p1": {"id": "p1", "name": "main", "mount_point": pool, "status": "online"}}
    import api_shares
    for module in (storage_manager, api_shares):
        monkeypatch.setattr(module, "load_pools_state", lambda: pools)
    os.makedirs(pool, exist_ok=True)
    monkeypatch.setattr(shares_manager, "ensure_samba_conf_exists", lambda: None)
    return b


def admin(backend):  # noqa: F811
    module, state = backend
    (state / "setup_complete.json").write_text("{}")
    token = auth_manager._create_session("root", role="admin")
    return module.app.test_client(), {"Authorization": token, "X-CSRF-Token": auth_manager.SESSIONS[token]["csrf_token"]}


def test_adding_a_person_and_a_shared_folder_passes_the_helper(backend, box, monkeypatch):  # noqa: F811
    import api_shares
    monkeypatch.setattr(api_shares, "is_path_on_system_disk", lambda path: False)
    client, headers = admin(backend)
    made = client.post("/api/v1/users", json={"username": "test", "password": "long enough"}, headers=headers)
    assert made.status_code == 200, (made.get_json(), box.denied)
    assert ["/usr/sbin/useradd", "-M", "-s", "/usr/sbin/nologin", "test"] in box.ran
    share = client.post("/api/v1/storage/shares", headers=headers, json={
        "name": "Family", "protocol": "smb", "pool_id": "p1", "folder": "Family", "new_folder": True,
        "smb_permissions": {"test": "write"}})
    assert share.status_code == 200, (share.get_json(), box.denied)
    assert any(a[:2] == ["/usr/bin/tee", "-a"] for a in box.ran)
    gone = client.delete("/api/v1/users", json={"username": "test"}, headers=headers)
    assert gone.status_code == 200, (gone.get_json(), box.denied)
    assert box.denied == []


KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl tim@laptop"
DROPIN = "/etc/ssh/sshd_config.d/00-alvaos-security.conf"


def test_ssh_settings_pass_the_helper_and_keep_admin_keys_to_root(backend, box):  # noqa: F811
    client, headers = admin(backend)
    res = client.post("/api/v1/system/ssh", headers=headers,
                      json={"enabled": True, "port": 2222, "password_login": False, "keys": [KEY]})
    assert res.status_code == 200, (res.get_json(), box.denied)
    assert box.denied == []
    config = box.system.files[DROPIN].decode()
    assert "PermitRootLogin yes" in config and "Port 2222" in config and "PasswordAuthentication no" in config
    # People keep their own keys; the admin keys exist for root only.
    assert "AuthorizedKeysFile .ssh/authorized_keys .ssh/authorized_keys2 /etc/ssh/alvaos-authorized-keys-%u" in config
    assert box.system.files["/etc/ssh/alvaos-authorized-keys-root"].decode() == KEY + "\n"
    names = [os.path.basename(a[0]) + " " + " ".join(a[1:]) for a in box.ran]
    assert names.index("sshd -t") < names.index("systemctl reload ssh")
    assert res.get_json()["keys"] == [KEY] and res.get_json()["port"] == 2222


def test_ssh_settings_that_sshd_refuses_are_put_back(backend, box):  # noqa: F811
    client, headers = admin(backend)
    old = b"PermitRootLogin no\nPasswordAuthentication yes\nPermitEmptyPasswords no\n"
    box.system.files[DROPIN] = old
    box.fail["sshd"] = (255, "/etc/ssh/sshd_config line 3: Bad configuration option")
    res = client.post("/api/v1/system/ssh", headers=headers, json={"enabled": True, "port": 2222})
    assert res.status_code == 400 and "nothing was changed" in res.get_json()["error"]
    assert "Bad configuration option" in res.get_json()["detail"]
    assert box.system.files[DROPIN] == old
    assert not any(os.path.basename(a[0]) == "systemctl" for a in box.ran)
    assert box.denied == []


def test_ssh_settings_that_lock_everyone_out_are_refused(backend, box):  # noqa: F811
    client, headers = admin(backend)
    res = client.post("/api/v1/system/ssh", headers=headers, json={"enabled": True, "password_login": False, "keys": []})
    assert res.status_code == 400 and "key" in res.get_json()["error"]
    assert DROPIN not in box.system.files and box.ran == [a for a in box.ran if os.path.basename(a[0]) == "cat"]


def test_admin_keys_of_the_first_ssh_settings_are_carried_over(backend, box):  # noqa: F811
    client, headers = admin(backend)
    box.system.files["/etc/ssh/alvaos-admin-authorized-keys"] = (KEY + "\n").encode()
    box.system.files[DROPIN] = b"PermitRootLogin yes\nAuthorizedKeysFile /etc/ssh/alvaos-admin-authorized-keys\n"
    assert client.get("/api/v1/system/ssh", headers=headers).get_json()["keys"] == [KEY]
    res = client.post("/api/v1/system/ssh", headers=headers, json={"enabled": True})
    assert res.status_code == 200, (res.get_json(), box.denied)
    assert box.system.files["/etc/ssh/alvaos-authorized-keys-root"].decode() == KEY + "\n"
