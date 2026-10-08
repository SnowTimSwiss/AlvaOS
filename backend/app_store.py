#!/usr/bin/env python3
"""
AlvaOS App Store Manager
Manages app catalog and installation
"""

import json
import copy
import hashlib
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests
from common import run_sudo_command
from docker_manager import DockerManager


def summarize_app_needs(app_data: Dict) -> Dict:
    """What an app needs from the NAS, for the store card before installing:
    the ports it opens and how many folders it keeps on your storage."""
    schema = app_data.get('config_schema') if isinstance(app_data, dict) else None
    schema = schema if isinstance(schema, dict) else {}
    ports = []
    for port in schema.get('ports') or []:
        if not isinstance(port, dict):
            continue
        external = port.get('external')
        if isinstance(external, int) and 0 < external < 65536:
            ports.append({
                'port': external,
                'protocol': str(port.get('protocol') or 'tcp'),
                'description': str(port.get('description') or ''),
            })
    folders = [str(v.get('description') or v.get('container_path') or '')
               for v in schema.get('volumes') or [] if isinstance(v, dict)]
    return {'ports': ports, 'folders': folders}


def normalize_port_mappings(port_mappings) -> Tuple[Optional[Dict[int, int]], Optional[str]]:
    """{inside port: port on the NAS} as numbers, or a sentence saying why not."""
    if not port_mappings:
        return {}, None
    if not isinstance(port_mappings, dict):
        return None, 'port_mappings must be an object of source:target ports'
    normalized: Dict[int, int] = {}
    try:
        for key, value in port_mappings.items():
            src = int(str(key).strip())
            dst = int(str(value).strip())
            if src <= 0 or dst <= 0:
                raise ValueError("ports must be positive integers")
            normalized[src] = dst
    except (TypeError, ValueError):
        return None, 'Invalid port_mappings format. Use positive integer source/target ports.'
    return normalized, None


def validate_install_paths(pool_path, volume_mappings, port_mappings, pools_state) -> Optional[str]:
    """Where an app may keep its files: on a managed pool, and any folder of
    yours it uses must be inside a pool too. Returns a sentence, or None.

    The privilege helper already refuses system paths in compose files; this
    says no earlier and more clearly, and keeps apps on the pools."""
    mounts = [os.path.normpath(str(p.get('mount_point')))
              for p in (pools_state or {}).values()
              if isinstance(p, dict) and p.get('mount_point') and p.get('mount_point') != '/']

    def inside_a_pool(path: str) -> bool:
        if not isinstance(path, str) or not path.startswith('/') or '..' in path.split('/'):
            return False
        clean = os.path.normpath(path)
        return any(clean == m or clean.startswith(m.rstrip('/') + '/') for m in mounts)

    if os.path.normpath(str(pool_path or '')) not in mounts:
        return 'Choose one of your storage pools for this app.'
    if volume_mappings:
        if not isinstance(volume_mappings, dict):
            return 'Folders must be given as container path: folder on the NAS.'
        for container_path, host_path in volume_mappings.items():
            if not str(container_path).startswith('/'):
                return f'"{container_path}" is not a folder inside the app.'
            if not inside_a_pool(host_path):
                return f'{host_path} is not a folder on one of your storage pools.'
    for internal, external in (port_mappings or {}).items():
        if not (0 < int(internal) < 65536 and 0 < int(external) < 65536):
            return 'Ports are numbers from 1 to 65535.'
    return None


