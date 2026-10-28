//! AlvaOS for the computer (docs/DESKTOP.md): an icon in the tray that keeps the computer
//! connected to the NAS through AlvaOS Link, from anywhere. The NAS then has addresses on this
//! computer (`http://127.0.0.1:18090` for the Hub, `:18091` for Files over WebDAV), for the
//! app's window, the browser and every other program. The setup asks what to use: the Hub apps
//! in a window, Files as a drive, starting with the computer.
//!
//! Two kinds of windows: `main` shows the app's own pages (ui/: setup, status, settings) and may
//! call the commands below; `hub` shows the Hub of the NAS and may call nothing.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod syncing;

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use alvaos_core::config::{Config, Store, LINK_KEY, SESSION};
use alvaos_core::hub::{Hub, NeedsCode, SignedOut};
use alvaos_core::link::{self, Link, NotPaired};
use alvaos_core::pairlink::{self, PairLink};
use alvaos_core::proxy::{Proxy, Way};
use alvaos_core::{drive, DeviceInfo, Paired};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::{DownloadEvent, NewWindowResponse};
use tauri::{AppHandle, Manager, RunEvent, State, WebviewUrl, WebviewWindowBuilder, WindowEvent, Wry};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

const CHECK_SECONDS: u64 = 20;

/// Tests against a NAS on the same machine (ALVAOS_LINK_HINTS: its addresses): no relay, loopback only.
fn hints() -> Vec<String> {
    std::env::var("ALVAOS_LINK_HINTS").map(|h| h.split(',').map(str::to_string).filter(|h| !h.is_empty()).collect()).unwrap_or_default()
}

fn direct_only() -> bool {
    !hints().is_empty()
}

#[derive(Default, Clone, Serialize)]
struct Status {
    /// `setup`, `off`, `connecting`, `ok`, `offline`, `signed_out`.
    state: String,
    /// `direct`, `relay` or `home` (a NAS without Link, at home).
    route: String,
    ms: u64,
    message: String,
    drive: bool,
}

#[derive(Default)]
struct Net {
    link: Option<Arc<Link>>,
    hub: Option<Proxy>,
    dav: Option<Proxy>,
}

struct TrayItems {
    status: MenuItem<Wry>,
    sync: MenuItem<Wry>,
    connected: CheckMenuItem<Wry>,
    drive: MenuItem<Wry>,
}

struct App {
    store: Store,
    config: Mutex<Config>,
    net: tokio::sync::Mutex<Net>,
    status: Mutex<Status>,
    tray: Mutex<Option<TrayItems>>,
    refused: AtomicU32,
    syncing: syncing::Syncing,
}

impl App {
    fn config(&self) -> Config {
        self.config.lock().unwrap().clone()
    }

    fn save(&self, config: Config) -> Result<(), String> {
        self.store.save(&config).map_err(text)?;
        *self.config.lock().unwrap() = config;
        Ok(())
    }

    fn token(&self) -> Option<String> {
        self.store.secret(SESSION)
    }

    /// This computer's Link key: made once, kept in the key store.
    fn key(&self) -> Result<[u8; 32], String> {
        if let Some(key) = self.store.secret(LINK_KEY).and_then(|hex| link::unhex(&hex)) {
            return Ok(key);
        }
        let key = link::new_key();
        self.store.set_secret(LINK_KEY, &link::hex(&key)).map_err(text)?;
        Ok(key)
    }

    async fn hub_url(&self) -> Option<String> {
        self.net.lock().await.hub.as_ref().map(Proxy::url)
    }
}

fn text(err: impl std::fmt::Display) -> String {
    err.to_string()
}

// ── Status: the tray and the app's page ──────────────────────────────────────

fn set_status(app: &AppHandle, state: &str, route: &str, ms: u64, message: &str) {
    let me = app.state::<App>();
    let drive = me.status.lock().unwrap().drive;
    let status = Status { state: state.into(), route: route.into(), ms, message: message.into(), drive };
    *me.status.lock().unwrap() = status.clone();
    paint_tray(app);
}

