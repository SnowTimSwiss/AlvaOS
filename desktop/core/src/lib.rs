//! The AlvaOS computer app without its window (docs/DESKTOP.md).
//!
//! - [`pairlink`]: the link of the Hub (Devices › Connect a computer) and typed codes.
//! - [`link`]: AlvaOS Link, the encrypted way to the NAS from anywhere (iroh, docs/LINK.md).
//! - [`proxy`]: the NAS as addresses on this computer (`127.0.0.1:<port>`) for the app's window,
//!   the browser and other programs; every connection goes through Link.
//! - [`hub`]: the few calls to the Hub the app makes itself.
//! - [`config`]: what is kept: settings in a file, the keys in the system's key store.
//! - [`drive`]: Files as a drive of the computer (WebDAV).

pub mod config;
pub mod drive;
pub mod hub;
pub mod link;
pub mod pairlink;
pub mod proxy;
pub mod sync;

/// The app's version (the version of AlvaOS).
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// What the NAS is told about this computer (Hub › Devices).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq)]
pub struct DeviceInfo {
    pub name: String,
    pub model: String,
    pub platform: String,
    pub app_version: String,
}

impl DeviceInfo {
    /// This computer: its name, and the system it runs.
    pub fn this_computer() -> Self {
        let name = std::env::var("COMPUTERNAME")
            .or_else(|_| std::env::var("HOSTNAME"))
            .ok()
            .filter(|n| !n.trim().is_empty())
            .or_else(|| std::fs::read_to_string("/etc/hostname").ok().map(|n| n.trim().to_string()))
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| "Computer".into());
        Self {
            name: name.chars().take(60).collect(),
            model: String::new(),
            platform: platform().into(),
            app_version: VERSION.into(),
        }
    }
}

/// `windows`, `linux` or `macos`, as the Hub shows it.
pub fn platform() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

/// What the NAS answers when a device is paired.
#[derive(Debug, Clone, serde::Deserialize, PartialEq)]
pub struct Paired {
    pub user: String,
    pub token: String,
    #[serde(default)]
    pub device: String,
    #[serde(default)]
    pub nas_name: String,
}
