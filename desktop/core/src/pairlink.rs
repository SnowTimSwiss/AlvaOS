//! The link of the Hub to connect a device: `alvaos://pair?c=<code>&a=<address>&l=<Link address>&n=<NAS>&u=<person>`
//! (Hub › Devices). A computer has no camera: the person pastes the link, or types the code
//! and the address.

use url::Url;

#[derive(Debug, Clone, PartialEq, Default)]
pub struct PairLink {
    /// The pairing code, letters and digits only (`QFR9YKZK`).
    pub code: String,
    /// Where the Hub answers at home (`http://192.168.1.20:8090`).
    pub addresses: Vec<String>,
    /// The NAS's name.
    pub nas: String,
    /// The person the code is for.
    pub user: String,
    /// The NAS's Link address (64 hex digits), when AlvaOS Link is on.
    pub link: Option<String>,
}

impl PairLink {
    /// The link as pasted; None when it is not one of AlvaOS's.
    pub fn parse(text: &str) -> Option<Self> {
        let url = Url::parse(text.trim()).ok()?;
        if url.scheme() != "alvaos" || url.host_str() != Some("pair") {
            return None;
        }
        let mut out = PairLink::default();
        for (key, value) in url.query_pairs() {
            match key.as_ref() {
                "c" => out.code = code(&value),
                "a" => {
                    let address = address(&value);
                    if !address.is_empty() {
                        out.addresses.push(address);
                    }
                }
                "n" => out.nas = value.trim().chars().take(60).collect(),
                "u" => out.user = value.trim().chars().take(32).collect(),
                "l" if is_link_address(&value) => out.link = Some(value.to_lowercase()),
                _ => {}
            }
        }
        if out.code.is_empty() || (out.addresses.is_empty() && out.link.is_none()) {
            return None;
        }
        Some(out)
    }

    /// A code and an address typed by hand (no Link address: at home only).
    pub fn typed(code_text: &str, address_text: &str) -> Option<Self> {
        let code = code(code_text);
        let address = address(address_text);
        if code.len() != 8 || address.is_empty() {
            return None;
        }
        Some(PairLink { code, addresses: vec![address], ..Default::default() })
    }
}

/// `qfr9-ykzk` → `QFR9YKZK`.
pub fn code(text: &str) -> String {
    text.chars().filter(|c| c.is_ascii_alphanumeric()).map(|c| c.to_ascii_uppercase()).take(16).collect()
}

/// `192.168.1.20:8090` → `http://192.168.1.20:8090`; '' when it is not an address.
pub fn address(text: &str) -> String {
    let text = text.trim().trim_end_matches('/');
    if text.is_empty() {
        return String::new();
    }
    let with_scheme = if text.contains("://") { text.to_string() } else { format!("http://{text}") };
    match Url::parse(&with_scheme) {
        Ok(url) if matches!(url.scheme(), "http" | "https") && url.host_str().is_some() => {
            with_scheme.trim_end_matches('/').to_string()
        }
        _ => String::new(),
    }
}

/// 64 hex digits.
pub fn is_link_address(text: &str) -> bool {
    text.len() == 64 && text.chars().all(|c| c.is_ascii_hexdigit())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_link_of_the_hub_is_read_with_all_its_parts() {
        let id = "ab".repeat(32);
        let link = PairLink::parse(&format!(
            "alvaos://pair?c=QFR9YKZK&n=cygnus&u=tim&a=http%3A%2F%2F192.168.1.20%3A8090&a=https%3A%2F%2Fnas.example.ch%2F&l={id}"
        ))
        .unwrap();
        assert_eq!(link.code, "QFR9YKZK");
        assert_eq!(link.addresses, vec!["http://192.168.1.20:8090", "https://nas.example.ch"]);
        assert_eq!((link.nas.as_str(), link.user.as_str()), ("cygnus", "tim"));
        assert_eq!(link.link.as_deref(), Some(id.as_str()));
    }

    #[test]
    fn only_the_link_address_is_still_a_way_in() {
        let id = "cd".repeat(32);
        let link = PairLink::parse(&format!("  alvaos://pair?c=QFR9-YKZK&l={id}\n")).unwrap();
        assert!(link.addresses.is_empty() && link.link.is_some() && link.code == "QFR9YKZK");
    }

    #[test]
    fn other_links_are_not_taken() {
        assert_eq!(PairLink::parse("https://example.com/?c=QFR9YKZK"), None);
        assert_eq!(PairLink::parse("alvaos://pair?c=QFR9YKZK"), None);
        assert_eq!(PairLink::parse("alvaos://pair?c=QFR9YKZK&l=nothex"), None);
        assert_eq!(PairLink::parse("not a link"), None);
    }

    #[test]
    fn a_code_and_an_address_can_be_typed() {
        let typed = PairLink::typed("qfr9-ykzk", "192.168.1.20:8090/").unwrap();
        assert_eq!((typed.code.as_str(), typed.addresses[0].as_str()), ("QFR9YKZK", "http://192.168.1.20:8090"));
        assert_eq!(PairLink::typed("short", "192.168.1.20"), None);
        assert_eq!(PairLink::typed("QFR9YKZK", ""), None);
        assert_eq!(address("ftp://x"), "");
    }
}
