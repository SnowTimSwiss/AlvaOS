//! What the app keeps. Settings in `settings.json` in the app's folder (AppData, ~/.config,
//! ~/Library/Application Support: `AlvaOS`). The two secrets, the session of this computer
//! and its Link key, go to the system's key store (Windows Credential Manager, macOS
//! Keychain, the Secret Service on Linux); only where there is none they are kept in a file
//! that only this user can read.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

pub const SESSION: &str = "session";
pub const LINK_KEY: &str = "link-key";
const SERVICE: &str = "AlvaOS";
const HUB_PORT: u16 = 18090;
const DAV_PORT: u16 = 18091;

/// A folder of the NAS kept on this computer (the sync folder).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SyncFolder {
    pub id: String,
    /// The share and the folder in it ('' for the whole share).
    pub share: String,
    pub folder: String,
    /// Where it is on this computer.
    pub local: PathBuf,
    /// A share the person may only read: it only comes down.
    pub read_only: bool,
    /// What is left out, one pattern per line ("Don't sync", see sync::Filter).
    #[serde(default)]
    pub exclude: Vec<String>,
    /// Files on demand: the folder shows everything on the NAS and fetches what is opened.
    #[serde(default)]
    pub on_demand: bool,
    /// What an on-demand folder always keeps here, one pattern per line (see sync::Filter).
    #[serde(default)]
    pub keep: Vec<String>,
}

impl SyncFolder {
    /// `Family › Photos`
    pub fn label(&self) -> String {
        if self.folder.is_empty() { self.share.clone() } else { format!("{} › {}", self.share, self.folder.replace('/', " › ")) }
    }
}

/// Where sync folders go unless chosen otherwise: `~/AlvaOS`.
pub fn default_sync_root() -> PathBuf {
    dirs::home_dir().unwrap_or_else(|| PathBuf::from(".")).join("AlvaOS")
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Config {
    /// The NAS's name (as in the Hub).
    pub nas_name: String,
    /// The person this computer is signed in as.
    pub user: String,
    /// This computer in the Hub's Devices (to sign it out there too).
    pub device_id: String,
    /// The NAS's Link address; None when Link is off on the NAS (then at home only).
    pub link: Option<String>,
    /// Where the Hub answers at home.
    pub addresses: Vec<String>,
    /// The ports of the NAS on this computer (kept, so bookmarks and sign-ins stay valid).
    pub hub_port: u16,
    pub dav_port: u16,
    /// The building blocks chosen at setup; remote access is always there.
    pub use_window: bool,
    pub use_drive: bool,
    /// The switch in the tray: connected to the NAS.
    pub connected: bool,
    /// Start with the computer.
    pub autostart: bool,
    /// The sync folders, and whether syncing is paused.
    pub sync: Vec<SyncFolder>,
    pub sync_paused: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            nas_name: String::new(),
            user: String::new(),
            device_id: String::new(),
            link: None,
            addresses: Vec::new(),
            hub_port: HUB_PORT,
            dav_port: DAV_PORT,
            use_window: true,
            use_drive: false,
            connected: true,
            autostart: true,
            sync: Vec::new(),
            sync_paused: false,
        }
    }
}

impl Config {
    pub fn is_set_up(&self) -> bool {
        !self.user.is_empty() && (self.link.is_some() || !self.addresses.is_empty())
    }

    /// `host:port` of the Hub at home, for a NAS without Link (plain HTTP on the home network).
    pub fn home(&self) -> Option<String> {
        self.addresses.iter().find_map(|a| {
            let url = url::Url::parse(a).ok()?;
            let host = url.host_str()?;
            (url.scheme() == "http").then(|| format!("{host}:{}", url.port().unwrap_or(80)))
        })
    }
}

pub struct Store {
    dir: PathBuf,
    keyring: bool,
}

impl Store {
    /// The app's folder; `ALVAOS_DESKTOP_DIR` for tests and portable use (secrets then in a file).
    pub fn open() -> Self {
        match std::env::var_os("ALVAOS_DESKTOP_DIR") {
            Some(dir) => Self::at(PathBuf::from(dir), false),
            None => Self::at(dirs::config_dir().unwrap_or_else(|| PathBuf::from(".")).join("AlvaOS"), true),
        }
    }

