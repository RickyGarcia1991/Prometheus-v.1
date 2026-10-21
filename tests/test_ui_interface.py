import json
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
import pytest
from prometheus_assistant import ui_server as ui
from prometheus_assistant.hardware import HardwareProfile
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.documents import search_documents


@pytest.fixture
def app(tmp_path):
    instance = ui.Interface(tmp_path/'memory.sqlite3')
    yield instance
    instance.close()


def wait(app, job):
    for _ in range(200):
        with app.lock:
            row = app.jobs[job['id']].copy()
        if row['state'] != 'running':
            return row
        time.sleep(.01)
    raise AssertionError('Job did not finish')


def test_real_arithmetic_saves_and_resumes(app):
    first = wait(app, app.submit({'mode':'calculate','prompt':'24 * 17'}))
    assert first['state']=='done'
    assert '408' in first['result']['reply']
    sid=first['result']['session']
    second=wait(app,app.submit({'mode':'calculate','prompt':'1/3','session':sid}))
    assert '1/3' in second['result']['reply']
    with MemoryStore(app.memory_path) as memory:
        assert len(memory.history(sid))==4
        assert memory.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_auto_exact_helper_does_not_start_model(app,monkeypatch):
    monkeypatch.setattr(ui,'detect_hardware',lambda:HardwareProfile(8,8,'Windows',.8))
    monkeypatch.setattr(ui,'installed_models',lambda:{'qwen3:0.6b'})
    result=wait(app,app.submit({'mode':'chat','prompt':'What is 24 times 17?'}))
    assert result['state']=='done'
    assert result['result']['details']['exact']=='408'
    assert result['result']['details']['model_used'] is False


def test_low_ram_returns_cited_evidence_not_generated_claim(app,monkeypatch):
    monkeypatch.setattr(ui,'detect_hardware',lambda:HardwareProfile(8,8,'Windows',.8))
    monkeypatch.setattr(ui,'installed_models',lambda:{'qwen3:0.6b'})
    calls=[]
    def reference(query,profile):
        calls.append((query,profile))
        return {'results':[{'source':'Civil Rules','excerpt':'Example evidence','url':'https://www.uscourts.gov/'}]}
    monkeypatch.setattr(ui,'reference_query',reference)
    result=wait(app,app.submit({'mode':'chat','prompt':'court summons'}))['result']
    assert calls==[('court summons','public')]
    assert 'uscourts.gov' in result['reply'] and 'Low-memory reference' in result['label']
    assert result['details']['routing']['model'] is None


def test_one_job_at_a_time_and_drain(app,monkeypatch):
    entered=threading.Event();release=threading.Event()
    def work(request):
        entered.set();assert release.wait(4);return {'reply':'finished'}
    monkeypatch.setattr(app,'execute',work)
    job=app.submit({'mode':'calculate','prompt':'2+2'})
    assert entered.wait(2)
    with pytest.raises(RuntimeError):app.submit({'mode':'calculate','prompt':'3+3'})
    app.closing=True
    release.set()
    assert wait(app,job)['state']=='done'
    with pytest.raises(RuntimeError):app.submit({'mode':'calculate','prompt':'3+3'})


def test_failure_does_not_poison_next_request(app):
    assert wait(app,app.submit({'mode':'calculate','prompt':'1/0'}))['state']=='error'
    assert wait(app,app.submit({'mode':'calculate','prompt':'6*7'}))['result']['details']['exact']=='42'


@pytest.mark.parametrize('payload',[{'mode':'shell','prompt':'whoami'}, {'mode':'chat','prompt':'x'*4001},
    {'mode':'calculate','prompt':'2+2','session':'../../data'}, {'mode':'coding','prompt':'x','language':'injected'},
    {'mode':'chat','prompt':'x','command':'anything'}, {'mode':'research','prompt':'x','provider':'http://evil.invalid'}])
def test_invalid_contract(app,payload):
    with pytest.raises(ValueError):app.submit(payload)
    assert app.current is None


def test_project_search_reads_multiple_languages_and_skips_dependencies(tmp_path):
    (tmp_path/'hello.py').write_text('def welcome(): return 1')
    (tmp_path/'hello.rs').write_text('fn welcome() {}')
    (tmp_path/'hello.ts').write_text('function welcome() {}')
    (tmp_path/'node_modules').mkdir()
    (tmp_path/'node_modules/bad.js').write_text('welcome')
    result=search_documents(tmp_path,'welcome',include_code=True)
    assert {r['source'] for r in result['results']}=={'hello.py','hello.rs','hello.ts'}
    assert all(r['line']==1 for r in result['results'])
    assert search_documents(tmp_path,'welcome')['results']==[]


def test_http_real_requests_and_boundaries(app):
    server=ui.Server(('127.0.0.1',0),app)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    opener=build_opener(ProxyHandler({}));origin=server.origin
    def request(path,body=None,headers=None):
        payload=None if body is None else json.dumps(body).encode()
        fields={'Content-Type':'application/json','Origin':origin,'X-Prometheus-Token':app.token}
        fields.update(headers or {})
        req=Request(origin+path,data=payload,headers=fields)
        try:
            with opener.open(req,timeout=4) as r:return r.status,r.read(),r.headers
        except HTTPError as e:return e.code,e.read(),e.headers
    try:
        code,raw,headers=request('/')
        assert code==200 and b'Helpers' in raw and 'frame-ancestors' in headers['Content-Security-Policy']
        assert request('/api/status',headers={'Host':'evil.invalid'})[0]==403
        assert request('/api/status',headers={'X-Prometheus-Token':'bad'})[0]==403
        assert request('/api/bootstrap',headers={'Sec-Fetch-Site':'cross-site'})[0]==403
        assert request('/api/jobs',{'mode':'calculate','prompt':'2+2'},headers={'Origin':'https://evil.invalid'})[0]==403
        assert request('/api/jobs',{'mode':'calculate','prompt':'x'*25000})[0]==413
        assert request('/../../memory.sqlite3')[0]==404
        code,raw,_=request('/api/jobs',{'mode':'calculate','prompt':'6*7'})
        assert code==202
        result=wait(app,json.loads(raw))
        assert result['result']['details']['exact']=='42'
        code,raw,_=request('/api/sessions')
        assert code==200 and len(json.loads(raw))==1
        assert request('/api/close',{})[0]==200 and app.closing
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_public_search_contract_is_explicit_online(app,monkeypatch):
    from prometheus_assistant import public_resources
    seen=[]
    def search(query,*,provider,online):
        seen.append((query,provider,online));return {'results':[]}
    monkeypatch.setattr(public_resources,'public_search',search)
    assert wait(app,app.submit({'mode':'research','prompt':'solar','provider':'nasa'}))['state']=='done'
    assert seen==[('solar','nasa',True)]


def test_saved_text_preserves_literal_markup(app):
    result=app.save({'prompt':'<script>test</script>'},{'reply':'<img src=x onerror=bad()>'},'test')
    with MemoryStore(app.memory_path) as memory:
        assert memory.history(result['session'])[1]['content']=='<img src=x onerror=bad()>'
    source=(ui.ASSETS/'app.js').read_text(encoding='utf-8-sig')
    assert '.innerHTML' not in source and 'content.textContent=text' in source


def test_ui_parser_does_not_need_model(monkeypatch,tmp_path):
    from prometheus_assistant import cli
    calls=[]
    monkeypatch.setattr(ui,'serve',lambda path,**options:calls.append(options) or 0)
    assert cli.main(['--memory',str(tmp_path/'m.db'),'ui','--no-browser','--port','0'])==0
    assert calls==[{'port':0,'open_browser':False,'shutdown_request':None}]
