//! A local HTTPS address for Files, for Windows' network drive: its WebDAV client only signs in
//! over HTTPS with a certificate it trusts, never over plain HTTP. So the app makes a
//! certificate of its own for `127.0.0.1` and `localhost` (valid for no other name, and not
//! allowed to sign anything: it is a plain server certificate, not a certificate authority),
//! asks Windows once to trust that one certificate for this user, and answers HTTPS on a
//! port of its own, handing every connection on to the plain Files address.
//!
//! The key stays in the app's folder (readable only by this user). Anyone who could read it
//! could only pretend to be `127.0.0.1` on this computer.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, Result};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use tokio::net::{TcpListener, TcpStream};
use tokio::task::JoinHandle;
use tokio_rustls::TlsAcceptor;

/// The name Windows shows for the certificate (and the drive code looks it up by).
pub const NAME: &str = "AlvaOS on this computer";

pub struct LocalCert {
    pub cert_der: Vec<u8>,
    key_der: Vec<u8>,
    /// Where the certificate (PEM) is, to hand to the system's trust store.
    pub pem_path: PathBuf,
}

impl LocalCert {
    /// The one in `dir`, made on the first call.
    pub fn load_or_create(dir: &Path) -> Result<Self> {
        let (cert_file, key_file) = (dir.join("local-cert.pem"), dir.join("local-key.pem"));
        if let (Ok(cert), Ok(key)) = (std::fs::read_to_string(&cert_file), std::fs::read_to_string(&key_file)) {
            if let (Some(cert_der), Some(key_der)) = (pem_der(&cert, "CERTIFICATE"), pem_der(&key, "PRIVATE KEY")) {
                return Ok(Self { cert_der, key_der, pem_path: cert_file });
            }
        }
        std::fs::create_dir_all(dir).with_context(|| format!("cannot make {}", dir.display()))?;
        let mut params = rcgen::CertificateParams::new(vec!["localhost".to_string()])?;
        params.subject_alt_names.push(rcgen::SanType::IpAddress(std::net::IpAddr::from([127, 0, 0, 1])));
        params.distinguished_name.push(rcgen::DnType::CommonName, NAME);
        params.is_ca = rcgen::IsCa::ExplicitNoCa;
        params.key_usages = vec![rcgen::KeyUsagePurpose::DigitalSignature, rcgen::KeyUsagePurpose::KeyEncipherment];
        params.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ServerAuth];
        params.not_before = rcgen::date_time_ymd(2024, 1, 1);
        params.not_after = rcgen::date_time_ymd(2044, 1, 1);
        let key = rcgen::KeyPair::generate()?;
        let cert = params.self_signed(&key)?;
        write_private(&key_file, &key.serialize_pem())?;
        std::fs::write(&cert_file, cert.pem())?;
        Ok(Self { cert_der: cert.der().to_vec(), key_der: key.serialize_der(), pem_path: cert_file })
    }

    fn server_config(&self) -> Result<rustls::ServerConfig> {
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        Ok(rustls::ServerConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()?
            .with_no_client_auth()
            .with_single_cert(
                vec![CertificateDer::from(self.cert_der.clone())],
                PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(self.key_der.clone())),
            )?)
    }
}

/// The text between `-----BEGIN <label>-----` and `-----END <label>-----`, decoded.
fn pem_der(pem: &str, label: &str) -> Option<Vec<u8>> {
    let begin = format!("-----BEGIN {label}-----");
    let end = format!("-----END {label}-----");
    let body = pem.split(&begin).nth(1)?.split(&end).next()?;
    let clean: String = body.chars().filter(|c| !c.is_whitespace()).collect();
    base64_decode(&clean)
}

