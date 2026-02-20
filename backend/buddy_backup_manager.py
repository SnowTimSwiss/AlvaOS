#!/usr/bin/env python3
"""
AlvaOS Buddy Backup Manager (v0.7.0)
Identity + token pairing + WireGuard tunnel automation.
"""

import base64
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

DEFAULT_BUDDY_SETTINGS = {
    "enabled": False,
    "incoming_path": "",
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

DEFAULT_BUDDY_RUNTIME = {
    "last_sync_at": "",
    "last_sync_status": "idle",
    "last_sync_error": "",
    "last_restore_at": "",
    "last_restore_status": "idle",
    "last_restore_error": "",
    "per_peer": {},
}


class BuddyBackupManager:
    def __init__(self, run_command: Callable):
        self.run_command = run_command
        self.state_dir = self._resolve_state_dir()
        self.identity_file = os.path.join(self.state_dir, "buddy_identity.json")
        self.peers_file = os.path.join(self.state_dir, "buddy_peers.json")
        self.tokens_file = os.path.join(self.state_dir, "buddy_tokens.json")
        self.settings_file = os.path.join(self.state_dir, "buddy_settings.json")
        self.streams_file = os.path.join(self.state_dir, "buddy_streams.json")
        self.runtime_file = os.path.join(self.state_dir, "buddy_runtime.json")
        self.wg_dir = os.path.join(self.state_dir, "wireguard")
        self.wg_config_path = os.path.join(self.wg_dir, "buddy0.conf")
        self.interface_name = "buddy0"
        self.default_listen_port = 51820
        self.connected_handshake_threshold_seconds = 180
        self._transfer_lock = threading.RLock()

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

        streams = self._load_json(self.streams_file, [])
        if not isinstance(streams, list):
            streams = []
        self._save_json(self.streams_file, streams)

        runtime = self._load_json(self.runtime_file, {})
        if not isinstance(runtime, dict):
            runtime = {}
        merged_runtime = dict(DEFAULT_BUDDY_RUNTIME)
        merged_runtime.update(runtime)
        if not isinstance(merged_runtime.get("per_peer"), dict):
            merged_runtime["per_peer"] = {}
        self._save_json(self.runtime_file, merged_runtime)

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

    def _normalize_path(self, path: Optional[str]) -> str:
        if not path:
            return ""
        return os.path.normpath(str(path).strip())

    def _existing_probe_path(self, path: str) -> str:
        probe = self._normalize_path(path)
        if not probe:
            return "/"
        while probe != "/" and not os.path.exists(probe):
            next_probe = os.path.dirname(probe)
            if next_probe == probe:
                break
            probe = next_probe
        return probe if probe else "/"

    def _path_on_system_disk(self, path: str) -> bool:
        if platform.system() != "Linux":
            return False
        target = self._normalize_path(path)
        if not target:
            return False
        try:
            probe = os.path.realpath(self._existing_probe_path(target))
            root = os.path.realpath("/")
            return os.stat(probe).st_dev == os.stat(root).st_dev
        except Exception:
            return False

    def _sanitize_non_system_path(self, path: Optional[str]) -> str:
        normalized = self._normalize_path(path)
        if not normalized.startswith("/"):
            return ""
        if self._path_on_system_disk(normalized):
            return ""
        return normalized

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

    def _ping_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/ping", "/bin/ping", "/usr/sbin/ping", "/sbin/ping"],
            "ping"
        )

    def _btrfs_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/btrfs", "/bin/btrfs", "/usr/sbin/btrfs", "/sbin/btrfs"],
            "btrfs"
        )

    def _mkdir_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/mkdir", "/bin/mkdir"],
            "mkdir"
        )

    def _mv_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/mv", "/bin/mv"],
            "mv"
        )

    def _chown_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/chown", "/bin/chown", "/usr/sbin/chown", "/sbin/chown"],
            "chown"
        )

    def _chmod_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/chmod", "/bin/chmod", "/usr/sbin/chmod", "/sbin/chmod"],
            "chmod"
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

    def _udp_port_in_use(self, port: int) -> bool:
        try:
            candidate = int(port)
        except Exception:
            return False
        if candidate < 1 or candidate > 65535:
            return False

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", candidate))
            return False
        except OSError as exc:
            code = getattr(exc, "errno", None)
            if code in (98, 10048):
                return True
            return "address already in use" in str(exc).lower()
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _derive_available_listen_port(self, preferred_port: int, avoid_port: int = 0) -> int:
        try:
            preferred = int(preferred_port)
        except Exception:
            preferred = self.default_listen_port
        preferred = max(1024, min(65535, preferred))
        avoid = int(avoid_port) if str(avoid_port or "").strip().isdigit() else 0
        span = 65535 - 1024 + 1
        for offset in range(span):
            candidate = 1024 + ((preferred - 1024 + offset) % span)
            if avoid and candidate == avoid:
                continue
            if not self._udp_port_in_use(candidate):
                return candidate
        return self.default_listen_port

    def _ensure_identity_listen_port(self, identity: Dict, force_rotate: bool = False) -> Tuple[Dict, bool]:
        if not isinstance(identity, dict):
            return identity, False

        try:
            current_port = int(identity.get("listen_port") or self.default_listen_port)
        except Exception:
            current_port = self.default_listen_port
        current_port = max(1024, min(65535, current_port))
        current_in_use = self._udp_port_in_use(current_port)

        if not (force_rotate or current_in_use):
            if identity.get("listen_port") != current_port:
                identity["listen_port"] = current_port
                self._save_identity(identity)
                return identity, True
            return identity, False

        replacement_port = self._derive_available_listen_port(
            current_port,
            avoid_port=current_port if force_rotate else 0,
        )
        if replacement_port == current_port:
            return identity, False

        identity["listen_port"] = replacement_port
        if current_in_use or force_rotate:
            identity["key_error"] = (
                f"WireGuard listen port conflict detected for {current_port}; switched to {replacement_port}. "
                "If your endpoint includes the old port, regenerate/share a fresh pairing token."
            )
        self._save_identity(identity)
        return identity, True

    def _parse_handshake_age_seconds(self, value: str) -> Optional[int]:
        text = str(value or "").strip().lower()
        if not text or text == "never":
            return None
        if text == "now":
            return 0
        if text.endswith("ago"):
            text = text[:-3].strip()

        total = 0
        matched = False
        for amount_raw, unit in re.findall(r"(\d+)\s*(second|seconds|minute|minutes|hour|hours|day|days)", text):
            try:
                amount = int(amount_raw)
            except Exception:
                continue
            matched = True
            if unit.startswith("second"):
                total += amount
            elif unit.startswith("minute"):
                total += amount * 60
            elif unit.startswith("hour"):
                total += amount * 3600
            elif unit.startswith("day"):
                total += amount * 86400
        if not matched:
            return None
        return total

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
            "api_secret": secrets.token_urlsafe(32),
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

            if not str(identity.get("api_secret") or "").strip():
                identity["api_secret"] = secrets.token_urlsafe(32)
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
                    path = self._sanitize_non_system_path(source)
                    if not path or path in seen:
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

        merged["incoming_path"] = self._sanitize_non_system_path(
            source.get("incoming_path", merged["incoming_path"])
        )
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
                path = self._sanitize_non_system_path(entry)
                if not path or path in seen:
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

    def _load_runtime(self) -> Dict:
        runtime = self._load_json(self.runtime_file, {})
        if not isinstance(runtime, dict):
            runtime = {}
        merged = dict(DEFAULT_BUDDY_RUNTIME)
        merged.update(runtime)
        if not isinstance(merged.get("per_peer"), dict):
            merged["per_peer"] = {}
        return merged

    def _save_runtime(self, runtime: Dict) -> Dict:
        payload = dict(DEFAULT_BUDDY_RUNTIME)
        if isinstance(runtime, dict):
            payload.update(runtime)
        if not isinstance(payload.get("per_peer"), dict):
            payload["per_peer"] = {}
        self._save_json(self.runtime_file, payload)
        return payload

    def _update_runtime(self, updates: Dict, node_id: str = "") -> Dict:
        runtime = self._load_runtime()
        runtime.update(updates or {})
        if node_id:
            per_peer = runtime.get("per_peer", {})
            if not isinstance(per_peer, dict):
                per_peer = {}
            peer_runtime = per_peer.get(node_id, {})
            if not isinstance(peer_runtime, dict):
                peer_runtime = {}
            peer_updates = updates.get("peer", {}) if isinstance(updates, dict) else {}
            if isinstance(peer_updates, dict):
                peer_runtime.update(peer_updates)
            per_peer[node_id] = peer_runtime
            runtime["per_peer"] = per_peer
        return self._save_runtime(runtime)

    def _load_stream_entries(self) -> List[Dict]:
        entries = self._load_json(self.streams_file, [])
        return entries if isinstance(entries, list) else []

    def _save_stream_entries(self, entries: List[Dict]) -> None:
        payload = entries if isinstance(entries, list) else []
        if len(payload) > 4000:
            payload = payload[-4000:]
        self._save_json(self.streams_file, payload)

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
        if merged.get("enabled") and not str(merged.get("incoming_path") or "").strip():
            return False, {"error": "Local incoming path is required and must be on a non-system disk"}
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

    def _identity_private(self) -> Dict:
        identity = self._load_identity()
        if not identity:
            identity = self._create_identity()
            self._save_identity(identity)
        if not str(identity.get("api_secret") or "").strip():
            identity["api_secret"] = secrets.token_urlsafe(32)
            self._save_identity(identity)
        return identity

    def verify_buddy_api_secret(self, secret: str) -> bool:
        provided = str(secret or "").strip()
        if not provided:
            return False
        identity = self._identity_private()
        expected = str(identity.get("api_secret") or "").strip()
        return bool(expected) and hmac.compare_digest(provided, expected)

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

    def _path_is_btrfs_subvolume(self, path: str) -> bool:
        if platform.system() != "Linux":
            return True
        btrfs_cmd = self._btrfs_cmd()
        target = str(path or "").strip()
        if not btrfs_cmd or not target:
            return False
        res, err = self.run_command([btrfs_cmd, "subvolume", "show", target], timeout=20)
        return bool(res and res.returncode == 0 and not err)

    def _list_btrfs_subvolume_names_under(self, path: str) -> List[str]:
        base = str(path or "").strip()
        if platform.system() != "Linux" or not base:
            return []
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return []

        res, err = self.run_command([btrfs_cmd, "subvolume", "list", "-o", base], timeout=30)
        if err or not res or res.returncode != 0:
            return []

        names: List[str] = []
        for raw in (res.stdout or "").splitlines():
            line = str(raw or "").strip()
            if not line:
                continue
            marker = " path "
            idx = line.find(marker)
            if idx == -1:
                continue
            rel_path = line[idx + len(marker):].strip()
            if not rel_path:
                continue
            name = os.path.basename(rel_path.rstrip("/"))
            if name:
                names.append(name)
        return sorted(list(set(names)))

    def _path_is_within_parent(self, path: str, parent: str) -> bool:
        target = os.path.normpath(str(path or "").strip())
        base = os.path.normpath(str(parent or "").strip())
        if not target or not base:
            return False
        if base == os.sep:
            return target.startswith(os.sep)
        return target == base or target.startswith(base + os.sep)

    def _extract_receive_exists_name(self, error_text: str) -> str:
        text = str(error_text or "")
        match = re.search(r"creating subvolume\s+(.+?)\s+failed:\s*File exists", text, re.IGNORECASE)
        if not match:
            return ""
        name = str(match.group(1) or "").strip().strip("'\"")
        if not name:
            return ""
        name = os.path.basename(name.rstrip("/"))
        if name in ("", ".", ".."):
            return ""
        return name

    def _delete_subvolume_if_exists(self, subvolume_path: str, btrfs_cmd: str) -> Tuple[bool, str, bool]:
        target = os.path.normpath(str(subvolume_path or "").strip())
        if not target:
            return False, "Invalid subvolume path", False

        exists = os.path.exists(target)
        is_subvolume = self._path_is_btrfs_subvolume(target)
        if not exists and not is_subvolume:
            return True, "", False
        if not is_subvolume:
            return False, f"Path exists but is not a Btrfs subvolume: {target}", False

        res, err = self.run_command([btrfs_cmd, "subvolume", "delete", target], timeout=300)
        if err or not res or res.returncode != 0:
            return False, err or f"Failed to delete existing subvolume: {target}", False
        return True, "", True

    def _cleanup_temp_subvolume(self, subvolume_path: str, btrfs_cmd: str, attempts: int = 3) -> Tuple[bool, str]:
        target = os.path.normpath(str(subvolume_path or "").strip())
        if not target:
            return False, "Invalid subvolume path"

        tries = max(1, int(attempts or 1))
        last_error = ""
        for attempt in range(tries):
            exists_now = os.path.exists(target) or self._path_is_btrfs_subvolume(target)
            if not exists_now:
                return True, ""
            res, err = self.run_command([btrfs_cmd, "subvolume", "delete", target], timeout=300)
            if not err and res and res.returncode == 0:
                return True, ""
            last_error = err or f"Failed to delete temporary snapshot: {target}"
            if attempt < tries - 1:
                time.sleep(0.35 * (attempt + 1))
        return False, last_error or f"Failed to delete temporary snapshot: {target}"

    def _cleanup_stale_send_snapshots(self, source_parent: str, source_slug: str, btrfs_cmd: str) -> None:
        base = os.path.normpath(str(source_parent or "").strip())
        slug = str(source_slug or "").strip()
        if not base or not slug:
            return
        pattern = re.compile(
            rf"^\.?alvaos-buddy-{re.escape(slug)}-\d{{8}}-\d{{6}}-[0-9a-f]{{4}}$",
            re.IGNORECASE,
        )
        for name in self._list_btrfs_subvolume_names_under(base):
            candidate = str(name or "").strip()
            if not pattern.match(candidate):
                continue
            path = os.path.join(base, candidate)
            self._cleanup_temp_subvolume(path, btrfs_cmd, attempts=2)

    def _device_id_for_path(self, path: str) -> Optional[int]:
        target = str(path or "").strip()
        if not target:
            return None
        try:
            return int(os.stat(target).st_dev)
        except Exception:
            return None

    def _best_temp_snapshot_parent(self, source_path: str) -> str:
        source = os.path.normpath(str(source_path or "").strip())
        if not source:
            return "/"

        source_dev = self._device_id_for_path(source)
        parent = os.path.normpath(os.path.dirname(source.rstrip("/")) or "/")

        if source_dev is None:
            return parent

        parent_dev = self._device_id_for_path(parent)
        if parent_dev is not None and parent_dev == source_dev:
            return parent

        # If parent is on another device (e.g. /mnt/alvaos vs /mnt/alvaos/pool),
        # place temp snapshot inside source to keep snapshot operation on one filesystem.
        return source

    def _mkdir_p(self, path: str) -> Tuple[bool, str]:
        target = str(path or "").strip()
        if not target:
            return False, "Invalid directory path"
        if platform.system() != "Linux":
            try:
                Path(target).mkdir(parents=True, exist_ok=True)
                return True, ""
            except Exception as exc:
                return False, str(exc)

        mkdir_cmd = self._mkdir_cmd()
        if not mkdir_cmd:
            return False, "mkdir command not found"
        res, err = self.run_command([mkdir_cmd, "-p", target], timeout=30)
        if err or not res or res.returncode != 0:
            return False, err or f"Failed to create directory: {target}"
        return True, ""

    def _file_sha256(self, path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _derive_transfer_key(self) -> Tuple[Optional[bytes], Optional[str]]:
        settings = self.get_settings(include_secret=True)
        if not settings.get("encryption_enabled"):
            return None, None
        enc_hash = str(settings.get("encryption_hash") or "").strip()
        enc_salt = str(settings.get("encryption_salt") or "").strip()
        if not enc_hash or not enc_salt:
            return None, "Encryption is enabled, but no encryption key material is configured"
        key = hashlib.sha256(f"{enc_hash}|{enc_salt}|alvaos-buddy-v1".encode("utf-8")).digest()
        return key, None

    def _xor_stream_chunk(self, data: bytes, key: bytes, nonce: bytes, counter_start: int) -> Tuple[bytes, int]:
        out = bytearray()
        counter = int(counter_start)
        cursor = 0
        while cursor < len(data):
            block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
            take = min(len(block), len(data) - cursor)
            segment = bytes(data[cursor + i] ^ block[i] for i in range(take))
            out.extend(segment)
            cursor += take
            counter += 1
        return bytes(out), counter

    def _encrypt_stream_file(self, plain_path: str, encrypted_path: str, key: bytes) -> Tuple[bool, str]:
        magic = b"ALVAENC1"
        nonce = secrets.token_bytes(16)
        header = magic + nonce
        mac_key = hashlib.sha256(key + b":mac").digest()
        mac = hmac.new(mac_key, digestmod=hashlib.sha256)
        mac.update(header)
        counter = 0
        try:
            with open(plain_path, "rb") as src, open(encrypted_path, "wb") as dst:
                dst.write(header)
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    cipher_chunk, counter = self._xor_stream_chunk(chunk, key, nonce, counter)
                    dst.write(cipher_chunk)
                    mac.update(cipher_chunk)
                dst.write(mac.digest())
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _decrypt_stream_file(self, encrypted_path: str, plain_path: str, key: bytes) -> Tuple[bool, str]:
        magic = b"ALVAENC1"
        header_len = len(magic) + 16
        tag_len = 32
        mac_key = hashlib.sha256(key + b":mac").digest()
        try:
            size = os.path.getsize(encrypted_path)
            if size < header_len + tag_len:
                return False, "Encrypted stream is too small"
            cipher_len = size - header_len - tag_len
            with open(encrypted_path, "rb") as src, open(plain_path, "wb") as dst:
                header = src.read(header_len)
                if len(header) != header_len or not header.startswith(magic):
                    return False, "Invalid encrypted stream header"
                nonce = header[len(magic):]
                mac = hmac.new(mac_key, digestmod=hashlib.sha256)
                mac.update(header)
                remaining = cipher_len
                counter = 0
                while remaining > 0:
                    read_len = min(1024 * 1024, remaining)
                    cipher_chunk = src.read(read_len)
                    if not cipher_chunk:
                        return False, "Encrypted stream is truncated"
                    remaining -= len(cipher_chunk)
                    mac.update(cipher_chunk)
                    plain_chunk, counter = self._xor_stream_chunk(cipher_chunk, key, nonce, counter)
                    dst.write(plain_chunk)
                expected_tag = src.read(tag_len)
                actual_tag = mac.digest()
                if not hmac.compare_digest(expected_tag, actual_tag):
                    try:
                        os.remove(plain_path)
                    except Exception:
                        pass
                    return False, "Encrypted stream authentication failed"
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _stream_root_path(self) -> str:
        settings = self.get_settings(include_secret=True)
        base = self._sanitize_non_system_path(settings.get("incoming_path"))
        if not base:
            return ""
        return os.path.join(base, ".alvaos-buddy-streams")

    def _fallback_stream_root_path(self) -> str:
        # Persistent service-owned fallback when selected incoming path is not writable.
        return os.path.join(self.state_dir, "buddy-streams")

    def _select_writable_stream_owner_dir(self, owner_node_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        owner = str(owner_node_id or "").strip()
        if not owner:
            return None, None, "owner_node_id is required"

        preferred_root = self._stream_root_path()
        if not preferred_root:
            return None, None, "Local incoming path is not configured or not allowed"
        fallback_root = self._fallback_stream_root_path()
        candidates = [preferred_root]
        if fallback_root and fallback_root != preferred_root:
            candidates.append(fallback_root)

        last_error = "No writable stream directory available"
        for root in candidates:
            try:
                Path(root).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                last_error = f"Failed to prepare stream root: {root} ({exc})"
                continue

            owner_dir = os.path.join(root, owner)
            try:
                Path(owner_dir).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                last_error = f"Failed to prepare owner stream path: {owner_dir} ({exc})"
                continue

            def _probe_write(path: str) -> Tuple[bool, str]:
                probe = os.path.join(path, f".write-probe-{secrets.token_hex(4)}")
                try:
                    with open(probe, "wb") as f:
                        f.write(b"ok")
                    try:
                        os.remove(probe)
                    except Exception:
                        pass
                    return True, ""
                except Exception as exc:
                    try:
                        if os.path.exists(probe):
                            os.remove(probe)
                    except Exception:
                        pass
                    return False, str(exc)

            writable, probe_err = _probe_write(owner_dir)
            if writable:
                return root, owner_dir, None

            # Try to repair ownership/permissions for the service user.
            if platform.system() == "Linux":
                mkdir_cmd = self._mkdir_cmd()
                chown_cmd = self._chown_cmd()
                chmod_cmd = self._chmod_cmd()
                uid = os.getuid()
                gid = os.getgid()
                if mkdir_cmd:
                    self.run_command([mkdir_cmd, "-p", owner_dir], timeout=30)
                if chown_cmd:
                    self.run_command([chown_cmd, "-R", f"{uid}:{gid}", owner_dir], timeout=30)
                if chmod_cmd:
                    self.run_command([chmod_cmd, "-R", "u+rwX,g+rwX", owner_dir], timeout=30)

                writable_after_fix, probe_err_after_fix = _probe_write(owner_dir)
                if writable_after_fix:
                    return root, owner_dir, None
                probe_err = probe_err_after_fix or probe_err

            if root == fallback_root:
                last_error = f"Stream path is not writable: {owner_dir} ({probe_err})"
            else:
                last_error = (
                    f"Primary stream path not writable ({owner_dir}): {probe_err}. "
                    f"Trying fallback path..."
                )
            continue

        return None, None, last_error

    def _notify_remote_peer_removed(self, peer: Dict) -> Dict:
        if not isinstance(peer, dict):
            return {"attempted": False, "success": False, "error": "Invalid peer payload"}

        remote_secret = str(peer.get("api_secret") or "").strip()
        if not remote_secret:
            return {"attempted": False, "success": False, "error": "Peer API secret missing"}

        local_node_id = str(self._identity_public().get("node_id") or "").strip()
        if not local_node_id:
            return {"attempted": False, "success": False, "error": "Local node ID missing"}

        urls = self._peer_api_urls(peer, "/api/v1/backup/pairing/remove/accept")
        if not urls:
            return {"attempted": False, "success": False, "error": "Peer API endpoint not configured"}

        last_url = ""
        last_error = "Reciprocal remove failed"
        for url in urls:
            last_url = url
            try:
                response = requests.post(
                    url,
                    json={"node_id": local_node_id},
                    headers={
                        "Content-Type": "application/json",
                        "X-Buddy-Secret": remote_secret,
                    },
                    timeout=8,
                    verify=False if url.lower().startswith("https://") else True,
                )
                body = {}
                try:
                    body = response.json() if response.text else {}
                except Exception:
                    body = {}
                if 200 <= response.status_code < 300 and not body.get("error"):
                    return {
                        "attempted": True,
                        "success": True,
                        "url": url,
                        "error": "",
                    }
                last_error = body.get("error") or f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = str(exc)

        return {
            "attempted": True,
            "success": False,
            "url": last_url,
            "error": last_error,
        }

    def _probe_peer_api(self, peer: Dict) -> Dict:
        remote_secret = str(peer.get("api_secret") or "").strip()
        if not remote_secret:
            return {
                "attempted": False,
                "success": False,
                "url": "",
                "error": "Peer is missing API secret. Re-pair required.",
            }

        urls = self._peer_api_urls(peer, "/api/v1/backup/buddy/peer/list")
        if not urls:
            return {
                "attempted": False,
                "success": False,
                "url": "",
                "error": "Peer API endpoint is not configured",
            }

        identity = self._identity_public()
        owner_node_id = str(identity.get("node_id") or "").strip()
        params = {"owner_node_id": owner_node_id, "limit": "1"}
        last_error = "API probe failed"
        last_url = ""

        for url in urls:
            last_url = url
            verify_tls = False if url.startswith("https://") else True
            try:
                response = requests.get(
                    url,
                    headers={"X-Buddy-Secret": remote_secret},
                    params=params,
                    timeout=8,
                    verify=verify_tls,
                )
                try:
                    body = response.json() if response.content else {}
                except Exception:
                    body = {}
                if 200 <= int(response.status_code) < 300 and not body.get("error"):
                    return {
                        "attempted": True,
                        "success": True,
                        "url": url,
                        "error": "",
                    }
                last_error = body.get("error") or f"HTTP {response.status_code}"
                # Peer answered -> use this concrete failure and stop.
                return {
                    "attempted": True,
                    "success": False,
                    "url": url,
                    "error": last_error,
                }
            except Exception as exc:
                last_error = str(exc)

        return {
            "attempted": True,
            "success": False,
            "url": last_url,
            "error": last_error,
        }

    def _public_stream_entry(self, item: Dict) -> Dict:
        return {
            "id": str(item.get("id") or ""),
            "owner_node_id": str(item.get("owner_node_id") or ""),
            "from_node_id": str(item.get("from_node_id") or ""),
            "source_path": str(item.get("source_path") or ""),
            "snapshot_name": str(item.get("snapshot_name") or ""),
            "created_at": str(item.get("created_at") or ""),
            "received_at": str(item.get("received_at") or ""),
            "encrypted": bool(item.get("encrypted")),
            "size_bytes": int(item.get("size_bytes") or 0),
            "sha256": str(item.get("sha256") or ""),
        }

    def _enforce_stream_quota(self, owner_node_id: str) -> None:
        owner = str(owner_node_id or "").strip()
        if not owner:
            return
        settings = self.get_settings(include_secret=True)
        policies = settings.get("peer_policies", {})
        policy = policies.get(owner, {}) if isinstance(policies, dict) else {}
        try:
            limit_gb = int(policy.get("max_storage_gb", settings.get("incoming_quota_gb", 200)))
        except Exception:
            limit_gb = 200
        limit_bytes = max(1, limit_gb) * (1024 ** 3)

        entries = self._load_stream_entries()
        owner_entries = [e for e in entries if str(e.get("owner_node_id") or "").strip() == owner]
        owner_entries.sort(key=lambda item: str(item.get("received_at") or ""), reverse=True)
        total = sum(int(item.get("size_bytes") or 0) for item in owner_entries)
        if total <= limit_bytes:
            return

        remove_ids = set()
        for item in reversed(owner_entries):
            if total <= limit_bytes:
                break
            remove_ids.add(str(item.get("id") or ""))
            total -= int(item.get("size_bytes") or 0)

        filtered = []
        for item in entries:
            item_id = str(item.get("id") or "")
            if item_id in remove_ids:
                payload_path = str(item.get("payload_path") or "").strip()
                try:
                    if payload_path and os.path.exists(payload_path):
                        os.remove(payload_path)
                except Exception:
                    pass
                continue
            filtered.append(item)
        self._save_stream_entries(filtered)

    def _peer_api_urls(self, peer: Dict, path: str) -> List[str]:
        path_part = str(path or "").strip()
        if not path_part.startswith("/"):
            path_part = f"/{path_part}"
        api_endpoint = str(peer.get("api_endpoint") or "").strip()
        if not api_endpoint:
            wg_endpoint = str(peer.get("endpoint") or "").strip()
            host = wg_endpoint.split(":", 1)[0].strip()
            if host:
                api_endpoint = f"{host}:8080"
        if not api_endpoint:
            return []
        port_hint = ""
        if ":" in api_endpoint:
            maybe_port = api_endpoint.rsplit(":", 1)[1].strip()
            if maybe_port.isdigit():
                port_hint = maybe_port

        # Avoid protocol mismatch noise on common plain-HTTP ports.
        if port_hint in ("80", "8080"):
            return [f"http://{api_endpoint}{path_part}"]
        if port_hint in ("443",):
            return [f"https://{api_endpoint}{path_part}"]

        return [
            f"http://{api_endpoint}{path_part}",
            f"https://{api_endpoint}{path_part}",
        ]

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
        identity_private = self._identity_private()
        payload = {
            "v": 1,
            "node_id": identity.get("node_id"),
            "name": identity.get("name"),
            "public_key": identity.get("public_key"),
            "api_secret": str(identity_private.get("api_secret") or ""),
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
            "api_secret": str(payload.get("api_secret") or "").strip(),
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
        try:
            listen_port = int(identity.get("listen_port") or self.default_listen_port)
        except Exception:
            listen_port = self.default_listen_port
        listen_port = max(1024, min(65535, listen_port))

        lines = [
            "[Interface]",
            f"PrivateKey = {identity.get('private_key', '')}",
            f"Address = {identity.get('tunnel_ip', '')}/24",
            f"ListenPort = {listen_port}",
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

        conflict_notes: List[str] = []
        for attempt in range(2):
            # Try to cleanly restart interface. Ignore "down" errors.
            self.run_command([wg_quick_cmd, "down", self.wg_config_path], timeout=20)

            identity, _ = self._ensure_identity_listen_port(identity)
            config_text = self._render_wg_config(identity, peers)
            Path(self.wg_dir).mkdir(parents=True, exist_ok=True)
            with open(self.wg_config_path, "w", encoding="utf-8") as f:
                f.write(config_text)
            try:
                os.chmod(self.wg_config_path, 0o600)
            except Exception:
                pass

            up_res, up_err = self.run_command([wg_quick_cmd, "up", self.wg_config_path], timeout=40)
            if not up_err and up_res and up_res.returncode == 0:
                break

            if attempt == 0 and self._is_address_in_use_error(up_err or ""):
                recovered = False

                current_ip = str(identity.get("tunnel_ip") or "").strip()
                has_ip_conflict = (
                    bool(current_ip)
                    and self._local_ipv4_in_use(current_ip)
                    and not self._ipv4_on_interface(current_ip, self.interface_name)
                )
                if has_ip_conflict:
                    old_ip = current_ip
                    identity, ip_changed = self._ensure_identity_tunnel_ip(identity, force_rotate=True)
                    if ip_changed:
                        conflict_notes.append(
                            f"Tunnel IP conflict resolved automatically: {old_ip} -> {identity.get('tunnel_ip', '')}"
                        )
                        recovered = True

                try:
                    current_port = int(identity.get("listen_port") or self.default_listen_port)
                except Exception:
                    current_port = self.default_listen_port
                if self._udp_port_in_use(current_port):
                    old_port = current_port
                    identity, port_changed = self._ensure_identity_listen_port(identity, force_rotate=True)
                    if port_changed:
                        conflict_notes.append(
                            f"Listen port conflict resolved automatically: {old_port} -> {identity.get('listen_port', '')}"
                        )
                        recovered = True

                if recovered:
                    continue
            return False, {"error": up_err or "Failed to bring up WireGuard interface"}

        show_res, show_err = self.run_command([wg_cmd, "show", self.interface_name], timeout=10)
        if show_err:
            payload = {"message": "Tunnel configured, but runtime status unavailable", "warning": show_err}
            if conflict_notes:
                payload["updates"] = conflict_notes
                for note in conflict_notes:
                    if note.startswith("Tunnel IP conflict"):
                        payload["ip_update"] = note
                    if note.startswith("Listen port conflict"):
                        payload["port_update"] = note
            return True, payload
        payload = {"message": "Tunnel configured", "runtime": (show_res.stdout or "").strip()[:1200]}
        if conflict_notes:
            payload["updates"] = conflict_notes
            for note in conflict_notes:
                if note.startswith("Tunnel IP conflict"):
                    payload["ip_update"] = note
                if note.startswith("Listen port conflict"):
                    payload["port_update"] = note
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
        identity_private = self._identity_private()
        payload = {
            "v": 1,
            "node_id": identity.get("node_id"),
            "name": identity.get("name"),
            "public_key": identity.get("public_key"),
            "api_secret": str(identity_private.get("api_secret") or ""),
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

    def ingest_peer_stream(
        self,
        owner_node_id: str,
        from_node_id: str,
        source_path: str,
        snapshot_name: str,
        created_at: str,
        encrypted: bool,
        payload_stream,
    ) -> Tuple[bool, Dict]:
        owner = str(owner_node_id or "").strip()
        sender = str(from_node_id or "").strip()
        source = str(source_path or "").strip()
        snap_name = str(snapshot_name or "").strip()
        created = str(created_at or "").strip() or self._now_iso()
        if not owner:
            return False, {"error": "owner_node_id is required"}
        if not sender:
            return False, {"error": "from_node_id is required"}
        if not source.startswith("/"):
            return False, {"error": "source_path must be an absolute path"}
        if not snap_name:
            return False, {"error": "snapshot_name is required"}

        peers = self._load_peers()
        if owner not in peers:
            return False, {"error": "Unknown owner_node_id"}

        root, owner_dir, select_err = self._select_writable_stream_owner_dir(owner)
        if not root or not owner_dir:
            return False, {"error": select_err or "Failed to prepare writable buddy stream path"}

        stream_id = f"{int(self._now().timestamp())}-{secrets.token_hex(4)}"
        ext = ".enc" if encrypted else ".stream"
        payload_path = os.path.join(owner_dir, f"{stream_id}{ext}")
        sha = hashlib.sha256()
        size_bytes = 0
        try:
            with open(payload_path, "wb") as dst:
                while True:
                    chunk = payload_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    sha.update(chunk)
                    size_bytes += len(chunk)
                    dst.write(chunk)
        except Exception as exc:
            try:
                if os.path.exists(payload_path):
                    os.remove(payload_path)
            except Exception:
                pass
            return False, {"error": f"Failed to store stream payload: {exc}"}

        entry = {
            "id": stream_id,
            "owner_node_id": owner,
            "from_node_id": sender,
            "source_path": source,
            "snapshot_name": snap_name,
            "created_at": created,
            "received_at": self._now_iso(),
            "encrypted": bool(encrypted),
            "size_bytes": int(size_bytes),
            "sha256": sha.hexdigest(),
            "payload_path": payload_path,
        }
        entries = self._load_stream_entries()
        entries.append(entry)
        self._save_stream_entries(entries)
        self._enforce_stream_quota(owner)
        return True, {"stream": self._public_stream_entry(entry)}

    def list_peer_streams(self, owner_node_id: str, limit: int = 100) -> Tuple[bool, Dict]:
        owner = str(owner_node_id or "").strip()
        if not owner:
            return False, {"error": "owner_node_id is required"}
        entries = [
            self._public_stream_entry(item)
            for item in self._load_stream_entries()
            if str(item.get("owner_node_id") or "").strip() == owner
        ]
        entries = sorted(entries, key=lambda item: str(item.get("created_at") or ""), reverse=True)
        max_items = max(1, min(500, int(limit or 100)))
        return True, {"streams": entries[:max_items]}

    def get_peer_stream_payload(self, owner_node_id: str, stream_id: str) -> Tuple[bool, Dict]:
        owner = str(owner_node_id or "").strip()
        sid = str(stream_id or "").strip()
        if not owner:
            return False, {"error": "owner_node_id is required"}
        if not sid:
            return False, {"error": "stream_id is required"}
        for item in self._load_stream_entries():
            if str(item.get("owner_node_id") or "").strip() != owner:
                continue
            if str(item.get("id") or "").strip() != sid:
                continue
            payload_path = str(item.get("payload_path") or "").strip()
            if not payload_path or not os.path.exists(payload_path):
                return False, {"error": "Stored stream payload is missing"}
            return True, {"stream": self._public_stream_entry(item), "payload_path": payload_path}
        return False, {"error": "Stream not found"}

    def delete_peer_stream(self, owner_node_id: str, stream_id: str) -> Tuple[bool, Dict]:
        owner = str(owner_node_id or "").strip()
        sid = str(stream_id or "").strip()
        if not owner:
            return False, {"error": "owner_node_id is required"}
        if not sid:
            return False, {"error": "stream_id is required"}

        entries = self._load_stream_entries()
        target_entry = None
        filtered = []
        for item in entries:
            item_owner = str(item.get("owner_node_id") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if item_owner == owner and item_id == sid and target_entry is None:
                target_entry = item
                continue
            filtered.append(item)

        if target_entry is None:
            return False, {"error": "Stream not found"}

        payload_path = str(target_entry.get("payload_path") or "").strip()
        payload_removed = False
        if payload_path and os.path.exists(payload_path):
            try:
                os.remove(payload_path)
                payload_removed = True
            except Exception as exc:
                return False, {"error": f"Failed to delete stream payload: {exc}"}

        self._save_stream_entries(filtered)
        return True, {
            "stream": self._public_stream_entry(target_entry),
            "payload_removed": payload_removed,
        }

    def _create_send_stream(self, source_path: str) -> Tuple[bool, Dict]:
        source = str(source_path or "").strip()
        if not source.startswith("/"):
            return False, {"error": "source_path must be an absolute path"}
        if platform.system() != "Linux":
            return False, {"error": "Buddy transfer is supported on Linux only"}

        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return False, {"error": "btrfs command not found"}
        # `os.path.exists()` can return False for non-root service users on paths
        # that are present but not traversable. Validate with btrfs metadata first.
        source_is_subvolume = self._path_is_btrfs_subvolume(source)
        if not source_is_subvolume:
            if not os.path.exists(source):
                return False, {
                    "error": (
                        f"Source path not found or not accessible by backend user: {source}. "
                        "If this path exists, ensure backend permissions or re-select the subvolume source."
                    )
                }
            return False, {"error": f"Source path is not a Btrfs subvolume: {source}"}

        source_parent = self._best_temp_snapshot_parent(source)
        source_slug = re.sub(r"[^a-z0-9._-]+", "-", source.strip("/").lower()) or "source"
        stamp = self._now().strftime("%Y%m%d-%H%M%S")
        temp_name = f".alvaos-buddy-{source_slug}-{stamp}-{secrets.token_hex(2)}"
        temp_snapshot = os.path.join(source_parent, temp_name)
        ok_parent, parent_err = self._mkdir_p(source_parent)
        if not ok_parent:
            return False, {"error": parent_err or "Failed to prepare source parent"}

        # Best-effort cleanup from older runs so stale temp snapshots do not accumulate in shares.
        self._cleanup_stale_send_snapshots(source_parent=source_parent, source_slug=source_slug, btrfs_cmd=btrfs_cmd)

        stream_fd, stream_path = tempfile.mkstemp(prefix="buddy-send-", suffix=".stream", dir=self.state_dir)
        os.close(stream_fd)
        payload = None
        try:
            snap_res, snap_err = self.run_command(
                [btrfs_cmd, "subvolume", "snapshot", "-r", source, temp_snapshot],
                timeout=240,
            )
            if (snap_err or not snap_res or snap_res.returncode != 0) and "invalid cross-device link" in str(snap_err or "").lower():
                # Retry once inside source path; some pool root paths have parent dirs on another device.
                fallback_parent = source
                fallback_snapshot = os.path.join(fallback_parent, temp_name)
                if fallback_snapshot != temp_snapshot:
                    ok_fallback_parent, fallback_parent_err = self._mkdir_p(fallback_parent)
                    if not ok_fallback_parent:
                        return False, {"error": fallback_parent_err or "Failed to prepare source parent"}
                    source_parent = fallback_parent
                    temp_snapshot = fallback_snapshot
                    snap_res, snap_err = self.run_command(
                        [btrfs_cmd, "subvolume", "snapshot", "-r", source, temp_snapshot],
                        timeout=240,
                    )
            if snap_err or not snap_res or snap_res.returncode != 0:
                return False, {"error": snap_err or "Failed to create temporary snapshot"}

            send_res, send_err = self.run_command(
                [btrfs_cmd, "send", "-f", stream_path, temp_snapshot],
                timeout=1800,
            )
            if send_err or not send_res or send_res.returncode != 0:
                return False, {"error": send_err or "Failed to create Btrfs stream"}

            payload = {
                "stream_path": stream_path,
                "snapshot_name": temp_name,
                "created_at": self._now_iso(),
            }
            return True, payload
        finally:
            cleanup_ok, cleanup_err = self._cleanup_temp_subvolume(temp_snapshot, btrfs_cmd, attempts=3)
            if not cleanup_ok:
                if isinstance(payload, dict):
                    payload["cleanup_warning"] = cleanup_err
                else:
                    print(f"Buddy temp snapshot cleanup warning: {cleanup_err}")

    def _upload_stream_to_peer(self, peer: Dict, payload_path: str, metadata: Dict) -> Tuple[bool, Dict]:
        remote_secret = str(peer.get("api_secret") or "").strip()
        if not remote_secret:
            return False, {"error": "Peer is missing API secret. Re-pair to enable transfer."}
        urls = self._peer_api_urls(peer, "/api/v1/backup/buddy/peer/upload")
        if not urls:
            return False, {"error": "Peer API endpoint is not configured"}

        last_error = "Upload failed"
        headers = {"X-Buddy-Secret": remote_secret}
        for url in urls:
            verify_tls = False if url.startswith("https://") else True
            try:
                with open(payload_path, "rb") as fh:
                    response = requests.post(
                        url,
                        headers=headers,
                        data=metadata,
                        files={"payload": ("stream.bin", fh, "application/octet-stream")},
                        timeout=1800,
                        verify=verify_tls,
                    )
                try:
                    body = response.json() if response.content else {}
                except Exception:
                    body = {}
                if response.status_code >= 200 and response.status_code < 300 and not body.get("error"):
                    return True, body
                last_error = body.get("error") or f"HTTP {response.status_code}"
                # The peer answered, so use this concrete error instead of trying another
                # scheme and potentially masking it with a secondary SSL error.
                return False, {"error": last_error}
            except Exception as exc:
                last_error = str(exc)
        return False, {"error": last_error}

    def sync_to_peer(self, node_id: str, sources: Optional[List[str]] = None) -> Tuple[bool, Dict]:
        target = str(node_id or "").strip()
        if not target:
            return False, {"error": "node_id is required"}

        with self._transfer_lock:
            peers = self._load_peers()
            peer = peers.get(target)
            if not isinstance(peer, dict):
                return False, {"error": "Peer not found"}

            settings = self.get_settings(include_secret=True)
            policies = settings.get("peer_policies", {})
            policy = policies.get(target, {}) if isinstance(policies, dict) else {}
            if policy.get("enabled") is False:
                return False, {"error": "Peer sync is disabled by policy"}

            selected_sources = []
            if isinstance(sources, list) and sources:
                selected_sources = [str(path or "").strip() for path in sources if str(path or "").strip().startswith("/")]
            elif isinstance(policy.get("outgoing_sources"), list) and policy.get("outgoing_sources"):
                selected_sources = [str(path or "").strip() for path in policy.get("outgoing_sources", []) if str(path or "").strip().startswith("/")]
            else:
                selected_sources = [str(path or "").strip() for path in settings.get("outgoing_sources", []) if str(path or "").strip().startswith("/")]

            deduped = []
            seen = set()
            for path in selected_sources:
                if path in seen:
                    continue
                seen.add(path)
                deduped.append(path)
            selected_sources = deduped

            if not selected_sources:
                return False, {"error": "No outgoing sources configured for this peer"}

            identity = self._identity_public()
            local_node_id = str(identity.get("node_id") or "")
            transfer_key, key_err = self._derive_transfer_key()
            if key_err:
                return False, {"error": key_err}
            encryption_enabled = bool(transfer_key)

            created = []
            failed = []
            for source_path in selected_sources:
                send_ok, send_payload = self._create_send_stream(source_path)
                if not send_ok:
                    failed.append({"source_path": source_path, "error": send_payload.get("error", "Failed to build stream")})
                    continue
                stream_path = str(send_payload.get("stream_path") or "")
                upload_path = stream_path
                encrypted_flag = False
                encrypted_path = ""
                try:
                    if encryption_enabled:
                        encrypted_path = f"{stream_path}.enc"
                        enc_ok, enc_err = self._encrypt_stream_file(stream_path, encrypted_path, transfer_key or b"")
                        if not enc_ok:
                            failed.append({"source_path": source_path, "error": enc_err or "Failed to encrypt stream"})
                            continue
                        upload_path = encrypted_path
                        encrypted_flag = True

                    meta = {
                        "owner_node_id": local_node_id,
                        "from_node_id": local_node_id,
                        "source_path": source_path,
                        "snapshot_name": str(send_payload.get("snapshot_name") or ""),
                        "created_at": str(send_payload.get("created_at") or self._now_iso()),
                        "encrypted": "1" if encrypted_flag else "0",
                        "sha256": self._file_sha256(upload_path),
                    }
                    upload_ok, upload_payload = self._upload_stream_to_peer(peer, upload_path, meta)
                    if upload_ok:
                        created.append({
                            "source_path": source_path,
                            "snapshot_name": meta["snapshot_name"],
                            "encrypted": encrypted_flag,
                            "remote": upload_payload.get("stream", {}),
                        })
                    else:
                        failed.append({"source_path": source_path, "error": upload_payload.get("error", "Upload failed")})
                finally:
                    for path in (stream_path, encrypted_path):
                        if not path:
                            continue
                        try:
                            if os.path.exists(path):
                                os.remove(path)
                        except Exception:
                            pass

            status = "success" if not failed else ("partial" if created else "error")
            sync_error = "; ".join(item.get("error", "") for item in failed if item.get("error"))
            now_iso = self._now_iso()
            self._update_runtime(
                {
                    "last_sync_at": now_iso,
                    "last_sync_status": status,
                    "last_sync_error": sync_error,
                    "peer": {
                        "last_sync_at": now_iso,
                        "last_sync_status": status,
                        "last_sync_error": sync_error,
                    },
                },
                node_id=target,
            )

            return True, {
                "node_id": target,
                "status": status,
                "created": created,
                "failed": failed,
                "encryption_enabled": encryption_enabled,
            }

    def fetch_remote_snapshots(self, node_id: str, limit: int = 100) -> Tuple[bool, Dict]:
        target = str(node_id or "").strip()
        if not target:
            return False, {"error": "node_id is required"}
        peers = self._load_peers()
        peer = peers.get(target)
        if not isinstance(peer, dict):
            return False, {"error": "Peer not found"}
        remote_secret = str(peer.get("api_secret") or "").strip()
        if not remote_secret:
            return False, {"error": "Peer is missing API secret. Re-pair to enable transfer."}

        identity = self._identity_public()
        owner_node_id = str(identity.get("node_id") or "")
        params = {"owner_node_id": owner_node_id, "limit": str(max(1, min(500, int(limit or 100))))}
        urls = self._peer_api_urls(peer, "/api/v1/backup/buddy/peer/list")
        if not urls:
            return False, {"error": "Peer API endpoint is not configured"}

        last_error = "Failed to fetch remote snapshots"
        for url in urls:
            verify_tls = False if url.startswith("https://") else True
            try:
                response = requests.get(
                    url,
                    headers={"X-Buddy-Secret": remote_secret},
                    params=params,
                    timeout=30,
                    verify=verify_tls,
                )
                try:
                    body = response.json() if response.content else {}
                except Exception:
                    body = {}
                if response.status_code >= 200 and response.status_code < 300 and not body.get("error"):
                    streams = body.get("streams", [])
                    if not isinstance(streams, list):
                        streams = []
                    streams = sorted(streams, key=lambda item: str(item.get("created_at") or ""), reverse=True)
                    return True, {"node_id": target, "streams": streams}
                last_error = body.get("error") or f"HTTP {response.status_code}"
                return False, {"error": last_error}
            except Exception as exc:
                last_error = str(exc)
        return False, {"error": last_error}

    def delete_remote_snapshot(self, node_id: str, stream_id: str) -> Tuple[bool, Dict]:
        target = str(node_id or "").strip()
        sid = str(stream_id or "").strip()
        if not target:
            return False, {"error": "node_id is required"}
        if not sid:
            return False, {"error": "stream_id is required"}

        peers = self._load_peers()
        peer = peers.get(target)
        if not isinstance(peer, dict):
            return False, {"error": "Peer not found"}

        remote_secret = str(peer.get("api_secret") or "").strip()
        if not remote_secret:
            return False, {"error": "Peer is missing API secret. Re-pair to enable transfer."}

        identity = self._identity_public()
        owner_node_id = str(identity.get("node_id") or "")
        params = {"owner_node_id": owner_node_id}
        urls = self._peer_api_urls(peer, f"/api/v1/backup/buddy/peer/delete/{sid}")
        if not urls:
            return False, {"error": "Peer API endpoint is not configured"}

        last_error = "Failed to delete remote snapshot"
        for url in urls:
            verify_tls = False if url.startswith("https://") else True
            try:
                response = requests.delete(
                    url,
                    headers={"X-Buddy-Secret": remote_secret},
                    params=params,
                    timeout=30,
                    verify=verify_tls,
                )
                try:
                    body = response.json() if response.content else {}
                except Exception:
                    body = {}
                if response.status_code >= 200 and response.status_code < 300 and not body.get("error"):
                    return True, {
                        "node_id": target,
                        "deleted": body.get("deleted", {}),
                    }
                last_error = body.get("error") or f"HTTP {response.status_code}"
                return False, {"error": last_error}
            except Exception as exc:
                last_error = str(exc)
        return False, {"error": last_error}

    def _download_remote_stream(self, peer: Dict, stream_id: str, owner_node_id: str, destination_path: str) -> Tuple[bool, str]:
        remote_secret = str(peer.get("api_secret") or "").strip()
        urls = self._peer_api_urls(peer, f"/api/v1/backup/buddy/peer/download/{stream_id}")
        if not remote_secret or not urls:
            return False, "Peer API is not configured"
        params = {"owner_node_id": owner_node_id}
        last_error = "Download failed"
        for url in urls:
            verify_tls = False if url.startswith("https://") else True
            try:
                with requests.get(
                    url,
                    headers={"X-Buddy-Secret": remote_secret},
                    params=params,
                    timeout=1800,
                    stream=True,
                    verify=verify_tls,
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        try:
                            payload = response.json() if response.content else {}
                            last_error = payload.get("error") or f"HTTP {response.status_code}"
                        except Exception:
                            last_error = f"HTTP {response.status_code}"
                        return False, last_error
                    with open(destination_path, "wb") as dst:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            dst.write(chunk)
                    return True, ""
            except Exception as exc:
                last_error = str(exc)
        return False, last_error

    def _restore_from_stream_file(self, stream_path: str, snapshot_name: str, source_path: str) -> Tuple[bool, Dict]:
        target_source = str(source_path or "").strip()
        if not target_source.startswith("/"):
            return False, {"error": "source_path must be an absolute path"}
        if platform.system() != "Linux":
            return True, {
                "restored_to": target_source,
                "previous_backup": None,
                "message": "Mock restore completed (non-Linux environment)",
            }

        btrfs_cmd = self._btrfs_cmd()
        mv_cmd = self._mv_cmd()
        bash_cmd = self._bash_cmd()
        if not (btrfs_cmd and mv_cmd and bash_cmd):
            return False, {"error": "Missing required system commands (btrfs/mv/bash)"}

        target_parent = os.path.dirname(target_source.rstrip("/")) or "/"
        ok_parent, parent_err = self._mkdir_p(target_parent)
        if not ok_parent:
            return False, {"error": parent_err or "Failed to prepare target parent"}
        expected_name = str(snapshot_name or "").strip()

        # If an old receive left the same temporary snapshot behind, remove it first.
        if expected_name:
            expected_path = os.path.normpath(os.path.join(target_parent, expected_name))
            if self._path_is_within_parent(expected_path, target_parent) and expected_path != os.path.normpath(target_parent):
                cleanup_ok, cleanup_err, _ = self._delete_subvolume_if_exists(expected_path, btrfs_cmd)
                if not cleanup_ok:
                    return False, {"error": cleanup_err or f"Failed to prepare restore target: {expected_path}"}

        before_names = set(self._list_btrfs_subvolume_names_under(target_parent))

        receive_cmd = f"{shlex.quote(btrfs_cmd)} receive {shlex.quote(target_parent)} < {shlex.quote(stream_path)}"
        recv_res, recv_err = self.run_command([bash_cmd, "-lc", receive_cmd], timeout=1800)
        if recv_err or not recv_res or recv_res.returncode != 0:
            # Retry once if receive failed because a subvolume from a previous run already exists.
            collision_name = self._extract_receive_exists_name(recv_err or "")
            retried = False
            if collision_name:
                collision_path = os.path.normpath(os.path.join(target_parent, collision_name))
                if self._path_is_within_parent(collision_path, target_parent) and collision_path != os.path.normpath(target_parent):
                    cleanup_ok, cleanup_err, cleaned = self._delete_subvolume_if_exists(collision_path, btrfs_cmd)
                    if cleanup_ok and cleaned:
                        retried = True
                        recv_res, recv_err = self.run_command([bash_cmd, "-lc", receive_cmd], timeout=1800)
                    elif not cleanup_ok:
                        return False, {"error": cleanup_err or f"Failed to clear existing receive subvolume: {collision_path}"}
            if (not retried) or recv_err or not recv_res or recv_res.returncode != 0:
                return False, {"error": recv_err or "Failed to receive Btrfs stream"}

        received_path = ""
        if expected_name:
            expected_path = os.path.join(target_parent, expected_name)
            if self._path_is_btrfs_subvolume(expected_path) or os.path.exists(expected_path):
                received_path = expected_path

        after_names = set(self._list_btrfs_subvolume_names_under(target_parent))
        new_names = sorted(list(after_names - before_names))

        if not received_path:
            name_candidates: List[str] = []
            if expected_name and expected_name in after_names:
                name_candidates.append(expected_name)
            if expected_name:
                name_candidates.extend([name for name in new_names if expected_name in name and name not in name_candidates])
            name_candidates.extend([name for name in new_names if name not in name_candidates])
            if len(name_candidates) == 0 and len(after_names) == 1:
                name_candidates = list(after_names)

            for name in name_candidates:
                candidate_path = os.path.join(target_parent, name)
                if self._path_is_btrfs_subvolume(candidate_path) or os.path.exists(candidate_path):
                    received_path = candidate_path
                    break

        if not received_path:
            extra = f" expected={expected_name or '-'} discovered={','.join(new_names) if new_names else '-'}"
            return False, {"error": f"Restore stream received, but snapshot path could not be resolved ({extra})"}

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        previous_path = f"{target_source}.pre-restore-{stamp}"
        moved_old = False

        if os.path.exists(target_source):
            mv_res, mv_err = self.run_command([mv_cmd, target_source, previous_path], timeout=120)
            if mv_err or not mv_res or mv_res.returncode != 0:
                return False, {"error": mv_err or "Failed to stage current data for restore"}
            moved_old = True

        snap_res, snap_err = self.run_command(
            [btrfs_cmd, "subvolume", "snapshot", received_path, target_source],
            timeout=300,
        )
        if snap_err or not snap_res or snap_res.returncode != 0:
            if moved_old and not os.path.exists(target_source):
                self.run_command([mv_cmd, previous_path, target_source], timeout=120)
            return False, {"error": snap_err or "Failed to apply restored snapshot"}

        self.run_command([btrfs_cmd, "subvolume", "delete", received_path], timeout=180)
        return True, {
            "restored_to": target_source,
            "previous_backup": previous_path if moved_old else None,
            "message": "Restore completed",
        }

    def restore_from_remote_snapshot(
        self,
        node_id: str,
        stream_id: str,
        source_path: str = "",
        encryption_passphrase: str = "",
    ) -> Tuple[bool, Dict]:
        target = str(node_id or "").strip()
        sid = str(stream_id or "").strip()
        source_override = str(source_path or "").strip()
        if not target:
            return False, {"error": "node_id is required"}
        if not sid:
            return False, {"error": "stream_id is required"}

        with self._transfer_lock:
            peers = self._load_peers()
            peer = peers.get(target)
            if not isinstance(peer, dict):
                return False, {"error": "Peer not found"}

            list_ok, list_payload = self.fetch_remote_snapshots(target, limit=500)
            if not list_ok:
                return False, {"error": list_payload.get("error", "Failed to fetch remote snapshots")}
            stream_entry = None
            for item in list_payload.get("streams", []):
                if str(item.get("id") or "") == sid:
                    stream_entry = item
                    break
            if not isinstance(stream_entry, dict):
                return False, {"error": "Remote snapshot not found"}

            restore_source = source_override or str(stream_entry.get("source_path") or "").strip()
            if not restore_source.startswith("/"):
                return False, {"error": "Invalid restore source path in remote snapshot"}

            encrypted = bool(stream_entry.get("encrypted"))
            transfer_key = None
            if encrypted:
                if not encryption_passphrase:
                    return False, {"error": "Encryption password is required for remote restore"}
                if not self.verify_encryption_passphrase(encryption_passphrase):
                    return False, {"error": "Invalid encryption password"}
                transfer_key, key_err = self._derive_transfer_key()
                if key_err or not transfer_key:
                    return False, {"error": key_err or "Missing encryption key material"}

            identity = self._identity_public()
            owner_node_id = str(identity.get("node_id") or "")
            fd_encrypted, encrypted_tmp = tempfile.mkstemp(prefix="buddy-restore-", suffix=".stream.enc", dir=self.state_dir)
            os.close(fd_encrypted)
            plain_tmp = ""
            try:
                dl_ok, dl_err = self._download_remote_stream(peer, sid, owner_node_id, encrypted_tmp)
                if not dl_ok:
                    return False, {"error": dl_err or "Failed to download remote snapshot stream"}

                stream_for_restore = encrypted_tmp
                if encrypted:
                    fd_plain, plain_tmp = tempfile.mkstemp(prefix="buddy-restore-", suffix=".stream", dir=self.state_dir)
                    os.close(fd_plain)
                    dec_ok, dec_err = self._decrypt_stream_file(encrypted_tmp, plain_tmp, transfer_key or b"")
                    if not dec_ok:
                        return False, {"error": dec_err or "Failed to decrypt remote snapshot stream"}
                    stream_for_restore = plain_tmp

                restore_ok, restore_payload = self._restore_from_stream_file(
                    stream_path=stream_for_restore,
                    snapshot_name=str(stream_entry.get("snapshot_name") or ""),
                    source_path=restore_source,
                )
                now_iso = self._now_iso()
                if restore_ok:
                    self._update_runtime({
                        "last_restore_at": now_iso,
                        "last_restore_status": "success",
                        "last_restore_error": "",
                    })
                    return True, {
                        "message": "Remote restore completed",
                        "stream": stream_entry,
                        "result": restore_payload,
                    }
                self._update_runtime({
                    "last_restore_at": now_iso,
                    "last_restore_status": "error",
                    "last_restore_error": str(restore_payload.get("error") or "Restore failed"),
                })
                return False, {"error": restore_payload.get("error", "Restore failed")}
            finally:
                for path in (encrypted_tmp, plain_tmp):
                    if not path:
                        continue
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except Exception:
                        pass

    def remove_peer(self, node_id: str, reciprocal: bool = True, allow_missing: bool = False) -> Tuple[bool, Dict]:
        target = str(node_id or "").strip()
        if not target:
            return False, {"error": "node_id is required"}
        peers = self._load_peers()
        if target not in peers:
            if allow_missing:
                return True, {"removed": {}, "already_absent": True}
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

        streams = self._load_stream_entries()
        kept_streams = []
        for entry in streams:
            owner = str(entry.get("owner_node_id") or "").strip()
            if owner == target:
                payload_path = str(entry.get("payload_path") or "").strip()
                try:
                    if payload_path and os.path.exists(payload_path):
                        os.remove(payload_path)
                except Exception:
                    pass
                continue
            kept_streams.append(entry)
        if len(kept_streams) != len(streams):
            self._save_stream_entries(kept_streams)

        reciprocal_result = {"attempted": False, "success": False, "error": ""}
        if reciprocal:
            reciprocal_result = self._notify_remote_peer_removed(removed)

        ok, tunnel_result = self.apply_tunnel_config()
        if not ok:
            err = str(tunnel_result.get("error", "")).lower()
            if "wireguard" in err or "wg" in err:
                return True, {
                    "removed": removed,
                    "reciprocal": reciprocal_result,
                    "tunnel_result": {
                        "message": "Peer removed. Tunnel update deferred until WireGuard is available.",
                        "warning": tunnel_result.get("error"),
                    }
                }
            return False, {"error": tunnel_result.get("error", "Peer removed, but tunnel reconfigure failed")}
        return True, {"removed": removed, "reciprocal": reciprocal_result, "tunnel_result": tunnel_result}

    def test_peer_connection(self, node_id: str) -> Tuple[bool, Dict]:
        target = str(node_id or "").strip()
        if not target:
            return False, {"error": "node_id is required"}

        peers = self._load_peers()
        peer = peers.get(target)
        if not isinstance(peer, dict):
            return False, {"error": "Peer not found"}

        runtime = self._runtime_status()
        tunnel_state = str(runtime.get("state") or "unknown").lower()
        tunnel_up = tunnel_state == "up"
        runtime_peers_by_key = {}
        for item in runtime.get("peers", []) if isinstance(runtime.get("peers"), list) else []:
            if not isinstance(item, dict):
                continue
            public_key = str(item.get("public_key") or "").strip()
            if public_key:
                runtime_peers_by_key[public_key] = item

        public_key = str(peer.get("public_key") or "").strip()
        runtime_peer = runtime_peers_by_key.get(public_key, {})
        latest_handshake = str(runtime_peer.get("latest_handshake") or "").strip()
        handshake_age = self._parse_handshake_age_seconds(latest_handshake)
        online = bool(tunnel_up and runtime_peer) and latest_handshake.lower() not in ("", "never")
        connected = bool(
            tunnel_up
            and online
            and handshake_age is not None
            and handshake_age <= self.connected_handshake_threshold_seconds
        )

        tunnel_ip = str(peer.get("tunnel_ip") or "").strip()
        ping_payload = {
            "attempted": False,
            "success": False,
            "error": "",
        }
        ping_cmd = self._ping_cmd()
        if platform.system() == "Linux" and ping_cmd and tunnel_ip:
            ping_payload["attempted"] = True
            ping_res, ping_err = self.run_command([ping_cmd, "-c", "1", "-W", "2", tunnel_ip], timeout=8)
            ping_ok = bool(ping_res and ping_res.returncode == 0 and not ping_err)
            ping_payload["success"] = ping_ok
            if not ping_ok:
                ping_payload["error"] = ping_err or ((ping_res.stderr or ping_res.stdout or "").strip()[:240])

        api_probe = self._probe_peer_api(peer)

        ping_ok = bool(ping_payload.get("success"))
        api_ok = bool(api_probe.get("success"))
        wg_transport_ok = bool(connected and (not ping_payload.get("attempted") or ping_ok))
        connection_ok = wg_transport_ok
        if connection_ok:
            message = "Connection test successful (WireGuard tunnel + buddy transport reachable)"
        else:
            if not tunnel_up:
                if api_ok:
                    message = "Buddy API reachable, but WireGuard tunnel is DOWN"
                else:
                    message = f"WireGuard tunnel is DOWN ({runtime.get('message') or 'no runtime info'})"
            elif not connected:
                message = "WireGuard tunnel is up, but no recent handshake with this buddy"
            elif ping_payload.get("attempted") and not ping_ok:
                message = f"WireGuard handshake exists, but tunnel ping failed: {ping_payload.get('error') or 'unknown error'}"
            else:
                message = f"Connection test failed: {api_probe.get('error') or 'unknown reason'}"

        return True, {
            "node_id": target,
            "peer_name": str(peer.get("name") or target),
            "connection_ok": connection_ok,
            "message": message,
            "runtime": {
                "state": str(runtime.get("state") or "unknown"),
                "online": online,
                "connected": connected,
                "latest_handshake": latest_handshake,
                "endpoint": str(runtime_peer.get("endpoint") or "").strip(),
            },
            "ping": ping_payload,
            "api_probe": api_probe,
        }

    def restart_tunnel(self) -> Tuple[bool, Dict]:
        return self.apply_tunnel_config()

    def get_status(self) -> Dict:
        identity = self._identity_public()
        peers_map = self._load_peers()
        settings_full = self.get_settings(include_secret=True)
        policies = settings_full.get("peer_policies", {})
        runtime = self._runtime_status()
        tunnel_state = str(runtime.get("state") or "unknown").lower()
        tunnel_up = tunnel_state == "up"
        runtime_peers_by_key = {}
        for item in runtime.get("peers", []) if isinstance(runtime.get("peers"), list) else []:
            if not isinstance(item, dict):
                continue
            public_key = str(item.get("public_key") or "").strip()
            if public_key:
                runtime_peers_by_key[public_key] = item

        peers = []
        for peer in peers_map.values():
            item = dict(peer if isinstance(peer, dict) else {})
            item.pop("api_secret", None)
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

            public_key = str(item.get("public_key") or "").strip()
            runtime_peer = runtime_peers_by_key.get(public_key, {})
            latest_handshake = str(runtime_peer.get("latest_handshake") or "").strip()
            handshake_age = self._parse_handshake_age_seconds(latest_handshake)
            online = bool(tunnel_up and runtime_peer) and latest_handshake.lower() not in ("", "never")
            connected = bool(
                tunnel_up
                and online
                and handshake_age is not None
                and handshake_age <= self.connected_handshake_threshold_seconds
            )
            item["runtime"] = {
                "in_tunnel": bool(runtime_peer),
                "endpoint": str(runtime_peer.get("endpoint") or "").strip(),
                "latest_handshake": latest_handshake,
                "handshake_age_seconds": handshake_age,
                "online": online,
                "connected": connected,
            }
            peers.append(item)
        peers = sorted(peers, key=lambda item: str(item.get("name", "")).lower())
        settings = self._public_settings(settings_full)
        runtime_state = self._load_runtime()
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
            "transfer": runtime_state,
            "mode": "transfer-ready",
            "requirements": {
                "linux": platform.system() == "Linux",
                "wg_cmd": wg_cmd or "",
                "wg_quick_cmd": wg_quick_cmd or "",
                "wireguard_identity": key_source,
            },
        }
