"""AlvaOS Hub: which Hub apps are on and who sees them (backend/hub_apps.py)."""

import json

import pytest

import hub_apps

PEOPLE = ["anna", "ben"]


def test_without_settings_every_app_is_on_for_everyone(tmp_path):
    path = str(tmp_path / "hub.json")
    settings = hub_apps.load(path)
    assert settings["apps"]["files"] == {"enabled": True, "people": None}
    assert [a["id"] for a in hub_apps.visible("anna", settings=settings)] == ["files", "photos", "calendar", "contacts"]
    assert settings["apps"]["chat"]["enabled"] is False                     # needs an AI service first


def test_apps_can_be_turned_off_or_given_to_some_people(tmp_path):
    path = str(tmp_path / "hub.json")
    settings, problem = hub_apps.save({"apps": {"photos": {"people": ["anna"]}}}, PEOPLE, path)
    assert problem == "" and settings["apps"]["photos"]["people"] == ["anna"]
    assert [a["id"] for a in hub_apps.visible("anna", settings=settings)] == ["files", "photos", "calendar", "contacts"]
    assert [a["id"] for a in hub_apps.visible("ben", settings=settings)] == ["files", "calendar", "contacts"]
    assert hub_apps.allowed("photos", "admin", "admin", settings)          # the admin account sees every app that is on
    settings, _ = hub_apps.save({"apps": {"files": {"enabled": False}}}, PEOPLE, path)
    assert [a["id"] for a in hub_apps.visible("anna", settings=settings)] == ["calendar", "contacts"]  # Photos needs Files
    assert json.loads(open(path).read())["apps"]["files"]["enabled"] is False
    settings, _ = hub_apps.save({"apps": {"files": {"enabled": True}, "photos": {"people": None}}}, PEOPLE, path)
    assert len(hub_apps.visible("ben", settings=settings)) == 4


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


POOLS = {"ssd-1": {"name": "fast", "mount_point": "/mnt/alvaos/fast"},
         "hdd-1": {"name": "big", "mount_point": "/mnt/alvaos/big"}}
SHARES = ["Family", "Media"]


def test_where_apps_keep_data_is_chosen_and_checked(tmp_path):
    path = str(tmp_path / "hub.json")
    settings, problem = hub_apps.save({"apps": {"photos": {"location": {"mode": "pool", "pool_id": "hdd-1", "limit_gb": 500},
                                                          "libraries": ["Family"]}},
                                       "storage": {"cache_pool": "ssd-1"}}, PEOPLE, path, pools=POOLS, shares=SHARES)
    assert problem == ""
    assert settings["apps"]["photos"]["location"] == {"mode": "pool", "pool_id": "hdd-1", "limit_gb": 500.0}
    assert settings["apps"]["photos"]["libraries"] == ["Family"] and settings["storage"]["cache_pool"] == "ssd-1"
    assert hub_apps.load(path)["apps"]["photos"]["location"]["pool_id"] == "hdd-1"
    for change, message in (
            ({"apps": {"photos": {"location": {"mode": "pool", "pool_id": "nope"}}}}, "Choose a storage pool"),
            ({"apps": {"photos": {"location": {"mode": "pool", "pool_id": "hdd-1", "limit_gb": -3}}}}, "number of GB"),
            ({"apps": {"photos": {"location": {"mode": "cloud"}}}}, "personal folder or a storage pool"),
            ({"apps": {"files": {"location": {"mode": "personal"}}}}, "keeps no data"),
            ({"apps": {"photos": {"libraries": ["Secret"]}}}, "shared folders that exist"),
            ({"apps": {"files": {"libraries": []}}}, "reads no shared folders"),
            ({"storage": {"cache_pool": "usb"}}, "pool for the cache")):
        settings_after, problem = hub_apps.save(change, PEOPLE, path, pools=POOLS, shares=SHARES)
        assert settings_after is None and message in problem, change
    settings, _ = hub_apps.save({"apps": {"photos": {"location": {"mode": "personal"}}}, "storage": {"cache_pool": ""}},
                                PEOPLE, path, pools=POOLS, shares=SHARES)
    assert settings["apps"]["photos"]["location"] == {"mode": "personal"} and settings["storage"]["cache_pool"] == ""


