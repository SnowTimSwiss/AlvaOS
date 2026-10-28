//! Files on demand against a real NAS: the Hub's folder tree and WebDAV (backend/files_server.py,
//! files_dav.py) with the real helper on a real share, through a real FUSE mount. Started by
//! scripts/dev/sync_against_nas.py (needs root for the helper); skipped without it, and
//! skipped where FUSE cannot be mounted.
#![cfg(target_os = "linux")]

use std::path::{Path, PathBuf};
use std::sync::Arc;

use alvaos_core::fusefs;
use alvaos_core::ondemand::{Cache, Volume};
use alvaos_core::sync::DavRemote;

fn env(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.is_empty())
}

fn names(dir: &Path) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(dir).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().to_string()).collect();
    v.sort();
    v
}

fn walk(dir: &Path) -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(entries) = std::fs::read_dir(dir) {
        for e in entries.flatten() {
            out.push(e.file_name().to_string_lossy().to_string());
            if e.path().is_dir() {
                out.extend(walk(&e.path()));
            }
        }
    }
    out
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_mounted_folder_against_the_real_hub_and_webdav() {
    let (Some(hub), Some(dav), Some(token), Some(share_dir)) =
        (env("ALVAOS_TEST_HUB"), env("ALVAOS_TEST_DAV"), env("ALVAOS_TEST_TOKEN"), env("ALVAOS_TEST_SHARE_DIR"))
    else {
        eprintln!("skipped: started by scripts/dev/sync_against_nas.py");
        return;
    };
    let nas_dir = PathBuf::from(share_dir);
    let top = std::env::temp_dir().join(format!("alvaos-od-nas-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&top);
    let (mountpoint, cache) = (top.join("mnt"), top.join("cache"));
    let remote = Arc::new(DavRemote::new(&hub, &dav, "SyncTest", "", "anna", &token, false));
    let vol = Arc::new(Volume::new(remote, Cache::open(&cache).unwrap(), u64::MAX));
    assert!(vol.refresh().await.unwrap());
    let session = match fusefs::mount(vol.clone(), tokio::runtime::Handle::current(), &mountpoint) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("skipped: no FUSE here ({e})");
            return;
        }
    };
    // A big file is read where it is: a piece asked for over WebDAV (a Range request).
    assert_eq!(vol.read_piece("Photos/2024/Sommer & Meer ä.txt", 1, 3).await.unwrap(), b"eac");

    let mp = mountpoint.clone();
    tokio::task::spawn_blocking(move || {
        assert_eq!(names(&mp), ["Photos"]);
        let beach = mp.join("Photos/2024/Sommer & Meer ä.txt");
        assert_eq!(std::fs::read_to_string(&beach).unwrap(), "beach");
        // The NAS's date is kept on the copy.
        let modified = std::fs::metadata(&beach).unwrap().modified().unwrap().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
        assert_eq!(modified, 1_790_000_000);
        std::fs::create_dir(mp.join("Docs")).unwrap();
        std::fs::write(mp.join("Docs/letter & more.txt"), "dear anna").unwrap();
        std::fs::write(&beach, "beach, longer").unwrap();                // replaces: the old one goes to the share's trash
        std::fs::rename(mp.join("Docs/letter & more.txt"), mp.join("Docs/letter.txt")).unwrap();
        std::fs::write(mp.join("gone.txt"), "x").unwrap();
        std::fs::remove_file(mp.join("gone.txt")).unwrap();
    })
    .await
    .unwrap();

    assert_eq!(std::fs::read_to_string(nas_dir.join("Docs/letter.txt")).unwrap(), "dear anna");
    assert!(!nas_dir.join("Docs/letter & more.txt").exists(), "renamed on the NAS (WebDAV MOVE)");
    assert_eq!(std::fs::read_to_string(nas_dir.join("Photos/2024/Sommer & Meer ä.txt")).unwrap(), "beach, longer");
    assert!(!nas_dir.join("gone.txt").exists());
    let trash = walk(&nas_dir.join(".alvaos-trash"));
    assert!(trash.iter().any(|n| n.contains("Sommer")), "the old version is in the trash: {trash:?}");

    // A change on the NAS shows after the next look at the tree.
    std::fs::write(nas_dir.join("fresh.txt"), "from the NAS").unwrap();
    assert!(vol.refresh().await.unwrap());
    let mp = mountpoint.clone();
    tokio::task::spawn_blocking(move || {
        assert!(names(&mp).contains(&"fresh.txt".to_string()));
        assert_eq!(std::fs::read_to_string(mp.join("fresh.txt")).unwrap(), "from the NAS");
    })
    .await
    .unwrap();
    eprintln!("mounted against the real hub, and everything held");
    drop(session);
    let _ = std::fs::remove_dir_all(&top);
}
