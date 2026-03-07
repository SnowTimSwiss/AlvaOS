#!/usr/bin/env python3
"""
AlvaOS App Store Manager
Manages app catalog and installation
"""

import json
import copy
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from docker_manager import DockerManager


class AppStore:
    """Manages the AlvaOS app catalog and installation"""
    
    def __init__(self):
        self.docker_manager = DockerManager()
        
        # Paths
        self.catalog_file = self._get_catalog_path()
        self.apps_state_file = '/var/lib/alvaos/apps_state.json'
        self.install_status_file = '/var/lib/alvaos/app_install_status.json'
        
        # Status tracking
        import threading
        self._status_lock = threading.RLock()
        self._active_install_status = None
        self._last_status_write = 0
        
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
        environment_vars: Optional[Dict[str, str]] = None
    ) -> Dict:
        """Build a mutable compose config with user mappings applied."""
        compose_config = copy.deepcopy(app_details.get('docker_compose', {}) or {})

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

        return compose_config

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

    def _update_install_status(self, app_id: str, status: str, progress: int = 0, message: str = "", logs: List[str] = None, force_write: bool = False, action: str = "install"):
        """Update app operation status with in-memory tracking and throttled disk writes"""
        import time
        
        with self._status_lock:
            if logs is None:
                if self._active_install_status:
                    logs = self._active_install_status.get('logs', [])
                else:
                    # Try to get from last known status if we just started.
                    # Read from disk directly here to avoid nested lock acquisition.
                    current = {}
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
        """
        Get list of available apps from catalog
        
        Returns:
            Tuple of (list of apps, error message)
        """
        catalog, error = self._load_catalog()
        if error:
            return None, error
        
        apps = []
        for app_id, app_data in catalog.items():
            apps.append({
                'id': app_id,
                'name': app_data.get('name', app_id),
                'description': app_data.get('description', ''),
                'icon': app_data.get('icon', ''),
                'category': app_data.get('category', 'Other'),
                'version': app_data.get('version', 'latest')
            })
        
        return apps, None
    
    def get_app_details(self, app_id: str) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Get detailed information about an app
        
        Args:
            app_id: App identifier
        
        Returns:
            Tuple of (app details dict, error message)
        """
        catalog, error = self._load_catalog()
        if error:
            return None, error
        
        if app_id not in catalog:
            return None, f"App '{app_id}' not found in catalog"
        
        return catalog[app_id], None

    def _tokenize_version(self, value: str) -> List[object]:
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
        """
        Compare two catalog version strings.

        Returns:
            -1 if left < right, 0 if equal, 1 if left > right, None if unknown
        """
        left_value = str(left or '').strip()
        right_value = str(right or '').strip()
        if not left_value or not right_value:
            return None

        left_lower = left_value.lower()
        right_lower = right_value.lower()
        special_values = {'latest', 'stable', 'edge', 'nightly', 'main', 'master'}
        if left_lower in special_values or right_lower in special_values:
            if left_lower == right_lower:
                return 0
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

    def _build_update_metadata(self, app_id: str, app_state: Dict) -> Dict:
        """Derive installed/catalog version metadata for an installed app."""
        source = str(app_state.get('source') or 'catalog').strip() or 'catalog'
        installed_version = str(
            app_state.get('installed_version')
            or app_state.get('version')
            or ''
        ).strip()

        metadata = {
            'source': source,
            'installed_version': installed_version,
            'catalog_version': '',
            'update_available': None,
            'update_status': 'unknown'
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
        if comparison is None:
            return metadata

        metadata['update_available'] = comparison < 0
        metadata['update_status'] = 'available' if comparison < 0 else 'up_to_date'
        return metadata
    
    def _create_subvolume(self, path: str) -> Tuple[bool, Optional[str]]:
        """Create a Btrfs subvolume"""
        try:
            # Use absolute path matching AlvaOS sudoers
            if os.getuid() != 0:
                cmd = ['sudo', '-n', '/usr/bin/btrfs', 'subvolume', 'create', path]
            else:
                cmd = ['/usr/bin/btrfs', 'subvolume', 'create', path]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env={'LC_ALL': 'C'}
            )
            
            if result.returncode != 0:
                return False, self._format_privilege_error(cmd, result)
            
            return True, None
        except Exception as e:
            return False, str(e)
    
    def _delete_subvolume(self, path: str) -> Tuple[bool, Optional[str]]:
        """Delete a Btrfs subvolume"""
        try:
            # Use absolute path matching AlvaOS sudoers
            if os.getuid() != 0:
                cmd = ['sudo', '-n', '/usr/bin/btrfs', 'subvolume', 'delete', path]
            else:
                cmd = ['/usr/bin/btrfs', 'subvolume', 'delete', path]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env={'LC_ALL': 'C'}
            )
            
            if result.returncode != 0:
                return False, self._format_privilege_error(cmd, result)
            
            return True, None
        except Exception as e:
            return False, str(e)

    def _format_privilege_error(self, cmd: List[str], result: subprocess.CompletedProcess) -> str:
        stderr_text = str(result.stderr or "").strip()
        stdout_text = str(result.stdout or "").strip()
        combined = f"{stderr_text}\n{stdout_text}".strip()
        lowered = combined.lower()
        cmd_str = " ".join(str(part) for part in cmd)

        if "/etc/sudoers.d/alvaos" in lowered and (
            "is owned by uid" in lowered
            or "is world writable" in lowered
            or "bad permissions" in lowered
        ):
            return (
                "System permission error: /etc/sudoers.d/alvaos has invalid ownership or mode. "
                "Run as root: chown root:root /etc/sudoers.d/alvaos && chmod 440 /etc/sudoers.d/alvaos"
            )
        if "password is required" in lowered or "a password is required" in lowered:
            return (
                "System permission error: Passwordless sudo is not configured for AlvaOS commands. "
                f"Command: {cmd_str}"
            )

        detail = stderr_text or stdout_text or f"exit code {result.returncode}"
        return f"Command failed ({result.returncode}): {cmd_str}: {detail}"
    
    def _prepare_app_storage(
        self,
        pool_path: str,
        parent_subvolume: Optional[str],
        app_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Prepare storage for an app with nested subvolumes
        
        Args:
            pool_path: Base pool path (e.g., /mnt/alvaos/main-pool)
            parent_subvolume: Optional parent subvolume (e.g., "data")
            app_id: App identifier
        
        Returns:
            Tuple of (app storage path, error message)
        """
        try:
            # Build base path
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
        """
        Start app installation in the background
        
        Returns:
            Tuple of (success bool, error message)
        """
        # Get app details from catalog
        app_details, error = self.get_app_details(app_id)
        if error:
            return False, error
        
        # Check if app is already installed
        apps_state = self._load_apps_state()
        if app_id in apps_state:
            return False, f"App '{app_id}' is already installed"

        # Check if any app operation is already in progress
        op_active, active_app_id = self._is_operation_in_progress()
        if op_active:
            if active_app_id:
                return False, f"Operation for '{active_app_id}' is already in progress"
            return False, "Another app operation is already in progress"

        # Start installation in a thread
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
        """
        Start installation for a custom app defined via Docker Compose.

        Returns:
            Tuple of (success bool, error message, resolved app_id)
        """
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
            
            app_details, _ = self.get_app_details(app_id)
            
            # Prepare storage
            self._update_install_status(app_id, "installing", 10, "Preparing storage...", action="install")
            app_storage_path, error = self._prepare_app_storage(pool_path, parent_subvolume, app_id)
            if error:
                self._update_install_status(app_id, "error", 0, f"Storage preparation failed: {error}", action="install")
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

            # Create containers from compose
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
            
            # Save app state
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
            if error:
                self._update_install_status(app_id, "error", 0, f"Storage preparation failed: {error}", logs, force_write=True, action="install")
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
        """
        Update an installed app by pulling latest images and recreating containers.

        Args:
            app_id: App identifier

        Returns:
            Tuple of (success bool, error message)
        """
        apps_state = self._load_apps_state()
        if app_id not in apps_state:
            return False, f"App '{app_id}' is not installed"

        app_state = apps_state.get(app_id) or {}
        update_metadata = self._build_update_metadata(app_id, app_state)
        if update_metadata.get('source') == 'custom_compose':
            return False, "Custom compose apps are not updateable via catalog"
        if update_metadata.get('update_available') is False:
            return False, f"App '{app_id}' is already up to date"

        app_details, error = self.get_app_details(app_id)
        if error:
            return False, error

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

    def _update_app_worker(
        self,
        app_id: str,
        storage_path: str,
        port_mappings: Optional[Dict[int, int]],
        volume_mappings: Optional[Dict[str, str]],
        environment_vars: Optional[Dict[str, str]]
    ) -> None:
        """Worker thread for app updates."""
        logs: List[str] = []
        try:
            self._update_install_status(app_id, "updating", 5, f"Starting update of {app_id}...", logs, action="update")
            app_details, error = self.get_app_details(app_id)
            if error:
                self._update_install_status(app_id, "error", 0, f"Failed to read catalog entry: {error}", logs, force_write=True, action="update")
                return

            compose_config = self._build_compose_config(
                app_details=app_details,
                port_mappings=port_mappings,
                volume_mappings=volume_mappings,
                environment_vars=environment_vars
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
                self._update_install_status(app_id, "updating", progress, message, logs, action="update")

            self._update_install_status(app_id, "updating", 20, "Invoking Docker Compose update...", logs, action="update")
            success, update_error = self.docker_manager.update_container_from_compose(
                compose_dict=compose_config,
                app_name=app_id,
                pool_path=storage_path,
                project_name=f"alvaos-{app_id}",
                callback=docker_callback
            )
            if not success:
                self._update_install_status(
                    app_id,
                    "error",
                    0,
                    f"Failed to update containers: {update_error}",
                    logs,
                    force_write=True,
                    action="update"
                )
                return

            apps_state = self._load_apps_state()
            app_state = apps_state.get(app_id) or {}
            app_state['app_id'] = app_id
            app_state['name'] = app_details.get('name', app_id)
            app_state['source'] = str(app_state.get('source') or 'catalog').strip() or 'catalog'
            app_state['installed_version'] = str(app_details.get('version') or '').strip()
            app_state['storage_path'] = storage_path
            app_state['port_mappings'] = port_mappings or {}
            app_state['volume_mappings'] = volume_mappings or {}
            app_state['environment_vars'] = environment_vars or {}
            app_state['updated_at'] = datetime.now().isoformat()
            if 'installed_at' not in app_state:
                app_state['installed_at'] = datetime.now().isoformat()
            apps_state[app_id] = app_state
            self._save_apps_state(apps_state)

            self._update_install_status(app_id, "success", 100, "Update completed successfully", logs, force_write=True, action="update")
        except Exception as e:
            self._update_install_status(app_id, "error", 0, f"Unexpected error during update: {str(e)}", logs, force_write=True, action="update")

    def uninstall_app(self, app_id: str, keep_data: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Uninstall an app
        
        Args:
            app_id: App identifier
            keep_data: If True, keep the app's data subvolume
        
        Returns:
            Tuple of (success bool, error message)
        """
        apps_state = self._load_apps_state()
        
        if app_id not in apps_state:
            return False, f"App '{app_id}' is not installed"
        
        app_state = apps_state[app_id]
        storage_path = app_state.get('storage_path')
        
        # Get all containers for this app
        containers, error = self.docker_manager.list_containers(all_containers=True)
        if error:
            return False, f"Failed to list containers: {error}"
        
        # Remove containers that belong to this app
        project_name = f"alvaos-{app_id}"
        for container in containers:
            labels = container.get('Labels', '')
            if f'com.docker.compose.project={project_name}' in labels:
                container_id = container.get('ID') or container.get('Names', '').split(',')[0]
                success, error = self.docker_manager.remove_container(container_id, force=True)
                if not success:
                    print(f"Warning: Failed to remove container {container_id}: {error}")
        
        # Remove data subvolume if requested
        if not keep_data and storage_path:
            success, error = self._delete_subvolume(storage_path)
            if not success:
                print(f"Warning: Failed to delete subvolume {storage_path}: {error}")
        
        # Remove from state
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
            installed_apps.append(enriched_state)
        return installed_apps
