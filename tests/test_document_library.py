"""Storage/import integration tests; synthetic vectors test ranking, not model quality."""
from contextlib import closing
import hashlib
import io
import math
import sqlite3
import stat
import zipfile

import pytest

from prometheus_assistant import document_library as module
from prometheus_assistant.document_library import DocumentLibrary, OllamaEmbeddingClient


def docx(text='The contract renewal date is January.', *, extra=None, xml=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', xml or (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'))
        for name, content in (extra or {}).items():
            # ZipInfo normalizes Windows slashes at construction; preserve hostile bytes.
            entry = zipfile.ZipInfo('placeholder')
            entry.filename = name
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)
    return stream.getvalue()


def pdf_bytes(text='The purchase order is approved.'):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    stream = DecodedStreamObject()
    stream.set_data(f'BT /F1 12 Tf 30 250 Td ({text}) Tj ET'.encode('ascii'))
    page[NameObject('/Contents')] = stream
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class FixtureEmbeddings:
    """Deterministic test boundary; these vectors are never shipped as model output."""
    model = 'test:model@digest-v1'
    dimension = 3

    def __init__(self):
        self.calls = []
        self.fail = False

    def identity(self):
        return self.model

    def embed(self, texts):
        if self.fail:
            raise RuntimeError('offline')
        self.calls.append(texts)
        result = []
        for text in texts:
            text = text.casefold()
            if any(term in text for term in ('automobile', 'car', 'vehicle')):
                values = [1., 0., 0.]
            elif any(term in text for term in ('cat', 'feline')):
                values = [0., 1., 0.]
            else:
                values = [0., 0., 1.]
            result.append(values[:self.dimension] if self.dimension < 3 else values + [0.] * (self.dimension - 3))
        return result


def test_import_search_reopen_retains_original_metadata_and_versions(tmp_path):
    root = tmp_path / 'library'
    library = DocumentLibrary(root)
    first = b'Contract says renewal is January 12.'
    a = library.import_bytes('contract.md', first, source_url='https://example.org/contract', source_date='2024-01-01')
    b = library.import_bytes('contract.md', b'Contract says renewal is February 20.', source_date='2025-01-01')
    assert a['document_id'] == b['document_id'] and a['version_id'] != b['version_id']
    assert a['sha256'] == hashlib.sha256(first).hexdigest()
    assert (root / a['original_path']).read_bytes() == first
    assert a['embedding_status'] == 'not_configured'
    reopened = DocumentLibrary(root)
    assert len(reopened.list_documents()) == 1
    assert len(reopened.list_documents(include_history=True)) == 2
    assert not reopened.search('January')['results']
    historical = reopened.search('January', include_history=True)['results'][0]
    assert historical['source_date'] == '2024-01-01' and not historical['is_latest']
    assert historical['source_url'] == 'https://example.org/contract'
    current = reopened.search('February')['results'][0]
    assert current['version_id'] == b['version_id'] and current['is_latest']
    assert current['retrieval'] == 'keyword'


def test_repeat_import_is_idempotent_and_metadata_changes_are_versioned(tmp_path):
    library = DocumentLibrary(tmp_path)
    first = library.import_bytes('note.txt', b'Calendar reminder')
    repeat = library.import_bytes('note.txt', b'Calendar reminder')
    assert repeat['duplicate'] and first['version_id'] == repeat['version_id']
    dated = library.import_bytes('note.txt', b'Calendar reminder', source_date='2026-01-02')
    assert not dated['duplicate'] and dated['version_id'] != first['version_id']
    assert len(list((tmp_path / 'originals').iterdir())) == 1


def test_explicit_document_version_retains_changed_filename(tmp_path):
    library = DocumentLibrary(tmp_path)
    first = library.import_bytes('old-name.txt', b'Original version')
    second = library.import_bytes('renamed.md', b'Revised version', document_id=first['document_id'])
    assert first['document_id'] == second['document_id']
    assert second['original_filename'] == 'renamed.md'
    assert {version['original_filename'] for version in library.list_documents(include_history=True)} == {'old-name.txt', 'renamed.md'}


