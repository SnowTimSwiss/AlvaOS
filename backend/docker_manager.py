#!/usr/bin/env python3
"""
AlvaOS Docker Manager
Manages Docker containers for the app store
"""

import subprocess
import json
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

from common import build_privileged_cmd, privilege_error_message

# Compose files are written here; the privilege helper only accepts compose
# files from this directory and checks them before running docker-compose.
COMPOSE_DIR = '/var/lib/alvaos/compose'
COMPOSE_ENV = {'LC_ALL': 'C', 'COMPOSE_INTERACTIVE_NO_CLI': '1'}


def write_compose_file(compose_str: str, project_name: str) -> str:
    """Write a compose file where the privilege helper will accept it."""
    import tempfile

    os.makedirs(COMPOSE_DIR, mode=0o750, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=f'{project_name}-', suffix='.yml', dir=COMPOSE_DIR)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(compose_str)
    return path


class DockerManager:
    """Manages Docker container lifecycle and operations"""
    
    def __init__(self):
        self.CMD = {
            'DOCKER': '/usr/bin/docker',
            'COMPOSE': '/usr/bin/docker-compose',
            'SYSTEMCTL': '/usr/bin/systemctl'
        }
        self.docker_cmd = self.CMD['DOCKER']
        self.compose_cmd = self.CMD['COMPOSE']
    
    def _run_docker_command(self, args: List[str], timeout: int = 30) -> Tuple[Optional[subprocess.CompletedProcess], Optional[str]]:
        """Run a docker command with sudo if needed"""
        try:
            cmd = build_privileged_cmd([self.docker_cmd] + args)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={'LC_ALL': 'C'}
            )

            if result.returncode != 0:
                # Never return an empty error: callers treat "" as success.
                return None, (privilege_error_message(result, cmd) or (result.stderr or '').strip()
                              or f"docker exited with status {result.returncode}")
            
            return result, None
        except subprocess.TimeoutExpired:
            return None, "Command timed out"
        except Exception as e:
            return None, str(e)
    
    def list_containers(self, all_containers: bool = True) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """
        List all Docker containers
        
        Args:
            all_containers: If True, include stopped containers
        
        Returns:
            Tuple of (list of containers, error message)
        """
        args = ['ps', '--format', '{{json .}}']
        if all_containers:
            args.append('-a')
        
        result, error = self._run_docker_command(args)
        if error or result is None:
            return None, error
        
        try:
            # Docker ps --format '{{json .}}' returns one JSON object per line
            containers = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    containers.append(json.loads(line))
            return containers, None
        except json.JSONDecodeError as e:
            return None, f"Failed to parse Docker output: {str(e)}"
    
    def get_container_details(self, container_id: str) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Get detailed information about a container
        
        Args:
            container_id: Container ID or name
        
        Returns:
            Tuple of (container details dict, error message)
        """
        result, error = self._run_docker_command(['inspect', container_id])
        if error or result is None:
            return None, error
        
        try:
            details = json.loads(result.stdout)
            if details and len(details) > 0:
                return details[0], None
            return None, "Container not found"
        except json.JSONDecodeError as e:
            return None, f"Failed to parse container details: {str(e)}"

    def get_image_repo_digests(self, image: str) -> Tuple[Optional[List[str]], Optional[str]]:
        """
        Return the locally known RepoDigests for an image reference.

        Args:
            image: Image reference such as 'nginx:latest'

        Returns:
            Tuple of (list of repo digests, error message)
        """
        result, error = self._run_docker_command([
            'image',
            'inspect',
            image,
            '--format',
            '{{json .RepoDigests}}'
        ])
        if error or result is None:
            return None, error

        try:
            digests = json.loads((result.stdout or '').strip() or '[]')
            if not isinstance(digests, list):
                return [], None
            return [str(item) for item in digests if str(item).strip()], None
        except json.JSONDecodeError as e:
            return None, f"Failed to parse image digests: {str(e)}"
    
    def start_container(self, container_id: str) -> Tuple[bool, Optional[str]]:
        """
        Start a container
        
        Args:
            container_id: Container ID or name
        
        Returns:
            Tuple of (success bool, error message)
        """
        result, error = self._run_docker_command(['start', container_id])
        if error or result is None:
            return False, error
        return True, None
    
    def stop_container(self, container_id: str, timeout: int = 10) -> Tuple[bool, Optional[str]]:
        """
        Stop a container
        
        Args:
            container_id: Container ID or name
            timeout: Seconds to wait before killing
        
        Returns:
            Tuple of (success bool, error message)
        """
        result, error = self._run_docker_command(['stop', '-t', str(timeout), container_id])
        if error or result is None:
            return False, error
        return True, None
    
    def restart_container(self, container_id: str) -> Tuple[bool, Optional[str]]:
        """
        Restart a container
        
        Args:
            container_id: Container ID or name
        
        Returns:
            Tuple of (success bool, error message)
        """
        result, error = self._run_docker_command(['restart', container_id])
        if error or result is None:
            return False, error
        return True, None
    
    def remove_container(self, container_id: str, force: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Remove a container
        
        Args:
            container_id: Container ID or name
            force: Force removal even if running
        
        Returns:
            Tuple of (success bool, error message)
        """
        args = ['rm']
        if force:
            args.append('-f')
        args.append(container_id)
        
        result, error = self._run_docker_command(args)
        if error or result is None:
            return False, error
        return True, None
    
    def get_container_logs(self, container_id: str, lines: int = 100) -> Tuple[Optional[str], Optional[str]]:
        """
        Get container logs
        
        Args:
            container_id: Container ID or name
            lines: Number of lines to retrieve
        
        Returns:
            Tuple of (logs string, error message)
        """
        result, error = self._run_docker_command(['logs', '--tail', str(lines), container_id])
        if error or result is None:
            return None, error
        
        # Combine stdout and stderr
        logs = result.stdout
        if result.stderr:
            logs += "\n" + result.stderr
        
        return logs, None

    def exec_in_container(
        self,
        container_id: str,
        command: str,
        timeout: int = 60,
        user: Optional[str] = None,
        workdir: Optional[str] = None
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Execute a shell command inside a running container.

        Args:
            container_id: Container ID or name
            command: Shell command to execute (limited to alphanumeric, spaces, and basic shell operators)
            timeout: Timeout in seconds
            user: Optional user to run as inside container
            workdir: Optional working directory inside container

        Returns:
            Tuple of (result dict, error message)
        """
        try:
            # Security: Validate container_id to prevent injection
            if not container_id or not isinstance(container_id, str):
                return None, "Invalid container ID"
            # Container IDs should be alphanumeric with optional hyphens/underscores
            if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$', container_id):
                return None, "Invalid container ID format"
            
            # Security: Validate command to prevent shell injection
            if not command or not isinstance(command, str):
                return None, "Invalid command"
            # Allow only safe characters: alphanumeric, spaces, basic shell operators, paths
            # Block dangerous patterns: $(), ``, ;, |, &, >, <, etc.
            dangerous_patterns = ['$(', '${', '`', ';', '|', '&', '>', '<', '&&', '||']
            for pattern in dangerous_patterns:
                if pattern in command:
                    return None, f"Command contains forbidden pattern: {pattern}"
            # Allow only whitelisted characters
            if not re.match(r'^[a-zA-Z0-9_./\-\s:*]+$', command):
                return None, "Command contains invalid characters"

            cmd = ['exec']
            if user:
                # Validate user parameter
                if not re.match(r'^[a-zA-Z0-9_-]+$', user):
                    return None, "Invalid user format"
                cmd.extend(['-u', user])
            if workdir:
                # Validate workdir parameter
                if not workdir.startswith('/') or '..' in workdir:
                    return None, "Invalid workdir path"
                cmd.extend(['-w', workdir])
            cmd.extend([container_id, '/bin/sh', '-lc', command])

            docker_cmd = build_privileged_cmd([self.docker_cmd] + cmd)

            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={'LC_ALL': 'C'}
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""
            output = stdout
            if stderr:
                output = f"{output}\n{stderr}" if output else stderr

            return {
                "output": output.strip(),
                "exit_code": int(result.returncode)
            }, None
        except subprocess.TimeoutExpired:
            return None, "Command timed out"
        except Exception as e:
            return None, str(e)
    
    def get_container_stats(self, container_id: str) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Get container resource usage statistics
        
        Args:
            container_id: Container ID or name
        
        Returns:
            Tuple of (stats dict, error message)
        """
        result, error = self._run_docker_command(['stats', '--no-stream', '--format', '{{json .}}', container_id])
        if error or result is None:
            return None, error
        
        try:
            stats = json.loads(result.stdout)
            return stats, None
        except json.JSONDecodeError as e:
            return None, f"Failed to parse stats: {str(e)}"
    
    def create_container_from_compose(
        self,
        compose_dict: Dict,
        app_name: str,
        pool_path: str,
        project_name: Optional[str] = None,
        callback: Optional[Callable[[str], None]] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Create and start containers from a Docker Compose configuration
        
        Args:
            compose_dict: Docker Compose configuration as dict
            app_name: Name of the app
            pool_path: Path to the pool where app data will be stored
            project_name: Optional project name (defaults to app_name)
            callback: Optional function(line: str) to receive real-time output
        
        Returns:
            Tuple of (success bool, error message)
        """
        import yaml
        
        if project_name is None:
            project_name = app_name
        
        try:
            # Replace ${POOL_PATH} placeholder in compose dict
            compose_str = yaml.dump(compose_dict)
            compose_str = compose_str.replace('${POOL_PATH}', pool_path)
            
            compose_file = write_compose_file(compose_str, project_name)

            cmd = build_privileged_cmd(
                [self.compose_cmd, '-f', compose_file, '-p', project_name, 'up', '-d'],
                env=COMPOSE_ENV,
            )

            if callback:
                # Use Popen for real-time output
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=COMPOSE_ENV
                )
                
                output = []
                assert process.stdout is not None  # stdout=PIPE
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        line_stripped = line.strip()
                        output.append(line_stripped)
                        callback(line_stripped)
                
                returncode = process.poll()
                full_output = "\n".join(output)
                
                # Clean up temp file
                os.unlink(compose_file)
                
                if returncode != 0:
                    return False, full_output
                return True, None
            else:
                # Original blocking behavior
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,  # Longer timeout for pulling images
                    env=COMPOSE_ENV
                )

                # Clean up temp file
                os.unlink(compose_file)

                if result.returncode != 0:
                    return False, privilege_error_message(result, cmd) or result.stderr
                
                return True, None
            
        except Exception as e:
            return False, str(e)

    def update_container_from_compose(
        self,
        compose_dict: Dict,
        app_name: str,
        pool_path: str,
        project_name: Optional[str] = None,
        callback: Optional[Callable[[str], None]] = None,
        pull: bool = True
    ) -> Tuple[bool, Optional[str]]:
        """
        Update containers from a Docker Compose configuration.
        Pulls new images first (unless pull is False, for a settings change),
        then recreates containers while preserving volumes.

        Args:
            compose_dict: Docker Compose configuration as dict
            app_name: Name of the app
            pool_path: Path to the app storage
            project_name: Optional project name (defaults to app_name)
            callback: Optional function(line: str) for real-time output

        Returns:
            Tuple of (success bool, error message)
        """
        import yaml

        if project_name is None:
            project_name = app_name

        try:
            compose_str = yaml.dump(compose_dict)
            compose_str = compose_str.replace('${POOL_PATH}', pool_path)

            compose_file = write_compose_file(compose_str, project_name)
            base_cmd = [self.compose_cmd, '-f', compose_file, '-p', project_name]
            compose_env = COMPOSE_ENV

            def run_step(step_args: List[str], timeout: int = 600) -> Tuple[bool, Optional[str]]:
                cmd = build_privileged_cmd(base_cmd + step_args, env=compose_env)
                if callback:
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        env=compose_env
                    )
                    output = []
                    assert process.stdout is not None  # stdout=PIPE
                    while True:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            line_stripped = line.strip()
                            output.append(line_stripped)
                            callback(line_stripped)
                    returncode = process.poll()
                    if returncode != 0:
                        return False, "\n".join(output)
                    return True, None

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=compose_env
                )
                if result.returncode != 0:
                    error = (privilege_error_message(result, cmd) or result.stderr or result.stdout
                             or f"Command failed: {' '.join(step_args)}")
                    return False, error
                return True, None

            if pull:
                ok, err = run_step(['pull'], timeout=900)
                if not ok:
                    return False, f"Failed to pull images: {err}"

            ok, err = run_step(['up', '-d'], timeout=900)
            if not ok:
                return False, f"Failed to recreate containers: {err}"

            return True, None
        except Exception as e:
            return False, str(e)
        finally:
            try:
                if 'compose_file' in locals() and compose_file and os.path.exists(compose_file):
                    os.unlink(compose_file)
            except Exception:
                pass
    
    def pull_image(self, image: str) -> Tuple[bool, Optional[str]]:
        """
        Pull a Docker image
        
        Args:
            image: Image name (e.g., 'nginx:latest')
        
        Returns:
            Tuple of (success bool, error message)
        """
        result, error = self._run_docker_command(['pull', image], timeout=300)
        if error or result is None:
            return False, error
        return True, None
    
    def check_docker_running(self) -> Tuple[bool, Optional[str]]:
        """
        Check if Docker daemon is running
        
        Returns:
            Tuple of (is_running bool, error message)
        """
        try:
            result = subprocess.run(
                [self.CMD['SYSTEMCTL'], 'is-active', 'docker'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            is_running = result.returncode == 0 and result.stdout.strip() == 'active'
            if not is_running:
                return False, "Docker daemon is not running"
            
            return True, None
        except Exception as e:
            return False, str(e)
