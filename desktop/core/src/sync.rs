//! The sync folder: a folder of the NAS kept on this computer, both ways, also offline.
//!
//! Each round compares three things: the folder on this computer, the folder on the NAS, and
//! how both were at the end of the last round (the state file). Whatever changed on one side
//! only is done on the other; what changed on both is a conflict, and then **both** are kept.
//! Both sides keep the same date for a file (the NAS takes it with X-OC-Mtime, the computer
//! sets it after a download), so size and date tell whether a file changed.
//!
//! Nothing is ever lost on the way:
//! - deleted on this computer → on the NAS it goes to the share's trash (WebDAV always does);
//! - deleted on the NAS → here it goes to the system's trash (Recycle Bin, Trash);
//! - a file replaced by a download → the NAS still has it (versions, and it was synced there);
//! - changed on both sides → both kept, this computer's as "name (conflict …).ext";
//! - many deletions at once (an emptied folder, a disk not plugged in) → the round stops and
//!   asks first; a sync folder that is not there at all → nothing happens;
//! - an incomplete listing from the NAS → nothing missing in it counts as deleted.

use std::collections::{BTreeMap, BTreeSet};
use std::future::Future;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use serde::{Deserialize, Serialize};

/// A file or folder, as both sides know it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Item {
    pub dir: bool,
    pub size: u64,
    /// Seconds since 1970.
    pub mtime: i64,
}

impl Item {
    pub fn file(size: u64, mtime: i64) -> Self {
        Self { dir: false, size, mtime }
    }
    pub fn folder() -> Self {
        Self { dir: true, size: 0, mtime: 0 }
    }
}

/// Relative path (`a/b.txt`, always with `/`) → item.
pub type Tree = BTreeMap<String, Item>;

/// The folder on the NAS (one share, one folder in it).
pub trait Remote: Sync {
    /// Everything below the folder, and whether the listing is complete.
    fn tree(&self) -> impl Future<Output = Result<(Tree, bool)>> + Send;
    fn download(&self, path: &str, to: &Path) -> impl Future<Output = Result<()>> + Send;
    fn upload(&self, path: &str, from: &Path, mtime: i64) -> impl Future<Output = Result<()>> + Send;
    fn mkdir(&self, path: &str) -> impl Future<Output = Result<()>> + Send;
    /// To the share's trash on the NAS.
    fn delete(&self, path: &str) -> impl Future<Output = Result<()>> + Send;
    /// A share the person may only read: nothing goes up, nothing is deleted there.
    fn read_only(&self) -> bool;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    MkdirRemote(String),
    MkdirLocal(String),
    Upload(String),
    Download(String),
    /// Changed on both sides: this computer's goes aside as a conflict copy (and up), the NAS's comes down.
    Conflict(String),
    DeleteRemote(String),
    TrashLocal(String),
    /// The same on both sides now: only noted.
    Adopt(String),
    /// Gone on both sides: only forgotten.
    Forget(String),
}

/// What one round did.
#[derive(Debug, Clone, Default, Serialize)]
pub struct Report {
    pub uploaded: usize,
    pub downloaded: usize,
    pub deleted_remote: usize,
    pub trashed_local: usize,
    pub conflicts: usize,
    /// The conflict copies made here this round (names of the files kept aside).
    pub conflict_files: Vec<String>,
    /// What was done, file by file (the newest 200), for the activity list.
    pub log: Vec<String>,
    pub problems: Vec<String>,
}

const LOG_LINES: usize = 200;

impl Report {
    fn note(&mut self, line: String) {
        if self.log.len() < LOG_LINES {
            self.log.push(line);
        }
    }

    pub fn changed(&self) -> usize {
        self.uploaded + self.downloaded + self.deleted_remote + self.trashed_local + self.conflicts
    }
}

/// Stopped before deleting many files at once: the person decides (Options::allow_many_deletions).
#[derive(Debug)]
pub struct ManyDeletions {
    pub count: usize,
}

impl std::fmt::Display for ManyDeletions {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} files would be deleted at once. The sync waits until you say it is right.", self.count)
    }
}

impl std::error::Error for ManyDeletions {}

#[derive(Clone, Default)]
pub struct Options {
    /// This computer's name, for conflict copies.
    pub computer: String,
    /// Go ahead with many deletions this one time.
    pub allow_many_deletions: bool,
    /// Where files deleted on the NAS go here; None: the system's trash.
    pub trash: Option<PathBuf>,
    /// What is left out ("Don't sync"), see [`Filter`].
    pub exclude: Vec<String>,
    /// Told before each file: how far the round is.
    pub progress: Option<ProgressFn>,
}

/// How far a round is: file `done` of `total`, and what it is doing now.
#[derive(Debug, Clone, Default, Serialize)]
pub struct Progress {
    pub done: usize,
    pub total: usize,
    pub current: String,
}

pub type ProgressFn = Arc<dyn Fn(Progress) + Send + Sync>;

/// What is left out of syncing, one pattern per line (blank lines and `#` lines are skipped):
/// - `*.mov`, `node_modules`: a name (with `*` and `?`), in any folder, ignoring case;
/// - `Videos/`, `Photos/Raw`, `/Backup`: a path from the top of the sync folder (everything in it too).
///
/// Left-out files are neither sent nor fetched, nor deleted on either side; if they were synced
/// before, they are simply forgotten (they stay where they are).
#[derive(Debug, Clone, Default)]
pub struct Filter {
    names: Vec<Vec<char>>,
    paths: Vec<Vec<char>>,
}

impl Filter {
    pub fn new(lines: &[String]) -> Self {
        let mut filter = Filter::default();
        for line in lines {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let core = line.trim_matches('/').to_lowercase();
            if core.is_empty() {
                continue;
            }
            let pattern: Vec<char> = core.chars().collect();
            if line.contains('/') {
                filter.paths.push(pattern);
            } else {
                filter.names.push(pattern);
            }
        }
        filter
    }

    pub fn is_empty(&self) -> bool {
        self.names.is_empty() && self.paths.is_empty()
    }

