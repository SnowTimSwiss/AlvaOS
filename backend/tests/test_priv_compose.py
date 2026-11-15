"""Compose files: what the policy refuses because Docker would read it later."""

import pytest
import yaml

import priv_policy as p


@pytest.mark.parametrize('text', [
    'services:\n  x:\n    image: alpine\n    volumes: ["${X}:/host"]\n',
    'services:\n  x:\n    image: alpine\n    network_mode: "$NET"\n',
    'services:\n  x:\n    image: alpine\n    volumes: ["$$${X}:/host"]\n',
])
def test_compose_variables_are_denied(text):
    # compose would fill them in later, from the environment or the backend's .env file
    with pytest.raises(p.PolicyError):
        p.check_compose(text.encode())


def test_a_literal_dollar_in_compose_is_fine():
    p.check_compose(b'services:\n  x:\n    image: alpine\n    command: ["echo", "$$HOME"]\n')


@pytest.mark.parametrize('vol', ['./data:/data', '.:/app', {'type': 'bind', 'source': './x', 'target': '/x'}])
def test_compose_relative_bind_mounts_are_denied(vol):
    doc = yaml.safe_dump({'services': {'x': {'image': 'alpine', 'volumes': [vol]}}}).encode()
    with pytest.raises(p.PolicyError):
        p.check_compose(doc)


def test_compose_bind_mount_through_a_symlink_to_etc_is_denied(tmp_path):
    link = tmp_path / 'appdata'
    link.symlink_to('/etc')
    doc = yaml.safe_dump({'services': {'x': {'image': 'alpine', 'volumes': [f'{link}:/data']}}}).encode()
    with pytest.raises(p.PolicyError):
        p.check_compose(doc)


def test_named_volumes_and_pool_paths_still_pass():
    p.check_compose(yaml.safe_dump({'services': {'x': {
        'image': 'alpine', 'volumes': ['cache:/cache', '/mnt/alvaos/main/apps/x:/data']}}}).encode())


# ── Samba and NFS share paths ────────────────────────────────────────────────

def test_a_share_path_through_a_symlink_to_etc_is_denied():
    def realpath(path):
        return '/etc' if path == '/mnt/alvaos/main/share' else path

    with pytest.raises(p.PolicyError):
        p.check_smb_conf(b'[media]\n    path = /mnt/alvaos/main/share\n', realpath=realpath)
    with pytest.raises(p.PolicyError):
        p.check_exports(b'/mnt/alvaos/main/share 192.168.1.0/24(rw,sync)\n', realpath=realpath)
    p.check_smb_conf(b'[media]\n    path = /mnt/alvaos/main/media\n', realpath=lambda x: x)


@pytest.mark.parametrize('line', ['wide links = yes', 'allow insecure wide links = yes'])
def test_samba_wide_links_are_denied(line):
    with pytest.raises(p.PolicyError):
        p.check_smb_conf(f'[media]\n    path = /mnt/alvaos/main/media\n    {line}\n'.encode(), realpath=lambda x: x)
