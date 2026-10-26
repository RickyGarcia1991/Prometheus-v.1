"""Pinned Browser Use public-page reader. Task authority belongs to the caller.

The renderer never receives user profiles, credentials, arbitrary JavaScript or
arbitrary browser actions. GET requests are fulfilled through public_http; an
owned rejecting proxy prevents the browser's other requests going direct.
This is a bounded reader, not a qualified model-driven Browser Use Agent.
"""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
from contextlib import contextmanager
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit

VERSION = '0.13.11'
BROWSER_VERSION = '155.0.8059.39'
BUNDLE_RELATIVE = Path('Integrations/BrowserUse/0.13.11')
MANIFEST_SHA256 = '0953bdba157ad2d8a74c8bb521cfbd4917ad219fe33bb6b7e83350b38e390096'
MAX_REQUESTS = 32
MAX_TOTAL_BYTES = 10_000_000
MAX_TEXT = 16_000
MAX_SECONDS = 60
_WIRE = 'PROMETHEUS_BROWSER:'
_SLOT = threading.Lock()
_LIMITATIONS = [
    'Public HTTPS pages only; one selected host, fresh profile, no sign-in or saved cookies.',
    'Read-only GET requests; redirects, other hosts, downloads, forms and media are blocked.',
    'Some sites need third-party resources or authentication and will not render completely.',
    'Model-driven browser tasks are not qualified; this adapter reads and extracts pages.',
]


def canonical_arguments(action, arguments):
    if action != 'read' or not isinstance(arguments, dict) or set(arguments) != {'url'}:
        raise ValueError('Browser Use supports read with exactly one url argument.')
    value = arguments['url']
    if not isinstance(value, str) or not 1 <= len(value) <= 2000 or '\\' in value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError('Use a public HTTPS URL of at most 2,000 characters.')
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').encode('idna').decode('ascii').lower()
        if (parsed.scheme != 'https' or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or parsed.fragment or not host or host.endswith('.')
                or len(host) > 253 or '.' not in host or '%' in host
                or host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid', '.example'))
                or host in {'localhost', 'metadata.google.internal'}
                or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.'))):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        try:
            socket.inet_aton(host)
        except OSError:
            pass
        else:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ValueError('Use a public HTTPS hostname without credentials, a fragment or a custom port.') from None
    return {'url': urlunsplit(('https', host, parsed.path or '/', parsed.query, ''))}


def _plain(path):
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Browser bundle links and reparse points are refused.')
    return info


def _manifest(root):
    if root is None:
        raise ValueError('Portable browser resources are not configured on this computer.')
    bundle = Path(root) / BUNDLE_RELATIVE
    for path in reversed([bundle, *bundle.parents]):
        _plain(path)
    manifest_path = bundle / 'manifest.json'
    if _plain(manifest_path).st_size > 4_000_000:
        raise ValueError('Browser Use dependency manifest exceeded its size limit.')
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError('Browser Use dependency manifest failed verification.')
    manifest = json.loads(raw)
    return bundle, manifest


def _verify_bundle(root, cancel_event=None):
    bundle, manifest = _manifest(root)
    expected = manifest['files']
    actual = set()
    for parent, dirs, files in os.walk(bundle, followlinks=False):
        for name in dirs:
            _plain(Path(parent) / name)
        for name in files:
            path = Path(parent) / name
            info = _plain(path)
            relative = path.relative_to(bundle).as_posix()
            if relative == 'manifest.json':
                continue
            actual.add(relative)
            row = expected.get(relative)
            if row is None or row['bytes'] != info.st_size:
                raise ValueError('Browser Use bundle contains missing, changed or unexpected files.')
            _cancel(cancel_event)
            with path.open('rb') as stream:
                checksum = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    _cancel(cancel_event)
                    checksum.update(block)
                digest = checksum.hexdigest()
            if digest != row['sha256']:
                raise ValueError('Browser Use dependency payload failed verification.')
    if actual != set(expected):
        raise ValueError('Browser Use dependency bundle is incomplete.')
    return bundle


def _native_chrome():
    from .linux_resources import system_executable
    system_executable('/usr/bin/node')
    return system_executable('/opt/google/chrome/chrome')


@contextmanager
def _reader_process(process):
    if sys.platform != 'linux':
        from .autonomous_promotion import _owned_test_process
        with _owned_test_process(process):
            yield
        return
    # The child starts a new session; its Chrome descendants share this group.
    try:
        yield
    finally:
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: pass
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=5)


