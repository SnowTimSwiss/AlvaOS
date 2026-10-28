//! Files on demand: a folder of the NAS that looks complete on this computer, with every
//! file in it, but only holds what was opened. Opening a file fetches it (a big one is read
//! where it is, piece by piece); what is saved goes back to the NAS when the file is closed;
//! files marked "always keep" are fetched ahead and stay; the rest can be freed again (the
//! oldest first, when the cache is over its size) and is simply fetched again when needed.
//!
//! This module is the part that does not depend on the system: the cache (a folder of
//! fetched files with an index), the picture of the NAS's tree (the catalog), and the
//! [`Volume`] that answers "what is in this folder", "give me this file", "save this file",
//! "make, rename, delete". The system's own door to it is a file system in the user's
//! space (`fusefs.rs`, Linux) or the Windows cloud files API.
//!
//! Nothing is lost: a file that could not go up yet stays in the cache, marked, and is tried
//! again (also after a restart); deleting goes to the NAS's trash like everywhere else.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};

use crate::sync::{Filter, Item, Remote, Tree};

/// Files above this size are read where they are (a piece at a time) until they are kept.
pub const BIG: u64 = 64 * 1024 * 1024;
const SLACK: i64 = 2;

// ── Errors a file system has numbers for ──────────────────────────────────────

pub const ENOENT: i32 = 2;
pub const EIO: i32 = 5;
pub const EACCES: i32 = 13;
pub const EEXIST: i32 = 17;
pub const ENOTDIR: i32 = 20;
pub const EISDIR: i32 = 21;
pub const EROFS: i32 = 30;
pub const ENOTEMPTY: i32 = 39;

#[derive(Debug)]
pub struct Fail {
    pub code: i32,
    pub why: &'static str,
}

impl std::fmt::Display for Fail {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.why)
    }
}

impl std::error::Error for Fail {}

fn fail(code: i32, why: &'static str) -> anyhow::Error {
    Fail { code, why }.into()
}

/// The error number for a file system: the one a [`Fail`] carries, else "input/output error".
pub fn errno(e: &anyhow::Error) -> i32 {
    e.downcast_ref::<Fail>().map(|f| f.code).unwrap_or(EIO)
}

// ── The cache ─────────────────────────────────────────────────────────────────

/// What the cache knows about one file it holds.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Held {
    pub size: u64,
    pub mtime: i64,
    /// Saved here, not on the NAS yet: the only copy of the changes.
    pub dirty: bool,
    /// When it was last used (seconds since 1970): the oldest go first.
    pub used: i64,
}

/// A file name for a path: FNV-1a over 128 bits, in hex (the same path, the same name, always).
fn name_of(path: &str) -> String {
    let mut hash: u128 = 0x6c62272e07bb014262b821756295c58d;
    for byte in path.bytes() {
        hash ^= byte as u128;
        hash = hash.wrapping_mul(0x0000000001000000000000000000013b);
    }
    format!("{hash:032x}")
}

fn now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

pub struct Cache {
    dir: PathBuf,
    index: PathBuf,
    held: Mutex<BTreeMap<String, Held>>,
    keep: Mutex<Filter>,
}

impl Cache {
    /// The cache in `dir`: its files and `index.json`; what the index does not know is removed,
    /// what it knows but is gone is forgotten.
    pub fn open(dir: &Path) -> Result<Self> {
        std::fs::create_dir_all(dir.join("files")).with_context(|| format!("cannot make {}", dir.display()))?;
        let index = dir.join("index.json");
        let mut held: BTreeMap<String, Held> =
            std::fs::read_to_string(&index).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default();
        let cache = Self { dir: dir.to_path_buf(), index, held: Mutex::new(BTreeMap::new()), keep: Mutex::new(Filter::default()) };
        held.retain(|path, _| cache.file(path).is_file());
        // What waits to go up may have been written to after the index was saved: the file is the truth.
        for (path, h) in held.iter_mut().filter(|(_, h)| h.dirty) {
            if let Ok(meta) = std::fs::metadata(cache.file(path)) {
                h.size = meta.len();
                h.mtime = mtime_of(&meta);
            }
        }
        let known: BTreeSet<PathBuf> = held.keys().map(|p| cache.file(p)).collect();
        if let Ok(entries) = std::fs::read_dir(dir.join("files")) {
            for entry in entries.flatten() {
                if !known.contains(&entry.path()) {
                    let _ = std::fs::remove_file(entry.path());
                }
            }
        }
        *cache.held.lock().unwrap() = held;
        cache.save();
        Ok(cache)
    }