    pub fn matches(&self, path: &str) -> bool {
        if self.is_empty() {
            return false;
        }
        let lower = path.to_lowercase();
        let mut prefix = String::new();
        for part in lower.split('/') {
            if !prefix.is_empty() {
                prefix.push('/');
            }
            prefix.push_str(part);
            let part_chars: Vec<char> = part.chars().collect();
            if self.names.iter().any(|n| glob(n, &part_chars)) {
                return true;
            }
            let prefix_chars: Vec<char> = prefix.chars().collect();
            if self.paths.iter().any(|p| glob(p, &prefix_chars)) {
                return true;
            }
        }
        false
    }
}

/// `*` (not over a `/`) and `?`, nothing else.
fn glob(pattern: &[char], text: &[char]) -> bool {
    match pattern.split_first() {
        None => text.is_empty(),
        Some(('*', rest)) => (0..=text.len()).take_while(|&n| n == 0 || text[n - 1] != '/').any(|n| glob(rest, &text[n..])),
        Some(('?', rest)) => text.first().is_some_and(|c| *c != '/') && glob(rest, &text[1..]),
        Some((c, rest)) => text.first() == Some(c) && glob(rest, &text[1..]),
    }
}

const MANY_DELETIONS: usize = 20;
const DATE_SLACK: i64 = 2; // FAT and some network disks keep dates in steps of two seconds

/// Names that are never synced: the app's own, half-done downloads and uploads, the systems' junk.
pub fn ignored(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    name.starts_with(".alvaos-sync")
        || name.ends_with(".alvaos-upload")
        || name.starts_with("~$")
        || name.starts_with(".~lock.")
        || lower.ends_with(".tmp")
        || matches!(lower.as_str(), ".ds_store" | "thumbs.db" | "desktop.ini" | "$recycle.bin" | "system volume information" | ".alvaos-trash")
        || lower.starts_with(".trash-")
}

fn ignored_path(path: &str) -> bool {
    path.split('/').any(ignored)
}

/// Can this computer have such a name? (Windows forbids some characters and names.)
pub fn fits_here(name: &str) -> bool {
    if !cfg!(windows) {
        return true;
    }
    let reserved = ["CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"];
    let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
    !name.chars().any(|c| "<>:\"\\|?*".contains(c) || (c as u32) < 32)
        && !name.ends_with('.')
        && !name.ends_with(' ')
        && !reserved.contains(&stem.as_str())
}

fn same(a: &Item, b: &Item) -> bool {
    a.dir == b.dir && (a.dir || (a.size == b.size && (a.mtime - b.mtime).abs() <= DATE_SLACK))
}

fn changed(now: Option<&Item>, before: Option<&Item>) -> bool {
    match (now, before) {
        (None, None) => false,
        (Some(a), Some(b)) => !same(a, b),
        _ => true,
    }
}

fn under(path: &str, parent: &str) -> bool {
    path.len() > parent.len() && path.starts_with(parent) && path.as_bytes()[parent.len()] == b'/'
}

/// What to do, from this computer's folder, the NAS's and how both were last time.
pub fn plan(local: &Tree, remote: &Tree, base: &Tree, remote_complete: bool, read_only: bool) -> Vec<Action> {
    let paths: BTreeSet<&String> = local.keys().chain(remote.keys()).chain(base.keys()).collect();
    let mut actions = Vec::new();
    for path in paths {
        let (l, b) = (local.get(path), base.get(path));
        let mut r = remote.get(path);
        // Not in an incomplete listing: unknown, not deleted.
        if r.is_none() && !remote_complete {
            r = b;
        }
        let (lc, rc) = (changed(l, b), changed(r, b));
        let action = match (lc, rc) {
            (false, false) => None,
            (true, false) => match (l, r) {
                (Some(_), _) if read_only => None, // a share to read only: what is changed here stays here
                (Some(li), Some(ri)) if same(li, ri) => Some(Action::Adopt(path.clone())),
                (Some(li), _) if li.dir => Some(Action::MkdirRemote(path.clone())),
                (Some(_), _) => Some(Action::Upload(path.clone())),
                (None, Some(ri)) if read_only => Some(if ri.dir { Action::MkdirLocal(path.clone()) } else { Action::Download(path.clone()) }),
                (None, Some(_)) => Some(Action::DeleteRemote(path.clone())),
                (None, None) => Some(Action::Forget(path.clone())),
            },
            (false, true) => match (l, r) {
                (Some(li), Some(ri)) if same(li, ri) => Some(Action::Adopt(path.clone())),
                (_, Some(ri)) if ri.dir => Some(Action::MkdirLocal(path.clone())),
                (_, Some(_)) => Some(Action::Download(path.clone())),
                (Some(_), None) => Some(Action::TrashLocal(path.clone())),
                (None, None) => Some(Action::Forget(path.clone())),
            },
            (true, true) => match (l, r) {
                (None, None) => Some(Action::Forget(path.clone())),
                (Some(li), Some(ri)) if (li.dir && ri.dir) || same(li, ri) => Some(Action::Adopt(path.clone())),
                (Some(_), Some(_)) => Some(Action::Conflict(path.clone())),
                // Deleted on the NAS, changed here: this computer's is kept, and goes up again.
                (Some(_), None) if read_only => Some(Action::Forget(path.clone())),
                (Some(li), None) => Some(if li.dir { Action::MkdirRemote(path.clone()) } else { Action::Upload(path.clone()) }),
                // Deleted here, changed on the NAS: the NAS's comes back.
                (None, Some(ri)) => Some(if ri.dir { Action::MkdirLocal(path.clone()) } else { Action::Download(path.clone()) }),
            },
        };
        actions.extend(action);
    }
    collapse_deletions(actions, local, remote, base)
}