def status(resource_root):
    result = {'provider': 'browser_use', 'version': VERSION, 'browser_version': BROWSER_VERSION,
              'installed': False, 'available': False, 'integrity': 'missing',
              'operations': ['read'], 'mode': 'public-page-reader', 'agentic_tasks_qualified': False,
              'limitations': list(_LIMITATIONS)}
    if sys.platform == 'linux':
        result.update(version='native-chromium', browser_version=None, backend='OS-managed Chromium CDP public-page reader')
        try:
            result.update(installed=True, available=bool(_native_chrome()), integrity='OS-managed runtime; application adapter')
        except (OSError, ValueError):
            result['integrity']='native-runtime-unavailable'
        return result
    try:
        bundle, _ = _manifest(resource_root)
        result['installed'] = True
        result['integrity'] = 'manifest-verified; payload checked before each execution'
        result['available'] = os.name == 'nt' and sys.version_info[:2] == (3, 12) and (bundle / 'browser/chrome-win64/chrome.exe').is_file()
    except (OSError, ValueError, KeyError):
        result['integrity'] = 'missing-or-unverified'
    return result


def _cancel(event):
    if event is not None and event.is_set():
        raise RuntimeError('Browser read cancelled.')


class _Deadline:
    def __init__(self, event):
        self.event, self.until = event, time.monotonic() + MAX_SECONDS

    def is_set(self):
        return (self.event is not None and self.event.is_set()) or time.monotonic() >= self.until

    def check(self):
        _cancel(self.event)
        if time.monotonic() >= self.until:
            raise RuntimeError('Browser read exceeded its 60-second time limit.')


def _fetch(url, host, cancel_event, max_bytes):
    from .public_http import fetch_public_bytes
    return fetch_public_bytes(url, {host}, max_bytes=max_bytes,
                              accept='text/html,application/javascript,text/css,application/json,text/plain',
                              cancel_event=cancel_event, overall_timeout=20)


def _response(message, host, limit, counts, selected_url=None):
    counts['requests'] += 1
    if counts['requests'] > MAX_REQUESTS:
        raise RuntimeError('Browser page exceeded its request limit.')
    kind = message.get('resource_type')
    try:
        target = canonical_arguments('read', {'url': message.get('url')})['url']
        if urlsplit(target).hostname != host or message.get('method') != 'GET' or kind not in {'Document', 'Script', 'Stylesheet', 'XHR', 'Fetch'}:
            raise ValueError('Request is outside the selected read scope.')
        if kind == 'Document' and selected_url is not None and target != selected_url:
            raise ValueError('Page navigation requires a separately selected read.')
        remaining = MAX_TOTAL_BYTES - counts['bytes']
        if remaining <= 0:
            raise ValueError('Browser page exceeded its download limit.')
        body = _fetch(target, host, limit, min(2_000_000, remaining))
        body.decode('utf-8')  # The reader supports UTF-8 text assets only.
        counts['bytes'] += len(body)
        if counts['bytes'] > MAX_TOTAL_BYTES:
            raise RuntimeError('Browser page exceeded its download limit.')
        mime = {'Document': 'text/html', 'Script': 'application/javascript', 'Stylesheet': 'text/css'}.get(kind, 'text/plain')
        headers = [{'name': 'Content-Type', 'value': mime + '; charset=utf-8'},
                   {'name': 'Cache-Control', 'value': 'no-store'}]
        if kind == 'Document':
            headers.append({'name': 'Content-Security-Policy', 'value': "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src data:; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; worker-src 'none'"})
        counts['fetched'] += 1
        return {'ok': True, 'body': base64.b64encode(body).decode('ascii'), 'headers': headers}
    except (ValueError, OSError, RuntimeError) as error:
        limit.check()
        counts['blocked'] += 1
        if kind == 'Document':
            raise ValueError('The selected public page could not be read within its access scope.') from error
        return {'ok': False}