    /// Where the copy of a file is (or would be): named by what the path hashes to.
    pub fn file(&self, path: &str) -> PathBuf {
        self.dir.join("files").join(name_of(path))
    }

    fn save(&self) {
        let held = self.held.lock().unwrap();
        let tmp = self.dir.join(".index.tmp");
        if let Ok(text) = serde_json::to_string(&*held) {
            if std::fs::write(&tmp, text).is_ok() {
                let _ = std::fs::rename(&tmp, &self.index);
            }
        }
    }

    pub fn held(&self, path: &str) -> Option<Held> {
        self.held.lock().unwrap().get(path).cloned()
    }

    /// Is the copy here the same as the NAS's (or newer, still waiting to go up)?
    pub fn fresh(&self, path: &str, item: &Item) -> bool {
        match self.held(path) {
            Some(h) => h.dirty || (h.size == item.size && (h.mtime - item.mtime).abs() <= SLACK),
            None => false,
        }
    }

    pub fn record(&self, path: &str, size: u64, mtime: i64, dirty: bool) {
        self.held.lock().unwrap().insert(path.to_string(), Held { size, mtime, dirty, used: now() });
        self.save();
    }

    /// New size and date for a file already marked as waiting (not written to the index).
    pub fn update(&self, path: &str, size: u64, mtime: i64) {
        if let Some(h) = self.held.lock().unwrap().get_mut(path) {
            h.size = size;
            h.mtime = mtime;
            h.used = now();
        }
    }

    pub fn touch(&self, path: &str) {
        if let Some(h) = self.held.lock().unwrap().get_mut(path) {
            h.used = now();
        }
    }

    /// The copy and its entry go (not the NAS's file).
    pub fn forget(&self, path: &str) {
        self.held.lock().unwrap().remove(path);
        let _ = std::fs::remove_file(self.file(path));
        self.save();
    }

    pub fn forget_below(&self, path: &str) {
        let prefix = format!("{path}/");
        let gone: Vec<String> = self.held.lock().unwrap().keys().filter(|k| *k == path || k.starts_with(&prefix)).cloned().collect();
        for p in gone {
            self.forget(&p);
        }
    }

    /// A file or a folder was renamed: the copies follow.
    pub fn rename(&self, from: &str, to: &str) {
        let prefix = format!("{from}/");
        let moving: Vec<String> = self.held.lock().unwrap().keys().filter(|k| *k == from || k.starts_with(&prefix)).cloned().collect();
        for old in moving {
            let new = format!("{to}{}", &old[from.len()..]);
            let _ = std::fs::rename(self.file(&old), self.file(&new));
            let mut held = self.held.lock().unwrap();
            if let Some(h) = held.remove(&old) {
                held.insert(new, h);
            }
        }
        self.save();
    }

    /// Files to always keep, one pattern per line (see [`Filter`]).
    pub fn set_keep(&self, lines: &[String]) {
        *self.keep.lock().unwrap() = Filter::new(lines);
    }

    pub fn is_kept(&self, path: &str) -> bool {
        self.keep.lock().unwrap().matches(path)
    }

    /// Bytes held here.
    pub fn used(&self) -> u64 {
        self.held.lock().unwrap().values().map(|h| h.size).sum()
    }

    pub fn dirty(&self) -> Vec<String> {
        self.held.lock().unwrap().iter().filter(|(_, h)| h.dirty).map(|(p, _)| p.clone()).collect()
    }

    /// Frees copies until at most `limit` bytes are held: the longest unused first, never the
    /// ones to keep and never the ones still waiting to go up. Returns the bytes freed.
    pub fn evict(&self, limit: u64) -> u64 {
        let mut order: Vec<(i64, String, u64)> = {
            let held = self.held.lock().unwrap();
            held.iter().filter(|(p, h)| !h.dirty && !self.is_kept(p)).map(|(p, h)| (h.used, p.clone(), h.size)).collect()
        };
        order.sort();
        let mut over = self.used().saturating_sub(limit);
        let mut freed = 0;
        for (_, path, size) in order {
            if over == 0 {
                break;
            }
            self.forget(&path);
            over = over.saturating_sub(size);
            freed += size;
        }
        freed
    }

