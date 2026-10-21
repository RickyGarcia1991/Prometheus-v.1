from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from prometheus_assistant import articles
from prometheus_assistant.offline_archive import ARCHIVES


def test_auto_search_bounds_disk_work_and_prioritizes_subject(tmp_path):
    for item in ARCHIVES:
        file=tmp_path/'Knowledge/Kiwix'/item.project/item.filename
        file.parent.mkdir(parents=True,exist_ok=True);file.touch()
    selected=articles._selected(tmp_path,'auto','quadratic algebra equation')
    assert len(selected)==4
    assert selected[0][0].id=='libretexts-en-math'
    assert len(articles._selected(tmp_path,'all'))==38
    assert len(articles._selected(tmp_path,'devdocs-en-python'))==1


def test_auto_search_uses_only_installed_archives(tmp_path):
    wiki=ARCHIVES[0]; file=tmp_path/'Knowledge/Kiwix'/wiki.project/wiki.filename
    file.parent.mkdir(parents=True);file.touch()
    assert [i.id for i,_ in articles._selected(tmp_path,'auto','algebra')]==[wiki.id]


@pytest.mark.parametrize('query',['quadratic equation','polynomial roots','derivative'])
def test_ordinary_math_queries_find_math_without_naming_the_subject(tmp_path,query):
    for item in ARCHIVES:
        path=tmp_path/'Knowledge/Kiwix'/item.project/item.filename
        path.parent.mkdir(parents=True,exist_ok=True);path.touch()
    selected=articles._selected(tmp_path,'auto',query)
    assert selected[0][0].id=='libretexts-en-math'
    assert all(item.project!='devdocs' for item,_ in selected)


def test_unknown_topic_prefers_broad_references_over_alphabetical_code_packs(tmp_path):
    for item in ARCHIVES:
        path=tmp_path/'Knowledge/Kiwix'/item.project/item.filename
        path.parent.mkdir(parents=True,exist_ok=True);path.touch()
    assert {item.project for item,_ in articles._selected(tmp_path,'auto','unclassified topic')}=={'wikipedia','wikibooks','wiktionary','wikisource'}


def test_source_links_for_non_wikimedia_do_not_invent_wikis():
    item=next(a for a in ARCHIVES if a.id=='devdocs-en-python')
    blob=SimpleNamespace(size=45,mimetype='text/html',content=b'<p>Python dictionary documentation.</p>')
    entry=SimpleNamespace(is_redirect=False,path='c-api/dict',title='Dictionary',get_item=lambda:blob)
    archive=SimpleNamespace(get_entry_by_path=lambda path:entry)
    result=articles._entry(archive,item,'c-api/dict',1000)
    assert result['source_url']=='https://devdocs.io/python'
    assert result['source_url_scope']=='collection'
    assert result['citation'].startswith('zim://devdocs-en-python/')


def test_rejects_archive_manifest_path_escape(tmp_path,monkeypatch):
    malformed=replace(ARCHIVES[0],project='../../../outside')
    monkeypatch.setattr(articles,'ARCHIVES',(malformed,))
    with pytest.raises(articles.ArticleError,match='escapes'):
        articles._selected(tmp_path,'all')


def test_programming_and_academic_packs_have_versioned_metadata():
    assert len(ARCHIVES)==38
    assert len({i.id for i in ARCHIVES})==38
    for item in ARCHIVES:
        assert len(item.sha256)==64 and item.version
        assert Path(item.filename).name==item.filename
