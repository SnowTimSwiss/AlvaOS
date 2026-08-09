#!/usr/bin/env python3
"""
CI guardrail: the Web UI must not depend on anything it cannot serve itself.

AlvaOS is routinely run on a LAN with no internet access. Any stylesheet, script,
font or image pulled from a third-party host simply fails to load there, so the
UI has to ship every runtime asset locally.

Links a user clicks (documentation, the project repository) are fine - only
resources the browser fetches automatically are rejected.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"

# Attributes whose URL the browser loads on its own.
LOADING_ATTRS = ("src", "href")

# href on <a> is a navigation target, not a fetched asset.
ANCHOR_RE = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
ASSET_RE = re.compile(
    r"""<(?!a\b)([a-zA-Z][\w-]*)\b[^>]*?\b(src|href)\s*=\s*["'](https?://[^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)
# url(...) inside CSS, and bare fetches of remote assets from JS.
CSS_URL_RE = re.compile(r"""url\(\s*["']?(https?://[^)"']+)""", re.IGNORECASE)
JS_IMPORT_RE = re.compile(
    r"""(?:import\s+[^;]*?from\s*|importScripts\s*\(\s*)["'](https?://[^"']+)["']""",
    re.IGNORECASE,
)


def scan(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    problems: list[str] = []

    for match in ASSET_RE.finditer(text):
        tag, attr, url = match.group(1), match.group(2), match.group(3)
        line = text[: match.start()].count("\n") + 1
        problems.append(f"{path.relative_to(ROOT)}:{line}: <{tag} {attr}=\"{url}\">")

    for regex, label in ((CSS_URL_RE, "css url()"), (JS_IMPORT_RE, "remote import")):
        for match in regex.finditer(text):
            line = text[: match.start()].count("\n") + 1
            problems.append(
                f"{path.relative_to(ROOT)}:{line}: {label} -> {match.group(1)}"
            )

    return problems


def main() -> int:
    if not FRONTEND.is_dir():
        print(f"frontend directory not found: {FRONTEND}", file=sys.stderr)
        return 1

    problems: list[str] = []
    for path in sorted(FRONTEND.rglob("*")):
        if path.suffix.lower() in (".html", ".css", ".js") and path.is_file():
            problems.extend(scan(path))

    if problems:
        print("The Web UI must not load assets from external hosts:\n")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\nBundle the asset under frontend/ and reference it with a relative "
            "path instead. AlvaOS has to work without internet access."
        )
        return 1

    print("OK: the Web UI loads no external assets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