fn status_line(config: &Config, status: &Status) -> String {
    let nas = if config.nas_name.is_empty() { "the NAS".to_string() } else { config.nas_name.clone() };
    match status.state.as_str() {
        "ok" => match status.route.as_str() {
            "relay" => format!("Connected to {nas} · through a relay"),
            "home" => format!("Connected to {nas} · at home"),
            _ => format!("Connected to {nas} · direct · {} ms", status.ms),
        },
        "connecting" => format!("Connecting to {nas}…"),
        "offline" => format!("{nas} cannot be reached"),
        "off" => "Not connected (switched off)".into(),
        "signed_out" => "Signed out: connect this computer again".into(),
        _ => "Not set up yet".into(),
    }
}

fn paint_tray(app: &AppHandle) {
    let me = app.state::<App>();
    let config = me.config();
    let status = me.status.lock().unwrap().clone();
    let line = status_line(&config, &status);
    if let Some(items) = me.tray.lock().unwrap().as_ref() {
        let _ = items.status.set_text(&line);
        let _ = items.connected.set_checked(config.connected && config.is_set_up());
        let _ = items.connected.set_enabled(config.is_set_up());
        let _ = items.drive.set_text(if status.drive { "Remove the Files drive" } else { "Show Files as a drive" });
        let _ = items.drive.set_enabled(config.is_set_up() && status.state == "ok");
        let sync_line = syncing::summary(app);
        let _ = items.sync.set_text(if sync_line.is_empty() { "No sync folder yet".to_string() } else { sync_line });
    }
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_tooltip(Some(format!("AlvaOS · {line}")));
    }
}

// ── Connected or not ──────────────────────────────────────────────────────────

/// Starts the way to the NAS: Link (or, for a NAS without Link, its address at home) and the
/// NAS's addresses on this computer.
async fn go_online(app: &AppHandle) -> Result<(), String> {
    let me = app.state::<App>();
    let mut config = me.config();
    if !config.is_set_up() {
        return Err("This computer is not connected to a NAS yet.".into());
    }
    set_status(app, "connecting", "", 0, "");
    let mut net = me.net.lock().await;
    net.hub = None;
    net.dav = None;
    let (hub_way, dav_way) = if let Some(nas) = config.link.clone() {
        let link = match net.link.clone() {
            Some(link) => link,
            None => Arc::new(Link::start(&me.key()?, direct_only()).await.map_err(text)?),
        };
        net.link = Some(link.clone());
        let way = |service: &str| Way::Link { link: link.clone(), nas: nas.clone(), service: service.into(), hints: hints() };
        (way("hub"), way("dav"))
    } else if let Some(home) = config.home() {
        let host = home.rsplit_once(':').map(|(h, _)| h.to_string()).unwrap_or_else(|| home.clone());
        (Way::Direct(home), Way::Direct(format!("{host}:8091")))
    } else {
        return Err("The NAS has no address this computer can use.".into());
    };
    let hub = Proxy::start(config.hub_port, hub_way).await.map_err(text)?;
    let dav = Proxy::start(config.dav_port, dav_way).await.map_err(text)?;
    if (hub.port, dav.port) != (config.hub_port, config.dav_port) {
        config.hub_port = hub.port;
        config.dav_port = dav.port;
        me.save(config)?;
    }
    net.hub = Some(hub);
    net.dav = Some(dav);
    drop(net);
    check(app).await;
    Ok(())
}

async fn go_offline(app: &AppHandle) {
    let me = app.state::<App>();
    let mut net = me.net.lock().await;
    net.hub = None;
    net.dav = None;
    if let Some(link) = net.link.take() {
        link.close().await;
    }
}

