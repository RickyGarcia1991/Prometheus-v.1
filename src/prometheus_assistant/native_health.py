"""Passive Ubuntu health observations, separate from Windows release acceptance."""
from pathlib import Path
import sys
import tomllib

def checks(root, resources, version, memory):
    from .health_check import _result, _json, _code_manifest, _memory
    from .linux_resources import system_executable
    from .ollama import OllamaClient, LocalModelError
    result = []
    try:
        declared = tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version']
        checkpoint = _json(root/'NATIVE-CHECKPOINT.json')
        if (declared != version or not isinstance(checkpoint, dict)
                or checkpoint.get('platform') != 'Linux' or checkpoint.get('version') != version
                or not isinstance(checkpoint.get('source_commit'), str)
                or len(checkpoint['source_commit']) != 40
                or any(c not in '0123456789abcdef' for c in checkpoint['source_commit'])):
            raise ValueError('Native metadata mismatch')
        result.append(_result('release_metadata', 'Ubuntu deployment', 'pass',
            'Running version and native checkpoint metadata agree.',
            source_commit=checkpoint.get('source_commit'),
            scope='Local deployment record; this is not Windows signed-release acceptance.'))
    except (OSError, ValueError, KeyError, TypeError):
        result.append(_result('release_metadata', 'Ubuntu deployment', 'untested',
            'Native checkpoint metadata is missing or does not match this package.',
            'Review the native deployment record. Do not substitute Windows release metadata.'))
    result.append(_code_manifest(root, manifest_name='NATIVE-SOURCE-MANIFEST.json'))
    result.append(_memory(memory))
    try:
        python = system_executable(sys.executable)
        from .integrations import ollama_executable
        ollama = ollama_executable()
        result.append(_result('runtimes', 'Native Ubuntu runtimes', 'pass' if ollama else 'blocked',
            'OS-managed Python and Ollama executables are present.' if ollama else 'The native Ollama executable is unavailable.',
            python=str(python), ollama=ollama, scope='Executable ownership and presence; no model inference.'))
    except (OSError, ValueError):
        result.append(_result('runtimes', 'Native Ubuntu runtimes', 'blocked',
            'Native executable ownership or availability could not be verified.'))
    try:
        rows = OllamaClient(timeout=2).local_models()
        names = sorted({row['name'] for row in rows if isinstance(row, dict)
            and isinstance(row.get('name'), str) and row['name'].strip()
            and 'cloud' not in row['name'].lower()
            and not (row.get('remote_host') or row.get('remote_model') or row.get('cloud'))})
        result.append(_result('model_files', 'Running local model inventory', 'pass' if names else 'blocked',
            f'{len(names)} local models reported by the running Ubuntu server.',
            models=names, scope='Loopback inventory only; weights and answer quality were not checked.'))
    except LocalModelError:
        result.append(_result('model_files', 'Running local model inventory', 'blocked',
            'The local Ollama server did not return its inventory.',
            'Check the local Ollama service.', models=[]))
    return result

def resources_check(resources):
    from .health_check import _result
    if resources is None or not Path(resources).is_dir():
        return _result('resource_library', 'Connected reference library', 'blocked',
            'The HDD resource directory is not connected to this application.',
            'Configure the mounted resource directory for this host.')
    from .offline_archive import inventory
    from .linux_resources import load_zim
    try:
        reader = load_zim()
        native = bool(reader)
    except ImportError:
        native = False
    # Presence only: large archive hashes and retrieval tests run as separate audits.
    rows = inventory(str(resources), verify=False)
    return _result('resource_library', 'Connected reference library', 'pass' if native else 'blocked',
        'The resource directory is connected; the native offline reader loads.' if native
        else 'The directory is connected but the native offline reader is unavailable.',
        scope='Presence and native reader readiness only. See the dated resource audit for hashes and retrieval tests.',
        archive_inventory=rows, native_reader=native)
