//! A [`Volume`] as a folder on Linux: a file system in user space (FUSE, through
//! `fusermount3`, so no root and no kernel module of ours). The folder shows every file of the
//! NAS; opening one fetches it; closing a file that was changed sends it back. Everything the
//! system or a program does in the folder (list, read, write, make, rename, delete) goes
//! through the volume.
//!
//! Needs `fusermount3` (package `fuse3`), as most desktops have it.

use std::collections::HashMap;
use std::ffi::OsStr;
use std::fs::File;
use std::os::unix::fs::FileExt;
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, UNIX_EPOCH};

use anyhow::{bail, Context, Result};
use fuser::{
    FileAttr, FileType, Filesystem, MountOption, ReplyAttr, ReplyCreate, ReplyData, ReplyDirectory, ReplyEmpty, ReplyEntry,
    ReplyOpen, ReplyStatfs, ReplyWrite, Request, TimeOrNow,
};
use tokio::runtime::Handle;

use crate::ondemand::{errno, Volume, EIO, ENOENT, ENOTDIR, EISDIR};
use crate::sync::{Item, Remote};

const TTL: Duration = Duration::from_secs(1);
const ROOT: u64 = 1;

struct Inodes {
    by_path: HashMap<String, u64>,
    by_ino: HashMap<u64, String>,
    next: u64,
}

impl Inodes {
    fn new() -> Self {
        Self { by_path: HashMap::from([(String::new(), ROOT)]), by_ino: HashMap::from([(ROOT, String::new())]), next: ROOT + 1 }
    }

    fn ino(&mut self, path: &str) -> u64 {
        if let Some(i) = self.by_path.get(path) {
            return *i;
        }
        let i = self.next;
        self.next += 1;
        self.by_path.insert(path.to_string(), i);
        self.by_ino.insert(i, path.to_string());
        i
    }

    fn path(&self, ino: u64) -> Option<String> {
        self.by_ino.get(&ino).cloned()
    }

    /// A file or folder was renamed: the numbers follow the new names.
    fn renamed(&mut self, from: &str, to: &str) {
        let prefix = format!("{from}/");
        let target = format!("{to}/");
        // Whatever was at the target is gone.
        let replaced: Vec<String> = self.by_path.keys().filter(|k| k.as_str() == to || k.starts_with(&target)).cloned().collect();
        for k in replaced {
            if let Some(i) = self.by_path.remove(&k) {
                self.by_ino.remove(&i);
            }
        }
        let moving: Vec<String> = self.by_path.keys().filter(|k| k.as_str() == from || k.starts_with(&prefix)).cloned().collect();
        for old in moving {
            let new = format!("{to}{}", &old[from.len()..]);
            if let Some(i) = self.by_path.remove(&old) {
                self.by_ino.insert(i, new.clone());
                self.by_path.insert(new, i);
            }
        }
    }
}

enum Handle2 {
    /// A copy here: read from it, written to it.
    File { file: File, path: String, write: bool, modified: bool },
    /// A big file read where it is, piece by piece.
    Piece { path: String },
}

pub struct AlvaFs<R: Remote + Send + Sync + 'static> {
    vol: Arc<Volume<R>>,
    rt: Handle,
    inodes: Inodes,
    open: HashMap<u64, Handle2>,
    next: u64,
    uid: u32,
    gid: u32,
}

