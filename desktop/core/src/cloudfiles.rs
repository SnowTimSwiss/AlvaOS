//! Files on demand on Windows, the way OneDrive does it: the sync folder shows every file of
//! the NAS as a placeholder (Explorer shows the cloud icon); opening one makes Windows ask
//! this program for the data, which is fetched from the NAS, a piece at a time for a big file.
//! "Always keep on this device" and "Free up space" in Explorer's own menu work as for
//! OneDrive. This is Windows' Cloud Files API (cfapi) through the `cloud-filter` crate.
//!
//! What the person does in the folder (delete, rename or move a placeholder) is asked of this
//! program first and done on the NAS (deleting goes to its trash). New and changed files are
//! found by looking at the folder each round and sent; a file sent becomes a placeholder
//! that is "in sync". What changed on the NAS is brought into the folder as placeholders
//! (new files, changed files; what was deleted there goes here too when it is in sync).
//! A file changed on both sides keeps the local one beside it ("name (conflict …)").
//!
//! The data lives in the files themselves (Windows keeps them), so the volume's own cache is
//! not used here; the volume only holds the picture of the NAS's tree and does the changes.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use anyhow::{anyhow, bail, Context, Result};
use cloud_filter::error::{CResult, CloudErrorKind};
use cloud_filter::filter::{info, ticket, Request, SyncFilter};
use cloud_filter::metadata::Metadata;
use cloud_filter::placeholder::{ConvertOptions, Placeholder, UpdateOptions};
use cloud_filter::placeholder_file::PlaceholderFile;
use cloud_filter::root::{Connection, HydrationType, PopulationType, SecurityId, Session, SyncRootId, SyncRootIdBuilder, SyncRootInfo};
use cloud_filter::utility::WriteAt;
use nt_time::FileTime;
use tokio::runtime::Handle;

use crate::ondemand::{nt_ticks, rel_path, widen, Volume};
use crate::sync::{self, Filter, Item, Remote};

const PROVIDER: &str = "AlvaOS";
/// Written to Windows in pieces of this size.
const CHUNK: u64 = 4 * 1024 * 1024;
/// A file changed less than this long ago may still be being written: next round.
const SETTLE_SECONDS: u64 = 3;

fn ticks(unix_secs: i64) -> FileTime {
    FileTime::new(nt_ticks(unix_secs))
}

fn windows_path(root: &Path, rel: &str) -> PathBuf {
    let mut path = root.to_path_buf();
    for part in rel.split('/').filter(|p| !p.is_empty()) {
        path.push(part);
    }
    path
}

fn unix_secs(time: std::io::Result<std::time::SystemTime>) -> i64 {
    time.ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// What Windows knows about a file in the folder.
enum State {
    /// A file that is not a placeholder: new here, or replaced by a program that saves that way.
    Plain,
    /// A placeholder in step with the NAS.
    InSync,
    /// A placeholder somebody changed here.
    Changed,
}

fn state_of(path: &Path) -> Result<State> {
    let placeholder = Placeholder::open(path).map_err(|e| anyhow!("{e}"))?;
    match placeholder.info().map_err(|e| anyhow!("{e}"))? {
        None => Ok(State::Plain),
        Some(info) if info.is_in_sync() => Ok(State::InSync),
        Some(_) => Ok(State::Changed),
    }
}

/// A file or folder of this folder becomes a placeholder in step with the NAS.
fn convert(path: &Path, rel: &str) -> Result<()> {
    let mut placeholder = Placeholder::open(path).map_err(|e| anyhow!("{e}"))?;
    placeholder
        .convert_to_placeholder(ConvertOptions::default().mark_in_sync().blob(rel.as_bytes().to_vec()), None)
        .map_err(|e| anyhow!("{e}"))?;
    Ok(())
}

fn create_placeholder(root: &Path, rel: &str, item: &Item) -> Result<()> {
    let (parent, name) = rel.rsplit_once('/').unwrap_or(("", rel));
    let at = ticks(item.mtime);
    let metadata = if item.dir { Metadata::directory() } else { Metadata::file().size(item.size) }.created(at).written(at).changed(at).accessed(at);
    let mut placeholder = PlaceholderFile::new(name).mark_in_sync().metadata(metadata).blob(rel.as_bytes().to_vec());
    if !item.dir {
        placeholder = placeholder.has_no_children();
    }
    placeholder.create::<&Path>(windows_path(root, parent)).map_err(|e| anyhow!("{e}"))?;
    Ok(())
}

// ── What Windows asks of us ───────────────────────────────────────────────────

struct Filtered<R: Remote + Send + Sync + 'static> {
    vol: Arc<Volume<R>>,
    root: PathBuf,
    rt: Handle,
    /// Places this program is changing itself: Windows' questions about them are just answered yes.
    quiet: Arc<Mutex<HashSet<String>>>,
}

