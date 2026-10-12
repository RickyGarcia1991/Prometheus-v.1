from pathlib import Path

import pytest

from prometheus_assistant.documents import search_documents


def test_project_search_ignores_question_words_and_keeps_exact_line_context(tmp_path):
    (tmp_path / 'noise.py').write_text('# please find where the function is defined\n')
    (tmp_path / 'agent.py').write_text('# planner entry point\ndef run_agent():\n    return 1\n')
    result = search_documents(tmp_path, 'Please find where the run_agent function is defined', include_code=True)
    hit = result['results'][0]
    assert hit['source'] == 'agent.py'
    assert hit['line'] == 2 and hit['excerpt'] == 'def run_agent():'
    assert hit['context_start_line'] == 1 and hit['context_end_line'] == 3
    assert hit['context'] == '# planner entry point\ndef run_agent():\n    return 1'


def test_exact_phrase_breaks_keyword_ties_without_fabricating_citations(tmp_path):
    (tmp_path / 'a.py').write_text('# beta elsewhere alpha\n')
    (tmp_path / 'b.py').write_text('# alpha beta\n')
    rows = search_documents(tmp_path, 'alpha beta', include_code=True)['results']
    assert [row['source'] for row in rows] == ['b.py', 'a.py']
    assert rows[0]['matched_terms'] == rows[1]['matched_terms'] == 2
    assert rows[0]['rank_score'] > rows[1]['rank_score']


@pytest.mark.parametrize('private_name', ['credentials.json', 'secrets.json', 'id_rsa.txt', 'secrets.toml'])
def test_project_search_never_reads_known_private_names(tmp_path, monkeypatch, private_name):
    private = tmp_path / private_name
    private.write_text('target SECRET_VALUE')
    (tmp_path / 'public.py').write_text('target = 1')
    original = Path.open
    def guarded_open(path, *args, **kwargs):
        assert path != private, 'Private file must be excluded before opening.'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    result = search_documents(tmp_path, 'target', include_code=True)
    assert [row['source'] for row in result['results']] == ['public.py']


def test_project_search_rejects_linked_selected_root_before_resolving(tmp_path, monkeypatch):
    from prometheus_assistant import coding_workspace
    (tmp_path / 'public.py').write_text('target = 1')
    original = coding_workspace._linked
    monkeypatch.setattr(coding_workspace, '_linked', lambda path: path == tmp_path or original(path))
    with pytest.raises(ValueError, match='[Ll]ink'):
        search_documents(tmp_path, 'target', include_code=True)


def test_project_search_context_is_bounded_and_results_have_stable_ties(tmp_path):
    (tmp_path / 'b.py').write_text('x' * 10000 + '\nneedle\n' + 'y' * 10000)
    (tmp_path / 'a.py').write_text('needle\n')
    rows = search_documents(tmp_path, 'needle', include_code=True)['results']
    assert [row['source'] for row in rows] == ['a.py', 'b.py']
    assert rows[1]['line'] == 2
    assert len(rows[1]['context']) <= 1200
    assert rows[1]['context_truncated']


@pytest.mark.parametrize('limit', [True, 0, -1, 21, '5'])
def test_document_search_rejects_invalid_result_limit(tmp_path, limit):
    with pytest.raises(ValueError, match='limit'):
        search_documents(tmp_path, 'target', limit=limit)