def test_each_persons_own_photos_folder(tmp_path):
    path = str(tmp_path / "hub.json")
    shares = {"1": {"name": "anna", "personal_for": "anna"}, "2": {"name": "ben-photos", "hub_for": "ben"},
              "3": {"name": "Family"}}
    settings = hub_apps.load(path)
    assert hub_apps.own_folder("photos", "anna", shares, settings) == ("anna", "Photos")
    assert hub_apps.own_folder("photos", "ben", shares, settings) is None          # no personal folder yet
    assert hub_apps.own_folder("files", "anna", shares, settings) is None          # Files keeps nothing
    settings, _ = hub_apps.save({"apps": {"photos": {"location": {"mode": "pool", "pool_id": "hdd-1"}}}}, PEOPLE, path,
                                pools=POOLS, shares=SHARES)
    assert hub_apps.own_folder("photos", "ben", shares, settings) == ("ben-photos", "")
    assert hub_apps.own_folder("photos", "anna", shares, settings) is None


def test_the_cache_goes_to_a_mounted_pool_or_stays_on_the_system_disk(tmp_path, monkeypatch):
    path = str(tmp_path / "hub.json")
    settings, _ = hub_apps.save({"storage": {"cache_pool": "ssd-1"}}, PEOPLE, path, pools=POOLS, shares=SHARES)
    monkeypatch.setattr(hub_apps.os.path, "ismount", lambda p: p == "/mnt/alvaos/fast")
    assert hub_apps.cache_dir(POOLS, settings) == "/mnt/alvaos/fast/.alvaos-hub"
    monkeypatch.setattr(hub_apps.os.path, "ismount", lambda p: False)               # pool not there: system disk
    assert hub_apps.cache_dir(POOLS, settings) is None
    assert hub_apps.cache_dir(POOLS, hub_apps.load(str(tmp_path / "none.json"))) is None


CATALOG = {
    "jellyfin": {"name": "Jellyfin", "config_schema": {"ports": [{"internal": 8096, "external": 8096, "description": "Web UI"}]}},
    "pihole": {"name": "Pi-hole", "config_schema": {"webui_path": "/admin/", "ports": [
        {"internal": 53, "external": 53, "description": "DNS"}, {"internal": 80, "external": 8053, "description": "Web UI"}]}},
    "dnsonly": {"name": "DNS only", "config_schema": {"ports": [{"internal": 53, "external": 5353, "description": "DNS"}]}},
    "evil": {"name": "Evil", "config_schema": {"webui_path": "/x\"><script>", "ports": [{"internal": 1, "external": 9, "description": "Web UI"}]}},
}
INSTALLED = {"jellyfin": {"port_mappings": {"8096": 18096}}, "pihole": {}, "dnsonly": {}, "evil": {},
             "mine": {"source": "custom_compose"}}


def test_store_apps_with_a_page_and_the_port_chosen_at_install():
    apps = hub_apps.store_apps(INSTALLED, CATALOG)
    assert apps == [{"id": "jellyfin", "name": "Jellyfin", "port": 18096, "path": "/"},
                    {"id": "pihole", "name": "Pi-hole", "port": 8053, "path": "/admin/"}]


def test_store_tiles_are_off_until_shown_and_follow_who_sees_them(tmp_path):
    path = str(tmp_path / "hub.json")
    apps = hub_apps.store_apps(INSTALLED, CATALOG)
    ids = [a["id"] for a in apps]
    settings = hub_apps.load(path)
    assert hub_apps.store_tiles("anna", settings=settings, apps=apps) == []
    settings, problem = hub_apps.save({"store": {"jellyfin": {"shown": True}, "pihole": {"shown": True, "people": ["tim"]}}},
                                      ["anna", "tim"], path=path, store_ids=ids)
    assert not problem
    assert [t["id"] for t in hub_apps.store_tiles("anna", settings=settings, apps=apps)] == ["jellyfin"]
    assert [t["id"] for t in hub_apps.store_tiles("tim", settings=settings, apps=apps)] == ["jellyfin", "pihole"]
    assert [t["id"] for t in hub_apps.store_tiles("root", "admin", settings=settings, apps=apps)] == ["jellyfin", "pihole"]
    assert hub_apps.load(path)["store"]["pihole"] == {"shown": True, "people": ["tim"]}
    for bad in ({"dnsonly": {"shown": True}}, {"jellyfin": {"people": ["nobody"]}}, {"jellyfin": {"people": []}}):
        assert hub_apps.save({"store": bad}, ["anna", "tim"], path=path, store_ids=ids)[0] is None
