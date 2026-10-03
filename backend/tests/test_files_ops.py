"""File changes in shared folders, as the helper runs them (backend/files_ops.py)."""

import io
import os
from datetime import datetime

import pytest

import files_ops as fo


@pytest.fixture
def share(tmp_path):
    root = tmp_path / "mnt"
    share = root / "main" / "Media"
    (share / "Films").mkdir(parents=True)
    os.chmod(share, 0o2770)
    (share / "notes.txt").write_text("old")
    outside = tmp_path / "etc"
    outside.mkdir()
    (outside / "shadow").write_text("secret")
    return str(root), str(share), str(outside)


def test_upload_creates_a_new_file_like_its_folder(share):
    root, s, _ = share
    assert fo.write_file(s, "a.txt", io.BytesIO(b"hello"), root) == 5
    st = os.stat(os.path.join(s, "a.txt"))
    assert open(os.path.join(s, "a.txt"), "rb").read() == b"hello"
    assert st.st_mode & 0o777 == 0o660          # share folder 2770 -> files 0660
    with pytest.raises(fo.FileOpError, match="already there"):
        fo.write_file(s, "notes.txt", io.BytesIO(b"x"), root)
    assert open(os.path.join(s, "notes.txt")).read() == "old"


def test_a_broken_upload_leaves_nothing_behind(share):
    root, s, _ = share

    class Broken(io.BytesIO):
        def read(self, n=-1):
            raise ConnectionError("browser went away")

    with pytest.raises(ConnectionError):
        fo.write_file(s, "half.bin", Broken(), root)
    assert not os.path.exists(os.path.join(s, "half.bin"))


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "../x", "x\x00y", ".alvaos-trash", "a" * 256])
def test_names_are_single_safe_components(share, name):
    root, s, _ = share
    with pytest.raises(fo.FileOpError):
        fo.write_file(s, name, io.BytesIO(b"x"), root)


def test_symlinked_folders_lead_nowhere(share):
    root, s, outside = share
    os.symlink(outside, os.path.join(s, "evil"))
    for action in (lambda: fo.write_file(os.path.join(s, "evil"), "x", io.BytesIO(b"x"), root),
                   lambda: fo.make_dir(os.path.join(s, "evil"), "x", root),
                   lambda: fo.write_file(outside, "x", io.BytesIO(b"x"), root),
                   lambda: fo.trash(s, outside, "shadow", root)):
        with pytest.raises(fo.FileOpError):
            action()
    assert os.listdir(outside) == ["shadow"]


def test_new_folder_and_rename_never_overwrite(share):
    root, s, _ = share
    fo.make_dir(s, "New", root)
    assert os.stat(os.path.join(s, "New")).st_mode & 0o7777 == 0o2770
    fo.rename(s, "New", "Holidays", root)
    assert os.path.isdir(os.path.join(s, "Holidays"))
    with pytest.raises(fo.FileOpError, match="already there"):
        fo.rename(s, "Holidays", "notes.txt", root)
    with pytest.raises(fo.FileOpError, match="not there"):
        fo.rename(s, "gone", "x", root)


def test_trash_and_restore(share):
    root, s, _ = share
    fo.write_file(os.path.join(s, "Films"), "a.mkv", io.BytesIO(b"film"), root)
    stamp = fo.trash(s, os.path.join(s, "Films"), "a.mkv", root, now=datetime(2026, 9, 1, 10, 0))
    assert not os.path.exists(os.path.join(s, "Films", "a.mkv"))
    items = fo.list_trash(s, root)
    assert [(i["name"], i["folder"], i["type"]) for i in items] == [("a.mkv", "Films", "file")]
    # Something new took the name meanwhile: the old one comes back beside it.
    fo.write_file(os.path.join(s, "Films"), "a.mkv", io.BytesIO(b"new"), root)
    assert fo.restore(s, stamp, root) == {"folder": "Films", "name": "a (restored).mkv"}
    assert open(os.path.join(s, "Films", "a (restored).mkv"), "rb").read() == b"film"
    assert fo.list_trash(s, root) == []


