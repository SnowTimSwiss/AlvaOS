"""When a photo was taken, for Photos in the Hub (backend/photo_dates.py)."""

from io import BytesIO

import pytest

import photo_dates as pd

Image = pytest.importorskip("PIL.Image")


def jpeg(date=None, size=(64, 48)):
    img = Image.new("RGB", size, (120, 160, 200))
    exif = Image.Exif()
    if date:
        exif.get_ifd(0x8769)[36867] = date
    out = BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


def test_the_camera_date_is_read_from_the_start_of_the_photo():
    data = jpeg("2019:07:14 18:03:22", size=(2000, 1500))
    assert pd.taken_at(data[:pd.HEAD_BYTES]) == "2019-07-14T18:03:22"
    assert pd.taken_at(jpeg()) == ""
    assert pd.taken_at(jpeg("0000:00:00 00:00:00")) == ""
    assert pd.taken_at(b"not a photo") == ""


def test_dates_are_looked_up_once_in_the_background_and_then_known(tmp_path):
    cache = pd.DateCache(lambda: str(tmp_path))
    items = [{"name": "beach.jpg", "folder": "2019", "size_bytes": 10, "modified_at": "2026-01-01T00:00:00+01:00"},
             {"name": "plain.JPG", "folder": "", "size_bytes": 5, "modified_at": "2026-01-02T00:00:00+01:00"},
             {"name": "clip.mp4", "folder": "", "size_bytes": 9, "modified_at": "2026-01-03T00:00:00+01:00"}]
    todo = pd.fill(items, "/mnt/alvaos/main/Anna/Photos", cache)
    assert [p for _, p in todo] == ["/mnt/alvaos/main/Anna/Photos/2019/beach.jpg", "/mnt/alvaos/main/Anna/Photos/plain.JPG"]
    files = {"/mnt/alvaos/main/Anna/Photos/2019/beach.jpg": jpeg("2019:07:14 18:03:22"),
             "/mnt/alvaos/main/Anna/Photos/plain.JPG": jpeg()}
    read = []
    pd.look_up(todo, lambda p: read.append(p) or files[p], cache, lambda work: work())
    assert sorted(read) == sorted(files)
    again = [dict(i) for i in items]
    assert pd.fill(again, "/mnt/alvaos/main/Anna/Photos", pd.DateCache(lambda: str(tmp_path))) == []   # kept on disk
    assert again[0]["taken_at"] == "2019-07-14T18:03:22" and "taken_at" not in again[1]
    changed = [dict(items[0], size_bytes=11)]
    assert len(pd.fill(changed, "/mnt/alvaos/main/Anna/Photos", cache)) == 1   # a changed photo is read again