impl<R: Remote + Send + Sync + 'static> Filtered<R> {
    fn is_quiet(&self, rel: &str) -> bool {
        self.quiet.lock().unwrap().contains(rel)
    }
}

impl<R: Remote + Send + Sync + 'static> SyncFilter for Filtered<R> {
    /// A file is opened: its data (or the part asked for) comes from the NAS.
    fn fetch_data(&self, request: Request, ticket: ticket::FetchData, info: info::FetchData) -> CResult<()> {
        let rel = String::from_utf8_lossy(request.file_blob()).to_string();
        let size = request.file_size();
        let range = widen(info.required_file_range(), size);
        let mut at = range.start;
        while at < range.end {
            let want = (range.end - at).min(CHUNK) as usize;
            let bytes = self.rt.block_on(self.vol.read_piece(&rel, at, want)).map_err(|_| CloudErrorKind::NetworkUnavailable)?;
            if bytes.is_empty() {
                return Err(CloudErrorKind::NetworkUnavailable);
            }
            ticket.write_at(&bytes, at).map_err(|_| CloudErrorKind::Unsuccessful)?;
            at += bytes.len() as u64;
            let _ = ticket.report_progress(size, at.min(size));
        }
        Ok(())
    }

    /// The person frees up space: fine, the NAS still has it.
    fn dehydrate(&self, _request: Request, ticket: ticket::Dehydrate, _info: info::Dehydrate) -> CResult<()> {
        ticket.pass().map_err(|_| CloudErrorKind::Unsuccessful)
    }

    /// A file or folder is deleted here: deleted on the NAS too (into its trash), first.
    fn delete(&self, request: Request, ticket: ticket::Delete, _info: info::Delete) -> CResult<()> {
        if let Some(rel) = rel_path(&self.root, &request.path()) {
            if !rel.is_empty() && !self.is_quiet(&rel) && self.vol.stat(&rel).is_some() {
                self.rt.block_on(self.vol.remove(&rel)).map_err(|_| CloudErrorKind::NetworkUnavailable)?;
            }
        }
        ticket.pass().map_err(|_| CloudErrorKind::Unsuccessful)
    }

    /// A file or folder is renamed or moved here: the same on the NAS, first.
    fn rename(&self, request: Request, ticket: ticket::Rename, info: info::Rename) -> CResult<()> {
        let from = rel_path(&self.root, &request.path());
        if let Some(from) = from.filter(|f| !f.is_empty() && !self.is_quiet(f)) {
            if self.vol.stat(&from).is_some() {
                let to = if info.target_in_scope() { rel_path(&self.root, &info.target_path()) } else { None };
                let done = match to {
                    Some(to) if !self.is_quiet(&to) => self.rt.block_on(self.vol.rename(&from, &to)),
                    Some(_) => Ok(()),
                    None => self.rt.block_on(self.vol.remove(&from)), // moved out of the folder: gone from the NAS
                };
                done.map_err(|_| CloudErrorKind::NetworkUnavailable)?;
            }
        }
        ticket.pass().map_err(|_| CloudErrorKind::Unsuccessful)
    }
}

// ── The mounted folder ────────────────────────────────────────────────────────

pub struct Mount<R: Remote + Send + Sync + 'static> {
    _connection: Connection<Filtered<R>>,
    vol: Arc<Volume<R>>,
    root: PathBuf,
    exclude: Filter,
    quiet: Arc<Mutex<HashSet<String>>>,
    computer: String,
}

// The connection only holds Windows handles; the callbacks run on its own threads already.
unsafe impl<R: Remote + Send + Sync + 'static> Send for Mount<R> {}
unsafe impl<R: Remote + Send + Sync + 'static> Sync for Mount<R> {}