/// A deleted folder is deleted once, as a whole (into the trash), not file by file; and a folder
/// is only deleted when nothing in it changed on the other side (else that is kept).
fn collapse_deletions(actions: Vec<Action>, local: &Tree, remote: &Tree, base: &Tree) -> Vec<Action> {
    let mut out = Vec::new();
    let mut gone_remote: Vec<String> = Vec::new();
    let mut gone_local: Vec<String> = Vec::new();
    let keeps = |tree: &Tree, folder: &str| tree.iter().any(|(p, item)| under(p, folder) && changed(Some(item), base.get(p)));
    for action in actions {
        match action {
            Action::DeleteRemote(p) => {
                if gone_remote.iter().any(|g| under(&p, g)) {
                    out.push(Action::Forget(p));
                } else if remote.get(&p).map(|i| i.dir).unwrap_or(false) && keeps(remote, &p) {
                    out.push(Action::MkdirLocal(p)); // something new in it on the NAS: the folder comes back here
                } else {
                    gone_remote.push(p.clone());
                    out.push(Action::DeleteRemote(p));
                }
            }
            Action::TrashLocal(p) => {
                if gone_local.iter().any(|g| under(&p, g)) {
                    out.push(Action::Forget(p));
                } else if local.get(&p).map(|i| i.dir).unwrap_or(false) && keeps(local, &p) {
                    out.push(Action::MkdirRemote(p)); // something new in it here: the folder goes back up
                } else {
                    gone_local.push(p.clone());
                    out.push(Action::TrashLocal(p));
                }
            }
            other => out.push(other),
        }
    }
    out
}

/// How many files a plan would delete (on either side).
pub fn deletions(actions: &[Action], local: &Tree, remote: &Tree) -> usize {
    actions
        .iter()
        .map(|a| match a {
            Action::DeleteRemote(p) => count_files(remote, p),
            Action::TrashLocal(p) => count_files(local, p),
            _ => 0,
        })
        .sum()
}

fn count_files(tree: &Tree, path: &str) -> usize {
    let own = usize::from(tree.get(path).map(|i| !i.dir).unwrap_or(false));
    own + tree.iter().filter(|(p, i)| !i.dir && under(p, path)).count()
}

// ── This computer's folder ────────────────────────────────────────────────────

/// Everything below `root` (no symlinks, nothing ignored).
pub fn scan(root: &Path) -> Result<Tree> {
    let mut tree = Tree::new();
    fn walk(dir: &Path, rel: &str, tree: &mut Tree) -> Result<()> {
        for entry in std::fs::read_dir(dir).with_context(|| format!("cannot read {}", dir.display()))? {
            let Ok(entry) = entry else { continue };
            let name = entry.file_name().to_string_lossy().to_string();
            if ignored(&name) {
                continue;
            }
            let Ok(meta) = std::fs::symlink_metadata(entry.path()) else { continue };
            let path = if rel.is_empty() { name.clone() } else { format!("{rel}/{name}") };
            if meta.is_dir() {
                tree.insert(path.clone(), Item::folder());
                walk(&entry.path(), &path, tree)?;
            } else if meta.is_file() {
                tree.insert(path, Item::file(meta.len(), mtime_of(&meta)));
            }
        }
        Ok(())
    }
    walk(root, "", &mut tree)?;
    Ok(tree)
}

fn mtime_of(meta: &std::fs::Metadata) -> i64 {
    filetime::FileTime::from_last_modification_time(meta).unix_seconds()
}

fn local_path(root: &Path, path: &str) -> PathBuf {
    path.split('/').fold(root.to_path_buf(), |p, part| p.join(part))
}

fn stat_local(root: &Path, path: &str) -> Option<Item> {
    let meta = std::fs::symlink_metadata(local_path(root, path)).ok()?;
    Some(if meta.is_dir() { Item::folder() } else { Item::file(meta.len(), mtime_of(&meta)) })
}

/// `report (conflict anna-laptop 2026-10-26 14-05).pdf`
pub fn conflict_name(path: &str, computer: &str, now: chrono::DateTime<chrono::Local>) -> String {
    let (dir, name) = path.rsplit_once('/').map(|(d, n)| (format!("{d}/"), n)).unwrap_or((String::new(), path));
    let (stem, ext) = match name.rfind('.') {
        Some(i) if i > 0 => (&name[..i], &name[i..]),
        _ => (name, ""),
    };
    let who: String = computer.chars().filter(|c| !"/\\:*?\"<>|".contains(*c)).take(30).collect();
    format!("{dir}{stem} (conflict {who} {}){ext}", now.format("%Y-%m-%d %H-%M"))
}

fn trash_local(root: &Path, path: &str, options: &Options) -> Result<()> {
    let target = local_path(root, path);
    if let Some(dir) = &options.trash {
        let to = local_path(dir, path);
        std::fs::create_dir_all(to.parent().unwrap_or(dir))?;
        std::fs::rename(&target, &to)?;
        return Ok(());
    }
    trash::delete(&target).map_err(|e| anyhow!("could not move {} to the trash: {e}", target.display()))
}

// ── A round ───────────────────────────────────────────────────────────────────

/// The state of the last round (what both sides agreed on).
pub fn load_state(file: &Path) -> Tree {
    std::fs::read_to_string(file).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default()
}

fn save_state(file: &Path, base: &Tree) -> Result<()> {
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = file.with_extension("tmp");
    std::fs::write(&tmp, serde_json::to_vec(base)?)?;
    std::fs::rename(tmp, file)?;
    Ok(())
}

/// One round: compare, then do what is needed, noting each step in the state file.
pub async fn sync_once<R: Remote>(root: &Path, remote: &R, state_file: &Path, options: &Options) -> Result<Report> {
    let mut base = load_state(state_file);
    if !root.is_dir() {
        if !base.is_empty() {
            bail!("The sync folder {} is not there (a disk not plugged in?). Nothing was changed.", root.display());
        }
        std::fs::create_dir_all(root).with_context(|| format!("cannot make {}", root.display()))?;
    }
    let filter = Filter::new(&options.exclude);
    let mut local = scan(root)?;
    local.retain(|p, _| !filter.matches(p));
    base.retain(|p, _| !filter.matches(p));
    let (remote_tree, complete) = remote.tree().await?;
    let remote_tree: Tree = remote_tree.into_iter().filter(|(p, _)| !ignored_path(p) && !filter.matches(p)).collect();
    let actions = plan(&local, &remote_tree, &base, complete, remote.read_only());
    let count = deletions(&actions, &local, &remote_tree);
    if count >= MANY_DELETIONS && count * 2 > base.values().filter(|i| !i.dir).count() && !options.allow_many_deletions {
        return Err(ManyDeletions { count }.into());
    }
    let mut report = Report::default();
    // Folders first (parents before children), then files, deletions last (whole folders).
    let rank = |a: &Action| match a {
        Action::MkdirRemote(_) | Action::MkdirLocal(_) => 0,
        Action::Conflict(_) | Action::Upload(_) | Action::Download(_) => 1,
        Action::DeleteRemote(_) | Action::TrashLocal(_) => 2,
        Action::Adopt(_) | Action::Forget(_) => 3,
    };
    let mut ordered = actions;
    ordered.sort_by_key(rank);
    let total = ordered.iter().filter(|a| !matches!(a, Action::Adopt(_) | Action::Forget(_))).count();
    let mut done = 0;
    for action in ordered {
        if !matches!(action, Action::Adopt(_) | Action::Forget(_)) {
            if let Some(tell) = &options.progress {
                tell(Progress { done, total, current: describe(&action) });
            }
            done += 1;
        }
        let result = run(&action, root, remote, &local, &remote_tree, &mut base, options, &mut report).await;
        if let Err(e) = result {
            report.problems.push(format!("{}: {e:#}", describe(&action)));
        }
    }
    save_state(state_file, &base)?;
    Ok(report)
}

