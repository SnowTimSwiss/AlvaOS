"""Path commands for alvaos-priv that cannot be raced with a symlink.

priv_policy checks a path as a string: it resolves it and makes sure the
result is inside the data directories. Between that check and the command,
the backend (which owns most of the data directories) could swap a folder on
the way for a symlink to /etc, and a root command would then work there.

So the helper does not hand the checked path to the command. It opens the
path again here, one component at a time from the data directory it lies in,
never following a symlink (O_NOFOLLOW), and works on the open descriptor:

- chown, chgrp, chmod (-R), mkdir -p and rmdir run in this process, relative
  to the descriptors (os.fwalk does not follow symlinks either);
- find, mv and cp still run as programs, but get /proc/self/fd/N instead of
  the path: that names the opened directory itself, whatever happens to its
  path afterwards. mv and cp also get -T, so a symlink planted at the target
  name is replaced or refused instead of being followed into another folder.
"""

import errno
import os
import re
import stat
from collections.abc import Iterator, Sequence

import priv_policy as policy

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_PATH_FLAGS = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC


def _root_of(path: str, roots: Sequence[str]) -> str:
    for root in roots:
        if path.startswith(root.rstrip('/') + '/'):
            return root
    raise policy.PolicyError(f'Path is outside the AlvaOS data directories: {path}')


def _parts(path: str, root: str) -> list[str]:
    rest = path[len(root.rstrip('/')) + 1:]
    parts = rest.split('/')
    if not rest or any(p in ('', '.', '..') for p in parts):
        raise policy.PolicyError(f'Invalid path: {path}')
    return parts


def _open_subdir(name: str, dir_fd: int, path: str) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise policy.PolicyError(f'{path} changed while it was checked (a symlink on the way)') from None
        raise


