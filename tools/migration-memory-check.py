import sqlite3
import sys

uri = 'file:' + sys.argv[1].replace('\\', '/') + '?mode=ro'
with sqlite3.connect(uri, uri=True, timeout=5) as connection:
    print(connection.execute('PRAGMA quick_check').fetchone()[0])
