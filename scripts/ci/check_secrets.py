#!/usr/bin/env python3
"""Fail when a tracked file holds something that looks like a secret.

Only the shapes of well-known keys and tokens are checked, so this is a
safety net for mistakes, not a full secret scanner. Files that contain such
text on purpose (test fixtures) are listed in ALLOWED.
"""

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

PATTERNS = {
    'private key': re.compile(r'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----'),
    'GitHub token': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}\b'),
    'AWS access key': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    'Slack token': re.compile(r'\bxox[abposr]-[A-Za-z0-9-]{10,}'),
}

ALLOWED = {
    # The authorized-keys check must refuse a private key; the test feeds it one.
    'backend/tests/test_priv_policy.py',
}


def tracked_files():
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True).stdout
    return [name for name in out.decode('utf-8').split('\0') if name]


def main():
    findings = []
    for name in tracked_files():
        if name in ALLOWED:
            continue
        data = (ROOT / name).read_bytes()
        if b'\0' in data:
            continue
        text = data.decode('utf-8', errors='replace')
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count('\n', 0, match.start()) + 1
                findings.append(f'{name}:{line}: {label}')
    for finding in findings:
        print(finding)
    if findings:
        print('Remove the secret, or add the file to ALLOWED if the text is a fixture.')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