def test_restore_into_a_folder_that_is_gone_goes_to_the_share(share):
    root, s, _ = share
    os.makedirs(os.path.join(s, "Old", "Deep"))
    open(os.path.join(s, "Old", "Deep", "x.txt"), "w").write("x")
    stamp = fo.trash(s, os.path.join(s, "Old", "Deep"), "x.txt", root)
    os.rmdir(os.path.join(s, "Old", "Deep"))
    assert fo.restore(s, stamp, root) == {"folder": "", "name": "x.txt"}
    assert os.path.exists(os.path.join(s, "x.txt"))


def test_purge_removes_old_items_only_and_empty_removes_all(share):
    root, s, _ = share
    fo.make_dir(s, "A", root)
    fo.make_dir(s, "B", root)
    fo.trash(s, s, "A", root, now=datetime(2026, 8, 1))
    fo.trash(s, s, "B", root, now=datetime(2026, 9, 28))
    assert fo.purge(s, 30, root, now=datetime(2026, 10, 2)) == 1
    assert [i["name"] for i in fo.list_trash(s, root)] == ["B"]
    assert fo.purge(s, 0, root) == 1
    assert fo.list_trash(s, root) == []


def test_bad_trash_ids_are_refused(share):
    root, s, _ = share
    for stamp in ("../../etc", "x", "20260901-100000-a/../../x"):
        with pytest.raises(fo.FileOpError):
            fo.restore(s, stamp, root)


def test_the_trash_itself_cannot_be_a_symlink(share):
    root, s, outside = share
    os.symlink(outside, os.path.join(s, fo.TRASH_DIR))
    fo.make_dir(s, "A", root)
    with pytest.raises(fo.FileOpError):
        fo.trash(s, s, "A", root)
    assert os.listdir(outside) == ["shadow"]


def test_listing_hides_the_trash_and_puts_folders_first(share):
    root, s, _ = share
    fo.make_dir(s, "B", root)
    fo.trash(s, s, "B", root)
    fo.make_dir(s, "zeta", root)
    entries = fo.list_dir(s, root)
    assert [(e["name"], e["type"]) for e in entries] == [("Films", "folder"), ("zeta", "folder"), ("notes.txt", "file")]
    assert entries[2]["size_bytes"] == 3


def test_only_file_operations_can_run_as_a_person():
    from test_files import load_helper
    helper = load_helper()
    assert {"files-list", "files-write", "files-trash", "read-file", "file-size"} <= helper.AS_USER_OPS
    assert not {"apply-update", "write-sysfs", "vault-open", "files-trash-purge"} & helper.AS_USER_OPS


def test_move_into_another_folder_without_overwriting(share):
    root, s, outside = share
    fo.make_dir(s, "Old", root)
    fo.move(s, "notes.txt", os.path.join(s, "Films"), root)
    assert os.path.exists(os.path.join(s, "Films", "notes.txt"))
    open(os.path.join(s, "notes.txt"), "w").write("new")
    with pytest.raises(fo.FileOpError, match="already a"):
        fo.move(s, "notes.txt", os.path.join(s, "Films"), root)
    with pytest.raises(fo.FileOpError, match="into itself"):
        fo.move(s, "Films", os.path.join(s, "Films"), root)
    fo.make_dir(os.path.join(s, "Films"), "Inner", root)
    with pytest.raises(fo.FileOpError, match="into itself"):
        fo.move(s, "Films", os.path.join(s, "Films", "Inner"), root)
    with pytest.raises(fo.FileOpError):
        fo.move(s, "notes.txt", outside, root)
    with pytest.raises(fo.FileOpError, match="already in that folder"):
        fo.move(s, "notes.txt", s, root)



