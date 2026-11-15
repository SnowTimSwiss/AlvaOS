"""priv_paths: the helper's path commands, on real files.

Each "attack" test plants the symlink that a racing backend would swap in
after priv_policy checked the path, and shows that nothing outside the data
directory is touched.
"""

import os
import stat
import subprocess

import pytest

import priv_paths as pp
import priv_policy as policy


@pytest.fixture
def box(tmp_path):
    data = tmp_path / "data"
    outside = tmp_path / "etc"
    (data / "share" / "sub").mkdir(parents=True)
    (data / "share" / "sub" / "file.txt").write_text("x")
    outside.mkdir()
    (outside / "shadow").write_text("secret")
    os.chmod(outside / "shadow", 0o600)
    return data, outside, (str(data),)


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


# ── chmod / chown / chgrp ────────────────────────────────────────────────────

def test_chmod_changes_the_whole_tree(box):
    data, _, roots = box
    pp.run_builtin(["/usr/bin/chmod", "-R", "0750", str(data / "share")], roots)
    assert mode(data / "share") == 0o750
    assert mode(data / "share" / "sub") == 0o750
    assert mode(data / "share" / "sub" / "file.txt") == 0o750


def test_chmod_does_not_follow_a_symlink_inside_the_tree(box):
    data, outside, roots = box
    os.symlink(outside / "shadow", data / "share" / "sub" / "link")
    pp.run_builtin(["/usr/bin/chmod", "-R", "0777", str(data / "share")], roots)
    assert mode(outside / "shadow") == 0o600


def test_a_folder_swapped_for_a_symlink_on_the_way_is_refused(box):
    data, outside, roots = box
    (outside / "sub").mkdir()
    os.chmod(outside / "sub", 0o700)
    # The policy saw data/share/sub as a real folder; now "share" points to /etc.
    os.rename(data / "share", data / "old")
    os.symlink(outside, data / "share")
    with pytest.raises(policy.PolicyError):
        pp.run_builtin(["/usr/bin/chmod", "-R", "0777", str(data / "share" / "sub")], roots)
    assert mode(outside / "sub") == 0o700


def test_a_target_swapped_for_a_symlink_is_refused(box):
    data, outside, roots = box
    os.symlink(outside, data / "share" / "evil")
    with pytest.raises(policy.PolicyError):
        pp.run_builtin(["/usr/bin/chmod", "-R", "0777", str(data / "share" / "evil")], roots)
    with pytest.raises(policy.PolicyError):
        pp.run_builtin(["/usr/bin/chown", "-R", f"{os.getuid()}:{os.getgid()}", str(data / "share" / "evil")], roots)
    assert mode(outside) != 0o777


def test_a_path_outside_the_roots_is_refused(box):
    _, outside, roots = box
    with pytest.raises(policy.PolicyError):
        pp.run_builtin(["/usr/bin/chmod", "-R", "0777", str(outside / "shadow")], roots)


def test_chown_and_chgrp_reach_files_and_the_links_themselves(box):
    data, outside, roots = box
    os.symlink(outside / "shadow", data / "share" / "link")
    me = f"{os.getuid()}:{os.getgid()}"
    pp.run_builtin(["/usr/bin/chown", "-R", me, str(data / "share")], roots)
    pp.run_builtin(["/usr/bin/chgrp", "-R", str(os.getgid()), str(data / "share")], roots)
    assert os.lstat(data / "share" / "sub" / "file.txt").st_gid == os.getgid()


def test_chmod_on_a_single_file(box):
    data, _, roots = box
    pp.run_builtin(["/usr/bin/chmod", "-R", "u+rwX,g+rwX", str(data / "share" / "sub" / "file.txt")], roots)
    assert mode(data / "share" / "sub" / "file.txt") & 0o770 == 0o660


@pytest.mark.parametrize("spec,old,is_dir,want", [
    ("2770", 0o755, True, 0o2770),
    ("0777", 0o2770, True, 0o2777),        # a short numeric mode keeps a folder's setgid
    ("00777", 0o2770, True, 0o777),        # five digits clear it
    ("0777", 0o2770, False, 0o777),        # files keep nothing
    ("u+rwX,g+rwX", 0o600, False, 0o660),  # X: no execute for a plain file
    ("u+rwX,g+rwX", 0o700, True, 0o770),
    ("u+rwX,g+rwX", 0o700, False, 0o770),  # X: execute when the owner has it already
    ("g-w,o=", 0o777, False, 0o750),
    ("+x", 0o644, False, 0o755),           # no "who": the umask decides
])
def test_new_mode_follows_gnu_chmod(spec, old, is_dir, want):
    assert pp.new_mode(spec, old, is_dir) == want


# ── mkdir -p / rmdir ─────────────────────────────────────────────────────────

def test_mkdir_makes_the_folders_on_the_way(box):
    data, _, roots = box
    target = data / "share" / "a" / "b" / "c"
    pp.run_builtin(["/usr/bin/mkdir", "-p", str(target)], roots)
    assert target.is_dir()
    pp.run_builtin(["/usr/bin/mkdir", "-p", str(target)], roots)   # already there: fine, as mkdir -p


