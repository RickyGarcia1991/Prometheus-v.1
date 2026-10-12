"""Explicit, bounded installation checks. No repair, inference or device capture."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
import time
import tomllib

from .hardware import detect_hardware, select_model
from .maintenance import _plain_path

MAX_METADATA_BYTES = 2_000_000
MAX_CODE_FILES = 10_000
MAX_CODE_BYTES = 512 * 1024 * 1024
MAX_MODELS = 512


def _json(path):
    path = _plain_path(path)
    if not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError('Missing or oversized metadata')
    with path.open('rb') as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError('Oversized metadata')
    try:
        return json.loads(raw.decode('utf-8-sig'))
    except RecursionError as error:
        raise ValueError('Metadata is too deeply nested') from error


def _result(identity, title, status, summary, next_step='', **details):
    return {'id': identity, 'title': title, 'status': status, 'summary': summary,
            'next_step': next_step, **details}


def _metadata(root, resources, version):
    identity, title = 'release_metadata', 'Active release and package version'
    try:
        provenance = _json(root / 'RELEASE-PROVENANCE.json')
        release = provenance.get('release') if isinstance(provenance, dict) else None
        if not isinstance(release, str) or not re.fullmatch(r'Prometheus-v[\w.-]{1,150}', release):
            raise ValueError('Invalid release metadata')
        project = _plain_path(root / 'pyproject.toml')
        if project.stat().st_size > MAX_METADATA_BYTES:
            raise ValueError('Oversized package metadata')
        declared = tomllib.loads(project.read_text(encoding='utf-8-sig')).get('project', {}).get('version')
        if declared != version:
            return _result(identity, title, 'fail', 'The running package version differs from its project metadata.',
                           'Rebuild the release with matching package and project versions.')
        if resources is None or root.parent != resources.parent or not root.name.startswith('Prometheus-v'):
            return _result(identity, title, 'untested', 'Development or copied package: active SSD selection is not confirmed.',
                           'Run this check from the SSD launcher after installing the reviewed release.')
        pointer = _json(resources.parent / 'PROMETHEUS-SSD-STATUS.json')
        raw = pointer.get('code_release') if isinstance(pointer, dict) else None
        if not isinstance(raw, str) or len(raw) > 500 or any(ord(c) < 32 for c in raw):
            raise ValueError('Invalid active release pointer')
        selected = PureWindowsPath(raw)
        if '..' in selected.parts or not re.fullmatch(r'Prometheus-v[\w.-]{1,150}', selected.name):
            raise ValueError('Invalid active release pointer')
        # Drive letters can change when the SSD moves. Resolve only the basename
        # on this SSD, never a path supplied by mutable status metadata.
        if resources.parent / selected.name != root or root.name != release or pointer.get('version') != version:
            return _result(identity, title, 'fail', 'SSD selection, release provenance and running package disagree.',
                           'Review the active release selection and restart from the SSD launcher.')
        return _result(identity, title, 'pass', f'Active selection and package version {version} agree.')
    except (OSError, ValueError, TypeError, AttributeError):
        return _result(identity, title, 'fail', 'Release metadata is missing, unreadable or malformed.',
                       'Restore valid release metadata from a reviewed build, then rerun this check.')


def _code_manifest(root):
    identity, title = 'code_manifest', 'Listed code file checksums'
    matched, total, seen, issues = 0, 0, set(), []
    scope = 'Checks listed files only. Unlisted files and signature authenticity were not checked.'

    def timed_out():
        summary = f'Time budget reached after {matched} matching files.'
        next_step = 'Retry when disk activity is lower; the remaining files are untested.'
        if issues:
            summary = f'{len(issues)} listed files are missing, changed or unreadable. ' + summary
            next_step = 'Review the detected file failures. ' + next_step
        return _result(identity, title, 'fail' if issues else 'blocked', summary, next_step,
                       checked_files=matched, issue_count=len(issues), files_needing_review=issues[:20],
                       scan_complete=False, scope=scope)

    try:
        rows = _json(root / 'SHA256-MANIFEST.json')
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CODE_FILES:
            raise ValueError('Invalid manifest list')
        # Validate every entry before following any of its file paths.
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Invalid manifest entry')
            raw, size, digest = row.get('path'), row.get('size_bytes'), row.get('sha256')
            if (not isinstance(raw, str) or not raw or len(raw) > 500 or '\\' in raw
                    or any(c in raw for c in '<>:"|?*') or any(ord(c) < 32 for c in raw)
                    or any(p in {'', '.', '..'} or p.endswith((' ', '.')) for p in raw.split('/'))
                    or PurePosixPath(raw).is_absolute() or raw.casefold() in seen
                    or type(size) is not int or not 0 <= size <= 64 * 1024 * 1024
                    or not isinstance(digest, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', digest)):
                raise ValueError('Invalid or duplicate manifest entry')
            seen.add(raw.casefold())
            total += size
            if total > MAX_CODE_BYTES:
                raise ValueError('Manifest exceeds byte budget')
        deadline = time.monotonic() + 30
        for row in rows:
            if time.monotonic() > deadline:
                return timed_out()
            try:
                file = _plain_path(root / row['path'])
                if not file.is_file() or file.stat().st_size != row['size_bytes']:
                    issues.append(row['path'])
                    continue
                digest = hashlib.sha256()
                read_bytes = 0
                with file.open('rb') as stream:
                    while block := stream.read(1024 * 1024):
                        read_bytes += len(block)
                        if time.monotonic() > deadline:
                            return timed_out()
                        if read_bytes > row['size_bytes']:
                            raise ValueError('File changed while reading')
                        digest.update(block)
                if read_bytes != row['size_bytes'] or digest.hexdigest().lower() != row['sha256'].lower():
                    issues.append(row['path'])
                else:
                    matched += 1
            except (OSError, ValueError):
                issues.append(row['path'])
        if issues:
            return _result(identity, title, 'fail', f'{len(issues)} listed files are missing, changed or unreadable; {matched} match.',
                           'Review modified files against the intended release. Development edits can make a saved manifest stale.',
                           checked_files=matched, issue_count=len(issues), files_needing_review=issues[:20], scope=scope)
        return _result(identity, title, 'pass', f'All {matched} listed files match their saved sizes and SHA-256 checksums.',
                       checked_files=matched, scope=scope)
    except (OSError, ValueError, TypeError):
        return _result(identity, title, 'fail', 'The code manifest is missing, malformed, unsafe or exceeds check limits.',
                       'Review the manifest paths, duplicate entries, sizes and checksums before rebuilding it.')


def _memory(path):
    identity, title = 'memory_database', 'Conversation database structure'
    try:
        path = _plain_path(path)
        if not path.is_file():
            return _result(identity, title, 'blocked', 'Conversation database does not exist; no database was created.',
                           'Save a conversation or select the existing memory database, then rerun.')
        if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-journal')):
            return _result(identity, title, 'blocked', 'Pending SQLite journal files require a stable database check.',
                           'Let database work finish and retry. Do not delete journal files.')
        if path.stat().st_size < 100:
            raise ValueError('Missing SQLite header')
        # A WAL-mode database may create shared-memory files even with mode=ro.
        # Refuse that mode so a health check never creates them or skips WAL data.
        with path.open('rb') as stream:
            header = stream.read(100)
        if header[:16] != b'SQLite format 3\0':
            raise ValueError('Invalid SQLite header')
        if header[18:20] != b'\x01\x01':
            return _result(identity, title, 'blocked', 'Database uses WAL or an unsupported journal format.',
                           'Use a consistent database backup for an offline integrity check.')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            result = db.execute('PRAGMA quick_check(1)').fetchone()
        if result != ('ok',):
            raise ValueError('Database quick check failed')
        return _result(identity, title, 'pass', 'Existing conversation database passed SQLite quick_check.',
                       scope='Structure check only; conversation contents and completeness were not reviewed.')
    except sqlite3.OperationalError:
        return _result(identity, title, 'blocked', 'Database check was busy, unreadable or exceeded its time budget.',
                       'Let other database work finish, check read access, then retry.')
    except (sqlite3.DatabaseError, OSError, ValueError):
        return _result(identity, title, 'fail', 'Conversation database could not pass its structure check.',
                       'Preserve the current file and verify a known backup before any repair.')


def _runtime(resources):
    identity, title = 'runtime', 'Portable runtime files'
    if resources is None:
        return _result(identity, title, 'blocked', 'Portable resource folder is not configured.',
                       'Start Prometheus through its SSD launcher.')
    missing = []
    for name, relative in (('Python', 'Python/python.exe'), ('Ollama', 'Ollama/runtime/ollama.exe')):
        try:
            file = _plain_path(resources / relative)
            if not file.is_file() or not file.stat().st_size:
                missing.append(name)
        except (OSError, ValueError):
            missing.append(name)
    return _result(identity, title, 'fail' if missing else 'pass',
                   'Missing or unreadable runtime files: ' + ', '.join(missing) if missing else 'Portable Python and Ollama files are present.',
                   'Restore the missing runtime from a reviewed installation.' if missing else '',
                   scope='Presence only; executables were not launched or authenticated.')


def _models(resources):
    identity, title = 'model_files', 'Installed model file completeness'
    complete, incomplete, count = [], [], 0
    try:
        if resources is None:
            raise FileNotFoundError()
        root = _plain_path(resources / 'Ollama/.ollama/models')
        library = _plain_path(root / 'manifests/registry.ollama.ai/library')
        # Only the same local library namespace used by the application's router.
        for family in library.iterdir():
            _plain_path(family)
            if not family.is_dir() or not re.fullmatch(r'[\w.-]{1,100}', family.name):
                raise ValueError('Invalid model family')
            count += 1
            if count > MAX_MODELS:
                raise ValueError('Too many model entries')
            for tag in family.iterdir():
                count += 1
                if count > MAX_MODELS or not re.fullmatch(r'[\w.-]{1,100}', tag.name):
                    raise ValueError('Invalid model tag')
                name = family.name + ':' + tag.name
                try:
                    data = _json(tag)
                    if (not isinstance(data, dict) or data.get('schemaVersion') != 2
                            or not isinstance(data.get('layers'), list) or not 1 <= len(data['layers']) <= 128):
                        raise ValueError('Invalid model manifest')
                    for row in [data.get('config'), *data['layers']]:
                        if (not isinstance(row, dict) or not isinstance(row.get('digest'), str)
                                or not re.fullmatch(r'sha256:[a-f0-9]{64}', row['digest'])
                                or type(row.get('size')) is not int or row['size'] <= 0):
                            raise ValueError('Invalid blob reference')
                        blob = _plain_path(root / 'blobs' / row['digest'].replace(':', '-'))
                        if not blob.is_file() or blob.stat().st_size != row['size']:
                            raise ValueError('Missing or incomplete blob')
                    complete.append(name)
                except (OSError, ValueError, TypeError):
                    incomplete.append(name)
        if not complete and not incomplete:
            raise FileNotFoundError()
        return _result(identity, title, 'fail' if incomplete else 'pass',
                       f'{len(complete)} complete models; {len(incomplete)} missing, malformed or incomplete.',
                       'Restore or finish installing the listed incomplete models.' if incomplete else '',
                       models=sorted(complete), incomplete_models=sorted(incomplete),
                       scope='Referenced blob presence and sizes only; model weights were not hashed or loaded.')
    except FileNotFoundError:
        return _result(identity, title, 'blocked', 'No local model manifests are available.',
                       'Start from the SSD launcher and check the local model installation.', models=[])
    except (OSError, ValueError, TypeError):
        return _result(identity, title, 'fail', 'Model library paths or manifests could not be safely checked.',
                       'Review local model directory names and file access.', models=[])


def run_health_check(memory_path, resources_root=None, *, release_root=None, running_version=None, hardware=None):
    """Return observations only; never infer task acceptance from file presence."""
    from prometheus_control_center import __version__
    root = Path(release_root or Path(__file__).resolve().parents[2]).absolute()
    resources = Path(resources_root).absolute() if resources_root else None
    hardware = hardware or detect_hardware()
    checks = [_metadata(root, resources, running_version or __version__), _code_manifest(root),
              _memory(memory_path), _runtime(resources), _models(resources)]
    names = set(checks[-1].get('models', []))
    selected = select_model(names, hardware)
    if not names:
        status, summary, step = 'blocked', 'No complete model is available for a RAM estimate.', 'Resolve model file checks first.'
    elif hardware.available_ram_gib is None:
        status, summary, step = 'untested', 'Available RAM could not be measured.', 'Retry when the operating system can report available RAM.'
    elif selected is None:
        status, summary, step = 'blocked', 'Current RAM or installed model profiles do not permit a supported helper.', 'Close other applications or use a supported smaller model. Exact calculation and source search remain available.'
    else:
        status, summary, step = 'pass', f'Current RAM permits {selected.model} under the existing selection policy.', ''
    checks.append(_result('model_ram', 'Current model RAM feasibility', status, summary, step,
                          installed_ram_gib=hardware.ram_gib, available_ram_gib=hardware.available_ram_gib,
                          scope='Estimate with no resident-model credit. Successful loading, speed and answer quality are untested.'))
    checks.append(_result('physical_acceptance', 'Microphone, speaker and portability acceptance', 'untested',
                          'No recording, audio playback, eject/reconnect or second-computer test was performed.',
                          'Review a spoken transcript, listen to a reply, then explicitly test safe eject/reconnect and startup on another computer.'))
    checks.append(_result('model_acceptance', 'Model task acceptance', 'untested',
                          'No model inference or answer-quality test was run by this health check.',
                          'Use Test available helpers for bounded task checks, then review a representative task yourself.'))
    counts = dict(Counter(item['status'] for item in checks))
    return {'checked_utc': datetime.now(timezone.utc).isoformat(), 'checks': checks, 'counts': counts,
            'network_used': False, 'inference_used': False, 'repairs_performed': False,
            'summary': (f"Health check: {counts.get('pass', 0)} passed, {counts.get('fail', 0)} failed, "
                        f"{counts.get('blocked', 0)} blocked, {counts.get('untested', 0)} untested. "
                        'Review the Maintenance cards for next steps.')}