def test_a_folder_zips_without_symlinks_hidden_files_or_trash(share):
    import io
    import zipfile
    root, s_, outside = share
    fo.write_file(os.path.join(s_, "Films"), "a.mkv", io.BytesIO(b"film"), root)
    os.symlink(os.path.join(outside, "shadow"), os.path.join(s_, "evil.txt"))
    open(os.path.join(s_, ".DS_Store"), "w").write("x")
    fo.make_dir(s_, "Gone", root)
    fo.trash(s_, s_, "Gone", root)
    buf = io.BytesIO()
    assert fo.zip_folder(s_, buf, root) == 2
    z = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    assert sorted(z.namelist()) == ["Films/a.mkv", "notes.txt"]
    assert z.read("Films/a.mkv") == b"film"


def test_uploads_in_pieces_continue_and_never_overwrite(share):
    import io
    root, s_, outside = share
    assert fo.part_size(s_, "big.bin", root) == 0
    assert fo.part_write(s_, "big.bin", 0, io.BytesIO(b"aaaa"), root) == 4
    with pytest.raises(fo.FileOpError, match="continues at 4"):
        fo.part_write(s_, "big.bin", 0, io.BytesIO(b"x"), root)       # a piece sent twice
    assert fo.part_size(s_, "big.bin", root) == 4                     # where to go on after a drop
    assert fo.part_write(s_, "big.bin", 4, io.BytesIO(b"bb"), root) == 6
    with pytest.raises(fo.FileOpError, match="not complete"):
        fo.part_finish(s_, "big.bin", 10, root)
    assert not any(e["name"].endswith(".alvaos-upload") for e in fo.list_dir(s_, root) if not e["name"].startswith("."))
    fo.part_finish(s_, "big.bin", 6, root)
    assert open(os.path.join(s_, "big.bin"), "rb").read() == b"aaaabb"
    assert os.stat(os.path.join(s_, "big.bin")).st_mode & 0o777 == 0o660
    with pytest.raises(fo.FileOpError, match="already there"):
        fo.part_write(s_, "notes.txt", 0, io.BytesIO(b"x"), root)
    fo.part_write(s_, "gone.bin", 0, io.BytesIO(b"x"), root)
    fo.part_abort(s_, "gone.bin", root)
    assert fo.part_size(s_, "gone.bin", root) == 0


def test_a_symlink_in_place_of_the_part_file_is_refused(share):
    import io
    root, s_, outside = share
    os.symlink(os.path.join(outside, "shadow"), os.path.join(s_, ".x.bin.alvaos-upload"))
    with pytest.raises((fo.FileOpError, OSError)):
        fo.part_write(s_, "x.bin", 0, io.BytesIO(b"pwn"), root)
    assert open(os.path.join(outside, "shadow")).read() == "secret"


def test_copy_files_and_folders_without_symlinks_or_overwriting(share):
    import io
    root, s_, outside = share
    fo.write_file(os.path.join(s_, "Films"), "a.mkv", io.BytesIO(b"film"), root)
    os.symlink(os.path.join(outside, "shadow"), os.path.join(s_, "Films", "evil"))
    assert fo.copy(s_, "Films", s_, root) == "Films (copy)"
    assert sorted(os.listdir(os.path.join(s_, "Films (copy)"))) == ["a.mkv"]
    assert fo.copy(s_, "notes.txt", s_, root) == "notes (copy).txt"
    assert fo.copy(s_, "notes.txt", s_, root) == "notes (copy 2).txt"
    assert fo.copy(s_, "notes.txt", os.path.join(s_, "Films"), root) == "notes.txt"
    with pytest.raises(fo.FileOpError, match="into itself"):
        fo.copy(s_, "Films", os.path.join(s_, "Films"), root)
    with pytest.raises(fo.FileOpError):
        fo.copy(s_, "evil", s_, root)          # not a file in the share root
    assert open(os.path.join(outside, "shadow")).read() == "secret"