def _environment(job):
    env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'SYSTEMDRIVE'}}
    # Provider keys, user HOME/profile paths, proxy settings and Python hooks are
    # deliberately not inherited by the third-party worker.
    env.update({name: str(job) for name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'BROWSER_USE_CONFIG_DIR')})
    env.update(ANONYMIZED_TELEMETRY='false', BROWSER_USE_CLOUD_SYNC='false', BROWSER_USE_DISABLE_EXTENSIONS='true',
               BROWSER_USE_LOGGING_LEVEL='error', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    return env


def _run_reader(bundle, url, job, cancel_event):
    limit = _Deadline(cancel_event)
    command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve()), '--worker', str(bundle), str(job)]
    if sys.platform == 'linux':
        command = ['/usr/bin/node', str(Path(__file__).with_name('native_browser.mjs')), str(_native_chrome()), str(job)]
    counts = {'requests': 0, 'fetched': 0, 'blocked': 0, 'bytes': 0}
    messages = queue.Queue(maxsize=128)
    with (job / 'worker.log').open('wb') as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                   cwd=job, env=_environment(job), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                   start_new_session=sys.platform == 'linux')
        def collect():
            try:
                while line := process.stdout.readline(200_001):
                    if len(line) > 200_000:
                        messages.put({'type': 'error'}); return
                    if line.startswith(_WIRE.encode()):
                        messages.put(json.loads(line[len(_WIRE):]))
                messages.put({'type': 'eof'})
            except Exception:
                messages.put({'type': 'error'})
        thread = threading.Thread(target=collect, name='Prometheus-browser-output', daemon=True)
        try:
            with _reader_process(process):
                thread.start()
                # Child cannot launch Chrome until cleanup ownership is established.
                process.stdin.write((json.dumps({'url': url}) + '\n').encode()); process.stdin.flush()
                while True:
                    limit.check()
                    try:
                        message = messages.get(timeout=.05)
                    except queue.Empty:
                        continue
                    if message.get('type') == 'fetch':
                        reply = _response(message, urlsplit(url).hostname, limit, counts, url)
                        process.stdin.write((json.dumps(reply) + '\n').encode()); process.stdin.flush()
                    elif message.get('type') == 'result':
                        result = message['result']
                        if not isinstance(result, dict) or not isinstance(result.get('text'), str) or not result['text'].strip():
                            raise ValueError('The page returned no readable text.')
                        if result.get('url') != url:
                            raise ValueError('The browser moved away from the selected page.')
                        result['text'] = result['text'][:MAX_TEXT]
                        result['title'] = str(result.get('title', ''))[:300]
                        links = []
                        for link in result.get('links', [])[:20]:
                            try:
                                safe = canonical_arguments('read', {'url': link['url']})['url']
                                if urlsplit(safe).hostname == urlsplit(url).hostname:
                                    links.append({'url': safe, 'text': str(link.get('text', ''))[:200]})
                            except (ValueError, KeyError, TypeError):
                                continue
                        result.update(url=url, links=links, requests=counts)
                        return result
                    else:
                        raise RuntimeError('The isolated Browser Use reader did not complete.')
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)
            for stream in (process.stdin, process.stdout):
                if stream: stream.close()
            if thread.ident is not None: thread.join(timeout=1)


def _require_windows_runtime():
    if os.name != 'nt' or sys.version_info[:2] != (3, 12):
        raise ValueError('Browser Use currently requires the portable Windows Python 3.12 runtime.')


def execute(action, arguments, *, data_root, resource_root, cancel_event=None):
    from .application_policy import require_dispatch
    require_dispatch('integrations.browser_use')
    args = canonical_arguments(action, arguments)
    if sys.platform == 'linux':
        _native_chrome()
    else:
        _require_windows_runtime()
    from .hardware import detect_hardware
    available = detect_hardware().available_ram_gib
    if available is None or available < 1.0:
        raise ValueError('Browser Use needs at least 1 GiB of available RAM; close an unused task and try again.')
    _cancel(cancel_event)
    if not _SLOT.acquire(blocking=False):
        raise RuntimeError('Another browser read is already running; wait or cancel it first.')
    try:
        bundle = None if sys.platform == 'linux' else _verify_bundle(resource_root, cancel_event)
        # Browser profiles and caches are machine-specific. The portable data
        # root is intentionally unused; no browser state is persisted on SSD.
        parent = Path(tempfile.gettempdir()).resolve()
        system_drive = os.environ.get('SystemDrive', 'C:')
        if sys.platform != 'linux' and parent.drive.casefold() != system_drive.casefold():
            raise ValueError('Browser Use requires a temporary directory on the host system drive.')
        for path in reversed([parent, *parent.parents]): _plain(path)
        job = Path(tempfile.mkdtemp(prefix='prometheus-browser-', dir=parent))
        try:
            result = _run_reader(bundle, args['url'], job, cancel_event)
            return {'provider': 'browser_use', 'version': 'native-chromium' if sys.platform == 'linux' else VERSION, 'action': action, 'result': result,
                    'network_used': True, 'generated_answer': False, 'agentic_tasks_qualified': False,
                    'limitations': list(_LIMITATIONS)}
        finally:
            _cleanup_job(job)
    finally:
        _SLOT.release()


def _cleanup_job(job):
    # Kill-on-close terminates descendants asynchronously on Windows. Give
    # their file handles a bounded chance to close before removing our exact
    # newly created directory; never reuse or traverse a user's profile.
    deadline = time.monotonic() + 3
    while True:
        try:
            shutil.rmtree(job)
            return
        except FileNotFoundError:
            if not job.exists(): return
            if time.monotonic() >= deadline: raise
        except PermissionError:
            if time.monotonic() >= deadline: raise
        time.sleep(.05)


