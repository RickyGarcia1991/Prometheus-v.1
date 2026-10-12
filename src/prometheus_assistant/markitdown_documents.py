"""Portable, on-demand offline Microsoft MarkItDown document conversion.

Only selected unmodified upstream converter modules are loaded. The generic
MarkItDown facade (URL handling, auto-detection, plugins) is never initialized.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from xml.etree import ElementTree

EXTENSIONS = ('.pdf', '.docx', '.pptx', '.xlsx')
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 400_000
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
TIMEOUT = 45
MAX_MEMORY_BYTES = 512 * 1024 * 1024
LOCK_SHA256 = 'd9f1952ed4bf08c7760fb119723dc9771c344a1623565ee96c9686f4789c1660'


def validate_office(content, suffix):
    """Inspect every member without extracting document-provided paths."""
    main = {'.docx': 'word/document.xml', '.pptx': 'ppt/presentation.xml', '.xlsx': 'xl/workbook.xml'}[suffix]
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > (256 if suffix == '.docx' else 1024) or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
                raise ValueError('Office archive exceeds the expanded size or entry limit.')
            names = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (entry.filename.casefold() in names or path.is_absolute() or '..' in path.parts
                        or '\\' in entry.orig_filename or ':' in entry.filename
                        or any(ord(c) < 32 for c in entry.orig_filename)
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                    raise ValueError('Office archive contains unsafe, duplicate or encrypted entries.')
                names.add(entry.filename.casefold())
                if entry.file_size > max(1, entry.compress_size) * 100:
                    raise ValueError('Office archive compression ratio exceeds the import limit.')
                if any(part.casefold() in {'activex', 'embeddings'} for part in path.parts) or path.name.casefold().endswith(('.exe', '.dll', '.bin')):
                    raise ValueError('Documents containing macros, active content or embedded programs are unsupported.')
                if path.suffix.lower() not in {'.xml', '.rels'}:
                    continue
                if entry.file_size > MAX_XML_BYTES:
                    raise ValueError('Office XML exceeds the import limit.')
                data = archive.read(entry)
                try:
                    xml = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
                except UnicodeError as error:
                    raise ValueError('Office XML must use UTF-8 or BOM-marked UTF-16.') from error
                if '\0' in xml or re.search(r'<!\s*(?:DOCTYPE|ENTITY)', xml, re.I):
                    raise ValueError('Office XML declarations are unsupported.')
                tree = ElementTree.fromstring(xml)
                if path.suffix.lower() == '.rels':
                    for rel in tree:
                        if rel.attrib.get('TargetMode', '').casefold() == 'external':
                            target = rel.attrib.get('Target', '')
                            # Ordinary hyperlinks are retained as text; linked
                            # images, templates, UNC paths and workbooks are not read.
                            if not rel.attrib.get('Type', '').endswith('/hyperlink') or not re.match(r'^https?://', target, re.I):
                                raise ValueError('Documents with external resource relationships are unsupported.')
                if suffix == '.pptx' and entry.filename == main:
                    if sum(1 for node in tree.iter() if node.tag.endswith('}sldId')) > 100:
                        raise ValueError('PowerPoint import is limited to 100 slides.')
                if suffix == '.xlsx':
                    if entry.filename == main and sum(1 for node in tree.iter() if node.tag.endswith('}sheet')) > 32:
                        raise ValueError('Excel import is limited to 32 sheets.')
                    if entry.filename.startswith('xl/worksheets/'):
                        cells = 0
                        for node in tree.iter():
                            if node.tag.endswith('}c'):
                                cells += 1
                                match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]*)', node.attrib.get('r', ''))
                                if match:
                                    column = 0
                                    for letter in match[1]:
                                        column = column * 26 + ord(letter) - 64
                                    if column > 256 or int(match[2]) > 10_000:
                                        raise ValueError('Excel import is limited to 10,000 rows and 256 columns per sheet.')
                        if cells > 100_000:
                            raise ValueError('Excel sheet exceeds the 100,000-cell import limit.')
            if main.casefold() not in names:
                raise ValueError('Office archive has no main document XML.')
    except (zipfile.BadZipFile, ElementTree.ParseError, RuntimeError, OSError) as error:
        raise ValueError('The Office archive or document XML is invalid.') from error


def _memory_limit():
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
        return
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                    ('max_working_set', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ('reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]

    class Limits(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    limits = Limits()
    limits.basic.flags = 0x100 | 0x8  # process memory + no child processes
    limits.basic.active_processes = 1
    limits.process_memory = MAX_MEMORY_BYTES
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise ValueError('Cannot establish the document parser memory limit on this host.')
    # The process owns the handle until exit so the limit remains in force.


def _offline_audit(event, args):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.startfile', 'os.posix_spawn', 'os.fork', 'os.exec'}:
        raise PermissionError('Document conversion cannot use the network or launch programs.')


def _install_vendor(suffix, target):
    if sys.version_info[:2] != (3, 12) or os.name != 'nt' or sys.maxsize <= 2**32:
        raise ValueError('Portable MarkItDown requires the bundled Windows x64 Python 3.12 runtime.')
    vendor = Path(__file__).with_name('vendor') / 'markitdown'
    lock_bytes = (vendor/'LOCK.json').read_bytes()
    if hashlib.sha256(lock_bytes).hexdigest() != LOCK_SHA256:
        raise ValueError('The bundled MarkItDown dependency lock failed verification.')
    lock = json.loads(lock_bytes)
    for row in lock['wheels']:
        if suffix not in row['formats']:
            continue
        wheel = vendor/row['filename']
        with wheel.open('rb') as stream:
            # Validate and extract one immutable snapshot so an interrupted
            # deployment cannot replace bytes between hashing and extraction.
            blob = stream.read(row['bytes'] + 1)
            if len(blob) != row['bytes'] or hashlib.sha256(blob).hexdigest() != row['sha256']:
                raise ValueError('A bundled MarkItDown dependency failed verification.')
            # These are SHA-256 pinned upstream distributions, not document
            # contents. Still validate paths before extracting into the new dir.
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                for entry in archive.infolist():
                    path = PurePosixPath(entry.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename:
                        raise ValueError('Invalid bundled dependency path.')
                archive.extractall(target)
    sys.path.insert(0, str(target))
    # Importing markitdown.__init__ eagerly imports all converters and Magika.
    # Namespace packages let us use the untouched selected converter modules
    # without enabling that unrelated generic dispatcher or plugin discovery.
    import importlib.machinery
    import types
    for name, path in [('markitdown', target/'markitdown'), ('markitdown.converters', target/'markitdown/converters')]:
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        package.__package__ = name
        package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        package.__spec__.submodule_search_locations = package.__path__
        sys.modules[name] = package


def _check_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('The document has no extractable text. Scanned PDFs need OCR before import.')
    if len(text) > MAX_TEXT_CHARS or '\0' in text:
        raise ValueError('Extracted text exceeds the import limit or contains binary characters.')
    return text.strip()


def _convert(path, suffix):
    from markitdown._stream_info import StreamInfo
    if suffix == '.pdf':
        import pypdf
        import pypdf.filters as filters
        from markitdown.converters._pdf_converter import PdfConverter
        for setting in ('ZLIB_MAX_OUTPUT_LENGTH', 'LZW_MAX_OUTPUT_LENGTH', 'RUN_LENGTH_MAX_OUTPUT_LENGTH',
                        'MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH', 'MAX_DECLARED_STREAM_LENGTH'):
            if not hasattr(filters, setting):
                raise ValueError('Bundled PDF parser does not provide required decompression limits.')
            setattr(filters, setting, MAX_XML_BYTES)
        reader = pypdf.PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise ValueError('Encrypted PDFs must be decrypted before importing.')
        if len(reader.pages) > 100:
            raise ValueError('PDF import is limited to 100 pages per document.')
        sections = []
        total = 0
        for index, page in enumerate(reader.pages, 1):
            # Enforce decompression/output limits before the richer converter.
            plain = page.extract_text(extraction_mode='plain') or ''
            if len(plain) > MAX_TEXT_CHARS:
                raise ValueError('PDF extracted text exceeds the import limit.')
            writer = pypdf.PdfWriter()
            writer.add_page(page)
            stream = io.BytesIO()
            writer.write(stream)
            stream.seek(0)
            text = PdfConverter().convert(stream, StreamInfo(extension=suffix)).markdown
            if text.strip():
                text = _check_text(text)
                total += len(text)
                if total > MAX_TEXT_CHARS:
                    raise ValueError('PDF extracted text exceeds the import limit.')
                sections.append((f'page {index}', text))
        if not sections:
            _check_text('')
        return sections
    import importlib
    converter_name = {'.docx': 'DocxConverter', '.pptx': 'PptxConverter', '.xlsx': 'XlsxConverter'}[suffix]
    module = importlib.import_module('markitdown.converters._'+suffix[1:]+'_converter')
    original_read_excel = None
    if suffix == '.xlsx':
        # Upstream's default pandas inference loses source evidence: literal
        # "00123" becomes 123 and "NA" becomes NaN. Keep cell types and strings.
        # This adapter is confined to this short-lived worker; wheels stay exact.
        original_read_excel = module.pd.read_excel
        def read_excel_preserving_cells(*args, **kwargs):
            return original_read_excel(*args, **{**kwargs, 'dtype': object, 'keep_default_na': False})
        module.pd.read_excel = read_excel_preserving_cells
    try:
        with path.open('rb') as stream:
            text = _check_text(getattr(module, converter_name)().convert(stream, StreamInfo(extension=suffix)).markdown)
    finally:
        if original_read_excel is not None:
            module.pd.read_excel = original_read_excel
    if suffix == '.pptx':
        parts = re.split(r'<!-- Slide number: (\d+) -->', text)
        sections = [(f'slide {parts[i]}', parts[i+1].strip()) for i in range(1, len(parts), 2) if parts[i+1].strip()]
        return sections or [('presentation', text)]
    if suffix == '.xlsx':
        parts = re.split(r'(?m)^## ([^\r\n]+)\r?\n', text)
        sections = [('sheet '+parts[i], parts[i+1].strip()) for i in range(1, len(parts), 2) if parts[i+1].strip()]
        return sections or [('workbook', text)]
    return [('document', text)]


def extract_document(content, suffix, *, cancel_event=None):
    if suffix not in EXTENSIONS or not isinstance(content, bytes) or not 1 <= len(content) <= MAX_FILE_BYTES:
        raise ValueError('Choose a supported document no larger than 8 MiB.')
    if suffix == '.pdf':
        if not content.startswith(b'%PDF-'):
            raise ValueError('The file does not have a PDF header.')
    if cancel_event is not None and cancel_event.is_set():
        from .ollama import GenerationCancelled
        raise GenerationCancelled('Document import cancelled.')
    # Host temp storage contains the short-lived source and native libraries;
    # all permanent runtime dependencies stay in the signed SSD release.
    with tempfile.TemporaryDirectory(prefix='prometheus-markitdown-') as folder:
        root = Path(folder)
        source, output = root/('source'+suffix), root/'result.json'
        source.write_bytes(content)
        env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        env.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
        # Windows virtual-environment python.exe is a redirector that launches
        # another process. Use its base interpreter so cancellation kills and
        # reaps the parser itself; the portable SSD interpreter is already base.
        python = getattr(sys, '_base_executable', None) or sys.executable
        process = subprocess.Popen([python, '-I', '-S', '-B', str(Path(__file__).resolve()), '--worker', str(source), str(output)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=env, cwd=folder, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + TIMEOUT
        try:
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    from .ollama import GenerationCancelled
                    raise GenerationCancelled('Document import cancelled.')
                if time.monotonic() >= deadline:
                    raise ValueError(f'Document extraction exceeded its {TIMEOUT}-second time limit.')
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        if not output.is_file() or output.stat().st_size > MAX_TEXT_CHARS * 8:
            raise ValueError('Document conversion failed or exceeded its output limit.')
        data = json.loads(output.read_text(encoding='utf-8'))
        if process.returncode or 'error' in data:
            raise ValueError(str(data.get('error', 'Document conversion failed.'))[:500])
        sections = data.get('sections')
        if not isinstance(sections, list) or not 1 <= len(sections) <= 100:
            raise ValueError('Document converter returned invalid sections.')
        total = 0
        for section in sections:
            if not isinstance(section, list) or len(section) != 2 or not isinstance(section[0], str) or len(section[0]) > 200:
                raise ValueError('Document converter returned an invalid location.')
            total += len(_check_text(section[1]))
        if total > MAX_TEXT_CHARS:
            raise ValueError('Extracted text exceeds the import limit.')
        return sections


def _worker(source, output):
    result = {}
    status = 0
    try:
        _memory_limit()
        sys.addaudithook(_offline_audit)
        source = Path(source)
        suffix = source.suffix.lower()
        if suffix not in EXTENSIONS:
            raise ValueError('Unsupported document format.')
        if not 1 <= source.stat().st_size <= MAX_FILE_BYTES:
            raise ValueError('Document must contain 1 byte to 8 MiB.')
        content = source.read_bytes()
        if suffix == '.pdf':
            if not content.startswith(b'%PDF-'):
                raise ValueError('The file does not have a PDF header.')
        else:
            validate_office(content, suffix)
        del content
        target = source.parent/'runtime'
        target.mkdir()
        _install_vendor(suffix, target)
        result = {'sections': _convert(source, suffix), 'converter': 'Microsoft MarkItDown 0.1.8'}
    except Exception as error:
        result = {'error': ('MarkItDown conversion failed: '+str(error))[:500]}
        status = 1
    Path(output).write_text(json.dumps(result, ensure_ascii=True), encoding='utf-8')
    return status


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--worker':
        raise SystemExit(2)
    raise SystemExit(_worker(sys.argv[2], sys.argv[3]))
