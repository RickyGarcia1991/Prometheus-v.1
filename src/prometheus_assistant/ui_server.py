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

ASSETS = Path(__file__).with_name('ui_assets')
LANGUAGES = ('Auto', 'Python', 'JavaScript', 'TypeScript', 'HTML / CSS', 'SQL', 'C',
             'C++', 'C#', 'Java', 'Kotlin', 'Swift', 'Go', 'Rust', 'Ruby', 'PHP',
             'R', 'Julia', 'MATLAB / Octave', 'Dart / Flutter', 'Lua', 'Perl',
             'Bash', 'PowerShell', 'F#', 'Haskell', 'Elixir', 'Erlang', 'Scala',
             'Clojure', 'OCaml', 'Zig', 'Assembly', 'Fortran', 'COBOL',
             'Solidity', 'VHDL / Verilog', 'Arduino', 'YAML / JSON / TOML',
             'Dockerfile / Terraform', 'GraphQL', 'Protobuf')
MODES = {'chat', 'coding', 'library', 'calculate', 'research', 'project', 'dictate', 'tools', 'models', 'home'}
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
    from .articles import search_articles
    return search_articles(resource_root(), query, project='auto')


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
                    excerpt = row.get('excerpt') or row.get('summary') or row.get('snippet') or row.get('description') or ''
                    location = row.get('url') or row.get('source_url') or row.get('path') or row.get('location') or ''
                    if row.get('line'):
                        location += f" line {row['line']}"
                    if row.get('location') and row['location'] != location:
                        location += ' | '+row['location']
                    if 'is_latest' in row:
                        location += ' | '+('Current saved version' if row['is_latest'] else 'Historical saved version')
                    if row.get('imported_at'):
                        location += f" | Source date: {row.get('source_date') or 'unknown'} | Imported: {row['imported_at']}"
                    lines.append(f'{title}\n{str(excerpt)[:2000]}\n{location}')
            if lines:
                return '\n\n'.join(lines)
    return json.dumps(value, ensure_ascii=False, indent=2)[:20000]


def validate_job(body):
    if not isinstance(body, dict) or set(body) - ({'mode', 'prompt', 'profile', 'provider', 'session', 'language', 'project', 'home_key'} | ui_workspace.FIELDS):
        raise ValueError('Invalid request fields.')
    mode = body.get('mode')
    if mode not in MODES:
        raise ValueError('Choose an available workspace mode.')
    if 'home_key' in body and (mode != 'home' or not isinstance(body['home_key'], str)
                               or len(body['home_key']) > 128 or '\x00' in body['home_key']):
        raise ValueError('Use a valid control key only in Smart home mode.')
    prompt = body.get('prompt', '')
    if not isinstance(prompt, str) or len(prompt) > 4000 or ('\x00' in prompt):
        raise ValueError('Use a prompt of at most 4000 characters.')
    if mode not in {'dictate', 'tools', 'models'} | ui_workspace.NO_PROMPT and not prompt.strip():
        raise ValueError('Enter a question or expression.')
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
    ui_workspace.validate(body)
    return {**body, 'prompt': prompt.strip()}