impl<R: Remote + Send + Sync + 'static> AlvaFs<R> {
    pub fn new(vol: Arc<Volume<R>>, rt: Handle) -> Self {
        // SAFETY: plain system calls without arguments.
        let (uid, gid) = unsafe { (libc::geteuid(), libc::getegid()) };
        Self { vol, rt, inodes: Inodes::new(), open: HashMap::new(), next: 1, uid, gid }
    }

    fn attr(&self, ino: u64, item: &Item) -> FileAttr {
        let when = UNIX_EPOCH + Duration::from_secs(item.mtime.max(0) as u64);
        let read_only = self.vol.read_only();
        FileAttr {
            ino,
            size: if item.dir { 0 } else { item.size },
            blocks: item.size.div_ceil(512),
            atime: when,
            mtime: when,
            ctime: when,
            crtime: when,
            kind: if item.dir { FileType::Directory } else { FileType::RegularFile },
            perm: match (item.dir, read_only) {
                (true, false) => 0o755,
                (true, true) => 0o555,
                (false, false) => 0o644,
                (false, true) => 0o444,
            },
            nlink: if item.dir { 2 } else { 1 },
            uid: self.uid,
            gid: self.gid,
            rdev: 0,
            blksize: 4096,
            flags: 0,
        }
    }

    fn child(&self, parent: u64, name: &OsStr) -> Option<String> {
        let parent = self.inodes.path(parent)?;
        let name = name.to_str()?;
        Some(if parent.is_empty() { name.to_string() } else { format!("{parent}/{name}") })
    }

    fn handle(&mut self, entry: Handle2) -> u64 {
        let fh = self.next;
        self.next += 1;
        self.open.insert(fh, entry);
        fh
    }

    /// Sends a changed file back (when a program closes it).
    fn send(&mut self, fh: u64) -> Result<(), i32> {
        let path = match self.open.get_mut(&fh) {
            Some(Handle2::File { path, modified, .. }) if *modified => {
                *modified = false;
                path.clone()
            }
            _ => return Ok(()),
        };
        self.rt.block_on(self.vol.finish(&path)).map_err(|e| {
            if let Some(Handle2::File { modified, .. }) = self.open.get_mut(&fh) {
                *modified = true;
            }
            errno(&e)
        })
    }

    fn open_file(path: &std::path::Path, write: bool) -> std::io::Result<File> {
        std::fs::OpenOptions::new().read(true).write(write).open(path)
    }
}

