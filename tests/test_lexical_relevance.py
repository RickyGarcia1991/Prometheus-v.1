"""General question phrasing and English plurals must not hide useful evidence."""
import hashlib

import pytest

from prometheus_assistant.document_library import DocumentLibrary
from prometheus_assistant.evidence_text import focused_terms


def test_singular_request_finds_plural_reference_without_reindex(tmp_path):
    library = DocumentLibrary(tmp_path)
    library.import_bytes('power-reference.md', b'Regulators require input range and load-current checks.')
    before = hashlib.sha256(library.path.read_bytes()).hexdigest()
    reader = DocumentLibrary(tmp_path, read_only=True)
    result = reader.search('regulator', semantic=False)
    assert [row['title'] for row in result['results']] == ['power-reference.md']
    assert hashlib.sha256(library.path.read_bytes()).hexdigest() == before


def test_plural_request_finds_singular_reference(tmp_path):
    library = DocumentLibrary(tmp_path)
    library.import_bytes('assembly.md', b'The bearing must match the shaft diameter and intended speed.')
    assert library.search('bearings', semantic=False)['results'][0]['title'] == 'assembly.md'


def test_question_scaffolding_does_not_outrank_requested_subject(tmp_path):
    library = DocumentLibrary(tmp_path)
    library.import_bytes('power-guide.md', b'Regulators: verify input supply and loads before startup. The board rails need measurements.')
    library.import_bytes('business-guide.md',
        b'Propose a new step-by-step plan. We do not know whether the new references help. '
        b'The step-by-step plan uses a new planning library and references for the next plan.')
    query = ('Using my regulator references, propose a step-by-step plan for a new board supply. '
             'We do not know whether the load or rail is causing the fault.')
    result = library.search(query, semantic=False)
    assert result['results'][0]['title'] == 'power-guide.md'


@pytest.mark.parametrize(('query', 'body'), [
    ('step response', 'The step response describes output after an abrupt input change.'),
    ('business plan', 'The business plan includes demand and funding assumptions.'),
    ('current limit', 'The current limit is configured before connecting the load.'),
    ('library references', 'The library references are listed by publication date.'),
])
def test_simple_subject_queries_keep_their_literal_meaning(tmp_path, query, body):
    library = DocumentLibrary(tmp_path)
    library.import_bytes('reference.md', body.encode())
    result = library.search(query, semantic=False)
    assert result['results']
    assert result['results'][0]['excerpt'] == body


@pytest.mark.parametrize(('query', 'unrelated'), [('RX100', 'RX100s'), ('CMOS', 'CMO'), ('axis', 'axi')])
def test_codes_and_nonplural_suffixes_are_not_broadly_stemmed(tmp_path, query, unrelated):
    library = DocumentLibrary(tmp_path)
    library.import_bytes('unrelated.md', unrelated.encode())
    assert library.search(query, semantic=False)['results'] == []


def test_exact_absent_designator_is_not_replaced_by_a_similar_identifier(tmp_path):
    library = DocumentLibrary(tmp_path)
    library.import_bytes('board.md', b'Board resistor R123s is a label in a different revision.')
    result = library.search('What is the exact value of resistor R123 on this board?', semantic=False)
    assert result['results'] == []
    assert 'R123' in result['note']


@pytest.mark.parametrize(('query', 'body', 'subject'), [
    ('Compare precision voltage reference circuits for low noise devices.',
     'Bandgap references: Overview of the temperature compensated junction network.', 'reference'),
    ('Compare plugin isolation approaches before choosing a software library.',
     'Shared library loading requires ABI-compatible exports.', 'library'),
    ('Using my circuit references, compare precision voltage references for low noise devices.',
     'Bandgap references: Overview of the temperature compensated junction network.', 'references'),
])
def test_long_questions_preserve_reference_and_library_subjects(tmp_path, query, body, subject):
    library = DocumentLibrary(tmp_path)
    library.import_bytes('notes.md', body.encode())
    result = library.search(query, semantic=False)
    assert subject in result['focused_search_terms']
    assert result['results'][0]['excerpt'] == body


@pytest.mark.parametrize('query', [
    'Using my installation library, inspect the pump motor supply current and load.',
    'Use my installation and coupling references to inspect the pump motor supply current and load.',
    'Use my coupling references and explain the pump motor supply current and load checks.',
])
def test_explicit_source_request_nouns_remain_discounted(query):
    assert not {'library', 'references'} & set(focused_terms(query))