/// Is the NAS there, which way, and does it still know this computer?
async fn check(app: &AppHandle) {
    let me = app.state::<App>();
    let config = me.config();
    if !config.is_set_up() {
        set_status(app, if me.status.lock().unwrap().state == "signed_out" { "signed_out" } else { "setup" }, "", 0, "");
        return;
    }
    if !config.connected {
        set_status(app, "off", "", 0, "");
        return;
    }
    let (link, url) = {
        let net = me.net.lock().await;
        (net.link.clone(), net.hub.as_ref().map(Proxy::url))
    };
    let Some(url) = url else { return };
    let token = me.token().unwrap_or_default();
    let reached = match (&link, &config.link) {
        (Some(link), Some(nas)) => match link.ping(nas, &hints()).await {
            Ok(route) => Ok((route.kind, route.ms)),
            Err(err) if err.is::<NotPaired>() => Err((true, err.to_string())),
            Err(err) => Err((false, err.to_string())),
        },
        _ => match Hub::new(&url).me(&token).await {
            Ok(_) => Ok(("home".to_string(), 0)),
            Err(err) if err.is::<SignedOut>() => Err((true, err.to_string())),
            Err(err) => Err((false, err.to_string())),
        },
    };
    match reached {
        Ok((route, ms)) => {
            // Signed out on the NAS (a new password ends the session, Link may still know the key)?
            let out = matches!(Hub::new(&url).me(&token).await, Err(ref e) if e.is::<SignedOut>());
            if out {
                refused(app).await;
            } else {
                me.refused.store(0, Ordering::Relaxed);
                set_status(app, "ok", &route, ms, "");
            }
        }
        // Once can be a moment (the NAS restarting): only the second time in a row counts.
        Err((true, _)) => refused(app).await,
        Err((false, message)) => {
            me.refused.store(0, Ordering::Relaxed);
            set_status(app, "offline", "", 0, &message);
        }
    }
}

async fn refused(app: &AppHandle) {
    let me = app.state::<App>();
    if me.refused.fetch_add(1, Ordering::Relaxed) == 0 {
        return;
    }
    me.refused.store(0, Ordering::Relaxed);
    forget(app).await;
    set_status(app, "signed_out", "", 0, "This computer was signed out on the NAS. Connect it again.");
    show_main(app);
}

/// Back to the start on this computer (the Link key stays: it is this computer's).
async fn forget(app: &AppHandle) {
    let me = app.state::<App>();
    let config = me.config();
    if me.status.lock().unwrap().drive {
        let _ = drive::unmount(config.dav_port, &config.user).await;
        me.status.lock().unwrap().drive = false;
    }
    go_offline(app).await;
    if let Some(window) = app.get_webview_window("hub") {
        let _ = window.destroy();
    }
    let _ = me.store.forget();
    *me.config.lock().unwrap() = me.store.load();
    me.syncing.folders.lock().unwrap().clear();
    syncing::watch(app);
}

// ── Windows ───────────────────────────────────────────────────────────────────

/// What opening AlvaOS means: the Hub apps in their window; the small window when that is
/// not possible (not set up, the window switched off, the NAS not reached).
async fn open_interface(app: &AppHandle) {
    let me = app.state::<App>();
    let config = me.config();
    if config.is_set_up() && config.use_window && config.connected && show_hub(app).await.is_ok() {
        return;
    }
    show_main(app);
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        return;
    }
    let built = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("AlvaOS")
        .inner_size(460.0, 640.0)
        .min_inner_size(380.0, 520.0)
        .build();
    if let Ok(window) = built {
        let _ = window.set_focus();
    }
}

/// A code that works once, for a minute: the Hub opened with it is signed in, with a session
/// of its own (the app's token never reaches a page).
async fn signed_in_url(app: &AppHandle) -> Result<String, String> {
    let me = app.state::<App>();
    let url = me.hub_url().await.ok_or("Not connected to the NAS. Switch it on in the tray.")?;
    let token = me.token().ok_or("This computer is not signed in.")?;
    let code = Hub::new(&url).handoff(&token).await.map_err(text)?;
    Ok(format!("{url}/?handoff={code}"))
}

