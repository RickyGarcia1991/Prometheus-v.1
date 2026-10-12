import json
import subprocess
import sys
import pytest
from prometheus_assistant import archive_worker


def test_query_is_data_for_a_separate_process(monkeypatch):
    original=subprocess.run;seen=[]
    query='literal $(command) "query"'
    def run(argv,**options):
        seen.append((argv,options))
        return original([sys.executable,'-B','-c',
            'import json,sys;value=json.load(sys.stdin);print(json.dumps({"results":[{"text":value["query"]}]}))'],**options)
    monkeypatch.setattr(archive_worker.subprocess,'run',run)
    value=archive_worker.search('resource-root',query)
    assert value['results'][0]['text']==query
    assert query not in ' '.join(seen[0][0])
    assert seen[0][1]['timeout']==90
    assert 'shell' not in seen[0][1]


@pytest.mark.parametrize('output,code,match',[(b'not json',0,'valid result'),(b'{"results":[]}',2,'failed'),
    (b'{"error":"missing archive"}',1,'missing archive'),(b'[]',0,'Invalid archive'),
    (b'{"other":[]}',0,'missing'),(b'x'*(256*1024+1),0,'size limit')],
    ids=['invalid-json','failed-process','reported-error','wrong-type','missing-results','oversized'])
def test_worker_errors_are_bounded(monkeypatch,output,code,match):
    def run(argv,**options):
        options['stdout'].write(output)
        return subprocess.CompletedProcess(argv,code)
    monkeypatch.setattr(archive_worker.subprocess,'run',run)
    with pytest.raises(ValueError,match=match):archive_worker.search('resource-root','query')


def test_worker_timeout_is_clear(monkeypatch):
    def run(argv,**options):raise subprocess.TimeoutExpired(argv,90)
    monkeypatch.setattr(archive_worker.subprocess,'run',run)
    with pytest.raises(ValueError,match='90 seconds'):archive_worker.search('resource-root','query')
