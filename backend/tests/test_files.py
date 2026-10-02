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


@pytest.mark.parametrize("header,size,expected", [
    (None, 100, (None, True)),
    ("bytes=0-9", 100, ((0, 10), True)),
    ("bytes=90-", 100, ((90, 10), True)),
    ("bytes=-5", 100, ((95, 5), True)),
    ("bytes=50-500", 100, ((50, 50), True)),
    ("bytes=100-", 100, (None, False)),
    ("bytes=5-2", 100, (None, False)),
    ("bytes=0-1,5-6", 100, (None, False)),
    ("items=0-1", 100, (None, False)),
    ("bytes=-", 100, (None, False)),
])
def test_one_byte_range_is_understood(header, size, expected):
    assert fm.parse_range(header, size) == expected


def test_upload_pipes_everything_into_a_real_process(monkeypatch):
    import io
    import sys
    script = "import sys, json; d = sys.stdin.buffer.read(); print(json.dumps({'written': len(d)}))"
    monkeypatch.setattr(fm, "_helper_cmd", lambda args, user=None: [sys.executable, "-c", script])
    result, error = fm.upload("/mnt/alvaos/main/Media", "a.bin", io.BytesIO(b"x" * 3_000_000), chunk=65536)
    assert error == "" and result == {"written": 3_000_000}


def test_upload_reports_a_refusal_from_the_helper(monkeypatch):
    import io
    import sys
    script = "import sys; sys.stderr.write('\"a.bin\" is already there.'); sys.exit(1)"
    monkeypatch.setattr(fm, "_helper_cmd", lambda args, user=None: [sys.executable, "-c", script])
    result, error = fm.upload("/mnt/alvaos/main/Media", "a.bin", io.BytesIO(b"x" * 5_000_000), chunk=65536)
    assert result is None and "already there" in error
