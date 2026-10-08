"""Videos in the Hub: stills, a converted copy, previews of HEIC/TIFF (hub_video.py, files_server.py)."""

import io
import os
import shutil
import subprocess
import time

import pytest

import files_manager
import files_server as fs
import hub_video

H = {"X-AlvaOS-Files": "1"}
have_ffmpeg = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg, reason="ffmpeg is not installed here")


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """A 3 second test video, once for all tests."""
    if not have_ffmpeg:
        pytest.skip("ffmpeg is not installed here")
    path = tmp_path_factory.mktemp("clip") / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10",
                    "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(path)], check=True)
    return path.read_bytes()


def reader(data):
    return lambda start, length: data[start:start + length]


@needs_ffmpeg
def test_a_still_of_a_video(clip, tmp_path):
    jpeg = hub_video.video_still("clip.mp4", len(clip), reader(clip), workdir=str(tmp_path))
    assert jpeg and jpeg[:2] == b"\xff\xd8"
    from PIL import Image
    assert Image.open(io.BytesIO(jpeg)).size[0] == hub_video.STILL_SIZE
    assert os.listdir(tmp_path) == []                       # the temporary copy is gone


@needs_ffmpeg
def test_a_still_when_the_index_is_at_the_end(clip, tmp_path):
    """A phone's MP4 often has its index (moov) after the pictures: only head and tail are read."""
    slow = tmp_path / "slow.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(slow)], check=True)
    data = slow.read_bytes()
    asked = []

    def read(start, length):
        asked.append((start, length))
        return data[start:start + length]

    assert hub_video.video_still("slow.mp4", len(data), read, workdir=str(tmp_path))
    assert asked[0] == (0, len(data))                       # a small file is read at once


def test_no_still_for_what_is_not_a_known_video(tmp_path):
    assert hub_video.video_still("notes.txt", 10, reader(b"x" * 10), workdir=str(tmp_path)) is None
    assert hub_video.video_still("clip.mp4", 0, reader(b""), workdir=str(tmp_path)) is None


def test_ffmpeg_is_told_the_demuxer_and_no_network():
    cmd = hub_video._base_command("/usr/bin/ffmpeg", "holiday.AVI", "/tmp/x.src")
    assert cmd[cmd.index("-f") + 1] == "avi" and cmd[cmd.index("-protocol_whitelist") + 1] == "file"
    assert "-nostdin" in cmd


@needs_ffmpeg
def test_a_converted_copy_is_made_once_and_kept(clip, tmp_path):
    conv = hub_video.Converter(lambda: str(tmp_path))
    key = hub_video.Converter.key("anna", "/mnt/x/clip.mkv", len(clip), "t1")
    assert conv.status(key)["state"] == "idle"
    # an .mkv name with MP4 bytes: the demuxer is chosen by name, so use a name that matches the bytes
    started = conv.start(key, "clip.mp4", len(clip), lambda: iter([clip[:1000], clip[1000:]]))
    assert started["state"] in ("queued", "working")
    for _ in range(200):
        if conv.status(key)["state"] in ("ready", "failed"):
            break
        time.sleep(0.1)
    status = conv.status(key)
    assert status["state"] == "ready", status
    out = conv.output(key)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,height", "-of", "csv=p=0", out],
                           stdout=subprocess.PIPE, text=True, check=True).stdout
    assert "h264" in probe and "aac" in probe
    assert os.listdir(os.path.join(tmp_path, "video", "work")) == []
    assert conv.start(key, "clip.mp4", len(clip), lambda: iter([]))["state"] == "ready"      # not done twice


@needs_ffmpeg
def test_a_broken_video_fails_with_a_sentence(tmp_path):
    conv = hub_video.Converter(lambda: str(tmp_path))
    key = "ab" * 32
    conv.start(key, "bad.mp4", 100, lambda: iter([b"not a video at all" * 5]))
    for _ in range(100):
        if conv.status(key)["state"] in ("ready", "failed"):
            break
        time.sleep(0.1)
    status = conv.status(key)
    assert status["state"] == "failed" and "could not convert" in status["error"]


def test_a_video_that_is_too_big_or_has_no_ffmpeg_says_so(tmp_path, monkeypatch):
    conv = hub_video.Converter(lambda: str(tmp_path))
    monkeypatch.setattr(hub_video, "ffmpeg", lambda: None)
    assert "ffmpeg" in conv.start("k1" * 32, "a.mp4", 10, lambda: iter([]))["error"]
    monkeypatch.setattr(hub_video, "ffmpeg", lambda: "/usr/bin/ffmpeg")
    assert "too big" in conv.start("k2" * 32, "a.mp4", hub_video.MAX_CONVERT_BYTES + 1, lambda: iter([]))["error"]