class AppStore:
    """Manages the AlvaOS app catalog and installation"""
    
    def __init__(self):
        self.docker_manager = DockerManager()
        
        # Paths
        self.catalog_file = self._get_catalog_path()
        self.apps_state_file = '/var/lib/alvaos/apps_state.json'
        self.install_status_file = '/var/lib/alvaos/app_install_status.json'
        self.update_cache_file = '/var/lib/alvaos/app_update_cache.json'
        self.update_cache_ttl_seconds = 1800
        
        # Status tracking
        import threading
        self._status_lock = threading.RLock()
        self._update_cache_lock = threading.RLock()
        self._active_install_status = None
        self._last_status_write = 0.0
        
        # Ensure directories exist
        Path('/var/lib/alvaos').mkdir(parents=True, exist_ok=True)
    
    def _get_catalog_path(self) -> str:
        """Get the app catalog path (production or development)"""
        prod_path = '/opt/alvaos/apps/catalog.json'
        dev_path = os.path.join(os.path.dirname(__file__), '..', 'apps', 'catalog.json')
        
        if os.path.exists(prod_path):
            return prod_path
        return dev_path
    
    def _load_catalog(self) -> Tuple[Optional[Dict], Optional[str]]:
        """Load the app catalog from disk"""
        try:
            if not os.path.exists(self.catalog_file):
                return {}, None
            
            with open(self.catalog_file, 'r') as f:
                catalog = json.load(f)
            return catalog, None
        except json.JSONDecodeError as e:
            return None, f"Invalid catalog JSON: {str(e)}"
        except Exception as e:
            return None, str(e)

    def _load_update_cache(self) -> Dict:
        """Load cached app update probe results from disk."""
        with self._update_cache_lock:
            try:
                if os.path.exists(self.update_cache_file):
                    with open(self.update_cache_file, 'r') as f:
                        data = json.load(f)
                    return data if isinstance(data, dict) else {}
            except Exception as e:
                print(f"Error loading app update cache: {e}")
        return {}

    def _save_update_cache(self, cache: Dict) -> None:
        """Persist cached app update probe results to disk."""
        with self._update_cache_lock:
            try:
                with open(self.update_cache_file, 'w') as f:
                    json.dump(cache or {}, f, indent=2)
            except Exception as e:
                print(f"Error saving app update cache: {e}")

    def _read_cached_update_status(self, app_id: str, allow_stale: bool = False) -> Optional[Dict]:
        """Return cached update status for an app if present and fresh enough."""
        cache = self._load_update_cache()
        entry = cache.get(str(app_id)) if isinstance(cache, dict) else None
        if not isinstance(entry, dict):
            return None

        checked_at = float(entry.get('checked_at_ts') or 0)
        if not allow_stale:
            if checked_at <= 0 or (time.time() - checked_at) > self.update_cache_ttl_seconds:
                return None

        return dict(entry)

    def _write_cached_update_status(self, app_id: str, payload: Dict) -> None:
        """Store update status metadata for a single app."""
        cache = self._load_update_cache()
        cache[str(app_id)] = dict(payload or {})
        self._save_update_cache(cache)

    def _is_operation_in_progress(self) -> Tuple[bool, Optional[str]]:
        """Check whether an app install/update operation is active."""
        current_status = self.get_install_status()
        if current_status.get('status') in ('starting', 'installing', 'updating'):
            return True, current_status.get('app_id')
        return False, None

    def _build_compose_config(
        self,
        app_details: Dict,
        port_mappings: Optional[Dict[int, int]] = None,
        volume_mappings: Optional[Dict[str, str]] = None,
        environment_vars: Optional[Dict[str, str]] = None,
        gpu_plan: Optional[Dict] = None
    ) -> Dict:
        """Build a mutable compose config with user mappings applied."""
        compose_config = copy.deepcopy(app_details.get('docker_compose', {}) or {})
        if gpu_plan:
            import gpu_manager
            gpu_manager.apply_plan(compose_config, gpu_plan)

        if port_mappings:
            for service_config in compose_config.get('services', {}).values():
                if 'ports' not in service_config:
                    continue
                new_ports = []
                for port_spec in service_config['ports']:
                    port_str = str(port_spec)
                    try:
                        if ':' in port_str:
                            parts = port_str.split(':')
                            container_part = parts[1]
                            internal_port_raw = container_part.split('/')[0]
                            internal_port = int(internal_port_raw)
                            if internal_port in port_mappings:
                                protocol = ""
                                if '/' in container_part:
                                    protocol = "/" + container_part.split('/')[1]
                                new_ports.append(f"{port_mappings[internal_port]}:{internal_port}{protocol}")
                            else:
                                new_ports.append(port_spec)
                        else:
                            internal_port_raw = port_str.split('/')[0]
                            internal_port = int(internal_port_raw)
                            if internal_port in port_mappings:
                                protocol = ""
                                if '/' in port_str:
                                    protocol = "/" + port_str.split('/')[1]
                                new_ports.append(f"{port_mappings[internal_port]}:{internal_port}{protocol}")
                            else:
                                new_ports.append(port_spec)
                    except Exception:
                        new_ports.append(port_spec)
                service_config['ports'] = new_ports

        if volume_mappings:
            for service_config in compose_config.get('services', {}).values():
                if 'volumes' not in service_config:
                    continue
                new_volumes = []
                for volume_spec in service_config['volumes']:
                    if ':' in str(volume_spec):
                        parts = str(volume_spec).split(':')
                        container_path = parts[-1]
                        if container_path in volume_mappings:
                            new_volumes.append(f"{volume_mappings[container_path]}:{container_path}")
                        else:
                            new_volumes.append(volume_spec)
                    else:
                        new_volumes.append(volume_spec)
                service_config['volumes'] = new_volumes

        if environment_vars:
            for service_config in compose_config.get('services', {}).values():
                if 'environment' not in service_config:
                    service_config['environment'] = []
                if isinstance(service_config['environment'], list):
                    env_dict = {}
                    for env_item in service_config['environment']:
                        if '=' in str(env_item):
                            key, value = str(env_item).split('=', 1)
                            env_dict[key] = value
                    service_config['environment'] = env_dict
                service_config['environment'].update(environment_vars)

        # Last line of defence: nothing leaves this function still holding a
        # placeholder secret, whichever install path built it.
        leftover = self._find_placeholder_secrets(compose_config)
        if leftover:
            raise ValueError(
                "Refusing to build a container configuration with placeholder "
                f"secrets: {', '.join(leftover)}"
            )

        return compose_config

    PLACEHOLDER_SECRETS = frozenset({'CHANGEME', 'CHANGE_ME', 'CHANGEME!', 'PLEASE_CHANGE'})

    def _validate_required_environment(
        self,
        app_details: Dict,
        environment_vars: Optional[Dict[str, str]]
    ) -> Optional[str]:
        """Return an error message if a required env field is missing or still a placeholder.

        Apps used to ship with a literal CHANGEME as their database and admin
        passwords, and nothing stopped that value from reaching a running
        container. Required fields must now carry a real value before install.
        """
        schema = ((app_details or {}).get('config_schema') or {})
        environment_schema = schema.get('environment') or []
        provided = {str(k): str(v) for k, v in (environment_vars or {}).items()}

        missing = []
        for entry in environment_schema:
            key = str(entry.get('key', '')).strip()
            if not key or not entry.get('required'):
                continue
            value = provided.get(key, '').strip()
            if not value or value.upper() in self.PLACEHOLDER_SECRETS:
                missing.append((key, entry.get('description') or key))

        if missing:
            names = ', '.join(key for key, _ in missing)
            return (
                f"This app needs a value for: {names}. "
                "Fill these in before installing - leaving the placeholder would "
                "start the app with a publicly known password."
            )
        return None

    def _find_placeholder_secrets(self, compose_config: Dict) -> list:
        """List env keys in a built compose config that still hold a placeholder."""
        found = []
        for service_name, service_config in (compose_config.get('services') or {}).items():
            env = (service_config or {}).get('environment') or {}
            pairs = env.items() if isinstance(env, dict) else (
                str(item).split('=', 1) for item in env if '=' in str(item)
            )
            for key, value in pairs:
                if str(value).strip().upper() in self.PLACEHOLDER_SECRETS:
                    found.append(f"{service_name}.{key}")
        return found

    def _normalize_custom_app_id(self, value: str) -> str:
        """Convert a free-form app name/id to a stable app identifier."""
        normalized = str(value or "").strip().lower()
        normalized = re.sub(r'[^a-z0-9._-]+', '-', normalized)
        normalized = re.sub(r'[-_.]{2,}', '-', normalized)
        normalized = normalized.strip('-_.')
        if len(normalized) > 64:
            normalized = normalized[:64].rstrip('-_.')
        return normalized

    def _infer_environment_vars_from_running_containers(self, app_id: str, app_details: Dict) -> Dict[str, str]:
        """
        Infer configured environment values from currently running/stopped containers.
        Only schema-defined keys are extracted.
        """
        schema = ((app_details or {}).get('config_schema') or {})
        environment_schema = schema.get('environment') or []
        keys = [str(entry.get('key', '')).strip() for entry in environment_schema if str(entry.get('key', '')).strip()]
        if not keys:
            return {}

        key_set = set(keys)
        inferred: Dict[str, str] = {}
        project_name = f"alvaos-{app_id}"

        containers, error = self.docker_manager.list_containers(all_containers=True)
        if error or not containers:
            return {}

        for container in containers:
            labels = str(container.get('Labels', '') or '')
            if f'com.docker.compose.project={project_name}' not in labels:
                continue
            container_id = container.get('ID') or str(container.get('Names', '')).split(',')[0]
            if not container_id:
                continue
            details, det_err = self.docker_manager.get_container_details(str(container_id))
            if det_err or not details:
                continue
            env_list = ((details.get('Config') or {}).get('Env') or [])
            for item in env_list:
                item_str = str(item)
                if '=' not in item_str:
                    continue
                key, value = item_str.split('=', 1)
                if key in key_set and key not in inferred:
                    inferred[key] = value
            if len(inferred) == len(key_set):
                break

        return inferred
    
    def _load_apps_state(self) -> Dict:
        """Load installed apps state"""
        try:
            if os.path.exists(self.apps_state_file):
                with open(self.apps_state_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading apps state: {e}")
        return {}
    
    def _save_apps_state(self, state: Dict) -> None:
        """Save installed apps state"""
        try:
            with open(self.apps_state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Error saving apps state: {e}")

    def get_install_status(self) -> Dict:
        """Get the current installation status (from memory if active, else from disk)"""
        with self._status_lock:
            if self._active_install_status:
                return self._active_install_status.copy()
            
        try:
            if os.path.exists(self.install_status_file):
                with open(self.install_status_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading install status: {e}")
        return {"status": "idle"}

    def _update_install_status(self, app_id: str, status: str, progress: int = 0, message: str = "", logs: Optional[List[str]] = None, force_write: bool = False, action: str = "install"):
        """Update app operation status with in-memory tracking and throttled disk writes"""
        import time
        
        with self._status_lock:
            if logs is None:
                if self._active_install_status:
                    logs = self._active_install_status.get('logs', [])
                else:
                    # Try to get from last known status if we just started.
                    # Read from disk directly here to avoid nested lock acquisition.
                    current: Dict[str, Any] = {}
                    try:
                        if os.path.exists(self.install_status_file):
                            with open(self.install_status_file, 'r') as f:
                                current = json.load(f) or {}
                    except Exception:
                        current = {}
                    logs = current.get('logs', [])
            
            # Keep only last 100 log lines
            if len(logs) > 100:
                logs = logs[-100:]

            payload = {
                "app_id": app_id,
                "action": action,
                "status": status,
                "progress": progress,
                "message": message,
                "logs": logs,
                "updated_at": datetime.now().isoformat()
            }
            
            self._active_install_status = payload
            
            # Determine if we should write to disk
            now = time.time()
            should_write = force_write or (now - self._last_status_write > 1.5) or status in ('success', 'error', 'idle')
            
            if should_write:
                try:
                    with open(self.install_status_file, 'w') as f:
                        json.dump(payload, f, indent=2)
                    self._last_status_write = now
                except Exception as e:
                    print(f"Error saving install status: {e}")
            
            # If finished, we can eventually clear the active status, 
            # but maybe keep it for a while so polling can pick up the final state.
            # For now, we keep it in memory until the next install starts.
    
    def get_available_apps(self) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """Get list of available apps from catalog"""
        catalog, error = self._load_catalog()
        if error or catalog is None:
            return None, error or "App catalog is unavailable"
        
        apps = []
        for app_id, app_data in catalog.items():
            apps.append({
                'id': app_id,
                'name': app_data.get('name', app_id),
                'description': app_data.get('description', ''),
                'icon': app_data.get('icon', ''),
                'category': app_data.get('category', 'Other'),
                'version': app_data.get('version', 'latest'),
                'needs': summarize_app_needs(app_data),
            })
        
        return apps, None
    
    def get_app_details(self, app_id: str) -> Tuple[Optional[Dict], Optional[str]]:
        """Get detailed information about an app"""
        catalog, error = self._load_catalog()
        if error or catalog is None:
            return None, error or "App catalog is unavailable"
        
        if app_id not in catalog:
            return None, f"App '{app_id}' not found in catalog"
        
        return catalog[app_id], None

    def _tokenize_version(self, value: str) -> List[Any]:
        """Split a version string into comparable numeric/text tokens."""
        cleaned = re.sub(r'^[^0-9a-zA-Z]+', '', str(value or '').strip().lower())
        if not cleaned:
            return []

        tokens: List[object] = []
        for part in re.findall(r'[0-9]+|[a-z]+', cleaned):
            if part.isdigit():
                tokens.append(int(part))
            else:
                tokens.append(part)
        return tokens

    def _compare_versions(self, left: str, right: str) -> Optional[int]:
        """Compare two catalog version strings."""
        left_value = str(left or '').strip()
        right_value = str(right or '').strip()
        if not left_value or not right_value:
            return None

        left_lower = left_value.lower()
        right_lower = right_value.lower()
        special_values = {'latest', 'stable', 'edge', 'nightly', 'main', 'master'}
        if left_lower in special_values or right_lower in special_values:
            return None

        left_tokens = self._tokenize_version(left_value)
        right_tokens = self._tokenize_version(right_value)
        if not left_tokens or not right_tokens:
            return None

        max_len = max(len(left_tokens), len(right_tokens))
        for index in range(max_len):
            left_token = left_tokens[index] if index < len(left_tokens) else 0
            right_token = right_tokens[index] if index < len(right_tokens) else 0

            if type(left_token) is type(right_token):
                if left_token < right_token:
                    return -1
                if left_token > right_token:
                    return 1
                continue

            left_cmp = str(left_token)
            right_cmp = str(right_token)
            if left_cmp < right_cmp:
                return -1
            if left_cmp > right_cmp:
                return 1

        return 0

    def _extract_compose_images(self, app_details: Dict) -> List[str]:
        """Return unique image references used by an app's compose definition."""
        images: List[str] = []
        seen = set()
        services = ((app_details or {}).get('docker_compose') or {}).get('services') or {}
        if not isinstance(services, dict):
            return images

        for service_config in services.values():
            image = str((service_config or {}).get('image') or '').strip()
            if not image or image in seen:
                continue
            seen.add(image)
            images.append(image)
        return images

    def _parse_image_reference(self, image: str) -> Optional[Dict]:
        """Normalize an image reference into registry/repository/reference parts."""
        value = str(image or '').strip()
        if not value:
            return None

        registry = 'docker.io'
        remainder = value
        first_segment = value.split('/', 1)[0]
        if '.' in first_segment or ':' in first_segment or first_segment == 'localhost':
            registry, _, remainder = value.partition('/')

        reference = 'latest'
        repository = remainder
        if '@' in remainder:
            repository, reference = remainder.split('@', 1)
        else:
            last_slash = remainder.rfind('/')
            last_colon = remainder.rfind(':')
            if last_colon > last_slash:
                repository = remainder[:last_colon]
                reference = remainder[last_colon + 1:]

        if registry == 'docker.io' and '/' not in repository:
            repository = f'library/{repository}'

        if not repository:
            return None

        return {
            'image': value,
            'registry': registry,
            'repository': repository,
            'reference': reference or 'latest'
        }

    def _parse_www_authenticate(self, header_value: str) -> Dict[str, str]:
        """Parse a WWW-Authenticate bearer challenge into key/value pairs."""
        value = str(header_value or '').strip()
        if not value.lower().startswith('bearer '):
            return {}

        attrs: Dict[str, str] = {}
        for key, attr_value in re.findall(r'([A-Za-z_]+)="([^"]*)"', value):
            attrs[key.lower()] = attr_value
        return attrs

    def _registry_request(self, method: str, url: str, headers: Dict[str, str], timeout: int, repository: str) -> requests.Response:
        """Perform a registry request, following bearer auth challenges when needed."""
        response = requests.request(method, url, headers=headers, timeout=timeout)
        if response.status_code != 401:
            return response

        auth_attrs = self._parse_www_authenticate(response.headers.get('WWW-Authenticate', ''))
        realm = auth_attrs.get('realm')
        if not realm:
            return response

        params = {}
        if auth_attrs.get('service'):
            params['service'] = auth_attrs['service']
        params['scope'] = auth_attrs.get('scope') or f'repository:{repository}:pull'

        token_response = requests.get(realm, params=params, timeout=timeout, headers={
            'User-Agent': 'AlvaOS-AppStore/1.0'
        })
        token_response.raise_for_status()
        token_payload = token_response.json() if token_response.content else {}
        token = token_payload.get('token') or token_payload.get('access_token')
        if not token:
            return response

        authorized_headers = dict(headers or {})
        authorized_headers['Authorization'] = f'Bearer {token}'
        return requests.request(method, url, headers=authorized_headers, timeout=timeout)

    def _get_remote_manifest_digest(self, image: str, timeout: int = 15) -> Tuple[Optional[str], Optional[str]]:
        """Fetch the current registry manifest digest for an image reference."""
        image_ref = self._parse_image_reference(image)
        if not image_ref:
            return None, "Invalid image reference"

        registry_host = 'registry-1.docker.io' if image_ref['registry'] == 'docker.io' else image_ref['registry']
        manifest_url = f"https://{registry_host}/v2/{image_ref['repository']}/manifests/{image_ref['reference']}"
        headers = {
            'Accept': ', '.join([
                'application/vnd.oci.image.index.v1+json',
                'application/vnd.docker.distribution.manifest.list.v2+json',
                'application/vnd.oci.image.manifest.v1+json',
                'application/vnd.docker.distribution.manifest.v2+json'
            ]),
            'User-Agent': 'AlvaOS-AppStore/1.0'
        }

        try:
            response = self._registry_request('HEAD', manifest_url, headers=headers, timeout=timeout, repository=image_ref['repository'])
            if response.status_code in (404, 405):
                response = self._registry_request('GET', manifest_url, headers=headers, timeout=timeout, repository=image_ref['repository'])
            response.raise_for_status()

            digest = str(response.headers.get('Docker-Content-Digest') or '').strip()
            if digest:
                return digest, None

            body = response.content or b''
            if body:
                return f"sha256:{hashlib.sha256(body).hexdigest()}", None
            return None, "Registry response did not include a manifest digest"
        except requests.RequestException as e:
            return None, f"Registry request failed for {image}: {e}"
        except Exception as e:
            return None, f"Failed to inspect registry manifest for {image}: {e}"

    def _probe_registry_update_status(self, app_id: str, app_details: Dict) -> Dict:
        """Check whether any compose image has a newer remote manifest digest."""
        images = self._extract_compose_images(app_details)
        if not images:
            return {
                'update_available': None,
                'update_status': 'unknown',
                'update_source': 'registry',
                'update_checked_at': datetime.utcnow().isoformat() + 'Z',
                'checked_at_ts': time.time(),
                'images': []
            }

        image_results: List[Dict] = []
        any_update_available = False
        any_known = False

        for image in images:
            local_digests, local_error = self.docker_manager.get_image_repo_digests(image)
            if local_error:
                image_results.append({
                    'image': image,
                    'status': 'unknown',
                    'error': local_error
                })
                continue

            remote_digest, remote_error = self._get_remote_manifest_digest(image)
            if remote_error:
                image_results.append({
                    'image': image,
                    'status': 'unknown',
                    'error': remote_error
                })
                continue

            normalized_local = {
                digest.split('@', 1)[1]
                for digest in (local_digests or [])
                if '@' in str(digest)
            }
            if not normalized_local:
                image_results.append({
                    'image': image,
                    'status': 'unknown',
                    'error': 'Local image digest unavailable'
                })
                continue

            any_known = True
            image_has_update = remote_digest not in normalized_local
            if image_has_update:
                any_update_available = True

            image_results.append({
                'image': image,
                'status': 'available' if image_has_update else 'up_to_date',
                'remote_digest': remote_digest,
                'local_digests': sorted(normalized_local)
            })

        checked_at_ts = time.time()
        update_status = 'available' if any_update_available else ('up_to_date' if any_known else 'unknown')
        return {
            'app_id': app_id,
            'update_available': True if any_update_available else (False if any_known else None),
            'update_status': update_status,
            'update_source': 'registry',
            'update_checked_at': datetime.utcnow().isoformat() + 'Z',
            'checked_at_ts': checked_at_ts,
            'images': image_results
        }

    def _build_update_metadata(self, app_id: str, app_state: Dict) -> Dict:
        """Derive installed/catalog version metadata for an installed app."""
        source = str(app_state.get('source') or 'catalog').strip() or 'catalog'
        installed_version = str(
            app_state.get('installed_version')
            or app_state.get('version')
            or ''
        ).strip()

        metadata: Dict[str, Any] = {
            'source': source,
            'installed_version': installed_version,
            'catalog_version': '',
            'update_available': None,
            'update_status': 'unknown',
            'update_source': 'catalog'
        }

        if source == 'custom_compose':
            metadata.update({
                'update_available': False,
                'update_status': 'unsupported'
            })
            return metadata

        app_details, error = self.get_app_details(app_id)
        if error or not app_details:
            return metadata

        catalog_version = str(app_details.get('version') or '').strip()
        metadata['catalog_version'] = catalog_version

        comparison = self._compare_versions(installed_version, catalog_version)
        if comparison is not None:
            metadata['update_available'] = comparison < 0
            metadata['update_status'] = 'available' if comparison < 0 else 'up_to_date'

        cached_status = self._read_cached_update_status(app_id, allow_stale=True)
        if cached_status:
            metadata.update({
                'update_available': cached_status.get('update_available'),
                'update_status': cached_status.get('update_status', metadata['update_status']),
                'update_source': cached_status.get('update_source', 'registry'),
                'update_checked_at': cached_status.get('update_checked_at'),
                'update_check_error': cached_status.get('update_check_error')
            })
        return metadata
    
    def _create_subvolume(self, path: str) -> Tuple[bool, Optional[str]]:
        """Create a Btrfs subvolume"""
        _, err = run_sudo_command(['/usr/bin/btrfs', 'subvolume', 'create', path], timeout=30)
        return (err is None), err

    def _delete_subvolume(self, path: str) -> Tuple[bool, Optional[str]]:
        """Delete a Btrfs subvolume"""
        _, err = run_sudo_command(['/usr/bin/btrfs', 'subvolume', 'delete', path], timeout=30)
        return (err is None), err

    def _prepare_app_storage(
        self,
        pool_path: str,
        parent_subvolume: Optional[str],
        app_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Prepare storage for an app with nested subvolumes"""
        try:
            if parent_subvolume:
                base_path = os.path.join(pool_path, parent_subvolume)
            else:
                base_path = pool_path
            
            # Ensure base path exists
            if not os.path.exists(base_path):
                return None, f"Base path does not exist: {base_path}"
            
            # Create apps/ subvolume if it doesn't exist
            apps_path = os.path.join(base_path, 'apps')
            if not os.path.exists(apps_path):
                success, error = self._create_subvolume(apps_path)
                if not success:
                    return None, f"Failed to create apps subvolume: {error}"
            
            # Create app-specific subvolume
            app_path = os.path.join(apps_path, app_id)
            if os.path.exists(app_path):
                return None, f"App storage already exists: {app_path}"
            
            success, error = self._create_subvolume(app_path)
            if not success:
                return None, f"Failed to create app subvolume: {error}"
            
            return app_path, None
            
        except Exception as e:
            return None, str(e)
    
    def install_app(
        self,
        app_id: str,
        pool_path: str,
        parent_subvolume: Optional[str] = None,
        port_mappings: Optional[Dict[int, int]] = None,
        volume_mappings: Optional[Dict[str, str]] = None,
        environment_vars: Optional[Dict[str, str]] = None
    ) -> Tuple[bool, Optional[str]]:
        """Start app installation in the background"""
        app_details, error = self.get_app_details(app_id)
        if error or app_details is None:
            return False, error or f"App '{app_id}' not found in catalog"

        # Refuse to deploy an app that would come up with placeholder secrets.
        secret_error = self._validate_required_environment(app_details, environment_vars)
        if secret_error:
            return False, secret_error

        apps_state = self._load_apps_state()
        if app_id in apps_state:
            return False, f"App '{app_id}' is already installed"

        # Check if any app operation is already in progress
        op_active, active_app_id = self._is_operation_in_progress()
        if op_active:
            if active_app_id:
                return False, f"Operation for '{active_app_id}' is already in progress"
            return False, "Another app operation is already in progress"

        import threading
        thread = threading.Thread(
            target=self._install_app_worker,
            args=(app_id, pool_path, parent_subvolume, port_mappings, volume_mappings, environment_vars)
        )
        thread.daemon = True
        
        # Reset memory status for new install
        with self._status_lock:
            self._active_install_status = {
                "app_id": app_id,
                "action": "install",
                "status": "starting",
                "progress": 0,
                "message": "Initializing...",
                "logs": [],
                "updated_at": datetime.now().isoformat()
            }
        self._update_install_status(app_id, "starting", 0, "Initializing...", [], force_write=True, action="install")
        
        thread.start()
        
        return True, None

    def install_compose_app(
        self,
        app_name: str,
        pool_path: str,
        compose_config: Dict,
        parent_subvolume: Optional[str] = None,
        app_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Start installation for a custom app defined via Docker Compose."""
        display_name = str(app_name or '').strip()
        if not display_name:
            return False, "app_name is required", None

        resolved_app_id = self._normalize_custom_app_id(app_id or display_name)
        if not resolved_app_id:
            return False, "app_name does not contain valid characters for an app id", None

        if not isinstance(compose_config, dict):
            return False, "compose_config must be an object", None
        services = compose_config.get('services')
        if not isinstance(services, dict) or len(services) == 0:
            return False, "compose configuration must define at least one service", None

        apps_state = self._load_apps_state()
        if resolved_app_id in apps_state:
            return False, f"App '{resolved_app_id}' is already installed", None

        op_active, active_app_id = self._is_operation_in_progress()
        if op_active:
            if active_app_id:
                return False, f"Operation for '{active_app_id}' is already in progress", None
            return False, "Another app operation is already in progress", None

        import threading
        thread = threading.Thread(
            target=self._install_compose_app_worker,
            args=(
                resolved_app_id,
                display_name,
                pool_path,
                parent_subvolume,
                copy.deepcopy(compose_config)
            )
        )
        thread.daemon = True

        with self._status_lock:
            self._active_install_status = {
                "app_id": resolved_app_id,
                "action": "install",
                "status": "starting",
                "progress": 0,
                "message": "Initializing...",
                "logs": [],
                "updated_at": datetime.now().isoformat()
            }
        self._update_install_status(
            resolved_app_id,
            "starting",
            0,
            "Initializing...",
            [],
            force_write=True,
            action="install"
        )

        thread.start()
        return True, None, resolved_app_id

    def _install_app_worker(
        self,
        app_id: str,
        pool_path: str,
        parent_subvolume: Optional[str],
        port_mappings: Optional[Dict[int, int]],
        volume_mappings: Optional[Dict[str, str]],
        environment_vars: Optional[Dict[str, str]]
    ):
        """Worker thread for app installation"""
        try:
            self._update_install_status(app_id, "installing", 5, f"Starting installation of {app_id}...", [], action="install")
            
            app_details, details_error = self.get_app_details(app_id)
            if details_error or app_details is None:
                self._update_install_status(app_id, "error", 0, f"Failed to read catalog entry: {details_error or 'not found'}", action="install")
                return
            
            # Prepare storage
            self._update_install_status(app_id, "installing", 10, "Preparing storage...", action="install")
            app_storage_path, error = self._prepare_app_storage(pool_path, parent_subvolume, app_id)
            if error or not app_storage_path:
                self._update_install_status(app_id, "error", 0, f"Storage preparation failed: {error or 'no storage path'}", action="install")
                return
            
            compose_config = self._build_compose_config(
                app_details=app_details,
                port_mappings=port_mappings,
                volume_mappings=volume_mappings,
                environment_vars=environment_vars
            )

            # Callback for docker output
            logs = []
            def docker_callback(line):
                # Smarter progress messaging based on docker output
                msg = "Running Docker Compose..."
                progress = 50
                
                lower_line = line.lower()
                if "pulling" in lower_line or "downloading" in lower_line:
                    msg = f"Pulling images: {line}"
                    progress = 40
                elif "extracting" in lower_line:
                    msg = f"Extracting layers: {line}"
                    progress = 45
                elif "creating" in lower_line or "started" in lower_line:
                    msg = f"Creating containers: {line}"
                    progress = 70
                
                logs.append(line)
                self._update_install_status(app_id, "installing", progress, msg, logs, action="install")

            self._update_install_status(app_id, "installing", 30, "Invoking Docker Compose...", logs, action="install")
            success, error = self.docker_manager.create_container_from_compose(
                compose_config,
                app_id,
                app_storage_path,
                project_name=f"alvaos-{app_id}",
                callback=docker_callback
            )
            
            if not success:
                # Cleanup: remove subvolume
                self._delete_subvolume(app_storage_path)
                self._update_install_status(app_id, "error", 0, f"Failed to create containers: {error}", logs, action="install")
                return
            
            self._update_install_status(app_id, "installing", 95, "Finalizing installation...", action="install")
            apps_state = self._load_apps_state()
            apps_state[app_id] = {
                'app_id': app_id,
                'name': app_details.get('name', app_id),
                'source': 'catalog',
                'installed_version': str(app_details.get('version') or '').strip(),
                'storage_path': app_storage_path,
                'pool_path': pool_path,
                'parent_subvolume': parent_subvolume,
                'port_mappings': port_mappings or {},
                'volume_mappings': volume_mappings or {},
                'environment_vars': environment_vars or {},
                'installed_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            self._save_apps_state(apps_state)
            
            self._update_install_status(app_id, "success", 100, "Installation completed successfully", force_write=True, action="install")
            
        except Exception as e:
            self._update_install_status(app_id, "error", 0, f"Unexpected error during installation: {str(e)}", force_write=True, action="install")

    def _install_compose_app_worker(
        self,
        app_id: str,
        app_name: str,
        pool_path: str,
        parent_subvolume: Optional[str],
        compose_config: Dict
    ) -> None:
        """Worker thread for custom compose app installation."""
        logs: List[str] = []
        app_storage_path: Optional[str] = None
        try:
            self._update_install_status(
                app_id,
                "installing",
                5,
                f"Starting installation of {app_name}...",
                logs,
                action="install"
            )

            self._update_install_status(app_id, "installing", 10, "Preparing storage...", logs, action="install")
            app_storage_path, error = self._prepare_app_storage(pool_path, parent_subvolume, app_id)
            if error or not app_storage_path:
                self._update_install_status(app_id, "error", 0, f"Storage preparation failed: {error or 'no storage path'}", logs, force_write=True, action="install")
                return

            def docker_callback(line: str):
                line = str(line or "")
                lower_line = line.lower()
                message = "Running Docker Compose..."
                progress = 50

                if "pulling" in lower_line or "downloading" in lower_line:
                    message = f"Pulling images: {line}"
                    progress = 40
                elif "extracting" in lower_line:
                    message = f"Extracting layers: {line}"
                    progress = 45
                elif "creating" in lower_line or "started" in lower_line or "running" in lower_line:
                    message = f"Creating containers: {line}"
                    progress = 70

                logs.append(line)
                self._update_install_status(app_id, "installing", progress, message, logs, action="install")

            self._update_install_status(app_id, "installing", 30, "Invoking Docker Compose...", logs, action="install")
            success, compose_error = self.docker_manager.create_container_from_compose(
                compose_dict=compose_config,
                app_name=app_id,
                pool_path=app_storage_path,
                project_name=f"alvaos-{app_id}",
                callback=docker_callback
            )
            if not success:
                if app_storage_path:
                    self._delete_subvolume(app_storage_path)
                self._update_install_status(
                    app_id,
                    "error",
                    0,
                    f"Failed to create containers: {compose_error}",
                    logs,
                    force_write=True,
                    action="install"
                )
                return

            self._update_install_status(app_id, "installing", 95, "Finalizing installation...", logs, action="install")
            apps_state = self._load_apps_state()
            apps_state[app_id] = {
                'app_id': app_id,
                'name': app_name,
                'source': 'custom_compose',
                'storage_path': app_storage_path,
                'pool_path': pool_path,
                'parent_subvolume': parent_subvolume,
                'port_mappings': {},
                'volume_mappings': {},
                'environment_vars': {},
                'installed_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            self._save_apps_state(apps_state)
            self._update_install_status(app_id, "success", 100, "Installation completed successfully", logs, force_write=True, action="install")
        except Exception as e:
            if app_storage_path:
                self._delete_subvolume(app_storage_path)
            self._update_install_status(
                app_id,
                "error",
                0,
                f"Unexpected error during installation: {str(e)}",
                logs,
                force_write=True,
                action="install"
            )

    def update_app(self, app_id: str) -> Tuple[bool, Optional[str]]:
        """Update an installed app by pulling latest images and recreating containers."""
        apps_state = self._load_apps_state()
        if app_id not in apps_state:
            return False, f"App '{app_id}' is not installed"

        app_state = apps_state.get(app_id) or {}
        update_metadata, update_error = self.get_app_update_status(app_id, force_refresh=True)
        if update_error:
            return False, update_error
        update_metadata = update_metadata or self._build_update_metadata(app_id, app_state)
        if update_metadata.get('source') == 'custom_compose':
            return False, "Custom compose apps are not updateable via catalog"
        if update_metadata.get('update_available') is False:
            return False, f"App '{app_id}' is already up to date"

        app_details, error = self.get_app_details(app_id)
        if error or app_details is None:
            return False, error or f"App '{app_id}' not found in catalog"

        op_active, active_app_id = self._is_operation_in_progress()
        if op_active:
            if active_app_id:
                return False, f"Operation for '{active_app_id}' is already in progress"
            return False, "Another app operation is already in progress"

        storage_path = str(app_state.get('storage_path') or '').strip()
        if not storage_path:
            return False, f"Missing storage path for installed app '{app_id}'"
        if not os.path.exists(storage_path):
            return False, f"App storage path not found: {storage_path}"

        port_mappings, volume_mappings, environment_vars = self._stored_settings(app_id, app_state, app_details)

        import threading
        thread = threading.Thread(
            target=self._update_app_worker,
            args=(app_id, storage_path, port_mappings, volume_mappings, environment_vars)
        )
        thread.daemon = True

        with self._status_lock:
            self._active_install_status = {
                "app_id": app_id,
                "action": "update",
                "status": "starting",
                "progress": 0,
                "message": "Initializing update...",
                "logs": [],
                "updated_at": datetime.now().isoformat()
            }
        self._update_install_status(
            app_id=app_id,
            status="starting",
            progress=0,
            message="Initializing update...",
            logs=[],
            force_write=True,
            action="update"
        )

        thread.start()
        return True, None

    def _stored_settings(self, app_id: str, app_state: Dict, app_details: Dict
                         ) -> Tuple[Dict[int, int], Dict[str, str], Dict[str, str]]:
        """Ports, folders and environment an installed app runs with."""
        stored_port_mappings = app_state.get('port_mappings') if isinstance(app_state.get('port_mappings'), dict) else {}
        stored_volume_mappings = app_state.get('volume_mappings') if isinstance(app_state.get('volume_mappings'), dict) else {}
        stored_environment_vars = app_state.get('environment_vars') if isinstance(app_state.get('environment_vars'), dict) else {}
        inferred_environment_vars = self._infer_environment_vars_from_running_containers(app_id, app_details)

        port_mappings: Dict[int, int] = {}
        for key, value in (stored_port_mappings or {}).items():
            try:
                port_mappings[int(key)] = int(value)
            except Exception:
                continue

        volume_mappings: Dict[str, str] = {
            str(k): str(v) for k, v in (stored_volume_mappings or {}).items()
            if str(k).strip() and str(v).strip()
        }
        environment_vars: Dict[str, str] = {
            str(k): str(v) for k, v in (inferred_environment_vars or {}).items()
            if str(k).strip()
        }
        environment_vars.update({
            str(k): str(v) for k, v in (stored_environment_vars or {}).items()
            if str(k).strip()
        })
        return port_mappings, volume_mappings, environment_vars

    def reconfigure_app(self, app_id: str, port_mappings: Optional[Dict[int, int]],
                        volume_mappings: Optional[Dict[str, str]]) -> Tuple[bool, Optional[str]]:
        """Change the ports and folders of an installed app. The containers are
        recreated with the same images; the app's own data stays where it is."""
        apps_state = self._load_apps_state()
        if app_id not in apps_state:
            return False, f"App '{app_id}' is not installed"
        app_state = apps_state.get(app_id) or {}
        if str(app_state.get('source') or 'catalog') == 'custom_compose':
            return False, "Custom apps are changed in their compose file"
        app_details, error = self.get_app_details(app_id)
        if error or app_details is None:
            return False, error or f"App '{app_id}' not found in catalog"
        # The catalog describes its newest version. Recreating with it would be
        # an update in disguise, so that happens through Update first.
        installed = str(app_state.get('installed_version') or '').strip()
        latest = str(app_details.get('version') or '').strip()
        if installed and latest and installed != latest:
            return False, "Update the app first, then change its folders and ports."
        op_active, active_app_id = self._is_operation_in_progress()
        if op_active:
            if active_app_id:
                return False, f"Operation for '{active_app_id}' is already in progress"
            return False, "Another app operation is already in progress"
        storage_path = str(app_state.get('storage_path') or '').strip()
        if not storage_path or not os.path.exists(storage_path):
            return False, f"App storage path not found: {storage_path or '-'}"

        _ports, _folders, environment_vars = self._stored_settings(app_id, app_state, app_details)
        new_ports = dict(port_mappings or {})
        new_folders = {str(k): str(v) for k, v in (volume_mappings or {}).items() if str(k).strip() and str(v).strip()}

        import threading
        thread = threading.Thread(
            target=self._update_app_worker,
            args=(app_id, storage_path, new_ports, new_folders, environment_vars),
            kwargs={'action': 'reconfigure', 'pull': False},
        )
        thread.daemon = True
        with self._status_lock:
            self._active_install_status = {
                "app_id": app_id,
                "action": "reconfigure",
                "status": "starting",
                "progress": 0,
                "message": "Applying the new settings...",
                "logs": [],
                "updated_at": datetime.now().isoformat()
            }
        self._update_install_status(app_id, "starting", 0, "Applying the new settings...", [],
                                    force_write=True, action="reconfigure")
        thread.start()
        return True, None

    def set_gpu(self, app_id: str, on: bool) -> Tuple[bool, Optional[str]]:
        """Give an installed app the graphics card, or take it away. The
        containers are recreated with the same images and settings."""
        apps_state = self._load_apps_state()
        if app_id not in apps_state:
            return False, f"App '{app_id}' is not installed"
        app_state = apps_state.get(app_id) or {}
        if str(app_state.get('source') or 'catalog') == 'custom_compose':
            return False, "Custom apps get a graphics card in their compose file"
        app_details, error = self.get_app_details(app_id)
        if error or app_details is None:
            return False, error or f"App '{app_id}' not found in catalog"
        if not isinstance(app_details.get('gpu'), dict):
            return False, "This app cannot use a graphics card."
        if on:
            import gpu_manager
            plan, why_not = gpu_manager.app_plan(app_details['gpu'])
            if plan is None:
                return False, why_not
        installed = str(app_state.get('installed_version') or '').strip()
        latest = str(app_details.get('version') or '').strip()
        if installed and latest and installed != latest:
            return False, "Update the app first, then give it the graphics card."
        op_active, active_app_id = self._is_operation_in_progress()
        if op_active:
            return False, (f"Operation for '{active_app_id}' is already in progress" if active_app_id
                           else "Another app operation is already in progress")
        storage_path = str(app_state.get('storage_path') or '').strip()
        if not storage_path or not os.path.exists(storage_path):
            return False, f"App storage path not found: {storage_path or '-'}"
        ports, folders, environment_vars = self._stored_settings(app_id, app_state, app_details)

        import threading
        thread = threading.Thread(
            target=self._update_app_worker,
            args=(app_id, storage_path, ports, folders, environment_vars),
            kwargs={'action': 'gpu', 'pull': False, 'gpu': bool(on)},   # compose fetches a missing image itself
        )
        thread.daemon = True
        message = 'Giving the app the graphics card...' if on else 'Taking the graphics card away...'
        with self._status_lock:
            self._active_install_status = {
                "app_id": app_id, "action": "gpu", "status": "starting", "progress": 0,
                "message": message, "logs": [], "updated_at": datetime.now().isoformat()
            }
        self._update_install_status(app_id, "starting", 0, message, [], force_write=True, action="gpu")
        thread.start()
        return True, None

    def gpu_overview(self, app_id: str, app_state: Dict) -> Dict:
        """For the app page: can this app use a card, does it, and if not why."""
        if str(app_state.get('source') or 'catalog') == 'custom_compose':
            return {'possible': False}
        app_details, _ = self.get_app_details(app_id)
        spec = (app_details or {}).get('gpu')
        if not isinstance(spec, dict):
            return {'possible': False}
        import gpu_manager
        plan, why_not = gpu_manager.app_plan(spec)
        return {'possible': True, 'on': bool(app_state.get('gpu')), 'card': (plan or {}).get('card', ''),
                'why_not': why_not, 'after': str(spec.get('after') or '')}

    def get_app_update_status(self, app_id: str, force_refresh: bool = False) -> Tuple[Optional[Dict], Optional[str]]:
        """Return update availability for an installed app, using cached registry probes when possible."""
        apps_state = self._load_apps_state()
        if app_id not in apps_state:
            return None, f"App '{app_id}' is not installed"

        app_state = apps_state.get(app_id) or {}
        metadata = self._build_update_metadata(app_id, app_state)
        if metadata.get('source') == 'custom_compose':
            return metadata, None

        cached_status = None if force_refresh else self._read_cached_update_status(app_id, allow_stale=False)
        if cached_status:
            metadata.update({
                'update_available': cached_status.get('update_available'),
                'update_status': cached_status.get('update_status', metadata.get('update_status', 'unknown')),
                'update_source': cached_status.get('update_source', 'registry'),
                'update_checked_at': cached_status.get('update_checked_at'),
                'update_check_error': cached_status.get('update_check_error')
            })
            return metadata, None

        app_details, error = self.get_app_details(app_id)
        if error or app_details is None:
            return metadata, error or f"App '{app_id}' not found in catalog"

        probe = self._probe_registry_update_status(app_id, app_details)
        cache_payload = dict(probe)
        cache_payload['update_check_error'] = None

        unknown_errors = [
            item.get('error')
            for item in probe.get('images', [])
            if item.get('status') == 'unknown' and item.get('error')
        ]
        if probe.get('update_status') == 'unknown' and unknown_errors:
            cache_payload['update_check_error'] = unknown_errors[0]

        self._write_cached_update_status(app_id, cache_payload)
        metadata.update({
            'update_available': cache_payload.get('update_available'),
            'update_status': cache_payload.get('update_status', metadata.get('update_status', 'unknown')),
            'update_source': cache_payload.get('update_source', 'registry'),
            'update_checked_at': cache_payload.get('update_checked_at'),
            'update_check_error': cache_payload.get('update_check_error')
        })
        return metadata, None

    def _update_app_worker(
        self,
        app_id: str,
        storage_path: str,
        port_mappings: Optional[Dict[int, int]],
        volume_mappings: Optional[Dict[str, str]],
        environment_vars: Optional[Dict[str, str]],
        action: str = "update",
        pull: bool = True,
        gpu: Optional[bool] = None
    ) -> None:
        """Worker thread for app updates and settings changes (no pull).
        `gpu` turns the graphics card on or off; None keeps how it was."""
        logs: List[str] = []
        try:
            self._update_install_status(app_id, "updating", 5, f"Starting update of {app_id}..." if pull else "Applying the new settings...", logs, action=action)
            app_details, error = self.get_app_details(app_id)
            if error or app_details is None:
                self._update_install_status(app_id, "error", 0, f"Failed to read catalog entry: {error or 'not found'}", logs, force_write=True, action=action)
                return

            if gpu is None:
                gpu = bool((self._load_apps_state().get(app_id) or {}).get('gpu'))
            plan = None
            if gpu:
                import gpu_manager
                plan, why_not = gpu_manager.app_plan(app_details.get('gpu'))
                if plan is None:
                    if action == 'gpu':
                        self._update_install_status(app_id, "error", 0, why_not, logs, force_write=True, action=action)
                        return
                    # The card is gone or not ready: the app still runs, without it.
                    logs.append(f'Without the graphics card: {why_not}')

            compose_config = self._build_compose_config(
                app_details=app_details,
                port_mappings=port_mappings,
                volume_mappings=volume_mappings,
                environment_vars=environment_vars,
                gpu_plan=plan
            )

            def docker_callback(line: str):
                line = str(line or "")
                lower = line.lower()
                progress = 55
                message = f"Updating containers: {line}"
                if "pulling" in lower or "downloading" in lower:
                    progress = 35
                    message = f"Pulling images: {line}"
                elif "extracting" in lower:
                    progress = 45
                    message = f"Extracting layers: {line}"
                elif "recreating" in lower or "creating" in lower:
                    progress = 70
                    message = f"Recreating containers: {line}"
                elif "started" in lower or "running" in lower:
                    progress = 85
                    message = f"Starting services: {line}"
                logs.append(line)
                self._update_install_status(app_id, "updating", progress, message, logs, action=action)

            self._update_install_status(app_id, "updating", 20, "Invoking Docker Compose update...", logs, action=action)
            success, update_error = self.docker_manager.update_container_from_compose(
                compose_dict=compose_config,
                app_name=app_id,
                pool_path=storage_path,
                project_name=f"alvaos-{app_id}",
                callback=docker_callback,
                pull=pull
            )
            if not success:
                self._update_install_status(
                    app_id,
                    "error",
                    0,
                    f"Failed to update containers: {update_error}",
                    logs,
                    force_write=True,
                    action=action
                )
                return

            apps_state = self._load_apps_state()
            app_state = apps_state.get(app_id) or {}
            app_state['app_id'] = app_id
            app_state['name'] = app_details.get('name', app_id)
            app_state['source'] = str(app_state.get('source') or 'catalog').strip() or 'catalog'
            if pull or not app_state.get('installed_version'):
                app_state['installed_version'] = str(app_details.get('version') or '').strip()
            app_state['storage_path'] = storage_path
            app_state['port_mappings'] = port_mappings or {}
            app_state['volume_mappings'] = volume_mappings or {}
            app_state['environment_vars'] = environment_vars or {}
            app_state['gpu'] = bool(gpu)
            app_state['updated_at'] = datetime.now().isoformat()
            if 'installed_at' not in app_state:
                app_state['installed_at'] = datetime.now().isoformat()
            apps_state[app_id] = app_state
            self._save_apps_state(apps_state)

            self._update_install_status(app_id, "success", 100, "Update completed successfully" if pull else "Settings applied", logs, force_write=True, action=action)
        except Exception as e:
            self._update_install_status(app_id, "error", 0, f"Unexpected error during update: {str(e)}", logs, force_write=True, action=action)

    def uninstall_app(self, app_id: str, keep_data: bool = False) -> Tuple[bool, Optional[str]]:
        """Uninstall an app"""
        apps_state = self._load_apps_state()
        
        if app_id not in apps_state:
            return False, f"App '{app_id}' is not installed"
        
        app_state = apps_state[app_id]
        storage_path = app_state.get('storage_path')
        
        containers, error = self.docker_manager.list_containers(all_containers=True)
        if error or containers is None:
            return False, f"Failed to list containers: {error or 'no response from Docker'}"
        
        project_name = f"alvaos-{app_id}"
        for container in containers:
            labels = container.get('Labels', '')
            if f'com.docker.compose.project={project_name}' in labels:
                container_id = container.get('ID') or container.get('Names', '').split(',')[0]
                success, error = self.docker_manager.remove_container(container_id, force=True)
                if not success:
                    print(f"Warning: Failed to remove container {container_id}: {error}")
        
        if not keep_data and storage_path:
            success, error = self._delete_subvolume(storage_path)
            if not success:
                print(f"Warning: Failed to delete subvolume {storage_path}: {error}")
        
        del apps_state[app_id]
        self._save_apps_state(apps_state)
        
        return True, None
    
    def get_installed_apps(self) -> List[Dict]:
        """Get list of installed apps"""
        installed_apps: List[Dict] = []
        for app_id, app_state in self._load_apps_state().items():
            enriched_state = dict(app_state or {})
            enriched_state['app_id'] = app_id
            enriched_state.update(self._build_update_metadata(app_id, enriched_state))
            try:
                enriched_state['gpu'] = self.gpu_overview(app_id, enriched_state)
            except Exception:  # noqa: BLE001 - the list must not fail over a card
                enriched_state['gpu'] = {'possible': False}
            installed_apps.append(enriched_state)
        return installed_apps