    /// "Free up space": every copy that is neither to keep nor waiting to go up.
    pub fn free_all(&self) -> u64 {
        self.evict(0)
    }
}

// ── The NAS's tree, as this computer knows it ─────────────────────────────────

#[derive(Default)]
struct Catalog {
    items: BTreeMap<String, Item>,
    kids: BTreeMap<String, BTreeSet<String>>,
}

fn split(path: &str) -> (&str, &str) {
    path.rsplit_once('/').unwrap_or(("", path))
}

impl Catalog {
    fn from(tree: Tree) -> Self {
        let mut catalog = Catalog::default();
        for (path, item) in tree {
            catalog.insert(&path, item);
        }
        catalog
    }

    fn insert(&mut self, path: &str, item: Item) {
        let (parent, name) = split(path);
        self.kids.entry(parent.to_string()).or_default().insert(name.to_string());
        self.items.insert(path.to_string(), item);
    }

    /// The path and everything below it.
    fn remove_below(&mut self, path: &str) {
        let prefix = format!("{path}/");
        let gone: Vec<String> = self.items.range(prefix.clone()..).take_while(|(k, _)| k.starts_with(&prefix)).map(|(k, _)| k.clone()).collect();
        for p in gone.into_iter().chain(std::iter::once(path.to_string())) {
            self.items.remove(&p);
            self.kids.remove(&p);
            let (parent, name) = split(&p);
            if let Some(set) = self.kids.get_mut(parent) {
                set.remove(name);
            }
        }
    }

    fn children(&self, path: &str) -> Vec<(String, Item)> {
        let Some(names) = self.kids.get(path) else { return Vec::new() };
        names
            .iter()
            .filter_map(|name| {
                let full = if path.is_empty() { name.clone() } else { format!("{path}/{name}") };
                self.items.get(&full).map(|item| (name.clone(), item.clone()))
            })
            .collect()
    }
}

// ── The volume ────────────────────────────────────────────────────────────────

pub struct Volume<R: Remote> {
    remote: Arc<R>,
    pub cache: Cache,
    cat: Mutex<Catalog>,
    busy: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
    /// How much the cache may hold (bytes) before the oldest copies are freed.
    pub limit: u64,
}

fn mtime_of(meta: &std::fs::Metadata) -> i64 {
    filetime::FileTime::from_last_modification_time(meta).unix_seconds()
}

