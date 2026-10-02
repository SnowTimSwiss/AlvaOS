"""AlvaOS Files: browsing and downloading shared folders (files_manager.py, alvaos-priv read-file)."""

import importlib.machinery
import importlib.util
import os
import subprocess

import pytest

import files_manager as fm
import priv_policy as p
from fakes import FakeSystem

SHARES = {"a": {"name": "Media", "path": "/mnt/alvaos/main/Media"},
          "b": {"name": "Photos", "path": "/mnt/alvaos/main/Photos"}}


def test_only_paths_inside_a_share_are_reached():
    assert fm.resolve(SHARES, "Media", "") == ("/mnt/alvaos/main/Media", "", "")
    assert fm.resolve(SHARES, "Media", "/Films//2024/") == ("/mnt/alvaos/main/Media/Films/2024", "Films/2024", "")
    assert fm.resolve(SHARES, "Media", "../Photos")[2]
    assert fm.resolve(SHARES, "Media", "Films/../../etc")[2]
    assert fm.resolve(SHARES, "Nope", "")[2]
    assert fm.resolve({"x": {"name": "Odd", "path": "relative"}}, "Odd", "")[2]


def test_listing_goes_through_the_helper_rule():
    calls = []

    def run(cmd, timeout=30):
        calls.append(cmd)
        p.validate(cmd, FakeSystem())
        return subprocess.CompletedProcess(cmd, 0, "d\t0\t1759287600.0\t2024\0f\t12\t1759287600.0\ta.mkv\0", ""), None

    entries, error = fm.list_folder("/mnt/alvaos/main/Media", run)
    assert not error and [e["name"] for e in entries] == ["2024", "a.mkv"]


@pytest.mark.parametrize("name,inline,expected", [
    ("photo.JPG", True, ("image/jpeg", True)),
    ("notes.md", True, ("text/plain; charset=utf-8", True)),
    ("page.html", True, ("application/octet-stream", False)),
    ("logo.svg", True, ("application/octet-stream", False)),
    ("script.js", True, ("application/octet-stream", False)),
    ("photo.jpg", False, ("application/octet-stream", False)),
    ("film.mp4", True, ("video/mp4", True)),
])
def test_only_harmless_types_are_shown_in_the_browser(name, inline, expected):
    assert fm.content_type(name, inline) == expected


def load_helper():
    path = os.path.join(os.path.dirname(__file__), "..", "alvaos-priv")
    loader = importlib.machinery.SourceFileLoader("alvaos_priv_under_test", path)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def test_read_file_stays_on_the_pools_even_through_symlinks(tmp_path):
    helper = load_helper()
    pool = tmp_path / "pool"
    (pool / "Media").mkdir(parents=True)
    (pool / "Media" / "ok.txt").write_text("hello")
    secret = tmp_path / "shadow"
    secret.write_text("root:x")
    os.symlink(secret, pool / "Media" / "link.txt")                 # the file itself is a symlink
    os.symlink(tmp_path, pool / "Media" / "escape")                  # a folder leading out
    root = str(pool)
    with helper.open_data_file(str(pool / "Media" / "ok.txt"), root) as f:
        assert f.read() == b"hello"
    for bad in (pool / "Media" / "link.txt", pool / "Media" / "escape" / "shadow", pool / "Media",
                secret):
        with pytest.raises((p.PolicyError, OSError)):
            helper.open_data_file(str(bad), root)