async fn show_hub(app: &AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("hub") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        return Ok(());
    }
    let target = signed_in_url(app).await?;
    let config = app.state::<App>().config();
    let port = config.hub_port;
    let ours = move |url: &tauri::Url| url.host_str() == Some("127.0.0.1") && url.port() == Some(port);
    let downloads = dirs::download_dir().unwrap_or_else(std::env::temp_dir);
    let handle = app.clone();
    WebviewWindowBuilder::new(app, "hub", WebviewUrl::External(target.parse().map_err(text)?))
        .title(format!("AlvaOS · {}", if config.nas_name.is_empty() { "Hub" } else { &config.nas_name }))
        .inner_size(1240.0, 820.0)
        .min_inner_size(380.0, 520.0)
        // Links to other places open in the browser, not in this window.
        .on_navigation(move |url| {
            if ours(url) {
                return true;
            }
            let _ = tauri_plugin_opener::open_url(url.as_str(), None::<&str>);
            false
        })
        .on_new_window(move |url, _| {
            if !ours(&url) {
                let _ = tauri_plugin_opener::open_url(url.as_str(), None::<&str>);
                return NewWindowResponse::Deny;
            }
            // A file of the NAS opened in a new tab: a window of its own, signed in as this one.
            let label = format!("hub-{}", link::hex(&link::new_key()[..4]));
            match WebviewWindowBuilder::new(&handle, label, WebviewUrl::External(url)).title("AlvaOS").inner_size(1000.0, 760.0).build() {
                Ok(window) => NewWindowResponse::Create { window },
                Err(_) => NewWindowResponse::Deny,
            }
        })
        // Downloads go to the Downloads folder, under their own name.
        .on_download(move |_, event| {
            if let DownloadEvent::Requested { url, destination } = event {
                let name = destination
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .filter(|n| !n.is_empty())
                    .or_else(|| url.path_segments().and_then(|mut s| s.next_back().map(str::to_string)))
                    .unwrap_or_else(|| "download".into());
                *destination = free_name(&downloads, &name);
            }
            true
        })
        .build()
        .map_err(text)?;
    Ok(())
}

fn free_name(dir: &std::path::Path, name: &str) -> std::path::PathBuf {
    let path = dir.join(name);
    if !path.exists() {
        return path;
    }
    let (stem, ext) = name.rsplit_once('.').map(|(s, e)| (s.to_string(), format!(".{e}"))).unwrap_or((name.to_string(), String::new()));
    (1..).map(|n| dir.join(format!("{stem} ({n}){ext}"))).find(|p| !p.exists()).unwrap()
}

// ── Commands of the app's own pages (ui/) ─────────────────────────────────────

#[tauri::command]
async fn app_state(app: AppHandle, state: State<'_, App>) -> Result<Value, String> {
    let config = state.config();
    let status = state.status.lock().unwrap().clone();
    let net = state.net.lock().await;
    Ok(json!({
        "set_up": config.is_set_up(),
        "nas": config.nas_name, "user": config.user, "link": config.link.is_some(),
        "use_window": config.use_window, "use_drive": config.use_drive,
        "connected": config.connected, "autostart": config.autostart,
        "hub_url": net.hub.as_ref().map(Proxy::url).unwrap_or_else(|| format!("http://127.0.0.1:{}", config.hub_port)),
        "dav_url": net.dav.as_ref().map(Proxy::url).unwrap_or_else(|| format!("http://127.0.0.1:{}", config.dav_port)),
        "status": status, "line": status_line(&config, &status),
        "platform": alvaos_core::platform(), "drive_supported": drive::supported(),
        "version": alvaos_core::VERSION, "computer": DeviceInfo::this_computer().name,
        "sync": syncing::state_json(&app),
    }))
}

#[tauri::command]
fn parse_link(text: String) -> Result<Value, String> {
    let link = PairLink::parse(&text).ok_or("That is not a link of AlvaOS. Copy it in the Hub: Devices › Connect a computer.")?;
    Ok(json!({"nas": link.nas, "user": link.user, "away": link.link.is_some(), "addresses": link.addresses}))
}

#[derive(Deserialize)]
#[serde(tag = "how", rename_all = "lowercase")]
enum Connect {
    Link { text: String },
    Code { code: String, address: String },
    Password { address: String, user: String, password: String, code: String },
}

