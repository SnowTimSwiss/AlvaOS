"""A graphics card for an app: one switch (gpu_manager.app_plan, AppStore.set_gpu)."""

import json

import pytest
import yaml

import gpu_manager as gm
import priv_policy
from test_app_reconfigure import DETAILS, store  # noqa: F401 - the fixture

SPEC = {"service": "jellyfin", "vendors": ["intel", "amd", "nvidia"], "after": "Turn it on in Jellyfin."}
OLLAMA = {"service": "ollama", "vendors": ["nvidia", "amd"], "images": {"amd": "ollama/ollama:rocm"}, "kfd": True}
GROUPS = {"render": 105, "video": 44}


def card(vendor, driver, render="/dev/dri/renderD128", model="Card"):
    return {"vendor": vendor, "vendor_name": gm.VENDOR_NAMES[vendor], "model": model, "driver": driver,
            "render_node": render}


def toolkit(have):
    return lambda packages: {p: have for p in packages}


def test_intel_and_amd_cards_come_in_as_dev_dri_with_their_groups():
    plan, why = gm.app_plan(SPEC, [card("intel", "i915")], GROUPS)
    assert why == "" and plan["devices"] == ["/dev/dri:/dev/dri"] and plan["group_add"] == ["105", "44"]
    assert plan["card"] == "Intel Card"
    plan, _ = gm.app_plan(OLLAMA, [card("amd", "amdgpu")], GROUPS)
    assert plan["devices"] == ["/dev/dri:/dev/dri", "/dev/kfd:/dev/kfd"] and plan["image"] == "ollama/ollama:rocm"


def test_nvidia_cards_need_the_driver_and_the_container_toolkit():
    nvidia = [card("nvidia", "nvidia")]
    plan, _ = gm.app_plan(SPEC, nvidia, GROUPS, installed=toolkit(True))
    assert plan["deploy"]["resources"]["reservations"]["devices"][0]["driver"] == "nvidia"
    assert "devices" not in plan
    plan, why = gm.app_plan(SPEC, nvidia, GROUPS, installed=toolkit(False))
    assert plan is None and "Settings › Graphics" in why
    plan, why = gm.app_plan(SPEC, [card("nvidia", "nouveau")], GROUPS, installed=toolkit(True))
    assert plan is None and "not ready" in why


def test_the_app_gets_the_first_card_it_prefers_and_says_when_there_is_none():
    cards = [card("intel", "i915"), card("nvidia", "nvidia")]
    plan, _ = gm.app_plan(OLLAMA, cards, GROUPS, installed=toolkit(True))
    assert plan["vendor"] == "nvidia"          # Ollama cannot use the Intel one
    plan, why = gm.app_plan(OLLAMA, [card("intel", "i915")], GROUPS)
    assert plan is None and "NVIDIA, AMD" in why
    assert gm.app_plan(None, [], GROUPS)[0] is None


def test_the_plan_goes_into_the_compose_file_and_passes_the_helper():
    compose = {"services": {"jellyfin": {"image": "jellyfin/jellyfin", "environment": ["TZ=UTC"]},
                            "other": {"image": "x"}}}
    plan, _ = gm.app_plan(SPEC, [card("intel", "i915")], GROUPS)
    gm.apply_plan(compose, plan)
    assert compose["services"]["jellyfin"]["devices"] == ["/dev/dri:/dev/dri"]
    assert "devices" not in compose["services"]["other"]
    priv_policy.check_compose(yaml.safe_dump(compose).encode())
    plan, _ = gm.app_plan(OLLAMA, [card("amd", "amdgpu")], GROUPS)
    compose = {"services": {"ollama": {"image": "ollama/ollama:latest"}}}
    priv_policy.check_compose(yaml.safe_dump(gm.apply_plan(compose, plan)).encode())
    plan, _ = gm.app_plan(SPEC, [card("nvidia", "nvidia")], GROUPS, installed=toolkit(True))
    compose = {"services": {"jellyfin": {"image": "j", "environment": ["TZ=UTC"]}}}
    gm.apply_plan(compose, plan)
    assert compose["services"]["jellyfin"]["environment"] == {
        "TZ": "UTC", "NVIDIA_VISIBLE_DEVICES": "all", "NVIDIA_DRIVER_CAPABILITIES": "compute,video,utility"}
    priv_policy.check_compose(yaml.safe_dump(compose).encode())
    with pytest.raises(priv_policy.PolicyError):
        priv_policy.check_compose(b"services:\n  a:\n    devices: ['/dev/sda:/dev/sda']\n")


