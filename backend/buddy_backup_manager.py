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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


class BuddyBackupManager:
    def __init__(self, run_command: Callable):
        self.run_command = run_command
        self.state_dir = self._resolve_state_dir()
        self.identity_file = os.path.join(self.state_dir, "buddy_identity.json")
        self.peers_file = os.path.join(self.state_dir, "buddy_peers.json")
        self.tokens_file = os.path.join(self.state_dir, "buddy_tokens.json")
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
            ["/usr/bin/wg", "/usr/sbin/wg", "/bin/wg", "/sbin/wg"],
            "wg"
        )

    def _wg_quick_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/wg-quick", "/usr/sbin/wg-quick", "/bin/wg-quick", "/sbin/wg-quick"],
            "wg-quick"
        )

    def _bash_cmd(self) -> Optional[str]:
        return self._detect_cmd(
            ["/usr/bin/bash", "/bin/bash"],
            "bash"
        )

    def _derive_tunnel_ip(self, node_id: str) -> str:
        # Deterministic host assignment in a private /24.
        digest = hashlib.sha256((node_id or "").encode("utf-8")).digest()
        host_octet = 2 + (digest[0] % 253)  # 2..254
        return f"10.77.77.{host_octet}"

    def _generate_wg_keypair(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        if platform.system() != "Linux":
            priv = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")[:44]
            pub = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")[:44]
            return priv, pub, None

        wg_cmd = self._wg_cmd()
        bash_cmd = self._bash_cmd()
        if not wg_cmd:
            return None, None, "WireGuard command not found (wg)"
        if not bash_cmd:
            return None, None, "Bash command not found"

        gen_res, gen_err = self.run_command([wg_cmd, "genkey"], timeout=10)
        if gen_err or not gen_res or gen_res.returncode != 0:
            return None, None, gen_err or "Failed to generate WireGuard private key"
        private_key = (gen_res.stdout or "").strip()
        if not private_key:
            return None, None, "WireGuard private key generation returned empty output"

        pub_cmd = f"printf '%s' {shlex.quote(private_key)} | {shlex.quote(wg_cmd)} pubkey"
        pub_res, pub_err = self.run_command([bash_cmd, "-lc", pub_cmd], timeout=10)
        if pub_err or not pub_res or pub_res.returncode != 0:
            return None, None, pub_err or "Failed to derive WireGuard public key"
        public_key = (pub_res.stdout or "").strip()
        if not public_key:
            return None, None, "WireGuard public key generation returned empty output"

        return private_key, public_key, None

    def _create_identity(self) -> Dict:
        node_id = secrets.token_hex(8)
        private_key, public_key, key_err = self._generate_wg_keypair()
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
        elif not identity.get("public_key") or not identity.get("private_key"):
            private_key, public_key, key_err = self._generate_wg_keypair()
            if private_key and public_key:
                identity["private_key"] = private_key
                identity["public_key"] = public_key
                identity["key_error"] = ""
                self._save_identity(identity)
            else:
                identity["key_error"] = key_err or identity.get("key_error", "")
                self._save_identity(identity)
        return {
            "node_id": identity.get("node_id", ""),
            "name": identity.get("name", ""),
            "public_key": identity.get("public_key", ""),
            "tunnel_ip": identity.get("tunnel_ip", ""),
            "listen_port": identity.get("listen_port", self.default_listen_port),
            "created_at": identity.get("created_at"),
            "updated_at": identity.get("updated_at"),
            "key_error": identity.get("key_error", ""),
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

    def generate_pairing_token(self, endpoint: str = "", expires_minutes: int = 20) -> Tuple[bool, Dict]:
        identity = self._identity_public()
        if not identity.get("public_key"):
            return False, {"error": identity.get("key_error") or "WireGuard identity is not ready"}

        try:
            ttl = int(expires_minutes)
        except Exception:
            ttl = 20
        ttl = max(1, min(240, ttl))

        normalized_endpoint, endpoint_err = self._normalize_endpoint(endpoint)
        if endpoint_err:
            return False, {"error": endpoint_err}

        issued_at = self._now()
        expires_at = issued_at + timedelta(minutes=ttl)
        payload = {
            "v": 1,
            "node_id": identity.get("node_id"),
            "name": identity.get("name"),
            "public_key": identity.get("public_key"),
            "tunnel_ip": identity.get("tunnel_ip"),
            "listen_port": identity.get("listen_port", self.default_listen_port),
            "endpoint": normalized_endpoint,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "nonce": secrets.token_hex(8),
        }
        token = self._token_encode(payload)
        return True, {
            "token": token,
            "expires_at": payload["expires_at"],
            "identity": identity,
        }

    def _token_to_peer(self, payload: Dict, endpoint_override: str = "", name_override: str = "") -> Tuple[Optional[Dict], Optional[str]]:
        required = ["node_id", "public_key", "tunnel_ip", "expires_at"]
        for field in required:
            if not payload.get(field):
                return None, f"Invalid pairing token: missing {field}"

        expires_at = self._parse_iso(payload.get("expires_at"))
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

        wg_cmd = self._wg_cmd()
        wg_quick_cmd = self._wg_quick_cmd()
        if not wg_cmd or not wg_quick_cmd:
            return False, {"error": "WireGuard tools (wg/wg-quick) are not installed"}

        peers = self._load_peers()
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

        active_peers = [
            p for p in peers.values()
            if str(p.get("endpoint") or "").strip() and str(p.get("public_key") or "").strip()
        ]
        if not active_peers:
            return True, {"message": "No active peers configured"}

        up_res, up_err = self.run_command([wg_quick_cmd, "up", self.wg_config_path], timeout=40)
        if up_err or not up_res or up_res.returncode != 0:
            return False, {"error": up_err or "Failed to bring up WireGuard interface"}

        show_res, show_err = self.run_command([wg_cmd, "show", self.interface_name], timeout=10)
        if show_err:
            return True, {"message": "Tunnel configured, but runtime status unavailable", "warning": show_err}
        return True, {"message": "Tunnel configured", "runtime": (show_res.stdout or "").strip()[:1200]}

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

    def validate_pairing_token(
        self,
        token: str,
        endpoint_override: str = "",
        name_override: str = ""
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

        return True, {
            "peer": peer,
            "tunnel_result": tunnel_result,
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
        ok, tunnel_result = self.apply_tunnel_config()
        if not ok:
            return False, {"error": tunnel_result.get("error", "Peer removed, but tunnel reconfigure failed")}
        return True, {"removed": removed, "tunnel_result": tunnel_result}

    def restart_tunnel(self) -> Tuple[bool, Dict]:
        return self.apply_tunnel_config()

    def get_status(self) -> Dict:
        identity = self._identity_public()
        peers_map = self._load_peers()
        peers = sorted(peers_map.values(), key=lambda item: str(item.get("name", "")).lower())
        runtime = self._runtime_status()
        wg_cmd = self._wg_cmd()
        wg_quick_cmd = self._wg_quick_cmd()
        supported = platform.system() == "Linux" and bool(wg_cmd and wg_quick_cmd and identity.get("public_key"))

        return {
            "supported": supported,
            "identity": identity,
            "peers": peers,
            "tunnel": runtime,
            "requirements": {
                "linux": platform.system() == "Linux",
                "wg_cmd": wg_cmd or "",
                "wg_quick_cmd": wg_quick_cmd or "",
            },
        }