def test_unknown_document_id_does_not_write_an_original(tmp_path):
    library = DocumentLibrary(tmp_path)
    with pytest.raises(ValueError, match='Unknown'):
        library.import_bytes('file.txt', b'Unrecognized document', document_id='f' * 32)
    assert not list((tmp_path / 'originals').iterdir())


def test_version_one_metadata_migrates_without_losing_content(tmp_path):
    library = DocumentLibrary(tmp_path)
    first = library.import_bytes('legacy.txt', b'Historical source')
    with closing(sqlite3.connect(library.path)) as db:
        db.execute('ALTER TABLE versions DROP COLUMN original_filename')
        db.execute('PRAGMA user_version=1')
        db.commit()
    migrated = DocumentLibrary(tmp_path)
    assert migrated.search('Historical')['results'][0]['original_filename'] == 'legacy.txt'
    assert migrated.list_documents()[0]['sha256'] == first['sha256']


def test_pdf_deadline_removes_temporary_source(tmp_path, monkeypatch):
    library = DocumentLibrary(tmp_path)
    def timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(args[0], 20)
    monkeypatch.setattr(module.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='20-second'):
        library.import_bytes('slow.pdf', b'%PDF-1.7\nslow')
    assert not list(tmp_path.glob('prometheus-pdf-*'))
    assert not library.list_documents()


def test_import_file_csv_docx_and_pdf(tmp_path):
    library = DocumentLibrary(tmp_path / 'library')
    path = tmp_path / 'inventory.csv'
    path.write_text('part,count\ntransistor,14', encoding='utf-8-sig')
    library.import_file(path)
    library.import_bytes('contract.docx', docx())
    library.import_bytes('purchase.pdf', pdf_bytes())
    assert library.search('transistor')['results'][0]['title'] == 'inventory.csv'
    assert library.search('renewal')['results'][0]['title'] == 'contract.docx'
    pdf = library.search('purchase')['results'][0]
    assert pdf['title'] == 'purchase.pdf' and pdf['location'].startswith('page 1')
    assert 'approved' in pdf['excerpt']


@pytest.mark.parametrize('name', ['../x.txt', 'C:\\x.txt', 'CON.txt', 'file.txt:stream', 'x.txt.', '/x.md', 'NUL.docx', 'x.exe'])
def test_hostile_names_do_not_write_originals(tmp_path, name):
    library = DocumentLibrary(tmp_path)
    with pytest.raises(ValueError):
        library.import_bytes(name, b'hello')
    assert not library.list_documents(include_history=True)
    assert not list((tmp_path / 'originals').iterdir())


@pytest.mark.parametrize('payload', [b'', b'\xff\xfeABC', b'abc\0def', b' ' * 8])
def test_invalid_text_is_rejected(tmp_path, payload):
    with pytest.raises(ValueError):
        DocumentLibrary(tmp_path).import_bytes('text.txt', payload)


def test_file_and_extracted_text_limits(tmp_path, monkeypatch):
    library = DocumentLibrary(tmp_path)
    monkeypatch.setattr(module, 'MAX_FILE_BYTES', 32)
    with pytest.raises(ValueError):
        library.import_bytes('big.txt', b'a' * 33)
    monkeypatch.setattr(module, 'MAX_FILE_BYTES', 1000)
    monkeypatch.setattr(module, 'MAX_TEXT_CHARS', 50)
    with pytest.raises(ValueError, match='character'):
        library.import_bytes('big.txt', b'a' * 51)


@pytest.mark.parametrize('extra', [
    {'../outside.txt': 'bad'}, {'word/../../escape': 'bad'}, {'word\\escape': 'bad'},
    {'/absolute': 'bad'}, {'C:stream': 'bad'}, {'bomb.bin': 'a' * 100_000},
])
def test_docx_unsafe_archives_are_rejected_without_extraction(tmp_path, extra):
    with pytest.raises(ValueError):
        DocumentLibrary(tmp_path / 'library').import_bytes('attack.docx', docx(extra=extra))
    assert not (tmp_path / 'outside.txt').exists()


