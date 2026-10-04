"""The package of the running version stays in, or comes back to, the update
cache: it is the way back when an update fails (update_manager.py)."""

import os

import pytest

pytest.importorskip("requests")
import update_manager as um  # noqa: E402


@pytest.fixture
def manager(tmp_path, monkeypatch):
    m = um.UpdateManager.__new__(um.UpdateManager)
    m.repo = "SnowTimSwiss/AlvaOS"
    m.state_dir = str(tmp_path)
    m.cache_dir = str(tmp_path / "updates")
    os.makedirs(m.cache_dir)
    monkeypatch.setattr(m, "get_current_version", lambda: "0.9.0")
    # The test packages carry their version in the file name.
    monkeypatch.setattr(um, "read_deb_version", lambda p: os.path.basename(p).split("_")[1])
    return m


def put(manager, name, mtime, signed=True):
    path = os.path.join(manager.cache_dir, name)
    open(path, "wb").write(b"deb")
    if signed:
        open(path + ".sig", "wb").write(b"sig")
    os.utime(path, (mtime, mtime))
    return path


def test_cleanup_never_removes_the_running_version(manager):
    put(manager, "alvaos_0.9.0_all.deb", 100)
    for i, v in enumerate(("0.9.1", "0.9.2", "0.9.3", "0.9.4")):
        put(manager, f"alvaos_{v}_all.deb", 200 + i)
    manager.cleanup_cache(keep=3)
    left = sorted(n for n in os.listdir(manager.cache_dir) if n.endswith(".deb"))
    assert left == ["alvaos_0.9.0_all.deb", "alvaos_0.9.2_all.deb", "alvaos_0.9.3_all.deb", "alvaos_0.9.4_all.deb"]
    assert manager.way_back_ready()


class Res:
    def __init__(self, status=200, data=None, body=b""):
        self.status_code = status
        self._data = data
        self._body = body

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, chunk_size=1):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_missing_package_is_fetched_from_its_release(manager):
    asked = []
    release = {"assets": [{"name": "alvaos_0.9.0_all.deb", "browser_download_url": "https://x/alvaos_0.9.0_all.deb"},
                          {"name": "alvaos_0.9.0_all.deb.sig", "browser_download_url": "https://x/alvaos_0.9.0_all.deb.sig"}]}

    def get(url, **kw):
        asked.append(url)
        if "releases/tags/v0.9.0" in url:
            return Res(data=release)
        return Res(body=b"SIG" if url.endswith(".sig") else b"DEB")

    assert not manager.way_back_ready()
    assert manager.ensure_way_back(get=get) == "fetched"
    assert manager.way_back_ready()
    assert open(os.path.join(manager.cache_dir, "alvaos_0.9.0_all.deb.sig"), "rb").read() == b"SIG"
    assert manager.ensure_way_back(get=lambda *a, **k: pytest.fail("no network needed")) == "ready"


def test_nothing_is_kept_without_a_signature_or_offline(manager):
    unsigned = {"assets": [{"name": "alvaos_0.9.0_all.deb", "browser_download_url": "https://x/a.deb"}]}
    assert manager.ensure_way_back(get=lambda url, **kw: Res(data=unsigned)) == "no signed package in the release"

    def offline(url, **kw):
        raise OSError("no network")

    assert manager.ensure_way_back(get=offline).startswith("offline")
    assert manager.ensure_way_back(get=lambda url, **kw: Res(status=404)) == "no release for this version"
    assert [n for n in os.listdir(manager.cache_dir)] == []


def test_a_broken_download_leaves_nothing_behind(manager):
    release = {"assets": [{"name": "alvaos_0.9.0_all.deb", "browser_download_url": "https://x/a.deb"},
                          {"name": "alvaos_0.9.0_all.deb.sig", "browser_download_url": "https://x/a.deb.sig"}]}

    def get(url, **kw):
        if "releases/tags" in url:
            return Res(data=release)
        return Res(status=500) if url.endswith(".sig") else Res(body=b"DEB")

    assert manager.ensure_way_back(get=get).startswith("download failed")
    assert os.listdir(manager.cache_dir) == []
