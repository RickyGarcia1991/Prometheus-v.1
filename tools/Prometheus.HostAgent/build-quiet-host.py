"""Build a new quiet-host v3 candidate; never install or sign it.

All compiler/reference inputs are explicit. The receipt records their hashes.
Requires Windows .NET Framework references and a Roslyn csc.dll compiler.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

SOURCE = Path(__file__).with_name('Prometheus-QuietHost-v3.cs')
NAME = 'Prometheus-QuietHost-v3.exe'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(dotnet, compiler, framework, output):
    dotnet, compiler, framework, output = map(lambda p: Path(p).resolve(), (dotnet, compiler, framework, output))
    references = [framework / name for name in ('mscorlib.dll', 'System.dll', 'System.Core.dll')]
    for path in [SOURCE, dotnet, compiler, *references]:
        if not path.is_file():
            raise ValueError(f'Missing build input: {path}')
    output.mkdir(parents=True, exist_ok=True)
    target = output / NAME
    if target.exists() or (output / 'quiet-host-build-receipt.json').exists():
        raise ValueError('Use a new candidate output directory; existing artifacts are preserved.')
    args = [str(dotnet), str(compiler), '/nologo', '/noconfig', '/nostdlib+', '/deterministic+',
            '/target:winexe', '/optimize+', '/debug-', '/platform:anycpu', '/out:' + str(target),
            *['/reference:' + str(p) for p in references], str(SOURCE)]
    subprocess.run(args, check=True, timeout=90)
    receipt = {'identity': NAME, 'version': '3.0.0.0', 'signed': False, 'installed': False,
               'source': {'path': str(SOURCE), 'sha256': digest(SOURCE)},
               'inputs': {str(p): digest(p) for p in [dotnet, compiler, *references]},
               'command': args, 'output_sha256': digest(target),
               'builder_sha256': digest(__file__),
               'compiler_dependencies': {str(p): digest(p) for p in sorted(compiler.parent.iterdir())
                                         if p.is_file() and p.suffix in ('.dll', '.json')},
               'provenance': 'New v3 source build; no claim that historical source reproduced v2.'}
    (output / 'quiet-host-build-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('dotnet', 'compiler', 'framework', 'output'):
        parser.add_argument('--' + key, required=True, type=Path)
    options = parser.parse_args()
    print(json.dumps(build(options.dotnet, options.compiler, options.framework, options.output), indent=2))