fn describe(action: &Action) -> String {
    match action {
        Action::MkdirRemote(p) | Action::Upload(p) => format!("{p} (to the NAS)"),
        Action::MkdirLocal(p) | Action::Download(p) => format!("{p} (from the NAS)"),
        Action::Conflict(p) => format!("{p} (changed on both)"),
        Action::DeleteRemote(p) => format!("{p} (deleting on the NAS)"),
        Action::TrashLocal(p) => format!("{p} (to the trash here)"),
        Action::Adopt(p) | Action::Forget(p) => p.clone(),
    }
}

fn forget_below(base: &mut Tree, path: &str) {
    base.remove(path);
    base.retain(|p, _| !under(p, path));
}

#[allow(clippy::too_many_arguments)]
async fn run<R: Remote>(action: &Action, root: &Path, remote: &R, local: &Tree, remote_tree: &Tree, base: &mut Tree,
                        options: &Options, report: &mut Report) -> Result<()> {
    match action {
        Action::MkdirRemote(p) => {
            remote.mkdir(p).await?;
            base.insert(p.clone(), Item::folder());
        }
        Action::MkdirLocal(p) => {
            if !p.split('/').all(fits_here) {
                bail!("this name is not possible on this computer");
            }
            std::fs::create_dir_all(local_path(root, p))?;
            base.insert(p.clone(), Item::folder());
        }
        Action::Upload(p) => {
            let seen = local.get(p).cloned().ok_or_else(|| anyhow!("gone"))?;
            // Still as it was a moment ago? (Else it is being written: next round.)
            if stat_local(root, p).as_ref() != Some(&seen) {
                return Ok(());
            }
            remote.upload(p, &local_path(root, p), seen.mtime).await?;
            base.insert(p.clone(), seen);
            report.uploaded += 1;
            report.note(format!("Sent {p}"));
        }
        Action::Download(p) => {
            let item = remote_tree.get(p).cloned().ok_or_else(|| anyhow!("gone"))?;
            download_into(root, remote, p, &item).await?;
            base.insert(p.clone(), item);
            report.downloaded += 1;
            report.note(format!("Received {p}"));
        }
        Action::Conflict(p) => {
            let mine = local.get(p).cloned().ok_or_else(|| anyhow!("gone"))?;
            let theirs = remote_tree.get(p).cloned().ok_or_else(|| anyhow!("gone"))?;
            let aside = conflict_name(p, &options.computer, chrono::Local::now());
            std::fs::rename(local_path(root, p), local_path(root, &aside))?;
            if theirs.dir {
                std::fs::create_dir_all(local_path(root, p))?;
            } else {
                download_into(root, remote, p, &theirs).await?;
            }
            base.insert(p.clone(), theirs);
            if !mine.dir && !remote.read_only() {
                remote.upload(&aside, &local_path(root, &aside), mine.mtime).await?;
                base.insert(aside.clone(), mine);
            }
            report.conflicts += 1;
            report.note(format!("Changed on both sides, both kept: {p}"));
            report.conflict_files.push(aside);
        }
        Action::DeleteRemote(p) => {
            remote.delete(p).await?;
            forget_below(base, p);
            report.deleted_remote += 1;
            report.note(format!("Deleted on the NAS (into its trash): {p}"));
        }
        Action::TrashLocal(p) => {
            trash_local(root, p, options)?;
            forget_below(base, p);
            report.trashed_local += 1;
            report.note(format!("Moved to the trash here: {p}"));
        }
        Action::Adopt(p) => {
            if let Some(item) = remote_tree.get(p).or_else(|| local.get(p)) {
                base.insert(p.clone(), item.clone());
            }
        }
        Action::Forget(p) => {
            base.remove(p);
        }
    }
    Ok(())
}

/// Into a hidden file next to it first, then in place, with the NAS's date.
async fn download_into<R: Remote>(root: &Path, remote: &R, path: &str, item: &Item) -> Result<()> {
    if !path.split('/').all(fits_here) {
        bail!("this name is not possible on this computer");
    }
    let target = local_path(root, path);
    let dir = target.parent().ok_or_else(|| anyhow!("no folder"))?.to_path_buf();
    std::fs::create_dir_all(&dir)?;
    let part = dir.join(format!(".alvaos-sync-{}.part", crate::link::hex(&crate::link::new_key()[..6])));
    let result = remote.download(path, &part).await;
    if let Err(e) = result {
        let _ = std::fs::remove_file(&part);
        return Err(e);
    }
    filetime::set_file_mtime(&part, filetime::FileTime::from_unix_time(item.mtime, 0))?;
    if target.is_dir() {
        let _ = std::fs::remove_file(&part);
        bail!("a folder here has this name");
    }
    std::fs::rename(&part, &target)?;
    Ok(())
}

// ── The NAS: the folder tree from the Hub, files over WebDAV ──────────────────

/// One folder of one share, reached through the NAS's addresses on this computer.
pub struct DavRemote {
    pub hub: String,
    pub dav: String,
    pub share: String,
    /// The folder in the share ('' for the whole share).
    pub folder: String,
    pub user: String,
    pub token: String,
    pub read_only: bool,
    http: reqwest::Client,
}