def open_parent(path: str, roots: Sequence[str] = policy.WRITE_ROOTS, create: bool = False) -> tuple[int, str]:
    """The directory that holds path, opened without following any symlink,
    and the last component's name. create: make missing folders on the way
    (root-owned, mode 0755 under the usual umask), as mkdir -p does."""
    path = os.path.normpath(path)
    root = _root_of(path, roots)
    parts = _parts(path, root)
    fd = os.open(root, _DIR_FLAGS)
    try:
        for name in parts[:-1]:
            if create:
                try:
                    os.mkdir(name, 0o777, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = _open_subdir(name, fd, path)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd, parts[-1]


def open_target(path: str, roots: Sequence[str] = policy.WRITE_ROOTS) -> int:
    """path itself as an O_PATH descriptor; a symlink there is refused."""
    parent, name = open_parent(path, roots)
    try:
        fd = os.open(name, _PATH_FLAGS, dir_fd=parent)
    finally:
        os.close(parent)
    if stat.S_ISLNK(os.fstat(fd).st_mode):
        os.close(fd)
        raise policy.PolicyError(f'{path} is a symlink')
    return fd


def _fd_path(fd: int) -> str:
    return f'/proc/self/fd/{fd}'


# ── chown, chgrp, chmod ──────────────────────────────────────────────────────

def _ids(spec: str, group_only: bool) -> tuple[int, int]:
    import grp
    import pwd

    def gid(g: str) -> int:
        return int(g) if g.isdigit() else grp.getgrnam(g).gr_gid

    if group_only:
        return -1, gid(spec)
    user, _, group = spec.partition(':')
    uid = int(user) if user.isdigit() else pwd.getpwnam(user).pw_uid
    return uid, (gid(group) if group else -1)


_SPECIAL = stat.S_ISUID | stat.S_ISGID


def new_mode(spec: str, old: int, is_dir: bool, umask: int = 0o022) -> int:
    """What chmod SPEC makes of mode old (the permission bits only), for the
    modes priv_policy allows: octal, or clauses like u+rwX,g-w,o=."""
    if re.fullmatch(r'[0-7]{3,5}', spec):
        mode = int(spec, 8)
        # GNU chmod keeps a directory's setuid/setgid bits for a short numeric
        # mode unless the mode sets them; five digits (00755) clear them.
        if is_dir and len(spec) < 5:
            mode |= old & _SPECIAL
        return mode
    mode = old & 0o7777
    for clause in spec.split(','):
        m = re.fullmatch(r'([ugoa]*)([+=-])([rwxX]*)', clause)
        if not m:
            raise policy.PolicyError(f'File mode not allowed: {spec}')
        who, op, perms = m.groups()
        mask = 0
        for w in who or 'a':
            mask |= {'u': 0o700, 'g': 0o070, 'o': 0o007, 'a': 0o777}[w]
        if not who:
            mask &= ~umask
        bits = 0
        for p in perms:
            if p == 'r':
                bits |= 0o444
            elif p == 'w':
                bits |= 0o222
            elif p == 'x' or (p == 'X' and (is_dir or old & 0o111)):
                bits |= 0o111
        bits &= mask
        if op == '+':
            mode |= bits
        elif op == '-':
            mode &= ~bits
        else:
            mode = (mode & ~mask) | bits
    return mode


def _walk(top_fd: int) -> Iterator[tuple[int, str]]:
    """(directory descriptor, name) for everything below top_fd, symlinks
    included but never followed."""
    for _dirpath, dirnames, filenames, dir_fd in os.fwalk('.', dir_fd=top_fd, follow_symlinks=False):
        for name in dirnames + filenames:
            yield dir_fd, name


def _change(fd_path: str, st: os.stat_result, uid: int, gid: int, mode_spec: str | None) -> None:
    if mode_spec is None:
        os.chown(fd_path, uid, gid)
    else:
        os.chmod(fd_path, new_mode(mode_spec, st.st_mode, stat.S_ISDIR(st.st_mode)))


def change_tree(path: str, owner: str | None = None, group: str | None = None, mode: str | None = None,
                roots: Sequence[str] = policy.WRITE_ROOTS) -> None:
    """chown -R / chgrp -R / chmod -R on path, as GNU coreutils does them with
    -P: symlinks below are not followed (chown changes the link itself, chmod
    leaves it alone)."""
    uid, gid = (-1, -1)
    if owner is not None:
        uid, gid = _ids(owner, group_only=False)
    elif group is not None:
        uid, gid = _ids(group, group_only=True)

    top = open_target(path, roots)
    try:
        st = os.fstat(top)
        _change(_fd_path(top), st, uid, gid, mode)
        if not stat.S_ISDIR(st.st_mode):
            return
        # Through the /proc link (which O_NOFOLLOW would refuse), to the folder it names.
        dir_fd = os.open(_fd_path(top), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    finally:
        os.close(top)
    try:
        for parent, name in _walk(dir_fd):
            try:
                fd = os.open(name, _PATH_FLAGS, dir_fd=parent)
            except FileNotFoundError:
                continue  # removed meanwhile
            try:
                st = os.fstat(fd)
                if stat.S_ISLNK(st.st_mode):
                    if mode is None:
                        os.chown(name, uid, gid, dir_fd=parent, follow_symlinks=False)
                    continue
                _change(_fd_path(fd), st, uid, gid, mode)
            finally:
                os.close(fd)
    finally:
        os.close(dir_fd)


# ── mkdir -p, rmdir ──────────────────────────────────────────────────────────

def make_dirs(path: str, roots: Sequence[str] = policy.WRITE_ROOTS) -> None:
    parent, name = open_parent(path, roots, create=True)
    try:
        try:
            os.mkdir(name, 0o777, dir_fd=parent)
        except FileExistsError:
            os.close(_open_subdir(name, parent, path))   # must be a real folder, as mkdir -p wants
    finally:
        os.close(parent)


def remove_dir(path: str, roots: Sequence[str] = policy.WRITE_ROOTS) -> None:
    parent, name = open_parent(path, roots)
    try:
        os.rmdir(name, dir_fd=parent)
    finally:
        os.close(parent)


# ── find, mv, cp ─────────────────────────────────────────────────────────────

def pin_argv(argv: list[str], roots: Sequence[str] = policy.WRITE_ROOTS) -> tuple[list[str], list[int]]:
    """argv with every data path replaced by a /proc/self/fd path, and the
    descriptors the command must inherit. argv is what priv_policy approved."""
    name = os.path.basename(argv[0])
    fds: list[int] = []

    def whole(path: str) -> str:
        fd = open_target(path, roots)
        fds.append(fd)
        return _fd_path(fd)

    def parent(path: str) -> str:
        if policy.is_temp_root_snapshot(path):
            return path   # directly below /, which only root can write to
        fd, last = open_parent(path, roots)
        fds.append(fd)
        return f'{_fd_path(fd)}/{last}'

    try:
        if name == 'find':
            # -H: follow the /proc link given on the command line, nothing else.
            out = [argv[0], '-H', whole(argv[1])] + argv[2:]
        elif name == 'mv':
            out = [argv[0], '-T', parent(argv[1]), parent(argv[2])]
        elif name == 'cp':
            dash = argv.index('--')
            out = argv[:dash] + ['-T', '--', parent(argv[dash + 1]), parent(argv[dash + 2])]
        else:
            raise policy.PolicyError(f'No pinning for {name}')
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise
    for fd in fds:
        os.set_inheritable(fd, True)
    return out, fds


def run_builtin(argv: list[str], roots: Sequence[str] = policy.WRITE_ROOTS) -> None:
    """chown/chgrp/chmod -R X PATH, mkdir -p PATH, rmdir PATH, as approved."""
    name, args = os.path.basename(argv[0]), argv[1:]
    if name == 'chown':
        change_tree(args[2], owner=args[1], roots=roots)
    elif name == 'chgrp':
        change_tree(args[2], group=args[1], roots=roots)
    elif name == 'chmod':
        change_tree(args[2], mode=args[1], roots=roots)
    elif name == 'mkdir':
        make_dirs(args[1], roots)
    elif name == 'rmdir':
        remove_dir(args[0], roots)
    else:
        raise policy.PolicyError(f'No built-in for {name}')
