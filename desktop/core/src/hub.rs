//! The few calls the app makes to the Hub itself; everything else the Hub does in its window
//! or in the browser. `base` is where the Hub answers: the local proxy (through Link) or, for a
//! NAS without Link, its address at home.

use std::time::Duration;

use anyhow::{anyhow, Result};
use serde_json::{json, Value};

use crate::{DeviceInfo, Paired};

pub const COOKIE: &str = "alvaos_files";

/// The NAS said this computer is signed out (removed in Devices, or a new password).
#[derive(Debug)]
pub struct SignedOut;

impl std::fmt::Display for SignedOut {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "This computer was signed out on the NAS. Connect it again.")
    }
}

impl std::error::Error for SignedOut {}

/// The person uses two-step sign-in: ask for the code and try again.
#[derive(Debug)]
pub struct NeedsCode;

impl std::fmt::Display for NeedsCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Enter the code of your two-step sign-in.")
    }
}

impl std::error::Error for NeedsCode {}

pub struct Hub {
    base: String,
    http: reqwest::Client,
}

impl Hub {
    pub fn new(base: &str) -> Self {
        let host = url::Url::parse(base).ok().and_then(|u| u.host_str().map(str::to_string)).unwrap_or_default();
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(20))
            .connect_timeout(Duration::from_secs(8))
            .user_agent(format!("AlvaOSDesktop/{}", crate::VERSION))
            // The NAS at home has a certificate of its own, not one of a public authority.
            .danger_accept_invalid_certs(is_home(&host))
            .build()
            .expect("an HTTP client");
        Self { base: base.trim_end_matches('/').to_string(), http }
    }

    async fn call(&self, method: reqwest::Method, path: &str, token: Option<&str>, body: Option<Value>) -> Result<Value> {
        let mut request = self.http.request(method.clone(), format!("{}{path}", self.base));
        if method != reqwest::Method::GET {
            request = request.header("X-AlvaOS-Files", "1");
        }
        if let Some(token) = token {
            request = request.header("Cookie", format!("{COOKIE}={token}"));
        }
        if let Some(body) = body {
            request = request.json(&body);
        }
        let res = request.send().await.map_err(|e| anyhow!("The NAS cannot be reached ({e})."))?;
        let status = res.status();
        let value: Value = res.json().await.unwrap_or(Value::Null);
        if status.is_success() {
            return Ok(value);
        }
        if status == 401 && value.get("signed_out").and_then(Value::as_bool) == Some(true) {
            return Err(SignedOut.into());
        }
        if status == 401 && value.get("needs_code").and_then(Value::as_bool) == Some(true)
            && value.get("error").and_then(Value::as_str).unwrap_or("").is_empty()
        {
            return Err(NeedsCode.into());
        }
        let said = value.get("error").and_then(Value::as_str).unwrap_or("").to_string();
        Err(anyhow!(if said.is_empty() { format!("The NAS answered {status}.") } else { said }))
    }

    /// The pairing code of the Hub, at home (for a NAS without Link).
    pub async fn pair(&self, code: &str, device: &DeviceInfo) -> Result<Paired> {
        let value = self.call(reqwest::Method::POST, "/api/devices/pair", None, Some(json!({"code": code, "device": device}))).await?;
        Ok(serde_json::from_value(value)?)
    }

    /// Name and password (and the two-step code); a session of this computer's own.
    pub async fn sign_in(&self, user: &str, password: &str, code: &str, device: &DeviceInfo) -> Result<String> {
        let value = self
            .call(reqwest::Method::POST, "/api/login", None,
                  Some(json!({"username": user, "password": password, "code": code, "device": device})))
            .await?;
        value.get("token").and_then(Value::as_str).map(str::to_string).ok_or_else(|| anyhow!("The NAS gave no session."))
    }

    /// Tells the NAS this computer's Link key, so it may come in from anywhere. The NAS's Link address back.
    pub async fn register_link(&self, token: &str, key: &str) -> Result<String> {
        let value = self.call(reqwest::Method::POST, "/api/devices/link", Some(token), Some(json!({"key": key}))).await?;
        Ok(value.get("link").and_then(Value::as_str).unwrap_or("").to_string())
    }

    /// Who is signed in, the NAS's name, the Hub apps.
    pub async fn me(&self, token: &str) -> Result<Value> {
        self.call(reqwest::Method::GET, "/api/me", Some(token), None).await
    }

    /// Signs this computer out on the NAS too (Devices).
    pub async fn remove_device(&self, token: &str, device: &str) -> Result<()> {
        self.call(reqwest::Method::DELETE, &format!("/api/devices/{device}"), Some(token), None).await.map(|_| ())
    }

    /// A code that works once, for a minute: the Hub opened with it (`/?handoff=`) is signed in.
    pub async fn handoff(&self, token: &str) -> Result<String> {
        let value = self.call(reqwest::Method::POST, "/api/devices/handoff", Some(token), Some(json!({}))).await?;
        value.get("code").and_then(Value::as_str).map(str::to_string).ok_or_else(|| anyhow!("The NAS gave no code."))
    }
}

/// An address at home (private network, `.local`, a plain name): a certificate of its own is normal there.
pub fn is_home(host: &str) -> bool {
    let host = host.trim_start_matches('[').trim_end_matches(']');
    if let Ok(ip) = host.parse::<std::net::IpAddr>() {
        return match ip {
            std::net::IpAddr::V4(v4) => v4.is_private() || v4.is_loopback() || v4.is_link_local(),
            std::net::IpAddr::V6(v6) => v6.is_loopback() || (v6.segments()[0] & 0xfe00) == 0xfc00 || (v6.segments()[0] & 0xffc0) == 0xfe80,
        };
    }
    host.ends_with(".local") || host.ends_with(".lan") || host.ends_with(".home.arpa") || !host.contains('.')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_home_addresses_may_have_a_certificate_of_their_own() {
        for home in ["192.168.1.20", "10.0.0.5", "127.0.0.1", "cygnus", "cygnus.local", "[fd00::1]"] {
            assert!(is_home(home), "{home}");
        }
        for away in ["nas.example.ch", "8.8.8.8", "[2001:4860::1]"] {
            assert!(!is_home(away), "{away}");
        }
    }
}
