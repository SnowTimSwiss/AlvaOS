//! AlvaOS Link on the computer: the way to the NAS from anywhere, without a router setting
//! (docs/LINK.md). The NAS has a key pair; its public key, 64 hex digits, is its address. This
//! computer has a key pair of its own, made once and kept in the system's key store. iroh
//! connects the two: directly through the routers if it can (at home on the home network),
//! else through a relay that only passes on encrypted packets. The NAS lets in only keys it
//! has paired.
//!
//! One QUIC stream per request; its first line names the service: `hub` (the Hub, also
//! CalDAV and CardDAV), `dav` (Files over WebDAV), `pair`, `ping`.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use iroh::endpoint::{presets, Connection, RecvStream, SendStream};
use iroh::{Endpoint, EndpointAddr, EndpointId, RelayMode, SecretKey, TransportAddr};
use tokio::sync::Mutex;

use crate::{DeviceInfo, Paired};

pub const ALPN: &[u8] = b"alvaos/link/1";

/// The NAS answers, but not to this computer: it was removed in the Hub (Devices), or never paired.
#[derive(Debug)]
pub struct NotPaired;

impl std::fmt::Display for NotPaired {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "The NAS does not know this computer (any more).")
    }
}

impl std::error::Error for NotPaired {}
const CONNECT_SECONDS: u64 = 30;

/// How the computer reaches the NAS right now.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Route {
    /// `direct` (through the routers, or on the home network) or `relay`.
    pub kind: String,
    /// Round trip in milliseconds.
    pub ms: u64,
}

pub struct Link {
    endpoint: Endpoint,
    connections: Mutex<HashMap<EndpointId, Connection>>,
}

/// A new key for this computer (kept by the caller, in the key store).
pub fn new_key() -> [u8; 32] {
    SecretKey::generate().to_bytes()
}

/// The public key of a secret key: this computer's Link address, 64 hex digits.
pub fn public_of(key: &[u8; 32]) -> String {
    hex(SecretKey::from_bytes(key).public().as_bytes())
}

impl Link {
    /// Starts the endpoint with this computer's key. `direct_only`: no relay and only the loopback
    /// network (tests against a NAS on the same machine).
    pub async fn start(key: &[u8; 32], direct_only: bool) -> Result<Self> {
        let secret = SecretKey::from_bytes(key);
        let endpoint = if direct_only {
            Endpoint::builder(presets::Minimal)
                .relay_mode(RelayMode::Disabled)
                .bind_addr("127.0.0.1:0")
                .map_err(|e| anyhow!("{e}"))?
                .secret_key(secret)
                .bind()
                .await
        } else {
            Endpoint::builder(presets::N0).secret_key(secret).bind().await
        }
        .map_err(|e| anyhow!("AlvaOS Link could not start: {e}"))?;
        Ok(Self { endpoint, connections: Mutex::new(HashMap::new()) })
    }

    /// This computer's Link address (what the NAS lists).
    pub fn id(&self) -> String {
        hex(self.endpoint.id().as_bytes())
    }

    async fn connection(&self, nas: &str, hints: &[String], fresh: bool) -> Result<Connection> {
        let id = endpoint_id(nas)?;
        let mut connections = self.connections.lock().await;
        if !fresh {
            if let Some(conn) = connections.get(&id) {
                if conn.close_reason().is_none() {
                    return Ok(conn.clone());
                }
            }
        }
        let addrs = hints.iter().filter_map(|h| h.parse::<SocketAddr>().ok()).map(TransportAddr::Ip);
        let addr = EndpointAddr::from_parts(id, addrs);
        let conn = tokio::time::timeout(Duration::from_secs(CONNECT_SECONDS), self.endpoint.connect(addr, ALPN))
            .await
            .map_err(|_| anyhow!("The NAS did not answer in time."))?
            .map_err(|e| anyhow!("The NAS cannot be reached: {e}"))?;
        connections.insert(id, conn.clone());
        Ok(conn)
    }

