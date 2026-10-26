import sys
import threading
from pathlib import Path
import pytest
from prometheus_assistant import browser_agent as browser

@pytest.mark.skipif(sys.platform != 'linux', reason='Native Ubuntu browser')
def test_native_public_reader_executes_scripts_and_keeps_request_scope(tmp_path, monkeypatch):
    calls=[]
    def fixture(url, host, event, max_bytes):
        calls.append(url)
        if url.endswith('/data'):return b'Native script verified'
        return b'<html><head><title>Native fixture</title></head><body><p id="answer">loading</p><a href="/more">More</a><a href="https://other.example.org/">Other</a><script>fetch("/data").then(r=>r.text()).then(t=>document.getElementById("answer").textContent=t);fetch("http://127.0.0.1/private").catch(()=>{});</script></body></html>'
    monkeypatch.setattr(browser,'_fetch',fixture)
    result=browser._run_reader(None,'https://example.com/',tmp_path,None)
    assert result['text'].startswith('Native script verified')
    assert calls==['https://example.com/','https://example.com/data']
    assert result['links']==[{'url':'https://example.com/more','text':'More'}]

@pytest.mark.skipif(sys.platform != 'linux', reason='Native Ubuntu browser')
def test_native_cancellation_stops_owned_process_group(tmp_path, monkeypatch):
    event=threading.Event()
    timer=threading.Timer(.2,event.set)
    timer.start()
    try:
        with pytest.raises(RuntimeError,match='cancelled'):
            browser._run_reader(None,'https://example.com/',tmp_path,event)
    finally:
        timer.cancel()
    # Cancellation must return promptly and release the fresh profile.
    browser._cleanup_job(tmp_path)
    assert not tmp_path.exists()

def test_native_status_never_claims_windows_runtime_version(monkeypatch):
    monkeypatch.setattr(browser.sys,'platform','linux')
    monkeypatch.setattr(browser,'_native_chrome',lambda:Path('/os/chrome'))
    row=browser.status(None)
    assert row['available'] is True
    assert row['version']=='native-chromium'
    assert row['browser_version'] is None
    assert row['agentic_tasks_qualified'] is False
