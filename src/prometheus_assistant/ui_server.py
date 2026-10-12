"""Local adaptive interface. One real job at a time; no model needed to open it."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import webbrowser

from .hardware import detect_hardware, resource_root, select_model
from .memory import MemoryStore
from .reference_cache import reference_session
from .model_routing import routing_record, ROLE_MODELS, choose_helper
from . import ui_workspace
from .model_quality import ModelEvidence
from .ollama import OllamaClient, GenerationCancelled
from .continuous_capture import EmptyRecording
from .request_timing import RequestTiming, stage, timed

ASSETS = Path(__file__).with_name('ui_assets')
BROWSER_PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=()'
LANGUAGES = ('Auto', 'Python', 'JavaScript', 'TypeScript', 'HTML / CSS', 'SQL', 'C',
             'C++', 'C#', 'Java', 'Kotlin', 'Swift', 'Go', 'Rust', 'Ruby', 'PHP',
             'R', 'Julia', 'MATLAB / Octave', 'Dart / Flutter', 'Lua', 'Perl',
             'Bash', 'PowerShell', 'F#', 'Haskell', 'Elixir', 'Erlang', 'Scala',
             'Clojure', 'OCaml', 'Zig', 'Assembly', 'Fortran', 'COBOL',
             'Solidity', 'VHDL / Verilog', 'Arduino', 'YAML / JSON / TOML',
             'Dockerfile / Terraform', 'GraphQL', 'Protobuf')
MODES = {'chat', 'coding', 'ask', 'vision', 'reach', 'capabilities', 'library', 'calculate', 'research', 'project', 'dictate', 'tools', 'models', 'home', 'core'}
MODES |= ui_workspace.MODES
PROFILES = {'public', 'medical', 'math', 'engineering', 'openai', 'general'}


def installed_models():
    root = resource_root()
    if not root:
        return set()
    library = Path(root) / 'Ollama/.ollama/models/manifests/registry.ollama.ai/library'
    if not library.is_dir():
        return set()
    return {f'{model.name}:{tag.name}' for model in library.iterdir() if model.is_dir()
            for tag in model.iterdir() if tag.is_file()}


def hardware_status():
    hardware = detect_hardware()
    profile = select_model(installed_models(), hardware, OllamaClient(timeout=.5).resident_memory_gib())
    return {**asdict(hardware), 'chat_ready': profile is not None,
            'suggested_model': profile.model if profile else None,
            'fallback': 'Offline references and exact calculation remain available.'}


def choose_reference(prompt):
    words = set(re.findall(r'[a-z]+', prompt.lower()))
    if words & {'law', 'court', 'judge', 'legal', 'summons', 'filing', 'deadline', 'lawsuit', 'government'}:
        return 'public'
    if words & {'health', 'disease', 'medicine', 'medical', 'diabetes', 'symptoms', 'treatment'}:
        return 'medical'
    if words & {'code', 'coding', 'python', 'programming', 'arduino', 'gpio', 'circuit', 'sensor'}:
        return 'engineering'
    if words & {'openai', 'embedding', 'embeddings'}:
        return 'openai'
    if words & {'algebra', 'theorem', 'geometry', 'mathematics'}:
        return 'math'
    return 'general'


def reference_query(query, profile):
    query = query[:400]
    if profile in {'public', 'engineering', 'openai'}:
        from .source_library import search_sources
        return search_sources(query, profile=profile)
    if profile == 'medical':
        from .library import search_library
        return search_library(query)
    if profile == 'math':
        from .mathematics import search_math
        return search_math(query)
    from .archive_worker import search
    return search(resource_root(), query[:256], project='auto')


def readable_result(value):
    if isinstance(value, dict):
        if isinstance(value.get('reply'), str):
            return value['reply']
        if 'result' in value:
            return 'Result: ' + str(value['result'])
        if 'exact' in value:
            return f"{value['expression']} = {value['exact']}\nDecimal: {value['decimal_approximation']}"
        rows = value.get('results', value.get('sources', value.get('articles')))
        if isinstance(rows, list):
            if not rows:
                return 'No matching source found. Try a shorter query or another reference collection.'
            lines = []
            for row in rows[:5]:
                if isinstance(row, dict):
                    title = row.get('title') or row.get('source') or row.get('path') or 'Reference'
                    excerpt = row.get('excerpt') or row.get('summary') or row.get('snippet') or row.get('description') or row.get('text') or ''
                    location = row.get('url') or row.get('source_url') or row.get('path') or row.get('location') or ''
                    if row.get('line'):
                        location += f" line {row['line']}"
                    if row.get('location') and row['location'] != location:
                        location += ' | '+row['location']
                    if row.get('citation') and row['citation'] != location:
                        location += ' | '+row['citation']
                    if 'is_latest' in row:
                        location += ' | '+('Current saved version' if row['is_latest'] else 'Historical saved version')
                    if row.get('imported_at'):
                        location += f" | Source date: {row.get('source_date') or 'unknown'} | Imported: {row['imported_at']}"
                    lines.append(f'{title}\n{str(excerpt)[:2000]}\n{location}')
            if lines:
                return '\n\n'.join(lines)
    return json.dumps(value, ensure_ascii=False, indent=2)[:20000]


def validate_job(body):
    if isinstance(body, dict) and body.get('mode') == 'core':
        if set(body) != {'mode', 'worker', 'tool', 'arguments', 'grant_id', 'invocation_id'}:
            raise ValueError('Use exactly the scoped Core job fields.')
        if any(not isinstance(body[k], str) or len(body[k]) > 80 for k in ('worker', 'tool', 'grant_id', 'invocation_id')):
            raise ValueError('Invalid scoped Core identifiers.')
        from .builtin_tools import build_builtin_registry
        from .scoped_grants import scope
        spec = build_builtin_registry(None).get(body['worker'], body['tool'])
        request, _ = scope(spec, body['arguments'])
        return {**body, 'arguments': dict(request.arguments)}
    if not isinstance(body, dict) or set(body) - ({'mode', 'prompt', 'reach_action', 'source', 'profile', 'provider', 'session', 'language', 'project', 'home_key'} | ui_workspace.FIELDS):
        raise ValueError('Invalid request fields.')
    mode = body.get('mode')
    if mode not in MODES:
        raise ValueError('Choose an available workspace mode.')
    from .capabilities import SOURCES
    if 'source' in body and (mode != 'ask' or not isinstance(body['source'], str) or body['source'] not in SOURCES):
        raise ValueError('Choose a valid source in Ask with evidence mode.')
    if mode == 'vision':
        from .vision import validate_upload
        validate_upload(body.get('content', ''))
    if 'home_key' in body and (mode != 'home' or not isinstance(body['home_key'], str)
                               or len(body['home_key']) > 128 or '\x00' in body['home_key']):
        raise ValueError('Use a valid control key only in Smart home mode.')
    prompt = body.get('prompt', '')
    if not isinstance(prompt, str) or len(prompt) > 4000 or ('\x00' in prompt):
        raise ValueError('Use a prompt of at most 4000 characters.')
    if mode not in {'dictate', 'tools', 'models', 'capabilities'} | ui_workspace.NO_PROMPT and not prompt.strip():
        raise ValueError('Enter a question or expression.')
    if mode == 'reach' or 'reach_action' in body:
        from .public_reach import validate_request
        if mode != 'reach':
            raise ValueError('Select Public reach to use its online operations.')
        validate_request(body.get('reach_action'), prompt)
    if body.get('profile', 'public') not in PROFILES:
        raise ValueError('Unknown reference collection.')
    from .public_resources import PROVIDERS
    if body.get('provider', 'crossref') not in PROVIDERS:
        raise ValueError('Unknown public source.')
    if body.get('language', 'Auto') not in LANGUAGES:
        raise ValueError('Unknown language selection.')
    session = body.get('session')
    if session is not None and (not isinstance(session, str) or not re.fullmatch(r'[a-f0-9]{32}', session)):
        raise ValueError('Invalid conversation identifier.')
    project = body.get('project', '')
    if not isinstance(project, str) or len(project) > 500 or '\x00' in project:
        raise ValueError('Invalid project directory.')
    if mode == 'ask' and body.get('source') == 'project' and not project.strip():
        raise ValueError('Select the project folder to use as evidence.')
    ui_workspace.validate(body)
    return {**body, 'prompt': prompt.strip()}


class Interface:
    def __init__(self, memory_path):
        self.memory_path = Path(memory_path)
        self.preview = os.environ.get('PROMETHEUS_DEVELOPMENT_PREVIEW') == '1'
        self.data_root = self.memory_path.parent/'Interface/Data'
        self.model_evidence = ModelEvidence(self.data_root/'Routing/evidence.sqlite3')
        self.last_backup = None
        self.cancel_event = threading.Event()
        self.dictation_finish = threading.Event()
        self.dictation_last_pulse = 0.
        self.speech_lock = threading.Lock()
        self.next_maintenance = time.monotonic()+60
        self.token = secrets.token_urlsafe(32)
        self.jobs = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='prometheus-ui')
        self.current = None
        self.closing = False
        self.last_seen = time.monotonic()
        self.state = 'idle'
        self.cache_context = None
        self.maintenance_error = None
        self.maintenance_turn = 1
        from .remote_access import RemoteAccess
        self.remote = RemoteAccess()
        from .scoped_grants import GrantStore
        from .host_authority import authority_path
        self.grants = GrantStore(authority_path())

    def status(self):
        self.last_seen = time.monotonic()
        hardware = hardware_status()
        with self.lock:
            return {'hardware': hardware, 'state': self.state,
                    'busy': self.current is not None, 'closing': self.closing,
                    'job': self.current, 'job_background': self.jobs.get(self.current, {}).get('background', False),
                    'saved_locally': True, 'development_preview': self.preview,
                    'maintenance_error': self.maintenance_error}

    def append_partial(self, text):
        with self.lock:
            if self.current:
                row = self.jobs[self.current]
                row['partial'] = (row.get('partial', '')+text)[-32000:]

    def progress(self, stage, *, reset=False):
        with self.lock:
            if self.current:
                self.jobs[self.current]['stage'] = stage
                if reset:
                    self.jobs[self.current]['partial'] = ''

    def cancel(self, job_id):
        with self.lock:
            if job_id != self.current or not self.jobs[job_id].get('cancellable'):
                raise ValueError('This request is complete or cannot be interrupted safely.')
            self.cancel_event.set()
            self.jobs[job_id]['stage'] = 'Stopping current request…'
        return {'cancel_requested': True}

    def dictation_control(self, job_id, *, finish=False):
        with self.lock:
            row = self.jobs.get(job_id, {})
            if (self.closing or job_id != self.current or row.get('action') != 'dictate'
                    or row.get('state') != 'running' or not row.get('recording')
                    or self.cancel_event.is_set()
                    or time.monotonic()-self.dictation_last_pulse >= 20):
                raise ValueError('This dictation is no longer recording.')
            if finish:
                self.dictation_finish.set()
                row['stage'] = 'Finishing recording…'
                return {'finish_requested': True}
            self.dictation_last_pulse = time.monotonic()
            self.last_seen = self.dictation_last_pulse
            return {'recording': True}

    def dictation_alive(self, job_id):
        with self.lock:
            return (not self.closing and job_id == self.current
                    and time.monotonic()-self.dictation_last_pulse < 20)

    def stop_dictation(self):
        """Shutdown/owner lock discards an active voice turn without affecting other work."""
        with self.lock:
            if self.jobs.get(self.current, {}).get('action') == 'dictate':
                self.cancel_event.set()

    def idle_maintenance(self):
        if time.monotonic() < self.next_maintenance or self.closing:
            return
        self.next_maintenance = time.monotonic()+60
        if self.current is not None:
            return
        errors = []
        source_due = update_due = False
        try:
            source_due = any(r.get('enabled') and r.get('due') for r in ui_workspace.schedule(self).status().get('watches', []))
        except Exception as error:
            errors.append('Source watch: '+str(error)[:180])
        try:
            update_due = ui_workspace.component_updates(self).status().get('due_count', 0) > 0
        except Exception as error:
            errors.append('Component updates: '+str(error)[:180])
        self.maintenance_error = '; '.join(errors) or None
        try:
            # Alternate when both queues are due, so neither can starve the other.
            if update_due and (not source_due or self.maintenance_turn % 2 == 0):
                self.submit({'mode': 'check_updates', 'prompt': ''}, background=True)
            elif source_due:
                self.submit({'mode': 'check_sources', 'prompt': ''}, background=True)
            self.maintenance_turn += 1
        except Exception as error:
            self.maintenance_error = str(error)[:400]

    def ensure_runtime(self):
        from .application_policy import require_dispatch
        require_dispatch('ollama.request')
        client = OllamaClient(timeout=2)
        try:
            client.local_models()
            return
        except Exception:
            root = resource_root()
            if not root:
                raise ValueError('Start Prometheus from its SSD launcher.')
        require_dispatch('ollama.request')
        require_dispatch('ollama.start')
        self.progress('Starting local model runtime')
        helper = Path(root)/'Tools/wait-ollama.ps1'
        # A background runtime can inherit pipe handles from its launcher. Read
        # actual readiness instead of waiting for those handles to reach EOF.
        with tempfile.TemporaryFile() as error_log:
            process = subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(helper)],
                                       stdout=subprocess.DEVNULL, stderr=error_log,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic()+130
            client.timeout = 1
            try:
                while True:
                    if self.cancel_event.is_set():
                        raise GenerationCancelled('Request cancelled while starting the local runtime.')
                    if time.monotonic() > deadline:
                        raise RuntimeError('The local runtime did not become ready in time.')
                    require_dispatch('ollama.request')
                    try:
                        client.local_models()
                        return
                    except Exception:
                        pass
                    code = process.poll()
                    if code is not None and code != 0:
                        error_log.seek(0)
                        error = error_log.read(4000).decode(errors='replace')
                        raise RuntimeError('Local runtime startup failed: '+error[-500:])
                    self.cancel_event.wait(.2)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def submit(self, request, *, grant_owner=None, background=False):
        request = validate_job(request)
        if type(background) is not bool or background and request['mode'] not in {'check_sources', 'check_updates'}:
            raise ValueError('Only scheduled source and component checks can be background jobs.')
        from .application_policy import require_dispatch
        require_dispatch('ui.'+request['mode'])
        with self.lock:
            if self.current is not None or self.closing:
                raise RuntimeError('Finish the current request before starting another.')
            job_id = secrets.token_hex(12)
            if len(self.jobs) >= 12:
                del self.jobs[next(iter(self.jobs))]
            self.cancel_event.clear()
            self.dictation_finish.clear()
            self.dictation_last_pulse = time.monotonic()
            self.jobs[job_id] = {'id': job_id, 'state': 'running', 'stage': 'Starting request', 'partial': '', 'background': background,
                                 'action': request['mode'],
                                 'cancellable': request['mode'] in {'chat', 'coding', 'ask', 'vision', 'reach', 'document_import', 'code_tests', 'model_benchmark', 'dictate', 'check_updates'}}
            if request['mode'] == 'dictate':
                self.jobs[job_id].update(recording=False, level=0., elapsed=0.)
            self.current = job_id
            self.state = 'listening' if request['mode'] == 'dictate' else 'thinking'
        self.executor.submit(self._work, job_id, request, grant_owner)
        return {'id': job_id, 'state': 'running'}

    def _work(self, job_id, request, grant_owner=None):
        start = time.monotonic()
        with RequestTiming(started=start, clock=time.monotonic,
                           cancelled_exceptions=(GenerationCancelled,)) as timing:
            try:
                # Only the single worker owns the reference cache and its read locks.
                if self.cache_context is None:
                    with stage('reference_cache'):
                        self.cache_context = reference_session()
                        self.cache_context.__enter__()
                if request['mode'] == 'core':
                    if grant_owner is None:
                        raise ValueError('A current authenticated owner is required for grant execution.')
                    with grant_owner() as owner:
                        result = self.execute_core(request, owner=owner)
                else:
                    result = self.execute(request)
                result = {'state': 'done', 'result': result}
            except EmptyRecording as error:
                result = {'state': 'cancelled', 'reason': 'empty_recording', 'error': str(error)}
            except GenerationCancelled as error:
                result = {'state': 'cancelled', 'error': str(error)}
            except Exception as error:
                result = {'state': 'error', 'error': str(error)[:1600]}
            with self.lock:
                background = self.jobs.get(job_id, {}).get('background', False)
                recorded_seconds = self.jobs.get(job_id, {}).get('elapsed', 0.)
                diagnostics = timing.snapshot({'done': 'completed', 'error': 'failed',
                                               'cancelled': 'cancelled'}[result['state']])
                payload = result.get('result')
                if isinstance(payload, dict) and isinstance(payload.get('details'), dict):
                    # The existing result-details panel can show final timing.
                    # Copy after persistence so stored evidence and its metrics
                    # stay untouched, and final persistence is measured too.
                    result['result'] = {**payload, 'details': {
                        **payload['details'], 'request_timing': diagnostics}}
                self.jobs[job_id] = {'id': job_id, **result, 'action': request['mode'], 'background': background,
                                    'seconds': round(diagnostics['worker_total_seconds'], 3),
                                    'diagnostics': diagnostics}
                if request['mode'] == 'dictate':
                    self.jobs[job_id].update(recording=False, level=0., elapsed=recorded_seconds)
                self.current = None
                # End/hidden-tab/shutdown or an empty Stop ends a voice turn normally; it is
                # not a failed microphone or decoder operation.
                ended_dictation = request['mode'] == 'dictate' and result['state'] == 'cancelled'
                self.state = 'idle' if background or result['state'] == 'done' or ended_dictation else 'error'
                if background and result['state'] != 'done':
                    self.maintenance_error = result['error'][:400]

    def save(self, request, data, label, model='references'):
        if self.cancel_event.is_set() and request['mode'] in {'chat', 'coding', 'ask', 'vision', 'reach'}:
            raise GenerationCancelled('Request cancelled before saving the result.')
        reply = readable_result(data)
        with stage('history_persistence'), MemoryStore(self.memory_path) as memory:
            session = request.get('session') or memory.create_session(model)
            memory.session(session)
            memory.save_exchange(session, request['prompt'], reply, label=label, details=data)
        return {'reply': reply, 'details': data, 'label': label, 'session': session}

    def execute(self, request):
        request = validate_job(request)
        from .application_policy import require_dispatch
        require_dispatch('ui.'+request['mode'])
        if request['mode'] == 'core':
            raise ValueError('Scoped Core execution requires the authenticated job path.')
        mode, prompt = request['mode'], request['prompt']
        self.progress(mode.replace('_', ' ').capitalize())
        if mode in {'chat', 'coding'}:
            from .structured_data import explicit_json_reply
            supplied = explicit_json_reply(prompt)
            if supplied is not None:
                return self.save(request, {'reply': supplied, 'generated_answer': False,
                                          'source': 'explicitly supplied data'}, 'Supplied JSON data')
        if mode == 'chat':
            from .owner_profile import load_profile, recall
            with MemoryStore(self.memory_path) as memory:
                reply = recall(prompt, load_profile(memory))
            if reply is not None:
                return self.save(request, {'reply': reply, 'source': 'user-declared owner memory',
                                 'identity_verified': False, 'generated_answer': False}, 'Saved owner memory')
        if mode in {'chat', 'coding'}:
            from .grounding import device_presence_question, DEVICE_UNCERTAINTY
            if device_presence_question(prompt):
                return self.save(request, {'reply': DEVICE_UNCERTAINTY, 'generated_answer': False,
                                          'device_presence_verified': False}, 'Device presence – not verified')
            from .shared_readonly import grounded_readonly
            with MemoryStore(self.memory_path) as memory:
                grounded = grounded_readonly(memory, prompt)
            if grounded is not None:
                core = grounded.core.as_dict()
                data = {'reply': grounded.core.reply or
                        'The registered local lookup did not complete. Its evidence records the denial or error.',
                        'generated_answer': False, 'grounding': {'route': grounded.route,
                        'evidence': core['evidence'], 'evaluation': core['evaluation']}}
                label = {'exact-stored-subject': 'Stored local evidence',
                         'registered-definition-search': 'Registered project evidence',
                         'registered-host-summary': 'Measured computer status'}[grounded.route]
                return self.save(request, data, label)
        if mode == 'capabilities':
            from .capabilities import catalog
            data = catalog(self)
            return {'reply': '\n\n'.join(row['title']+'\n'+row['conditions'] for row in data['capabilities']),
                    'details': data, 'label': 'Capabilities and requirements'}
        if mode == 'ask':
            return self.answer_with_evidence(request)
        if mode == 'vision':
            from .vision import describe_upload
            from .owner_profile import load_profile, context as owner_context
            with MemoryStore(self.memory_path) as memory:
                profile_context = owner_context(load_profile(memory))
            self.ensure_runtime()
            data = describe_upload(request['content'], prompt, on_token=self.append_partial,
                                   on_stage=self.progress, cancel_event=self.cancel_event, owner_context=profile_context)
            return self.save({k: v for k, v in request.items() if k != 'content'}, data,
                             'Local image answer · '+data['model'], model=data['model'])
        if mode in ui_workspace.MODES:
            return ui_workspace.execute(self, request)
        if mode == 'home':
            from .smart_home import handle_command
            result = handle_command(prompt, self.memory_path.parent/'SmartHome',
                                    control_key=request.get('home_key', ''))
            return self.save({key: value for key, value in request.items() if key != 'home_key'},
                             result, 'Smart home · configured devices')
        if mode == 'models':
            hardware = detect_hardware()
            names = installed_models()
            evidence = self.model_evidence.summary()
            rows = []
            for name in sorted(names):
                roles = [role for role, choices in ROLE_MODELS.items() if name in choices]
                fits = any(choose_helper({name}, hardware, role) is not None for role in roles)
                if 'embedding' in name:
                    roles = ['embedding / retrieval (Ollama clients)']
                rows.append({'model': name, 'roles': roles or ['Experimental / manual use'],
                             'fits_chat_now': fits, 'installed': True,
                             'inference_tested': any(r['model']==name for r in evidence['benchmarks'])})
            lines = [f"{row['model']} — {', '.join(row['roles'])} — {'fits now' if row['fits_chat_now'] else 'not selected for chat now'}" for row in rows]
            return {'reply': 'Automatic helper selection is on.\n\n'+'\n'.join(lines), 'details': {'models': rows,
                    'hardware': asdict(hardware), 'role_catalog': ROLE_MODELS,
                    'benchmark_evidence': evidence,
                    'note': 'Installed files and memory estimates do not certify answer quality. Embedding models do not generate chat answers.'},
                    'label': 'Your local helpers · automatic selection'}
        if mode == 'dictate':
            from .voice import capture_prompt_continuous
            job_id = self.current
            def voice_started():
                with self.lock:
                    if self.current == job_id:
                        self.jobs[job_id]['recording'] = True
            def voice_level(level, elapsed):
                with self.lock:
                    if self.current == job_id:
                        self.jobs[job_id].update(level=level, elapsed=elapsed)
            def voice_stage(stage):
                self.state = 'thinking' if stage.startswith('Microphone off') else 'listening'
                self.progress(stage)
                if stage.startswith('Microphone off'):
                    with self.lock:
                        if self.current == job_id:
                            self.jobs[job_id].update(recording=False, level=0.)
            try:
                return {'transcript': capture_prompt_continuous(
                    cancel_event=self.cancel_event, finish_event=self.dictation_finish,
                    on_stage=voice_stage, on_started=voice_started, on_level=voice_level,
                    lease_alive=lambda: self.dictation_alive(job_id)),
                    'label': 'Local dictation · review before sending'}
            finally:
                with self.lock:
                    if self.current == job_id:
                        self.jobs[job_id].update(recording=False, level=0.)
        if mode == 'tools':
            from .coding import coding_status
            from .hf_resources import describe as curated_huggingface
            from .security_hardware_catalog import hardware_sources
            tool_details = coding_status()
            tool_details['huggingface_catalog'] = curated_huggingface(limit=8)
            tool_details['security_hardware_catalog'] = hardware_sources(limit=5)
            return {'reply': 'Local coding uses the installed coding model when memory permits. '
                    'For a full project agent, close this interface to release the SSD session, then open '
                    'START-PROMETHEUS-CODING.cmd or START-PROMETHEUS-EXTRA-CODING.cmd on the drive. '
                    'Provider tools use your configured accounts. Language selection guides the model; '
                    'it does not install a compiler or guarantee correct code. '
                    'Curated Hugging Face models and security hardware sources are available for read-only review.',
                    'details': tool_details,
                    'label': 'Installed coding tools'}
        if mode == 'calculate':
            from .exact_math import calculate
            return self.save(request, calculate(prompt), 'Exact calculation · no model')
        if mode == 'project':
            from .documents import search_documents
            if not request.get('project'):
                raise ValueError('Enter the project directory you want to search.')
            return self.save(request, search_documents(Path(request['project']), prompt, include_code=True), 'Project file evidence · no execution')
        if mode == 'reach':
            from .public_reach import read_public
            data = read_public(request['reach_action'], prompt, online=True, cancel_event=self.cancel_event)
            return self.save(request, data, 'Public reach · retrieved source evidence')
        if mode == 'research':
            from .public_resources import public_search
            data = public_search(prompt, provider=request.get('provider', 'crossref'), online=True)
            return self.save(request, data, 'Public source search · online')
        if mode == 'library':
            return self.save(request, reference_query(prompt, request.get('profile', 'public')), 'Offline source evidence')
        from .exact_math import direct_expression, calculate
        expression = direct_expression(prompt)
        if expression is not None:
            return self.save(request, calculate(expression), 'Automatic helper · exact calculation')
        from .compact_chat import candidates, generate, ModelMemoryPressure
        client = timed('runtime_client_setup', OllamaClient, timeout=.5)
        hardware = timed('hardware', detect_hardware)
        resident = timed('resident_memory', client.resident_memory_gib)
        names = timed('model_inventory', installed_models)
        role, profiles = timed('eligibility', candidates, prompt, names, hardware, self.model_evidence,
                               task='coding' if mode == 'coding' else 'general', resident=resident)
        profile = profiles[0] if profiles else None
        route = {'role': role, 'model': profile.model if profile else None, 'automatic': True}
        if profile is None:
            # Deterministic useful work remains possible even when generation cannot fit.
            expression = re.sub(r'^(?:what is|calculate|compute)\s+', '', prompt, flags=re.I).rstrip(' ?')
            if re.fullmatch(r'[\d\s.+*/()%eE-]+', expression):
                from .exact_math import calculate
                return self.save(request, calculate(expression), 'Low-memory calculation · no model')
            data = reference_query(prompt, 'engineering' if mode == 'coding' else choose_reference(prompt))
            paused = [r for r in self.model_evidence.summary()['paused_roles'] if r['role'] == role]
            data['memory_note'] = ('No helper meets both the available-memory and recent task-check requirements. '
                'These are source excerpts, not a generated answer.' if paused else
                'A language model is not loaded. These are source excerpts, not a generated answer.')
            data['paused_helpers'] = paused
            data['routing'] = route
            return self.save(request, data, 'Source evidence · helper quality and memory' if paused else 'Low-memory reference mode · no model')
        timed('runtime_readiness', self.ensure_runtime)
        names = timed('model_inventory', installed_models)
        hardware = timed('hardware', detect_hardware)
        resident = timed('resident_memory', client.resident_memory_gib)
        role, profiles = timed('eligibility', candidates, prompt, names, hardware, self.model_evidence,
                               task='coding' if mode=='coding' else 'general', resident=resident)
        if not profiles:
            data = reference_query(prompt, 'engineering' if mode=='coding' else choose_reference(prompt))
            data['memory_note'] = 'Available memory changed during startup. Source excerpts remain available; no model answer was generated.'
            return self.save(request, data, 'Memory pressure · source evidence')
        try:
            return generate(request, self.memory_path, profiles, role, self.model_evidence,
                            on_token=self.append_partial, on_stage=self.progress, cancel_event=self.cancel_event)
        except ModelMemoryPressure as error:
            self.progress('Memory pressure · switching to source evidence', reset=True)
            data = reference_query(prompt, 'engineering' if mode=='coding' else choose_reference(prompt))
            data['memory_note'] = str(error)+' These are source excerpts, not a generated answer.'
            return self.save(request, data, 'Automatic memory recovery · source evidence')

    def execute_core(self, request, *, owner):
        from .application_policy import require_dispatch
        from .builtin_tools import build_builtin_registry
        request = validate_job(request)
        require_dispatch('ui.core')
        with MemoryStore(self.memory_path) as memory:
            spec = build_builtin_registry(memory).get(request['worker'], request['tool'])
            result = self.grants.run(request['grant_id'], request['invocation_id'], spec,
                                     request['arguments'], owner=owner)
        return {'reply': str(result), 'label': 'Exact scoped Core action',
                'details': {'capability': spec.worker+'/'+spec.name, 'generated_answer': False}}

    def answer_with_evidence(self, request):
        from .capabilities import retrieve, excerpt_reply
        from .compact_chat import candidates, generate, EvidenceContextError
        from .ollama import LocalModelError
        self.progress('Searching the selected source')
        data = timed('retrieval', retrieve, self, request)
        def excerpts(reason):
            return self.save(request, {**data, 'reply': excerpt_reply(data, reason), 'reason': reason},
                             'Selected source evidence · no generated answer')
        if not data['sources']:
            return excerpts('No matching source evidence.')
        client = timed('runtime_client_setup', OllamaClient, timeout=.5)
        def fitting():
            names = timed('model_inventory', installed_models)
            hardware = timed('hardware', detect_hardware)
            resident = timed('resident_memory', client.resident_memory_gib)
            return timed('eligibility', candidates, request['prompt'], names, hardware, self.model_evidence,
                         task='coding' if data['source'] == 'project' else 'general', resident=resident)
        role, profiles = fitting()
        if not profiles:
            return excerpts('No eligible local helper fits available memory. These are source excerpts.')
        try:
            timed('runtime_readiness', self.ensure_runtime)
            role, profiles = fitting()
            if not profiles:
                return excerpts('Available memory changed. These are source excerpts.')
            return generate(request, self.memory_path, profiles, role, self.model_evidence,
                            on_token=self.append_partial, on_stage=self.progress, cancel_event=self.cancel_event,
                            source_evidence=data['sources'])
        except GenerationCancelled:
            raise
        except (LocalModelError, EvidenceContextError) as error:
            self.progress('Showing source excerpts', reset=True)
            return excerpts(str(error)+' A generated answer is unavailable; these are source excerpts.')

    def close(self):
        self.closing = True
        self.stop_dictation()
        # Let an owned speech subprocess observe closing and remove its temporary PCM.
        with self.speech_lock:
            pass
        self.remote.close()
        def release():
            if self.cache_context is not None:
                self.cache_context.__exit__(None, None, None)
                self.cache_context = None
        self.executor.submit(release).result()
        self.executor.shutdown(wait=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        from .owner_access import OwnerAccess
        self.owner_origin = f'http://localhost:{self.server_port}'
        self.owner_access = OwnerAccess(app.memory_path.parent/'OwnerAccess', self.owner_origin)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, *args):
        pass  # Prompts and tokens never go into access logs.

    def respond(self, status, data, kind='application/json; charset=utf-8'):
        try:
            self._respond(status, data, kind)
        except (ConnectionError, TimeoutError):
            pass  # A tab may close/reload while response headers are being sent.

    def _respond(self, status, data, kind):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header('Permissions-Policy', BROWSER_PERMISSIONS_POLICY)
        if getattr(self, 'owner_cookie', None) is not None:
            value = self.owner_cookie
            self.send_header('Set-Cookie', 'prometheus_owner='+value+'; HttpOnly; SameSite=Strict; Path=/; Max-Age='+('1800' if value else '0'))
        if getattr(self, 'redirect_to_owner', False):
            self.send_header('Location', self.server.owner_origin+'/')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, *, token=False, post=False):
        origin = self.server.owner_origin if self.headers.get('Host') == self.server.owner_origin.removeprefix('http://') else self.server.origin
        if self.headers.get('Host') != origin.removeprefix('http://'):
            return False
        if self.headers.get('Sec-Fetch-Site') not in {None, 'none', 'same-origin'}:
            return False
        supplied = self.headers.get('Origin')
        if (post and supplied != origin) or supplied not in {None, origin}:
            return False
        if token and not secrets.compare_digest(self.headers.get('X-Prometheus-Token', ''), self.server.app.token):
            return False
        return True

    def owner_session(self):
        from http.cookies import SimpleCookie, CookieError
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            value = cookies.get('prometheus_owner')
            return value.value if value else None
        except CookieError:
            return None

    def owner_unlocked(self):
        return self.server.owner_access.authenticated(self.owner_session())

    def do_GET(self):
        app = self.server.app
        if not self.allowed(token=self.path.startswith('/api/') and self.path != '/api/bootstrap'):
            self.respond(403, {'error': 'Open this interface from its local address.'}); return
        access = self.server.owner_access
        if access.required and self.path == '/' and self.headers.get('Host') != self.server.owner_origin.removeprefix('http://'):
            self.redirect_to_owner = True
            self.respond(302, b''); return
        if self.path.startswith('/api/') and self.path not in {'/api/bootstrap', '/api/owner/state'} and not self.owner_unlocked():
            self.respond(401, {'error': 'Unlock Prometheus with your passkey.'}); return
        if self.path == '/' and not self.owner_unlocked():
            app.last_seen = time.monotonic()
            self.respond(200, (ASSETS/'owner-access.html').read_bytes(), 'text/html; charset=utf-8'); return
        routes = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/owner-access': ('owner-access.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/folded-core.js': ('folded-core.js', 'text/javascript; charset=utf-8'),
                  '/voice-player.js': ('voice-player.js', 'text/javascript; charset=utf-8'),
                  '/folded-core.webp': ('folded-core.webp', 'image/webp'),
                  '/adaptive.js': ('adaptive.js', 'text/javascript; charset=utf-8'),
                  '/transport.js': ('transport.js', 'text/javascript; charset=utf-8'),
                  '/workspace.js': ('workspace.js', 'text/javascript; charset=utf-8'),
                  '/remote.js': ('remote.js', 'text/javascript; charset=utf-8'),
                  '/permissions.js': ('permissions.js', 'text/javascript; charset=utf-8'),
                  '/grants.js': ('grants.js', 'text/javascript; charset=utf-8'),
                  '/owner-access.js': ('owner-access.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/approved-orb.png': ('approved-orb.png', 'image/png')}
        try:
            if self.path in routes:
                name, kind = routes[self.path]
                self.respond(200, (ASSETS / name).read_bytes(), kind)
            elif self.path == '/api/bootstrap':
                from .public_resources import provider_catalog
                self.respond(200, {'token': app.token, 'languages': LANGUAGES, 'providers': provider_catalog(),
                                  'owner_access': access.state(self.owner_session())})
            elif self.path == '/api/owner/state':
                app.last_seen = time.monotonic()
                self.respond(200, access.state(self.owner_session()))
            elif self.path == '/api/status':
                self.respond(200, app.status())
            elif self.path == '/api/capabilities':
                from .capabilities import catalog
                self.respond(200, catalog(app))
            elif self.path == '/api/permissions':
                from .permissions_inventory import snapshot
                self.respond(200, snapshot(owner=access.boundary_state(self.owner_session())))
            elif self.path == '/api/permissions/grants':
                with access.grant_owner(self.owner_session()) as owner:
                    self.respond(200, {'grants': app.grants.inspect(owner=owner)})
            elif self.path == '/api/remote/state':
                app.last_seen = time.monotonic()
                self.respond(200, app.remote.state())
            elif self.path == '/api/sessions':
                with MemoryStore(app.memory_path) as memory:
                    self.respond(200, memory.sessions()[:80])
            elif self.path.startswith('/api/history/'):
                session = self.path.rsplit('/', 1)[-1]
                if not re.fullmatch(r'[a-f0-9]{32}', session):
                    raise ValueError('Invalid conversation identifier.')
                with MemoryStore(app.memory_path) as memory:
                    self.respond(200, memory.history(session, limit=40, include_metadata=True))
            elif self.path.startswith('/api/job/'):
                with app.lock:
                    job = app.jobs.get(self.path.rsplit('/', 1)[-1])
                    self.respond(200 if job else 404, job or {'error': 'Request no longer available.'})
            else:
                self.respond(404, {'error': 'Not found.'})
        except (ValueError, OSError) as error:
            self.respond(400, {'error': str(error)[:500]})

    def do_POST(self):
        if not self.allowed(token=True, post=True):
            self.reject(403, {'error': 'This action requires your local interface session.'}); return
        if not self.path.startswith('/api/owner/') and not self.owner_unlocked():
            self.reject(401, {'error': 'Unlock Prometheus with your passkey.'}); return
        if self.headers.get('Content-Type') != 'application/json' or self.headers.get('Transfer-Encoding'):
            self.reject(415, {'error': 'Use a JSON request.'}); return
        try:
            size = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            self.reject(400, {'error': 'Use a valid Content-Length.'}); return
        try:
            if not 0 < size <= 12_000_000:
                self.reject(413, {'error': 'Request exceeds the interface limit.'}); return
            if self.path == '/api/remote/action' and size > 150000:
                self.reject(413, {'error': 'Remote request exceeds its size limit.'}); return
            if self.path.startswith('/api/owner/') and size > 20000:
                self.reject(413, {'error': 'Verification request is too large.'}); return
            body = json.loads(self.rfile.read(size))
            if self.path != '/api/remote/action' and size > 24576 and isinstance(body, dict) and body.get('mode') not in {'document_import', 'vision', 'code_preview', 'code_apply'}:
                self.respond(413, {'error': 'Request exceeds the interface limit.'}); return
            if self.path.startswith('/api/owner/'):
                self.server.app.last_seen = time.monotonic()
                if self.headers.get('Origin') != self.server.owner_origin:
                    raise ValueError('Use the localhost owner sign-in page.')
                if not isinstance(body, dict):
                    raise ValueError('Invalid verification request.')
                access = self.server.owner_access
                if self.path == '/api/owner/begin' and set(body) <= {'kind', 'setup_ticket'}:
                    if not isinstance(body.get('kind'), str) or not isinstance(body.get('setup_ticket', ''), str):
                        raise ValueError('Invalid verification request.')
                    self.respond(200, access.begin(body['kind'], self.owner_session(), body.get('setup_ticket', '')))
                elif self.path == '/api/owner/complete' and set(body) == {'request_id', 'response'} and isinstance(body['request_id'], str):
                    result, session = access.complete(body['request_id'], body['response'], self.owner_session())
                    if session:
                        self.owner_cookie = session
                    self.respond(200, result)
                elif self.path == '/api/owner/lock' and body == {}:
                    access.logout(self.owner_session())
                    self.server.app.stop_dictation()
                    self.owner_cookie = ''
                    self.respond(200, {'locked': True})
                else:
                    raise ValueError('Unknown verification action.')
            elif self.path == '/api/permissions/preview':
                from .permissions_inventory import preview_core
                self.respond(200, preview_core(body))
            elif self.path.startswith('/api/permissions/grants/'):
                if not isinstance(body, dict):
                    raise ValueError('Invalid scoped grant request.')
                from .builtin_tools import build_builtin_registry
                with self.server.owner_access.grant_owner(self.owner_session()) as owner:
                    grants = self.server.app.grants
                    if self.path.endswith('/review') and set(body) == {'worker', 'tool', 'arguments', 'seconds', 'uses'}:
                        if not isinstance(body['worker'], str) or not isinstance(body['tool'], str):
                            raise ValueError('Invalid capability.')
                        spec = build_builtin_registry(None).get(body['worker'], body['tool'])
                        result = grants.review(spec, body['arguments'], owner=owner, seconds=body['seconds'], uses=body['uses'])
                    elif self.path.endswith('/issue') and set(body) == {'review_id', 'scope_sha256'} and all(isinstance(v, str) for v in body.values()):
                        result = grants.issue(body['review_id'], body['scope_sha256'], owner=owner)
                    elif self.path.endswith('/revoke') and set(body) == {'id'} and isinstance(body['id'], str):
                        result = grants.revoke(body['id'], owner=owner)
                    else:
                        raise ValueError('Unknown scoped grant action or fields.')
                self.respond(200, result)
            elif self.path == '/api/speech':
                if not isinstance(body, dict) or set(body) != {'text'}:
                    raise ValueError('Use only answer text for speech.')
                app = self.server.app
                if app.closing or not app.speech_lock.acquire(blocking=False):
                    raise RuntimeError('Voice preparation is busy or closing.')
                try:
                    from .voice import synthesize_wav
                    import base64
                    app.last_seen = time.monotonic()
                    audio = synthesize_wav(body['text'], cancelled=lambda: app.closing or not self.owner_unlocked())
                    if not self.owner_unlocked():
                        self.respond(401, {'error': 'Unlock Prometheus with your passkey.'})
                    elif app.closing:
                        raise RuntimeError('Prometheus is closing.')
                    else:
                        self.respond(200, {'encoding': 'wav-base64', 'audio': base64.b64encode(audio).decode('ascii')})
                finally:
                    app.speech_lock.release()
            elif self.path == '/api/jobs':
                cookie = self.owner_session()
                if isinstance(body, dict) and body.get('mode') == 'core':
                    self.respond(202, self.server.app.submit(body, grant_owner=lambda: self.server.owner_access.grant_owner(cookie)))
                else:
                    self.respond(202, self.server.app.submit(body))
            elif self.path == '/api/remote/action':
                self.server.app.last_seen = time.monotonic()
                self.respond(200, self.server.app.remote.action(body))
            elif self.path == '/api/cancel' and isinstance(body, dict) and set(body) == {'id'} and isinstance(body['id'], str):
                self.respond(200, self.server.app.cancel(body['id']))
            elif self.path in {'/api/dictation/finish', '/api/dictation/pulse'}:
                if not isinstance(body, dict) or set(body) != {'id'} or not isinstance(body['id'], str):
                    raise ValueError('Use only the current dictation id.')
                self.respond(200, self.server.app.dictation_control(body['id'], finish=self.path.endswith('/finish')))
            elif self.path == '/api/close' and body == {}:
                self.server.app.closing = True
                self.server.app.stop_dictation()
                self.respond(200, {'closing': True, 'busy': self.server.app.current is not None})
            else:
                self.respond(404, {'error': 'Unknown action.'})
        except (ValueError, TypeError) as error:
            self.respond(400, {'error': str(error)[:500]})
        except RuntimeError as error:
            self.respond(409, {'error': str(error)})

    def reject(self, status, data):
        """Send the refusal before a bounded, protocol-agnostic lingering close.

        On Windows, closing with a body still arriving can reset the connection
        and discard the error response. Reject first, finish the response side,
        then discard at most 24 KiB for at most 250 ms. Never interpret untrusted
        Content-Length/chunk framing or dispatch any of these discarded bytes.
        """
        self.respond(status, data)
        self.close_connection = True
        timeout = self.connection.gettimeout()
        try:
            self.connection.shutdown(socket.SHUT_WR)
            remaining = 24576
            deadline = time.monotonic()+.25
            while remaining and time.monotonic() < deadline:
                self.connection.settimeout(max(.001, deadline-time.monotonic()))
                chunk = self.rfile.read1(remaining)
                if not chunk:
                    break
                remaining -= len(chunk)
        except (OSError, TimeoutError):
            pass
        finally:
            self.connection.settimeout(timeout)


def serve(memory_path, *, port=54555, open_browser=True, shutdown_request=None):
    if not 0 <= port <= 65535:
        raise ValueError('Invalid local interface port.')
    app = Interface(memory_path)
    try:
        server = Server(('127.0.0.1', port), app)
    except OSError:
        app.close()
        print('The interface port is occupied. Close the existing interface or choose --port NUMBER.', flush=True)
        return 1
    url = server.origin + '/'
    print('Prometheus interface: ' + url, flush=True)
    server.timeout = .5
    if open_browser:
        webbrowser.open(url)
    try:
        while True:
            server.handle_request()
            app.idle_maintenance()
            if shutdown_request and Path(shutdown_request).exists():
                app.closing = True
            if time.monotonic() - app.last_seen > 300:
                app.closing = True
            if app.closing:
                app.stop_dictation()
            if app.closing and app.current is None:
                break
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
    return 0
