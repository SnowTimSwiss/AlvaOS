"""Space limits for shares and personal folders (share_quota.py, api_shares.py)."""

import subprocess
from types import SimpleNamespace

import pytest

import share_quota as q

SHOW_NEW = """Qgroupid    Referenced    Exclusive   Max referenced   Max exclusive   Path
--------    ----------    ---------   --------------   -------------   ----
0/261       5368709120    5368709120     107374182400            none   anna
"""
SHOW_OLD = """qgroupid         rfer         excl     max_rfer     max_excl
--------         ----         ----     --------     --------
0/258           16384        16384         none         none
"""


def test_qgroup_output_is_read_old_and_new():
    assert q.parse_qgroup_show(SHOW_NEW) == {"used_bytes": 5368709120, "limit_bytes": 107374182400}
    assert q.parse_qgroup_show(SHOW_OLD) == {"used_bytes": 16384, "limit_bytes": None}
    assert q.parse_qgroup_show("ERROR: quotas not enabled") is None


@pytest.mark.parametrize("value, limit, problem", [
    (None, None, ""), ("", None, ""), (0, None, ""), (100, 100 * q.GB, ""), ("2.5", int(2.5 * q.GB), ""),
    ("0.5", None, "at least 1 GB"), ("lots", None, "in GB"), (10 ** 9, None, "at least"),
])
def test_limits_are_given_in_gb(value, limit, problem):
    got, message = q.limit_from_gb(value)
    assert got == limit and problem in message


def ok(cmd, timeout=30):
    return subprocess.CompletedProcess(cmd, 0, "", ""), None


def test_a_limit_turns_quotas_on_for_the_pool_first(monkeypatch):
    calls = []
    monkeypatch.setattr(q, "is_subvolume", lambda p: True)
    assert q.apply_limit("/mnt/alvaos/main/anna", "/mnt/alvaos/main", 50 * q.GB,
                         lambda c, timeout=30: (calls.append(c), ok(c))[1]) == ""
    assert calls == [["btrfs", "quota", "enable", "/mnt/alvaos/main"],
                     ["btrfs", "qgroup", "limit", str(50 * q.GB), "/mnt/alvaos/main/anna"]]
    calls.clear()
    q.apply_limit("/mnt/alvaos/main/anna", "/mnt/alvaos/main", None,
                  lambda c, timeout=30: (calls.append(c), ok(c))[1])
    assert calls == [["btrfs", "qgroup", "limit", "none", "/mnt/alvaos/main/anna"]]


def test_clearing_a_limit_when_quotas_were_never_on_is_fine(monkeypatch):
    monkeypatch.setattr(q, "is_subvolume", lambda p: True)
    failing = lambda c, timeout=30: (None, "ERROR: quotas not enabled")  # noqa: E731
    assert q.apply_limit("/mnt/alvaos/main/x", "/mnt/alvaos/main", None, failing) == ""
    assert "Could not" in q.apply_limit("/mnt/alvaos/main/x", "/mnt/alvaos/main", q.GB, failing)


def test_only_a_subvolume_can_have_a_limit(tmp_path):
    assert "not a separate Btrfs folder" in q.apply_limit(str(tmp_path), "/mnt/alvaos/main", q.GB, ok)


@pytest.fixture
def shares(monkeypatch):
    import api_shares
    state = {"share-1": {"id": "share-1", "name": "Media", "path": "/mnt/alvaos/main/Media"},
             "share-2": {"id": "share-2", "name": "Pool", "path": "/mnt/alvaos/main"}}
    monkeypatch.setattr(api_shares, "load_pools_state", lambda: {"p1": {"mount_point": "/mnt/alvaos/main"}})
    monkeypatch.setattr(api_shares, "load_shares_state", lambda: state)
    monkeypatch.setattr(api_shares, "save_shares_state", lambda s: state.update(s))
    monkeypatch.setattr(api_shares, "platform", SimpleNamespace(system=lambda: "Darwin"))
    return api_shares, state


def test_share_limits_are_kept_and_cleared(shares):
    api, state = shares
    answer, status = api.set_share_limit("share-1", 200)
    assert status == 200 and state["share-1"]["quota_bytes"] == 200 * q.GB and "200 GB" in answer["message"]
    assert api.set_share_limit("share-1", None)[0]["quota_bytes"] is None
    assert api.set_share_limit("share-2", 10)[1] == 400          # the whole pool
    assert api.set_share_limit("nope", 10)[1] == 404


def test_personal_folder_checks_and_creation(shares, monkeypatch):
    api, state = shares
    monkeypatch.setattr(api.os.path, "lexists", lambda p: p == "/mnt/alvaos/main/taken")
    assert "Choose the pool" in api.check_personal_folder("anna", {"pool_id": "zz"})
    assert "already a share" in api.check_personal_folder("media", {"pool_id": "p1"})
    assert "already has a folder" in api.check_personal_folder("taken", {"pool_id": "p1"})
    assert "at least" in api.check_personal_folder("anna", {"pool_id": "p1", "limit_gb": "0.1"})
    assert api.check_personal_folder("anna", {"pool_id": "p1", "limit_gb": 50}) == ""
    answer, status = api.create_personal_folder("anna", {"pool_id": "p1", "limit_gb": 50}, {"anna"})
    assert status == 200, answer
    made = state[answer["share_id"]]
    assert made["name"] == "anna" and made["path"] == "/mnt/alvaos/main/anna"
    assert made["smb_permissions"] == {"anna": "write"} and made["personal_for"] == "anna"
    assert made["quota_bytes"] == 50 * q.GB
