"""Bounded component discovery and licensed text-document updates.

The signed catalog is policy, publisher responses are untrusted data. Executable
packages, models and runtimes are never downloaded, executed or promoted here.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote, urlsplit
import uuid

from . import public_http

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
MAX_RESPONSE = 5_000_000
MAX_DOCUMENT = 2_000_000
INTERVAL = 86400
BACKOFF = 21600
LEASE = 300
DISCLOSURE = ('Checks send only registered public component names to their publishers while Prometheus is open. '
              'Version discovery is not installation or model qualification. Only explicitly approved text documents '
              'can be imported automatically; prior originals and citations are retained. No downloaded code runs.')


class UpdateCancelled(Exception):
    pass


def _cancel(event):
    if event is not None and event.is_set():
        raise UpdateCancelled('Component update cancelled; incomplete stages were not activated.')


def _plain(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Linked component-update paths are refused.')
    return path


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def _policy_digest(entry):
    # Display copy can evolve without discarding installed-version evidence.
    # Any actual provider, baseline or import-policy change remains distinct.
    display = {'label', 'reason', 'scope', 'documentation', 'prepared_on'}
    return _hash(_json({key: value for key, value in entry.items() if key not in display}))


def _text(value, limit=200):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid component catalog text.')
    return value


def _path(value):
    value = _text(value, 400)
    if '\\' in value or ':' in value or value.startswith('/') or any(
            p in ('', '.', '..') or p.endswith(('.', ' ')) for p in value.split('/')):
        raise ValueError('Unsafe publisher document path.')
    return value


def _catalog(catalog):
    if (not isinstance(catalog, dict) or catalog.get('schema_version') != 1
            or type(catalog.get('auto_check')) is not bool or not isinstance(catalog.get('entries'), list)
            or len(catalog['entries']) > 256):
        raise ValueError('Invalid signed component catalog.')
    rows = {}
    for original in catalog['entries']:
        if not isinstance(original, dict):
            raise ValueError('Invalid component entry.')
        row = dict(original)
        identity = _text(row.get('id'), 80)
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', identity) or identity in rows:
            raise ValueError('Invalid or duplicate component identity.')
        for key in ('label', 'category'):
            _text(row.get(key))
        if row.get('installed_revision') != '':
            _text(row.get('installed_revision'))
        provider = row.get('provider')
        if provider not in {'pypi', 'github_release', 'github_commit', 'ollama_manifest', 'pinned'}:
            raise ValueError('Unsupported update provider.')
        url = row.get('source_url')
        if provider == 'pinned' and url == '':
            rows[identity] = row
            continue
        url = _text(url, 2000)
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or '\\' in url or not parsed.hostname):
                raise ValueError('Invalid registered publisher URL.')
        except ValueError as error:
            raise ValueError('Invalid registered publisher URL.') from error
        if provider == 'pypi':
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', row.get('package', '')):
                raise ValueError('Invalid PyPI package.')
            if parsed.hostname != 'pypi.org' or parsed.path.strip('/') not in {
                    'project/'+row['package'], 'pypi/'+row['package']+'/json'}:
                raise ValueError('PyPI source does not match its registered package.')
        elif provider.startswith('github_'):
            if (not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}', row.get('repo', ''))
                    or any(part in {'.', '..'} for part in row['repo'].split('/'))):
                raise ValueError('Invalid GitHub repository.')
            if parsed.hostname not in {'github.com', 'raw.githubusercontent.com'} or not (
                    parsed.path.strip('/') == row['repo'] or parsed.path.lstrip('/').startswith(row['repo']+'/')):
                raise ValueError('GitHub source does not match its registered repository.')
            if provider == 'github_commit':
                _path(row.get('ref', 'HEAD'))
                if 'path' in row:
                    _path(row['path'])
                    _path(row.get('license_path'))
                    if Path(row['path']).suffix.lower() not in {'.md', '.txt', '.rst'} and row['path'] != row['license_path']:
                        raise ValueError('Only Markdown and plain text documents can be staged.')
                    if not re.fullmatch(r'[a-f0-9]{64}', row.get('license_sha256', '')):
                        raise ValueError('A pinned license checksum is required.')
                    _text(row.get('license'), 160)
                    _text(row.get('publisher'), 200)
                    name = _path(row.get('import_name'))
                    if '/' in name or Path(name).suffix.lower() not in {'.md', '.txt'}:
                        raise ValueError('Use a single Markdown or text import filename.')
                    if type(row.get('auto_import', False)) is not bool:
                        raise ValueError('auto_import must be boolean.')
                    if row.get('auto_import') and (row.get('rights') not in {'MIT', 'open-source', 'open_license', 'licensed', 'public-domain', 'public_domain'}
                            or not re.fullmatch(r'[a-f0-9]{64}', row.get('installed_sha256', ''))):
                        raise ValueError('Automatic import requires reviewed rights and the installed document checksum.')
            if 'version_prefix' in row:
                _text(row['version_prefix'], 80)
            if type(row.get('exclude_prerelease', True)) is not bool:
                raise ValueError('exclude_prerelease must be boolean.')
        elif provider == 'ollama_manifest':
            for key in ('model', 'tag'):
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', row.get(key, '')):
                    raise ValueError('Invalid registered Ollama model or tag.')
            if parsed.hostname not in {'ollama.com', 'registry.ollama.ai'}:
                raise ValueError('Invalid Ollama publisher.')
        else:
            _text(row.get('reason', 'No automatic update channel has been reviewed.'), 500)
        rows[identity] = row
    return rows


class ComponentUpdates:
    def __init__(self, root, catalog):
        self.entries = _catalog(catalog)
        self.root = _plain(root)
        self.path = self.root/'updates.sqlite3'
        self.root.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY, value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS components (
                    id TEXT PRIMARY KEY, catalog_sha TEXT NOT NULL, status TEXT NOT NULL,
                    next_due REAL NOT NULL DEFAULT 0, last_checked REAL, last_success REAL,
                    available_revision TEXT, detail TEXT NOT NULL DEFAULT '{}', error TEXT,
                    lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT,
                    applied_revision TEXT, applied_sha TEXT, applied_source TEXT, pending TEXT);
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', ('enabled', int(catalog['auto_check'])))
            if any(row['id'] not in self.entries for row in db.execute('SELECT id FROM components WHERE pending IS NOT NULL')):
                raise ValueError('Recover interrupted document updates before removing their catalog entries.')
            for identity, row in self.entries.items():
                digest = _policy_digest(row)
                old = db.execute('SELECT catalog_sha,pending,applied_revision FROM components WHERE id=?', (identity,)).fetchone()
                if old and old['catalog_sha'] != digest and old['pending']:
                    raise ValueError('An interrupted document update must be recovered before changing its catalog.')
                initial = 'check_unavailable' if row['provider'] == 'pinned' else 'installed'
                if old and old['catalog_sha'] != digest:
                    if old['applied_revision']:
                        raise ValueError('Reviewed document update policy changed; reconcile the catalog with retained applied provenance before updating.')
                    db.execute('''UPDATE components SET catalog_sha=?,status=?,next_due=0,last_checked=NULL,last_success=NULL,
                        available_revision=NULL,detail='{}',error=NULL,lease_until=0,lease_token=NULL WHERE id=?''',
                        (digest, initial, identity))
                db.execute('INSERT OR IGNORE INTO components(id,catalog_sha,status) VALUES(?,?,?)', (identity, digest, initial))

    @contextmanager
    def _db(self):
        _plain(self.path)
        for suffix in ('-journal', '-wal', '-shm'):
            _plain(str(self.path)+suffix)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Automatic update checks require a boolean setting.')
        with self._db() as db:
            db.execute('UPDATE settings SET value=? WHERE name=?', (int(enabled), 'enabled'))
        return self.status()

    def status(self):
        now = time.time()
        with self._db() as db:
            enabled = bool(db.execute('SELECT value FROM settings WHERE name=?', ('enabled',)).fetchone()[0])
            saved = {row['id']: dict(row) for row in db.execute('SELECT * FROM components')}
        rows = []
        for identity, entry in self.entries.items():
            row = saved[identity]
            details = json.loads(row['detail'])
            rows.append(dict(id=identity, label=entry['label'], category=entry['category'], provider=entry['provider'],
                source_url=entry['source_url'], installed_revision=row['applied_revision'] or entry['installed_revision'],
                available_revision=row['available_revision'], status=row['status'], details=details, error=row['error'],
                next_due=row['next_due'], last_checked=row['last_checked'], last_success=row['last_success'],
                next_due_utc=_utc(row['next_due']), last_checked_utc=_utc(row['last_checked']),
                last_success_utc=_utc(row['last_success']),
                due=enabled and entry['provider'] != 'pinned' and row['next_due'] <= now and row['lease_until'] <= now,
                pending_recovery=bool(row['pending']), reason=entry.get('reason'), automatic_apply=bool(entry.get('auto_import'))))
        return dict(enabled=enabled, entries=rows, due_count=sum(row['due'] for row in rows),
                    network_used=False, disclosure=DISCLOSURE, executable_updates_supported=False,
                    model_promotion_supported=False)

    def _lease(self, db, identity, token):
        row = db.execute('SELECT lease_token,lease_until FROM components WHERE id=?', (identity,)).fetchone()
        if row is None or row['lease_token'] != token or row['lease_until'] <= time.time():
            raise ValueError('Update lease expired; no stage may be activated.')

    def _save(self, identity, token, body, suffix):
        limit = MAX_RESPONSE if suffix == 'snapshot' else MAX_DOCUMENT+8192
        if not isinstance(body, bytes) or not 1 <= len(body) <= limit:
            raise ValueError('Update artifact exceeds its byte limit.')
        folder = _plain(self.root/identity)
        target = _plain(folder/(_hash(body)+'.'+suffix))
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._lease(db, identity, token)
            if target.exists():
                if not target.is_file() or target.stat().st_size != len(body) or _hash(target.read_bytes()) != _hash(body):
                    raise ValueError('Retained update artifact failed its checksum; it was not replaced.')
                return target.relative_to(self.root).as_posix()
            size = count = 0
            for path in self.root.rglob('*'):
                _plain(path)
                count += 1
                if count > MAX_FILES:
                    raise ValueError('Update staging file limit reached; retained revisions were preserved.')
                if path.is_file():
                    size += path.stat().st_size
            if size+len(body) > MAX_BYTES:
                raise ValueError('Update staging storage limit reached; retained revisions were preserved.')
            folder.mkdir(exist_ok=True)
            temporary = _plain(folder/('.'+uuid.uuid4().hex+'.partial'))
            try:
                with temporary.open('xb') as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                if os.name == 'nt':
                    temporary.rename(target)
                else:
                    os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return target.relative_to(self.root).as_posix()

    def _fetch(self, url, event, *, limit=MAX_RESPONSE, accept='application/json'):
        _cancel(event)
        body = public_http.fetch_public_bytes(url, {urlsplit(url).hostname}, timeout=8, max_bytes=limit,
                                             accept=accept, cancel_event=event, overall_timeout=8)
        _cancel(event)
        if not isinstance(body, bytes) or not 1 <= len(body) <= limit:
            raise ValueError('Publisher response exceeded its byte limit.')
        return body

    def _discover(self, entry, event):
        provider = entry['provider']
        if provider == 'pypi':
            url = 'https://pypi.org/pypi/'+quote(entry['package'], safe='')+'/json'
        elif provider == 'github_release':
            endpoint = '/tags?per_page=100' if entry.get('version_prefix') else '/releases/latest'
            url = 'https://api.github.com/repos/'+entry['repo']+endpoint
        elif provider == 'github_commit':
            url = 'https://api.github.com/repos/'+entry['repo']+'/commits/'+quote(entry.get('ref', 'HEAD'), safe='')
        else:
            url = 'https://registry.ollama.ai/v2/library/'+entry['model']+'/manifests/'+entry['tag']
        accept = ('application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'
                  if provider == 'ollama_manifest' else 'application/json')
        body = self._fetch(url, event, accept=accept, limit=MAX_RESPONSE if provider == 'pypi' else 2_000_000)
        payload = json.loads(body)
        if provider == 'pypi':
            revision = payload.get('info', {}).get('version')
        elif provider == 'github_release':
            if entry.get('version_prefix'):
                if not isinstance(payload, list):
                    raise ValueError('Invalid publisher tag list.')
                candidates = [row.get('name') for row in payload if isinstance(row, dict)
                              and isinstance(row.get('name'), str) and row['name'].startswith(entry['version_prefix'])]
                if entry.get('exclude_prerelease', True):
                    candidates = [tag for tag in candidates if re.fullmatch(r'v?\d+(?:\.\d+){1,3}', tag)]
                if not candidates:
                    raise ValueError('No stable matching version in the bounded publisher tag page.')
                revision = max(candidates, key=lambda value: tuple(int(n) for n in re.findall(r'\d+', value)))
            else:
                if not isinstance(payload, dict) or payload.get('draft') or (entry.get('exclude_prerelease', True) and payload.get('prerelease')):
                    raise ValueError('Publisher release is not an eligible stable release.')
                revision = payload.get('tag_name')
        elif provider == 'github_commit':
            revision = payload.get('sha') if isinstance(payload, dict) else None
            if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}', revision):
                raise ValueError('Publisher did not return an immutable commit.')
        else:
            if (not isinstance(payload, dict) or payload.get('schemaVersion') != 2
                    or not isinstance(payload.get('layers'), list) or not 1 <= len(payload['layers']) <= 128):
                raise ValueError('Invalid Ollama manifest metadata.')
            for layer in [payload.get('config'), *payload['layers']]:
                if (not isinstance(layer, dict) or not isinstance(layer.get('digest'), str)
                        or not re.fullmatch(r'sha256:[a-f0-9]{64}', layer['digest'])
                        or type(layer.get('size')) is not int or not 0 < layer['size'] <= 100*1024**3):
                    raise ValueError('Invalid Ollama blob reference; no weights were downloaded.')
            revision = 'sha256:'+_hash(body)
        return _text(revision), body, url

    def run_due_checks(self, max_checks=1, force=False, cancel_event=None, library=None):
        if type(max_checks) is not int or not 1 <= max_checks <= 3 or type(force) is not bool:
            raise ValueError('Run one to three bounded component checks.')
        checks, seen = [], set()
        for _ in range(max_checks):
            if cancel_event is not None and cancel_event.is_set():
                break
            stamp, token = time.time(), uuid.uuid4().hex
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                enabled = bool(db.execute('SELECT value FROM settings WHERE name=?', ('enabled',)).fetchone()[0])
                if not enabled and not force:
                    break
                candidates = [row for row in db.execute('SELECT * FROM components ORDER BY next_due,id')
                    if row['id'] in self.entries and row['id'] not in seen and self.entries[row['id']]['provider'] != 'pinned'
                    and row['lease_until'] <= stamp and (force or row['next_due'] <= stamp)]
                if not candidates:
                    break
                row = dict(candidates[0])
                db.execute('UPDATE components SET lease_until=?,lease_token=? WHERE id=?', (stamp+LEASE, token, row['id']))
            identity = row['id']
            seen.add(identity)
            entry = self.entries[identity]
            check = dict(id=identity, network_used=False)
            try:
                if row['pending']:
                    self._recover(entry, token, json.loads(row['pending']), library)
                _cancel(cancel_event)
                check['network_used'] = True
                revision, body, url = self._discover(entry, cancel_event)
                _cancel(cancel_event)
                snapshot = self._save(identity, token, body, 'snapshot')
                installed = row['applied_revision'] or entry['installed_revision']
                same = revision.removeprefix('v') == installed.removeprefix('v')
                older = False
                if entry['provider'] in {'pypi', 'github_release'} and all(
                        re.fullmatch(r'v?\d+(?:\.\d+){1,3}', value) for value in (installed, revision)):
                    available_parts = tuple(map(int, revision.removeprefix('v').split('.')))
                    installed_parts = tuple(map(int, installed.removeprefix('v').split('.')))
                    width = max(len(available_parts), len(installed_parts))
                    available_parts += (0,)*(width-len(available_parts))
                    installed_parts += (0,)*(width-len(installed_parts))
                    older, same = available_parts < installed_parts, available_parts == installed_parts
                state = ('upstream_changed' if row['available_revision'] and revision != row['available_revision'] else 'baseline_recorded') if not installed else (
                    'no_update' if same or older else 'update_available')
                details = dict(metadata_sha256=_hash(body), metadata_snapshot=snapshot, checked_url=url,
                               executable_downloaded=False, model_promoted=False,
                               metadata_baseline_only=not bool(installed), publisher_revision_is_older=older)
                if older:
                    details['reason'] = 'The publisher version is older than the installed numeric version; no downgrade is proposed.'
                elif not installed:
                    details['reason'] = 'Observed upstream metadata only; the original summary has not been updated or replaced.'
                if not same and entry['provider'] == 'github_commit' and entry.get('path'):
                    state, document = self._document(entry, row, token, revision, cancel_event, library)
                    details.update(document)
                check.update(status=state, available_revision=revision, details=details)
                with self._db() as db:
                    self._lease(db, identity, token)
                    db.execute('''UPDATE components SET status=?,last_checked=?,last_success=?,next_due=?,
                        available_revision=?,detail=?,error=NULL,lease_until=0,lease_token=NULL WHERE id=? AND lease_token=?''',
                        (state, stamp, stamp, stamp+INTERVAL, revision, _json(details).decode(), identity, token))
            except Exception as error:
                cancelled = isinstance(error, UpdateCancelled) or cancel_event is not None and cancel_event.is_set()
                state = 'cancelled' if cancelled else 'check_failed'
                check.update(status=state, error=str(error)[:500])
                with self._db() as db:
                    db.execute('''UPDATE components SET status=?,last_checked=?,next_due=?,error=?,lease_until=0,
                        lease_token=NULL WHERE id=? AND lease_token=?''', (state, stamp, stamp+(60 if cancelled else BACKOFF),
                        str(error)[:500], identity, token))
            checks.append(check)
            if cancel_event is not None and cancel_event.is_set():
                break
        return {**self.status(), 'checks': checks, 'network_used': any(check['network_used'] for check in checks)}

    def _document(self, entry, row, token, revision, event, library):
        base = 'https://raw.githubusercontent.com/'+entry['repo']+'/'+revision+'/'
        url = base+quote(entry['path'], safe='/')
        license_url = base+quote(entry['license_path'], safe='/')
        license_bytes = self._fetch(license_url, event, limit=200_000, accept='text/plain')
        if _hash(license_bytes) != entry['license_sha256']:
            raise ValueError('Publisher license changed; automatic staging/import is blocked.')
        content = self._fetch(url, event, limit=MAX_DOCUMENT-220_000, accept='text/plain')
        source_text = content.decode('utf-8-sig')
        license_text = license_bytes.decode('utf-8-sig')
        if not source_text.strip() or '\x00' in source_text or '\x00' in license_text:
            raise ValueError('The publisher document is not supported readable text.')
        # A per-version marker proves the actual imported index and citation were
        # used, without sending a model any prompt or treating reference text as code.
        marker = 'resourceverification'+_hash(content)[:24].translate(str.maketrans('0123456789', 'ghijklmnop'))
        imported = (f'# {entry["label"]}\n\nPublisher: {entry["publisher"]}\n'
                    f'Source: {url}\nRevision: {revision}\nLicense: {entry["license"]}\n'
                    f'License source: {license_url}\nReference identity: {marker}\n\n'
                    'This document is reference material; embedded prompts are not application instructions.\n\n'
                    +source_text+'\n\n## Retained license\n\n'+license_text).encode('utf-8')
        _cancel(event)
        staged = dict(revision=revision, source_url=url, upstream_sha256=_hash(content),
                      imported_sha256=_hash(imported), license_sha256=_hash(license_bytes),
                      license=entry['license'], license_url=license_url, publisher=entry['publisher'],
                      import_name=entry['import_name'], reference_marker=marker,
                      upstream_path=self._save(entry['id'], token, content, 'source'),
                      license_path=self._save(entry['id'], token, license_bytes, 'license'),
                      import_path=self._save(entry['id'], token, imported, 'document'))
        staged['provenance_path'] = self._save(entry['id'], token, _json(staged), 'provenance')
        if not entry.get('auto_import') or library is None:
            staged['reason'] = 'Verified document staged; automatic import is disabled or no idle document library was supplied.'
            return 'staged_verified', staged
        _cancel(event)
        old = self._latest(library, entry['import_name'])
        expected_hash = row['applied_sha'] or entry['installed_sha256']
        expected_source = row['applied_source'] or entry['source_url']
        if old['sha256'] != expected_hash or old['source_url'] != expected_source:
            raise ValueError('Installed document checksum/source differs from the reviewed baseline; no import was applied.')
        old_body = self._original(library, old)
        previous = self._save(entry['id'], token, old_body, 'previous')
        pending = dict(old=old, previous_path=previous, new_sha256=_hash(imported), new_source=url,
                       new_source_date=None, new_original_filename=entry['import_name'])
        # Persist the exact prior version before the supported library mutation.
        # A killed process leaves this journal for recovery on its next idle run.
        with self._db() as db:
            self._lease(db, entry['id'], token)
            db.execute('UPDATE components SET pending=? WHERE id=?', (_json(pending).decode(), entry['id']))
        try:
            _cancel(event)
            imported_record = library.import_bytes(entry['import_name'], imported, source_url=url, source_date=None,
                                                  document_id=old['document_id'], expected_version_id=old['version_id'])
            pending['new_version_id'] = imported_record['version_id']
            with self._db() as db:
                self._lease(db, entry['id'], token)
                db.execute('UPDATE components SET pending=? WHERE id=?', (_json(pending).decode(), entry['id']))
            _cancel(event)
            latest = self._latest(library, entry['import_name'])
            if latest['document_id'] != old['document_id'] or latest['sha256'] != _hash(imported) or latest['source_url'] != url:
                raise ValueError('Imported document record did not match its staged evidence.')
            self._original(library, latest)
            hits = library.search(marker, limit=20, semantic=False)['results']
            if not any(hit.get('document_id') == old['document_id'] and hit.get('sha256') == _hash(imported)
                       and hit.get('source_url') == url and hit.get('is_latest') is True
                       and marker in hit.get('excerpt', '') for hit in hits):
                raise ValueError('Imported keyword search/citation verification failed.')
            _cancel(event)
            if self._latest(library, entry['import_name'])['version_id'] != imported_record['version_id']:
                raise ValueError('A newer owner version appeared during verification; it was preserved.')
            with self._db() as db:
                self._lease(db, entry['id'], token)
                db.execute('''UPDATE components SET applied_revision=?,applied_sha=?,applied_source=?,pending=NULL
                              WHERE id=?''', (revision, _hash(imported), url, entry['id']))
            staged.update(document_id=old['document_id'], version_id=latest['version_id'],
                          keyword_and_citation_verified=True, previous_sha256=old['sha256'],
                          validation_scope='Actual document version, retained bytes, keyword index and source citation; no model-quality claim.')
            return 'auto_applied', staged
        except Exception as error:
            restored = self._recover(entry, token, pending, library)
            if event is not None and event.is_set():
                raise UpdateCancelled('Update cancelled; prior document content was preserved or restored.') from error
            if not restored:
                raise
            staged.update(reason=str(error)[:500], prior_document_restored=True)
            return 'rolled_back', staged

    @staticmethod
    def _latest(library, name):
        rows = [row for row in library.list_documents(limit=500)
                if row.get('title') == name and row.get('original_filename') == name and row.get('is_latest') is True]
        if len(rows) != 1:
            raise ValueError('The exact installed document is unavailable in the bounded library inventory.')
        return rows[0]

    @staticmethod
    def _original(library, record):
        relative = _path(record['original_path'].replace('\\', '/'))
        root = _plain(library.root)
        path = _plain(root/relative)
        if not path.is_relative_to(root) or not path.is_file() or not 1 <= path.stat().st_size <= MAX_DOCUMENT+8192:
            raise ValueError('Retained document is missing or exceeds the automatic update limit.')
        body = path.read_bytes()
        if _hash(body) != record['sha256']:
            raise ValueError('The retained original failed its checksum; no update was applied.')
        return body

    def _recover(self, entry, token, pending, library):
        if library is None:
            raise ValueError('Interrupted document update requires the original idle document library.')
        old = pending['old']
        previous = _plain(self.root/_path(pending['previous_path']))
        if (previous.parent != self.root/entry['id'] or not previous.is_file()
                or not 1 <= previous.stat().st_size <= MAX_DOCUMENT+8192):
            raise ValueError('Document rollback source is unavailable.')
        body = previous.read_bytes()
        if _hash(body) != old['sha256']:
            raise ValueError('Document rollback checksum failed; current library was left intact.')
        current = self._latest(library, entry['import_name'])
        if current['document_id'] != old['document_id']:
            raise ValueError('Document changed during update; automatic recovery refused.')
        restored = False
        if (current['sha256'] == pending['new_sha256'] and current['source_url'] == pending['new_source']
                and current['source_date'] == pending.get('new_source_date')
                and current['original_filename'] == pending.get('new_original_filename', entry['import_name'])
                and (pending.get('new_version_id') is None or current['version_id'] == pending['new_version_id'])):
            with self._db() as db:
                self._lease(db, entry['id'], token)
            library.import_bytes(old['original_filename'], body, source_url=old['source_url'],
                                 source_date=old['source_date'], document_id=old['document_id'],
                                 expected_version_id=current['version_id'])
            current = self._latest(library, entry['import_name'])
            restored = True
        if (current['sha256'] != old['sha256'] or current['source_url'] != old['source_url']
                or current['source_date'] != old['source_date']):
            raise ValueError('Document recovery requires review; a changed version was not overwritten.')
        self._original(library, current)
        with self._db() as db:
            self._lease(db, entry['id'], token)
            db.execute('UPDATE components SET pending=NULL WHERE id=?', (entry['id'],))
        return restored
