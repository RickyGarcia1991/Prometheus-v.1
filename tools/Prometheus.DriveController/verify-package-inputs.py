"""Read-only verification of canonical lifecycle files in a controller build."""
from pathlib import Path
import argparse
import hashlib
import json

CANONICAL = Path(__file__).resolve().parent.parent / 'Prometheus.HostAgent'
INPUTS = (
    'Prometheus-Controller-Common.ps1', 'Prometheus-Drive-Engine.ps1',
    'Prometheus-Eject-Orchestrator.ps1', 'Prometheus-Lifecycle.ps1',
    'Prometheus-Safe-Eject.ps1', 'verify-portable-memory.py',
)


def verify(output):
    output = Path(output)
    if not output.is_dir():
        raise ValueError('Controller output directory is missing.')
    scripts = {path.relative_to(output).as_posix() for path in output.rglob('*')
               if path.suffix.lower() in ('.ps1', '.py')}
    if scripts != set(INPUTS):
        raise ValueError('Controller output has missing or unexpected lifecycle scripts.')
    hashes = {}
    for name in INPUTS:
        source, target = CANONICAL / name, output / name
        if not source.is_file() or not target.is_file() or target.is_symlink():
            raise ValueError(f'Canonical controller input is not a regular file: {name}')
        expected = source.read_bytes()
        if expected != target.read_bytes():
            raise ValueError(f'Controller input differs from canonical HostAgent source: {name}')
        hashes[name] = hashlib.sha256(expected).hexdigest()
    return hashes


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps({'canonical_source': str(CANONICAL), 'files': verify(args.output)}, indent=2))
