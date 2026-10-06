import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([sys.executable, str(ROOT / 'prometheus.py'), *map(str, args)],
                          capture_output=True, text=True, encoding='utf-8', timeout=20)


def test_search_works_without_model_and_reports_citations(tmp_path):
    (tmp_path / 'project.txt').write_text('Launch date: November 12.', encoding='utf-8')
    result = run('--base-url', 'http://127.0.0.1:1', 'search', tmp_path, 'launch', '--json')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['results'][0]['line'] == 1
    assert run('search', tmp_path, 'absent').stdout.strip() == 'No matching evidence found.'


def test_backup_commands_work_without_model(tmp_path):
    import sqlite3
    source = tmp_path / 'live.db'
    with sqlite3.connect(source) as db:
        db.executescript('CREATE TABLE sessions(session_id); CREATE TABLE turns(content); PRAGMA user_version=1;')
    backup, restore = tmp_path / 'backup.db', tmp_path / 'restore.db'
    assert run('--memory', source, 'backup-memory', backup).returncode == 0
    assert run('restore-memory', backup, restore).returncode == 0
    assert run('restore-memory', backup, source).returncode == 1
