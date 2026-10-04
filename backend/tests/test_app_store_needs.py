"""What the store says an app needs before it is installed."""

import json
import os

from app_store import summarize_app_needs

CATALOG = os.path.join(os.path.dirname(__file__), "..", "..", "apps", "catalog.json")


def test_ports_and_folders_are_summarized():
    needs = summarize_app_needs({"config_schema": {
        "ports": [{"external": 8096, "description": "Web UI"}, {"external": 53, "protocol": "udp"},
                  {"external": "x"}, {"external": 70000}, "junk"],
        "volumes": [{"container_path": "/config", "description": "Settings"}, {"container_path": "/media"}],
    }})
    assert needs["ports"] == [{"port": 8096, "protocol": "tcp", "description": "Web UI"},
                              {"port": 53, "protocol": "udp", "description": ""}]
    assert needs["folders"] == ["Settings", "/media"]


def test_apps_without_a_schema_need_nothing():
    assert summarize_app_needs({}) == {"ports": [], "folders": []}
    assert summarize_app_needs({"config_schema": "nonsense"}) == {"ports": [], "folders": []}


def test_every_catalog_app_can_be_summarized():
    with open(CATALOG) as f:
        catalog = json.load(f)
    for app_id, app in catalog.items():
        needs = summarize_app_needs(app)
        assert isinstance(needs["ports"], list) and isinstance(needs["folders"], list), app_id


# ── Where an app may keep its files ──────────────────────────────────────────

from app_store import validate_install_paths  # noqa: E402

POOLS = {"u1": {"name": "main", "mount_point": "/mnt/alvaos/main"},
         "sys": {"name": "system", "mount_point": "/"}}


def test_apps_go_on_a_managed_pool():
    assert validate_install_paths("/mnt/alvaos/main", {}, {}, POOLS) is None
    assert validate_install_paths("/mnt/alvaos/main/", {}, {}, POOLS) is None
    for bad in ("/", "/etc", "/mnt/alvaos/other", "", None):
        assert validate_install_paths(bad, {}, {}, POOLS), bad


def test_your_folders_must_be_inside_a_pool():
    ok = {"/media": "/mnt/alvaos/main/Media", "/photos": "/mnt/alvaos/main"}
    assert validate_install_paths("/mnt/alvaos/main", ok, {}, POOLS) is None
    for host in ("/etc", "/mnt/alvaos/main/../../etc", "/mnt/alvaos/mainx/Media", "relative/path", "/var/lib/docker"):
        assert validate_install_paths("/mnt/alvaos/main", {"/media": host}, {}, POOLS), host
    assert validate_install_paths("/mnt/alvaos/main", {"media": "/mnt/alvaos/main/Media"}, {}, POOLS)
    assert validate_install_paths("/mnt/alvaos/main", ["nope"], {}, POOLS)


def test_ports_must_be_real_ports():
    assert validate_install_paths("/mnt/alvaos/main", {}, {80: 8081}, POOLS) is None
    assert validate_install_paths("/mnt/alvaos/main", {}, {80: 70000}, POOLS)
