#!/usr/bin/env python3
"""
CI guardrail: no app in the catalog may ship a usable placeholder secret.

Apps used to define values like MYSQL_ROOT_PASSWORD=CHANGEME in their compose
definition. Some of those keys were not even exposed in the install form, so
there was no way for a user to replace them and the container came up with a
password anyone could look up in this repository.

A placeholder in the compose block is allowed only when the matching key is
declared 'required' in config_schema.environment, which forces the installer to
collect a real value first.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "apps" / "catalog.json"

PLACEHOLDERS = {"CHANGEME", "CHANGE_ME", "CHANGEME!", "PLEASE_CHANGE"}


def compose_placeholder_keys(app: dict) -> set[str]:
    keys: set[str] = set()
    services = ((app.get("docker_compose") or {}).get("services") or {})
    for service in services.values():
        env = (service or {}).get("environment") or {}
        if isinstance(env, dict):
            pairs = env.items()
        else:
            pairs = (str(item).split("=", 1) for item in env if "=" in str(item))
        for key, value in pairs:
            if str(value).strip().upper() in PLACEHOLDERS:
                keys.add(str(key))
    return keys


def required_schema_keys(app: dict) -> set[str]:
    schema = ((app.get("config_schema") or {}).get("environment") or [])
    return {
        str(entry.get("key", "")).strip()
        for entry in schema
        if entry.get("required") is True and str(entry.get("key", "")).strip()
    }


def main() -> int:
    if not CATALOG.is_file():
        print(f"catalog not found: {CATALOG}", file=sys.stderr)
        return 1

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    problems: list[str] = []

    for app_id, app in catalog.items():
        if not isinstance(app, dict):
            continue
        placeholders = compose_placeholder_keys(app)
        if not placeholders:
            continue
        required = required_schema_keys(app)
        unguarded = sorted(placeholders - required)
        for key in unguarded:
            problems.append(
                f"{app_id}: {key} holds a placeholder but is not a required "
                f"field in config_schema.environment"
            )

    if problems:
        print("Catalog apps would deploy with placeholder secrets:\n")
        for problem in problems:
            print(f"  {problem}")
        print(
            '\nAdd the key to config_schema.environment with "required": true '
            '(and "secret": true for credentials) so the install form collects '
            "a real value."
        )
        return 1

    print("OK: every placeholder secret in the catalog is a required field.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
