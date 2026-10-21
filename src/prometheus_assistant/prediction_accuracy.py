"""Bounded observational forecast/outcome pairs; never admission tuning."""
from contextlib import closing
from pathlib import Path
import sqlite3
import re

METRICS = ('direct_ram_gib', 'direct_cpu_seconds', 'job_cpu_seconds')


def number(value):
    return type(value) in (int, float) and 0 <= value <= 1e300


class AccuracyHistory:
    def __init__(self, path, limit=500):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Invalid accuracy history bound')
        self.path, self.limit = Path(path), limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS accuracy (id INTEGER PRIMARY KEY, run TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, direct_ram_gib_pred REAL, direct_ram_gib_actual REAL, direct_cpu_seconds_pred REAL, direct_cpu_seconds_actual REAL, job_cpu_seconds_pred REAL, job_cpu_seconds_actual REAL)')

    def record(self, run, kind, forecasts, actuals):
        if not isinstance(run,str) or not re.fullmatch('[0-9a-f]{32}',run):
            raise ValueError('Invalid opaque run identity')
        if not isinstance(kind,str) or not re.fullmatch('[a-z0-9_.-]{1,64}',kind):
            raise ValueError('Invalid anonymous category')
        comparisons = {}
        values=[]
        for metric in METRICS:
            predicted, actual = forecasts.get(metric),actuals.get(metric)
            if number(predicted) and number(actual):
                comparisons[metric] = dict(predicted=predicted,actual=actual,
                    absolute_error=abs(predicted-actual),underpredicted=actual>predicted)
                values.extend((predicted,actual))
            else:
                values.extend((None,None))
        if not comparisons:
            return dict(recorded=False,reason='No same-scope forecast and complete measurement pair.')
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor=db.execute('INSERT OR IGNORE INTO accuracy(run,kind,direct_ram_gib_pred,direct_ram_gib_actual,direct_cpu_seconds_pred,direct_cpu_seconds_actual,job_cpu_seconds_pred,job_cpu_seconds_actual) VALUES (?,?,?,?,?,?,?,?)',(run,kind,*values))
            inserted=cursor.rowcount==1
            db.execute('DELETE FROM accuracy WHERE id NOT IN (SELECT id FROM accuracy ORDER BY id DESC LIMIT ?)',(self.limit,))
        return dict(recorded=inserted,comparisons=comparisons,policy_tuned=False)

    def summary(self,kind):
        with closing(sqlite3.connect(self.path)) as db, db:
            rows=db.execute('SELECT direct_ram_gib_pred,direct_ram_gib_actual,direct_cpu_seconds_pred,direct_cpu_seconds_actual,job_cpu_seconds_pred,job_cpu_seconds_actual FROM accuracy WHERE kind=? ORDER BY id DESC LIMIT ?',(kind,self.limit)).fetchall()
        output={}
        for index,metric in enumerate(METRICS):
            pairs=[(row[index*2],row[index*2+1]) for row in rows if row[index*2] is not None and row[index*2+1] is not None]
            output[metric]=dict(samples=len(pairs),mean_absolute_error=sum(abs(p-a)/len(pairs) for p,a in pairs) if pairs else None,underpredictions=sum(a>p for p,a in pairs))
        return output
