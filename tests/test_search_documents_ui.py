"""Cancellation reaches the real document worker through UI job dispatch."""
import base64
import threading
import time

from prometheus_assistant import markitdown_documents
from prometheus_assistant.document_library import DocumentLibrary
from prometheus_assistant.ui_server import Interface
from test_markitdown_documents import xlsx_bytes


def test_ui_cancel_document_import_reaps_worker_without_persisting(tmp_path, monkeypatch):
    app = Interface(tmp_path/'memory.sqlite3')
    launched = threading.Event()
    processes = []
    original = markitdown_documents.subprocess.Popen
    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        launched.set()
        assert app.cancel_event.wait(3)
        return process
    monkeypatch.setattr(markitdown_documents.subprocess, 'Popen', start)
    try:
        job = app.submit({'mode': 'document_import', 'name': 'cancel.xlsx',
                          'content': base64.b64encode(xlsx_bytes()).decode()})
        assert launched.wait(3)
        assert app.jobs[job['id']]['cancellable']
        app.cancel(job['id'])
        deadline = time.monotonic()+5
        while app.jobs[job['id']]['state'] == 'running' and time.monotonic() < deadline:
            time.sleep(.02)
        assert app.jobs[job['id']]['state'] == 'cancelled'
        assert processes and all(process.poll() is not None for process in processes)
        assert not DocumentLibrary(app.data_root/'Library').list_documents()
    finally:
        app.close()
