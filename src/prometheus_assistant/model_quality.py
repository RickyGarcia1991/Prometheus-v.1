"""Local measured capability evidence, separate from unverified model claims."""
from pathlib import Path
from contextlib import contextmanager
import sqlite3
import time


class ModelEvidence:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS benchmarks (
                    model TEXT, role TEXT, case_id TEXT, passed INTEGER, seconds REAL,
                    first_token REAL, resident_gib REAL, context INTEGER, checked REAL,
                    PRIMARY KEY(model, role, case_id));
                CREATE TABLE IF NOT EXISTS failures(model TEXT PRIMARY KEY, count INTEGER, last_error TEXT, retry_after REAL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record_benchmark(self, model, role, case_id, *, passed, seconds, first_token=None,
                         resident_gib=None, context=1024):
        if not isinstance(passed, bool) or seconds < 0:
            raise ValueError('Benchmark needs a checked result and nonnegative timing.')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO benchmarks VALUES(?,?,?,?,?,?,?,?,?)',
                       (model, role, case_id, int(passed), seconds, first_token, resident_gib, context, time.time()))

    def record_failure(self, model, error):
        with self.connect() as db:
            db.execute('INSERT INTO failures VALUES(?,1,?,?) ON CONFLICT(model) DO UPDATE SET '
                       'count=count+1,last_error=excluded.last_error,retry_after=excluded.retry_after',
                       (model, str(error)[:400], time.time()+120))

    def record_success(self, model):
        with self.connect() as db:
            db.execute('DELETE FROM failures WHERE model=?', (model,))

    def summary(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            report = {'benchmarks': [dict(r) for r in db.execute(
                'SELECT model,role,COUNT(*) cases,SUM(passed) passed,AVG(seconds) seconds,'
                'AVG(first_token) first_token,MAX(resident_gib) resident_gib,MIN(context) context '
                'FROM benchmarks WHERE checked>? GROUP BY model,role', (time.time()-30*86400,))],
                'failures': [dict(r) for r in db.execute('SELECT * FROM failures WHERE retry_after>?', (time.time(),))],
                'scope': 'Small recorded task checks; not a general accuracy guarantee.'}
        report['paused_roles'] = [dict(model=r['model'], role=r['role'], cases=r['cases'], passed=r['passed'],
            reason='Fewer than 75% of at least four recent task checks passed; automatic use of this role is paused.')
            for r in report['benchmarks'] if r['cases'] >= 4 and r['passed']/r['cases'] < .75]
        return report

    def order(self, names, role):
        report = self.summary()
        blocked = {r['model'] for r in report['failures']}
        blocked.update(r['model'] for r in report['paused_roles'] if r['role'] == role)
        evidence = {r['model']: r for r in report['benchmarks'] if r['role'] == role}
        def score(name):
            row = evidence.get(name)
            measured = bool(row and row['cases'] >= 2)
            # Demonstrated failures rank behind unmeasured candidates; latency breaks quality ties.
            ratio = row['passed']/row['cases'] if measured else .5
            return (-ratio, 0 if measured else 1, row['seconds'] if measured else 0, names.index(name))
        return sorted((n for n in names if n not in blocked), key=score)