def test_the_cache_keeps_the_newest_converted_copies(tmp_path, monkeypatch):
    conv = hub_video.Converter(lambda: str(tmp_path))
    monkeypatch.setattr(hub_video, "CACHE_LIMIT_BYTES", 2500)
    for i, age in enumerate((300, 200, 100)):
        path = conv.output(f"{i:02d}" + "0" * 62)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x" * 1000)
        os.utime(path, (time.time() - age, time.time() - age))
    conv._prune()
    left = sorted(p for _d, _s, names in os.walk(tmp_path) for p in names)
    assert len(left) == 2 and "0000" + "0" * 60 + ".mp4" not in left       # the oldest went


def test_heic_and_tiff_previews(tmp_path):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4000, 3000), (200, 50, 50)).save(buf, "TIFF")
    jpeg = hub_video.jpeg(buf.getvalue(), "scan.tiff", hub_video.MAX_PREVIEW_SIDE, 86)
    assert jpeg and Image.open(io.BytesIO(jpeg)).size == (2400, 1800)
    assert hub_video.jpeg(b"not a picture", "x.heic", 400) is None


# ── Through the server ──────────────────────────────────────────────────────

from test_files_server import client, sign_in  # noqa: E402,F401  (the fixture)


def test_mov_and_mkv_are_shown_inline():
    assert files_manager.content_type("trip.MOV", True) == ("video/quicktime", True)
    assert files_manager.content_type("film.mkv", True) == ("video/x-matroska", True)
    assert files_manager.content_type("clip.avi", True)[1] is False          # a browser cannot play it


def test_the_hub_says_what_this_nas_can_do(client):  # noqa: F811
    sign_in(client, "anna", "anna-pass")
    features = client.get("/api/me").get_json()["features"]
    assert set(features) == {"ffmpeg", "heif"}


@needs_ffmpeg
def test_a_video_still_through_the_thumbnail_address(client, clip, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(fs, "THUMB_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setattr(files_manager, "file_size", lambda path, user=None: len(clip))
    monkeypatch.setattr(files_manager, "open_stream",
                        lambda path, chunk=0, part=None, user=None: (iter([clip[part[0]:part[0] + part[1]] if part else clip]), ""))
    sign_in(client, "anna", "anna-pass")
    r = client.get("/api/thumb?share=Anna&path=holiday.mp4&v=1")
    assert r.status_code == 200 and r.data[:2] == b"\xff\xd8"
    assert client.get("/api/thumb?share=Anna&path=holiday.mp4&v=1").data == r.data      # from the cache


def test_converting_through_the_server(client, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(fs, "THUMB_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setattr(files_manager, "file_size", lambda path, user=None: 1234)
    sign_in(client, "anna", "anna-pass")
    assert client.get("/api/video?share=Anna&path=clip.mp4&v=1").get_json()["state"] == "idle"
    assert client.get("/api/video?share=Anna&path=notes.txt").status_code == 404
    assert client.post("/api/video/convert", json={"share": "Anna", "path": "clip.mp4", "v": "1"}).status_code == 403   # needs the header
    started = []
    monkeypatch.setattr(fs._converter, "start", lambda key, name, size, src: started.append((name, size)) or {"state": "queued", "percent": 0})
    r = client.post("/api/video/convert", json={"share": "Anna", "path": "clip.mp4", "v": "1"}, headers=H)
    assert r.status_code == 200 and r.get_json()["state"] == "queued" and started == [("clip.mp4", 1234)]
    assert client.get("/api/video/stream?share=Anna&path=clip.mp4&v=1").status_code == 404     # not made yet
    # Ben cannot reach Anna's share, so not her converted copy either
    sign_in(client, "ben", "ben-pass")
    assert client.get("/api/video?share=Anna&path=clip.mp4&v=1").status_code == 404


def test_a_converted_copy_is_played_with_ranges(client, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(fs, "THUMB_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setattr(files_manager, "file_size", lambda path, user=None: 50)
    sign_in(client, "anna", "anna-pass")
    key = hub_video.Converter.key("anna", "/mnt/alvaos/main/Anna/clip.avi", 50, "9")
    out = fs._converter.output(key)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(bytes(range(200)))
    info = client.get("/api/video?share=Anna&path=clip.avi&v=9").get_json()
    assert info["state"] == "ready" and info["url"].startswith("/api/video/stream?")
    whole = client.get(info["url"])
    assert whole.status_code == 200 and whole.mimetype == "video/mp4" and len(whole.data) == 200
    part = client.get(info["url"], headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.data == bytes(range(10, 20))