impl DavRemote {
    async fn download_once(&self, path: &str, to: &Path) -> Result<()> {
        use futures_util::StreamExt;
        use tokio::io::AsyncWriteExt;
        let res = checked(self.dav_request("GET", path).send().await?, &[]).await?;
        let mut file = tokio::fs::File::create(to).await?;
        let mut body = res.bytes_stream();
        while let Some(chunk) = body.next().await {
            file.write_all(&chunk?).await?;
        }
        file.sync_all().await?;
        Ok(())
    }

    async fn upload_once(&self, path: &str, from: &Path, mtime: i64) -> Result<()> {
        let file = tokio::fs::File::open(from).await?;
        let size = file.metadata().await?.len();
        let body = reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(file));
        let req = self
            .dav_request("PUT", path)
            .header("X-OC-Mtime", mtime.to_string())
            .header(reqwest::header::CONTENT_LENGTH, size)
            .body(body);
        checked(req.send().await?, &[]).await?;
        Ok(())
    }

    pub fn new(hub: &str, dav: &str, share: &str, folder: &str, user: &str, token: &str, read_only: bool) -> Self {
        let http = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(15))
            .read_timeout(Duration::from_secs(90))
            .user_agent(format!("AlvaOSDesktop/{}", crate::VERSION))
            .build()
            .expect("an HTTP client");
        Self {
            hub: hub.trim_end_matches('/').into(),
            dav: dav.trim_end_matches('/').into(),
            share: share.into(),
            folder: folder.trim_matches('/').into(),
            user: user.into(),
            token: token.into(),
            read_only,
            http,
        }
    }

    fn url(&self, path: &str) -> String {
        const SEGMENT: &percent_encoding::AsciiSet = &percent_encoding::NON_ALPHANUMERIC.remove(b'-').remove(b'_').remove(b'.').remove(b'~');
        let parts = std::iter::once(self.share.as_str())
            .chain(self.folder.split('/').filter(|p| !p.is_empty()))
            .chain(path.split('/').filter(|p| !p.is_empty()));
        let encoded: Vec<String> = parts.map(|p| percent_encoding::utf8_percent_encode(p, SEGMENT).to_string()).collect();
        format!("{}/{}", self.dav, encoded.join("/"))
    }

    fn dav_request(&self, method: &str, path: &str) -> reqwest::RequestBuilder {
        self.http
            .request(reqwest::Method::from_bytes(method.as_bytes()).expect("a method"), self.url(path))
            .basic_auth(&self.user, Some(&self.token))
    }
}

/// An answer that may well work a moment later (the NAS busy or restarting).
#[derive(Debug)]
struct Transient(String);

impl std::fmt::Display for Transient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Transient {}

async fn checked(res: reqwest::Response, ok_also: &[u16]) -> Result<reqwest::Response> {
    let status = res.status();
    if status.is_success() || ok_also.contains(&status.as_u16()) {
        return Ok(res);
    }
    let said = res.text().await.unwrap_or_default();
    let message = format!("the NAS answered {status}{}", if said.trim().is_empty() { String::new() } else { format!(": {}", said.trim()) });
    if status.is_server_error() || matches!(status.as_u16(), 408 | 423 | 429) {
        return Err(Transient(message).into());
    }
    Err(anyhow!(message))
}

fn transient(e: &anyhow::Error) -> bool {
    e.downcast_ref::<Transient>().is_some()
        || e.chain().filter_map(|c| c.downcast_ref::<reqwest::Error>()).any(|r| r.is_connect() || r.is_timeout() || r.is_body() || r.is_request())
}

/// Seconds to wait before the second and the third try of a file.
const RETRY_AFTER: [Duration; 2] = [Duration::from_secs(2), Duration::from_secs(8)];

/// Tries again after a network hiccup or a busy NAS; any other error stops at once.
async fn retrying<T, F, Fut>(delays: &[Duration], mut attempt: F) -> Result<T>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<T>>,
{
    let mut waits = delays.iter();
    loop {
        match attempt().await {
            Ok(value) => return Ok(value),
            Err(e) if transient(&e) => match waits.next() {
                Some(wait) => tokio::time::sleep(*wait).await,
                None => return Err(e),
            },
            Err(e) => return Err(e),
        }
    }
}

impl Remote for DavRemote {
    async fn tree(&self) -> Result<(Tree, bool)> {
        let res = self
            .http
            .get(format!("{}/api/sync/tree", self.hub))
            .query(&[("share", self.share.as_str()), ("path", self.folder.as_str())])
            .header("Cookie", format!("{}={}", crate::hub::COOKIE, self.token))
            .send()
            .await
            .map_err(|e| anyhow!("The NAS cannot be reached ({e})."))?;
        let res = checked(res, &[]).await?;
        let body: serde_json::Value = res.json().await?;
        let mut tree = Tree::new();
        for entry in body["entries"].as_array().cloned().unwrap_or_default() {
            let Some(path) = entry["path"].as_str() else { continue };
            let item = if entry["type"] == "folder" {
                Item::folder()
            } else {
                Item::file(entry["size"].as_u64().unwrap_or(0), entry["mtime"].as_i64().unwrap_or(0))
            };
            tree.insert(path.to_string(), item);
        }
        Ok((tree, body["complete"].as_bool().unwrap_or(false)))
    }

    async fn download(&self, path: &str, to: &Path) -> Result<()> {
        retrying(&RETRY_AFTER, || self.download_once(path, to)).await
    }

    async fn upload(&self, path: &str, from: &Path, mtime: i64) -> Result<()> {
        retrying(&RETRY_AFTER, || self.upload_once(path, from, mtime)).await
    }

    async fn mkdir(&self, path: &str) -> Result<()> {
        retrying(&RETRY_AFTER, || async {
            checked(self.dav_request("MKCOL", path).send().await?, &[405]).await?; // 405: already there
            Ok(())
        })
        .await
    }

    async fn delete(&self, path: &str) -> Result<()> {
        retrying(&RETRY_AFTER, || async {
            checked(self.dav_request("DELETE", path).send().await?, &[404]).await?;
            Ok(())
        })
        .await
    }

