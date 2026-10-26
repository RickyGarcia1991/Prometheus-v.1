"""Offline native text extraction inside the existing bounded parser child.

No OCR, macros, formula evaluation, external links or document-provided code.
Office input has already passed validate_office; PDF uses the pinned pure-Python pypdf wheel.
"""
import re
import zipfile
from xml.etree import ElementTree

MAX_TEXT = 400_000

def pdf(path):
    import pypdf
    import pypdf.filters as filters
    for setting in ('ZLIB_MAX_OUTPUT_LENGTH', 'LZW_MAX_OUTPUT_LENGTH', 'RUN_LENGTH_MAX_OUTPUT_LENGTH',
                    'MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH', 'MAX_DECLARED_STREAM_LENGTH'):
        if not hasattr(filters, setting):
            raise ValueError('The pinned PDF reader lacks decompression limits.')
        setattr(filters, setting, 4 * 1024 * 1024)
    reader = pypdf.PdfReader(path, strict=True)
    if reader.is_encrypted and not reader.decrypt(''):
        raise ValueError('This PDF requires a password; unlock an authorized copy before importing.')
    if len(reader.pages) > 100:
        raise ValueError('PDF import is limited to 100 pages.')
    rows, total = [], 0
    for index, page in enumerate(reader.pages, 1):
        text = page.extract_text(extraction_mode='plain') or ''
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError('Extracted text exceeds the import limit.')
        if text.strip():
            rows.append((f'page {index}', text.strip()))
    if not rows:
        raise ValueError('The document has no extractable text. Scanned PDFs need OCR before import.')
    return rows

def office(path, suffix):
    rows = []
    with zipfile.ZipFile(path) as archive:
        def tree(name):
            return ElementTree.fromstring(archive.read(name))
        def texts(node):
            return [x.text or '' for x in node.iter() if x.tag.rsplit('}', 1)[-1] == 't']
        if suffix == '.docx':
            text = '\n'.join(''.join(texts(p)) for p in tree('word/document.xml').iter()
                             if p.tag.rsplit('}', 1)[-1] == 'p')
            rows = [('document', text)]
        elif suffix == '.pptx':
            # Honor presentation order through its local relationship targets.
            rels = {x.attrib['Id']:x.attrib['Target'] for x in tree('ppt/_rels/presentation.xml.rels')
                    if x.attrib.get('Type', '').endswith('/slide')}
            for index, slide in enumerate((x for x in tree('ppt/presentation.xml').iter()
                                           if x.tag.endswith('}sldId')), 1):
                identity = slide.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                target = rels.get(identity, '')
                if not re.fullmatch(r'slides/slide[0-9]+\.xml', target):
                    raise ValueError('Unsupported slide relationship.')
                text = '\n'.join(texts(tree('ppt/'+target)))
                if text.strip(): rows.append((f'slide {index}', text))
        elif suffix == '.xlsx':
            shared = [''.join(texts(x)) for x in tree('xl/sharedStrings.xml')] if 'xl/sharedStrings.xml' in archive.namelist() else []
            rels = {x.attrib['Id']:x.attrib['Target'] for x in tree('xl/_rels/workbook.xml.rels')
                    if x.attrib.get('Type', '').endswith('/worksheet')}
            for sheet in (x for x in tree('xl/workbook.xml').iter() if x.tag.endswith('}sheet')):
                identity = sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                target = rels.get(identity, '')
                if target.startswith('/xl/'): target = target[4:]
                if not re.fullmatch(r'worksheets/sheet[0-9]+\.xml', target):
                    raise ValueError('Unsupported worksheet relationship.')
                lines = []
                for row in (x for x in tree('xl/'+target).iter() if x.tag.endswith('}row')):
                    cells = []
                    for cell in row:
                        values = [x.text or '' for x in cell if x.tag.endswith('}v')]
                        value = values[0] if values else ''.join(texts(cell))
                        if cell.attrib.get('t') == 's':
                            if not value.isdigit() or int(value) >= len(shared):
                                raise ValueError('Invalid shared string reference.')
                            value = shared[int(value)]
                        # Cached cell values only; formulas are never evaluated.
                        cells.append(cell.attrib.get('r', '')+': '+value)
                    lines.append('\t'.join(cells))
                rows.append(('sheet '+sheet.attrib.get('name', '')[:100], '\n'.join(lines)))
        else:
            raise ValueError('Unsupported native document format.')
    if sum(len(text) for _, text in rows) > MAX_TEXT:
        raise ValueError('Extracted text exceeds the import limit.')
    return [(label, text) for label, text in rows if text.strip()]
