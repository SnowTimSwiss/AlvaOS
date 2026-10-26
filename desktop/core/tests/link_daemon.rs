//! The computer's Link client against the real daemon of the NAS (backend/link_daemon.py),
//! the Hub and WebDAV stood in for by small web servers. Needs python3 with `pip install iroh`;
//! skipped (with a note) where there is none.

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use alvaos_core::hub::Hub;
use alvaos_core::link::{new_key, Link};
use alvaos_core::proxy::{Proxy, Way};
use alvaos_core::DeviceInfo;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

type Seen = Arc<Mutex<Vec<String>>>;

/// A web server that answers every request with JSON: who asked, and what.
async fn fake(name: &'static str, seen: Seen) -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        loop {
            let Ok((mut socket, from)) = listener.accept().await else { continue };
            let seen = seen.clone();
            tokio::spawn(async move {
                let mut buf = Vec::new();
                let mut chunk = [0u8; 4096];
                // Head, then as much body as Content-Length says.
                loop {
                    let n = socket.read(&mut chunk).await.unwrap_or(0);
                    if n == 0 { return; }
                    buf.extend_from_slice(&chunk[..n]);
                    if let Some(end) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                        let head = String::from_utf8_lossy(&buf[..end]).to_string();
                        let length = head.lines().find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0))).unwrap_or(0);
                        while buf.len() < end + 4 + length {
                            let n = socket.read(&mut chunk).await.unwrap_or(0);
                            if n == 0 { break; }
                            buf.extend_from_slice(&chunk[..n]);
                        }
                        let body = String::from_utf8_lossy(&buf[end + 4..]).to_string();
                        let path = head.split_whitespace().nth(1).unwrap_or("").to_string();
                        seen.lock().unwrap().push(format!("{name} {} {path} {body}", from.ip()));
                        let answer = if path == "/api/devices/pair" {
                            if body.contains("ABCD1234") {
                                r#"{"success":true,"user":"anna","token":"tok","device":"dev1","nas_name":"cygnus"}"#.to_string()
                            } else {
                                r#"{"error":"This code is not valid"}"#.to_string()
                            }
                        } else {
                            format!(r#"{{"hello":"from the {name}","path":"{path}","you":"{}"}}"#, from.ip())
                        };
                        let reply = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{answer}", answer.len());
                        let _ = socket.write_all(reply.as_bytes()).await;
                        let _ = socket.shutdown().await;
                        return;
                    }
                }
            });
        }
    });
    port
}

struct Daemon(Child);

impl Drop for Daemon {
    fn drop(&mut self) {
        let _ = self.0.kill();
    }
}

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
}

fn start_daemon(hub: u16, dav: u16) -> Option<(Daemon, String, Vec<String>)> {
    let ok = Command::new("python3").args(["-c", "import iroh"]).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false);
    if !ok {
        eprintln!("skipped: python3 with iroh is needed for this test");
        return None;
    }
    let backend = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../backend");
    let dir = std::env::temp_dir().join(format!("alvaos-link-{}", alvaos_core::link::hex(&new_key()[..6])));
    let mut child = Command::new("python3")
        .arg(backend.join("link_daemon.py"))
        .current_dir(&backend)
        .env("ALVAOS_LINK_DIR", &dir)
        .env("ALVAOS_LINK_TEST", format!(r#"{{"services": {{"hub": {hub}, "dav": {dav}}}, "control_port": {}}}"#, free_port()))
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("python3");
    let stdout = child.stdout.take().unwrap();
    let mut lines = BufReader::new(stdout).lines();
    let first = loop {
        let line = lines.next().expect("the daemon did not start").unwrap();
        if line.starts_with('{') { break line; }
    };
    let found: serde_json::Value = serde_json::from_str(&first).unwrap();
    let id = found["node_id"].as_str().unwrap().to_string();
    let hints = found["addresses"].as_array().unwrap().iter().map(|a| a.as_str().unwrap().to_string()).collect();
    Some((Daemon(child), id, hints))
}

async fn get(url: &str) -> Result<String, String> {
    let client = reqwest::Client::builder().timeout(Duration::from_secs(10)).build().unwrap();
    match client.get(url).send().await {
        Ok(res) => res.text().await.map_err(|e| e.to_string()),
        Err(e) => Err(e.to_string()),
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_computer_pairs_and_then_reaches_the_hub_and_files_through_its_own_addresses() {
    let seen: Seen = Arc::new(Mutex::new(Vec::new()));
    let hub_port = fake("hub", seen.clone()).await;
    let dav_port = fake("dav", seen.clone()).await;
    let Some((_daemon, nas, hints)) = start_daemon(hub_port, dav_port) else { return };

    let key = new_key();
    let link = Arc::new(Link::start(&key, true).await.unwrap());
    let way = |service: &str| Way::Link { link: link.clone(), nas: nas.clone(), service: service.into(), hints: hints.clone() };
    let hub = Proxy::start(0, way("hub")).await.unwrap();
    let dav = Proxy::start(0, way("dav")).await.unwrap();

    // Not paired yet: the Hub is not open to a stranger, only pairing is.
    assert!(get(&format!("{}/api/me", hub.url())).await.is_err(), "a stranger must not reach the Hub");
    assert!(link.ping(&nas, &hints).await.is_err());

    // A wrong code pairs nothing; the right one trades for this computer's session.
    let computer = DeviceInfo { name: "Laptop".into(), model: String::new(), platform: "linux".into(), app_version: "test".into() };
    let wrong = link.pair(&nas, "WRONG000", &computer, &hints).await.unwrap_err();
    assert!(wrong.to_string().contains("not valid"), "{wrong}");
    let paired = link.pair(&nas, "ABCD1234", &computer, &hints).await.unwrap();
    assert_eq!((paired.user.as_str(), paired.token.as_str()), ("anna", "tok"));
    assert!(seen.lock().unwrap().iter().any(|s| s.starts_with("hub 127.95.0.1 /api/devices/pair") && s.contains("\"linux\"")));

    // Now the Hub and Files are ordinary web addresses on this computer, seen from its own address on the NAS.
    let me = get(&format!("{}/api/me", hub.url())).await.unwrap();
    assert!(me.contains("from the hub") && me.contains("/api/me"), "{me}");
    assert!(me.contains(r#""you":"127.95."#), "{me}");
    let files = get(&format!("{}/Family/", dav.url())).await.unwrap();
    assert!(files.contains("from the dav") && files.contains("/Family/"), "{files}");
    // The app's own calls go the same way.
    let answer = Hub::new(&hub.url()).me("tok").await.unwrap();
    assert_eq!(answer["hello"], "from the hub");
    // Many at once (a page loads many things).
    let many = futures_all((0..12).map(|i| get_owned(format!("{}/p{i}", hub.url())))).await;
    assert!(many.iter().all(|r| r.as_ref().map(|t| t.contains("from the hub")).unwrap_or(false)), "{many:?}");

    let route = link.ping(&nas, &hints).await.unwrap();
    assert_eq!(route.kind, "direct");
    assert_eq!(alvaos_core::link::public_of(&key), link.id());
    link.close().await;
}

async fn get_owned(url: String) -> Result<String, String> {
    get(&url).await
}

async fn futures_all<F: std::future::Future<Output = Result<String, String>> + Send + 'static>(
    jobs: impl Iterator<Item = F>,
) -> Vec<Result<String, String>> {
    let handles: Vec<_> = jobs.map(tokio::spawn).collect();
    let mut out = Vec::new();
    for h in handles {
        out.push(h.await.unwrap());
    }
    out
}
