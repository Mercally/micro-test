"""Install pinned WSL binaries in .state/tools; never change system executables."""
import hashlib
import io
import os
from pathlib import Path
import tarfile
from kube import run

TOOLS = {
    'k3d': ('https://github.com/k3d-io/k3d/releases/download/v5.8.3/k3d-linux-amd64',
            'dbaa79a76ace7f4ca230a1ff41dc7d8a5036a8ad0309e9c54f9bf3836dbe853e', None),
    'kubectl': ('https://dl.k8s.io/release/v1.34.1/bin/linux/amd64/kubectl',
                '7721f265e18709862655affba5343e85e1980639395d5754473dafaadcaa69e3', None),
    'istioctl': ('https://github.com/istio/istio/releases/download/1.31.3/istio-1.31.3-linux-amd64.tar.gz',
                 'b32bba09bc6138d738fd19a09ef6d0fcdd5c5c013efeb72969663c24a2bddd47',
                 'istio-1.31.3/bin/istioctl'),
}


def install(root):
    directory = Path(root) / '.state/tools'
    directory.mkdir(parents=True, exist_ok=True)
    for name, (url, digest, member) in TOOLS.items():
        archive = directory / (name + '.download')
        if archive.is_symlink():
            raise ValueError('Tool download path must not be a symlink')
        if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            print('Downloading verified ' + name, flush=True)
            run(['curl', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
                 '--proto-redir', '=https', '--max-time', '180', url, '-o', str(archive)])
        data = archive.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(name + ' checksum mismatch')
        if member:
            with tarfile.open(fileobj=io.BytesIO(data)) as bundle:
                data = bundle.extractfile(member).read()
        binary = directory / name
        if binary.is_symlink():
            raise ValueError('Tool destination must not be a symlink')
        if not binary.exists() or binary.read_bytes() != data:
            binary.write_bytes(data)
        binary.chmod(0o700)
    os.environ['PATH'] = str(directory) + os.pathsep + os.environ.get('PATH', '')
