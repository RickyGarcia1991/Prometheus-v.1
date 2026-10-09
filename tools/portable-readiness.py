"""Read portable prerequisites without WMI, host installs, or model startup."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


def inspect(root):
    root = Path(root).resolve()
    report = dict(core_present=False, core_integrity=False, python_present=False,
                  ollama_present=False, model_present=False, memory_present=False,
                  memory_integrity='not checked', model_names=[], problems=[])
    resource = root / 'Prometheus-Resources'
    report['python_present'] = (resource / 'Python/python.exe').is_file()
    report['ollama_present'] = (resource / 'Ollama/runtime/ollama.exe').is_file()
    try:
        meta = json.loads((root / 'PROMETHEUS-SSD-STATUS.json').read_text(encoding='utf-8-sig'))
        name = str(meta['code_release']).replace('\\', '/').rstrip('/').split('/')[-1]
        if not re.fullmatch(r'Prometheus-[A-Za-z0-9._-]+', name):
            raise ValueError('Invalid active core release name')
        package = root / name
        report['core_release'] = name
        report['core_present'] = (package / 'prometheus.py').is_file() and (package / 'src/prometheus_assistant/cli.py').is_file()
        entries = json.loads((package / 'SHA256-MANIFEST.json').read_text(encoding='utf-8-sig'))
        if not isinstance(entries, list) or not entries:
            raise ValueError('Empty or invalid core manifest')
        seen = set()
        for entry in entries:
            relative = entry['path']
            target = (package / relative).resolve()
            key = str(target).casefold()
            if not target.is_relative_to(package.resolve()) or key in seen:
                raise ValueError('Invalid or duplicate core manifest path')
            seen.add(key)
            with target.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != entry['sha256'].lower():
                raise ValueError('Core checksum mismatch: ' + relative)
        for required in ('prometheus.py', 'src/prometheus_assistant/cli.py'):
            if str((package / required).resolve()).casefold() not in seen:
                raise ValueError('Required core file missing from manifest: ' + required)
        report['core_integrity'] = True
        report['core_files_verified'] = len(entries)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report['problems'].append('Core: ' + str(exc))
    try:
        models = resource / 'Ollama/.ollama/models'
        manifests = sorted((models / 'manifests').rglob('*'))
        manifests = [path for path in manifests if path.is_file()]
        if not manifests:
            raise ValueError('No installed model manifests')
        for path in manifests:
            manifest = json.loads(path.read_text(encoding='utf-8-sig'))
            layers = manifest.get('layers')
            if not isinstance(layers, list) or not layers:
                raise ValueError('Model manifest has no layers: ' + path.name)
            for entry in [manifest['config']] + layers:
                digest = entry['digest']
                if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                    raise ValueError('Invalid model digest')
                blob = models / 'blobs' / digest.replace(':', '-')
                if not blob.is_file() or blob.stat().st_size != entry['size']:
                    raise ValueError('Missing or incomplete model blob: ' + blob.name)
            report['model_names'].append(path.relative_to(models / 'manifests').as_posix())
        report['model_present'] = True
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report['problems'].append('Models: ' + str(exc))
    db = root / 'Prometheus-Data/memory.sqlite3'
    report['memory_present'] = db.is_file()
    if db.is_file():
        try:
            with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=5) as connection:
                findings = [row[0] for row in connection.execute('PRAGMA quick_check')]
            report['memory_integrity'] = 'ok' if findings == ['ok'] else '; '.join(findings)
        except sqlite3.Error as exc:
            report['memory_integrity'] = 'check failed: ' + str(exc)
    report['portable_ready'] = all(report[key] for key in ('core_present', 'core_integrity', 'python_present', 'ollama_present', 'model_present', 'memory_present')) and report['memory_integrity'] == 'ok'
    report['runtime_architecture_bits'] = ctypes.sizeof(ctypes.c_void_p) * 8
    report['notes'] = ['Model presence uses manifest sizes; full blob hashes are checked by the separate SSD audit.',
                       'Portable readiness does not verify USB detection on another computer or model response speed.']
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    result = inspect(args.root)
    print(json.dumps(result))
    sys.exit(0 if result['portable_ready'] else 2)
