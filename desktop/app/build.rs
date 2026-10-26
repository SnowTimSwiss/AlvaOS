fn main() {
    // The app's own commands, allowed only to its own pages (capabilities/main.json), never to
    // the Hub's window, which shows pages of the NAS.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
        "app_state", "parse_link", "connect", "choose", "open_hub", "open_browser", "drive", "set_connected",
        "sign_out", "remote_shares", "remote_folders", "add_sync", "remove_sync", "sync_now", "sync_answer",
        "pause_sync", "open_sync", "pick_local",
    ])))
    .expect("tauri build");
}
