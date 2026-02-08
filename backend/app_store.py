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
            if os.geteuid() != 0:
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
                return False, result.stderr
            
            return True, None
        except Exception as e:
            return False, str(e)
    
    def _delete_subvolume(self, path: str) -> Tuple[bool, Optional[str]]:
        """Delete a Btrfs subvolume"""
        try:
            if os.geteuid() != 0:
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
                return False, result.stderr
            
            return True, None
        except Exception as e:
            return False, str(e)
    
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
        Install an app
        
        Args:
            app_id: App identifier
            pool_path: Base pool path
            parent_subvolume: Optional parent subvolume name
            port_mappings: Dict of internal_port -> external_port
            volume_mappings: Dict of container_path -> host_path
            environment_vars: Dict of env var key -> value
        
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
        
        # Prepare storage
        app_storage_path, error = self._prepare_app_storage(pool_path, parent_subvolume, app_id)
        if error:
            return False, error
        
        # Get docker compose config
        compose_config = app_details.get('docker_compose', {})
        if not compose_config:
            return False, "App has no Docker Compose configuration"
        
        # Apply port mappings
        if port_mappings:
            for service_name, service_config in compose_config.get('services', {}).items():
                if 'ports' in service_config:
                    new_ports = []
                    for port_spec in service_config['ports']:
                        # Parse port spec (e.g., "8080:80")
                        if ':' in str(port_spec):
                            parts = str(port_spec).split(':')
                            internal_port = int(parts[-1])
                            if internal_port in port_mappings:
                                new_ports.append(f"{port_mappings[internal_port]}:{internal_port}")
                            else:
                                new_ports.append(port_spec)
                        else:
                            new_ports.append(port_spec)
                    service_config['ports'] = new_ports
        
        # Apply volume mappings
        if volume_mappings:
            for service_name, service_config in compose_config.get('services', {}).items():
                if 'volumes' in service_config:
                    new_volumes = []
                    for volume_spec in service_config['volumes']:
                        # Parse volume spec (e.g., "/host/path:/container/path")
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
                
                # Convert environment to dict if it's a list
                if isinstance(service_config['environment'], list):
                    env_dict = {}
                    for env_item in service_config['environment']:
                        if '=' in env_item:
                            key, value = env_item.split('=', 1)
                            env_dict[key] = value
                    service_config['environment'] = env_dict
                
                # Update with user-provided values
                service_config['environment'].update(environment_vars)
        
        # Create containers from compose
        success, error = self.docker_manager.create_container_from_compose(
            compose_config,
            app_id,
            app_storage_path,
            project_name=f"alvaos-{app_id}"
        )
        
        if not success:
            # Cleanup: remove subvolume
            self._delete_subvolume(app_storage_path)
            return False, f"Failed to create containers: {error}"
        
        # Save app state
        apps_state[app_id] = {
            'app_id': app_id,
            'name': app_details.get('name', app_id),
            'storage_path': app_storage_path,
            'pool_path': pool_path,
            'parent_subvolume': parent_subvolume,
            'installed_at': __import__('datetime').datetime.now().isoformat()
        }
        self._save_apps_state(apps_state)
        
        return True, None
    
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