    pub fn at(dir: PathBuf, keyring: bool) -> Self {
        Self { dir, keyring }
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// Where a sync folder keeps how both sides were after its last round.
    pub fn sync_state(&self, id: &str) -> PathBuf {
        self.dir.join("sync").join(format!("{id}.json"))
    }

    pub fn load(&self) -> Config {
        std::fs::read_to_string(self.dir.join("settings.json"))
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, config: &Config) -> Result<()> {
        write_private(&self.dir, "settings.json", &serde_json::to_string_pretty(config)?)
    }

    pub fn secret(&self, name: &str) -> Option<String> {
        if self.keyring {
            if let Ok(value) = keyring::Entry::new(SERVICE, name).and_then(|e| e.get_password()) {
                return Some(value);
            }
        }
        self.file_secrets().get(name).and_then(|v| v.as_str()).map(str::to_string)
    }

    pub fn set_secret(&self, name: &str, value: &str) -> Result<()> {
        if self.keyring && keyring::Entry::new(SERVICE, name).and_then(|e| e.set_password(value)).is_ok() {
            self.drop_file_secret(name)?;
            return Ok(());
        }
        let mut all = self.file_secrets();
        all.insert(name.into(), value.into());
        write_private(&self.dir, "secrets.json", &serde_json::to_string(&all)?)
    }

    /// Signing out on this computer: the session goes, the settings go back to the start.
    /// The Link key stays (the NAS forgets it when the device is removed there).
    pub fn forget(&self) -> Result<()> {
        if self.keyring {
            if let Ok(entry) = keyring::Entry::new(SERVICE, SESSION) {
                let _ = entry.delete_credential();
            }
        }
        self.drop_file_secret(SESSION)?;
        let keep = self.load();
        self.save(&Config { hub_port: keep.hub_port, dav_port: keep.dav_port, ..Config::default() })
    }

    fn file_secrets(&self) -> serde_json::Map<String, serde_json::Value> {
        std::fs::read_to_string(self.dir.join("secrets.json"))
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default()
    }

    fn drop_file_secret(&self, name: &str) -> Result<()> {
        let mut all = self.file_secrets();
        if all.remove(name).is_some() {
            write_private(&self.dir, "secrets.json", &serde_json::to_string(&all)?)?;
        }
        Ok(())
    }
}

/// Written whole (never half), readable only by this user.
fn write_private(dir: &Path, name: &str, text: &str) -> Result<()> {
    std::fs::create_dir_all(dir).with_context(|| format!("cannot make {}", dir.display()))?;
    let tmp = dir.join(format!(".{name}.tmp"));
    {
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        use std::io::Write;
        let mut file = options.open(&tmp)?;
        file.write_all(text.as_bytes())?;
        file.sync_all()?;
    }
    std::fs::rename(&tmp, dir.join(name))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (Store, PathBuf) {
        let dir = std::env::temp_dir().join(format!("alvaos-config-{}", crate::link::hex(&crate::link::new_key()[..6])));
        (Store::at(dir.clone(), false), dir)
    }

    #[test]
    fn settings_and_secrets_are_kept_and_forgotten() {
        let (store, dir) = store();
        assert!(!store.load().is_set_up());
        let config = Config { user: "anna".into(), link: Some("ab".repeat(32)), hub_port: 18555, ..Config::default() };
        store.save(&config).unwrap();
        store.set_secret(SESSION, "tok").unwrap();
        store.set_secret(LINK_KEY, "key").unwrap();
        assert_eq!(store.load(), config);
        assert!(store.load().is_set_up());
        assert_eq!(store.secret(SESSION).as_deref(), Some("tok"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(dir.join("secrets.json")).unwrap().permissions().mode() & 0o777, 0o600);
        }
        store.forget().unwrap();
        assert_eq!(store.secret(SESSION), None);
        assert_eq!(store.secret(LINK_KEY).as_deref(), Some("key"));       // the computer stays the same computer
        assert_eq!((store.load().user.as_str(), store.load().hub_port), ("", 18555));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn the_home_address_is_only_plain_http() {
        let mut config = Config { addresses: vec!["https://nas.example.ch".into()], ..Config::default() };
        assert_eq!(config.home(), None);
        config.addresses.push("http://192.168.1.20:8090".into());
        assert_eq!(config.home().as_deref(), Some("192.168.1.20:8090"));
    }
}
