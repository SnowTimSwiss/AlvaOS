#!/usr/bin/env python3
"""
AlvaOS App Store Manager
Manages app catalog and installation
"""

import json
import os
import subprocess
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
        self._status_lock = threading.Lock()
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

    def _update_install_status(self, app_id: str, status: str, progress: int = 0, message: str = "", logs: List[str] = None, force_write: bool = False):
        """Update the installation status with in-memory tracking and throttled disk writes"""
        import time
        from datetime import datetime
        
        with self._status_lock:
            if logs is None:
                if self._active_install_status:
                    logs = self._active_install_status.get('logs', [])
                else:
                    # Try to get from last known status if we just started
                    current = self.get_install_status()
                    logs = current.get('logs', [])
            
            # Keep only last 100 log lines
            if len(logs) > 100:
                logs = logs[-100:]

            payload = {
                "app_id": app_id,
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

        # Check if an installation is already in progress
        current_status = self.get_install_status()
        if current_status.get('status') == 'installing' and current_status.get('app_id') == app_id:
            return False, f"Installation of '{app_id}' is already in progress"

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
                "status": "starting",
                "progress": 0,
                "message": "Initializing...",
                "logs": [],
                "updated_at": __import__('datetime').datetime.now().isoformat()
            }
        self._update_install_status(app_id, "starting", 0, "Initializing...", [], force_write=True)
        
        thread.start()
        
        return True, None

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
            self._update_install_status(app_id, "installing", 5, f"Starting installation of {app_id}...", [])
            
            app_details, _ = self.get_app_details(app_id)
            
            # Prepare storage
            self._update_install_status(app_id, "installing", 10, "Preparing storage...")
            app_storage_path, error = self._prepare_app_storage(pool_path, parent_subvolume, app_id)
            if error:
                self._update_install_status(app_id, "error", 0, f"Storage preparation failed: {error}")
                return
            
            # Get docker compose config
            compose_config = app_details.get('docker_compose', {})
            
            # Apply mappings (Ports, Volumes, Env) - same as before
            # [Re-using the logic from the original install_app]
            
            # Apply port mappings
            if port_mappings:
                for service_name, service_config in compose_config.get('services', {}).items():
                    if 'ports' in service_config:
                        new_ports = []
                        for port_spec in service_config['ports']:
                            port_str = str(port_spec)
                            if ':' in port_str:
                                parts = port_str.split(':')
                                host_part = parts[0]
                                container_part = parts[1]
                                
                                # container_part might be "80" or "53/udp"
                                internal_port_raw = container_part.split('/')[0]
                                internal_port = int(internal_port_raw)
                                
                                if internal_port in port_mappings:
                                    # Preserve protocol if present
                                    protocol = ""
                                    if '/' in container_part:
                                        protocol = "/" + container_part.split('/')[1]
                                    
                                    new_ports.append(f"{port_mappings[internal_port]}:{internal_port}{protocol}")
                                else:
                                    new_ports.append(port_spec)
                            else:
                                # Single port spec like "80" (meaning 80:80)
                                internal_port_raw = port_str.split('/')[0]
                                internal_port = int(internal_port_raw)
                                if internal_port in port_mappings:
                                    protocol = ""
                                    if '/' in port_str:
                                        protocol = "/" + port_str.split('/')[1]
                                    new_ports.append(f"{port_mappings[internal_port]}:{internal_port}{protocol}")
                                else:
                                    new_ports.append(port_spec)
                        service_config['ports'] = new_ports
            
            # Apply volume mappings
            if volume_mappings:
                for service_name, service_config in compose_config.get('services', {}).items():
                    if 'volumes' in service_config:
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
            
            # Apply environment variables
            if environment_vars:
                for service_name, service_config in compose_config.get('services', {}).items():
                    if 'environment' not in service_config:
                        service_config['environment'] = []
                    if isinstance(service_config['environment'], list):
                        env_dict = {}
                        for env_item in service_config['environment']:
                            if '=' in env_item:
                                key, value = env_item.split('=', 1)
                                env_dict[key] = value
                        service_config['environment'] = env_dict
                    service_config['environment'].update(environment_vars)

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
                self._update_install_status(app_id, "installing", progress, msg, logs)

            # Create containers from compose
            self._update_install_status(app_id, "installing", 30, "Invoking Docker Compose...", logs)
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
                self._update_install_status(app_id, "error", 0, f"Failed to create containers: {error}", logs)
                return
            
            # Save app state
            self._update_install_status(app_id, "installing", 95, "Finalizing installation...")
            apps_state = self._load_apps_state()
            apps_state[app_id] = {
                'app_id': app_id,
                'name': app_details.get('name', app_id),
                'storage_path': app_storage_path,
                'pool_path': pool_path,
                'parent_subvolume': parent_subvolume,
                'installed_at': __import__('datetime').datetime.now().isoformat()
            }
            self._save_apps_state(apps_state)
            
            self._update_install_status(app_id, "success", 100, "Installation completed successfully", force_write=True)
            
        except Exception as e:
            self._update_install_status(app_id, "error", 0, f"Unexpected error during installation: {str(e)}", force_write=True)
    
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
        return list(self._load_apps_state().values())