impl<R: Remote + Send + Sync + 'static> Volume<R> {
    pub fn new(remote: Arc<R>, cache: Cache, limit: u64) -> Self {
        Self { remote, cache, cat: Mutex::new(Catalog::default()), busy: Mutex::new(HashMap::new()), limit }
    }

    pub fn read_only(&self) -> bool {
        self.remote.read_only()
    }

    /// The NAS itself (for a system that keeps the files itself: Windows' cloud files).
    pub fn remote(&self) -> &Arc<R> {
        &self.remote
    }

    /// Everything the catalog has, parents before their children.
    pub fn items(&self) -> Vec<(String, Item)> {
        self.cat.lock().unwrap().items.iter().map(|(p, i)| (p.clone(), i.clone())).collect()
    }

    /// Something is on the NAS now (a file this computer just sent, a folder it made).
    pub fn note(&self, path: &str, item: Item) {
        self.cat.lock().unwrap().insert(path, item);
    }

    fn lock_for(&self, path: &str) -> Arc<tokio::sync::Mutex<()>> {
        self.busy.lock().unwrap().entry(path.to_string()).or_default().clone()
    }

    /// What the NAS has now. A listing that may be incomplete changes nothing. What is waiting
    /// to go up stays as it is here; copies of what is gone on the NAS are freed.
    pub async fn refresh(&self) -> Result<bool> {
        let (tree, complete) = self.remote.tree().await?;
        if !complete {
            return Ok(false);
        }
        let mut tree = tree;
        for path in self.cache.dirty() {
            if let Some(held) = self.cache.held(&path) {
                tree.insert(path.clone(), Item::file(held.size, held.mtime));
            }
        }
        let gone: Vec<String> = {
            let held = self.cache.held.lock().unwrap();
            held.iter().filter(|(p, h)| !h.dirty && !tree.contains_key(*p)).map(|(p, _)| p.clone()).collect()
        };
        for path in gone {
            self.cache.forget(&path);
        }
        *self.cat.lock().unwrap() = Catalog::from(tree);
        Ok(true)
    }

    pub fn stat(&self, path: &str) -> Option<Item> {
        if path.is_empty() {
            return Some(Item::folder());
        }
        self.cat.lock().unwrap().items.get(path).cloned()
    }

    pub fn children(&self, path: &str) -> Vec<(String, Item)> {
        self.cat.lock().unwrap().children(path)
    }

    /// How many files the folder has, and how many bytes are held here.
    pub fn counts(&self) -> (usize, u64) {
        (self.cat.lock().unwrap().items.values().filter(|i| !i.dir).count(), self.cache.used())
    }

    /// The whole file, fetched if it is not here (or here an older one).
    pub async fn hydrate(&self, path: &str) -> Result<PathBuf> {
        let item = self.stat(path).ok_or_else(|| fail(ENOENT, "not there"))?;
        if item.dir {
            return Err(fail(EISDIR, "a folder"));
        }
        let lock = self.lock_for(path);
        let _one = lock.lock().await;
        let file = self.cache.file(path);
        if self.cache.fresh(path, &item) && file.is_file() {
            self.cache.touch(path);
            return Ok(file);
        }
        let part = file.with_extension("part");
        if let Err(e) = self.remote.download(path, &part).await {
            let _ = std::fs::remove_file(&part);
            return Err(e);
        }
        let _ = filetime::set_file_mtime(&part, filetime::FileTime::from_unix_time(item.mtime, 0));
        std::fs::rename(&part, &file)?;
        self.cache.record(path, item.size, item.mtime, false);
        self.cache.evict(self.limit);
        Ok(file)
    }

    /// Opening to read: the copy here, or (a big file, not kept here) None: read it in pieces.
    pub async fn open_read(&self, path: &str) -> Result<Option<PathBuf>> {
        let item = self.stat(path).ok_or_else(|| fail(ENOENT, "not there"))?;
        if item.dir {
            return Err(fail(EISDIR, "a folder"));
        }
        if item.size > BIG && !self.cache.fresh(path, &item) && !self.cache.is_kept(path) {
            return Ok(None);
        }
        Ok(Some(self.hydrate(path).await?))
    }

    /// A piece of a file that is not held here.
    pub async fn read_piece(&self, path: &str, offset: u64, len: usize) -> Result<Vec<u8>> {
        self.remote.read_range(path, offset, len).await
    }

    fn writable(&self) -> Result<()> {
        if self.remote.read_only() {
            return Err(fail(EROFS, "this shared folder is read only"));
        }
        Ok(())
    }

    /// A new, empty file (its copy here; it goes up when it is closed).
    pub async fn create(&self, path: &str) -> Result<PathBuf> {
        self.writable()?;
        let (parent, _) = split(path);
        match self.stat(parent) {
            Some(p) if p.dir => {}
            Some(_) => return Err(fail(ENOTDIR, "not a folder")),
            None => return Err(fail(ENOENT, "no such folder")),
        }
        if self.stat(path).is_some() {
            return Err(fail(EEXIST, "already there"));
        }
        let file = self.cache.file(path);
        std::fs::File::create(&file)?;
        self.cache.record(path, 0, now(), true);
        self.cat.lock().unwrap().insert(path, Item::file(0, now()));
        Ok(file)
    }

    /// Opening to change: the copy here (fetched first unless the file is emptied anyway).
    pub async fn open_write(&self, path: &str, truncate: bool) -> Result<PathBuf> {
        self.writable()?;
        let item = self.stat(path).ok_or_else(|| fail(ENOENT, "not there"))?;
        if item.dir {
            return Err(fail(EISDIR, "a folder"));
        }
        let file = if truncate && !self.cache.fresh(path, &item) {
            let f = self.cache.file(path);
            std::fs::File::create(&f)?;
            f
        } else {
            self.hydrate(path).await?
        };
        if truncate {
            std::fs::OpenOptions::new().write(true).open(&file)?.set_len(0)?;
        }
        self.touched(path);
        Ok(file)
    }

    /// The copy here was written to: it is the newer one now, and says how big it is.
    pub fn touched(&self, path: &str) {
        let file = self.cache.file(path);
        let Ok(meta) = std::fs::metadata(&file) else { return };
        let (size, mtime) = (meta.len(), mtime_of(&meta).max(now()));
        if self.cache.held(path).is_some_and(|h| h.dirty) {
            self.cache.update(path, size, mtime);      // already marked: no need to write the index for every write
        } else {
            self.cache.record(path, size, mtime, true);
        }
        self.cat.lock().unwrap().insert(path, Item::file(size, mtime));
    }

    /// The file was closed: what was changed goes to the NAS. Stays marked when that fails.
    pub async fn finish(&self, path: &str) -> Result<()> {
        let Some(held) = self.cache.held(path) else { return Ok(()) };
        if !held.dirty {
            return Ok(());
        }
        self.writable()?;
        let lock = self.lock_for(path);
        let _one = lock.lock().await;
        let file = self.cache.file(path);
        let before = std::fs::metadata(&file)?;
        let (size, mtime) = (before.len(), mtime_of(&before));
        self.remote.upload(path, &file, mtime).await?;
        // Changed again while it went up? Then the new changes are still to go.
        let after = std::fs::metadata(&file)?;
        let again = after.len() != size || mtime_of(&after) != mtime;
        self.cache.record(path, after.len(), mtime_of(&after), again);
        self.cat.lock().unwrap().insert(path, Item::file(after.len(), mtime_of(&after)));
        Ok(())
    }

    /// Everything that is waiting to go up, tried again. Returns how many went.
    pub async fn flush(&self) -> usize {
        let mut sent = 0;
        for path in self.cache.dirty() {
            if self.finish(&path).await.is_ok() {
                sent += 1;
            }
        }
        sent
    }

    pub async fn mkdir(&self, path: &str) -> Result<()> {
        self.writable()?;
        let (parent, _) = split(path);
        match self.stat(parent) {
            Some(p) if p.dir => {}
            Some(_) => return Err(fail(ENOTDIR, "not a folder")),
            None => return Err(fail(ENOENT, "no such folder")),
        }
        if self.stat(path).is_some() {
            return Err(fail(EEXIST, "already there"));
        }
        self.remote.mkdir(path).await?;
        self.cat.lock().unwrap().insert(path, Item::folder());
        Ok(())
    }

    /// A file or an empty folder: deleted on the NAS (into its trash) and gone from here.
    pub async fn remove(&self, path: &str) -> Result<()> {
        self.writable()?;
        let item = self.stat(path).ok_or_else(|| fail(ENOENT, "not there"))?;
        if item.dir && !self.children(path).is_empty() {
            return Err(fail(ENOTEMPTY, "the folder is not empty"));
        }
        let never_went_up = self.cache.held(path).is_some_and(|h| h.dirty) && !item.dir;
        if !never_went_up {
            self.remote.delete(path).await?;
        } else {
            let _ = self.remote.delete(path).await; // it may have gone up once before
        }
        self.cat.lock().unwrap().remove_below(path);
        self.cache.forget_below(path);
        Ok(())
    }

    pub async fn rename(&self, from: &str, to: &str) -> Result<()> {
        self.writable()?;
        let item = self.stat(from).ok_or_else(|| fail(ENOENT, "not there"))?;
        let (parent, _) = split(to);
        if !self.stat(parent).is_some_and(|p| p.dir) {
            return Err(fail(ENOENT, "no such folder"));
        }
        if let Some(target) = self.stat(to) {
            if target.dir && (!item.dir || !self.children(to).is_empty()) {
                return Err(fail(if item.dir { ENOTEMPTY } else { EISDIR }, "the target is a folder"));
            }
        }
        // Changes that never went up must be on the NAS to be moved there.
        if self.cache.held(from).is_some_and(|h| h.dirty) {
            self.finish(from).await?;
        }
        self.remote.rename(from, to).await?;
        let mut cat = self.cat.lock().unwrap();
        let prefix = format!("{from}/");
        let moved: Vec<(String, Item)> = cat
            .items
            .iter()
            .filter(|(k, _)| k.as_str() == from || k.starts_with(&prefix))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        cat.remove_below(to);
        cat.remove_below(from);
        for (old, item) in moved {
            cat.insert(&format!("{to}{}", &old[from.len()..]), item);
        }
        drop(cat);
        self.cache.forget_below(to);
        self.cache.rename(from, to);
        Ok(())
    }

    /// Fetches what is to be kept (and not here yet), then frees what the cache is over by.
    /// Returns how many files were fetched.
    pub async fn keep_up(&self) -> usize {
        let wanted: Vec<String> = {
            let cat = self.cat.lock().unwrap();
            cat.items.iter().filter(|(p, i)| !i.dir && self.cache.is_kept(p) && !self.cache.fresh(p, i)).map(|(p, _)| p.clone()).collect()
        };
        let mut fetched = 0;
        for path in wanted {
            if self.hydrate(&path).await.is_ok() {
                fetched += 1;
            }
        }
        self.cache.evict(self.limit);
        fetched
    }
}