def test_docx_entity_declaration_and_symlink_are_rejected(tmp_path):
    library = DocumentLibrary(tmp_path)
    xml = '<!DOCTYPE doc [<!ENTITY x "value">]><doc>&x;</doc>'
    with pytest.raises(ValueError, match='declarations'):
        library.import_bytes('entity.docx', docx(xml=xml))
    with pytest.raises(ValueError, match='declarations'):
        library.import_bytes('entity-utf16.docx', docx(xml=xml.encode('utf-16')))
    output = io.BytesIO(docx())
    with zipfile.ZipFile(output, 'a') as archive:
        link = zipfile.ZipInfo('word/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, '../../other')
    with pytest.raises(ValueError, match='unsafe'):
        library.import_bytes('link.docx', output.getvalue())


def test_invalid_pdf_and_no_text_pdf(tmp_path):
    library = DocumentLibrary(tmp_path)
    with pytest.raises(ValueError, match='header'):
        library.import_bytes('fake.pdf', b'not a pdf')
    with pytest.raises(ValueError):
        library.import_bytes('bad.pdf', b'%PDF-1.7\ngarbage')
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(ValueError, match='OCR'):
        library.import_bytes('scan.pdf', output.getvalue())


def test_checksum_damage_is_detected_before_reusing_original(tmp_path):
    library = DocumentLibrary(tmp_path)
    entry = library.import_bytes('note.txt', b'Original text')
    (tmp_path / entry['original_path']).write_bytes(b'Tampered text')
    with pytest.raises(ValueError, match='checksum'):
        library.import_bytes('note.txt', b'Original text')
    assert len(library.list_documents(include_history=True)) == 1


def test_semantic_synonyms_hybrid_ranking_and_visible_fallback(tmp_path):
    client = FixtureEmbeddings()
    library = DocumentLibrary(tmp_path, embedding_client=client)
    library.import_bytes('manual.md', b'Automobile maintenance includes engine oil.')
    library.import_bytes('pets.md', b'Cats are companion animals.')
    semantic = library.search('vehicle')
    assert semantic['retrieval'] == 'semantic' and semantic['semantic_status'] == 'ready'
    assert semantic['results'][0]['title'] == 'manual.md'
    assert semantic['results'][0]['semantic_score'] == pytest.approx(1)
    assert semantic['results'][0]['keyword_score'] is None
    hybrid = library.search('automobile')
    assert hybrid['retrieval'] == 'hybrid' and hybrid['results'][0]['retrieval'] == 'hybrid'
    client.fail = True
    fallback = library.search('automobile')
    assert fallback['retrieval'] == 'keyword' and fallback['semantic_status'] == 'unavailable'
    assert fallback['results'][0]['title'] == 'manual.md'


def test_low_ram_defers_embeddings_and_can_index_later(tmp_path):
    client = FixtureEmbeddings()
    allowed = False
    library = DocumentLibrary(tmp_path, embedding_client=client, embedding_allowed=lambda: allowed)
    imported = library.import_bytes('manual.txt', b'Automobile service instructions')
    assert imported['embedding_status'] == 'resource_deferred' and not client.calls
    assert library.search('automobile')['semantic_status'] == 'resource_deferred'
    assert not client.calls
    allowed = True
    result = library.index_missing_embeddings()
    assert result['status'] == 'indexed' and result['indexed'] == 1 and result['pending'] == 0
    assert library.search('vehicle')['results']


def test_digest_change_never_compares_old_vectors(tmp_path):
    client = FixtureEmbeddings()
    library = DocumentLibrary(tmp_path, embedding_client=client)
    library.import_bytes('manual.txt', b'Automobile service instructions')
    assert library.search('vehicle')['results']
    client.model = 'test:model@digest-v2'
    missing = library.search('vehicle')
    assert missing['semantic_status'] == 'not_indexed' and not missing['results']
    assert library.index_missing_embeddings()['indexed'] == 1
    assert library.search('vehicle')['results']


def test_dimension_change_invalidates_vectors_and_allows_reindex(tmp_path):
    client = FixtureEmbeddings()
    library = DocumentLibrary(tmp_path, embedding_client=client)
    library.import_bytes('manual.txt', b'Automobile service instructions')
    client.dimension = 4
    result = library.search('automobile')
    assert result['semantic_status'] == 'dimension_changed_reindex_required'
    assert result['results'][0]['retrieval'] == 'keyword'
    indexed = library.index_missing_embeddings()
    assert indexed['indexed'] == 1 and indexed['dimension'] == 4
    assert library.search('vehicle')['results']


@pytest.mark.parametrize('bad', [[math.nan, 1], [math.inf], [], [0, 0], ['invalid'], [1] * 4097])
def test_invalid_embedding_data_cannot_break_keyword_search(tmp_path, bad):
    class BadEmbedding(FixtureEmbeddings):
        def embed(self, texts):
            return [bad for _ in texts]
    library = DocumentLibrary(tmp_path, embedding_client=BadEmbedding())
    result = library.import_bytes('manual.txt', b'Automobile maintenance')
    assert result['embedding_status'] == 'unavailable'
    assert library.search('automobile')['results'][0]['retrieval'] == 'keyword'


def test_latest_only_embeddings_bounded_reindex_and_literal_query(tmp_path):
    client = FixtureEmbeddings()
    library = DocumentLibrary(tmp_path, embedding_client=client, embedding_allowed=lambda: False)
    library.import_bytes('manual.txt', b'Cats in ancient records.')
    library.import_bytes('manual.txt', b'Automobile service instructions. ' * 120)
    library.embedding_allowed = lambda: True
    partial = library.index_missing_embeddings(limit=1)
    assert partial['indexed'] == 1 and partial['pending'] > 0 and partial['status'] == 'partial'
    complete = library.index_missing_embeddings(limit=128)
    assert complete['pending'] == 0
    assert library.search('automobile OR nonexistent')['results']
    assert not library.search('cats', semantic=False)['results']
    assert library.search('cats', semantic=False, include_history=True)['results']


def test_metadata_and_limits_reject_bad_values(tmp_path):
    library = DocumentLibrary(tmp_path)
    for meta in ({'source_url': 'javascript:alert(1)'}, {'source_url': 'https://user:pass@example.com'},
                 {'source_date': 'tomorrow'}, {'source_date': '2026-02-30'}, {'document_id': '../bad'}):
        with pytest.raises(ValueError):
            library.import_bytes('text.md', b'Source content', **meta)
    for query in ('', '*', 'the and' * 1000):
        with pytest.raises(ValueError):
            library.search(query)
    with pytest.raises(ValueError):
        library.search('valid', limit=True)


def test_ollama_embedding_contract_requires_digest_and_bounded_batches(monkeypatch):
    from prometheus_assistant.ollama import OllamaClient
    monkeypatch.setattr(OllamaClient, 'ensure_local_model', lambda self: {'digest': 'a' * 64})
    calls = []
    def request(self, path, payload):
        calls.append((path, payload))
        return {'embeddings': [[0.2, 0.3]] * len(payload['input'])}
    monkeypatch.setattr(OllamaClient, '_request', request)
    client = OllamaEmbeddingClient()
    assert 'a' * 64 in client.identity()
    assert client.embed(['document']) == [[0.2, 0.3]]
    assert calls[0][0] == '/api/embed'
    assert calls[0][1]['truncate'] is False
    assert calls[0][1]['keep_alive'] == '10s'
    with pytest.raises(ValueError):
        client.embed(['one', 'two', 'three'])
    monkeypatch.setattr(OllamaClient, 'ensure_local_model', lambda self: {'digest': 'unknown'})
    with pytest.raises(ValueError, match='digest'):
        client.identity()