/// Connects this computer: trades the link, the code, or name and password for a session of
/// its own, and tells the NAS its Link key.
#[tauri::command]
async fn connect(app: AppHandle, state: State<'_, App>, request: Connect) -> Result<Value, String> {
    let device = DeviceInfo::this_computer();
    let key = state.key()?;
    let public = link::public_of(&key);
    let (paired, link_id, addresses) = match request {
        Connect::Link { text: pasted } => {
            let pair = PairLink::parse(&pasted).ok_or("That is not a link of AlvaOS. Copy it in the Hub: Devices › Connect a computer.")?;
            let mut result = Err(String::new());
            if let Some(nas) = &pair.link {
                // Through Link: works at home and away, and the NAS learns this computer's key at once.
                let link = Arc::new(Link::start(&key, direct_only()).await.map_err(text)?);
                result = link.pair(nas, &pair.code, &device, &hints()).await.map_err(text);
                if result.is_ok() {
                    state.net.lock().await.link = Some(link);
                } else {
                    link.close().await;
                }
            }
            match result {
                Ok(paired) => (paired, pair.link.clone(), pair.addresses.clone()),
                Err(first) => {
                    // At home: the address of the Hub, if the link has one.
                    let address = pair.addresses.first().cloned().ok_or_else(|| first.clone())?;
                    let paired = Hub::new(&address).pair(&pair.code, &device).await.map_err(|e| if first.is_empty() { text(e) } else { first.clone() })?;
                    let link_id = Hub::new(&address).register_link(&paired.token, &public).await.ok().filter(|l| pairlink::is_link_address(l));
                    (paired, link_id.or(pair.link.clone()), pair.addresses.clone())
                }
            }
        }
        Connect::Code { code, address } => {
            let pair = PairLink::typed(&code, &address).ok_or("Type the code of the Hub (8 letters and digits) and the address of the NAS.")?;
            let hub = Hub::new(&pair.addresses[0]);
            let paired = hub.pair(&pair.code, &device).await.map_err(text)?;
            let link_id = hub.register_link(&paired.token, &public).await.ok().filter(|l| pairlink::is_link_address(l));
            (paired, link_id, pair.addresses)
        }
        Connect::Password { address, user, password, code } => {
            let address = pairlink::address(&address);
            if address.is_empty() {
                return Err("Type the address of the NAS, as in the browser (like 192.168.1.20:8090).".into());
            }
            let hub = Hub::new(&address);
            let token = hub.sign_in(user.trim(), &password, code.trim(), &device).await.map_err(|e| {
                if e.is::<NeedsCode>() { "needs_code".to_string() } else { text(e) }
            })?;
            let me = hub.me(&token).await.unwrap_or(Value::Null);
            let link_id = hub.register_link(&token, &public).await.ok().filter(|l| pairlink::is_link_address(l));
            let paired = Paired {
                user: me["user"].as_str().unwrap_or(user.trim()).to_string(),
                token,
                device: String::new(),
                nas_name: me["nas_name"].as_str().unwrap_or("").to_string(),
            };
            (paired, link_id, vec![address])
        }
    };
    state.store.set_secret(SESSION, &paired.token).map_err(text)?;
    let mut config = state.config();
    config.user = paired.user.clone();
    config.nas_name = paired.nas_name.clone();
    config.device_id = paired.device.clone();
    config.link = link_id;
    config.addresses = addresses;
    config.connected = true;
    state.save(config)?;
    state.refused.store(0, Ordering::Relaxed);
    go_online(&app).await?;
    // The name of the NAS, when the pairing did not say it.
    if state.config().nas_name.is_empty() {
        if let (Some(url), Some(token)) = (state.hub_url().await, state.token()) {
            if let Ok(me) = Hub::new(&url).me(&token).await {
                let mut config = state.config();
                config.nas_name = me["nas_name"].as_str().unwrap_or("").to_string();
                state.save(config)?;
            }
        }
    }
    app_state(app, state).await
}

#[derive(Deserialize)]
struct Choice {
    use_window: bool,
    use_drive: bool,
    autostart: bool,
}

