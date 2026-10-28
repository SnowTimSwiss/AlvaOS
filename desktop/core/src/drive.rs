//! Files as a drive of the computer: the shares of the person, over WebDAV, through the local
//! address of the NAS (so it works away from home too). The computer signs in with its own
//! session, not the person's password; removing the computer in Devices ends it.
//!
//! - Linux: GNOME's `gio` (Files, and every app that uses GIO, see it under Other Locations).
//! - macOS: Finder (`mount volume`).
//! - Windows: a network drive (`net use Z:`). Windows' WebDAV client only signs in over HTTPS
//!   with a certificate it trusts, so the app answers HTTPS on a port of its own with a
//!   certificate made for `127.0.0.1` (tls.rs) and asks Windows once to trust that one
//!   certificate for this user. Windows limits files it opens through WebDAV to 50 MB (a
//!   setting of the WebClient service); bigger files are for the sync folder.

use std::path::Path;

use anyhow::{anyhow, bail, Context, Result};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

pub fn supported() -> bool {
    cfg!(any(target_os = "linux", target_os = "macos", windows))
}

/// The address the drive is mounted from.
pub fn location(port: u16, user: &str) -> String {
    if cfg!(target_os = "macos") {
        format!("http://127.0.0.1:{port}/")
    } else {
        format!("dav://{user}@127.0.0.1:{port}/")
    }
}

/// `\\127.0.0.1@SSL@18092\DavWWWRoot`: how Windows is told to use HTTPS and a port.
pub fn windows_share(tls_port: u16) -> String {
    format!(r"\\127.0.0.1@SSL@{tls_port}\DavWWWRoot")
}

/// The drive letter a `net use` listing shows for this share, if there is one.
pub fn mapped_letter(listing: &str, tls_port: u16) -> Option<char> {
    let marker = format!("@SSL@{tls_port}");
    listing.lines().filter(|l| l.contains(&marker)).find_map(|line| {
        line.split_whitespace().find_map(|word| {
            let mut chars = word.chars();
            match (chars.next(), chars.next(), chars.next()) {
                (Some(letter), Some(':'), None) if letter.is_ascii_alphabetic() => Some(letter.to_ascii_uppercase()),
                _ => None,
            }
        })
    })
}

/// The SHA-1 of the certificate, as Windows shows it (a thumbprint).
pub fn thumbprint(der: &[u8]) -> String {
    let digest = ring::digest::digest(&ring::digest::SHA1_FOR_LEGACY_USE_ONLY, der);
    digest.as_ref().iter().map(|b| format!("{b:02x}")).collect()
}

/// `tls_port` and `cert` (the certificate file of tls.rs) are only used on Windows.
pub async fn mount(port: u16, user: &str, token: &str, tls_port: u16, cert: &Path) -> Result<String> {
    if cfg!(windows) {
        return mount_windows(user, token, tls_port, cert).await;
    }
    if !supported() {
        bail!("Files as a drive is not possible on this system.");
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

pub async fn unmount(port: u16, user: &str, tls_port: u16) -> Result<()> {
    if cfg!(windows) {
        if let Some(letter) = windows_letter(tls_port).await {
            let _ = Command::new("net").args(["use", &format!("{letter}:"), "/delete", "/y"]).output().await;
        }
    } else if cfg!(target_os = "linux") {
        let _ = Command::new("gio").args(["mount", "-u", &location(port, user)]).output().await;
    } else if cfg!(target_os = "macos") {
        let _ = Command::new("diskutil").args(["unmount", "/Volumes/127.0.0.1"]).output().await;
    }
    Ok(())
}

// ── Windows ───────────────────────────────────────────────────────────────────

async fn windows_letter(tls_port: u16) -> Option<char> {
    let out = Command::new("net").arg("use").output().await.ok()?;
    mapped_letter(&String::from_utf8_lossy(&out.stdout), tls_port)
}

/// Windows trusts this one certificate for this user (asks the person once, with its own dialog).
async fn trust(cert: &Path) -> Result<()> {
    let pem = std::fs::read_to_string(cert).context("the certificate of this computer is missing")?;
    let der = pem
        .split("-----BEGIN CERTIFICATE-----")
        .nth(1)
        .and_then(|rest| rest.split("-----END CERTIFICATE-----").next())
        .map(|body| body.split_whitespace().collect::<String>())
        .and_then(|b64| decode(&b64))
        .ok_or_else(|| anyhow!("the certificate of this computer cannot be read"))?;
    let print = thumbprint(&der);
    let known = Command::new("certutil").args(["-user", "-store", "Root", &print]).output().await.context("certutil is missing")?;
    if known.status.success() {
        return Ok(());
    }
    // An older certificate of ours (the app's folder was deleted once): out with it.
    let _ = Command::new("certutil").args(["-user", "-delstore", "Root", crate::tls::NAME]).output().await;
    let added = Command::new("certutil").args(["-user", "-addstore", "-f", "Root"]).arg(cert).output().await?;
    if !added.status.success() {
        bail!("Windows was not asked to trust the address of this computer. Say Yes in the window that asks, then try again.");
    }
    Ok(())
}

fn decode(b64: &str) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    let (mut bits, mut have) = (0u32, 0);
    for c in b64.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            b'=' => break,
            _ => return None,
        };
        bits = (bits << 6) | v as u32;
        have += 6;
        if have >= 8 {
            have -= 8;
            out.push((bits >> have) as u8);
            bits &= (1 << have) - 1;
        }
    }
    Some(out)
}

/// From Z down: the first letter nothing uses.
fn free_letter() -> Option<char> {
    ('D'..='Z').rev().find(|c| !Path::new(&format!("{c}:\\")).exists())
}

async fn mount_windows(user: &str, token: &str, tls_port: u16, cert: &Path) -> Result<String> {
    if let Some(letter) = windows_letter(tls_port).await {
        return Ok(format!("{letter}:"));
    }
    trust(cert).await?;
    let letter = free_letter().ok_or_else(|| anyhow!("There is no free drive letter."))?;
    let out = Command::new("net")
        .args(["use", &format!("{letter}:"), &windows_share(tls_port), token, &format!("/user:{user}"), "/persistent:no"])
        .output()
        .await
        .context("net is missing")?;
    if !out.status.success() {
        let said = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
        let hint = if said.contains("67") || said.contains("1222") {
            " Windows' \"WebClient\" service may be off: start it in Services, then try again."
        } else {
            ""
        };
        bail!("The drive could not be added: {}{hint}", said.trim());
    }
    Ok(format!("{letter}:"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_address_windows_is_given_and_the_letter_it_shows() {
        assert_eq!(windows_share(18092), r"\\127.0.0.1@SSL@18092\DavWWWRoot");
        let listing = "New connections will not be remembered.\n\n\
            Status       Local     Remote                    Network\n\
            -------------------------------------------------------------------------------\n\
            OK           Y:        \\\\server\\share             Microsoft Windows Network\n\
            OK           z:        \\\\127.0.0.1@SSL@18092\\DavWWWRoot  Web Client Network\n\
            The command completed successfully.\n";
        assert_eq!(mapped_letter(listing, 18092), Some('Z'));
        assert_eq!(mapped_letter(listing, 18099), None);
        assert_eq!(mapped_letter("", 18092), None);
    }

    #[test]
    fn a_thumbprint_is_the_sha1_in_hex() {
        assert_eq!(thumbprint(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
    }

    #[test]
    fn the_certificate_is_decoded_like_a_pem_body() {
        assert_eq!(decode("aGVsbG8gd29ybGQ=").unwrap(), b"hello world");
        assert!(decode("not*base64").is_none());
    }
}
