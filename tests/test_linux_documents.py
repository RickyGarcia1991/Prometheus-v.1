import io
import sys
import pytest
from prometheus_assistant.document_library import DocumentLibrary
from test_markitdown_documents import office_zip, pptx_bytes

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Native Ubuntu reader')

def test_native_docx_text_can_be_imported_and_searched(tmp_path):
    raw = office_zip({'word/document.xml':'<w:document xmlns:w="urn:word"><w:body><w:p><w:r><w:t>Cobalt native document evidence</w:t></w:r></w:p></w:body></w:document>'})
    library = DocumentLibrary(tmp_path)
    item = library.import_bytes('native.docx', raw)
    hits = library.search('cobalt', semantic=False)['results']
    assert hits and 'evidence' in hits[0]['excerpt']
    assert (library.root/item['original_path']).read_bytes() == raw

def test_pdf_password_is_not_bypassed(tmp_path):
    from pypdf import PdfWriter
    writer = PdfWriter();writer.add_blank_page(width=200, height=200);writer.encrypt('synthetic-test-password')
    stream = io.BytesIO();writer.write(stream)
    library = DocumentLibrary(tmp_path)
    with pytest.raises(ValueError, match='password'):
        library.import_bytes('locked.pdf', stream.getvalue())
    assert not library.list_documents()

def test_slide_order_follows_relationships_not_filename(tmp_path):
    raw = office_zip({
        'ppt/presentation.xml':'<p:presentation xmlns:p="urn:powerpoint" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId r:id="second"/><p:sldId r:id="first"/></p:sldIdLst></p:presentation>',
        'ppt/_rels/presentation.xml.rels':'<Relationships><Relationship Id="first" Type="x/slide" Target="slides/slide1.xml"/><Relationship Id="second" Type="x/slide" Target="slides/slide2.xml"/></Relationships>',
        'ppt/slides/slide1.xml':'<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Alpha later</a:t></p:sld>',
        'ppt/slides/slide2.xml':'<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Beta first</a:t></p:sld>',
    })
    library = DocumentLibrary(tmp_path);library.import_bytes('ordered.pptx',raw)
    assert library.search('Beta',semantic=False)['results'][0]['location'].startswith('slide 1')
    assert library.search('Alpha',semantic=False)['results'][0]['location'].startswith('slide 2')
