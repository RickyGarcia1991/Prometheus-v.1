from pathlib import Path
import json
import statistics
import subprocess
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / 'runtime/python/python.exe'
APP = ROOT / 'prometheus.py'
MEMORY = ROOT / 'evaluation.sqlite3'
cases = [
    ('arithmetic', 'What is 2+2? Answer with only the number.', lambda s: s.strip() == '4'),
    ('conversion', 'Convert 3 hours to minutes. Answer with only the number.', lambda s: s.strip() == '180'),
    ('extraction', 'A project note says: delivery is November 12 and budget is 200 dollars. What is the budget? Answer with only the amount.', lambda s: '200' in s),
    ('glossary', 'Rewrite using simpler words: We utilize the dictionary.', lambda s: 'use' in s.lower() and 'utilize' not in s.lower()),
    ('uncertainty', 'What is my private bank balance? You have no bank records. Answer in one sentence.', lambda s: any(t in s.lower() for t in ['cannot', "don't", 'do not', 'unable', "can't", 'no access'])),
    ('planning', 'Give exactly three numbered steps to back up local project files and test recovery.', lambda s: all(f'{n}.' in s for n in (1, 2, 3))),
]
rows = []
session = None
for name, prompt, check in cases + [('remember', 'Remember this synthetic code: amber-731. Reply with the code only.', lambda s: 'amber-731' in s)]:
    started = time.perf_counter()
    cmd = [str(PYTHON), str(APP), '--memory', str(MEMORY), 'ask', prompt, '--json']
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=150)
    row = {'task': name, 'process_seconds': round(time.perf_counter() - started, 3), 'exit_code': result.returncode}
    if result.returncode == 0:
        value = json.loads(result.stdout)
        row.update(reply=value['reply'], model_seconds=value['elapsed_seconds'], passed=check(value['reply']))
        if name == 'remember': session = value['session_id']
    else: row.update(error=result.stderr, passed=False)
    rows.append(row)
    print(json.dumps(row), flush=True)
if session:
    result = subprocess.run([str(PYTHON), str(APP), '--memory', str(MEMORY), 'ask',
        'What is the synthetic code I asked you to remember? Reply with the code only.',
        '--session', session, '--json'], capture_output=True, text=True, encoding='utf-8', timeout=150)
    value = json.loads(result.stdout) if result.returncode == 0 else {}
    rows.append({'task': 'fresh_process_recall', 'exit_code': result.returncode,
                 'reply': value.get('reply'), 'model_seconds': value.get('elapsed_seconds'),
                 'passed': 'amber-731' in value.get('reply', '')})
opener = build_opener(ProxyHandler({}))
with opener.open('http://127.0.0.1:11434/api/ps', timeout=10) as response:
    loaded = json.load(response)
summary = {'tasks': rows, 'passed': sum(r['passed'] for r in rows), 'total': len(rows),
    'median_model_seconds': statistics.median(r['model_seconds'] for r in rows if 'model_seconds' in r),
    'loaded_models': loaded, 'limitation': 'Small single-run rubric; not a general accuracy score. Model allocation is not peak process RAM.'}
(ROOT / 'evaluation.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
