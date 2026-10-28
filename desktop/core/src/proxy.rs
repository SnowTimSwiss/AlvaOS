//! The NAS as addresses on this computer: `http://127.0.0.1:<port>` for the Hub (and CalDAV,
//! CardDAV), another port for Files over WebDAV. Every connection made to them is carried
//! through AlvaOS Link to the NAS, so the app's window, the browser, a calendar program or the
//! file manager reach the NAS from anywhere as if it were at home.
//!
//! Only this computer can connect (the loopback network), and the NAS still asks everyone to
//! sign in: the address is a way to the NAS, not a key to it. The ports stay the same between
//! starts, so bookmarks and the browser's sign-in keep working.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use anyhow::{Context, Result};
use tokio::net::{TcpListener, TcpStream};
use tokio::task::JoinHandle;

use crate::link::Link;

/// How a connection reaches the NAS.
#[derive(Clone)]
pub enum Way {
    /// Through AlvaOS Link: the NAS's Link address, the service, address hints (tests).
    Link { link: Arc<Link>, nas: String, service: String, hints: Vec<String> },
    /// Straight to an address on the home network (`192.168.1.20:8090`), when the NAS has Link off.
    Direct(String),
}

#[derive(Default)]
pub struct Stats {
    pub open: AtomicU64,
    pub total: AtomicU64,
    pub failed: AtomicU64,
    pub last_error: Mutex<String>,
}

pub struct Proxy {
    pub port: u16,
    pub stats: Arc<Stats>,
    task: JoinHandle<()>,
}

impl Proxy {
    /// Listens on `127.0.0.1:<preferred>`; when that port is taken (or 0), on any free one.
    pub async fn start(preferred: u16, way: Way) -> Result<Self> {
        let listener = match TcpListener::bind(("127.0.0.1", preferred)).await {
            Ok(l) => l,
            Err(_) => TcpListener::bind(("127.0.0.1", 0)).await.context("no free port on this computer")?,
        };
        let port = listener.local_addr()?.port();
        let stats = Arc::new(Stats::default());
        let task = tokio::spawn(serve(listener, way, stats.clone()));
        Ok(Self { port, stats, task })
    }

    /// Exactly this port: the Hub builds the address of an app from it, so another one is no use.
    pub async fn start_exact(port: u16, way: Way) -> Result<Self> {
        let listener = TcpListener::bind(("127.0.0.1", port)).await.with_context(|| format!("port {port} is taken"))?;
        let stats = Arc::new(Stats::default());
        let task = tokio::spawn(serve(listener, way, stats.clone()));
        Ok(Self { port, stats, task })
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    pub fn stop(&self) {
        self.task.abort();
    }
}

impl Drop for Proxy {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn serve(listener: TcpListener, way: Way, stats: Arc<Stats>) {
    loop {
        let Ok((socket, _)) = listener.accept().await else { continue };
        let way = way.clone();
        let stats = stats.clone();
        tokio::spawn(async move {
            stats.open.fetch_add(1, Ordering::Relaxed);
            stats.total.fetch_add(1, Ordering::Relaxed);
            if let Err(e) = carry(socket, &way).await {
                stats.failed.fetch_add(1, Ordering::Relaxed);
                if let Ok(mut last) = stats.last_error.lock() {
                    *last = e.to_string();
                }
            }
            stats.open.fetch_sub(1, Ordering::Relaxed);
        });
    }
}

/// Both ways at once, until both sides are done.
async fn carry(mut socket: TcpStream, way: &Way) -> Result<()> {
    let _ = socket.set_nodelay(true);
    match way {
        Way::Link { link, nas, service, hints } => {
            let (send, recv) = link.open(nas, service, hints).await?;
            let mut stream = tokio::io::join(recv, send);
            // Ends when both are done; a reset by either side is the end of this one connection.
            let _ = tokio::io::copy_bidirectional(&mut socket, &mut stream).await;
        }
        Way::Direct(address) => {
            let mut nas = TcpStream::connect(address).await.with_context(|| format!("{address} does not answer"))?;
            let _ = tokio::io::copy_bidirectional(&mut socket, &mut nas).await;
        }
    }
    Ok(())
}

/// The ports the NAS's Hub gives to apps that open in its window: the same number as the NAS's proxy
/// for each (`embed_port` in the Hub's `me`), counted from 20001 up.
pub const EMBED_FIRST: u16 = 20001;
const MAX_APPS: usize = 32;

/// Which of the Hub's apps open in its window, from the Hub's `me` answer.
pub fn embed_ports(me: &serde_json::Value) -> Vec<u16> {
    let mut ports: Vec<u16> = me["hub"]["store"]
        .as_array()
        .map(|apps| apps.iter().filter_map(|a| a["embed_port"].as_u64()).filter_map(|p| u16::try_from(p).ok()).collect())
        .unwrap_or_default();
    ports.retain(|p| *p >= EMBED_FIRST);
    ports.sort_unstable();
    ports.dedup();
    ports.truncate(MAX_APPS);
    ports
}

/// A local port for each app in the Hub's window, carried through Link like the Hub's own.
#[derive(Default)]
pub struct AppPorts {
    proxies: HashMap<u16, Proxy>,
}

impl AppPorts {
    /// Listen on exactly `wanted`: new ones start, those no longer wanted stop. A port that is taken
    /// on this computer is left out (the app then opens in a new tab).
    pub async fn sync(&mut self, wanted: &[u16], way: impl Fn(u16) -> Way) {
        self.proxies.retain(|port, _| wanted.contains(port));
        let missing: Vec<u16> = wanted.iter().copied().filter(|port| !self.proxies.contains_key(port)).collect();
        for port in missing {
            if let Ok(proxy) = Proxy::start_exact(port, way(port)).await {
                self.proxies.insert(port, proxy);
            }
        }
    }