def _worker(bundle, job):
    """Private child entry point; authority and remote HTTP stay in the parent."""
    import asyncio
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    sys.path.insert(0, str(bundle / 'site-packages'))
    request = json.loads(sys.stdin.readline(8192))  # Wait for parent-owned job first.
    def local_writes(event, args):
        paths = ()
        if event == 'open':
            mode, flags = args[1], args[2]
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                paths = (args[0],)
        elif event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime'}:
            paths = (args[0],)
        elif event in {'os.rename', 'os.link', 'os.symlink'}:
            paths = args[:2]
        for value in paths:
            if isinstance(value, (str, bytes, os.PathLike)) and not Path(os.fsdecode(value)).resolve().is_relative_to(job):
                raise PermissionError('Browser worker writes are limited to its fresh private job directory.')
    sys.addaudithook(local_writes)  # Python write guard; not a claim of an OS sandbox.
    class Reject(BaseHTTPRequestHandler):
        def do_CONNECT(self): self.send_error(403)
        def do_GET(self): self.send_error(403)
        def do_POST(self): self.send_error(403)
        def log_message(self, *args): pass
    proxy = ThreadingHTTPServer(('127.0.0.1', 0), Reject)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    def emit(value):
        print(_WIRE + json.dumps(value, ensure_ascii=True), flush=True)
    async def run():
        from browser_use import Browser
        profile = job / 'profile'
        profile.mkdir()
        chrome = subprocess.Popen([str(bundle / 'browser/chrome-win64/chrome.exe'), '--headless=new',
                                   '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                                   '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                                   '--user-data-dir=' + str(profile), '--disable-background-networking',
                                   '--enable-logging=stderr', '--log-file=' + str(job / 'chrome.log'),
                                   '--disable-quic', '--disable-extensions',
                                   '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                                   '--host-resolver-rules=MAP * ~NOTFOUND', '--proxy-bypass-list=<-loopback>',
                                   f'--proxy-server=http://127.0.0.1:{proxy.server_port}', 'about:blank'],
                                  stdout=sys.stderr, stderr=sys.stderr, creationflags=subprocess.CREATE_NO_WINDOW)
        active_port = profile / 'DevToolsActivePort'
        for _ in range(100):
            if active_port.is_file(): break
            if chrome.poll() is not None: raise RuntimeError('Pinned browser could not start.')
            await asyncio.sleep(.1)
        port = int(active_port.read_text().splitlines()[0])
        browser = Browser(cdp_url=f'http://127.0.0.1:{port}', headless=True, downloads_path=str(job / 'downloads'),
                          accept_downloads=False, enable_default_extensions=False, chromium_sandbox=True,
                          disable_security=False, keep_alive=False, permissions=[])
        pending = set()
        try:
            await browser.start()
            page = await browser.new_page()
            session = await page.session_id
            client = browser.cdp_client
            lock = asyncio.Lock()
            async def answer(event, sid):
                async with lock:
                    item = event['request']
                    emit({'type': 'fetch', 'url': item['url'], 'method': item['method'], 'resource_type': event.get('resourceType')})
                    response = json.loads(await asyncio.to_thread(sys.stdin.readline))
                    if response.get('ok'):
                        await client.send.Fetch.fulfillRequest({'requestId': event['requestId'], 'responseCode': 200,
                                                               'body': response['body'], 'responseHeaders': response['headers']}, session_id=sid)
                    else:
                        await client.send.Fetch.failRequest({'requestId': event['requestId'], 'errorReason': 'BlockedByClient'}, session_id=sid)
            def paused(event, sid):
                task = asyncio.create_task(answer(event, sid))
                pending.add(task); task.add_done_callback(pending.discard)
            client.register.Fetch.requestPaused(paused)
            await client.send.Network.setBypassServiceWorker({'bypass': True}, session_id=session)
            await client.send.Fetch.enable({'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]}, session_id=session)
            await page.goto(request['url'])
            for _ in range(50):
                await asyncio.sleep(.1)
                if await page.evaluate('() => document.readyState') == 'complete' and not pending:
                    await asyncio.sleep(.35)
                    if not pending: break
            value = json.loads(await page.evaluate("() => ({url:location.href, title:document.title, text:(document.body?.innerText || '').slice(0,16000), links:Array.from(document.querySelectorAll('a[href]')).slice(0,20).map(a=>({url:a.href,text:(a.innerText||'').slice(0,200)}))})"))
            await browser.kill()
            emit({'type': 'result', 'result': value})
        finally:
            for task in pending: task.cancel()
            try: await asyncio.wait_for(browser.kill(), timeout=5)
            except Exception: pass
            if chrome.poll() is None: chrome.terminate()
            await asyncio.to_thread(chrome.wait, timeout=5)
    try:
        asyncio.run(run())
    finally:
        proxy.shutdown(); proxy.server_close()


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--worker':
        raise SystemExit('Private Browser Use worker only.')
    _worker(Path(sys.argv[2]), Path(sys.argv[3]))
