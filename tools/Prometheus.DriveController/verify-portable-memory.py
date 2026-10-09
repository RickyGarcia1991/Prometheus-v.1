"""Read-only shutdown integrity check; never creates or repairs a database."""
from pathlib import Path
import sqlite3
import sys


def main(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        return 0 if (db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
                     and not db.execute('PRAGMA foreign_key_check').fetchall()) else 3
    finally:
        db.close()


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1]))
    except (OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(3)