@pytest.fixture
def gpu_store(store, monkeypatch):  # noqa: F811 - the fixture
    monkeypatch.setattr(store, "get_app_details", lambda app_id: ({**DETAILS, "gpu": SPEC}, None))
    monkeypatch.setattr(gm, "detect", lambda *a, **k: [card("intel", "i915", model="UHD 770")])
    monkeypatch.setattr(gm, "_group_ids", lambda *a: GROUPS)
    return store


def test_the_switch_recreates_the_app_with_the_card_and_keeps_it_on_updates(gpu_store):
    ok, error = gpu_store.set_gpu("jellyfin", True)
    assert ok, error
    call = gpu_store.docker_manager.calls[-1]
    assert call["compose"]["services"]["jellyfin"]["devices"] == ["/dev/dri:/dev/dri"]
    assert call["compose"]["services"]["jellyfin"]["environment"]["TZ"] == "Europe/Zurich"
    assert gpu_store._load_apps_state()["jellyfin"]["gpu"] is True
    overview = gpu_store.get_installed_apps()[0]["gpu"]
    assert overview == {"possible": True, "on": True, "card": "Intel UHD 770", "why_not": "",
                        "after": "Turn it on in Jellyfin."}
    # Folders and ports later: the card stays.
    gpu_store.reconfigure_app("jellyfin", {8096: 8100}, {})
    assert gpu_store.docker_manager.calls[-1]["compose"]["services"]["jellyfin"]["devices"] == ["/dev/dri:/dev/dri"]
    ok, _ = gpu_store.set_gpu("jellyfin", False)
    assert ok and "devices" not in gpu_store.docker_manager.calls[-1]["compose"]["services"]["jellyfin"]
    assert gpu_store._load_apps_state()["jellyfin"]["gpu"] is False


def test_no_card_no_switch(gpu_store, monkeypatch):
    monkeypatch.setattr(gm, "detect", lambda *a, **k: [])
    ok, error = gpu_store.set_gpu("jellyfin", True)
    assert not ok and "no graphics card" in error
    monkeypatch.setattr(gpu_store, "get_app_details", lambda app_id: (dict(DETAILS), None))
    assert gpu_store.set_gpu("jellyfin", True) == (False, "This app cannot use a graphics card.")
    assert gpu_store.get_installed_apps()[0]["gpu"] == {"possible": False}


def test_an_app_whose_card_went_missing_still_starts_without_it(gpu_store, monkeypatch):
    gpu_store.set_gpu("jellyfin", True)
    monkeypatch.setattr(gm, "detect", lambda *a, **k: [])
    gpu_store.reconfigure_app("jellyfin", {}, {})
    assert gpu_store.get_install_status()["status"] == "success"
    assert "devices" not in gpu_store.docker_manager.calls[-1]["compose"]["services"]["jellyfin"]
    assert json.dumps(gpu_store.get_installed_apps()[0]["gpu"]).count("no graphics card") == 1


def test_the_catalog_names_services_that_exist():
    catalog = json.load(open(__file__.rsplit("/backend/", 1)[0] + "/apps/catalog.json"))
    with_gpu = {k: v["gpu"] for k, v in catalog.items() if "gpu" in v}
    assert set(with_gpu) == {"jellyfin", "immich", "ollama"}
    for app_id, spec in with_gpu.items():
        assert spec["service"] in catalog[app_id]["docker_compose"]["services"]
        assert set(spec["vendors"]) <= set(gm.GOOD_DRIVERS)