def test_search_finds_names_below_a_folder(share, tmp_path):
    root, s, outside = share
    os.makedirs(os.path.join(s, "Films", "Summer 2024"))
    open(os.path.join(s, "Films", "Summer 2024", "beach-summer.MP4"), "w").close()
    open(os.path.join(s, "Films", ".beach-summer.mp4.alvaos-upload"), "w").close()
    os.symlink(outside, os.path.join(s, "Films", "summer-link"))
    fo.make_dir(s, "Old summer", root)
    fo.trash(s, s, "Old summer", root)
    out = fo.search(s, "SUMMER", root)
    assert out["complete"] is True
    assert [(r["folder"], r["name"], r["type"]) for r in out["results"]] == [
        ("Films", "Summer 2024", "folder"), ("Films/Summer 2024", "beach-summer.MP4", "file")]
    assert [r["name"] for r in fo.search(s, "beach summer", root)["results"]] == ["beach-summer.MP4"]
    assert fo.search(os.path.join(s, "Films"), "shadow", root)["results"] == []


def test_search_stops_at_the_limit_and_needs_words(share):
    root, s, _ = share
    for i in range(5):
        open(os.path.join(s, f"photo{i}.jpg"), "w").close()
    out = fo.search(s, "photo", root, limit=3)
    assert len(out["results"]) == 3 and out["complete"] is False
    with pytest.raises(fo.FileOpError):
        fo.search(s, "   ", root)


def test_versions_and_restoring_one_as_a_copy(share, tmp_path):
    root, s, outside = share
    snap = os.path.join(root, "main", ".alvaos-snapshots", "Media", "20261001-030000-auto")
    os.makedirs(snap)
    with open(os.path.join(snap, "notes.txt"), "w") as f:
        f.write("older text")
    os.symlink(os.path.join(outside, "shadow"), os.path.join(snap, "link.txt"))
    out = fo.versions("notes.txt", [s, snap, os.path.join(root, "gone"), outside], root)
    assert out[0]["size_bytes"] == 3 and out[1]["size_bytes"] == 10 and out[2] is None and out[3] is None
    assert fo.versions("link.txt", [snap], root) == [None]
    new = fo.restore_version(snap, "notes.txt", s, "notes (restored 2026-10-01 0300).txt", root)
    assert open(os.path.join(s, new)).read() == "older text"
    assert open(os.path.join(s, "notes.txt")).read() == "old"
    with pytest.raises(fo.FileOpError, match="already there"):
        fo.restore_version(snap, "notes.txt", s, new, root)
    with pytest.raises(fo.FileOpError, match="Only files"):
        fo.restore_version(snap, "link.txt", s, "x.txt", root)


def test_media_lists_photos_and_videos_newest_first(share):
    root, s, outside = share
    os.makedirs(os.path.join(s, "Films", "2023"))
    os.makedirs(os.path.join(s, ".hidden"))
    for rel, t in (("Films/2023/old.JPG", 100), ("Films/clip.mp4", 300), ("new.heic", 500),
                   (".hidden/secret.jpg", 600), ("notes.txt", 700), ("Films/.x.jpg.alvaos-upload", 800)):
        path = os.path.join(s, rel)
        open(path, "w").close()
        os.utime(path, (t, t))
    os.symlink(outside, os.path.join(s, "Films", "link"))
    out = fo.media(s, root)
    assert [(r["folder"], r["name"]) for r in out["results"]] == [("", "new.heic"), ("Films", "clip.mp4"),
                                                                  ("Films/2023", "old.JPG")]
    assert out["complete"] is True
    assert len(fo.media(s, root, limit=2)["results"]) == 2 and fo.media(s, root, limit=2)["complete"] is False


def test_a_finished_upload_keeps_its_own_date(share):
    root, s, _ = share
    for name, mtime, expect in (("old.jpg", 1600000000, 1600000000), ("bogus.jpg", 5, None)):
        fo.part_write(s, name, 0, io.BytesIO(b"abc"), root)
        fo.part_finish(s, name, 3, root, mtime=mtime)
        got = int(os.stat(os.path.join(s, name)).st_mtime)
        assert got == expect if expect else got > 1600000000      # nonsense dates are ignored
