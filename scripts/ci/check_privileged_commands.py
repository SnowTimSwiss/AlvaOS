#!/usr/bin/env python3
"""
CI guardrail:
- Verify canonical sudoers rules cover privileged backend command paths.
- Verify critical exact sudoers rules exist.
- Verify installer/setup/package scripts reference the canonical sudoers file.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SUDOERS_FILE = ROOT / "scripts" / "sudoers.alvaos"
BACKEND_FILE = ROOT / "backend" / "alvaos-backend.py"


def parse_sudoers(path: Path):
    full_rules = set()
    binaries = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        marker = "NOPASSWD:"
        if marker not in line:
            continue
        cmd = line.split(marker, 1)[1].strip()
        if not cmd:
            continue
        full_rules.add(cmd)
        binaries.add(cmd.split()[0].strip())
    return full_rules, binaries


def parse_backend_cmd_paths(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CMD" and isinstance(node.value, ast.Dict):
                    for key_node, value_node in zip(node.value.keys, node.value.values):
                        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                            values.add(value_node.value.strip())
    return sorted(v for v in values if v)


def fail(messages):
    print("Privileged command check failed:")
    for msg in messages:
        print(f"- {msg}")
    return 1


def main():
    errors = []

    if not SUDOERS_FILE.exists():
        return fail([f"Missing canonical sudoers file: {SUDOERS_FILE}"])
    if not BACKEND_FILE.exists():
        return fail([f"Missing backend file: {BACKEND_FILE}"])

    full_rules, binaries = parse_sudoers(SUDOERS_FILE)
    backend_cmd_paths = parse_backend_cmd_paths(BACKEND_FILE)

    missing_backend = [cmd for cmd in backend_cmd_paths if cmd not in binaries]
    if missing_backend:
        errors.append(
            "Canonical sudoers is missing backend command binaries: "
            + ", ".join(missing_backend)
        )

    required_full_rules = [
        "/usr/bin/systemctl restart docker",
        "/usr/bin/systemctl restart nfs-kernel-server",
        "/usr/bin/mv",
        "/bin/mv",
        "/usr/bin/chown",
        "/bin/chown",
    ]
    missing_full = [rule for rule in required_full_rules if rule not in full_rules]
    if missing_full:
        errors.append(
            "Canonical sudoers is missing required exact rules: "
            + ", ".join(missing_full)
        )

    scripts_expected_to_reference_template = [
        ROOT / "scripts" / "setup_sudoers.sh",
        ROOT / "installer" / "install-system.sh",
        ROOT / "scripts" / "package" / "build-deb.sh",
    ]
    for script_path in scripts_expected_to_reference_template:
        text = script_path.read_text(encoding="utf-8")
        if "sudoers.alvaos" not in text:
            errors.append(f"{script_path} does not reference canonical sudoers.alvaos")
        if "SUDOERS_EOF" in text:
            errors.append(f"{script_path} still contains inline sudoers heredoc (SUDOERS_EOF)")

    if errors:
        return fail(errors)

    print("Privileged command check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