class Interface:
    def __init__(self, memory_path):
        self.memory_path = Path(memory_path)
        self.data_root = self.memory_path.parent/'Interface/Data'
        self.model_evidence = ModelEvidence(self.data_root/'Routing/evidence.sqlite3')
        self.last_backup = None
        self.cancel_event = threading.Event()
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

    def status(self):
        self.last_seen = time.monotonic()
        return {'hardware': hardware_status(), 'state': self.state,
                'busy': self.current is not None, 'closing': self.closing,
                'job': self.current, 'saved_locally': True, 'maintenance_error': self.maintenance_error}

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

    def idle_maintenance(self):
        if time.monotonic() < self.next_maintenance or self.closing:
            return
        self.next_maintenance = time.monotonic()+60
        try:
            if self.current is None and any(r.get('enabled') and r.get('due') for r in ui_workspace.schedule(self).status().get('watches', [])):
                self.submit({'mode': 'check_sources', 'prompt': ''})
        except Exception as error:
            self.maintenance_error = str(error)[:400]

    def ensure_runtime(self):
        client = OllamaClient(timeout=2)
        try:
            client.local_models()
            return
        except Exception:
            root = resource_root()
            if not root:
                raise ValueError('Start Prometheus from its SSD launcher.')
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

    def submit(self, request):
        request = validate_job(request)
        with self.lock:
            if self.current is not None or self.closing:
                raise RuntimeError('Finish the current request before starting another.')
            job_id = secrets.token_hex(12)
            if len(self.jobs) >= 12:
                del self.jobs[next(iter(self.jobs))]
            self.cancel_event.clear()
            self.jobs[job_id] = {'id': job_id, 'state': 'running', 'stage': 'Starting request', 'partial': '',
                                 'cancellable': request['mode'] in {'chat', 'coding', 'code_tests', 'model_benchmark'}}
            self.current = job_id
            self.state = 'listening' if request['mode'] == 'dictate' else 'thinking'
        self.executor.submit(self._work, job_id, request)
        return {'id': job_id, 'state': 'running'}

    def _work(self, job_id, request):
        start = time.monotonic()
        try:
            # Only the single worker owns the reference cache and its read locks.
            if self.cache_context is None:
                self.cache_context = reference_session()
                self.cache_context.__enter__()
            result = self.execute(request)
            result = {'state': 'done', 'result': result}
        except GenerationCancelled as error:
            result = {'state': 'cancelled', 'error': str(error)}
        except Exception as error:
            result = {'state': 'error', 'error': str(error)[:1600]}
        with self.lock:
            self.jobs[job_id] = {'id': job_id, **result, 'action': request['mode'], 'seconds': round(time.monotonic() - start, 3)}
            self.current = None
            self.state = 'idle' if result['state'] == 'done' else 'error'

    def save(self, request, data, label, model='references'):
        if self.cancel_event.is_set() and request['mode'] in {'chat', 'coding'}:
            raise GenerationCancelled('Request cancelled before saving the result.')
        reply = readable_result(data)
        with MemoryStore(self.memory_path) as memory:
            session = request.get('session') or memory.create_session(model)
            memory.session(session)
            memory.save_exchange(session, request['prompt'], reply)
        return {'reply': reply, 'details': data, 'label': label, 'session': session}

    def execute(self, request):
        mode, prompt = request['mode'], request['prompt']
        self.progress(mode.replace('_', ' ').capitalize())
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
            from .voice import capture_prompt
            return {'transcript': capture_prompt(), 'label': 'Local dictation · review before sending'}
        if mode == 'tools':
            from .coding import coding_status
            return {'reply': 'Local coding uses the installed coding model when memory permits. '
                    'For a full project agent, close this interface to release the SSD session, then open '
                    'START-PROMETHEUS-CODING.cmd or START-PROMETHEUS-EXTRA-CODING.cmd on the drive. '
                    'Provider tools use your configured accounts. Language selection guides the model; '
                    'it does not install a compiler or guarantee correct code.', 'details': coding_status(),
                    'label': 'Installed coding tools'}
        if mode == 'calculate':
            from .exact_math import calculate
            return self.save(request, calculate(prompt), 'Exact calculation · no model')
        if mode == 'project':
            from .documents import search_documents
            if not request.get('project'):
                raise ValueError('Enter the project directory you want to search.')
            return self.save(request, search_documents(Path(request['project']), prompt, include_code=True), 'Project file evidence · no execution')
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
        if re.search(r'\b(?:this|my|your) (?:computer|system|hardware)\b', prompt, re.I) and re.search(r'\b(?:ram|memory|cpu|operating system)\b', prompt, re.I):
            hardware = detect_hardware()
            return self.save(request, {'reply': f'RAM: {hardware.ram_gib} GiB total, {hardware.available_ram_gib} GiB available. CPU threads: {hardware.cpu_threads}. OS: {hardware.system}.',
                            'hardware': asdict(hardware)}, 'Measured computer status')
        from .compact_chat import candidates, generate, ModelMemoryPressure
        client = OllamaClient(timeout=.5)
        hardware = detect_hardware()
        resident = client.resident_memory_gib()
        role, profiles = candidates(prompt, installed_models(), hardware, self.model_evidence,
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
        self.ensure_runtime()
        role, profiles = candidates(prompt, installed_models(), detect_hardware(), self.model_evidence,
                                    task='coding' if mode=='coding' else 'general', resident=client.resident_memory_gib())
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

    def close(self):
        self.closing = True
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


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, *args):
        pass  # Prompts and tokens never go into access logs.

    def respond(self, status, data, kind='application/json; charset=utf-8'):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, *, token=False, post=False):
        origin = self.server.origin
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

    def do_GET(self):
        app = self.server.app
        if not self.allowed(token=self.path.startswith('/api/') and self.path != '/api/bootstrap'):
            self.respond(403, {'error': 'Open this interface from its local address.'}); return
        routes = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/adaptive.js': ('adaptive.js', 'text/javascript; charset=utf-8'),
                  '/transport.js': ('transport.js', 'text/javascript; charset=utf-8'),
                  '/workspace.js': ('workspace.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/approved-orb.png': ('approved-orb.png', 'image/png')}
        try:
            if self.path in routes:
                name, kind = routes[self.path]
                self.respond(200, (ASSETS / name).read_bytes(), kind)
            elif self.path == '/api/bootstrap':
                from .public_resources import provider_catalog
                self.respond(200, {'token': app.token, 'languages': LANGUAGES, 'providers': provider_catalog()})
            elif self.path == '/api/status':
                self.respond(200, app.status())
            elif self.path == '/api/sessions':
                with MemoryStore(app.memory_path) as memory:
                    self.respond(200, memory.sessions()[:80])
            elif self.path.startswith('/api/history/'):
                session = self.path.rsplit('/', 1)[-1]
                if not re.fullmatch(r'[a-f0-9]{32}', session):
                    raise ValueError('Invalid conversation identifier.')
                with MemoryStore(app.memory_path) as memory:
                    self.respond(200, memory.history(session, limit=40))
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
            self.respond(403, {'error': 'This action requires your local interface session.'}); return
        if self.headers.get('Content-Type') != 'application/json' or self.headers.get('Transfer-Encoding'):
            self.respond(415, {'error': 'Use a JSON request.'}); return
        try:
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 < size <= 12_000_000:
                self.respond(413, {'error': 'Request exceeds the interface limit.'}); return
            body = json.loads(self.rfile.read(size))
            if size > 24576 and isinstance(body, dict) and body.get('mode') not in {'document_import', 'code_preview', 'code_apply'}:
                self.respond(413, {'error': 'Request exceeds the interface limit.'}); return
            if self.path == '/api/jobs':
                self.respond(202, self.server.app.submit(body))
            elif self.path == '/api/cancel' and isinstance(body, dict) and set(body) == {'id'} and isinstance(body['id'], str):
                self.respond(200, self.server.app.cancel(body['id']))
            elif self.path == '/api/close' and body == {}:
                self.server.app.closing = True
                self.respond(200, {'closing': True, 'busy': self.server.app.current is not None})
            else:
                self.respond(404, {'error': 'Unknown action.'})
        except (ValueError, TypeError) as error:
            self.respond(400, {'error': str(error)[:500]})
        except RuntimeError as error:
            self.respond(409, {'error': str(error)})


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
            if app.closing and app.current is None:
                break
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
    return 0