// ── Helpers for systems that keep the files themselves (Windows' cloud files) ───

/// The path of `path` below `root` as the catalog writes it (`a/b.txt`), or None outside it.
pub fn rel_path(root: &Path, path: &Path) -> Option<String> {
    let rest = path.strip_prefix(root).ok()?;
    let parts: Vec<String> = rest.components().filter_map(|c| match c {
        std::path::Component::Normal(n) => Some(n.to_string_lossy().to_string()),
        _ => None,
    }).collect();
    Some(parts.join("/"))
}

/// Windows' own time: 100 ns steps since 1601.
pub fn nt_ticks(unix_secs: i64) -> u64 {
    ((unix_secs.max(0) as u64) + 11_644_473_600) * 10_000_000
}

/// What to fetch when a program asks for `want` of a file of `size` bytes: at least a block
/// (a film read from the start is not asked for 4 KB at a time), starting and ending on the
/// 4 KB steps the system requires (or at the end of the file).
pub fn widen(want: std::ops::Range<u64>, size: u64) -> std::ops::Range<u64> {
    const STEP: u64 = 4096;
    const BLOCK: u64 = 1024 * 1024;
    if size <= 8 * BLOCK {
        return 0..size;
    }
    let start = want.start / STEP * STEP;
    let end = want.end.max(start + BLOCK).min(size);
    let end = if end == size { size } else { end.div_ceil(STEP) * STEP };
    start..end.min(size)
}