    pub fn ports(&self) -> Vec<u16> {
        let mut ports: Vec<u16> = self.proxies.keys().copied().collect();
        ports.sort_unstable();
        ports
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    async fn echo() -> (u16, tokio::task::JoinHandle<()>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let task = tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else { return };
                tokio::spawn(async move {
                    let mut buf = [0u8; 64];
                    while let Ok(n) = socket.read(&mut buf).await {
                        if n == 0 || socket.write_all(&buf[..n].to_ascii_uppercase()).await.is_err() {
                            break;
                        }
                    }
                });
            }
        });
        (port, task)
    }

    async fn free_port() -> u16 {
        let l = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        l.local_addr().unwrap().port()
    }

    #[test]
    fn the_hub_names_the_ports_and_nothing_odd_is_listened_on() {
        let me = json!({"hub": {"store": [
            {"id": "jellyfin", "embed_port": 28096}, {"id": "pihole"}, {"id": "x", "embed_port": 22},
            {"id": "y", "embed_port": 70000}, {"id": "z", "embed_port": 28096}, {"id": "w", "embed_port": 23001}]}});
        assert_eq!(embed_ports(&me), vec![23001, 28096]);
        assert!(embed_ports(&json!({})).is_empty());
        assert!(embed_ports(&json!({"hub": {"store": "no"}})).is_empty());
    }

    #[tokio::test]
    async fn apps_get_their_port_here_until_they_are_no_longer_wanted() {
        let (nas, _echo) = echo().await;
        let (a, b) = (free_port().await, free_port().await);
        let mut apps = AppPorts::default();
        apps.sync(&[a, b], |_| Way::Direct(format!("127.0.0.1:{nas}"))).await;
        let mut expected = vec![a, b];
        expected.sort_unstable();
        assert_eq!(apps.ports(), expected);
        let mut conn = TcpStream::connect(("127.0.0.1", a)).await.unwrap();
        conn.write_all(b"hello").await.unwrap();
        let mut got = [0u8; 5];
        conn.read_exact(&mut got).await.unwrap();
        assert_eq!(&got, b"HELLO");
        // The app is hidden in the Hub: its port goes, the other stays.
        apps.sync(&[b], |_| Way::Direct(format!("127.0.0.1:{nas}"))).await;
        assert_eq!(apps.ports(), vec![b]);
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        assert!(TcpStream::connect(("127.0.0.1", a)).await.is_err());
    }

    #[tokio::test]
    async fn a_port_that_is_taken_is_left_out_not_fatal() {
        let busy = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let port = busy.local_addr().unwrap().port();
        let mut apps = AppPorts::default();
        apps.sync(&[port], |_| Way::Direct("127.0.0.1:1".into())).await;
        assert!(apps.ports().is_empty());
    }
}
