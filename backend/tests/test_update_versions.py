"""Comparing AlvaOS versions for updates (update_manager.normalize_version, is_newer)."""

import update_manager as um


def manager():
    return um.UpdateManager.__new__(um.UpdateManager)


def test_the_stage_first_form_of_the_version_file_is_understood():
    m = manager()
    assert m.normalize_version("beta-v0.1.0") == "0.1.0b"
    assert m.normalize_version("rc-v1.0.0") == "1.0.0rc"
    assert m.is_newer("beta-v0.1.0", "beta-v0.2.0")
    assert m.is_newer("beta-v0.1.0", "v0.1.0")            # the release after its beta
    assert not m.is_newer("beta-v0.1.0", "beta-v0.1.0")
    assert not m.is_newer("beta-v0.2.0", "beta-v0.1.0")


def test_the_usual_forms_still_work():
    m = manager()
    assert m.is_newer("1.0.0-beta2", "1.0.0") and m.is_newer("v1.2.0", "v1.10.0")
    assert not m.is_newer("1.0.0", "not a version")


def test_the_phone_app_pre_release_is_not_an_update_for_the_nas():
    m = manager()
    assert not m.is_system_release({"tag_name": "android-beta"})
    assert m.is_system_release({"tag_name": "beta-v0.2.0"}) and m.is_system_release({"tag_name": "v1.0.0"})
