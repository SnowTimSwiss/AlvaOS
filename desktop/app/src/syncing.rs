//! The sync folders in the app (the engine is alvaos_core::sync): a round every minute, and
//! a few seconds after something changed in a sync folder on this computer. One round at a
//! time, one folder after the other.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use alvaos_core::config::{default_sync_root, SyncFolder};
use alvaos_core::hub::Hub;
use alvaos_core::link;
use alvaos_core::sync::{self, DavRemote, ManyDeletions, Options};
use alvaos_core::DeviceInfo;
use notify::{RecursiveMode, Watcher};
use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Manager, State};

use crate::{paint_tray, text, App};

const EVERY: Duration = Duration::from_secs(60);
const SETTLE: Duration = Duration::from_secs(3);

#[derive(Debug, Clone, Default, Serialize)]
pub struct FolderStatus {
    /// `waiting`, `syncing`, `ok`, `problem`, `ask` (many deletions), `paused`.
    pub state: String,
    /// When the last round of this folder ended (seconds since 1970).
    pub last: Option<i64>,
    pub message: String,
    /// Files a round would delete, waiting for a yes.
    pub ask: usize,
    pub problems: Vec<String>,
    pub changed: usize,
    /// While a round runs: which file, and how far.
    pub progress: Option<sync::Progress>,
    /// Conflict copies made here (shown while they are still there).
    pub conflicts: Vec<String>,
    /// What was done lately, the newest first.
    pub recent: Vec<Activity>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Activity {
    pub at: i64,
    pub text: String,
}

const RECENT: usize = 40;

#[derive(Default)]
pub struct Syncing {
    pub folders: Mutex<BTreeMap<String, FolderStatus>>,
    pub kick: tokio::sync::Notify,
    round: tokio::sync::Mutex<()>,
    allow_once: Mutex<BTreeSet<String>>,
    watcher: Mutex<Option<notify::RecommendedWatcher>>,
}

fn now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// The line for the tray and the app's page.
pub fn summary(app: &AppHandle) -> String {
    let me = app.state::<App>();
    let config = me.config();
    if config.sync.is_empty() {
        return String::new();
    }
    if config.sync_paused {
        return "Sync paused".into();
    }
    let folders = me.syncing.folders.lock().unwrap();
    let states: Vec<&FolderStatus> = config.sync.iter().filter_map(|f| folders.get(&f.id)).collect();
    if states.iter().any(|s| s.state == "syncing") {
        return "Syncing…".into();
    }
    if let Some(ask) = states.iter().find(|s| s.state == "ask") {
        return format!("Sync waits for you: {} deletions", ask.ask);
    }
    let problems: usize = states.iter().map(|s| s.problems.len() + usize::from(s.state == "problem")).sum();
    if problems > 0 {
        return format!("Synced, with {problems} problem{}", if problems == 1 { "" } else { "s" });
    }
    match states.iter().filter_map(|s| s.last).min() {
        Some(at) => format!("Synced {}", ago(now() - at)),
        None => "Sync starts…".into(),
    }
}

pub fn ago(seconds: i64) -> String {
    match seconds {
        s if s < 60 => "just now".into(),
        s if s < 3600 => format!("{} min ago", s / 60),
        s if s < 86400 => format!("{} h ago", s / 3600),
        s => format!("{} days ago", s / 86400),
    }
}

/// What the app's page shows about the sync folders.
pub fn state_json(app: &AppHandle) -> Value {
    let me = app.state::<App>();
    let config = me.config();
    let folders = me.syncing.folders.lock().unwrap();
    let list: Vec<Value> = config
        .sync
        .iter()
        .map(|f| {
            let status = folders.get(&f.id).cloned().unwrap_or(FolderStatus { state: "waiting".into(), ..Default::default() });
            let conflicts: Vec<&String> = status.conflicts.iter().filter(|c| f.local.join(c.as_str()).exists()).collect();
            json!({"id": f.id, "label": f.label(), "share": f.share, "folder": f.folder, "local": f.local, "read_only": f.read_only,
                   "exclude": f.exclude, "conflicts": conflicts,
                   "status": status, "ago": status.last.map(|at| ago(now() - at))})
        })
        .collect();
    drop(folders);
    json!({"folders": list, "paused": config.sync_paused, "root": default_sync_root(), "line": summary(app)})
}

/// The loop: a round every minute, or soon after a change here.
pub async fn run(app: AppHandle) {
    loop {
        let syncing = &app.state::<App>().syncing;
        tokio::select! {
            _ = syncing.kick.notified() => tokio::time::sleep(SETTLE).await,
            _ = tokio::time::sleep(EVERY) => {}
        }
        round(&app).await;
    }
}

fn set(app: &AppHandle, id: &str, change: impl FnOnce(&mut FolderStatus)) {
    let me = app.state::<App>();
    {
        let mut folders = me.syncing.folders.lock().unwrap();
        change(folders.entry(id.to_string()).or_default());
    }
    paint_tray(app);
}

/// One round over every sync folder, when connected and not paused.
pub async fn round(app: &AppHandle) {
    let me = app.state::<App>();
    let Ok(_one) = me.syncing.round.try_lock() else { return };
    let config = me.config();
    if config.sync.is_empty() || !config.is_set_up() || !config.connected {
        return;
    }
    if config.sync_paused {
        for f in &config.sync {
            set(app, &f.id, |s| s.state = "paused".into());
        }
        return;
    }
    if me.status.lock().unwrap().state != "ok" {
        return; // not connected right now: the next round
    }
    let (hub, dav) = {
        let net = me.net.lock().await;
        (net.hub.as_ref().map(|p| p.url()), net.dav.as_ref().map(|p| p.url()))
    };
    let (Some(hub), Some(dav), Some(token)) = (hub, dav, me.token()) else { return };
    let computer = DeviceInfo::this_computer().name;
    for folder in &config.sync {
        set(app, &folder.id, |s| { s.state = "syncing".into(); s.progress = None; });
        let remote = DavRemote::new(&hub, &dav, &folder.share, &folder.folder, &config.user, &token, folder.read_only);
        let allow = me.syncing.allow_once.lock().unwrap().remove(&folder.id);
        let tell = {
            let (handle, id) = (app.clone(), folder.id.clone());
            Arc::new(move |p: sync::Progress| set(&handle, &id, |s| s.progress = Some(p)))
        };
        let options = Options { computer: computer.clone(), allow_many_deletions: allow, trash: None,
                                exclude: folder.exclude.clone(), progress: Some(tell) };
        let result = sync::sync_once(&folder.local, &remote, &me.store.sync_state(&folder.id), &options).await;
        set(app, &folder.id, |s| match result {
            Ok(report) => {
                let mut recent: Vec<Activity> = report.log.iter().rev().map(|text| Activity { at: now(), text: text.clone() }).collect();
                recent.extend(std::mem::take(&mut s.recent));
                recent.truncate(RECENT);
                let mut conflicts = std::mem::take(&mut s.conflicts);
                conflicts.extend(report.conflict_files.iter().cloned());
                *s = FolderStatus {
                    state: if report.problems.is_empty() { "ok" } else { "problem" }.into(),
                    last: Some(now()),
                    message: String::new(),
                    ask: 0,
                    changed: report.changed(),
                    problems: report.problems.into_iter().take(20).collect(),
                    progress: None,
                    conflicts,
                    recent,
                };
            }
            Err(err) if err.is::<ManyDeletions>() => {
                s.progress = None;
                s.state = "ask".into();
                s.ask = err.downcast_ref::<ManyDeletions>().map(|m| m.count).unwrap_or(0);
                s.message = err.to_string();
            }
            Err(err) => {
                s.progress = None;
                s.state = "problem".into();
                s.message = format!("{err:#}");
            }
        });
    }
}

/// Watches the sync folders here: a change starts a round a few seconds later.
pub fn watch(app: &AppHandle) {
    let me = app.state::<App>();
    let folders: Vec<PathBuf> = me.config().sync.iter().map(|f| f.local.clone()).collect();
    let handle = app.clone();
    let watcher = notify::recommended_watcher(move |event: notify::Result<notify::Event>| {
        let Ok(event) = event else { return };
        // Our own half-done downloads do not count.
        let ours = event.paths.iter().all(|p| p.file_name().map(|n| sync::ignored(&n.to_string_lossy())).unwrap_or(false));
        if !ours && !matches!(event.kind, notify::EventKind::Access(_)) {
            handle.state::<App>().syncing.kick.notify_one();
        }
    });
    let mut slot = me.syncing.watcher.lock().unwrap();
    *slot = None;
    if let Ok(mut watcher) = watcher {
        for folder in folders {
            let _ = std::fs::create_dir_all(&folder);
            let _ = watcher.watch(&folder, RecursiveMode::Recursive);
        }
        *slot = Some(watcher);
    }
}

// ── Commands of the app's pages ───────────────────────────────────────────────

/// The shares of the person (to choose a sync folder).
#[tauri::command]
pub async fn remote_shares(state: State<'_, App>) -> Result<Value, String> {
    let url = state.hub_url().await.ok_or("Not connected to the NAS.")?;
    let token = state.token().ok_or("This computer is not signed in.")?;
    let me = Hub::new(&url).me(&token).await.map_err(text)?;
    Ok(me["shares"].clone())
}

/// The folders in a folder of a share.
#[tauri::command]
pub async fn remote_folders(state: State<'_, App>, share: String, path: String) -> Result<Vec<String>, String> {
    let url = state.hub_url().await.ok_or("Not connected to the NAS.")?;
    let token = state.token().ok_or("This computer is not signed in.")?;
    Hub::new(&url).folders(&token, &share, &path).await.map_err(text)
}

fn overlaps(a: &Path, b: &Path) -> bool {
    a.starts_with(b) || b.starts_with(a)
}

/// A new sync folder: a folder of a share, kept here (by default in ~/AlvaOS/<share>/<folder>).
#[tauri::command]
pub async fn add_sync(app: AppHandle, state: State<'_, App>, share: String, folder: String, read_only: bool,
                      local: Option<String>) -> Result<Value, String> {
    let folder = folder.trim_matches('/').to_string();
    let mut config = state.config();
    let place = match local.filter(|l| !l.trim().is_empty()) {
        Some(chosen) => PathBuf::from(chosen),
        None => {
            let mut p = default_sync_root().join(&share);
            for part in folder.split('/').filter(|p| !p.is_empty()) {
                p = p.join(part);
            }
            p
        }
    };
    if let Some(other) = config.sync.iter().find(|f| overlaps(&f.local, &place)) {
        return Err(format!("{} is already kept in {}. Choose another place.", other.label(), other.local.display()));
    }
    if config.sync.iter().any(|f| f.share == share && (f.folder == folder || f.folder.is_empty() || folder.starts_with(&format!("{}/", f.folder)))) {
        return Err("This folder (or the folder around it) is already a sync folder.".into());
    }
    // Files there already are fine: the first round puts both together (a conflict keeps both).
    let id = link::hex(&link::new_key()[..5]);
    config.sync.push(SyncFolder { id: id.clone(), share, folder, local: place, read_only, exclude: Vec::new() });
    state.save(config)?;
    watch(&app);
    state.syncing.kick.notify_one();
    Ok(state_json(&app))
}

/// What a sync folder leaves out ("Don't sync"), one pattern per line.
#[tauri::command]
pub async fn set_exclude(app: AppHandle, state: State<'_, App>, id: String, text: String) -> Result<Value, String> {
    let mut config = state.config();
    let folder = config.sync.iter_mut().find(|f| f.id == id).ok_or("This sync folder is not there any more.")?;
    folder.exclude = text.lines().map(str::trim).filter(|l| !l.is_empty()).map(str::to_string).collect();
    state.save(config)?;
    state.syncing.kick.notify_one();
    Ok(state_json(&app))
}

/// No more sync for a folder: the files here stay where they are.
#[tauri::command]
pub async fn remove_sync(app: AppHandle, state: State<'_, App>, id: String) -> Result<Value, String> {
    let mut config = state.config();
    config.sync.retain(|f| f.id != id);
    state.save(config)?;
    let _ = std::fs::remove_file(state.store.sync_state(&id));
    state.syncing.folders.lock().unwrap().remove(&id);
    watch(&app);
    paint_tray(&app);
    Ok(state_json(&app))
}

#[tauri::command]
pub async fn sync_now(app: AppHandle) -> Result<Value, String> {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move { round(&handle).await });
    Ok(state_json(&app))
}

