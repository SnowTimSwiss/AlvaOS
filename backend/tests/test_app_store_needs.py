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
