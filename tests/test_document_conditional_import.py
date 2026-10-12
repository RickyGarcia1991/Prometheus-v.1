"""Concurrent owner changes cannot be superseded by an updater or rollback."""
import hashlib
import json

import pytest

from prometheus_assistant import component_updates
from prometheus_assistant.document_library import DocumentLibrary


BEFORE = b'Original reviewed electronics reference.'
OWNER = b'Owner revision: preserve this current source and its corrections.'
NEW = b'Upstream revision: revised component reference.'
LICENSE = b'MIT License\nCopyright test publisher\nPermission is hereby granted.'
OLD_SHA = '1' * 40
NEW_SHA = '2' * 40
OWNER_SOURCE = 'https://example.org/owner-revision'


def digest(body):
    return hashlib.sha256(body).hexdigest()


@pytest.fixture
def document(tmp_path):
    library = DocumentLibrary(tmp_path / 'library')
    previous = library.import_bytes('guide.md', BEFORE,
                                   source_url='https://example.org/original')
    return library, previous


def test_conditional_import_accepts_only_the_current_version(document):
    library, old = document
    new = library.import_bytes('guide.md', NEW, document_id=old['document_id'],
                               expected_version_id=old['version_id'])
    assert new['version_id'] != old['version_id']
    originals = {p.name: p.read_bytes() for p in library.originals.iterdir()}
    with pytest.raises(ValueError):
        library.import_bytes('guide.md', OWNER, document_id=old['document_id'],
                             expected_version_id=old['version_id'])
    assert library.list_documents()[0]['sha256'] == digest(NEW)
    assert {p.name: p.read_bytes() for p in library.originals.iterdir()} == originals
    assert len(library.list_documents(include_history=True)) == 2


def test_second_library_caller_is_protected_by_atomic_version_check(document):
    library, old = document
    other = DocumentLibrary(library.root)
    owner = other.import_bytes('guide.md', OWNER, source_url=OWNER_SOURCE,
                               document_id=old['document_id'])
    with pytest.raises(ValueError):
        library.import_bytes('guide.md', NEW, document_id=old['document_id'],
                             expected_version_id=old['version_id'])
    current = library.list_documents()[0]
    assert current['version_id'] == owner['version_id']
    assert current['source_url'] == OWNER_SOURCE
    assert len(library.list_documents(include_history=True)) == 2


@pytest.mark.parametrize('expected', [0, -1, True, 1.5, '1'])
def test_invalid_expected_version_never_mutates_library(document, expected):
    library, old = document
    with pytest.raises(ValueError):
        library.import_bytes('guide.md', NEW, document_id=old['document_id'],
                             expected_version_id=expected)
    assert len(library.list_documents(include_history=True)) == 1
    assert library.list_documents()[0]['sha256'] == digest(BEFORE)


def test_conditional_import_requires_explicit_document_identity(document):
    library, old = document
    with pytest.raises(ValueError):
        library.import_bytes('guide.md', NEW, expected_version_id=old['version_id'])
    assert len(library.list_documents(include_history=True)) == 1


@pytest.fixture
def updater(tmp_path, monkeypatch):
    source = f'https://raw.githubusercontent.com/example/guide/{OLD_SHA}/guide.md'
    row = dict(id='guide', label='Guide', category='document', provider='github_commit',
               repo='example/guide', ref='main', installed_revision=OLD_SHA,
               source_url=source, path='guide.md', license_path='LICENSE',
               license_sha256=digest(LICENSE), import_name='guide.md',
               installed_sha256=digest(BEFORE), auto_import=True, rights='MIT',
               license='MIT', publisher='Test publisher')
    library = DocumentLibrary(tmp_path / 'library')
    old = library.import_bytes('guide.md', BEFORE, source_url=source)
    replies = {
        'https://api.github.com/repos/example/guide/commits/main': json.dumps({'sha': NEW_SHA}).encode(),
        f'https://raw.githubusercontent.com/example/guide/{NEW_SHA}/LICENSE': LICENSE,
        f'https://raw.githubusercontent.com/example/guide/{NEW_SHA}/guide.md': NEW,
    }
    monkeypatch.setattr(component_updates.public_http, 'fetch_public_bytes',
                        lambda url, hosts, **options: replies[url])
    manager = component_updates.ComponentUpdates(tmp_path / 'updates',
        dict(schema_version=1, auto_check=True, entries=[row]))
    return manager, library, old


def test_owner_import_between_updater_check_and_import_stays_current(updater, monkeypatch):
    manager, library, old = updater
    original = library.import_bytes
    owner = None

    def interleave(*args, **kwargs):
        nonlocal owner
        if owner is None:
            owner = original('guide.md', OWNER, source_url=OWNER_SOURCE,
                             document_id=old['document_id'])
        return original(*args, **kwargs)

    monkeypatch.setattr(library, 'import_bytes', interleave)
    result = manager.run_due_checks(library=library)['entries'][0]
    current = library.list_documents()[0]
    assert result['status'] == 'check_failed'
    assert current['version_id'] == owner['version_id']
    assert current['source_url'] == OWNER_SOURCE
    assert len(library.list_documents(include_history=True)) == 2
    assert (library.root / old['original_path']).read_bytes() == BEFORE


def test_owner_import_between_recovery_check_and_rollback_stays_current(updater, monkeypatch):
    manager, library, old = updater
    original = library.import_bytes
    owner = None

    def interleave(name, body, **kwargs):
        nonlocal owner
        if body == BEFORE and owner is None:
            owner = original('guide.md', OWNER, source_url=OWNER_SOURCE,
                             document_id=old['document_id'])
        return original(name, body, **kwargs)

    monkeypatch.setattr(library, 'import_bytes', interleave)
    monkeypatch.setattr(library, 'search', lambda *a, **k: {'results': []})
    result = manager.run_due_checks(library=library)['entries'][0]
    current = library.list_documents()[0]
    assert result['status'] == 'check_failed'
    assert result['pending_recovery'] is True
    assert current['version_id'] == owner['version_id']
    assert current['source_url'] == OWNER_SOURCE
    assert len(library.list_documents(include_history=True)) == 3
    assert (library.root / old['original_path']).read_bytes() == BEFORE


def test_recovery_does_not_claim_an_owner_citation_edit_as_its_own_version(updater, monkeypatch):
    manager, library, old = updater
    owner = None

    def edit_citation_then_fail_validation(*args, **kwargs):
        nonlocal owner
        current = library.list_documents()[0]
        body = (library.root / current['original_path']).read_bytes()
        owner = library.import_bytes('guide.md', body, source_url=current['source_url'],
            source_date='2026-10-10', document_id=current['document_id'])
        return {'results': []}

    monkeypatch.setattr(library, 'search', edit_citation_then_fail_validation)
    result = manager.run_due_checks(library=library)['entries'][0]
    current = library.list_documents()[0]
    assert result['status'] == 'check_failed'
    assert result['pending_recovery'] is True
    assert current['version_id'] == owner['version_id']
    assert current['source_date'] == '2026-10-10'
    assert len(library.list_documents(include_history=True)) == 3
