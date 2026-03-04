#!/usr/bin/env python3
"""
AlvaOS Docker Manager
Manages Docker containers for the app store
"""

import subprocess
import json
import os
from typing import List, Dict, Optional, Tuple


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
            # Check if we need sudo
            if os.geteuid() != 0:
                cmd = ['sudo', '-n', self.docker_cmd] + args
            else:
                cmd = [self.docker_cmd] + args
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={'LC_ALL': 'C'}
            )
            
            if result.returncode != 0:
                return None, result.stderr
            
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
        if error:
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
        if error:
            return None, error
        
        try:
            details = json.loads(result.stdout)
            if details and len(details) > 0:
                return details[0], None
            return None, "Container not found"
        except json.JSONDecodeError as e:
            return None, f"Failed to parse container details: {str(e)}"
    
    def start_container(self, container_id: str) -> Tuple[bool, Optional[str]]:
        """
        Start a container
        
        Args:
            container_id: Container ID or name
        
        Returns:
            Tuple of (success bool, error message)
        """
        result, error = self._run_docker_command(['start', container_id])
        if error:
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
        if error:
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
        if error:
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
        if error:
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
        if error:
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
            command: Shell command to execute
            timeout: Timeout in seconds
            user: Optional user to run as inside container
            workdir: Optional working directory inside container

        Returns:
            Tuple of (result dict, error message)
        """
        try:
            cmd = ['exec']
            if user:
                cmd.extend(['-u', user])
            if workdir:
                cmd.extend(['-w', workdir])
            cmd.extend([container_id, '/bin/sh', '-lc', command])

            if os.geteuid() != 0:
                docker_cmd = ['sudo', '-n', self.docker_cmd] + cmd
            else:
                docker_cmd = [self.docker_cmd] + cmd

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
        if error:
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
        callback: Optional[callable] = None
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
        import tempfile
        
        if project_name is None:
            project_name = app_name
        
        try:
            # Replace ${POOL_PATH} placeholder in compose dict
            compose_str = yaml.dump(compose_dict)
            compose_str = compose_str.replace('${POOL_PATH}', pool_path)
            
            # Write to temporary file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
                f.write(compose_str)
                compose_file = f.name
            
            # Run docker-compose up
            if os.geteuid() != 0:
                cmd = ['sudo', '-n', self.compose_cmd, '-f', compose_file, '-p', project_name, 'up', '-d']
            else:
                cmd = [self.compose_cmd, '-f', compose_file, '-p', project_name, 'up', '-d']
            
            if callback:
                compose_env = {'LC_ALL': 'C', 'COMPOSE_INTERACTIVE_NO_CLI': '1'}
                # Use Popen for real-time output
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=compose_env
                )
                
                output = []
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
                    env={'LC_ALL': 'C', 'COMPOSE_INTERACTIVE_NO_CLI': '1'}
                )
                
                # Clean up temp file
                os.unlink(compose_file)
                
                if result.returncode != 0:
                    return False, result.stderr
                
                return True, None
            
        except Exception as e:
            return False, str(e)
    
    def pull_image(self, image: str) -> Tuple[bool, Optional[str]]:
        """
        Pull a Docker image
        
        Args:
            image: Image name (e.g., 'nginx:latest')
        
        Returns:
            Tuple of (success bool, error message)
        """
        result, error = self._run_docker_command(['pull', image], timeout=300)
        if error:
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
