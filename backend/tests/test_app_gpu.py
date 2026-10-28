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
    return lambda packages: dict.fromkeys(packages, have)


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
                        "after": "Turn it on in Jellyfin.", "choices": [], "chosen": "", "auto": "Intel UHD 770"}
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


def two_cards():
    igpu = {**card("intel", "i915", "/dev/dri/renderD128", "UHD 770"), "slot": "0000:00:02.0",
            "card_node": "/dev/dri/card0", "integrated": True}
    dgpu = {**card("amd", "amdgpu", "/dev/dri/renderD129", "RX 7600"), "slot": "0000:03:00.0", "card_node": "/dev/dri/card1"}
    return [igpu, dgpu]


def test_with_two_cards_an_app_gets_only_its_own_and_sees_it_as_the_first():
    cards = two_cards()
    plan, _ = gm.app_plan(SPEC, cards, GROUPS)                       # Jellyfin prefers the Intel one
    assert plan["card"] == "Intel UHD 770 (in the processor)"
    assert plan["devices"] == ["/dev/dri/renderD128:/dev/dri/renderD128", "/dev/dri/card0:/dev/dri/card0"]
    plan, _ = gm.app_plan(OLLAMA, cards, GROUPS)                     # Ollama only takes AMD (or NVIDIA)
    assert plan["slot"] == "0000:03:00.0"
    assert plan["devices"] == ["/dev/dri/renderD129:/dev/dri/renderD128", "/dev/dri/card1:/dev/dri/card0",
                               "/dev/kfd:/dev/kfd"]
    priv_policy.check_compose(yaml.safe_dump(gm.apply_plan({"services": {"ollama": {"image": "o"}}}, plan)).encode())


def test_the_person_can_choose_the_card_and_a_missing_one_is_refused():
    cards = two_cards()
    plan, why = gm.app_plan(SPEC, cards, GROUPS, slot="0000:03:00.0")
    assert why == "" and plan["slot"] == "0000:03:00.0" and plan["devices"][0] == "/dev/dri/renderD129:/dev/dri/renderD128"
    plan, why = gm.app_plan(SPEC, cards, GROUPS, slot="0000:09:00.0")
    assert plan is None and "not available" in why
    # An app that cannot use that vendor does not offer it.
    assert [c["slot"] for c in gm.fitting_cards(OLLAMA, cards)] == ["0000:03:00.0"]
    assert [c["slot"] for c in gm.fitting_cards(SPEC, cards)] == ["0000:00:02.0", "0000:03:00.0"]


def test_with_two_nvidia_cards_the_app_gets_one_by_its_number():
    a = {**card("nvidia", "nvidia"), "slot": "0000:01:00.0"}
    b = {**card("nvidia", "nvidia"), "slot": "0000:02:00.0"}
    plan, _ = gm.app_plan(OLLAMA, [a, b], GROUPS, installed=toolkit(True), slot="0000:02:00.0",
                          nvidia_index=lambda slot: {"0000:01:00.0": "0", "0000:02:00.0": "1"}[slot])
    device = plan["deploy"]["resources"]["reservations"]["devices"][0]
    assert device["device_ids"] == ["1"] and "count" not in device
    assert plan["environment"]["NVIDIA_VISIBLE_DEVICES"] == "1"
    assert gm._nvidia_index("0000:02:00.0", lambda: "0, 00000000:01:00.0\n1, 00000000:02:00.0\n") == "1"


def test_the_choice_is_kept_in_the_app_state_and_offered_in_the_overview(gpu_store, monkeypatch):
    monkeypatch.setattr(gm, "detect", lambda *a, **k: two_cards())
    ok, error = gpu_store.set_gpu("jellyfin", True, "0000:03:00.0")
    assert ok, error
    assert gpu_store._load_apps_state()["jellyfin"]["gpu_card"] == "0000:03:00.0"
    assert gpu_store.docker_manager.calls[-1]["compose"]["services"]["jellyfin"]["devices"][0].startswith("/dev/dri/renderD129")
    overview = gpu_store.get_installed_apps()[0]["gpu"]
    assert overview["chosen"] == "0000:03:00.0" and overview["card"] == "AMD RX 7600"
    assert [c["slot"] for c in overview["choices"]] == ["0000:00:02.0", "0000:03:00.0"]
    gpu_store.reconfigure_app("jellyfin", {8096: 8100}, {})            # later changes keep the choice
    assert gpu_store.docker_manager.calls[-1]["compose"]["services"]["jellyfin"]["devices"][0].startswith("/dev/dri/renderD129")
    ok, error = gpu_store.set_gpu("jellyfin", True, "0000:09:00.0")
    assert not ok and "not available" in error


def test_how_busy_the_cards_are_is_read_from_sys_and_nvidia_smi(tmp_path):
    amd = tmp_path / "bus/pci/devices/0000:03:00.0"
    (amd / "hwmon/hwmon2").mkdir(parents=True)
    for name, value in (("gpu_busy_percent", "37"), ("mem_info_vram_used", str(2 * 1024 ** 3)),
                        ("mem_info_vram_total", str(8 * 1024 ** 3))):
        (amd / name).write_text(value + "\n")
    (amd / "hwmon/hwmon2/temp1_input").write_text("61000\n")
    (amd / "hwmon/hwmon2/power1_average").write_text("88000000\n")
    intel = tmp_path / "bus/pci/devices/0000:00:02.0/drm/card0"
    intel.mkdir(parents=True)
    (intel / "gt_cur_freq_mhz").write_text("350\n")
    cards = [{**card("amd", "amdgpu", "/dev/dri/renderD129", "RX 7600"), "slot": "0000:03:00.0", "card_node": "/dev/dri/card1"},
             {**card("intel", "i915", model="UHD 770"), "slot": "0000:00:02.0", "card_node": "/dev/dri/card0", "integrated": True},
             {**card("nvidia", "nvidia", model="RTX 3060"), "slot": "0000:01:00.0", "card_node": ""}]
    smi = lambda: "00000000:01:00.0, 12, 1500, 12288, 48, 35.20\n"  # noqa: E731
    got = {c["slot"]: c for c in gm.gpu_stats(cards, str(tmp_path), smi)}
    assert got["0000:03:00.0"]["usage"] == 37 and got["0000:03:00.0"]["vram_total"] == 8 * 1024 ** 3
    assert got["0000:03:00.0"]["temp_c"] == 61 and got["0000:03:00.0"]["power_w"] == 88
    assert got["0000:00:02.0"]["freq_mhz"] == 350 and got["0000:00:02.0"]["usage"] is None and got["0000:00:02.0"]["integrated"]
    assert got["0000:01:00.0"]["usage"] == 12 and got["0000:01:00.0"]["vram_used"] == 1500 * 1024 ** 2
    assert got["0000:01:00.0"]["temp_c"] == 48 and got["0000:01:00.0"]["power_w"] == 35.2
    assert all(c["ready"] for c in got.values())
    # A card whose driver is not running yet (nouveau for NVIDIA, none for the rest) says so.
    waiting = gm.gpu_stats([{**card("nvidia", "nouveau", model="RTX 3060"), "slot": "0000:01:00.0", "card_node": ""},
                            {**card("amd", "", render=""), "slot": "0000:03:00.0", "card_node": ""}], str(tmp_path), smi)
    assert [c["ready"] for c in waiting] == [False, False] and waiting[0]["usage"] is None
