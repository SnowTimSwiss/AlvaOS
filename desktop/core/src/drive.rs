//! Files as a drive of the computer: the shares of the person, over WebDAV, through the local
//! address of the NAS (so it works away from home too). The computer signs in with its own
//! session, not the person's password; removing the computer in Devices ends it.
//!
//! - Linux: GNOME's `gio` (Files, and every app that uses GIO, see it under Other Locations).
//! - macOS: Finder (`mount volume`).
//! - Windows: its WebDAV client only signs in over HTTPS with a trusted certificate, so the
//!   drive comes with the sync folder (0.6). Until then Files in the app's window.

use anyhow::{bail, Context, Result};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

pub fn supported() -> bool {
    cfg!(any(target_os = "linux", target_os = "macos"))
}

/// The address the drive is mounted from.
pub fn location(port: u16, user: &str) -> String {
    if cfg!(target_os = "macos") {
        format!("http://127.0.0.1:{port}/")
    } else {
        format!("dav://{user}@127.0.0.1:{port}/")
    }
}

pub async fn mount(port: u16, user: &str, token: &str) -> Result<String> {
    if !supported() {
        bail!("On Windows the drive comes with the sync folder. Until then: Files in the AlvaOS window.");
    }
    let place = location(port, user);
    if cfg!(target_os = "macos") {
        let script = format!(
            "mount volume \"{place}\" as user name \"{}\" with password \"{}\"",
            user.replace(['"', '\\'], ""),
            token.replace(['"', '\\'], "")
        );
        let out = Command::new("osascript").arg("-e").arg(script).output().await.context("osascript is missing")?;
        if !out.status.success() {
            bail!("The drive could not be added: {}", String::from_utf8_lossy(&out.stderr).trim());
        }
        return Ok(place);
    }
    // gio asks for the password on its input.
    let mut child = Command::new("gio")
        .args(["mount", &place])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .context("gio is missing: install gvfs (GNOME) to see Files as a drive")?;
    if let Some(mut input) = child.stdin.take() {
        input.write_all(format!("{token}\n").as_bytes()).await?;
    }
    let out = child.wait_with_output().await?;
    let said = String::from_utf8_lossy(&out.stderr);
    if !out.status.success() && !said.contains("already mounted") {
        bail!("The drive could not be added: {}", said.trim());
    }
    Ok(place)
}

pub async fn unmount(port: u16, user: &str) -> Result<()> {
    if cfg!(target_os = "linux") {
        let _ = Command::new("gio").args(["mount", "-u", &location(port, user)]).output().await;
    } else if cfg!(target_os = "macos") {
        let _ = Command::new("diskutil").args(["unmount", "/Volumes/127.0.0.1"]).output().await;
    }
    Ok(())
}