impl<R: Remote + Send + Sync + 'static> Filesystem for AlvaFs<R> {
    fn lookup(&mut self, _req: &Request<'_>, parent: u64, name: &OsStr, reply: ReplyEntry) {
        let Some(path) = self.child(parent, name) else { return reply.error(ENOENT) };
        match self.vol.stat(&path) {
            Some(item) => {
                let ino = self.inodes.ino(&path);
                reply.entry(&TTL, &self.attr(ino, &item), 0);
            }
            None => reply.error(ENOENT),
        }
    }

    fn getattr(&mut self, _req: &Request<'_>, ino: u64, _fh: Option<u64>, reply: ReplyAttr) {
        let Some(path) = self.inodes.path(ino) else { return reply.error(ENOENT) };
        match self.vol.stat(&path) {
            Some(item) => reply.attr(&TTL, &self.attr(ino, &item)),
            None => reply.error(ENOENT),
        }
    }

    fn setattr(
        &mut self, _req: &Request<'_>, ino: u64, _mode: Option<u32>, _uid: Option<u32>, _gid: Option<u32>, size: Option<u64>,
        _atime: Option<TimeOrNow>, _mtime: Option<TimeOrNow>, _ctime: Option<std::time::SystemTime>, fh: Option<u64>,
        _crtime: Option<std::time::SystemTime>, _chgtime: Option<std::time::SystemTime>, _bkuptime: Option<std::time::SystemTime>,
        _flags: Option<u32>, reply: ReplyAttr,
    ) {
        let Some(path) = self.inodes.path(ino) else { return reply.error(ENOENT) };
        if let Some(size) = size {
            let open = fh.and_then(|fh| match self.open.get_mut(&fh) {
                Some(Handle2::File { file, write: true, modified, .. }) => {
                    *modified = true;
                    Some(file.set_len(size))
                }
                _ => None,
            });
            let done = match open {
                Some(r) => r.map_err(|_| EIO),
                // Truncated without being open (a program that truncates and then opens, or truncate(2)):
                // marked as changed; it goes up when it is closed, or in the next round.
                None => self.rt.block_on(async {
                    let file = self.vol.open_write(&path, false).await?;
                    std::fs::OpenOptions::new().write(true).open(&file)?.set_len(size)?;
                    self.vol.touched(&path);
                    anyhow::Ok(())
                }).map_err(|e| errno(&e)),
            };
            if let Err(code) = done {
                return reply.error(code);
            }
            self.vol.touched(&path);
        }
        match self.vol.stat(&path) {
            Some(item) => reply.attr(&TTL, &self.attr(ino, &item)),
            None => reply.error(ENOENT),
        }
    }

    fn readdir(&mut self, _req: &Request<'_>, ino: u64, _fh: u64, offset: i64, mut reply: ReplyDirectory) {
        let Some(path) = self.inodes.path(ino) else { return reply.error(ENOENT) };
        match self.vol.stat(&path) {
            Some(item) if item.dir => {}
            Some(_) => return reply.error(ENOTDIR),
            None => return reply.error(ENOENT),
        }
        let mut entries: Vec<(u64, FileType, String)> = vec![(ino, FileType::Directory, ".".into()), (ino, FileType::Directory, "..".into())];
        for (name, item) in self.vol.children(&path) {
            let full = if path.is_empty() { name.clone() } else { format!("{path}/{name}") };
            let child = self.inodes.ino(&full);
            entries.push((child, if item.dir { FileType::Directory } else { FileType::RegularFile }, name));
        }
        for (i, (child, kind, name)) in entries.into_iter().enumerate().skip(offset as usize) {
            if reply.add(child, (i + 1) as i64, kind, name) {
                break;
            }
        }
        reply.ok();
    }

    fn open(&mut self, _req: &Request<'_>, ino: u64, flags: i32, reply: ReplyOpen) {
        let Some(path) = self.inodes.path(ino) else { return reply.error(ENOENT) };
        let write = flags & libc::O_ACCMODE != libc::O_RDONLY;
        let truncate = flags & libc::O_TRUNC != 0;
        let entry = if write {
            match self.rt.block_on(self.vol.open_write(&path, truncate)) {
                Ok(file) => match Self::open_file(&file, true) {
                    Ok(f) => Handle2::File { file: f, path, write: true, modified: truncate },
                    Err(_) => return reply.error(EIO),
                },
                Err(e) => return reply.error(errno(&e)),
            }
        } else {
            match self.rt.block_on(self.vol.open_read(&path)) {
                Ok(Some(file)) => match Self::open_file(&file, false) {
                    Ok(f) => Handle2::File { file: f, path, write: false, modified: false },
                    Err(_) => return reply.error(EIO),
                },
                Ok(None) => Handle2::Piece { path },
                Err(e) => return reply.error(errno(&e)),
            }
        };
        let fh = self.handle(entry);
        reply.opened(fh, 0);
    }

    fn read(&mut self, _req: &Request<'_>, _ino: u64, fh: u64, offset: i64, size: u32, _flags: i32, _lock: Option<u64>, reply: ReplyData) {
        match self.open.get(&fh) {
            Some(Handle2::File { file, .. }) => {
                let mut buf = vec![0u8; size as usize];
                match file.read_at(&mut buf, offset as u64) {
                    Ok(n) => reply.data(&buf[..n]),
                    Err(_) => reply.error(EIO),
                }
            }
            Some(Handle2::Piece { path }) => match self.rt.block_on(self.vol.read_piece(path, offset as u64, size as usize)) {
                Ok(bytes) => reply.data(&bytes),
                Err(e) => reply.error(errno(&e)),
            },
            None => reply.error(EIO),
        }
    }

    fn write(&mut self, _req: &Request<'_>, _ino: u64, fh: u64, offset: i64, data: &[u8], _wf: u32, _flags: i32, _lock: Option<u64>, reply: ReplyWrite) {
        match self.open.get_mut(&fh) {
            Some(Handle2::File { file, path, write: true, modified }) => match file.write_all_at(data, offset as u64) {
                Ok(()) => {
                    *modified = true;
                    self.vol.touched(path);
                    reply.written(data.len() as u32);
                }
                Err(_) => reply.error(EIO),
            },
            _ => reply.error(EIO),
        }
    }

    fn flush(&mut self, _req: &Request<'_>, _ino: u64, fh: u64, _lock: u64, reply: ReplyEmpty) {
        match self.send(fh) {
            Ok(()) => reply.ok(),
            Err(code) => reply.error(code),
        }
    }

    fn release(&mut self, _req: &Request<'_>, _ino: u64, fh: u64, _flags: i32, _lock: Option<u64>, _flush: bool, reply: ReplyEmpty) {
        let _ = self.send(fh);
        self.open.remove(&fh);
        reply.ok();
    }

    fn create(&mut self, _req: &Request<'_>, parent: u64, name: &OsStr, _mode: u32, _umask: u32, _flags: i32, reply: ReplyCreate) {
        let Some(path) = self.child(parent, name) else { return reply.error(ENOENT) };
        match self.rt.block_on(self.vol.create(&path)) {
            Ok(file) => match Self::open_file(&file, true) {
                Ok(f) => {
                    let ino = self.inodes.ino(&path);
                    let item = self.vol.stat(&path).unwrap_or_else(|| Item::file(0, 0));
                    let fh = self.handle(Handle2::File { file: f, path, write: true, modified: true });
                    reply.created(&TTL, &self.attr(ino, &item), 0, fh, 0);
                }
                Err(_) => reply.error(EIO),
            },
            Err(e) => reply.error(errno(&e)),
        }
    }

    fn mkdir(&mut self, _req: &Request<'_>, parent: u64, name: &OsStr, _mode: u32, _umask: u32, reply: ReplyEntry) {
        let Some(path) = self.child(parent, name) else { return reply.error(ENOENT) };
        match self.rt.block_on(self.vol.mkdir(&path)) {
            Ok(()) => {
                let ino = self.inodes.ino(&path);
                reply.entry(&TTL, &self.attr(ino, &Item::folder()), 0);
            }
            Err(e) => reply.error(errno(&e)),
        }
    }

    fn unlink(&mut self, _req: &Request<'_>, parent: u64, name: &OsStr, reply: ReplyEmpty) {
        let Some(path) = self.child(parent, name) else { return reply.error(ENOENT) };
        match self.vol.stat(&path) {
            Some(item) if item.dir => return reply.error(EISDIR),
            None => return reply.error(ENOENT),
            _ => {}
        }
        match self.rt.block_on(self.vol.remove(&path)) {
            Ok(()) => reply.ok(),
            Err(e) => reply.error(errno(&e)),
        }
    }

    fn rmdir(&mut self, _req: &Request<'_>, parent: u64, name: &OsStr, reply: ReplyEmpty) {
        let Some(path) = self.child(parent, name) else { return reply.error(ENOENT) };
        match self.vol.stat(&path) {
            Some(item) if !item.dir => return reply.error(ENOTDIR),
            None => return reply.error(ENOENT),
            _ => {}
        }
        match self.rt.block_on(self.vol.remove(&path)) {
            Ok(()) => reply.ok(),
            Err(e) => reply.error(errno(&e)),
        }
    }

    fn rename(&mut self, _req: &Request<'_>, parent: u64, name: &OsStr, newparent: u64, newname: &OsStr, _flags: u32, reply: ReplyEmpty) {
        let (Some(from), Some(to)) = (self.child(parent, name), self.child(newparent, newname)) else { return reply.error(ENOENT) };
        match self.rt.block_on(self.vol.rename(&from, &to)) {
            Ok(()) => {
                self.inodes.renamed(&from, &to);
                reply.ok();
            }
            Err(e) => reply.error(errno(&e)),
        }
    }

    fn statfs(&mut self, _req: &Request<'_>, _ino: u64, reply: ReplyStatfs) {
        // The NAS's room is not known here: say there is plenty (4 TB).
        reply.statfs(1 << 30, 1 << 30, 1 << 30, 1 << 20, 1 << 20, 4096, 255, 4096);
    }
}

