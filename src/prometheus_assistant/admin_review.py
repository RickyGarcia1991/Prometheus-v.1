"""Local, one-command-at-a-time Ubuntu administrator review companion.

The queue and receipt log are user-owned, not a tamper-proof security boundary.
Only Ubuntu sudo authenticates administrator execution. This module never
accepts a password, grants passwordless access, or runs a privileged server.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import stat
import subprocess
import sys
import time
import uuid


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {'title', 'reason', 'risk', 'argv'}:
        raise ValueError('Supply title, reason, risk and argv only.')
    for key in ('title', 'reason', 'risk'):
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError('A bounded, nonempty explanation is required.')
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Control characters are not accepted.')
    argv = payload['argv']
    if not isinstance(argv, list) or not 1 <= len(argv) <= 64:
        raise ValueError('argv must be a nonempty JSON array of at most 64 strings.')
    if any(not isinstance(a, str) or len(a) > 8000 or
           any(ord(c) < 32 or ord(c) == 127 for c in a) for a in argv):
        raise ValueError('Arguments must be bounded strings without control characters.')
    if not argv[0].startswith('/') or len(json.dumps(payload)) > 24000:
        raise ValueError('Use an absolute executable path and a bounded request.')
    return json.loads(json.dumps(payload))


def canonical(payload):
    return json.dumps(validate(payload), sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def connect(queue):
    queue = Path(queue)
    if queue.is_symlink():
        raise ValueError('Queue cannot be a symlink.')
    queue.mkdir(parents=True, exist_ok=True, mode=0o700)
    if queue.stat().st_uid != os.getuid():
        raise ValueError('Queue must belong to the current user.')
    queue.chmod(0o700)
    path = queue / 'requests.sqlite3'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('Queue database must be an ordinary file owned by this user.')
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    db = sqlite3.connect(path, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL, finished REAL, exit_code INTEGER, detail TEXT)')
    db.commit()
    return db


def prepare(queue, title, reason, risk, argv_json):
    payload = canonical(dict(title=title, reason=reason, risk=risk, argv=json.loads(argv_json)))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    rid, now = uuid.uuid4().hex, time.time()
    with connect(queue) as db:
        count = db.execute("SELECT count(*) FROM requests WHERE state='pending' AND expires>?", (now,)).fetchone()[0]
        if count >= 20:
            raise ValueError('Review existing pending requests before preparing more.')
        db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,NULL,NULL,NULL)',
                   (rid, payload, digest, now, now + 86400, 'pending'))
    return {'id': rid, 'sha256': digest, 'state': 'pending', 'executed': False,
            'next_step': 'Open Prometheus Admin Review on the Surface. Review and approve or reject there. Ubuntu authentication is required for execution.'}


def status(queue):
    with connect(queue) as db:
        return [dict(r) for r in db.execute('SELECT id,digest,created,expires,state,finished,exit_code,detail FROM requests ORDER BY created DESC LIMIT 20')]


def privileged_program(argv):
    path = Path(argv[0]).resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('The selected executable is unavailable.')
    for part in (path, *path.parents):
        info = part.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Select a system executable protected from user/group writes.')
    return [str(path), *argv[1:]]


def terminal_ready():
    if os.getuid() == 0 or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('Open the desktop review launcher as your regular Ubuntu user.')
    flags = Path('/proc/self/status').read_text()
    if 'NoNewPrivs:\t1' in flags:
        raise ValueError('Open the desktop review launcher; the application service cannot elevate.')


def review(queue, rid, *, ask=input, run=subprocess.run, check=terminal_ready):
    check()
    with connect(queue) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
        if row is None or row['state'] != 'pending':
            raise ValueError('This request is unavailable or has already been reviewed.')
        text = canonical(json.loads(row['payload']))
        digest = hashlib.sha256(text.encode()).hexdigest()
        if text != row['payload'] or digest != row['digest']:
            raise ValueError('The stored request failed its integrity check.')
        if time.time() >= row['expires']:
            db.execute("UPDATE requests SET state='expired',finished=? WHERE id=?", (time.time(), rid))
            db.commit()
            return 'expired'
        db.execute("UPDATE requests SET state='reviewing' WHERE id=?", (rid,))
        db.commit()  # Consumed before prompting; interrupted reviews are never retried automatically.
    payload = json.loads(text)
    state, code, detail = 'cancelled', None, 'No execution approved.'
    try:
        argv = privileged_program(payload['argv'])
        print('\nPROMETHEUS ADMINISTRATOR REQUEST — Surface Ubuntu')
        print('Request:', rid, '\nSHA-256:', digest)
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        print('Resolved command:', shlex.join(argv))
        print('Runs as root from /. Approval covers this invocation, including its side effects.')
        print('Referenced files, scripts and downloaded packages can affect what it does.')
        print('Enter nothing to reject. Never put passwords in the request.')
        answer = ask('Type APPROVE ' + digest[:12] + ' to proceed: ').strip()
        if answer != 'APPROVE ' + digest[:12]:
            state, detail = 'denied', 'User did not approve the exact request.'
        elif time.time() >= row['expires']:
            state, detail = 'expired', 'Request expired before execution.'
        else:
            # Execute the in-memory snapshot, never re-read mutable queue arguments.
            if privileged_program(payload['argv']) != argv:
                raise ValueError('The executable target changed during review.')
            print('Ubuntu will authenticate you now. Enter your password only at its prompt.')
            env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                   'HOME': str(Path.home()), 'TERM': os.environ.get('TERM', 'xterm-256color')}
            command = ['/usr/bin/sudo', '-k', '--', *argv]
            probe = payload['argv'] == ['/usr/bin/id', '-u']
            result = run(command, cwd='/', env=env, **({'capture_output': True, 'text': True} if probe else {}))
            code = result.returncode
            state = 'completed' if code == 0 else 'failed'
            detail = 'Approved invocation returned exit code ' + str(code) + '. This alone does not verify the intended result.'
            if probe:
                observed = (result.stdout or '').strip()
                detail += ' Root identity probe stdout: ' + json.dumps(observed[:100])
                print('Root identity result:', observed)
                if code == 0 and observed != '0':
                    state = 'failed'
    except (EOFError, KeyboardInterrupt):
        state, detail = 'cancelled', 'Review or execution interrupted; inspect system state before a new request.'
    except Exception as exc:
        state, detail = 'failed', str(exc)[:1000]
    finally:
        with connect(queue) as db:
            db.execute('UPDATE requests SET state=?,finished=?,exit_code=?,detail=?,payload=?,digest=? WHERE id=?',
                       (state, time.time(), code, detail, text, digest, rid))
        print('Result:', state, '—', detail)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue', type=Path, required=True)
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--probe', action='store_true', help='Prepare a harmless root-identity check; does not execute it.')
    args = parser.parse_args()
    if args.list:
        print(json.dumps(status(args.queue), indent=2))
        return
    if args.probe:
        print(json.dumps(prepare(args.queue, 'Verify approved administrator execution',
              'Confirm that an action you approve runs with Ubuntu root privileges.',
              'Read-only identity check. Does not change files, settings, services or accounts.',
              '["/usr/bin/id","-u"]'), indent=2))
        return
    terminal_ready()
    with connect(args.queue) as db:
        rows = list(db.execute("SELECT id,payload,expires FROM requests WHERE state='pending' ORDER BY created"))
    if not rows:
        print('No pending administrator requests. Nothing will run.')
    else:
        print('Prometheus Admin Review — choose a request to inspect; Enter cancels.')
        for i, row in enumerate(rows, 1):
            print(i, json.dumps(json.loads(row['payload'])['title'], ensure_ascii=True))
        choice = input('Request number: ').strip()
        if choice:
            if not choice.isdecimal() or not 1 <= int(choice) <= len(rows):
                raise ValueError('Invalid request number; nothing executed.')
            review(args.queue, rows[int(choice)-1]['id'])
    input('\nPress Enter to close this review window. ')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, sqlite3.Error) as exc:
        print('Administrator review stopped:', exc, file=sys.stderr)
        sys.exit(1)
    except (EOFError, KeyboardInterrupt):
        print('\nCancelled. No further action will be started.')