/// The building blocks (setup and settings).
#[tauri::command]
async fn choose(app: AppHandle, state: State<'_, App>, choice: Choice) -> Result<Value, String> {
    let mut config = state.config();
    config.use_window = choice.use_window;
    config.use_drive = choice.use_drive && drive::supported();
    config.autostart = choice.autostart;
    state.save(config.clone())?;
    let autolaunch = app.autolaunch();
    let _ = if config.autostart { autolaunch.enable() } else { autolaunch.disable() };
    let mounted = state.status.lock().unwrap().drive;
    if config.use_drive != mounted && state.status.lock().unwrap().state == "ok" {
        let _ = set_drive(&app, config.use_drive).await;
    }
    app_state(app, state).await
}

#[tauri::command]
async fn open_hub(app: AppHandle) -> Result<(), String> {
    show_hub(&app).await
}

#[tauri::command]
async fn open_browser(app: AppHandle) -> Result<(), String> {
    let url = signed_in_url(&app).await?;
    tauri_plugin_opener::open_url(url, None::<&str>).map_err(text)
}

async fn set_drive(app: &AppHandle, on: bool) -> Result<String, String> {
    let me = app.state::<App>();
    let config = me.config();
    let result = if on {
        let token = me.token().ok_or("This computer is not signed in.")?;
        drive::mount(config.dav_port, &config.user, &token).await.map_err(text)
    } else {
        drive::unmount(config.dav_port, &config.user).await.map(|_| String::new()).map_err(text)
    };
    if result.is_ok() {
        me.status.lock().unwrap().drive = on;
        paint_tray(app);
    }
    result
}

#[tauri::command]
async fn drive(app: AppHandle, on: bool) -> Result<String, String> {
    set_drive(&app, on).await
}

#[tauri::command]
async fn set_connected(app: AppHandle, state: State<'_, App>, on: bool) -> Result<Value, String> {
    let mut config = state.config();
    config.connected = on;
    state.save(config)?;
    if on {
        go_online(&app).await?;
    } else {
        if state.status.lock().unwrap().drive {
            let _ = set_drive(&app, false).await;
        }
        go_offline(&app).await;
        set_status(&app, "off", "", 0, "");
    }
    app_state(app, state).await
}

/// Signs this computer out: on the NAS (Devices) and here.
#[tauri::command]
async fn sign_out(app: AppHandle, state: State<'_, App>) -> Result<Value, String> {
    let config = state.config();
    if let (Some(url), Some(token)) = (state.hub_url().await, state.token()) {
        if !config.device_id.is_empty() {
            let _ = Hub::new(&url).remove_device(&token, &config.device_id).await;
        }
    }
    forget(&app).await;
    set_status(&app, "setup", "", 0, "");
    app_state(app, state).await
}

// ── The tray ──────────────────────────────────────────────────────────────────

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", "Starting…", false, None::<&str>)?;
    let sync = MenuItem::with_id(app, "sync-status", "No sync folder yet", false, None::<&str>)?;
    let sync_open = MenuItem::with_id(app, "sync-open", "Open the sync folder", true, None::<&str>)?;
    let sync_now = MenuItem::with_id(app, "sync-now", "Sync now", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "Show AlvaOS", true, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Open the Hub apps", true, None::<&str>)?;
    let browser = MenuItem::with_id(app, "browser", "Open in the browser", true, None::<&str>)?;
    let drive = MenuItem::with_id(app, "drive", "Show Files as a drive", false, None::<&str>)?;
    let connected = CheckMenuItem::with_id(app, "connected", "Connected to the NAS", true, true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit AlvaOS", true, None::<&str>)?;
    let separator = || PredefinedMenuItem::separator(app);
    // "Show AlvaOS" first: on Linux every click on the icon opens this menu (AppIndicator has no clicks).
    let (s0, s1, s2, s3) = (separator()?, separator()?, separator()?, separator()?);
    let mut items: Vec<&dyn tauri::menu::IsMenuItem<Wry>> = vec![&show, &s0, &status, &sync, &s1, &open, &browser, &sync_open, &sync_now];
    if drive::supported() {
        items.push(&drive);
    }
    items.extend([&s2 as &dyn tauri::menu::IsMenuItem<Wry>, &connected, &s3, &quit]);
    let menu = Menu::with_items(app, &items)?;
    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?;
    TrayIconBuilder::with_id("main")
        .icon(icon)
        .tooltip("AlvaOS")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| {
            let app = app.clone();
            let id = event.id().as_ref().to_string();
            tauri::async_runtime::spawn(async move { on_menu(&app, &id).await });
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                let app = tray.app_handle().clone();
                show_main(&app);
            }
        })
        .build(app)?;
    *app.state::<App>().tray.lock().unwrap() = Some(TrayItems { status, sync, connected, drive });
    Ok(())
}

