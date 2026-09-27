#!/usr/bin/env python3
"""
CI guardrail for privilege separation.

- The canonical sudoers file grants the alvaos user exactly one command: the
  alvaos-priv helper. Any other NOPASSWD rule is an error.
- Every command path the backend declares in common.CMD must be known to the
  helper's policy (otherwise it would be denied at runtime).
- Backend code must not bypass the helper: no hand-built "sudo" command lines
  (except the helper's named operations), no shell=True, no "bash -c"/"-lc".
- Installer/package scripts must install the canonical sudoers file and keep
  /opt/alvaos root-owned.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SUDOERS_FILE = ROOT / "scripts" / "sudoers.alvaos"
HELPER_PATH = "/opt/alvaos/bin/alvaos-priv"

sys.path.insert(0, str(BACKEND))


def sudoers_rules(path: Path):
    rules = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("Defaults"):
            continue
        rules.append(line)
    return rules


def backend_cmd_paths():
    tree = ast.parse((BACKEND / "common.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CMD" for t in node.targets
        ):
            return [v.value for v in node.value.values if isinstance(v, ast.Constant)]
    return []


def python_sources():
    for path in sorted(BACKEND.glob("*.py")):
        if path.name == "priv_policy.py":
            continue  # the policy names the patterns it forbids
        yield path
    yield BACKEND / "alvaos-priv"


def main():
    errors = []

    rules = sudoers_rules(SUDOERS_FILE)
    expected = f"alvaos ALL=(root) NOPASSWD: {HELPER_PATH}"
    if rules != [expected]:
        errors.append(
            "scripts/sudoers.alvaos must contain exactly one rule:\n    "
            + expected + "\n  found:\n    " + "\n    ".join(rules or ["(none)"])
        )

    import priv_policy

    unknown = []
    for cmd in backend_cmd_paths():
        try:
            priv_policy.resolve_binary(cmd)
        except priv_policy.PolicyError:
            unknown.append(cmd)
    # Read-only commands the backend runs without the helper.
    unprivileged_ok = {"/usr/bin/lsblk"}
    unknown = [c for c in unknown if c not in unprivileged_ok]
    if unknown:
        errors.append("common.CMD has commands the privilege policy does not know: " + ", ".join(unknown))

    shell_patterns = [
        (re.compile(r"shell\s*=\s*True"), "shell=True"),
        (re.compile(r"""["'](?:/usr)?/bin/(?:ba)?sh["']\s*,\s*["']-l?c["']"""), "bash -c"),
        (re.compile(r"""\[\s*["'](?:/usr/bin/)?sudo["']"""), "hand-built sudo command"),
    ]
    # Allowed exceptions: the named helper operations and the root-only watchdog.
    allowed_sudo = {
        "common.py",           # build_privileged_cmd itself
        "power_ups_manager.py",  # sudo alvaos-priv write-sysfs
        "update_manager.py",   # sudo alvaos-priv apply-update
        "buddy_vault.py",      # sudo alvaos-priv vault-open / vault-close
        "watchdog_manager.py",  # runs as root; sudo is a no-op there
    }
    for path in python_sources():
        text = path.read_text(encoding="utf-8")
        for pattern, label in shell_patterns:
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                line = text.splitlines()[line_no - 1]
                if label == "hand-built sudo command" and path.name in allowed_sudo:
                    continue
                # `sudo -n -l` only lists the caller's own rules (diagnostics page).
                if label == "hand-built sudo command" and "'-l'" in line:
                    continue
                # `docker exec ... /bin/sh -lc` runs inside a container, not on the host.
                if label == "bash -c" and path.name == "docker_manager.py" and "container_id" in line:
                    continue
                errors.append(f"{path.relative_to(ROOT)}:{line_no}: {label} bypasses the privilege helper")

    for script in (
        ROOT / "scripts" / "setup_sudoers.sh",
        ROOT / "installer" / "install-system.sh",
        ROOT / "scripts" / "package" / "build-deb.sh",
    ):
        text = script.read_text(encoding="utf-8")
        if "sudoers.alvaos" not in text:
            errors.append(f"{script.relative_to(ROOT)} does not reference canonical sudoers.alvaos")
        if "SUDOERS_EOF" in text:
            errors.append(f"{script.relative_to(ROOT)} still contains an inline sudoers heredoc")
        if re.search(r"chown -R alvaos:alvaos[^\n]*/opt/alvaos", text):
            errors.append(
                f"{script.relative_to(ROOT)} hands /opt/alvaos to the alvaos user; "
                "code that runs as root must stay root-owned"
            )

    for script in (ROOT / "installer" / "install-system.sh", ROOT / "scripts" / "package" / "build-deb.sh"):
        if "alvaos-priv" not in script.read_text(encoding="utf-8"):
            errors.append(f"{script.relative_to(ROOT)} does not install the alvaos-priv helper")

    if errors:
        print("Privileged command check failed:")
        for msg in errors:
            print(f"- {msg}")
        return 1
    print("Privileged command check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