fn base64_decode(text: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(text.len() * 3 / 4);
    let (mut bits, mut have) = (0u32, 0);
    for c in text.bytes() {
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

fn write_private(path: &Path, text: &str) -> Result<()> {
    use std::io::Write;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(path)?.write_all(text.as_bytes())?;
    Ok(())
}

/// HTTPS on `127.0.0.1:<port>`, every connection handed on to the plain address `127.0.0.1:<to>`.
pub struct Front {
    pub port: u16,
    task: JoinHandle<()>,
}

impl Front {
    /// Listens on `preferred` (or any free port when that is taken).
    pub async fn start(cert: &LocalCert, preferred: u16, to: u16) -> Result<Self> {
        let acceptor = TlsAcceptor::from(Arc::new(cert.server_config()?));
        let listener = match TcpListener::bind(("127.0.0.1", preferred)).await {
            Ok(l) => l,
            Err(_) => TcpListener::bind(("127.0.0.1", 0)).await.context("no free port on this computer")?,
        };
        let port = listener.local_addr()?.port();
        let task = tokio::spawn(async move {
            loop {
                let Ok((socket, _)) = listener.accept().await else { continue };
                let acceptor = acceptor.clone();
                tokio::spawn(async move {
                    let _ = socket.set_nodelay(true);
                    let Ok(mut secure) = acceptor.accept(socket).await else { return };
                    let Ok(mut plain) = TcpStream::connect(("127.0.0.1", to)).await else { return };
                    let _ = tokio::io::copy_bidirectional(&mut secure, &mut plain).await;
                });
            }
        });
        Ok(Self { port, task })
    }

    pub fn stop(&self) {
        self.task.abort();
    }
}

impl Drop for Front {
    fn drop(&mut self) {
        self.task.abort();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    fn dir() -> PathBuf {
        std::env::temp_dir().join(format!("alvaos-tls-{}", crate::link::hex(&crate::link::new_key()[..6])))
    }

    #[test]
    fn the_certificate_is_made_once_and_is_no_authority() {
        let d = dir();
        let first = LocalCert::load_or_create(&d).unwrap();
        let again = LocalCert::load_or_create(&d).unwrap();
        assert_eq!(first.cert_der, again.cert_der, "the same one the next time");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(d.join("local-key.pem")).unwrap().permissions().mode() & 0o777, 0o600);
        }
        // Not a CA: basicConstraints would say CA:TRUE (DER: 30 03 01 01 ff) if it were one.
        assert!(!first.cert_der.windows(5).any(|w| w == [0x30, 0x03, 0x01, 0x01, 0xff]));
        let _ = std::fs::remove_dir_all(d);
    }

    #[tokio::test]
    async fn https_on_this_computer_is_handed_on_to_the_plain_address() {
        let d = dir();
        let cert = LocalCert::load_or_create(&d).unwrap();
        // A plain "Files" that answers one line.
        let plain = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let plain_port = plain.local_addr().unwrap().port();
        tokio::spawn(async move {
            let (mut s, _) = plain.accept().await.unwrap();
            let mut buf = [0u8; 4];
            s.read_exact(&mut buf).await.unwrap();
            s.write_all(format!("you said {}", String::from_utf8_lossy(&buf)).as_bytes()).await.unwrap();
        });
        let front = Front::start(&cert, 0, plain_port).await.unwrap();
        // A client that trusts only this certificate, asking for 127.0.0.1 as Windows will.
        let mut roots = rustls::RootCertStore::empty();
        roots.add(CertificateDer::from(cert.cert_der.clone())).unwrap();
        let config = rustls::ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_root_certificates(roots)
            .with_no_client_auth();
        let connector = tokio_rustls::TlsConnector::from(Arc::new(config));
        let tcp = TcpStream::connect(("127.0.0.1", front.port)).await.unwrap();
        let name = rustls::pki_types::ServerName::try_from("127.0.0.1").unwrap();
        let mut tls = connector.connect(name, tcp).await.expect("the certificate is valid for 127.0.0.1");
        tls.write_all(b"ping").await.unwrap();
        let mut said = String::new();
        tls.read_to_string(&mut said).await.unwrap_or_default();
        assert_eq!(said, "you said ping");
        // Any other name is refused.
        let tcp = TcpStream::connect(("127.0.0.1", front.port)).await.unwrap();
        let other = rustls::pki_types::ServerName::try_from("example.com").unwrap();
        assert!(connector.connect(other, tcp).await.is_err());
        let _ = std::fs::remove_dir_all(d);
    }
}
