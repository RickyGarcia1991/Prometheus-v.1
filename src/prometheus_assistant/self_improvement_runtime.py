"""Bind application proposals to the configured host verifier and test runner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from .autonomous_promotion import WindowsPromotionHost, REQUIRED_CHECKS
from .compact_chat import MemorySignal, candidates
from .hardware import detect_hardware
from .ollama import OllamaClient, GenerationCancelled
from .self_improvement import SelfImprovementEngine, _no_links, _read


def _check_context(messages, schema, context, output_tokens):
    # Count bytes, including the schema, rather than silently truncating source.
    size = len(json.dumps({'messages': messages, 'format': schema},
                         ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    if size + output_tokens + 104 > context:
        raise ValueError('The complete change request does not fit the local model context. '
                         'Choose a smaller source file or a shorter, more specific change. No source was truncated.')


def _generator(app):
    def generate(messages, schema):
        if app.cancel_event.is_set():
            raise GenerationCancelled('Change preparation cancelled.')
        client = OllamaClient(timeout=5)
        names = {row['name'] for row in client.local_models()
                 if isinstance(row, dict) and isinstance(row.get('name'), str)
                 and not any(row.get(key) for key in ('remote_host', 'remote_model', 'cloud'))}
        _, profiles = candidates('Implement a small source code change', names,
            detect_hardware(), app.model_evidence, task='coding',
            resident=client.resident_memory_gib())
        fitting = []
        for profile in profiles:
            try:
                _check_context(messages, schema, profile.context, 1024)
                fitting.append(profile)
            except ValueError:
                continue
        if not fitting:
            raise ValueError('No installed local helper fits this complete source and request in available memory. '
                             'Use a smaller target or free memory before trying again.')
        profile = fitting[0]
        app.progress('Preparing a source change with ' + profile.model)
        model = OllamaClient(model=profile.model, timeout=180,
                             num_ctx=profile.context, num_thread=profile.cpu_threads)
        model.ensure_local_model()
        reply, _ = model.chat_stream(messages, json_format=schema, num_predict=1024,
                                     cancel_event=MemorySignal(app.cancel_event))
        app._self_improvement_model = profile.model
        return reply
    return generate


def _run_checks(host, app, candidate, baseline, checks, *, cancel_event=None):
    if tuple(checks) != REQUIRED_CHECKS:
        raise ValueError('The fixed regression contract changed.')
    generated_by = getattr(app, '_self_improvement_model', None)
    if generated_by:
        OllamaClient(model=generated_by, timeout=5)._request('/api/generate',
            {'model': generated_by, 'keep_alive': 0})
        app._self_improvement_model = None
    host.jobs.mkdir(parents=True, exist_ok=True)
    job = host.jobs / ('test-' + uuid.uuid4().hex)
    _no_links(job)
    job.mkdir()
    report, cancel = job / 'test-report.json', job / 'cancel.request'
    arguments = [host.tool('python'), '-B', host.tool('test_runner'),
                 '--candidate', candidate, '--baseline', baseline,
                 '--report', report, '--cancel-file', cancel]
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    app.progress('Running the complete Python and interface regression checks')
    deadline = time.monotonic() + 2400
    cancelled = False
    cancel_started = None
    with (job / 'runner-output.txt').open('w', encoding='utf-8') as output:
        with subprocess.Popen(list(map(str, arguments)), stdout=output, stderr=subprocess.STDOUT,
                env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            while process.poll() is None:
                if (cancel_event is not None and cancel_event.is_set()) or time.monotonic() > deadline:
                    if not cancelled:
                        cancel.write_text('Cancel the owned test processes.\n', encoding='utf-8')
                        cancelled = True
                        cancel_started = time.monotonic()
                if cancel_started is not None and time.monotonic() - cancel_started > 30:
                    if os.name == 'nt':
                        killer = Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'
                        subprocess.run([str(killer), '/PID', str(process.pid), '/T', '/F'],
                            capture_output=True, timeout=15,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if process.poll() is None:
                        process.kill()
                time.sleep(.1)
    if cancelled:
        raise GenerationCancelled('Change testing cancelled; no proposal was approved or installed.')
    if not report.is_file():
        raise ValueError('The trusted test runner did not produce a result; no change qualifies.')
    result = json.loads(_read(report, 1_000_000).decode('utf-8'))
    if not isinstance(result, dict):
        raise ValueError('Invalid trusted test result.')
    # Failed checks are returned for the engine's bounded repair attempts.
    if process.returncode and all(row.get('exit_code') == 0 for row in result.get('checks', [])):
        raise ValueError('The test runner failed after reporting success.')
    if not process.returncode and (result.get('state') != 'passed' or
            result.get('payloads_unchanged') is not True or result.get('isolation_clean') is not True):
        raise ValueError('The test runner did not confirm unchanged payloads and clean isolation checks.')
    return result


def get_self_improvement(app):
    """Use this host's explicit tool pins; never infer signing credentials."""
    cached = getattr(app, '_self_improvement_engine', None)
    if cached is not None:
        return cached
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise ValueError('This host has no configured application-data directory.')
    config = Path(local) / 'Prometheus/self-improvement-host.json'
    if not config.is_file():
        raise ValueError('Application-change testing and signing are not configured on this computer. '
                         'Configure its host tools before preparing a replacement.')
    host = WindowsPromotionHost(config)
    baseline = Path(__file__).resolve().parents[2]

    def verify(root):
        root = Path(root).resolve(strict=True)
        digest = hashlib.sha256(_read(root / 'SHA256-MANIFEST.json', 1_000_000)).hexdigest()
        # A valid old signature is insufficient: it must still be the active release.
        selection = host.capture_selection(root, digest)
        host.assert_selection(selection)
        return {'verified': True}

    engine = SelfImprovementEngine(baseline, Path(app.memory_path).parent,
        verifier=verify, generator=_generator(app),
        runner=lambda candidate, current, checks, **kwargs:
            _run_checks(host, app, candidate, current, checks, **kwargs),
        promoter=host, required_checks=REQUIRED_CHECKS, max_source_bytes=1500)
    app._self_improvement_engine = engine
    return engine
