//! The sync folder against a real NAS: the Hub's folder tree and WebDAV (backend/files_server.py,
//! files_dav.py) with the real helper on a real share. Started by
//! scripts/dev/sync_against_nas.py (needs root for the helper); skipped without it.

use std::path::{Path, PathBuf};

use alvaos_core::sync::{scan, sync_once, DavRemote, Options};

fn env(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.is_empty())
}

fn mtime(path: &Path) -> i64 {
    filetime::FileTime::from_last_modification_time(&std::fs::metadata(path).unwrap()).unix_seconds()
}

fn write(path: &Path, body: &str, at: i64) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, body).unwrap();
    filetime::set_file_mtime(path, filetime::FileTime::from_unix_time(at, 0)).unwrap();
}

#[tokio::test]
async fn a_sync_folder_against_the_real_hub_and_webdav() {
    let (Some(hub), Some(dav), Some(token), Some(share_dir)) =
        (env("ALVAOS_TEST_HUB"), env("ALVAOS_TEST_DAV"), env("ALVAOS_TEST_TOKEN"), env("ALVAOS_TEST_SHARE_DIR"))
    else {
        eprintln!("skipped: started by scripts/dev/sync_against_nas.py");
        return;
    };
    let nas_dir = PathBuf::from(share_dir);
    let top = std::env::temp_dir().join(format!("alvaos-sync-nas-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&top);
    let (root, state, trash) = (top.join("AlvaOS/SyncTest"), top.join("state.json"), top.join("trash"));
    let nas = DavRemote::new(&hub, &dav, "SyncTest", "", "anna", &token, false);
    let options = Options { computer: "laptop".into(), allow_many_deletions: false, trash: Some(trash.clone()), ..Options::default() };
    let t = 1_790_000_000;

    // 1. What the NAS has comes down, with its date.
    let first = sync_once(&root, &nas, &state, &options).await.unwrap();
    assert!(first.problems.is_empty(), "{first:?}");
    assert_eq!(std::fs::read_to_string(root.join("Photos/2024/Sommer & Meer ä.txt")).unwrap(), "beach");
    assert_eq!(mtime(&root.join("Photos/2024/Sommer & Meer ä.txt")), t);

    // 2. New here: it goes up, keeping its date on the NAS.
    write(&root.join("Docs/letter.txt"), "dear anna", t + 100);
    let up = sync_once(&root, &nas, &state, &options).await.unwrap();
    assert_eq!((up.uploaded, up.problems.len()), (1, 0), "{up:?}");
    assert_eq!(std::fs::read_to_string(nas_dir.join("Docs/letter.txt")).unwrap(), "dear anna");
    assert_eq!(mtime(&nas_dir.join("Docs/letter.txt")), t + 100, "the NAS keeps the date");

    // 3. Nothing changed: nothing happens.
    let quiet = sync_once(&root, &nas, &state, &options).await.unwrap();
    assert_eq!(quiet.changed(), 0, "{quiet:?}");

    // 4. Changed here: replaced on the NAS, the old one in the share's trash.
    write(&root.join("Docs/letter.txt"), "dear anna, again", t + 200);
    let again = sync_once(&root, &nas, &state, &options).await.unwrap();
    assert_eq!(again.uploaded, 1, "{again:?}");
    assert_eq!(std::fs::read_to_string(nas_dir.join("Docs/letter.txt")).unwrap(), "dear anna, again");
    let in_trash = walk_names(&nas_dir.join(".alvaos-trash"));
    assert!(in_trash.iter().any(|n| n == "letter.txt"), "{in_trash:?}");

    // 5. Deleted here: into the share's trash on the NAS.
    std::fs::remove_file(root.join("Docs/letter.txt")).unwrap();
    let gone = sync_once(&root, &nas, &state, &options).await.unwrap();
    assert_eq!(gone.deleted_remote, 1, "{gone:?}");
    assert!(!nas_dir.join("Docs/letter.txt").exists());

    // 6. Deleted on the NAS: into the trash here.
    std::fs::remove_file(nas_dir.join("Photos/2024/Sommer & Meer ä.txt")).unwrap();
    let theirs = sync_once(&root, &nas, &state, &options).await.unwrap();
    assert_eq!(theirs.trashed_local, 1, "{theirs:?}");
    assert!(trash.join("Photos/2024/Sommer & Meer ä.txt").exists());

    // 7. Changed on both: both kept, on both sides.
    write(&root.join("plan.txt"), "mine", t + 300);
    std::fs::write(nas_dir.join("plan.txt"), "theirs, longer").unwrap();
    let both = sync_once(&root, &nas, &state, &options).await.unwrap();
    assert_eq!(both.conflicts, 1, "{both:?}");
    let names = scan(&root).unwrap();
    let copy = names.keys().find(|p| p.starts_with("plan (conflict laptop")).expect("a conflict copy").clone();
    assert_eq!(std::fs::read_to_string(root.join("plan.txt")).unwrap(), "theirs, longer");
    assert_eq!(std::fs::read_to_string(nas_dir.join(&copy)).unwrap(), "mine");
    assert_eq!(sync_once(&root, &nas, &state, &options).await.unwrap().changed(), 0);
    let _ = std::fs::remove_dir_all(&top);
}

fn walk_names(dir: &Path) -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(entries) = std::fs::read_dir(dir) {
        for e in entries.flatten() {
            out.push(e.file_name().to_string_lossy().to_string());
            if e.path().is_dir() {
                out.extend(walk_names(&e.path()));
            }
        }
    }
    out
}