fn sync_root_id(account: &str) -> Result<SyncRootId> {
    Ok(SyncRootIdBuilder::new(PROVIDER)
        .user_security_id(SecurityId::current_user().map_err(|e| anyhow!("{e}"))?)
        .account_name(account)
        .build())
}

/// Registers the folder with Windows (once) and connects: from now on Windows asks this
/// program when a placeholder is opened, deleted or renamed. The folder must be empty the
/// first time.
pub fn mount<R: Remote + Send + Sync + 'static>(
    vol: Arc<Volume<R>>, rt: Handle, root: &Path, name: &str, account: &str, exclude: &[String], computer: &str,
) -> Result<Mount<R>> {
    std::fs::create_dir_all(root).with_context(|| format!("cannot make {}", root.display()))?;
    let id = sync_root_id(account)?;
    if !id.is_registered().map_err(|e| anyhow!("{e}"))? {
        if std::fs::read_dir(root)?.next().is_some() {
            bail!("{} is not empty: files on demand need an empty folder (move what is in it away first).", root.display());
        }
        let exe = std::env::current_exe().map(|p| format!("{},0", p.display())).unwrap_or_default();
        let info = SyncRootInfo::default()
            .with_display_name(format!("AlvaOS – {name}"))
            .with_hydration_type(HydrationType::Progressive)
            .with_population_type(PopulationType::AlwaysFull)
            .with_icon(exe)
            .with_version(crate::VERSION)
            .with_allow_pinning(true)
            .with_show_siblings_as_group(false)
            .with_path(root)
            .map_err(|e| anyhow!("{e}"))?;
        id.register(info).map_err(|e| anyhow!("Windows would not take the folder as a cloud folder: {e}"))?;
    }
    let quiet = Arc::new(Mutex::new(HashSet::new()));
    let filter = Filtered { vol: vol.clone(), root: root.to_path_buf(), rt, quiet: quiet.clone() };
    let connection = Session::new().connect(root, filter).map_err(|e| anyhow!("The folder could not be connected: {e}"))?;
    Ok(Mount { _connection: connection, vol, root: root.to_path_buf(), exclude: Filter::new(exclude), quiet, computer: computer.to_string() })
}

/// The folder is no cloud folder any more (what was downloaded stays; what was not, is gone).
pub fn unregister(account: &str) {
    if let Ok(id) = sync_root_id(account) {
        if id.is_registered().unwrap_or(false) {
            let _ = id.unregister();
        }
    }
}

