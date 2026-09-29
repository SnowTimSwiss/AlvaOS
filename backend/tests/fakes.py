"""Test doubles shared by several test modules."""

import os
import subprocess

import priv_policy as p


class FakeSystem(p.System):
    """Deterministic stand-in for the real system the privilege policy inspects."""

    def __init__(self, users=None, groups=None, files=None, links=None, system_disks=('sda',)):
        self.users = {'root': 0, 'alvaos': 998, 'nobody': 65534, 'tim': 1000, 'anna': 1001}
        self.users.update(users or {})
        self.groups = {'root': 0, 'sudo': 27, 'docker': 999, 'alvaos': 998, 'share_media': 1002}
        self.groups.update(groups or {})
        self.files = files or {}
        self.links = links or {}
        self._system_disks = set(system_disks)

    def system_disks(self):
        return self._system_disks

    def uid_of(self, user):
        return self.users.get(user)

    def gid_of(self, group):
        return self.groups.get(group)

    def realpath(self, path):
        for link, target in self.links.items():
            if path == link or path.startswith(link + '/'):
                return target + path[len(link):]
        return os.path.normpath(path)

    def timezone_exists(self, tz):
        return tz in {'Europe/Zurich', 'UTC'}

    def read_file(self, path):
        return self.files.get(path, b'')


class FakeBtrfsRunner:
    """Stands in for run_sudo_command: simulates btrfs subvolumes in memory.

    Every command is also checked against the privilege policy, because a
    command the helper would deny is a runtime failure on a real NAS.
    """

    def __init__(self, subvolumes=(), system=None):
        self.subvolumes = set(subvolumes)
        self.dirs = set()
        self.calls = []
        self.denied = []
        self.sent = {}
        self.fail = set()   # substrings of commands that should fail
        self.system = system or FakeSystem()

    def exists(self, path):
        path = os.path.normpath(path)
        return path in self.subvolumes or path in self.dirs

    def _result(self, cmd, rc=0, out="", err=""):
        return subprocess.CompletedProcess(cmd, rc, out, err)

    def __call__(self, cmd, timeout=30, extra_env=None, input=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        try:
            p.validate(cmd, self.system)
        except p.PolicyError as exc:
            self.denied.append((cmd, str(exc)))
            return self._result(cmd, 126, "", f"alvaos-priv: denied: {exc}"), f"denied: {exc}"
        joined = " ".join(cmd)
        if any(marker in joined for marker in self.fail):
            return self._result(cmd, 1, "", "simulated failure"), "simulated failure"

        name = os.path.basename(cmd[0])
        args = cmd[1:]
        if name == "mkdir":
            self.dirs.add(os.path.normpath(args[-1]))
        elif name == "mv":
            src, dst = map(os.path.normpath, args)
            for bucket in (self.subvolumes, self.dirs):
                if src in bucket:
                    bucket.discard(src)
                    bucket.add(dst)
        elif name == "btrfs":
            if args[:2] == ["subvolume", "show"]:
                ok = os.path.normpath(args[2]) in self.subvolumes
                return self._result(cmd, 0 if ok else 1), (None if ok else "not a subvolume")
            if args[:2] == ["subvolume", "create"]:
                self.subvolumes.add(os.path.normpath(args[2]))
            elif args[:2] == ["subvolume", "delete"]:
                self.subvolumes.discard(os.path.normpath(args[2]))
            elif args[:2] == ["subvolume", "snapshot"]:
                src, dst = [a for a in args[2:] if a != "-r"]
                if os.path.normpath(src) not in self.subvolumes:
                    return self._result(cmd, 1, "", "source is not a subvolume"), "source is not a subvolume"
                self.subvolumes.add(os.path.normpath(dst))
            elif args[0] == "send":
                self.sent[args[2]] = os.path.basename(args[3])
            elif args[0] == "receive":
                name_sent = self.sent.get(args[2], "received")
                self.subvolumes.add(os.path.join(os.path.normpath(args[3]), name_sent))
        return self._result(cmd), None
