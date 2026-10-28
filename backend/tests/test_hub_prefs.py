"""Each person's own side bar in the Hub (hub_prefs.py and /api/prefs)."""

import pytest

import hub_prefs

AVAILABLE = ["files", "photos", "calendar", "store:jellyfin"]


def test_nothing_chosen_means_the_default(tmp_path):
    path = str(tmp_path / "prefs.json")
    assert hub_prefs.get("anna", path) == {"bar": None}


def test_a_side_bar_is_kept_in_order_per_person_and_can_be_reset(tmp_path):
    path = str(tmp_path / "prefs.json")
    assert hub_prefs.save("anna", ["calendar", "store:jellyfin", "files", "files"], AVAILABLE, path) == {
        "bar": ["calendar", "store:jellyfin", "files"]}
    assert hub_prefs.get("ben", path) == {"bar": None}                  # nobody else's
    assert hub_prefs.get("anna", path)["bar"][0] == "calendar"
    assert hub_prefs.save("anna", None, AVAILABLE, path) == {"bar": None}


@pytest.mark.parametrize("bad", [[], ["chat"], ["store:ghost"], "files", ["files", 3], ["x"] * 80])
def test_only_what_the_person_has_and_at_least_one_app_is_accepted(tmp_path, bad):
    with pytest.raises(ValueError):
        hub_prefs.save("anna", bad, AVAILABLE, str(tmp_path / "prefs.json"))
