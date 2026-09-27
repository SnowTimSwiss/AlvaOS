#!/usr/bin/env python3
"""
AlvaOS Buddy Backup Manager (v0.7.0)
Identity + token pairing + WireGuard tunnel automation.
"""

import base64
import hashlib
import hmac
import itertools
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

import requests

from alerts_manager import push_notification
from common import build_privileged_cmd
import buddy_crypto

WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$")
WG_ENDPOINT_RE = re.compile(r"^(\[[0-9a-fA-F:]{2,39}\]|[A-Za-z0-9.-]{1,253}):\d{1,5}$")
IPV4_RE = re.compile(r"^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$")

# Marker source path for a full-system (root subvolume) buddy transfer. A stream whose
# source_path is "/" is restored as a full system rollback (set-default + reboot) rather
# than a data subvolume replace.
SYSTEM_SOURCE_PATH = "/"

DEFAULT_BUDDY_SETTINGS: Dict[str, Any] = {
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

DEFAULT_BUDDY_RUNTIME: Dict[str, Any] = {
    "last_sync_at": "",
    "last_sync_status": "idle",
    "last_sync_error": "",
    "last_restore_at": "",
    "last_restore_status": "idle",
    "last_restore_error": "",
    "per_peer": {},
}


class _TransferError(Exception):
    """A buddy transfer failed; the message is shown to the user."""


def _spawn_privileged(cmd: List[str], stdin_pipe: bool = False) -> Tuple[subprocess.Popen, Any]:
    """Start a root command that streams: (process, stderr file).

    By default its stdout is a pipe to read from (btrfs send); with
    stdin_pipe its stdin is a pipe to write to (btrfs receive).
    """
    stderr = tempfile.TemporaryFile()
    env = dict(os.environ, LC_ALL="C")
    proc = subprocess.Popen(
        build_privileged_cmd(cmd),
        stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL if stdin_pipe else subprocess.PIPE,
        stderr=stderr, env=env,
    )
    return proc, stderr


def _read_stderr(stderr) -> str:
    try:
        stderr.seek(0)
        return stderr.read()[-4000:].decode("utf-8", "replace").strip()
    except Exception:
        return ""


def _stop_process(proc) -> None:
    """End a streaming child. Closing the pipe stops `btrfs send` with EPIPE;
    the process runs as root, so signals from the backend may not reach it."""
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=60)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class BuddyBackupManager:
    UPLOAD_ATTEMPTS = 4

    def __init__(self, run_command: Callable, spawn_privileged: Callable = _spawn_privileged):
        self.run_command = run_command
        # Starts streaming root commands (btrfs send/receive); replaced in tests.
        self.spawn_privileged = spawn_privileged
        self.state_dir = self._resolve_state_dir()
        self.identity_file = os.path.join(self.state_dir, "buddy_identity.json")
        self.peers_file = os.path.join(self.state_dir, "buddy_peers.json")
        self.tokens_file = os.path.join(self.state_dir, "buddy_tokens.json")
        self.settings_file = os.path.join(self.state_dir, "buddy_settings.json")
        self.streams_file = os.path.join(self.state_dir, "buddy_streams.json")
        self.runtime_file = os.path.join(self.state_dir, "buddy_runtime.json")
        # ALVAENC2 key material (scrypt output). Needed to encrypt unattended;
        # restores derive the key from the passphrase and the stream header.
        self.encryption_key_file = os.path.join(self.state_dir, "buddy_encryption_key.json")
        self.wg_dir = os.path.join(self.state_dir, "wireguard")
        self.wg_config_path = os.path.join(self.wg_dir, "buddy0.conf")
        self.interface_name = "buddy0"
        self.default_listen_port = 51820
        self.connected_handshake_threshold_seconds = 180
        self._transfer_lock = threading.RLock()
        # Snapshots currently being received from buddies, by upload id.
        self._uploads: Dict[str, Dict[str, Any]] = {}
        self._uploads_lock = threading.Lock()

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

    def _generate_wg_keypair(self) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
        """Create a WireGuard key pair.

        WireGuard keys are plain X25519 keys, so they are generated in-process
        with `cryptography`: no wg binary, no root, and never a fake key. (A
        random "public key" that does not belong to the private key would make
        pairing look successful while the tunnel can never come up.)
        """
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

            key = X25519PrivateKey.generate()
            private_raw = key.private_bytes(
                serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
            )
            public_raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        except Exception as exc:
            return None, None, f"Could not generate a WireGuard key pair: {exc}", ""
        return (
            base64.b64encode(private_raw).decode("ascii"),
            base64.b64encode(public_raw).decode("ascii"),
            None,
            "wireguard",
        )

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
            # Older versions stored random placeholder keys when wg was missing;
            # those can never form a tunnel, so replace them with a real pair.
            has_placeholder_keys = key_source != "wireguard"
            if needs_regen or has_placeholder_keys:
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
                "enabled": bool(item.get("enabled", False)),
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
        new_material = None
        if password_supplied and encryption_password:
            if len(encryption_password) < 8:
                return False, {"error": "The encryption password must have at least 8 characters"}
            digest, salt = self._hash_passphrase(encryption_password)
            candidate["encryption_hash"] = digest
            candidate["encryption_salt"] = salt
            new_material = buddy_crypto.new_key_material(encryption_password)

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
        # Remember the old password's salt so snapshots in the legacy format
        # stay restorable with the old password after a password change.
        old_salt = str(current.get("encryption_salt") or "").strip()
        if old_salt and old_salt != merged.get("encryption_salt"):
            self._remember_legacy_salt(old_salt)
        if new_material is not None and merged.get("encryption_enabled"):
            self._save_encryption_material(new_material)
        elif not merged.get("encryption_enabled"):
            self._delete_encryption_material()
        self._save_json(self.settings_file, merged)
        return True, self._public_settings(merged)

    # ── Encryption key material ─────────────────────────────────────────────

    def _load_key_file(self) -> Dict:
        data = self._load_json(self.encryption_key_file, {})
        return data if isinstance(data, dict) else {}

    def _write_key_file(self, data: Dict) -> None:
        tmp = self.encryption_key_file + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.encryption_key_file)

    def _save_encryption_material(self, material: "buddy_crypto.KeyMaterial") -> None:
        data = self._load_key_file()
        data["current"] = material.to_json()
        self._write_key_file(data)

    def _delete_encryption_material(self) -> None:
        data = self._load_key_file()
        if "current" in data:
            data.pop("current", None)
            self._write_key_file(data)

    def _remember_legacy_salt(self, salt: str) -> None:
        data = self._load_key_file()
        salts = [s for s in data.get("legacy_v1_salts", []) if isinstance(s, str)]
        if salt not in salts:
            salts.append(salt)
        data["legacy_v1_salts"] = salts[-20:]
        self._write_key_file(data)

    def _encryption_material(self) -> Optional["buddy_crypto.KeyMaterial"]:
        current = self._load_key_file().get("current")
        if isinstance(current, dict):
            return buddy_crypto.KeyMaterial.from_json(current)
        return None

    def _ensure_encryption_material(self, passphrase: str) -> None:
        """Create ALVAENC2 key material for installs that predate it."""
        settings = self.get_settings(include_secret=True)
        if not settings.get("encryption_enabled") or self._encryption_material() is not None:
            return
        try:
            self._save_encryption_material(buddy_crypto.new_key_material(passphrase))
        except Exception as exc:
            print(f"Buddy backup: could not create encryption key material: {exc}")

    def _decrypt_stream_with_passphrase(self, encrypted_path: str, plain_path: str,
                                        passphrase: str) -> Tuple[bool, str]:
        """Decrypt a downloaded stream using only the passphrase.

        ALVAENC2 streams carry their key-derivation salt, so this works on a
        freshly installed machine. Legacy ALVAENC1 streams can only be opened
        with salts this machine has seen (current or earlier passwords).
        """
        try:
            kind = buddy_crypto.is_encrypted_stream(encrypted_path)
            if kind == "v2":
                buddy_crypto.decrypt_file(
                    encrypted_path, plain_path,
                    lambda h: buddy_crypto.derive_master_key(passphrase, h.kdf_salt, h.log2_n, h.r, h.p),
                )
                return True, ""
            if kind == "v1":
                settings = self.get_settings(include_secret=True)
                salts = [str(settings.get("encryption_salt") or "").strip()]
                salts += list(reversed(self._load_key_file().get("legacy_v1_salts", [])))
                for salt in [s for s in salts if s]:
                    digest, _ = self._hash_passphrase(passphrase, salt=salt)
                    try:
                        buddy_crypto.decrypt_legacy_v1_file(
                            encrypted_path, plain_path, buddy_crypto.legacy_v1_key(digest, salt)
                        )
                        return True, ""
                    except buddy_crypto.BuddyCryptoError:
                        continue
                return False, ("Wrong encryption password. Snapshots in the old encryption format can "
                               "only be restored on the machine that created them.")
            return False, "The downloaded snapshot is not in a known encrypted format"
        except buddy_crypto.BuddyCryptoError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Failed to decrypt snapshot: {exc}"

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
            "enabled": False,
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
            "enabled": False,
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
            ok = secrets.compare_digest(digest, str(settings.get("encryption_hash")))
        except Exception:
            return False
        if ok:
            self._ensure_encryption_material(passphrase)
        return ok

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

    def _ensure_restore_collision_not_default(
        self,
        collision_path: str,
        mount_root: str,
        btrfs_cmd: str,
    ) -> Tuple[bool, str]:
        target = os.path.normpath(str(collision_path or "").strip())
        root = os.path.normpath(str(mount_root or "").strip())
        if not target or not root:
            return False, "Invalid restore collision path"
        if not self._path_is_btrfs_subvolume(target):
            return True, ""

        collision_id = self._get_subvolume_id(target)
        if not collision_id:
            return True, ""
        default_id = self._get_default_subvolume_id(root)
        if not default_id or collision_id != default_id:
            return True, ""

        active_id = self._get_subvolume_id(root)
        if not active_id:
            return False, (
                f"Restore target default subvolume points to existing snapshot ({target}), "
                "but mounted subvolume ID could not be resolved"
            )
        if active_id == collision_id:
            return False, (
                f"Restore collision subvolume is currently mounted/default and cannot be deleted in place: {target}"
            )

        set_res, set_err = self.run_command(
            [btrfs_cmd, "subvolume", "set-default", str(active_id), root],
            timeout=120,
        )
        if set_err or not set_res or set_res.returncode != 0:
            return False, set_err or "Failed to switch default subvolume before cleanup"
        return True, ""

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
        if os.path.exists(source):
            return source

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

    def _move_path_best_effort(self, source: str, destination: str, timeout: int = 120) -> Tuple[bool, str]:
        src = os.path.normpath(str(source or "").strip())
        dst = os.path.normpath(str(destination or "").strip())
        if not src or not dst:
            return False, "Invalid move path"

        try:
            os.rename(src, dst)
            return True, ""
        except Exception as exc:
            last_error = str(exc)

        mv_cmd = self._mv_cmd()
        if mv_cmd:
            mv_res, mv_err = self.run_command([mv_cmd, src, dst], timeout=timeout)
            if not mv_err and mv_res and mv_res.returncode == 0:
                return True, ""
            last_error = mv_err or last_error


        return False, last_error or f"Failed to move {src} -> {dst}"

    def _is_mounted_path(self, path: str) -> bool:
        target = os.path.normpath(str(path or "").strip())
        if not target:
            return False
        try:
            return os.path.ismount(target)
        except Exception:
            return False

    def _get_subvolume_id(self, path: str) -> Optional[int]:
        target = os.path.normpath(str(path or "").strip())
        if not target:
            return None
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return None
        res, err = self.run_command([btrfs_cmd, "subvolume", "show", target], timeout=20)
        if err or not res or res.returncode != 0:
            return None
        for line in (res.stdout or "").splitlines():
            if "Subvolume ID:" in line:
                try:
                    return int(line.split(":", 1)[1].strip())
                except Exception:
                    return None
        return None

    def _get_default_subvolume_id(self, path: str) -> Optional[int]:
        target = os.path.normpath(str(path or "").strip())
        if not target:
            return None
        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return None
        res, err = self.run_command([btrfs_cmd, "subvolume", "get-default", target], timeout=20)
        if err or not res or res.returncode != 0:
            return None
        m = re.search(r"ID\s+(\d+)", (res.stdout or "").strip())
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

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

        # An unset incoming_path must not block receiving: fall back to the
        # service-owned state dir so a buddy can receive streams even before
        # the receiving node has explicitly configured a local incoming path.
        preferred_root = self._stream_root_path()
        fallback_root = self._fallback_stream_root_path()
        candidates = []
        if preferred_root:
            candidates.append(preferred_root)
        if fallback_root and fallback_root not in candidates:
            candidates.append(fallback_root)
        if not candidates:
            return None, None, "Local incoming path is not configured or not allowed"

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
                    verify=False,  # buddy nodes use self-signed certs
                )
                body: Dict[str, Any] = {}
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
            verify_tls = False  # buddy nodes use self-signed certs
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
        limit_bytes = self._owner_quota_bytes(owner)

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

    # Every AlvaOS backend listens on this port; peers reach it through the tunnel.
    PEER_API_PORT = 8080

    def _peer_api_urls(self, peer: Dict, path: str) -> List[str]:
        """URLs for talking to a paired buddy: only ever through the WireGuard tunnel.

        The backend speaks plain HTTP, so going to the buddy's public address
        would send the buddy secret and snapshot data unencrypted over the
        internet and require exposing the admin port. Inside the tunnel,
        WireGuard encrypts the traffic and authenticates the buddy by the
        public key exchanged during pairing.
        """
        path_part = str(path or "").strip()
        if not path_part.startswith("/"):
            path_part = f"/{path_part}"
        tunnel_ip = str(peer.get("tunnel_ip") or "").strip()
        if not IPV4_RE.match(tunnel_ip):
            return []
        return [f"http://{tunnel_ip}:{self.PEER_API_PORT}{path_part}"]

    def peer_for_tunnel_ip(self, remote_ip: str) -> Optional[Dict]:
        """The paired buddy that owns this tunnel address, if any.

        WireGuard only accepts packets from a tunnel IP when they are signed
        by that peer's key (AllowedIPs = tunnel_ip/32), so the source address
        of a request inside the tunnel identifies the buddy.
        """
        ip = str(remote_ip or "").strip()
        if not IPV4_RE.match(ip):
            return None
        for peer in self._load_peers().values():
            if isinstance(peer, dict) and str(peer.get("tunnel_ip") or "").strip() == ip:
                return peer
        return None

    def start_tunnel_if_paired(self) -> None:
        """Bring the tunnel up at startup; wg-quick state does not survive a reboot."""
        if platform.system() != "Linux" or not self._load_peers():
            return
        ok, result = self.apply_tunnel_config()
        if not ok:
            print(f"Buddy backup: tunnel not started: {result.get('error')}")

    # ── Recovery kit ────────────────────────────────────────────────────────
    # A fresh install gets a new node id and new keys, so its buddies would not
    # recognise it and it could not see its old snapshots. The recovery kit
    # carries this node's identity (node id, WireGuard keys, buddy secret,
    # tunnel address) and its buddy list, sealed with a password. Importing it
    # makes the new machine the same node again; the buddies need no change.

    RECOVERY_KIT_KIND = "alvaos-buddy-recovery"

    def _recovery_kit_fingerprint(self) -> str:
        identity = self._load_identity()
        peers = self._load_peers()
        material = json.dumps({
            "node_id": identity.get("node_id"),
            "public_key": identity.get("public_key"),
            "peers": sorted((nid, str(p.get("public_key") or "")) for nid, p in peers.items()),
        }, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def export_recovery_kit(self, passphrase: str) -> Tuple[bool, Dict]:
        if len(passphrase or "") < 8:
            return False, {"error": "Choose a recovery kit password with at least 8 characters"}
        self._identity_public()   # make sure a complete identity exists
        identity = self._identity_private()
        peers = self._load_peers()
        kit: Dict[str, Any] = {
            "kind": self.RECOVERY_KIT_KIND,
            "v": 1,
            "created_at": self._now_iso(),
            "identity": {
                key: identity.get(key)
                for key in ("node_id", "name", "private_key", "public_key", "api_secret",
                            "tunnel_ip", "listen_port")
            },
            "peers": peers,
        }
        try:
            blob = buddy_crypto.seal_json(kit, passphrase)
        except buddy_crypto.BuddyCryptoError as exc:
            return False, {"error": str(exc)}
        self._update_runtime({
            "recovery_kit_exported_at": kit["created_at"],
            "recovery_kit_fingerprint": self._recovery_kit_fingerprint(),
        })
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", str(identity.get("name") or "alvaos")).strip("-") or "alvaos"
        return True, {
            "kit": blob,
            "filename": f"{name}-buddy-recovery-kit.txt",
            "created_at": kit["created_at"],
            "peer_count": len(peers),
        }

    def recovery_kit_status(self) -> Dict:
        runtime = self._load_runtime()
        exported_at = str(runtime.get("recovery_kit_exported_at") or "")
        return {
            "exported_at": exported_at,
            # A kit made before the last pairing change would restore an
            # incomplete buddy list.
            "up_to_date": bool(exported_at)
            and runtime.get("recovery_kit_fingerprint") == self._recovery_kit_fingerprint(),
        }

    def _validated_kit_identity(self, raw: Any) -> Dict:
        if not isinstance(raw, dict):
            raise ValueError("identity is missing")
        node_id = str(raw.get("node_id") or "")
        private_key = str(raw.get("private_key") or "")
        public_key = str(raw.get("public_key") or "")
        api_secret = str(raw.get("api_secret") or "")
        tunnel_ip = str(raw.get("tunnel_ip") or "")
        if not re.fullmatch(r"[0-9a-f]{8,64}", node_id):
            raise ValueError("invalid node id")
        if not (WG_KEY_RE.match(private_key) and WG_KEY_RE.match(public_key)):
            raise ValueError("invalid WireGuard key")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", api_secret):
            raise ValueError("invalid buddy secret")
        if not IPV4_RE.match(tunnel_ip):
            raise ValueError("invalid tunnel address")
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

            derived = X25519PrivateKey.from_private_bytes(base64.b64decode(private_key)).public_key()
            derived_b64 = base64.b64encode(
                derived.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            ).decode("ascii")
        except Exception:
            raise ValueError("invalid WireGuard key") from None
        if derived_b64 != public_key:
            raise ValueError("WireGuard keys do not belong together")
        try:
            listen_port = max(1024, min(65535, int(raw.get("listen_port") or self.default_listen_port)))
        except (TypeError, ValueError):
            listen_port = self.default_listen_port
        name = re.sub(r"[^\w .-]+", "", str(raw.get("name") or ""))[:64] or self._hostname()
        return {
            "node_id": node_id, "name": name, "private_key": private_key, "public_key": public_key,
            "api_secret": api_secret, "tunnel_ip": tunnel_ip, "listen_port": listen_port,
        }

    def _validated_kit_peers(self, raw: Any) -> Dict[str, Dict]:
        if not isinstance(raw, dict):
            raise ValueError("buddy list is missing")
        peers: Dict[str, Dict] = {}
        for node_id, peer in raw.items():
            if not isinstance(peer, dict) or not re.fullmatch(r"[0-9a-f]{8,64}", str(node_id)):
                raise ValueError("invalid buddy entry")
            public_key = str(peer.get("public_key") or "")
            tunnel_ip = str(peer.get("tunnel_ip") or "")
            endpoint = str(peer.get("endpoint") or "")
            api_endpoint = str(peer.get("api_endpoint") or "")
            if not (WG_KEY_RE.match(public_key) and IPV4_RE.match(tunnel_ip)):
                raise ValueError(f"invalid keys for buddy {node_id}")
            if endpoint and not WG_ENDPOINT_RE.match(endpoint):
                raise ValueError(f"invalid endpoint for buddy {node_id}")
            if api_endpoint and not WG_ENDPOINT_RE.match(api_endpoint):
                api_endpoint = ""
            api_secret = str(peer.get("api_secret") or "")
            if api_secret and not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", api_secret):
                raise ValueError(f"invalid secret for buddy {node_id}")
            peers[str(node_id)] = {
                "node_id": str(node_id),
                "name": re.sub(r"[^\w .-]+", "", str(peer.get("name") or node_id))[:64],
                "public_key": public_key,
                "api_secret": api_secret,
                "tunnel_ip": tunnel_ip,
                "listen_port": peer.get("listen_port") or self.default_listen_port,
                "endpoint": endpoint,
                "api_endpoint": api_endpoint,
                "last_paired_at": str(peer.get("last_paired_at") or ""),
                "status": "configured",
                "last_error": "",
            }
        return peers

    @staticmethod
    def _kit_restored_message(peer_count: int) -> str:
        if peer_count == 0:
            return "Identity restored. The kit contained no buddies yet."
        buddies = "1 buddy" if peer_count == 1 else f"{peer_count} buddies"
        return (f"Identity restored with {buddies}. Your snapshots on them now appear under "
                "\"Restore from a buddy\".")

    def import_recovery_kit(self, kit: str, passphrase: str, replace: bool = False) -> Tuple[bool, Dict]:
        try:
            data = buddy_crypto.open_json(kit, passphrase)
        except buddy_crypto.BuddyCryptoError as exc:
            return False, {"error": str(exc)}
        except Exception:
            return False, {"error": "The recovery kit could not be read"}
        if data.get("kind") != self.RECOVERY_KIT_KIND or data.get("v") != 1:
            return False, {"error": "This recovery kit comes from an unsupported AlvaOS version"}
        try:
            identity_fields = self._validated_kit_identity(data.get("identity"))
            peers = self._validated_kit_peers(data.get("peers"))
        except ValueError as exc:
            return False, {"error": f"The recovery kit is not valid: {exc}"}

        current_peers = self._load_peers()
        current = self._load_identity()
        if current_peers and current.get("node_id") != identity_fields["node_id"] and not replace:
            return False, {
                "error": "This NAS already has buddies of its own. Importing replaces its identity "
                         "and buddy list; confirm to continue.",
                "needs_confirmation": True,
            }

        identity = dict(current) if isinstance(current, dict) else {}
        identity.update(identity_fields)
        identity.update({"key_source": "wireguard", "key_error": "", "restored_at": self._now_iso()})
        identity.setdefault("created_at", self._now_iso())
        with self._transfer_lock:
            self._save_identity(identity)
            self._save_peers(peers)
        tunnel_ok, tunnel_result = self.apply_tunnel_config()
        self._update_runtime({
            "recovery_kit_exported_at": str(data.get("created_at") or ""),
            "recovery_kit_fingerprint": self._recovery_kit_fingerprint(),
        })
        return True, {
            "node_id": identity_fields["node_id"],
            "peer_count": len(peers),
            "tunnel": tunnel_result if tunnel_ok else {"error": tunnel_result.get("error", "")},
            "message": self._kit_restored_message(len(peers)),
        }

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
            if not public_key or not tunnel_ip:
                continue
            # These values come from the remote buddy. A newline in any of them
            # would let a peer inject wg-quick directives such as PostUp (a root
            # shell command), so only accept their exact expected shapes.
            if not (WG_KEY_RE.match(public_key) and IPV4_RE.match(tunnel_ip)):
                continue
            if endpoint and not WG_ENDPOINT_RE.match(endpoint):
                continue
            lines.extend(["[Peer]", f"PublicKey = {public_key}", f"AllowedIPs = {tunnel_ip}/32"])
            # A buddy behind NAT has no reachable endpoint; it connects to us
            # and WireGuard learns its address from the handshake.
            if endpoint:
                lines.append(f"Endpoint = {endpoint}")
            lines.extend(["PersistentKeepalive = 25", ""])

        return "\n".join(lines).strip() + "\n"

    def apply_tunnel_config(self) -> Tuple[bool, Dict]:
        if platform.system() != "Linux":
            return False, {"error": "The Buddy Backup tunnel is only available on the AlvaOS NAS itself (Linux)"}

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
            return {"state": "unsupported", "message": "WireGuard is only available on the AlvaOS NAS itself (Linux)"}

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
        remote_secret: str = "",
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
                # Proves we received the remote's pairing code: the remote only
                # accepts reciprocal pairing from someone who knows its secret.
                headers={"Content-Type": "application/json", "X-Buddy-Secret": remote_secret},
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
        if decode_err or payload is None:
            return False, {"error": decode_err or "Invalid pairing token"}

        if self._is_token_used(token):
            return False, {"error": "This pairing token was already used"}

        peer, peer_err = self._token_to_peer(payload, endpoint_override=endpoint_override, name_override=name_override)
        if peer_err or peer is None:
            return False, {"error": peer_err or "Invalid pairing token"}

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
                "enabled": False,
                "interval_minutes": int(settings.get("interval_minutes", DEFAULT_BUDDY_SETTINGS["interval_minutes"])),
                "send_time": "02:00",
                "max_storage_gb": int(settings.get("incoming_quota_gb", DEFAULT_BUDDY_SETTINGS["incoming_quota_gb"])),
                "outgoing_sources": [],
                "updated_at": self._now_iso(),
            }
            settings["peer_policies"] = policies
            settings["updated_at"] = self._now_iso()
            self._save_json(self.settings_file, self._merge_settings(settings))

        # Apply even without an endpoint: a buddy behind NAT connects to us.
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

        reciprocal: Dict[str, Any] = {"skipped": True}
        if auto_reciprocal:
            remote_api_endpoint = str(peer.get("api_endpoint") or "").strip()
            if remote_api_endpoint:
                reciprocal_ok, reciprocal_payload = self._attempt_reciprocal_pair(
                    remote_api_endpoint=remote_api_endpoint,
                    local_wg_endpoint=local_wg_endpoint,
                    local_api_endpoint=local_api_endpoint,
                    remote_secret=str(peer.get("api_secret") or ""),
                )
                reciprocal = {"success": reciprocal_ok, **reciprocal_payload}
            else:
                reciprocal = {"skipped": True, "reason": "Remote API endpoint is not available in token"}

        return True, {
            "peer": peer,
            "tunnel_result": tunnel_result,
            "reciprocal": reciprocal,
        }

    # ── Receiving snapshots from a buddy ─────────────────────────────────────
    # A snapshot arrives as start → chunk, chunk, … → finish. No single request
    # is large (the HTTP server buffers each request body before the route
    # runs), chunks go straight to the partial file on the incoming pool, and
    # quota and free space are checked before each chunk rather than after
    # the whole snapshot. Each chunk names its offset, so a chunk that is sent
    # twice after a network hiccup is recognised instead of corrupting data.
    UPLOAD_CHUNK_BYTES = 32 * 1024 * 1024
    UPLOAD_STALE_SECONDS = 6 * 3600
    PARTIAL_PREFIX = ".partial-"

    def _owner_quota_bytes(self, owner_node_id: str) -> int:
        settings = self.get_settings(include_secret=True)
        policies = settings.get("peer_policies", {})
        policy = policies.get(owner_node_id, {}) if isinstance(policies, dict) else {}
        try:
            limit_gb = int(policy.get("max_storage_gb", settings.get("incoming_quota_gb", 200)))
        except Exception:
            limit_gb = 200
        return max(1, limit_gb) * (1024 ** 3)

    def _free_bytes(self, path: str) -> int:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize

    def _free_space_reserve_bytes(self, path: str) -> int:
        """Space a buddy may never use up: 2 GiB or 2 % of the filesystem."""
        st = os.statvfs(path)
        return max(2 * 1024 ** 3, (st.f_blocks * st.f_frsize) // 50)

    def _discard_upload(self, upload: Dict) -> None:
        try:
            os.remove(upload["path"])
        except OSError:
            pass

    def _drop_stale_uploads(self, owner_dir: str = "") -> None:
        now = time.time()
        with self._uploads_lock:
            stale = [uid for uid, item in self._uploads.items()
                     if now - item["touched"] > self.UPLOAD_STALE_SECONDS]
            dropped = [self._uploads.pop(uid) for uid in stale]
            active = {item["path"] for item in self._uploads.values()}
        for item in dropped:
            self._discard_upload(item)
        if not owner_dir:
            return
        # Partial files whose upload was lost, e.g. because the backend restarted.
        try:
            names = os.listdir(owner_dir)
        except OSError:
            return
        for name in names:
            path = os.path.join(owner_dir, name)
            if not name.startswith(self.PARTIAL_PREFIX) or path in active:
                continue
            try:
                if now - os.path.getmtime(path) > self.UPLOAD_STALE_SECONDS:
                    os.remove(path)
            except OSError:
                pass

    def _find_upload(self, owner_node_id: str, upload_id: str) -> Optional[Dict]:
        with self._uploads_lock:
            upload = self._uploads.get(str(upload_id or ""))
        if upload is None or upload["owner"] != owner_node_id:
            return None
        return upload

    def begin_peer_upload(
        self,
        owner_node_id: str,
        source_path: str,
        snapshot_name: str,
        created_at: str,
        encrypted: bool,
    ) -> Tuple[bool, Dict]:
        owner = str(owner_node_id or "").strip()
        source = str(source_path or "").strip()
        snap_name = str(snapshot_name or "").strip()
        if not owner:
            return False, {"error": "owner_node_id is required"}
        if not source.startswith("/"):
            return False, {"error": "source_path must be an absolute path"}
        if not snap_name:
            return False, {"error": "snapshot_name is required"}
        if owner not in self._load_peers():
            return False, {"error": "Unknown owner_node_id"}

        root, owner_dir, select_err = self._select_writable_stream_owner_dir(owner)
        if not root or not owner_dir:
            return False, {"error": select_err or "Failed to prepare writable buddy stream path"}
        self._drop_stale_uploads(owner_dir)

        # A buddy sends one snapshot at a time; an unfinished earlier upload
        # from it is dead and only holds disk space.
        with self._uploads_lock:
            replaced = [uid for uid, item in self._uploads.items() if item["owner"] == owner]
            dropped = [self._uploads.pop(uid) for uid in replaced]
        for item in dropped:
            self._discard_upload(item)

        upload_id = secrets.token_hex(16)
        path = os.path.join(owner_dir, f"{self.PARTIAL_PREFIX}{upload_id}")
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except OSError as exc:
            return False, {"error": f"Failed to start receiving snapshot: {exc}"}
        with self._uploads_lock:
            self._uploads[upload_id] = {
                "owner": owner,
                "path": path,
                "owner_dir": owner_dir,
                "source_path": source,
                "snapshot_name": snap_name,
                "created_at": str(created_at or "").strip() or self._now_iso(),
                "encrypted": bool(encrypted),
                "size": 0,
                "sha": hashlib.sha256(),
                "touched": time.time(),
                "lock": threading.Lock(),
            }
        return True, {"upload_id": upload_id, "chunk_bytes": self.UPLOAD_CHUNK_BYTES}

    def append_peer_upload(self, owner_node_id: str, upload_id: str, offset: int, body) -> Tuple[bool, Dict]:
        """Append one chunk. Errors carry an HTTP "status" for the API layer."""
        owner = str(owner_node_id or "").strip()
        upload = self._find_upload(owner, upload_id)
        if upload is None:
            return False, {"error": "Unknown or expired upload", "status": 404}
        if not upload["lock"].acquire(blocking=False):
            return False, {"error": "Another chunk of this upload is still being written", "status": 409}
        try:
            start = upload["size"]
            if offset != start:
                return False, {"error": "Chunk offset does not match the data received so far",
                               "status": 409, "expected_offset": start}
            quota = self._owner_quota_bytes(owner)
            owner_dir = upload["owner_dir"]
            try:
                if self._free_bytes(owner_dir) - self.UPLOAD_CHUNK_BYTES < self._free_space_reserve_bytes(owner_dir):
                    return False, {"error": "Not enough free space on this NAS to keep the buddy's snapshot",
                                   "status": 507}
            except OSError as exc:
                return False, {"error": f"Could not check free space: {exc}", "status": 500}

            sha = upload["sha"].copy()
            written = 0
            rejected: Optional[Tuple[int, str]] = None
            try:
                with open(upload["path"], "r+b") as dst:
                    dst.seek(start)
                    while True:
                        block = body.read(1024 * 1024)
                        if not block:
                            break
                        written += len(block)
                        if written > self.UPLOAD_CHUNK_BYTES:
                            rejected = (413, "Chunk is larger than allowed")
                            break
                        if start + written > quota:
                            rejected = (413, (f"Snapshot is larger than the {quota // 1024 ** 3} GB "
                                              "this NAS keeps for this buddy"))
                            break
                        dst.write(block)
                        sha.update(block)
            except OSError as exc:
                rejected = (507 if exc.errno == 28 else 500, f"Failed to store snapshot data: {exc}")
            if rejected is not None:
                try:
                    os.truncate(upload["path"], start)
                except OSError:
                    pass
                return False, {"error": rejected[1], "status": rejected[0]}

            upload["size"] = start + written
            upload["sha"] = sha
            upload["touched"] = time.time()
            return True, {"received": upload["size"]}
        finally:
            upload["lock"].release()

    def finish_peer_upload(self, owner_node_id: str, upload_id: str, size: int, sha256: str) -> Tuple[bool, Dict]:
        owner = str(owner_node_id or "").strip()
        upload = self._find_upload(owner, upload_id)
        if upload is None:
            return False, {"error": "Unknown or expired upload", "status": 404}
        with upload["lock"]:
            with self._uploads_lock:
                self._uploads.pop(upload_id, None)
            digest = upload["sha"].hexdigest()
            if upload["size"] != size or not hmac.compare_digest(digest, str(sha256 or "").strip().lower()):
                self._discard_upload(upload)
                return False, {"error": "Snapshot arrived incomplete or damaged; it was discarded", "status": 422}

            stream_id = f"{int(self._now().timestamp())}-{secrets.token_hex(4)}"
            ext = ".enc" if upload["encrypted"] else ".stream"
            payload_path = os.path.join(upload["owner_dir"], f"{stream_id}{ext}")
            try:
                os.replace(upload["path"], payload_path)
            except OSError as exc:
                self._discard_upload(upload)
                return False, {"error": f"Failed to store snapshot: {exc}", "status": 500}

        entry = {
            "id": stream_id,
            "owner_node_id": owner,
            "from_node_id": owner,
            "source_path": upload["source_path"],
            "snapshot_name": upload["snapshot_name"],
            "created_at": upload["created_at"],
            "received_at": self._now_iso(),
            "encrypted": upload["encrypted"],
            "size_bytes": int(size),
            "sha256": digest,
            "payload_path": payload_path,
        }
        entries = self._load_stream_entries()
        entries.append(entry)
        self._save_stream_entries(entries)
        self._enforce_stream_quota(owner)

        sender_peer = self._load_peers().get(owner)
        sender_name = str((sender_peer or {}).get("name") or owner)
        push_notification(
            severity="info",
            title="Buddy backup received",
            message=f'Received a backup snapshot from buddy "{sender_name}" ({upload["snapshot_name"]}).',
            source="backup",
            dismissible=True,
            link="backup.html",
        )
        return True, {"stream": self._public_stream_entry(entry)}

    def abort_peer_upload(self, owner_node_id: str, upload_id: str) -> Tuple[bool, Dict]:
        owner = str(owner_node_id or "").strip()
        upload = self._find_upload(owner, upload_id)
        if upload is None:
            return False, {"error": "Unknown or expired upload", "status": 404}
        with upload["lock"]:
            with self._uploads_lock:
                self._uploads.pop(upload_id, None)
            self._discard_upload(upload)
        return True, {"aborted": upload_id}

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

    def _take_send_snapshot(self, source_path: str) -> Tuple[bool, Dict]:
        """Read-only snapshot of a source to send from. The caller removes it."""
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
        if not self._path_is_btrfs_subvolume(source):
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

        snap_res, snap_err = self.run_command(
            [btrfs_cmd, "subvolume", "snapshot", "-r", source, temp_snapshot],
            timeout=240,
        )
        if (snap_err or not snap_res or snap_res.returncode != 0) and "invalid cross-device link" in str(snap_err or "").lower():
            # Retry once inside source path; some pool root paths have parent dirs on another device.
            fallback_snapshot = os.path.join(source, temp_name)
            if fallback_snapshot != temp_snapshot:
                temp_snapshot = fallback_snapshot
                snap_res, snap_err = self.run_command(
                    [btrfs_cmd, "subvolume", "snapshot", "-r", source, temp_snapshot],
                    timeout=240,
                )
        if snap_err or not snap_res or snap_res.returncode != 0:
            return False, {"error": snap_err or "Failed to create temporary snapshot"}
        return True, {
            "btrfs_cmd": btrfs_cmd,
            "snapshot_path": temp_snapshot,
            "snapshot_name": temp_name,
            "created_at": self._now_iso(),
        }

    def _peer_request(self, peer: Dict, method: str, path: str, timeout: int = 60, **kwargs) -> Tuple[int, Dict]:
        """One request to a buddy through the tunnel: (HTTP status, JSON body)."""
        urls = self._peer_api_urls(peer, path)
        if not urls:
            raise _TransferError("Peer API endpoint is not configured")
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["X-Buddy-Secret"] = str(peer.get("api_secret") or "").strip()
        response = requests.request(method, urls[0], headers=headers, timeout=timeout, **kwargs)
        try:
            body = response.json() if response.content else {}
        except Exception:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    def _send_chunk(self, peer: Dict, upload_id: str, offset: int, data: bytes) -> None:
        """Send one chunk, retrying network errors. A chunk is never applied twice:
        the receiver reports the offset it expects, which tells whether a chunk
        whose answer got lost did arrive."""
        path = f"/api/v1/backup/buddy/peer/upload/{upload_id}"
        last_error = "Upload failed"
        for attempt in range(self.UPLOAD_ATTEMPTS):
            if attempt:
                time.sleep(2 ** attempt)
            try:
                status, body = self._peer_request(
                    peer, "PUT", path, timeout=300, params={"offset": str(offset)}, data=data,
                    headers={"Content-Type": "application/octet-stream"},
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                continue
            if 200 <= status < 300:
                return
            if status == 409 and body.get("expected_offset") == offset + len(data):
                return  # the previous attempt arrived; only its answer was lost
            if status == 409 and body.get("expected_offset") is None:
                last_error = body.get("error") or "Buddy is busy with this upload"
                continue
            raise _TransferError(body.get("error") or f"HTTP {status}")
        raise _TransferError(last_error)

    def _stream_snapshot_to_peer(self, peer: Dict, snapshot: Dict, meta: Dict,
                                 material: Optional["buddy_crypto.KeyMaterial"]) -> Tuple[bool, Dict]:
        """Pipe `btrfs send` into the buddy chunk by chunk, encrypting on the way.

        Neither the plain nor the encrypted stream is ever written to local
        disk, so snapshots can be as large as the buddy is willing to keep.
        """
        if not str(peer.get("api_secret") or "").strip():
            return False, {"error": "Peer is missing API secret. Re-pair to enable transfer."}
        try:
            status, body = self._peer_request(peer, "POST", "/api/v1/backup/buddy/peer/upload/start",
                                              json={**meta, "encrypted": material is not None})
        except (requests.RequestException, _TransferError) as exc:
            return False, {"error": str(exc)}
        upload_id = str(body.get("upload_id") or "")
        if not (200 <= status < 300) or not upload_id:
            return False, {"error": body.get("error") or f"HTTP {status}"}
        chunk_bytes = max(1024 * 1024, min(self.UPLOAD_CHUNK_BYTES, int(body.get("chunk_bytes") or 0)))

        proc, stderr = self.spawn_privileged([snapshot["btrfs_cmd"], "send", snapshot["snapshot_path"]])
        encryptor = buddy_crypto.StreamEncryptor(material) if material is not None else None
        digest = hashlib.sha256()
        offset = 0
        pending = bytearray()

        def flush(final: bool) -> None:
            nonlocal offset
            while len(pending) >= chunk_bytes or (final and pending):
                data = bytes(pending[:chunk_bytes])
                del pending[:chunk_bytes]
                self._send_chunk(peer, upload_id, offset, data)
                digest.update(data)
                offset += len(data)

        try:
            assert proc.stdout is not None
            for block in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                pending.extend(encryptor.update(block) if encryptor else block)
                flush(final=False)
            if proc.wait() != 0:
                raise _TransferError(f"btrfs send failed: {_read_stderr(stderr) or f'exit code {proc.returncode}'}")
            if encryptor:
                pending.extend(encryptor.finalize())
            flush(final=True)
            status, body = self._peer_request(
                peer, "POST", f"/api/v1/backup/buddy/peer/upload/{upload_id}/finish",
                json={"size": offset, "sha256": digest.hexdigest()},
            )
            if not (200 <= status < 300):
                raise _TransferError(body.get("error") or f"HTTP {status}")
            return True, body
        except (requests.RequestException, _TransferError, OSError, buddy_crypto.BuddyCryptoError) as exc:
            try:
                self._peer_request(peer, "DELETE", f"/api/v1/backup/buddy/peer/upload/{upload_id}", timeout=15)
            except Exception:
                pass
            return False, {"error": str(exc)}
        finally:
            _stop_process(proc)
            stderr.close()

    def sync_to_peer(
        self,
        node_id: str,
        sources: Optional[List[str]] = None,
        include_system: bool = False,
    ) -> Tuple[bool, Dict]:
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

            # Full-system transfer is an explicit, manual choice only. Scheduled/policy
            # sources stay data-only (system paths are stripped when saved), so a full
            # root copy is never sent automatically without the user asking for it.
            if include_system:
                selected_sources = [SYSTEM_SOURCE_PATH] + selected_sources

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

            if SYSTEM_SOURCE_PATH in selected_sources and not self._path_is_btrfs_subvolume(SYSTEM_SOURCE_PATH):
                return False, {"error": "Full system transfer requires a Btrfs root subvolume"}

            identity = self._identity_public()
            local_node_id = str(identity.get("node_id") or "")
            settings_now = self.get_settings(include_secret=True)
            encryption_enabled = bool(settings_now.get("encryption_enabled"))

            material = self._encryption_material() if encryption_enabled else None
            # Installs whose password predates ALVAENC2 have no key material
            # until the password is entered once; sending unencrypted instead
            # is not an option.
            upgrade_error = "" if (material is not None or not encryption_enabled) else (
                "Buddy backups use a new encryption format. Enter your encryption password "
                "once in the Buddy Backup settings to switch over."
            )

            created = []
            failed = []
            for source_path in selected_sources:
                if upgrade_error:
                    failed.append({"source_path": source_path, "error": upgrade_error})
                    continue
                snap_ok, snapshot = self._take_send_snapshot(source_path)
                if not snap_ok:
                    failed.append({"source_path": source_path, "error": snapshot.get("error", "Failed to snapshot source")})
                    continue
                meta = {
                    "owner_node_id": local_node_id,
                    "source_path": source_path,
                    "snapshot_name": snapshot["snapshot_name"],
                    "created_at": snapshot["created_at"],
                }
                try:
                    upload_ok, upload_payload = self._stream_snapshot_to_peer(peer, snapshot, meta, material)
                finally:
                    cleanup_ok, cleanup_err = self._cleanup_temp_subvolume(
                        snapshot["snapshot_path"], snapshot["btrfs_cmd"], attempts=3
                    )
                    if not cleanup_ok:
                        print(f"Buddy temp snapshot cleanup warning: {cleanup_err}")
                if upload_ok:
                    created.append({
                        "source_path": source_path,
                        "snapshot_name": meta["snapshot_name"],
                        "encrypted": material is not None,
                        "remote": upload_payload.get("stream", {}),
                    })
                else:
                    failed.append({"source_path": source_path, "error": upload_payload.get("error", "Upload failed")})

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

            peer_name = str(peer.get("name") or target)
            if status == "success":
                push_notification(
                    severity="success",
                    title="Buddy backup completed",
                    message=f'Backup to buddy "{peer_name}" completed successfully ({len(created)} source(s) sent).',
                    source="backup",
                    dismissible=True,
                    link="backup.html",
                )
            else:
                push_notification(
                    severity="critical" if status == "error" else "warning",
                    title="Buddy backup failed" if status == "error" else "Buddy backup partially failed",
                    message=f'Backup to buddy "{peer_name}" {"failed" if status == "error" else "partially failed"}: {sync_error or "unknown error"}',
                    source="backup",
                    dismissible=True,
                    link="backup.html",
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
            verify_tls = False  # buddy nodes use self-signed certs
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
            verify_tls = False  # buddy nodes use self-signed certs
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

    def _open_remote_stream(self, peer: Dict, stream_id: str, owner_node_id: str):
        """Start downloading one of our snapshots from a buddy (streamed response)."""
        remote_secret = str(peer.get("api_secret") or "").strip()
        urls = self._peer_api_urls(peer, f"/api/v1/backup/buddy/peer/download/{stream_id}")
        if not remote_secret or not urls:
            raise _TransferError("Peer API is not configured")
        try:
            response = requests.get(
                urls[0],
                headers={"X-Buddy-Secret": remote_secret},
                params={"owner_node_id": owner_node_id},
                timeout=300,
                stream=True,
            )
        except requests.RequestException as exc:
            raise _TransferError(f"Download failed: {exc}") from exc
        if not (200 <= response.status_code < 300):
            try:
                error = (response.json() or {}).get("error")
            except Exception:
                error = None
            response.close()
            raise _TransferError(error or f"Download failed: HTTP {response.status_code}")
        return response

    @staticmethod
    def _checked_stream(chunks: Iterator[bytes], expected_sha256: str) -> Iterator[bytes]:
        """Pass data through, holding the last piece back until the checksum matched.

        `btrfs receive` only completes a snapshot when it sees the end of the
        stream, so a download that is cut short or altered never becomes a
        finished snapshot.
        """
        digest = hashlib.sha256()
        previous = b""
        for chunk in chunks:
            if not chunk:
                continue
            digest.update(chunk)
            if previous:
                yield previous
            previous = chunk
        if expected_sha256 and not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
            raise _TransferError("The downloaded snapshot does not match its checksum")
        if previous:
            yield previous

    @staticmethod
    def _decrypted_stream(chunks: Iterator[bytes], passphrase: str) -> Iterator[bytes]:
        decryptor = buddy_crypto.StreamDecryptor(
            lambda h: buddy_crypto.derive_master_key(passphrase, h.kdf_salt, h.log2_n, h.r, h.p)
        )
        for chunk in chunks:
            plain = decryptor.update(chunk)
            if plain:
                yield plain
        yield decryptor.finalize()

    def _receive_stream(self, btrfs_cmd: str, stream: Union[str, Iterator[bytes]],
                        target_parent: str) -> Tuple[bool, str]:
        """`btrfs receive` from a file path or by piping an iterator of bytes."""
        if isinstance(stream, str):
            res, err = self.run_command([btrfs_cmd, "receive", "-f", stream, target_parent], timeout=None)
            if err or not res or res.returncode != 0:
                return False, err or "Failed to receive Btrfs stream"
            return True, ""

        proc, stderr = self.spawn_privileged([btrfs_cmd, "receive", target_parent], stdin_pipe=True)
        feed_error = ""
        try:
            assert proc.stdin is not None
            try:
                for chunk in stream:
                    proc.stdin.write(chunk)
            except BrokenPipeError:
                pass  # receive stopped early; its exit status and stderr say why
            except (_TransferError, buddy_crypto.BuddyCryptoError, requests.RequestException, OSError) as exc:
                feed_error = str(exc)
            finally:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            returncode = proc.wait()
            if feed_error:
                return False, feed_error
            if returncode != 0:
                return False, _read_stderr(stderr) or f"btrfs receive failed (exit code {returncode})"
            return True, ""
        finally:
            stderr.close()

    def _restore_from_stream(self, stream: Union[str, Iterator[bytes]], snapshot_name: str,
                             source_path: str) -> Tuple[bool, Dict]:
        """Receive a snapshot stream (file path or iterator of bytes) and put it in place."""
        target_source = str(source_path or "").strip()
        if not target_source.startswith("/"):
            return False, {"error": "source_path must be an absolute path"}
        if platform.system() != "Linux":
            return False, {"error": "Restores are only possible on the AlvaOS NAS itself (Linux)"}
        target_is_mount_root = self._is_mounted_path(target_source)

        btrfs_cmd = self._btrfs_cmd()
        if not btrfs_cmd:
            return False, {"error": "Missing required system command (btrfs)"}

        target_parent = target_source if target_is_mount_root else (os.path.dirname(target_source.rstrip("/")) or "/")
        if not target_is_mount_root:
            ok_parent, parent_err = self._mkdir_p(target_parent)
            if not ok_parent:
                return False, {"error": parent_err or "Failed to prepare target parent"}
        expected_name = str(snapshot_name or "").strip()

        # If an old receive left the same temporary snapshot behind, remove it first.
        if expected_name:
            expected_path = os.path.normpath(os.path.join(target_parent, expected_name))
            if self._path_is_within_parent(expected_path, target_parent) and expected_path != os.path.normpath(target_parent):
                if target_is_mount_root:
                    def_ok, def_err = self._ensure_restore_collision_not_default(
                        collision_path=expected_path,
                        mount_root=target_source,
                        btrfs_cmd=btrfs_cmd,
                    )
                    if not def_ok:
                        return False, {"error": def_err or "Failed to prepare restore target default subvolume"}
                cleanup_ok, cleanup_err, _ = self._delete_subvolume_if_exists(expected_path, btrfs_cmd)
                if not cleanup_ok:
                    return False, {"error": cleanup_err or f"Failed to prepare restore target: {expected_path}"}

        before_names = set(self._list_btrfs_subvolume_names_under(target_parent))

        recv_ok, recv_err = self._receive_stream(btrfs_cmd, stream, target_parent)
        if not recv_ok:
            # A streamed receive cannot be replayed, so collisions are cleared
            # up front (above); from a file one retry is still possible.
            collision_name = self._extract_receive_exists_name(recv_err) if isinstance(stream, str) else ""
            retried = False
            if collision_name:
                collision_path = os.path.normpath(os.path.join(target_parent, collision_name))
                if self._path_is_within_parent(collision_path, target_parent) and collision_path != os.path.normpath(target_parent):
                    if target_is_mount_root:
                        def_ok, def_err = self._ensure_restore_collision_not_default(
                            collision_path=collision_path,
                            mount_root=target_source,
                            btrfs_cmd=btrfs_cmd,
                        )
                        if not def_ok:
                            return False, {"error": def_err or "Failed to prepare receive collision cleanup"}
                    cleanup_ok, cleanup_err, cleaned = self._delete_subvolume_if_exists(collision_path, btrfs_cmd)
                    if cleanup_ok and cleaned:
                        retried = True
                        recv_ok, recv_err = self._receive_stream(btrfs_cmd, stream, target_parent)
                    elif not cleanup_ok:
                        return False, {"error": cleanup_err or f"Failed to clear existing receive subvolume: {collision_path}"}
            if not retried or not recv_ok:
                # Do not leave a half-received subvolume behind.
                new_partial = set(self._list_btrfs_subvolume_names_under(target_parent)) - before_names
                for name in new_partial:
                    self._delete_subvolume_if_exists(os.path.join(target_parent, name), btrfs_cmd)
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

        if target_is_mount_root:
            previous_default_id = self._get_default_subvolume_id(target_source)
            received_id = self._get_subvolume_id(received_path)
            if not received_id:
                return False, {"error": f"Received snapshot has no resolvable subvolume ID: {received_path}"}
            set_res, set_err = self.run_command(
                [btrfs_cmd, "subvolume", "set-default", str(received_id), target_source],
                timeout=120,
            )
            if set_err or not set_res or set_res.returncode != 0:
                return False, {"error": set_err or "Failed to switch default subvolume for mounted target"}
            return True, {
                "restored_to": target_source,
                "previous_backup": None,
                "previous_default_subvolume_id": previous_default_id,
                "new_default_subvolume_id": received_id,
                "pending_remount": True,
                "message": (
                    "Rollback prepared. Default subvolume switched to restored snapshot. "
                    "Unmount/mount the pool or reboot to activate it."
                ),
            }

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        previous_path = f"{target_source}.pre-restore-{stamp}"
        moved_old = False

        if os.path.exists(target_source):
            move_ok, move_err = self._move_path_best_effort(target_source, previous_path, timeout=120)
            if not move_ok:
                return False, {"error": move_err or "Failed to stage current data for restore"}
            moved_old = True

        snap_res, snap_err = self.run_command(
            [btrfs_cmd, "subvolume", "snapshot", received_path, target_source],
            timeout=300,
        )
        if snap_err or not snap_res or snap_res.returncode != 0:
            if moved_old and not os.path.exists(target_source):
                self._move_path_best_effort(previous_path, target_source, timeout=120)
            return False, {"error": snap_err or "Failed to apply restored snapshot"}

        self.run_command([btrfs_cmd, "subvolume", "delete", received_path], timeout=180)
        return True, {
            "restored_to": target_source,
            "previous_backup": previous_path if moved_old else None,
            "message": "Restore completed",
        }

    def _legacy_v1_to_file(self, body: Iterator[bytes], passphrase: str,
                           temp_paths: List[str]) -> Tuple[bool, str]:
        """Download and decrypt an ALVAENC1 snapshot; returns the plain file path."""
        fd, encrypted_tmp = tempfile.mkstemp(prefix="buddy-restore-", suffix=".enc", dir=self.state_dir)
        temp_paths.append(encrypted_tmp)
        try:
            with os.fdopen(fd, "wb") as dst:
                for chunk in body:
                    dst.write(chunk)
        except (requests.RequestException, OSError) as exc:
            return False, f"Download failed: {exc}"
        fd, plain_tmp = tempfile.mkstemp(prefix="buddy-restore-", suffix=".stream", dir=self.state_dir)
        os.close(fd)
        temp_paths.append(plain_tmp)
        ok, err = self._decrypt_stream_with_passphrase(encrypted_tmp, plain_tmp, passphrase)
        return (True, plain_tmp) if ok else (False, err or "Failed to decrypt remote snapshot stream")

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
            if encrypted and not encryption_passphrase:
                return False, {"error": "Encryption password is required for remote restore"}
            # No local password check here: on a freshly installed machine there
            # are no local settings yet. Decryption itself proves the password.

            identity = self._identity_public()
            owner_node_id = str(identity.get("node_id") or "")
            temp_paths: List[str] = []
            response = None
            try:
                try:
                    response = self._open_remote_stream(peer, sid, owner_node_id)
                    chunks = response.iter_content(chunk_size=1024 * 1024)
                    head = b""
                    for chunk in chunks:
                        head += chunk
                        if len(head) >= 8:
                            break
                except (_TransferError, requests.RequestException) as exc:
                    return False, {"error": str(exc) or "Failed to download remote snapshot stream"}
                body = itertools.chain([head], chunks)

                stream: Union[str, Iterator[bytes]]
                if not encrypted:
                    stream = self._checked_stream(body, str(stream_entry.get("sha256") or ""))
                elif head[:8] == buddy_crypto.MAGIC_V2:
                    # Every chunk is authenticated before it reaches btrfs receive.
                    plain = self._decrypted_stream(body, encryption_passphrase)
                    try:
                        # Check the password before anything on disk is touched.
                        first = next(plain, b"")
                    except (buddy_crypto.BuddyCryptoError, requests.RequestException) as exc:
                        return False, {"error": str(exc)}
                    stream = itertools.chain([first], plain)
                elif head[:8] == buddy_crypto.MAGIC_V1:
                    # The old format has no per-chunk authentication, so it is
                    # downloaded and checked completely before it is used.
                    ok, result = self._legacy_v1_to_file(body, encryption_passphrase, temp_paths)
                    if not ok:
                        return False, {"error": result}
                    stream = result
                else:
                    return False, {"error": "The downloaded snapshot is not in a known encrypted format"}

                restore_ok, restore_payload = self._restore_from_stream(
                    stream,
                    snapshot_name=str(stream_entry.get("snapshot_name") or ""),
                    source_path=restore_source,
                )
                if restore_ok and encrypted:
                    # Upgrades pre-ALVAENC2 installs once the right password is known.
                    self.verify_encryption_passphrase(encryption_passphrase)
                now_iso = self._now_iso()
                if restore_ok:
                    self._update_runtime({
                        "last_restore_at": now_iso,
                        "last_restore_status": "success",
                        "last_restore_error": "",
                    })
                    result_message = str((restore_payload or {}).get("message") or "").strip()
                    pending_remount = bool((restore_payload or {}).get("pending_remount"))
                    return True, {
                        "message": (
                            result_message
                            or ("Remote restore prepared. Remount/reboot required." if pending_remount else "Remote restore completed")
                        ),
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
                if response is not None:
                    response.close()
                for path in temp_paths:
                    try:
                        os.remove(path)
                    except OSError:
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
            ping_err_text = str(ping_err or "").lower()
            ping_permission_blocked = (
                "passwordless sudo is not configured" in ping_err_text
                or "a password is required" in ping_err_text
                or "password is required" in ping_err_text
            )
            if ping_permission_blocked:
                # Ping is optional for connection test. Do not fail when sudo policy blocks ping.
                ping_payload["attempted"] = False
                ping_payload["success"] = False
                ping_payload["error"] = ""
                ping_payload["permission_blocked"] = True
            else:
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
                    "enabled": False,
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