/// Mounts the volume on `mountpoint` (an empty folder, made if needed); the folder shows the
/// NAS's files until the returned session is dropped.
pub fn mount<R: Remote + Send + Sync + 'static>(vol: Arc<Volume<R>>, rt: Handle, mountpoint: &Path) -> Result<fuser::BackgroundSession> {
    unmount(mountpoint); // what an earlier crash left behind
    std::fs::create_dir_all(mountpoint).with_context(|| format!("cannot make {}", mountpoint.display()))?;
    if std::fs::read_dir(mountpoint)?.next().is_some() {
        bail!("{} is not empty: files on demand need an empty folder (move what is in it away first).", mountpoint.display());
    }
    let options = [MountOption::FSName("alvaos".into()), MountOption::Subtype("alvaos".into()), MountOption::DefaultPermissions, MountOption::NoAtime];
    fuser::spawn_mount2(AlvaFs::new(vol, rt), mountpoint, &options)
        .map_err(|e| anyhow::anyhow!("The folder could not be mounted ({e}). Is fuse3 installed (fusermount3)?"))
}

/// Lets go of a mount of ours (also a dead one).
pub fn unmount(mountpoint: &Path) {
    for tool in ["fusermount3", "fusermount"] {
        if std::process::Command::new(tool).args(["-u", "-z"]).arg(mountpoint).output().map(|o| o.status.success()).unwrap_or(false) {
            break;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ondemand::Cache;
    use crate::sync::tests::Mem;

    /// A real mount, when this machine allows it (a /dev/fuse and fusermount3); else skipped.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn the_folder_shows_the_nas_and_changes_go_back() {
        let top = std::env::temp_dir().join(format!("alvaos-fuse-{}", crate::link::hex(&crate::link::new_key()[..6])));
        let (mountpoint, cache) = (top.join("mnt"), top.join("cache"));
        let nas = Arc::new(Mem::default());
        nas.put("Docs/letter.txt", "dear anna", 1_790_000_000);
        nas.put("readme.txt", "hello", 1_790_000_000);
        let vol = Arc::new(Volume::new(nas.clone(), Cache::open(&cache).unwrap(), u64::MAX));
        vol.refresh().await.unwrap();
        let session = match mount(vol.clone(), Handle::current(), &mountpoint) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("skipped: no FUSE here ({e})");
                let _ = std::fs::remove_dir_all(&top);
                return;
            }
        };
        let mp = mountpoint.clone();
        tokio::task::spawn_blocking(move || {
            use std::io::Write;
            let names = |dir: &Path| {
                let mut v: Vec<String> = std::fs::read_dir(dir).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().to_string()).collect();
                v.sort();
                v
            };
            assert_eq!(names(&mp), ["Docs", "readme.txt"]);
            assert_eq!(std::fs::read_to_string(mp.join("Docs/letter.txt")).unwrap(), "dear anna");
            assert_eq!(std::fs::metadata(mp.join("readme.txt")).unwrap().len(), 5);
            // Make a file, change one, make a folder, rename, delete.
            std::fs::write(mp.join("new.txt"), "brand new").unwrap();
            let mut f = std::fs::OpenOptions::new().append(true).open(mp.join("readme.txt")).unwrap();
            f.write_all(b", world").unwrap();
            drop(f);
            std::fs::create_dir(mp.join("Archive")).unwrap();
            std::fs::rename(mp.join("Docs/letter.txt"), mp.join("Archive/letter.txt")).unwrap();
            std::fs::write(mp.join("gone.txt"), "x").unwrap();
            std::fs::remove_file(mp.join("gone.txt")).unwrap();
            assert!(std::fs::remove_dir(mp.join("Archive")).is_err(), "not empty");
            assert_eq!(std::fs::read_to_string(mp.join("Archive/letter.txt")).unwrap(), "dear anna");
            assert_eq!(names(&mp), ["Archive", "Docs", "new.txt", "readme.txt"]);
        })
        .await
        .unwrap();
        assert_eq!(nas.body("new.txt").as_deref(), Some("brand new"));
        assert_eq!(nas.body("readme.txt").as_deref(), Some("hello, world"));
        assert!(nas.has("Archive") && nas.has("Archive/letter.txt") && !nas.has("Docs/letter.txt") && !nas.has("gone.txt"));
        eprintln!("mounted for real, and everything held");
        drop(session);
        let _ = std::fs::remove_dir_all(&top);
    }
}
