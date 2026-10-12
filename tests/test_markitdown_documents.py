"""Real offline converters and their process/archive boundaries."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import zipfile

import pytest

from prometheus_assistant.document_library import DocumentLibrary
from prometheus_assistant.ollama import GenerationCancelled
from prometheus_assistant import document_library
from prometheus_assistant import markitdown_documents as converter


def office_zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


def xlsx_bytes(text='Portable document evidence', *, cell='A2', extra=None):
    entries = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Inventory" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Reference</t></is></c></row><row r="2"><c r="{cell}" t="inlineStr"><is><t>{text}</t></is></c></row></sheetData></worksheet>',
    }
    entries.update(extra or {})
    return office_zip(entries)


def pptx_bytes(text='Portable presentation evidence'):
    return office_zip({
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>',
        'ppt/presentation.xml': '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/></p:presentation>',
        'ppt/_rels/presentation.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>',
        'ppt/slides/slide1.xml': f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1000" cy="1000"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
    })


@pytest.mark.parametrize('name,body,location', [('inventory.xlsx', xlsx_bytes(), 'sheet Inventory'), ('brief.pptx', pptx_bytes(), 'slide 1')], ids=['xlsx','pptx'])
def test_real_offline_office_import_search_and_original(tmp_path, name, body, location, monkeypatch):
    # Worker isolation ignores caller's Python import overrides and cloud keys.
    monkeypatch.setenv('PYTHONPATH', str(tmp_path/'unrelated-host-packages'))
    monkeypatch.setenv('OPENAI_API_KEY', 'not-a-usable-key')
    library = DocumentLibrary(tmp_path/'library')
    record = library.import_bytes(name, body)
    found = library.search('portable', semantic=False)['results'][0]
    assert found['location'].startswith(location)
    assert 'evidence' in found['excerpt']
    assert (library.root/record['original_path']).read_bytes() == body
    assert record['sha256'] == hashlib.sha256(body).hexdigest()


def test_xlsx_revised_content_retains_history_and_conditional_updates(tmp_path):
    library = DocumentLibrary(tmp_path/'library')
    first = library.import_bytes('inventory.xlsx', xlsx_bytes('Original capacitor'))
    second = library.import_bytes('inventory.xlsx', xlsx_bytes('Revised transistor'), document_id=first['document_id'], expected_version_id=first['version_id'])
    assert second['version_id'] != first['version_id']
    assert not library.search('capacitor', semantic=False)['results']
    assert library.search('capacitor', semantic=False, include_history=True)['results']
    assert library.search('transistor', semantic=False)['results']


def test_xlsx_keeps_literal_identifiers_na_and_cached_numeric_values(tmp_path):
    xml = '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>Evidence</t></is></c></row>
<row r="2"><c r="A2" t="inlineStr"><is><t>00123</t></is></c></row>
<row r="3"><c r="A3" t="inlineStr"><is><t>NA</t></is></c></row>
<row r="4"><c r="A4"><v>42</v></c></row>
<row r="5"><c r="A5"><f>1+1</f><v>2</v></c></row>
</sheetData></worksheet>'''
    library = DocumentLibrary(tmp_path)
    library.import_bytes('identifiers.xlsx', xlsx_bytes(extra={'xl/worksheets/sheet1.xml': xml}))
    excerpt = library.search('Evidence', semantic=False)['results'][0]['excerpt']
    for value in ('00123', 'NA', '42', '2'):
        assert '| '+value+' |' in excerpt
    assert 'NaN' not in excerpt


@pytest.mark.parametrize('cell', ['A10001', 'IW2', 'XFD1048576'])
def test_sparse_sheet_cannot_expand_large_dimensions(tmp_path, cell):
    library = DocumentLibrary(tmp_path)
    with pytest.raises(ValueError, match='rows and 256 columns'):
        library.import_bytes('large.xlsx', xlsx_bytes(cell=cell))
    assert not library.list_documents()
    assert not list(library.originals.iterdir())


@pytest.mark.parametrize('extra,match', [
    ({'xl/vbaProject.bin': b'program'}, 'macros'),
    ({'xl/embeddings/data.xlsx': b'embedded'}, 'embedded'),
    ({'xl/danger.xml': '<!DOCTYPE doc [<!ENTITY x "secret">]><doc>&x;</doc>'}, 'declarations'),
    ({'xl/_rels/external.rels': '<Relationships><Relationship TargetMode="External" Type="image" Target="file:///C:/private"/></Relationships>'}, 'external resource'),
    ({'xl/_rels/external.rels': '<Relationships><Relationship TargetMode="External" Type="hyperlink" Target="https://example.org"/></Relationships>'}, 'external resource'),
])
def test_office_active_or_external_resources_rejected(tmp_path, extra, match):
    with pytest.raises(ValueError, match=match):
        DocumentLibrary(tmp_path).import_bytes('unsafe.xlsx', xlsx_bytes(extra=extra))


def test_cancellation_before_import_does_not_create_a_document(tmp_path):
    event = threading.Event()
    event.set()
    library = DocumentLibrary(tmp_path)
    with pytest.raises(GenerationCancelled, match='cancelled'):
        library.import_bytes('note.txt', b'owner evidence', cancel_event=event)
    assert not library.list_documents()
    assert not list(library.originals.iterdir())


def test_cancellation_after_extraction_preserves_library(tmp_path, monkeypatch):
    event = threading.Event()
    def extract(*args, **kwargs):
        event.set()
        return [('document', 'Never committed')]
    monkeypatch.setattr(document_library, '_extract', extract)
    library = DocumentLibrary(tmp_path)
    with pytest.raises(GenerationCancelled, match='cancelled'):
        library.import_bytes('note.docx', b'unparsed fixture', cancel_event=event)
    assert not library.list_documents()
    assert not list(library.originals.iterdir())


def test_cancelling_active_worker_reaps_process_and_tempfiles(tmp_path, monkeypatch):
    event = threading.Event()
    original = converter.subprocess.Popen
    processes = []
    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        event.set()
        return process
    monkeypatch.setattr(converter.subprocess, 'Popen', launch)
    monkeypatch.setattr(converter.tempfile, 'tempdir', str(tmp_path))
    library = DocumentLibrary(tmp_path/'library')
    with pytest.raises(GenerationCancelled, match='cancelled'):
        library.import_bytes('cancel.xlsx', xlsx_bytes(), cancel_event=event)
    assert processes and all(process.poll() is not None for process in processes)
    assert not list(tmp_path.glob('prometheus-markitdown-*'))
    assert not library.list_documents()
    assert not list(library.originals.iterdir())


def test_worker_guard_denies_real_socket_and_subprocess_calls():
    code = '''
import socket, subprocess, sys
from prometheus_assistant.markitdown_documents import _offline_audit
sys.addaudithook(_offline_audit)
for action in [lambda: socket.socket(), lambda: subprocess.Popen([sys.executable, '-c', 'pass'])]:
    try: action()
    except PermissionError: pass
    else: raise AssertionError('unsafe operation was allowed')
print('blocked')
'''
    result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, timeout=10,
                            env={**os.environ, 'PYTHONPATH': str(Path(converter.__file__).parent.parent)})
    assert result.returncode == 0, result.stderr
    assert b'blocked' in result.stdout


def test_bundled_lock_and_all_wheels_are_exact_pinned_upstream_artifacts():
    vendor = Path(converter.__file__).with_name('vendor')/'markitdown'
    data = (vendor/'LOCK.json').read_bytes()
    assert hashlib.sha256(data).hexdigest() == converter.LOCK_SHA256
    lock = json.loads(data)
    assert lock['version'] == '0.1.8'
    for row in lock['wheels']:
        path = vendor/row['filename']
        assert path.stat().st_size == row['bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        assert row['license_files']
        assert row['source_url'].startswith('https://files.pythonhosted.org/')
    assert not any('magika' in row['filename'] or 'onnxruntime' in row['filename'] for row in lock['wheels'])
