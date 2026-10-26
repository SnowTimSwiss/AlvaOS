//! The NAS as addresses on this computer: `http://127.0.0.1:<port>` for the Hub (and CalDAV,
//! CardDAV), another port for Files over WebDAV. Every connection made to them is carried
//! through AlvaOS Link to the NAS, so the app's window, the browser, a calendar program or the
//! file manager reach the NAS from anywhere as if it were at home.
//!
//! Only this computer can connect (the loopback network), and the NAS still asks everyone to
//! sign in: the address is a way to the NAS, not a key to it. The ports stay the same between
//! starts, so bookmarks and the browser's sign-in keep working.

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