/// An error that is a plain sentence (for the app's status line).
pub fn explain(e: &anyhow::Error) -> String {
    e.chain().map(|c| c.to_string()).next().unwrap_or_else(|| anyhow!("unknown").to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sync::tests::Mem;

    const T: i64 = 1_790_000_000;

    fn temp() -> PathBuf {
        std::env::temp_dir().join(format!("alvaos-ondemand-{}", crate::link::hex(&crate::link::new_key()[..6])))
    }

    async fn volume(nas: Arc<Mem>, limit: u64) -> (Volume<Mem>, PathBuf) {
        let dir = temp();
        let v = Volume::new(nas, Cache::open(&dir).unwrap(), limit);
        assert!(v.refresh().await.unwrap());
        (v, dir)
    }

    #[tokio::test]
    async fn the_folder_looks_complete_and_a_file_is_fetched_when_opened() {
        let nas = Arc::new(Mem::default());
        nas.put("Photos/2024/beach.jpg", "beach", T);
        nas.put("notes.txt", "hello", T);
        let (v, dir) = volume(nas.clone(), u64::MAX).await;
        assert_eq!(v.children("").iter().map(|(n, _)| n.as_str()).collect::<Vec<_>>(), ["Photos", "notes.txt"]);
        assert_eq!(v.stat("Photos/2024/beach.jpg").unwrap().size, 5);
        assert_eq!(v.counts(), (2, 0), "nothing is held yet");
        let file = v.open_read("notes.txt").await.unwrap().unwrap();
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "hello");
        assert_eq!(v.counts(), (2, 5));
        // Fresh: not fetched again; changed on the NAS: fetched again.
        nas.files.lock().unwrap().get_mut("notes.txt").unwrap().1 = b"hello again".to_vec();
        nas.files.lock().unwrap().get_mut("notes.txt").unwrap().0 = Item::file(11, T + 50);
        assert_eq!(std::fs::read_to_string(v.hydrate("notes.txt").await.unwrap()).unwrap(), "hello", "same as before: still the held copy");
        assert!(v.refresh().await.unwrap());
        assert_eq!(std::fs::read_to_string(v.hydrate("notes.txt").await.unwrap()).unwrap(), "hello again");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn saving_a_file_sends_it_and_a_failed_send_stays_marked() {
        let nas = Arc::new(Mem::default());
        nas.put("a.txt", "old", T);
        let (v, dir) = volume(nas.clone(), u64::MAX).await;
        let file = v.open_write("a.txt", false).await.unwrap();
        std::fs::write(&file, "new and longer").unwrap();
        v.touched("a.txt");
        assert_eq!(v.stat("a.txt").unwrap().size, 14, "the folder shows the new size at once");
        assert_eq!(nas.body("a.txt").as_deref(), Some("old"), "nothing goes up before it is closed");
        v.finish("a.txt").await.unwrap();
        assert_eq!(nas.body("a.txt").as_deref(), Some("new and longer"));
        assert!(v.cache.dirty().is_empty());
        // A new file.
        let made = v.create("b.txt").await.unwrap();
        std::fs::write(&made, "fresh").unwrap();
        v.touched("b.txt");
        assert!(v.create("b.txt").await.is_err(), "it is there now");
        v.finish("b.txt").await.unwrap();
        assert_eq!(nas.body("b.txt").as_deref(), Some("fresh"));
        // Read only: nothing is changed.
        let ro = Arc::new(Mem { read_only: true, ..Mem::default() });
        ro.put("x.txt", "x", T);
        let (rv, dir2) = volume(ro, u64::MAX).await;
        assert_eq!(errno(&rv.open_write("x.txt", false).await.unwrap_err()), EROFS);
        assert_eq!(errno(&rv.remove("x.txt").await.unwrap_err()), EROFS);
        let _ = std::fs::remove_dir_all(dir);
        let _ = std::fs::remove_dir_all(dir2);
    }

    #[tokio::test]
    async fn a_file_that_cannot_go_up_yet_is_kept_and_sent_later_even_after_a_restart() {
        let nas = Arc::new(Mem::default());
        let (v, dir) = volume(nas.clone(), u64::MAX).await;
        let file = v.create("draft.txt").await.unwrap();
        std::fs::write(&file, "my work").unwrap();
        v.touched("draft.txt");
        // The app stops before it went up: the index remembers.
        drop(v);
        let v = Volume::new(nas.clone(), Cache::open(&dir).unwrap(), u64::MAX);
        assert_eq!(v.cache.dirty(), ["draft.txt"]);
        assert!(v.refresh().await.unwrap());
        assert_eq!(v.stat("draft.txt").unwrap().size, 7, "still in the folder, though the NAS does not have it");
        assert_eq!(v.flush().await, 1);
        assert_eq!(nas.body("draft.txt").as_deref(), Some("my work"));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn the_oldest_copies_go_when_the_cache_is_full_but_not_the_ones_to_keep() {
        let nas = Arc::new(Mem::default());
        for (i, name) in ["a", "b", "c", "keep"].iter().enumerate() {
            nas.put(name, &"x".repeat(10), T + i as i64);
        }
        let (v, dir) = volume(nas, 20).await;
        v.cache.set_keep(&["keep".to_string()]);
        for name in ["keep", "a", "b"] {
            v.hydrate(name).await.unwrap();
            std::thread::sleep(std::time::Duration::from_millis(1100));
        }
        v.hydrate("c").await.unwrap();           // over the limit: the oldest that may go, goes
        assert!(v.cache.held("keep").is_some() && v.cache.held("c").is_some());
        assert!(v.cache.held("a").is_none(), "the oldest unkept copy is freed");
        assert!(v.cache.used() <= 20 + 10);
        v.cache.free_all();
        assert!(v.cache.held("keep").is_some() && v.cache.held("c").is_none(), "free up space keeps what is to be kept");
        // Kept files are fetched ahead.
        v.cache.forget("keep");
        assert_eq!(v.keep_up().await, 1);
        assert!(v.cache.held("keep").is_some());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn renaming_deleting_and_folders() {
        let nas = Arc::new(Mem::default());
        nas.put("Docs/a.txt", "A", T);
        nas.put("Docs/sub/b.txt", "B", T);
        let (v, dir) = volume(nas.clone(), u64::MAX).await;
        v.hydrate("Docs/a.txt").await.unwrap();
        v.mkdir("New").await.unwrap();
        assert!(nas.has("New") && v.stat("New").unwrap().dir);
        assert_eq!(errno(&v.mkdir("New").await.unwrap_err()), EEXIST);
        assert_eq!(errno(&v.mkdir("Nope/deep").await.unwrap_err()), ENOENT);
        v.rename("Docs", "Moved").await.unwrap();
        assert!(nas.has("Moved/a.txt") && nas.has("Moved/sub/b.txt") && !nas.has("Docs/a.txt"));
        assert!(v.stat("Docs").is_none() && v.stat("Moved/sub/b.txt").is_some());
        assert!(v.cache.held("Moved/a.txt").is_some(), "the copy followed");
        assert_eq!(std::fs::read_to_string(v.hydrate("Moved/a.txt").await.unwrap()).unwrap(), "A");
        assert_eq!(errno(&v.remove("Moved").await.unwrap_err()), ENOTEMPTY);
        v.remove("Moved/a.txt").await.unwrap();
        assert!(nas.deleted.lock().unwrap().contains(&"Moved/a.txt".to_string()));
        assert!(v.stat("Moved/a.txt").is_none() && v.cache.held("Moved/a.txt").is_none());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn a_big_file_is_read_in_pieces_until_it_is_kept() {
        let nas = Arc::new(Mem::default());
        nas.put("movie.mkv", "0123456789", T);
        nas.files.lock().unwrap().get_mut("movie.mkv").unwrap().0 = Item::file(BIG + 1, T);
        let (v, dir) = volume(nas, u64::MAX).await;
        assert!(v.open_read("movie.mkv").await.unwrap().is_none());
        assert_eq!(v.read_piece("movie.mkv", 2, 3).await.unwrap(), b"234");
        v.cache.set_keep(&["*.mkv".to_string()]);
        assert!(v.open_read("movie.mkv").await.unwrap().is_some(), "kept: fetched whole");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn an_incomplete_listing_changes_nothing_and_what_vanished_on_the_nas_is_freed() {
        let nas = Arc::new(Mem::default());
        nas.put("a.txt", "A", T);
        let (v, dir) = volume(nas.clone(), u64::MAX).await;
        v.hydrate("a.txt").await.unwrap();
        nas.files.lock().unwrap().clear();
        let part = Arc::new(Mem { incomplete: true, ..Mem::default() });
        let dir2 = temp();
        let v2 = Volume::new(part, Cache::open(&dir2).unwrap(), u64::MAX);
        assert!(!v2.refresh().await.unwrap(), "an incomplete listing is not taken");
        assert!(v.refresh().await.unwrap());
        assert!(v.stat("a.txt").is_none() && v.cache.held("a.txt").is_none());
        let _ = std::fs::remove_dir_all(dir);
        let _ = std::fs::remove_dir_all(dir2);
    }

    #[test]
    fn paths_times_and_ranges_for_the_system_that_keeps_the_files() {
        let root = Path::new("/sync/AlvaOS");
        assert_eq!(rel_path(root, Path::new("/sync/AlvaOS/Docs/a.txt")).as_deref(), Some("Docs/a.txt"));
        assert_eq!(rel_path(root, Path::new("/sync/AlvaOS")).as_deref(), Some(""));
        assert_eq!(rel_path(root, Path::new("/elsewhere/a.txt")), None);
        assert_eq!(nt_ticks(0), 116_444_736_000_000_000);
        assert_eq!(nt_ticks(1_790_000_000), (1_790_000_000u64 + 11_644_473_600) * 10_000_000);
        // A small file is fetched whole; a big one in blocks on 4 KB steps, never past its end.
        assert_eq!(widen(0..10, 100), 0..100);
        let big = 100 * 1024 * 1024;
        assert_eq!(widen(5000..5100, big), 4096..4096 + 1024 * 1024);
        assert_eq!(widen(big - 10..big, big), (big - 10) / 4096 * 4096..big);
        assert_eq!(widen(0..3 * 1024 * 1024, big), 0..3 * 1024 * 1024);
    }
}