def test_mkdir_does_not_follow_a_planted_symlink(box):
    data, outside, roots = box
    os.symlink(outside, data / "share" / "a")
    with pytest.raises(policy.PolicyError):
        pp.run_builtin(["/usr/bin/mkdir", "-p", str(data / "share" / "a" / "cron.d")], roots)
    assert not (outside / "cron.d").exists()


def test_mkdir_refuses_a_symlink_as_the_last_folder(box):
    data, outside, roots = box
    os.symlink(outside, data / "share" / "a")
    with pytest.raises(policy.PolicyError):
        pp.run_builtin(["/usr/bin/mkdir", "-p", str(data / "share" / "a")], roots)


def test_rmdir_removes_an_empty_folder_and_nothing_through_a_link(box):
    data, outside, roots = box
    (data / "share" / "empty").mkdir()
    pp.run_builtin(["/usr/bin/rmdir", str(data / "share" / "empty")], roots)
    assert not (data / "share" / "empty").exists()
    (outside / "empty").mkdir()
    os.symlink(outside, data / "share" / "link")
    with pytest.raises(policy.PolicyError):
        pp.run_builtin(["/usr/bin/rmdir", str(data / "share" / "link" / "empty")], roots)
    assert (outside / "empty").is_dir()


# ── find / mv / cp: the command gets /proc/self/fd paths ─────────────────────

def run_pinned(argv, roots):
    out, fds = pp.pin_argv(argv, roots)
    try:
        return subprocess.run(out, pass_fds=fds, capture_output=True, text=True)
    finally:
        for fd in fds:
            os.close(fd)


def test_find_lists_the_pinned_folder(box):
    data, _, roots = box
    res = run_pinned(["/usr/bin/find", str(data / "share"), "-mindepth", "1", "-maxdepth", "1",
                      "-printf", policy.FIND_LIST_FORMAT], roots)
    assert res.returncode == 0, res.stderr
    names = [rec.split("\t")[-1] for rec in res.stdout.split("\0") if rec]
    assert names == ["sub"]


def test_find_refuses_a_symlink_to_another_folder(box):
    data, outside, roots = box
    os.symlink(outside, data / "share" / "link")
    with pytest.raises(policy.PolicyError):
        pp.pin_argv(["/usr/bin/find", str(data / "share" / "link"), "-mindepth", "1"], roots)


def test_mv_moves_and_a_planted_link_at_the_target_is_not_followed(box):
    data, outside, roots = box
    res = run_pinned(["/usr/bin/mv", str(data / "share" / "sub"), str(data / "share" / "moved")], roots)
    assert res.returncode == 0, res.stderr
    assert (data / "share" / "moved" / "file.txt").exists()

    (data / "share" / "f").write_text("planted")
    os.symlink(outside, data / "share" / "target")
    res = run_pinned(["/usr/bin/mv", str(data / "share" / "f"), str(data / "share" / "target")], roots)
    assert sorted(os.listdir(outside)) == ["shadow"]   # nothing moved into /etc


def test_cp_copies_and_does_not_write_through_a_planted_link(box):
    data, outside, roots = box
    res = run_pinned(["/usr/bin/cp", "-a", "--reflink=auto", "--no-clobber", "--",
                      str(data / "share" / "sub"), str(data / "share" / "copy")], roots)
    assert res.returncode == 0, res.stderr
    assert (data / "share" / "copy" / "file.txt").read_text() == "x"

    os.symlink(outside, data / "share" / "copy2")
    run_pinned(["/usr/bin/cp", "-a", "--reflink=auto", "--no-clobber", "--",
                str(data / "share" / "sub"), str(data / "share" / "copy2")], roots)
    assert sorted(os.listdir(outside)) == ["shadow"]


def test_mv_with_a_parent_swapped_for_a_symlink_is_refused(box):
    data, outside, roots = box
    os.symlink(outside, data / "share" / "link")
    with pytest.raises(policy.PolicyError):
        pp.pin_argv(["/usr/bin/mv", str(data / "share" / "sub"), str(data / "share" / "link" / "x")], roots)


# ── the policy marks which commands go through here ──────────────────────────

def test_the_policy_sends_the_path_commands_here():
    from fakes import FakeSystem
    system = FakeSystem()
    for argv in (["/usr/bin/chmod", "-R", "2770", "/mnt/alvaos/main/media"],
                 ["/usr/bin/chown", "-R", "998:998", "/mnt/alvaos/main/x"],
                 ["/usr/bin/chgrp", "-R", "share_media", "/mnt/alvaos/main/media"],
                 ["/usr/bin/mkdir", "-p", "/mnt/alvaos/main/x"],
                 ["/usr/bin/rmdir", "/mnt/alvaos/main"]):
        assert policy.validate(argv, system).builtin, argv
    for argv in (["/usr/bin/mv", "/mnt/alvaos/main/a", "/mnt/alvaos/main/b"],
                 ["/usr/bin/find", "/mnt/alvaos/main/x", "-mindepth", "1", "-maxdepth", "1",
                  "-printf", policy.FIND_LIST_FORMAT],
                 ["/usr/bin/cp", "-a", "--reflink=auto", "--no-clobber", "--",
                  "/mnt/alvaos/main/a", "/mnt/alvaos/main/b"]):
        assert policy.validate(argv, system).pin, argv