async fn on_menu(app: &AppHandle, id: &str) {
    let me = app.state::<App>();
    let set_up = me.config().is_set_up();
    match id {
        "open" if set_up && me.config().use_window => {
            if show_hub(app).await.is_err() {
                show_main(app);
            }
        }
        "open" | "show" => show_main(app),
        "browser" if set_up => {
            if let Ok(url) = signed_in_url(app).await {
                let _ = tauri_plugin_opener::open_url(url, None::<&str>);
            } else {
                show_main(app);
            }
        }
        "sync-open" => {
            let _ = syncing::open_sync(app.state::<App>(), None).await;
        }
        "sync-now" => {
            syncing::round(app).await;
        }
        "drive" => {
            let on = !me.status.lock().unwrap().drive;
            let _ = set_drive(app, on).await;
        }
        "connected" => {
            let on = !me.config().connected;
            let _ = set_connected(app.clone(), app.state::<App>(), on).await;
        }
        "quit" => {
            if me.status.lock().unwrap().drive {
                let config = me.config();
                let _ = drive::unmount(config.dav_port, &config.user).await;
            }
            go_offline(app).await;
            app.exit(0);
        }
        _ => show_main(app),
    }
    paint_tray(app);
}

fn main() {
    let hidden = std::env::args().any(|a| a == "--hidden");
    let store = Store::open();
    let config = store.load();
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // Started again (from the launcher, a menu, a shortcut): the interface, not just the tray.
            let app = app.clone();
            tauri::async_runtime::spawn(async move { open_interface(&app).await });
        }))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--hidden"])))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(App {
            store,
            config: Mutex::new(config),
            net: tokio::sync::Mutex::new(Net::default()),
            status: Mutex::new(Status { state: "setup".into(), ..Status::default() }),
            tray: Mutex::new(None),
            refused: AtomicU32::new(0),
            syncing: syncing::Syncing::default(),
        })
        .invoke_handler(tauri::generate_handler![
            app_state, parse_link, connect, choose, open_hub, open_browser, drive, set_connected, sign_out,
            syncing::remote_shares, syncing::remote_folders, syncing::add_sync, syncing::remove_sync, syncing::sync_now,
            syncing::sync_answer, syncing::pause_sync, syncing::open_sync, syncing::pick_local, syncing::set_exclude
        ])
        .setup(move |app| {
            build_tray(app.handle())?;
            let handle = app.handle().clone();
            let config = app.state::<App>().config();
            // Started by the person (not quietly at login): the interface opens once the NAS is there.
            let open_now = !hidden && config.is_set_up() && config.use_window && config.connected;
            if !hidden && !open_now || !config.is_set_up() {
                show_main(&handle);
            }
            syncing::watch(app.handle());
            tauri::async_runtime::spawn(syncing::run(app.handle().clone()));
            tauri::async_runtime::spawn(async move {
                let config = handle.state::<App>().config();
                if config.is_set_up() && config.connected {
                    let online = go_online(&handle).await.is_ok();
                    if open_now {
                        open_interface(&handle).await;
                    }
                    if online && config.use_drive && handle.state::<App>().status.lock().unwrap().state == "ok" {
                        let _ = set_drive(&handle, true).await;
                    }
                } else {
                    check(&handle).await;
                }
                loop {
                    tokio::time::sleep(Duration::from_secs(CHECK_SECONDS)).await;
                    check(&handle).await;
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing a window hides it: the app goes on in the tray.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" || window.label() == "hub" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("AlvaOS could not start")
        .run(|_, event| {
            if let RunEvent::ExitRequested { api, code, .. } = event {
                if code.is_none() {
                    api.prevent_exit();   // the last window closed: the tray stays
                }
            }
        });
}