    /// A stream to a service of the NAS; its first line is already sent.
    pub async fn open(&self, nas: &str, service: &str, hints: &[String]) -> Result<(SendStream, RecvStream)> {
        let mut last = None;
        for fresh in [false, true] {
            let attempt = async {
                let conn = self.connection(nas, hints, fresh).await?;
                let (mut send, recv) = conn.open_bi().await.context("no stream")?;
                send.write_all(format!("{service}\n").as_bytes()).await.context("no stream")?;
                Ok::<_, anyhow::Error>((send, recv))
            };
            match attempt.await {
                Ok(pair) => return Ok(pair),
                Err(e) => last = Some(e),   // a stale connection: once more, new
            }
        }
        Err(last.unwrap_or_else(|| anyhow!("The NAS cannot be reached.")))
    }

    /// Trades the pairing code of the Hub for a session of this computer's own. The NAS then
    /// knows this computer's key: from now on it may come in from anywhere.
    pub async fn pair(&self, nas: &str, code: &str, device: &DeviceInfo, hints: &[String]) -> Result<Paired> {
        let (mut send, mut recv) = self.open(nas, "pair", hints).await?;
        let request = serde_json::json!({"op": "device", "code": code, "device": device});
        send.write_all(format!("{request}\n").as_bytes()).await?;
        send.finish()?;
        let answer = recv.read_to_end(256 * 1024).await.context("The NAS did not answer.")?;
        let line = String::from_utf8_lossy(&answer);
        let value: serde_json::Value =
            serde_json::from_str(line.lines().next().unwrap_or("")).context("The NAS gave an answer that is not understood.")?;
        if let Some(error) = value.get("error").and_then(|e| e.as_str()) {
            bail!("{error}");
        }
        serde_json::from_value(value).context("The NAS gave an answer that is not understood.")
    }

    /// Whether the NAS answers (only for a paired computer), and which way.
    pub async fn ping(&self, nas: &str, hints: &[String]) -> Result<Route> {
        let started = Instant::now();
        let (mut send, mut recv) = self.open(nas, "ping", hints).await?;
        send.finish()?;
        // The connection stands, so the NAS is there: a refused stream means it does not let this computer in.
        let answer = recv.read_to_end(16).await.map_err(|_| NotPaired)?;
        if String::from_utf8_lossy(&answer).trim() != "pong" {
            return Err(NotPaired.into());
        }
        let ms = started.elapsed().as_millis() as u64;
        Ok(self.route(nas).await.unwrap_or(Route { kind: "direct".into(), ms }))
    }

    /// Direct or through a relay, from the path the connection uses now.
    pub async fn route(&self, nas: &str) -> Option<Route> {
        let id = endpoint_id(nas).ok()?;
        let connections = self.connections.lock().await;
        let conn = connections.get(&id)?;
        let paths = conn.paths();
        let selected = paths.into_iter().find(|p| p.is_selected())?;
        Some(Route {
            kind: if selected.is_relay() { "relay" } else { "direct" }.into(),
            ms: selected.rtt().as_millis() as u64,
        })
    }

    /// Ends every connection (the switch in the tray, or quitting).
    pub async fn close(&self) {
        for (_, conn) in self.connections.lock().await.drain() {
            conn.close(0u32.into(), b"bye");
        }
        self.endpoint.close().await;
    }
}

fn endpoint_id(nas: &str) -> Result<EndpointId> {
    let bytes = unhex(nas).ok_or_else(|| anyhow!("A Link address is 64 hex digits."))?;
    EndpointId::from_bytes(&bytes).map_err(|_| anyhow!("That is not a Link address."))
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn unhex(text: &str) -> Option<[u8; 32]> {
    if text.len() != 64 {
        return None;
    }
    let mut out = [0u8; 32];
    for (i, chunk) in text.as_bytes().chunks(2).enumerate() {
        out[i] = u8::from_str_radix(std::str::from_utf8(chunk).ok()?, 16).ok()?;
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_gives_the_same_address_every_time() {
        let key = new_key();
        assert_eq!(public_of(&key), public_of(&key));
        assert_eq!(public_of(&key).len(), 64);
        assert_eq!(hex(&unhex(&"ab".repeat(32)).unwrap()), "ab".repeat(32));
        assert!(unhex("xyz").is_none());
    }
}
