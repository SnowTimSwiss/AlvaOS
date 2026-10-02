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