    fn read_only(&self) -> bool {
        self.read_only
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// A NAS folder in memory.
    #[derive(Default)]
    struct Mem {
        files: Mutex<BTreeMap<String, (Item, Vec<u8>)>>,
        incomplete: bool,
        read_only: bool,
        deleted: Mutex<Vec<String>>,
    }

    impl Mem {
        fn put(&self, path: &str, body: &str, mtime: i64) {
            let mut files = self.files.lock().unwrap();
            let mut parent = String::new();
            for part in path.split('/').collect::<Vec<_>>().split_last().unwrap().1 {
                parent = if parent.is_empty() { part.to_string() } else { format!("{parent}/{part}") };
                files.insert(parent.clone(), (Item::folder(), Vec::new()));
            }
            files.insert(path.into(), (Item::file(body.len() as u64, mtime), body.as_bytes().to_vec()));
        }
        fn body(&self, path: &str) -> Option<String> {
            self.files.lock().unwrap().get(path).map(|(_, b)| String::from_utf8_lossy(b).to_string())
        }
        fn has(&self, path: &str) -> bool {
            self.files.lock().unwrap().contains_key(path)
        }
    }

    impl Remote for Mem {
        async fn tree(&self) -> Result<(Tree, bool)> {
            Ok((self.files.lock().unwrap().iter().map(|(p, (i, _))| (p.clone(), i.clone())).collect(), !self.incomplete))
        }
        async fn download(&self, path: &str, to: &Path) -> Result<()> {
            let body = self.files.lock().unwrap().get(path).map(|(_, b)| b.clone()).ok_or_else(|| anyhow!("404"))?;
            std::fs::write(to, body)?;
            Ok(())
        }
        async fn upload(&self, path: &str, from: &Path, mtime: i64) -> Result<()> {
            let body = std::fs::read(from)?;
            self.files.lock().unwrap().insert(path.into(), (Item::file(body.len() as u64, mtime), body));
            Ok(())
        }
        async fn mkdir(&self, path: &str) -> Result<()> {
            self.files.lock().unwrap().insert(path.into(), (Item::folder(), Vec::new()));
            Ok(())
        }
        async fn delete(&self, path: &str) -> Result<()> {
            self.deleted.lock().unwrap().push(path.into());
            self.files.lock().unwrap().retain(|p, _| p != path && !under(p, path));
            Ok(())
        }
        fn read_only(&self) -> bool {
            self.read_only
        }
    }

    struct Place {
        root: PathBuf,
        state: PathBuf,
        trash: PathBuf,
        top: PathBuf,
    }

    impl Place {
        fn new() -> Self {
            let top = std::env::temp_dir().join(format!("alvaos-sync-test-{}", crate::link::hex(&crate::link::new_key()[..6])));
            Self { root: top.join("AlvaOS"), state: top.join("state.json"), trash: top.join("trash"), top }
        }
        fn write(&self, path: &str, body: &str, mtime: i64) {
            let p = local_path(&self.root, path);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(&p, body).unwrap();
            filetime::set_file_mtime(&p, filetime::FileTime::from_unix_time(mtime, 0)).unwrap();
        }
        fn read(&self, path: &str) -> Option<String> {
            std::fs::read_to_string(local_path(&self.root, path)).ok()
        }
        fn options(&self, allow: bool) -> Options {
            Options { computer: "laptop".into(), allow_many_deletions: allow, trash: Some(self.trash.clone()), ..Options::default() }
        }
        async fn round(&self, nas: &Mem) -> Result<Report> {
            sync_once(&self.root, nas, &self.state, &self.options(false)).await
        }
    }

    impl Drop for Place {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.top);
        }
    }

    const T: i64 = 1_790_000_000;

    #[tokio::test]
    async fn the_first_round_brings_both_sides_together_and_the_next_does_nothing() {
        let (here, nas) = (Place::new(), Mem::default());
        nas.put("Photos/2024/beach.jpg", "beach", T);
        nas.put("notes.txt", "from the NAS", T);
        here.write("Docs/letter.odt", "dear", T + 5);
        here.write(".DS_Store", "junk", T);
        here.write("~$letter.odt", "lock", T);
        let report = here.round(&nas).await.unwrap();
        assert_eq!((report.uploaded, report.downloaded, report.problems.len()), (1, 2, 0), "{report:?}");
        assert_eq!(here.read("Photos/2024/beach.jpg").as_deref(), Some("beach"));
        assert_eq!(nas.body("Docs/letter.odt").as_deref(), Some("dear"));
        assert!(nas.has("Docs") && !nas.has(".DS_Store") && !nas.has("~$letter.odt"));
        // The download has the NAS's date: the next round sees nothing to do.
        assert_eq!(stat_local(&here.root, "notes.txt").unwrap().mtime, T);
        let again = here.round(&nas).await.unwrap();
        assert_eq!(again.changed(), 0, "{again:?}");
    }

    #[tokio::test]
    async fn a_change_goes_to_the_other_side() {
        let (here, nas) = (Place::new(), Mem::default());
        nas.put("a.txt", "one", T);
        here.write("b.txt", "two", T);
        here.round(&nas).await.unwrap();
        here.write("b.txt", "two, longer", T + 60);
        nas.put("a.txt", "one, changed", T + 60);
        let report = here.round(&nas).await.unwrap();
        assert_eq!((report.uploaded, report.downloaded), (1, 1));
        assert_eq!(nas.body("b.txt").as_deref(), Some("two, longer"));
        assert_eq!(here.read("a.txt").as_deref(), Some("one, changed"));
    }

    #[tokio::test]
    async fn deleted_here_goes_to_the_nas_trash_and_a_folder_goes_as_a_whole() {
        let (here, nas) = (Place::new(), Mem::default());
        nas.put("old.txt", "x", T);
        nas.put("Trip/1.jpg", "1", T);
        nas.put("Trip/2.jpg", "2", T);
        here.round(&nas).await.unwrap();
        std::fs::remove_file(local_path(&here.root, "old.txt")).unwrap();
        std::fs::remove_dir_all(local_path(&here.root, "Trip")).unwrap();
        let report = here.round(&nas).await.unwrap();
        assert_eq!(report.deleted_remote, 2, "{report:?}");
        assert_eq!(*nas.deleted.lock().unwrap(), vec!["Trip".to_string(), "old.txt".to_string()]);
        assert!(!nas.has("Trip/1.jpg"));
        assert_eq!(here.round(&nas).await.unwrap().changed(), 0);
    }

    #[tokio::test]
    async fn deleted_on_the_nas_goes_to_the_trash_here() {
        let (here, nas) = (Place::new(), Mem::default());
        nas.put("Trip/1.jpg", "1", T);
        nas.put("keep.txt", "k", T);
        here.round(&nas).await.unwrap();
        nas.files.lock().unwrap().retain(|p, _| !p.starts_with("Trip"));
        let report = here.round(&nas).await.unwrap();
        assert_eq!(report.trashed_local, 1);
        assert!(here.read("Trip/1.jpg").is_none());
        assert_eq!(std::fs::read_to_string(here.trash.join("Trip").join("1.jpg")).unwrap(), "1");
        assert_eq!(here.read("keep.txt").as_deref(), Some("k"));
    }

    #[tokio::test]
    async fn changed_on_both_sides_keeps_both() {
        let (here, nas) = (Place::new(), Mem::default());
        nas.put("plan.txt", "v1", T);
        here.round(&nas).await.unwrap();
        here.write("plan.txt", "mine, v2", T + 100);
        nas.put("plan.txt", "theirs, v2", T + 200);
        let report = here.round(&nas).await.unwrap();
        assert_eq!(report.conflicts, 1, "{report:?}");
        assert_eq!(here.read("plan.txt").as_deref(), Some("theirs, v2"));
        assert_eq!(nas.body("plan.txt").as_deref(), Some("theirs, v2"));
        let copy = scan(&here.root).unwrap().keys().find(|p| p.contains("(conflict laptop")).cloned().expect("a conflict copy");
        assert!(copy.starts_with("plan (conflict laptop ") && copy.ends_with(").txt"), "{copy}");
        assert_eq!(here.read(&copy).as_deref(), Some("mine, v2"));
        assert_eq!(nas.body(&copy).as_deref(), Some("mine, v2"));
        assert_eq!(here.round(&nas).await.unwrap().changed(), 0);
    }

    #[tokio::test]
    async fn many_deletions_wait_for_a_yes() {
        let (here, nas) = (Place::new(), Mem::default());
        for i in 0..30 {
            nas.put(&format!("f{i}.txt"), "x", T);
        }
        here.round(&nas).await.unwrap();
        for i in 0..30 {
            std::fs::remove_file(local_path(&here.root, &format!("f{i}.txt"))).unwrap();
        }
        let err = here.round(&nas).await.unwrap_err();
        assert_eq!(err.downcast_ref::<ManyDeletions>().map(|m| m.count), Some(30));
        assert!(nas.has("f0.txt"), "nothing deleted before the yes");
        let report = sync_once(&here.root, &nas, &here.state, &here.options(true)).await.unwrap();
        assert_eq!(report.deleted_remote, 30);
    }

    #[tokio::test]
    async fn a_missing_sync_folder_changes_nothing() {
        let (here, nas) = (Place::new(), Mem::default());
        nas.put("a.txt", "a", T);
        here.round(&nas).await.unwrap();
        std::fs::remove_dir_all(&here.root).unwrap();
        let err = here.round(&nas).await.unwrap_err();
        assert!(err.to_string().contains("is not there"), "{err}");
        assert!(nas.has("a.txt") && nas.deleted.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn an_incomplete_listing_deletes_nothing_here() {
        let (here, mut nas) = (Place::new(), Mem::default());
        nas.put("a.txt", "a", T);
        here.round(&nas).await.unwrap();
        nas.files.lock().unwrap().clear();
        nas.incomplete = true;
        let report = here.round(&nas).await.unwrap();
        assert_eq!(report.trashed_local, 0);
        assert_eq!(here.read("a.txt").as_deref(), Some("a"));
    }

    #[tokio::test]
    async fn a_share_to_read_only_never_changes_on_the_nas() {
        let (here, mut nas) = (Place::new(), Mem::default());
        nas.read_only = true;
        nas.put("a.txt", "a", T);
        here.round(&nas).await.unwrap();
        here.write("mine.txt", "m", T);
        std::fs::remove_file(local_path(&here.root, "a.txt")).unwrap();
        let report = here.round(&nas).await.unwrap();
        assert_eq!((report.uploaded, report.deleted_remote, report.downloaded), (0, 0, 1));
        assert!(!nas.has("mine.txt") && nas.has("a.txt"));
        assert_eq!(here.read("a.txt").as_deref(), Some("a"));
        assert_eq!(here.read("mine.txt").as_deref(), Some("m"));
    }

    #[tokio::test]
    async fn a_folder_deleted_here_comes_back_when_the_nas_has_something_new_in_it() {
        let (here, nas) = (Place::new(), Mem::default());
        nas.put("Trip/old.jpg", "o", T);
        here.round(&nas).await.unwrap();
        std::fs::remove_dir_all(local_path(&here.root, "Trip")).unwrap();
        nas.put("Trip/new.jpg", "n", T + 50);
        here.round(&nas).await.unwrap();
        assert_eq!(here.read("Trip/new.jpg").as_deref(), Some("n"));
        assert!(nas.has("Trip/new.jpg"));
    }

    #[test]
    fn plans_are_what_they_say() {
        let file = |s, m| Item::file(s, m);
        let base: Tree = [("a".to_string(), file(1, T))].into();
        let same: Tree = base.clone();
        assert!(plan(&same, &same, &base, true, false).is_empty());
        let newer: Tree = [("a".to_string(), file(2, T + 9))].into();
        assert_eq!(plan(&newer, &same, &base, true, false), vec![Action::Upload("a".into())]);
        assert_eq!(plan(&same, &newer, &base, true, false), vec![Action::Download("a".into())]);
        // Two seconds apart is the same date (FAT).
        let fat: Tree = [("a".to_string(), file(1, T + 2))].into();
        assert!(plan(&fat, &same, &base, true, false).is_empty());
        assert_eq!(plan(&newer, &Tree::new(), &base, true, false), vec![Action::Upload("a".into())]);
        assert_eq!(plan(&Tree::new(), &newer, &base, true, false), vec![Action::Download("a".into())]);
    }

    #[test]
    fn names_for_conflicts_and_what_is_left_out() {
        let at = chrono::TimeZone::with_ymd_and_hms(&chrono::Local, 2026, 10, 26, 14, 5, 0).unwrap();
        assert_eq!(conflict_name("Docs/report.pdf", "anna-laptop", at), "Docs/report (conflict anna-laptop 2026-10-26 14-05).pdf");
        assert_eq!(conflict_name("Makefile", "pc", at), "Makefile (conflict pc 2026-10-26 14-05)");
        assert_eq!(conflict_name(".bashrc", "pc", at), ".bashrc (conflict pc 2026-10-26 14-05)");
        for junk in [".DS_Store", "Thumbs.db", "desktop.ini", "~$doc.docx", ".~lock.a.odt#", "x.tmp", ".alvaos-sync-1.part", "a.alvaos-upload"] {
            assert!(ignored(junk), "{junk}");
        }
        assert!(!ignored("report.pdf") && !ignored(".bashrc"));
    }

    #[test]
    fn webdav_addresses_are_encoded_per_part() {
        let nas = DavRemote::new("http://127.0.0.1:18090", "http://127.0.0.1:18091/", "Family", "/Photos/2024/", "anna", "t", false);
        assert_eq!(nas.url("Sommer & Meer/ä #1.jpg"), "http://127.0.0.1:18091/Family/Photos/2024/Sommer%20%26%20Meer/%C3%A4%20%231.jpg");
    }

    #[test]
    fn the_patterns_of_what_is_left_out() {
        let f = Filter::new(&["*.MOV".into(), "node_modules".into(), "# a comment".into(), "".into(), "Videos/".into(), "/Backup/old?".into(), "Photos/Raw".into()]);
        for left_out in ["a.mov", "trip/clip.mov", "app/node_modules", "app/node_modules/x/y.js", "Videos", "videos/a.txt", "Backup/old1/f", "Photos/Raw/img.cr2"] {
            assert!(f.matches(left_out), "{left_out}");
        }
        for kept in ["a.mp4", "trip/Videos", "Docs/Backup/old1", "Backup/older/f", "Photos/Jpg/Raw", "notes.txt"] {
            assert!(!f.matches(kept), "{kept}");
        }
        assert!(!Filter::new(&[]).matches("anything"));
    }

    #[tokio::test]
    async fn left_out_files_are_not_sent_fetched_or_deleted() {
        let (here, nas) = (Place::new(), Mem::default());
        nas.put("Videos/b.mov", "theirs", T);
        nas.put("keep.txt", "keep", T);
        here.write("Videos/a.mov", "mine", T);
        here.write("Docs/cache/x.tmp2", "x", T);
        let mut options = here.options(false);
        options.exclude = vec!["Videos/".into(), "cache".into()];
        let report = sync_once(&here.root, &nas, &here.state, &options).await.unwrap();
        assert_eq!((report.uploaded, report.downloaded), (0, 1), "{report:?}");
        assert!(here.read("Videos/b.mov").is_none() && !nas.has("Videos/a.mov") && !nas.has("Docs/cache/x.tmp2"));
        // It was synced before, then left out: nothing is deleted on either side, it is only forgotten.
        let before = Place::new();
        let nas2 = Mem::default();
        before.write("Videos/a.mov", "mine", T);
        before.round(&nas2).await.unwrap();
        assert!(nas2.has("Videos/a.mov"));
        let mut options = before.options(false);
        options.exclude = vec!["*.mov".into()];
        let r = sync_once(&before.root, &nas2, &before.state, &options).await.unwrap();
        assert_eq!(r.changed(), 0, "{r:?}");
        assert!(before.read("Videos/a.mov").is_some() && nas2.has("Videos/a.mov"));
        assert!(!load_state(&before.state).contains_key("Videos/a.mov"));
    }

    #[tokio::test]
    async fn progress_and_the_activity_log_say_what_happened() {
        let (here, nas) = (Place::new(), Mem::default());
        nas.put("a.txt", "A", T);
        nas.put("b.txt", "B", T);
        here.write("c.txt", "C", T);
        let seen = Arc::new(Mutex::new(Vec::<Progress>::new()));
        let sink = seen.clone();
        let mut options = here.options(false);
        options.progress = Some(Arc::new(move |p| sink.lock().unwrap().push(p)));
        let report = sync_once(&here.root, &nas, &here.state, &options).await.unwrap();
        let seen = seen.lock().unwrap().clone();
        assert_eq!(seen.len(), 3);
        assert!(seen.iter().enumerate().all(|(i, p)| p.done == i && p.total == 3), "{seen:?}");
        assert!(report.log.contains(&"Sent c.txt".to_string()) && report.log.contains(&"Received a.txt".to_string()), "{:?}", report.log);
        // A conflict names the copy it kept.
        here.write("c.txt", "mine again", T + 100);
        nas.put("c.txt", "theirs, longer", T + 200);
        let r = here.round(&nas).await.unwrap();
        assert_eq!(r.conflict_files.len(), 1, "{r:?}");
        assert!(r.conflict_files[0].starts_with("c (conflict laptop"), "{:?}", r.conflict_files);
    }

    #[tokio::test]
    async fn a_busy_nas_is_tried_again_and_other_errors_are_not() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let none = [Duration::ZERO, Duration::ZERO];
        let tries = AtomicUsize::new(0);
        let ok = retrying(&none, || async {
            if tries.fetch_add(1, Ordering::SeqCst) < 2 { Err(Transient("503".into()).into()) } else { Ok(7) }
        })
        .await;
        assert_eq!((ok.unwrap(), tries.load(Ordering::SeqCst)), (7, 3));
        let tries = AtomicUsize::new(0);
        let gave_up: Result<()> = retrying(&none, || async { tries.fetch_add(1, Ordering::SeqCst); Err(Transient("503".into()).into()) }).await;
        assert!(gave_up.is_err() && tries.load(Ordering::SeqCst) == 3);
        let tries = AtomicUsize::new(0);
        let not_again: Result<()> = retrying(&none, || async { tries.fetch_add(1, Ordering::SeqCst); Err(anyhow!("the NAS answered 403")) }).await;
        assert!(not_again.is_err() && tries.load(Ordering::SeqCst) == 1);
    }
}