/// The answer to "many files would be deleted": yes (delete them, into the trash), or no
/// (bring them back: the next round starts as the first one did, and copies what is missing).
#[tauri::command]
pub async fn sync_answer(app: AppHandle, state: State<'_, App>, id: String, delete: bool) -> Result<Value, String> {
    if delete {
        state.syncing.allow_once.lock().unwrap().insert(id);
    } else {
        let _ = std::fs::remove_file(state.store.sync_state(&id));
    }
    let handle = app.clone();
    tauri::async_runtime::spawn(async move { round(&handle).await });
    Ok(state_json(&app))
}

#[tauri::command]
pub async fn pause_sync(app: AppHandle, state: State<'_, App>, paused: bool) -> Result<Value, String> {
    let mut config = state.config();
    config.sync_paused = paused;
    state.save(config)?;
    if !paused {
        state.syncing.kick.notify_one();
    }
    paint_tray(&app);
    Ok(state_json(&app))
}

#[tauri::command]
pub async fn open_sync(state: State<'_, App>, id: Option<String>) -> Result<(), String> {
    let config = state.config();
    let place = id
        .and_then(|id| config.sync.iter().find(|f| f.id == id).map(|f| f.local.clone()))
        .unwrap_or_else(default_sync_root);
    let _ = std::fs::create_dir_all(&place);
    tauri_plugin_opener::open_path(&place, None::<&str>).map_err(text)
}

/// A folder of this computer, chosen in the system's dialog.
#[tauri::command]
pub async fn pick_local(app: AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = tokio::sync::oneshot::channel();
    app.dialog().file().set_directory(default_sync_root()).pick_folder(move |picked| {
        let _ = tx.send(picked.and_then(|p| p.into_path().ok()).map(|p| p.to_string_lossy().to_string()));
    });
    rx.await.map_err(text)
}
