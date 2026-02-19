#!/usr/bin/env python3
"""
AlvaOS Buddy Backup Manager (v0.7.0)
Identity + token pairing + WireGuard tunnel automation.
"""

import base64
import hashlib
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

DEFAULT_BUDDY_SETTINGS = {
    "enabled": False,
    "incoming_path": "/mnt/alvaos/buddy-incoming",
    "incoming_quota_gb": 200,
    "outgoing_sources": [],
    "interval_minutes": 1440,
    "keep_last": 30,
    "recursive_retention": True,
    "peer_policies": {},
    "encryption_enabled": False,
    "encryption_salt": "",
    "encryption_hash": "",
}


class BuddyBackupManager:
    def __init__(self, run_command: Callable):
        self.run_command = run_command
        self.state_dir = self._resolve_state_dir()
        self.identity_file = os.path.join(self.state_dir, "buddy_identity.json")
        self.peers_file = os.path.join(self.state_dir, "buddy_peers.json")
        self.tokens_file = os.path.join(self.state_dir, "buddy_tokens.json")
        self.settings_file = os.path.join(self.state_dir, "buddy_settings.json")
        self.wg_dir = os.path.join(self.state_dir, "wireguard")
        self.wg_config_path = os.path.join(self.wg_dir, "buddy0.conf")
        self.interface_name = "buddy0"
        self.default_listen_port = 51820

        self._ensure_dirs()
        self._ensure_defaults()

    def _resolve_state_dir(self) -> str:
        preferred = Path("/var/lib/alvaos")
        try:
            preferred.mkdir(parents=True, exist_ok=True)
            probe = preferred / ".alvaos_buddy_write_test"
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            probe.unlink(missing_ok=True)
            return str(preferred)
        except Exception:
            fallback = (Path(__file__).resolve().parent / ".." / ".alvaos_state").resolve()
            fallback.mkdir(parents=True, exist_ok=True)
            return str(fallback)

    def _ensure_dirs(self) -> None:
        Path(self.state_dir).mkdir(parents=True, exist_ok=True)
        Path(self.wg_dir).mkdir(parents=True, exist_ok=True)

    def _load_json(self, path: str, default):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _save_json(self, path: str, payload) -> None:
        self._ensure_dirs()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _ensure_defaults(self) -> None:
        identity = self._load_identity()
        if not identity:
            self._save_json(self.identity_file, self._create_identity())

        peers = self._load_json(self.peers_file, {})
        if not isinstance(peers, dict):
            self._save_json(self.peers_file, {})

        tokens = self._load_json(self.tokens_file, {})
        if not isinstance(tokens, dict):
            tokens = {}
        tokens.setdefault("used", [])
        self._save_json(self.tokens_file, tokens)

        settings = self._load_json(self.settings_file, {})
        if not isinstance(settings, dict):
            settings = {}
        merged = self._merge_settings(settings)
        if merged != settings:
            self._save_json(self.settings_file, merged)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _now_iso(self) -> str:
        return self._now().isoformat()

    def _parse_iso(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    def _hostname(self) -> str:
        try:
            return os.uname().nodename or "alvaos"
        except Exception:
            return "alvaos"

    def _detect_cmd(self, candidates: List[str], which_name: str) -> Optional[str]:
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        found = shutil.which(which_name)
        return found if found else None

    def _wg_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/wg", "/usr/sbin/wg", "/usr/local/bin/wg", "/bin/wg", "/sbin/wg"],
            "wg"
        )

    def _wg_quick_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/wg-quick", "/usr/sbin/wg-quick", "/usr/local/bin/wg-quick", "/bin/wg-quick", "/sbin/wg-quick"],
            "wg-quick"
        )

    def _ip_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/sbin/ip", "/usr/bin/ip", "/sbin/ip", "/bin/ip"],
            "ip"
        )

    def _bash_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/bash", "/bin/bash"],
            "bash"
        )

    def _derive_tunnel_ip(self, node_id: str) -> str:
        # Deterministic host assignment in a private /24 (less likely to collide with common home LAN ranges).
        digest = hashlib.sha256((node_id or "").encode("utf-8")).digest()
        host_octet = 2 + (digest[0] % 253)  # 2..254
        return f"100.95.95.{host_octet}"

    def _local_ipv4_in_use(self, ip: str) -> bool:
        if platform.system() != "Linux":
            return False
        ip_cmd = self._ip_cmd()
        if not ip_cmd or not ip:
            return False
        res, err = self.run_command([ip_cmd, "-4", "-o", "addr", "show"], timeout=10)
        if err or not res or res.returncode != 0:
            return False
        pattern = re.compile(rf"\b{re.escape(ip)}/\d+\b")
        for raw in (res.stdout or "").splitlines():
            if pattern.search(raw):
                return True
        return False

    def _ipv4_on_interface(self, ip: str, interface: str) -> bool:
        if platform.system() != "Linux":
            return False
        ip_cmd = self._ip_cmd()
        if not ip_cmd or not ip or not interface:
            return False
        res, err = self.run_command([ip_cmd, "-4", "-o", "addr", "show", "dev", interface], timeout=10)
        if err or not res or res.returncode != 0:
            return False
        pattern = re.compile(rf"\b{re.escape(ip)}/\d+\b")
        return bool(pattern.search(res.stdout or ""))

    def _derive_available_tunnel_ip(self, node_id: str, avoid_ip: str = "") -> str:
        digest = hashlib.sha256((node_id or "").encode("utf-8")).digest()
        start = 2 + (digest[0] % 253)
        avoid = str(avoid_ip or "").strip()
        for offset in range(253):
            host_octet = 2 + ((start - 2 + offset) % 253)
            candidate = f"100.95.95.{host_octet}"
            if avoid and candidate == avoid:
                continue
            if not self._local_ipv4_in_use(candidate):
                return candidate
        fallback = f"100.95.95.{start}"
        if avoid and fallback == avoid:
            alt_octet = 2 + ((start - 1) % 253)
            fallback = f"100.95.95.{alt_octet}"
        return fallback

    def _ensure_identity_tunnel_ip(self, identity: Dict, force_rotate: bool = False) -> Tuple[Dict, bool]:
        if not isinstance(identity, dict):
            return identity, False

        current_ip = str(identity.get("tunnel_ip") or "").strip()
        conflict = (
            bool(current_ip)
            and self._local_ipv4_in_use(current_ip)
            and not self._ipv4_on_interface(current_ip, self.interface_name)
        )
        if not (force_rotate or not current_ip or conflict):
            return identity, False

        replacement_ip = self._derive_available_tunnel_ip(
            str(identity.get("node_id") or ""),
            avoid_ip=current_ip if force_rotate else "",
        )
        if replacement_ip == current_ip:
            return identity, False

        identity["tunnel_ip"] = replacement_ip
        if current_ip and (conflict or force_rotate):
            identity["key_error"] = (
                f"Tunnel IP conflict detected for {current_ip}; switched to {replacement_ip}. "
                "Re-pair with buddy if needed."
            )
        self._save_identity(identity)
        return identity, True

    def _is_address_in_use_error(self, error_text: str) -> bool:
        text = str(error_text or "").lower()
        return (
            "address already in use" in text
            or ("rtnetlink answers" in text and "already in use" in text)
        )

    def _normalize_api_endpoint(self, endpoint: str) -> Tuple[Optional[str], Optional[str]]:
        value = str(endpoint or "").strip()
        if not value:
            return "", None
        value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE).strip("/")
        if len(value) > 255:
            return None, "API endpoint is too long"
        if any(ch.isspace() for ch in value):
            return None, "API endpoint must not contain spaces"
        if ":" not in value:
            value = f"{value}:8080"
        return value, None

    def _placeholder_wg_keypair(self) -> Tuple[str, str]:
        private_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        public_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        return private_key, public_key

    def _generate_wg_keypair(self) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
        if platform.system() != "Linux":
            priv, pub = self._placeholder_wg_keypair()
            return priv, pub, None, "placeholder"

        wg_cmd = self._wg_cmd()
        bash_cmd = self._bash_cmd()
        if not wg_cmd:
            priv, pub = self._placeholder_wg_keypair()
            return priv, pub, "WireGuard command not found (wg). Install wireguard-tools to enable the tunnel.", "placeholder"
        if not bash_cmd:
            priv, pub = self._placeholder_wg_keypair()
            return priv, pub, "Bash command not found. Install bash to enable WireGuard key generation.", "placeholder"

        gen_res, gen_err = self.run_command([wg_cmd, "genkey"], timeout=10)
        if gen_err or not gen_res or gen_res.returncode != 0:
            priv, pub = self._placeholder_wg_keypair()
            return priv, pub, gen_err or "Failed to generate WireGuard private key", "placeholder"
        private_key = (gen_res.stdout or "").strip()
        if not private_key:
            priv, pub = self._placeholder_wg_keypair()
            return priv, pub, "WireGuard private key generation returned empty output", "placeholder"

        pub_cmd = f"printf '%s' {shlex.quote(private_key)} | {shlex.quote(wg_cmd)} pubkey"
        pub_res, pub_err = self.run_command([bash_cmd, "-lc", pub_cmd], timeout=10)
        if pub_err or not pub_res or pub_res.returncode != 0:
            priv, pub = self._placeholder_wg_keypair()
            return priv, pub, pub_err or "Failed to derive WireGuard public key", "placeholder"
        public_key = (pub_res.stdout or "").strip()
        if not public_key:
            priv, pub = self._placeholder_wg_keypair()
            return priv, pub, "WireGuard public key generation returned empty output", "placeholder"

        return private_key, public_key, None, "wireguard"

    def _create_identity(self) -> Dict:
        node_id = secrets.token_hex(8)
        private_key, public_key, key_err, key_source = self._generate_wg_keypair()
        identity = {
            "node_id": node_id,
            "name": self._hostname(),
            "private_key": private_key or "",
            "public_key": public_key or "",
            "tunnel_ip": self._derive_tunnel_ip(node_id),
            "listen_port": self.default_listen_port,
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
            "key_error": key_err or "",
            "key_source": key_source,
        }
        return identity

    def _load_identity(self) -> Dict:
        raw = self._load_json(self.identity_file, {})
        if not isinstance(raw, dict):
            return {}
        return raw

    def _save_identity(self, identity: Dict) -> None:
        identity["updated_at"] = self._now_iso()
        self._save_json(self.identity_file, identity)

    def _identity_public(self) -> Dict:
        identity = self._load_identity()
        if not identity:
            identity = self._create_identity()
            self._save_identity(identity)
        else:
            key_source = str(identity.get("key_source") or "").strip()
            if not key_source:
                identity["key_source"] = "wireguard"
                key_source = "wireguard"
                self._save_identity(identity)

            needs_regen = not identity.get("public_key") or not identity.get("private_key")
            can_upgrade_from_placeholder = (
                key_source != "wireguard"
                and platform.system() == "Linux"
                and bool(self._wg_cmd())
            )
            if needs_regen or can_upgrade_from_placeholder:
                private_key, public_key, key_err, new_source = self._generate_wg_keypair()
                if private_key and public_key:
                    identity["private_key"] = private_key
                    identity["public_key"] = public_key
                    identity["key_error"] = key_err or ""
                    identity["key_source"] = new_source
                    self._save_identity(identity)
                else:
                    identity["key_error"] = key_err or identity.get("key_error", "")
                    self._save_identity(identity)

        identity, _ = self._ensure_identity_tunnel_ip(identity)

        return {
            "node_id": identity.get("node_id", ""),
            "name": identity.get("name", ""),
            "public_key": identity.get("public_key", ""),
            "tunnel_ip": identity.get("tunnel_ip", ""),
            "listen_port": identity.get("listen_port", self.default_listen_port),
            "created_at": identity.get("created_at"),
            "updated_at": identity.get("updated_at"),
            "key_error": identity.get("key_error", ""),
            "key_source": identity.get("key_source", "wireguard"),
        }

    def _token_encode(self, payload: Dict) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _token_decode(self, token: str) -> Tuple[Optional[Dict], Optional[str]]:
        text = str(token or "").strip()
        if not text:
            return None, "Pairing token is required"
        try:
            padded = text + ("=" * ((4 - len(text) % 4) % 4))
            decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            payload = json.loads(decoded)
            if not isinstance(payload, dict):
                return None, "Invalid pairing token payload"
            return payload, None
        except Exception:
            return None, "Invalid pairing token format"

    def _normalize_endpoint(self, endpoint: str) -> Tuple[Optional[str], Optional[str]]:
        value = str(endpoint or "").strip()
        if not value:
            return "", None
        if len(value) > 255:
            return None, "Endpoint is too long"
        if any(ch.isspace() for ch in value):
            return None, "Endpoint must not contain spaces"
        if ":" not in value:
            return None, "Endpoint must include host:port"
        return value, None

    def _load_peers(self) -> Dict[str, Dict]:
        data = self._load_json(self.peers_file, {})
        return data if isinstance(data, dict) else {}

    def _save_peers(self, peers: Dict[str, Dict]) -> None:
        self._save_json(self.peers_file, peers if isinstance(peers, dict) else {})

    def _hash_passphrase(self, passphrase: str, salt: Optional[str] = None) -> Tuple[str, str]:
        if salt:
            salt_bytes = base64.b64decode(salt.encode("ascii"))
        else:
            salt_bytes = secrets.token_bytes(16)
            salt = base64.b64encode(salt_bytes).decode("ascii")
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(passphrase or "").encode("utf-8"),
            salt_bytes,
            160000,
        )
        digest_b64 = base64.b64encode(digest).decode("ascii")
        return digest_b64, salt

    def _normalize_time_hhmm(self, value: str, default: str = "02:00") -> str:
        text = str(value or "").strip()
        if not re.match(r"^\d{2}:\d{2}$", text):
            return default
        hh = int(text[:2])
        mm = int(text[3:5])
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            return default
        return f"{hh:02d}:{mm:02d}"

    def _normalize_peer_policies(self, raw: Optional[Dict]) -> Dict[str, Dict]:
        policies = raw if isinstance(raw, dict) else {}
        normalized: Dict[str, Dict] = {}
        for node_id, payload in policies.items():
            nid = str(node_id or "").strip()
            if not nid:
                continue
            item = payload if isinstance(payload, dict) else {}

            try:
                interval = int(item.get("interval_minutes", DEFAULT_BUDDY_SETTINGS["interval_minutes"]))
            except Exception:
                interval = int(DEFAULT_BUDDY_SETTINGS["interval_minutes"])
            interval = max(15, min(43200, interval))

            try:
                quota = int(item.get("max_storage_gb", DEFAULT_BUDDY_SETTINGS["incoming_quota_gb"]))
            except Exception:
                quota = int(DEFAULT_BUDDY_SETTINGS["incoming_quota_gb"])
            quota = max(1, min(20000, quota))

            sources_raw = item.get("outgoing_sources", [])
            unique_sources = []
            seen = set()
            if isinstance(sources_raw, list):
                for source in sources_raw:
                    path = str(source or "").strip()
                    if not path.startswith("/") or path in seen:
                        continue
                    seen.add(path)
                    unique_sources.append(path)

            normalized[nid] = {
                "enabled": bool(item.get("enabled", True)),
                "interval_minutes": interval,
                "send_time": self._normalize_time_hhmm(item.get("send_time", "02:00")),
                "max_storage_gb": quota,
                "outgoing_sources": unique_sources,
                "updated_at": str(item.get("updated_at", "") or "").strip() or self._now_iso(),
            }
        return normalized

    def _merge_settings(self, raw: Optional[Dict]) -> Dict:
        source = raw if isinstance(raw, dict) else {}
        merged = dict(DEFAULT_BUDDY_SETTINGS)

        merged["enabled"] = bool(source.get("enabled", merged["enabled"]))

        incoming_path = str(source.get("incoming_path", merged["incoming_path"]) or "").strip()
        if not incoming_path.startswith("/"):
            incoming_path = DEFAULT_BUDDY_SETTINGS["incoming_path"]
        merged["incoming_path"] = incoming_path
        try:
            incoming_quota = int(source.get("incoming_quota_gb", merged["incoming_quota_gb"]))
        except Exception:
            incoming_quota = int(DEFAULT_BUDDY_SETTINGS["incoming_quota_gb"])
        merged["incoming_quota_gb"] = max(1, min(20000, incoming_quota))

        outgoing = source.get("outgoing_sources", [])
        unique_sources = []
        seen = set()
        if isinstance(outgoing, list):
            for entry in outgoing:
                path = str(entry or "").strip()
                if not path.startswith("/") or path in seen:
                    continue
                seen.add(path)
                unique_sources.append(path)
        merged["outgoing_sources"] = unique_sources

        try:
            interval = int(source.get("interval_minutes", merged["interval_minutes"]))
        except Exception:
            interval = int(DEFAULT_BUDDY_SETTINGS["interval_minutes"])
        merged["interval_minutes"] = max(15, min(43200, interval))

        try:
            keep_last = int(source.get("keep_last", merged["keep_last"]))
        except Exception:
            keep_last = int(DEFAULT_BUDDY_SETTINGS["keep_last"])
        merged["keep_last"] = max(1, min(500, keep_last))

        # Recursive retention is mandatory by design.
        merged["recursive_retention"] = True
        merged["peer_policies"] = self._normalize_peer_policies(source.get("peer_policies", {}))

        merged["encryption_enabled"] = bool(source.get("encryption_enabled", False))
        merged["encryption_salt"] = str(source.get("encryption_salt", "") or "").strip()
        merged["encryption_hash"] = str(source.get("encryption_hash", "") or "").strip()

        updated_at = str(source.get("updated_at", "") or "").strip()
        merged["updated_at"] = updated_at or self._now_iso()
        return merged

    def _public_settings(self, settings: Dict) -> Dict:
        result = dict(settings or {})
        result.pop("encryption_hash", None)
        result.pop("encryption_salt", None)
        result["encryption_password_set"] = bool(
            settings and settings.get("encryption_enabled") and settings.get("encryption_hash")
        )
        return result

    def get_settings(self, include_secret: bool = False) -> Dict:
        settings = self._load_json(self.settings_file, {})
        merged = self._merge_settings(settings)
        if merged != settings:
            self._save_json(self.settings_file, merged)
        return merged if include_secret else self._public_settings(merged)

    def save_settings(self, payload: Dict) -> Tuple[bool, Dict]:
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return False, {"error": "Invalid buddy settings payload"}

        current = self.get_settings(include_secret=True)
        candidate = dict(current)
        for field in (
            "enabled",
            "incoming_path",
            "incoming_quota_gb",
            "outgoing_sources",
            "interval_minutes",
            "keep_last",
            "recursive_retention",
            "peer_policies",
            "encryption_enabled",
        ):
            if field in payload:
                candidate[field] = payload.get(field)

        password_supplied = "encryption_password" in payload
        encryption_password = str(payload.get("encryption_password", "") or "")
        if password_supplied and encryption_password:
            digest, salt = self._hash_passphrase(encryption_password)
            candidate["encryption_hash"] = digest
            candidate["encryption_salt"] = salt

        if candidate.get("encryption_enabled"):
            if not str(candidate.get("encryption_hash", "") or "").strip():
                return False, {"error": "Encryption password is required when encryption is enabled"}
        else:
            candidate["encryption_hash"] = ""
            candidate["encryption_salt"] = ""

        merged = self._merge_settings(candidate)
        merged["updated_at"] = self._now_iso()
        self._save_json(self.settings_file, merged)
        return True, self._public_settings(merged)

    def save_peer_policy(self, node_id: str, payload: Dict) -> Tuple[bool, Dict]:
        nid = str(node_id or "").strip()
        if not nid:
            return False, {"error": "node_id is required"}
        peers = self._load_peers()
        if nid not in peers:
            return False, {"error": "Peer not found"}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return False, {"error": "Invalid peer policy payload"}

        settings = self.get_settings(include_secret=True)
        policies = dict(settings.get("peer_policies", {}))
        existing = policies.get(nid, {})
        candidate = dict(existing if isinstance(existing, dict) else {})
        for field in ("enabled", "interval_minutes", "send_time", "max_storage_gb", "outgoing_sources"):
            if field in payload:
                candidate[field] = payload.get(field)
        candidate["updated_at"] = self._now_iso()

        normalized = self._normalize_peer_policies({nid: candidate})
        policies[nid] = normalized.get(nid, {
            "enabled": True,
            "interval_minutes": int(DEFAULT_BUDDY_SETTINGS["interval_minutes"]),
            "send_time": "02:00",
            "max_storage_gb": int(DEFAULT_BUDDY_SETTINGS["incoming_quota_gb"]),
            "outgoing_sources": [],
            "updated_at": self._now_iso(),
        })
        settings["peer_policies"] = policies
        merged = self._merge_settings(settings)
        merged["updated_at"] = self._now_iso()
        self._save_json(self.settings_file, merged)
        return True, merged["peer_policies"].get(nid, {})

    def get_peer_policy(self, node_id: str) -> Tuple[bool, Dict]:
        nid = str(node_id or "").strip()
        if not nid:
            return False, {"error": "node_id is required"}
        peers = self._load_peers()
        if nid not in peers:
            return False, {"error": "Peer not found"}
        settings = self.get_settings(include_secret=True)
        policies = settings.get("peer_policies", {})
        policy = self._normalize_peer_policies({nid: policies.get(nid, {})}).get(nid, {
            "enabled": True,
            "interval_minutes": int(settings.get("interval_minutes", DEFAULT_BUDDY_SETTINGS["interval_minutes"])),
            "send_time": "02:00",
            "max_storage_gb": int(settings.get("incoming_quota_gb", DEFAULT_BUDDY_SETTINGS["incoming_quota_gb"])),
            "outgoing_sources": [],
            "updated_at": self._now_iso(),
        })
        return True, policy

    def requires_encryption_passphrase(self) -> bool:
        settings = self.get_settings(include_secret=True)
        return bool(settings.get("encryption_enabled") and settings.get("encryption_hash") and settings.get("encryption_salt"))

    def verify_encryption_passphrase(self, passphrase: str) -> bool:
        settings = self.get_settings(include_secret=True)
        if not (settings.get("encryption_enabled") and settings.get("encryption_hash") and settings.get("encryption_salt")):
            return True
        try:
            digest, _ = self._hash_passphrase(passphrase, salt=str(settings.get("encryption_salt")))
            return secrets.compare_digest(digest, str(settings.get("encryption_hash")))
        except Exception:
            return False

    def _token_hash(self, token: str) -> str:
        return hashlib.sha256((token or "").encode("utf-8")).hexdigest()

    def _is_token_used(self, token: str) -> bool:
        token_hash = self._token_hash(token)
        state = self._load_json(self.tokens_file, {"used": []})
        used = state.get("used", [])
        if not isinstance(used, list):
            return False
        return any(entry.get("hash") == token_hash for entry in used if isinstance(entry, dict))

    def _mark_token_used(self, token: str) -> None:
        state = self._load_json(self.tokens_file, {"used": []})
        used = state.get("used", [])
        if not isinstance(used, list):
            used = []
        token_hash = self._token_hash(token)
        used.append({
            "hash": token_hash,
            "used_at": self._now_iso(),
        })
        # Keep only most recent entries.
        used = used[-1000:]
        state["used"] = used
        self._save_json(self.tokens_file, state)

    def generate_pairing_token(
        self,
        endpoint: str = "",
        expires_minutes: int = 20,
        api_endpoint: str = "",
    ) -> Tuple[bool, Dict]:
        identity = self._identity_public()
        if not identity.get("public_key"):
            return False, {"error": identity.get("key_error") or "WireGuard identity is not ready"}

        try:
            ttl = int(expires_minutes)
        except Exception:
            ttl = 20
        if ttl < 0:
            ttl = 20
        ttl = min(ttl, 43200)

        normalized_endpoint, endpoint_err = self._normalize_endpoint(endpoint)
        if endpoint_err:
            return False, {"error": endpoint_err}
        normalized_api_endpoint, api_err = self._normalize_api_endpoint(api_endpoint)
        if api_err:
            return False, {"error": api_err}

        issued_at = self._now()
        expires_at = None if ttl == 0 else (issued_at + timedelta(minutes=ttl))
        payload = {
            "v": 1,
            "node_id": identity.get("node_id"),
            "name": identity.get("name"),
            "public_key": identity.get("public_key"),
            "tunnel_ip": identity.get("tunnel_ip"),
            "listen_port": identity.get("listen_port", self.default_listen_port),
            "endpoint": normalized_endpoint,
            "api_endpoint": normalized_api_endpoint,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else "",
            "nonce": secrets.token_hex(8),
        }
        token = self._token_encode(payload)
        return True, {
            "token": token,
            "expires_at": payload["expires_at"],
            "expires_mode": "until_used" if ttl == 0 else "time_limited",
            "identity": identity,
        }

    def _token_to_peer(self, payload: Dict, endpoint_override: str = "", name_override: str = "") -> Tuple[Optional[Dict], Optional[str]]:
        required = ["node_id", "public_key", "tunnel_ip"]
        for field in required:
            if not payload.get(field):
                return None, f"Invalid pairing token: missing {field}"

        expires_raw = payload.get("expires_at")
        if expires_raw:
            expires_at = self._parse_iso(expires_raw)
            if not expires_at:
                return None, "Invalid pairing token: invalid expires_at"
            if self._now() > expires_at:
                return None, "Pairing token expired"

        local = self._identity_public()
        if payload.get("node_id") == local.get("node_id"):
            return None, "Cannot pair with the same system"

        endpoint_candidate = endpoint_override if str(endpoint_override or "").strip() else payload.get("endpoint", "")
        endpoint, endpoint_err = self._normalize_endpoint(endpoint_candidate)
        if endpoint_err:
            return None, endpoint_err
        api_endpoint, api_err = self._normalize_api_endpoint(payload.get("api_endpoint", ""))
        if api_err:
            return None, api_err

        peer_name = str(name_override or payload.get("name") or payload.get("node_id")).strip()
        if not peer_name:
            peer_name = str(payload.get("node_id"))

        peer = {
            "node_id": str(payload.get("node_id")),
            "name": peer_name,
            "public_key": str(payload.get("public_key")).strip(),
            "tunnel_ip": str(payload.get("tunnel_ip")).strip(),
            "listen_port": int(payload.get("listen_port") or self.default_listen_port),
            "endpoint": endpoint,
            "api_endpoint": api_endpoint,
            "last_paired_at": self._now_iso(),
            "status": "configured" if endpoint else "pending_endpoint",
            "last_error": "",
        }
        return peer, None

    def _render_wg_config(self, identity: Dict, peers: Dict[str, Dict]) -> str:
        lines = [
            "[Interface]",
            f"PrivateKey = {identity.get('private_key', '')}",
            f"Address = {identity.get('tunnel_ip', '')}/24",
            f"ListenPort = {int(identity.get('listen_port') or self.default_listen_port)}",
            "",
        ]

        for node_id in sorted(peers.keys()):
            peer = peers.get(node_id, {})
            endpoint = str(peer.get("endpoint") or "").strip()
            public_key = str(peer.get("public_key") or "").strip()
            tunnel_ip = str(peer.get("tunnel_ip") or "").strip()
            if not endpoint or not public_key or not tunnel_ip:
                continue
            lines.extend([
                "[Peer]",
                f"PublicKey = {public_key}",
                f"AllowedIPs = {tunnel_ip}/32",
                f"Endpoint = {endpoint}",
                "PersistentKeepalive = 25",
                "",
            ])

        return "\n".join(lines).strip() + "\n"

    def apply_tunnel_config(self) -> Tuple[bool, Dict]:
        if platform.system() != "Linux":
            return True, {"message": "Tunnel config is mocked on non-Linux systems"}

        identity = self._load_identity()
        if not identity.get("private_key"):
            return False, {"error": identity.get("key_error") or "Missing WireGuard private key"}
        if str(identity.get("key_source") or "wireguard") != "wireguard":
            return False, {"error": "WireGuard is not fully configured. Install wireguard-tools and regenerate pairing identity."}

        wg_cmd = self._wg_cmd()
        wg_quick_cmd = self._wg_quick_cmd()
        if not wg_cmd or not wg_quick_cmd:
            return False, {"error": "WireGuard tools (wg/wg-quick) are not installed"}

        peers = self._load_peers()
        identity, _ = self._ensure_identity_tunnel_ip(identity)

        active_peers = [
            p for p in peers.values()
            if str(p.get("endpoint") or "").strip() and str(p.get("public_key") or "").strip()
        ]
        if not active_peers:
            return True, {"message": "No active peers configured"}

        conflict_note = ""
        for attempt in range(2):
            config_text = self._render_wg_config(identity, peers)
            Path(self.wg_dir).mkdir(parents=True, exist_ok=True)
            with open(self.wg_config_path, "w", encoding="utf-8") as f:
                f.write(config_text)
            try:
                os.chmod(self.wg_config_path, 0o600)
            except Exception:
                pass

            # Try to cleanly restart interface. Ignore "down" errors.
            self.run_command([wg_quick_cmd, "down", self.wg_config_path], timeout=20)

            up_res, up_err = self.run_command([wg_quick_cmd, "up", self.wg_config_path], timeout=40)
            if not up_err and up_res and up_res.returncode == 0:
                break

            if attempt == 0 and self._is_address_in_use_error(up_err or ""):
                old_ip = str(identity.get("tunnel_ip") or "").strip()
                identity, changed = self._ensure_identity_tunnel_ip(identity, force_rotate=True)
                if changed:
                    conflict_note = f"Tunnel IP conflict resolved automatically: {old_ip} -> {identity.get('tunnel_ip', '')}"
                    continue
            return False, {"error": up_err or "Failed to bring up WireGuard interface"}

        show_res, show_err = self.run_command([wg_cmd, "show", self.interface_name], timeout=10)
        if show_err:
            payload = {"message": "Tunnel configured, but runtime status unavailable", "warning": show_err}
            if conflict_note:
                payload["ip_update"] = conflict_note
            return True, payload
        payload = {"message": "Tunnel configured", "runtime": (show_res.stdout or "").strip()[:1200]}
        if conflict_note:
            payload["ip_update"] = conflict_note
        return True, payload

    def _runtime_status(self) -> Dict:
        if platform.system() != "Linux":
            return {"state": "mock", "message": "WireGuard runtime status is mocked on non-Linux"}

        wg_cmd = self._wg_cmd()
        if not wg_cmd:
            return {"state": "unsupported", "message": "WireGuard command not found"}

        res, err = self.run_command([wg_cmd, "show", self.interface_name], timeout=10)
        if err or not res or res.returncode != 0:
            return {"state": "down", "message": err or "Tunnel is down"}

        peers = []
        current_peer = None
        for raw in (res.stdout or "").splitlines():
            line = raw.strip()
            if line.startswith("peer:"):
                if current_peer:
                    peers.append(current_peer)
                current_peer = {"public_key": line.split("peer:", 1)[1].strip()}
            elif current_peer and line.startswith("endpoint:"):
                current_peer["endpoint"] = line.split("endpoint:", 1)[1].strip()
            elif current_peer and line.startswith("latest handshake:"):
                current_peer["latest_handshake"] = line.split("latest handshake:", 1)[1].strip()
        if current_peer:
            peers.append(current_peer)

        return {
            "state": "up",
            "message": "Tunnel is up",
            "peers": peers,
        }

    def _attempt_reciprocal_pair(
        self,
        remote_api_endpoint: str,
        local_wg_endpoint: str = "",
        local_api_endpoint: str = "",
    ) -> Tuple[bool, Dict]:
        normalized_remote, remote_err = self._normalize_api_endpoint(remote_api_endpoint)
        if remote_err or not normalized_remote:
            return False, {"error": remote_err or "Remote API endpoint missing"}

        identity = self._identity_public()
        if not identity.get("public_key"):
            return False, {"error": identity.get("key_error") or "Local identity is not ready"}

        normalized_local_wg, wg_err = self._normalize_endpoint(local_wg_endpoint)
        if wg_err:
            normalized_local_wg = ""

        normalized_local_api, _ = self._normalize_api_endpoint(local_api_endpoint)
        issued_at = self._now()
        expires_at = issued_at + timedelta(minutes=20)
        payload = {
            "v": 1,
            "node_id": identity.get("node_id"),
            "name": identity.get("name"),
            "public_key": identity.get("public_key"),
            "tunnel_ip": identity.get("tunnel_ip"),
            "listen_port": identity.get("listen_port", self.default_listen_port),
            "endpoint": normalized_local_wg,
            "api_endpoint": normalized_local_api,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "nonce": secrets.token_hex(8),
        }
        token = self._token_encode(payload)
        req_payload = json.dumps({"token": token}).encode("utf-8")

        urls = [
            f"http://{normalized_remote}/api/v1/backup/pairing/accept",
            f"https://{normalized_remote}/api/v1/backup/pairing/accept",
        ]
        last_error = "Reciprocal pairing failed"
        for url in urls:
            request_obj = urllib.request.Request(
                url,
                data=req_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request_obj, timeout=8) as response:
                    text = response.read().decode("utf-8", errors="replace")
                    try:
                        data = json.loads(text) if text else {}
                    except Exception:
                        data = {}
                    if int(response.status) >= 200 and int(response.status) < 300 and not data.get("error"):
                        return True, {
                            "message": "Reciprocal pairing completed",
                            "remote_api_endpoint": normalized_remote,
                        }
                    last_error = data.get("error") or f"HTTP {response.status}"
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                    parsed = json.loads(body) if body else {}
                    last_error = parsed.get("error") or f"HTTP {exc.code}"
                except Exception:
                    last_error = f"HTTP {exc.code}"
            except Exception as exc:
                last_error = str(exc)
        return False, {
            "error": last_error,
            "remote_api_endpoint": normalized_remote,
        }

    def validate_pairing_token(
        self,
        token: str,
        endpoint_override: str = "",
        name_override: str = "",
        auto_reciprocal: bool = False,
        local_wg_endpoint: str = "",
        local_api_endpoint: str = "",
    ) -> Tuple[bool, Dict]:
        payload, decode_err = self._token_decode(token)
        if decode_err:
            return False, {"error": decode_err}

        if self._is_token_used(token):
            return False, {"error": "This pairing token was already used"}

        peer, peer_err = self._token_to_peer(payload, endpoint_override=endpoint_override, name_override=name_override)
        if peer_err:
            return False, {"error": peer_err}

        peers = self._load_peers()
        peers[peer["node_id"]] = peer
        self._save_peers(peers)
        self._mark_token_used(token)

        settings = self.get_settings(include_secret=True)
        policies = settings.get("peer_policies", {})
        if not isinstance(policies, dict):
            policies = {}
        if peer["node_id"] not in policies:
            policies[peer["node_id"]] = {
                "enabled": True,
                "interval_minutes": int(settings.get("interval_minutes", DEFAULT_BUDDY_SETTINGS["interval_minutes"])),
                "send_time": "02:00",
                "max_storage_gb": int(settings.get("incoming_quota_gb", DEFAULT_BUDDY_SETTINGS["incoming_quota_gb"])),
                "outgoing_sources": [],
                "updated_at": self._now_iso(),
            }
            settings["peer_policies"] = policies
            settings["updated_at"] = self._now_iso()
            self._save_json(self.settings_file, self._merge_settings(settings))

        tunnel_result = {"message": "Peer saved (endpoint missing). Add endpoint to activate tunnel."}
        if peer.get("endpoint"):
            ok, tunnel_result = self.apply_tunnel_config()
            peers = self._load_peers()
            current = peers.get(peer["node_id"], peer)
            if ok:
                current["status"] = "configured"
                current["last_error"] = ""
            else:
                current["status"] = "error"
                current["last_error"] = tunnel_result.get("error", "Failed to apply tunnel config")
            peers[peer["node_id"]] = current
            self._save_peers(peers)
            peer = current

        reciprocal = {"skipped": True}
        if auto_reciprocal:
            remote_api_endpoint = str(peer.get("api_endpoint") or "").strip()
            if remote_api_endpoint:
                reciprocal_ok, reciprocal_payload = self._attempt_reciprocal_pair(
                    remote_api_endpoint=remote_api_endpoint,
                    local_wg_endpoint=local_wg_endpoint,
                    local_api_endpoint=local_api_endpoint,
                )
                reciprocal = {"success": reciprocal_ok, **reciprocal_payload}
            else:
                reciprocal = {"skipped": True, "reason": "Remote API endpoint is not available in token"}

        return True, {
            "peer": peer,
            "tunnel_result": tunnel_result,
            "reciprocal": reciprocal,
        }

    def remove_peer(self, node_id: str) -> Tuple[bool, Dict]:
        target = str(node_id or "").strip()
        if not target:
            return False, {"error": "node_id is required"}
        peers = self._load_peers()
        if target not in peers:
            return False, {"error": "Peer not found"}
        removed = peers.pop(target)
        self._save_peers(peers)

        settings = self.get_settings(include_secret=True)
        policies = settings.get("peer_policies", {})
        if isinstance(policies, dict) and target in policies:
            policies.pop(target, None)
            settings["peer_policies"] = policies
            settings["updated_at"] = self._now_iso()
            self._save_json(self.settings_file, self._merge_settings(settings))

        ok, tunnel_result = self.apply_tunnel_config()
        if not ok:
            err = str(tunnel_result.get("error", "")).lower()
            if "wireguard" in err or "wg" in err:
                return True, {
                    "removed": removed,
                    "tunnel_result": {
                        "message": "Peer removed. Tunnel update deferred until WireGuard is available.",
                        "warning": tunnel_result.get("error"),
                    }
                }
            return False, {"error": tunnel_result.get("error", "Peer removed, but tunnel reconfigure failed")}
        return True, {"removed": removed, "tunnel_result": tunnel_result}

    def restart_tunnel(self) -> Tuple[bool, Dict]:
        return self.apply_tunnel_config()

    def get_status(self) -> Dict:
        identity = self._identity_public()
        peers_map = self._load_peers()
        settings_full = self.get_settings(include_secret=True)
        policies = settings_full.get("peer_policies", {})
        peers = []
        for peer in peers_map.values():
            item = dict(peer if isinstance(peer, dict) else {})
            node_id = str(item.get("node_id") or "").strip()
            policy = self._normalize_peer_policies({node_id: policies.get(node_id, {})}).get(node_id, {})
            if not policy:
                policy = {
                    "enabled": True,
                    "interval_minutes": int(settings_full.get("interval_minutes", DEFAULT_BUDDY_SETTINGS["interval_minutes"])),
                    "send_time": "02:00",
                    "max_storage_gb": int(settings_full.get("incoming_quota_gb", DEFAULT_BUDDY_SETTINGS["incoming_quota_gb"])),
                    "outgoing_sources": [],
                    "updated_at": self._now_iso(),
                }
            item["policy"] = policy
            peers.append(item)
        peers = sorted(peers, key=lambda item: str(item.get("name", "")).lower())
        runtime = self._runtime_status()
        settings = self._public_settings(settings_full)
        wg_cmd = self._wg_cmd()
        wg_quick_cmd = self._wg_quick_cmd()
        key_source = str(identity.get("key_source") or "wireguard")
        supported = platform.system() == "Linux" and bool(wg_cmd and wg_quick_cmd and identity.get("public_key")) and key_source == "wireguard"

        return {
            "supported": supported,
            "identity": identity,
            "peers": peers,
            "tunnel": runtime,
            "settings": settings,
            "mode": "configuration-only",
            "requirements": {
                "linux": platform.system() == "Linux",
                "wg_cmd": wg_cmd or "",
                "wg_quick_cmd": wg_quick_cmd or "",
                "wireguard_identity": key_source,
            },
        }
