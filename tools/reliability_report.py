"""Non-destructive local reliability smoke test; uses temporary synthetic data only."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.recovery import backup_memory, restore_memory
from prometheus_assistant.research_http import HttpJsonResearchProvider


def run():
    checks = {}
    with tempfile.TemporaryDirectory(prefix='prometheus-reliability-') as folder:
        root = Path(folder)
        start = time.perf_counter()
        with MemoryStore(root / 'live.db') as memory:
            session = memory.create_session('fixture')
            memory.save_exchange(session, 'fixture question', 'fixture answer')
            memory.retain_research(session, 'robot actuator', 'Source: https://example.org/robot; actuator evidence')
        metadata = backup_memory(root / 'live.db', root / 'backup.db')
        restore_memory(root / 'backup.db', root / 'restored.db')
        with MemoryStore(root / 'restored.db') as memory:
            checks['backup_restore'] = len(memory.history(session)) == 2
            checks['research_recall'] = len(memory.research_notes('robot actuator')) == 1
            checks['sqlite_integrity'] = memory.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            checks['trusted_knowledge_boundary'] = memory.knowledge() == []
        checks['research_backup_count'] = metadata.get('research_notes') == 1
        checks['backup_restore_seconds'] = round(time.perf_counter() - start, 4)
    provider = HttpJsonResearchProvider('https://example.org/search', fetch=lambda url: b'{"results":[{"url":"http://unsafe.example/","title":"Unsafe","content":"bad"}]}')
    checks['unsafe_source_rejected'] = len(provider.search('fixture').sources) == 0
    passed = all(value for key, value in checks.items() if key != 'backup_restore_seconds')
    return {'status': 'pass' if passed else 'fail', 'scope': 'synthetic offline fixtures only', 'checks': checks}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional JSON report path')
    args = parser.parse_args()
    report = run()
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    raise SystemExit(0 if report['status'] == 'pass' else 1)
