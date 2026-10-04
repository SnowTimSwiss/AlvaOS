"""AlvaOS Hub: which Hub apps are on and who sees them (backend/hub_apps.py)."""

import json

import pytest

import hub_apps

PEOPLE = ["anna", "ben"]


def test_without_settings_every_app_is_on_for_everyone(tmp_path):
    path = str(tmp_path / "hub.json")
    settings = hub_apps.load(path)
    assert settings["apps"]["files"] == {"enabled": True, "people": None}
    assert [a["id"] for a in hub_apps.visible("anna", settings=settings)] == ["files", "photos"]


def test_apps_can_be_turned_off_or_given_to_some_people(tmp_path):
    path = str(tmp_path / "hub.json")
    settings, problem = hub_apps.save({"apps": {"photos": {"people": ["anna"]}}}, PEOPLE, path)
    assert problem == "" and settings["apps"]["photos"]["people"] == ["anna"]
    assert [a["id"] for a in hub_apps.visible("anna", settings=settings)] == ["files", "photos"]
    assert [a["id"] for a in hub_apps.visible("ben", settings=settings)] == ["files"]
    assert hub_apps.allowed("photos", "admin", "admin", settings)          # the admin account sees every app that is on
    settings, _ = hub_apps.save({"apps": {"files": {"enabled": False}}}, PEOPLE, path)
    assert hub_apps.visible("anna", settings=settings) == []                # Photos needs Files
    assert json.loads(open(path).read())["apps"]["files"]["enabled"] is False
    settings, _ = hub_apps.save({"apps": {"files": {"enabled": True}, "photos": {"people": None}}}, PEOPLE, path)
    assert len(hub_apps.visible("ben", settings=settings)) == 2


@pytest.mark.parametrize("change, problem", [
    ({"apps": {"mail": {"enabled": True}}}, "no Hub app"),
    ({"apps": {"files": {"people": ["eve"]}}}, "no person called eve"),
    ({"apps": {"files": {"people": []}}}, "at least one person"),
    ({"apps": {"files": {"people": "anna"}}}, "everyone or a list"),
    ({}, "Nothing to change"),
])
def test_bad_changes_are_refused(tmp_path, change, problem):
    settings, message = hub_apps.save(change, PEOPLE, str(tmp_path / "hub.json"))
    assert settings is None and problem in message


def test_a_broken_settings_file_falls_back_to_everything_on(tmp_path):
    path = tmp_path / "hub.json"
    path.write_text('{"apps": {"files": {"enabled": false, "people": ["anna", "../root", 3]}}, "x": 1}')
    settings = hub_apps.load(str(path))
    assert settings["apps"]["files"] == {"enabled": False, "people": ["anna"]}
    path.write_text("not json")
    assert hub_apps.load(str(path))["apps"]["files"]["enabled"] is True