impl<R: Remote + Send + Sync + 'static> Mount<R> {
    fn quietly<T>(&self, places: &[&str], work: impl FnOnce() -> T) -> T {
        {
            let mut quiet = self.quiet.lock().unwrap();
            for p in places {
                quiet.insert((*p).to_string());
            }
        }
        let result = work();
        let mut quiet = self.quiet.lock().unwrap();
        for p in places {
            quiet.remove(*p);
        }
        result
    }

    /// One round: what is new or changed here goes to the NAS, then the NAS's tree is looked at
    /// again and brought into the folder. Returns how many files went up.
    pub async fn round(&self) -> Result<usize> {
        let sent = self.upload_local().await;
        self.vol.refresh().await?;
        self.reconcile();
        Ok(sent)
    }

    /// New folders and new or changed files of this folder, sent.
    async fn upload_local(&self) -> usize {
        if self.vol.read_only() {
            return 0;
        }
        let (mut dirs, mut files) = (Vec::new(), Vec::new());
        let mut stack = vec![self.root.clone()];
        while let Some(dir) = stack.pop() {
            let Ok(entries) = std::fs::read_dir(&dir) else { continue };
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                let path = entry.path();
                let Some(rel) = rel_path(&self.root, &path) else { continue };
                if sync::ignored(&name) || self.exclude.matches(&rel) {
                    continue;
                }
                let Ok(meta) = std::fs::symlink_metadata(&path) else { continue };
                if meta.is_dir() {
                    stack.push(path.clone());
                    dirs.push((rel, path));
                } else if meta.is_file() {
                    files.push((rel, path, meta));
                }
            }
        }
        dirs.sort_by_key(|(rel, _)| rel.matches('/').count());
        for (rel, path) in dirs {
            if self.vol.stat(&rel).is_none() && self.vol.mkdir(&rel).await.is_ok() {
                let _ = self.quietly(&[&rel], || convert(&path, &rel));
            }
        }
        let mut sent = 0;
        for (rel, path, meta) in files {
            let age = meta.modified().ok().and_then(|m| m.elapsed().ok()).map(|e| e.as_secs()).unwrap_or(SETTLE_SECONDS);
            if age < SETTLE_SECONDS {
                continue; // maybe still being written: next round
            }
            let Ok(state) = state_of(&path) else { continue }; // in use: next round
            if matches!(state, State::InSync) {
                continue;
            }
            let mtime = unix_secs(meta.modified());
            if self.vol.remote().upload(&rel, &path, mtime).await.is_err() {
                continue;
            }
            let done = self.quietly(&[&rel], || match state {
                State::Plain => convert(&path, &rel),
                _ => Placeholder::open(&path)
                    .and_then(|mut p| p.mark_in_sync(true, None).map(|_| ()))
                    .map_err(|e| anyhow!("{e}")),
            });
            if done.is_ok() {
                self.vol.note(&rel, Item::file(meta.len(), mtime));
                sent += 1;
            }
        }
        sent
    }

    /// The NAS's tree, brought into the folder: new things as placeholders, changed files
    /// updated, and what is gone on the NAS gone here when nobody changed it here.
    fn reconcile(&self) {
        let items = self.vol.items();
        for (rel, item) in &items {
            if self.exclude.matches(rel) || !rel.split('/').all(sync::fits_here) {
                continue;
            }
            let path = windows_path(&self.root, rel);
            let Ok(local) = std::fs::symlink_metadata(&path) else {
                let _ = self.quietly(&[rel], || create_placeholder(&self.root, rel, item));
                continue;
            };
            if item.dir || !local.is_file() {
                continue;
            }
            let same = local.len() == item.size && (unix_secs(local.modified()) - item.mtime).abs() <= 2;
            if same {
                continue;
            }
            match state_of(&path) {
                Ok(State::InSync) => {
                    let _ = self.quietly(&[rel], || self.update_placeholder(&path, rel, item));
                }
                Ok(State::Changed) => {
                    // Changed here and on the NAS: both are kept.
                    let aside = sync::conflict_name(rel, &self.computer, chrono::Local::now());
                    let moved = self.quietly(&[rel, &aside], || std::fs::rename(&path, windows_path(&self.root, &aside)));
                    if moved.is_ok() {
                        let _ = self.quietly(&[rel], || create_placeholder(&self.root, rel, item));
                    }
                }
                _ => {} // a plain file: the next round sends it
            }
        }
        // Gone on the NAS: gone here, if nobody changed it here (children before their folders).
        let mut local = Vec::new();
        let mut stack = vec![self.root.clone()];
        while let Some(dir) = stack.pop() {
            let Ok(entries) = std::fs::read_dir(&dir) else { continue };
            for entry in entries.flatten() {
                let path = entry.path();
                let Some(rel) = rel_path(&self.root, &path) else { continue };
                if sync::ignored(&entry.file_name().to_string_lossy()) || self.exclude.matches(&rel) {
                    continue;
                }
                if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
                    stack.push(path.clone());
                }
                local.push((rel, path));
            }
        }
        local.sort_by(|a, b| b.0.cmp(&a.0));
        for (rel, path) in local {
            if self.vol.stat(&rel).is_some() {
                continue;
            }
            if matches!(state_of(&path), Ok(State::InSync)) {
                let _ = self.quietly(&[&rel], || {
                    if path.is_dir() { std::fs::remove_dir(&path) } else { std::fs::remove_file(&path) }
                });
            }
        }
    }

    fn update_placeholder(&self, path: &Path, rel: &str, item: &Item) -> Result<()> {
        let at = ticks(item.mtime);
        let mut placeholder = Placeholder::options().write_access().open(path).map_err(|e| anyhow!("{e}"))?;
        placeholder
            .update(
                UpdateOptions::default()
                    .metadata(Metadata::file().size(item.size).written(at).changed(at))
                    .dehydrate()
                    .update_if_in_sync()
                    .mark_in_sync()
                    .blob(rel.as_bytes()),
                None,
            )
            .map_err(|e| anyhow!("{e}"))?;
        Ok(())
    }
}
