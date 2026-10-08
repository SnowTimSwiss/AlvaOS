"""Checks on our systemd units that only show on a real machine otherwise.

ReadWritePaths= makes systemd bind-mount the folder when it sets up the sandbox.
If the folder is missing, the service does not start (status 226/NAMESPACE), and an
ExecStartPre= that would make it fails the same way, because it runs in the sandbox, too.
A folder is only safe there if systemd makes it (StateDirectory=) or the path may be missing ("-").
"""

import configparser
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UNITS = sorted(glob.glob(os.path.join(ROOT, 'scripts', '*.service')) + glob.glob(os.path.join(ROOT, 'backend', '*.service')))


def _service(path):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    with open(path) as f:
        parser.read_file(f)
    return parser['Service'] if parser.has_section('Service') else {}


def test_there_are_units_to_check():
    assert any(u.endswith('alvaos-link.service') for u in UNITS)


def test_a_writable_folder_in_the_sandbox_exists_before_the_sandbox_does():
    for path in UNITS:
        service = _service(path)
        made = ['/var/lib/' + d for d in str(service.get('StateDirectory', '')).split()]
        made += ['/var/log/' + d for d in str(service.get('LogsDirectory', '')).split()]
        for entry in str(service.get('ReadWritePaths', '')).split():
            if entry.startswith('-'):
                continue
            assert any(entry == m or entry.startswith(m + '/') for m in made), \
                f'{os.path.basename(path)}: ReadWritePaths={entry} is not made by systemd; use StateDirectory= or "-{entry}"'
