"""The installer puts the same things on a fresh NAS as the update package."""

import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def read(path):
    with open(os.path.join(ROOT, path)) as f:
        return f.read()


def test_a_fresh_install_has_every_package_the_update_package_needs():
    depends = re.search(r"^Depends: (.*)$", read("scripts/package/build-deb.sh"), re.M).group(1)
    needed = {d.strip().split()[0] for d in depends.split(",")} - {"systemd"}
    script = read("installer/install-system.sh")
    first = script.index("linux-image-amd64")
    installed = set(script[first:script.index(">>", first)].replace("\\", " ").split())
    assert needed <= installed, sorted(needed - installed)


def test_the_installer_image_carries_the_files_app_and_its_service():
    build = read("installer/build.sh")
    assert 'cp -r "${SCRIPT_DIR}/../frontend/"*' in build          # files-app/ is a folder
    assert "scripts/alvaos-files.service" in build
    assert "scripts/alvaos-vm@.service" in build
    assert "alvaos-vm@.service" in read("installer/install-system.sh")
    assert "useradd -r -g alvaos" in read("installer/install-system.sh")
    assert "alvaos-files.service" in read("installer/install-system.sh")
