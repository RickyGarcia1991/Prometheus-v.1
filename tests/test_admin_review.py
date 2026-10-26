import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from prometheus_assistant import admin_review as a


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = Path(self.tmp.name) / 'review'
        self.runner = Mock(return_value=subprocess.CompletedProcess([], 0, '0\n', ''))

    def prepare(self, argv=None):
        return a.prepare(self.queue, 'Test request', 'Verification', 'No changes',
                         json.dumps(argv or ['/usr/bin/id', '-u']))

    def review(self, request, answer='', **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return a.review(self.queue, request['id'], ask=lambda prompt: answer,
                            run=self.runner, check=lambda: None, **kwargs)

    def test_prepare_never_executes(self):
        with patch.object(a.subprocess, 'run') as run:
            r = self.prepare()
        run.assert_not_called()
        self.assertFalse(r['executed'])
        self.assertEqual(a.status(self.queue)[0]['state'], 'pending')

    def test_deny_and_replay_never_execute(self):
        r = self.prepare()
        self.assertEqual(self.review(r), 'denied')
        with self.assertRaises(ValueError):
            self.review(r, 'APPROVE ' + r['sha256'][:12])
        self.runner.assert_not_called()

    def test_exact_approval_uses_fresh_sudo_and_literal_arguments(self):
        r = self.prepare(['/usr/bin/printf', '%s', '$(touch /tmp/never-created); &'])
        self.assertEqual(self.review(r, 'APPROVE ' + r['sha256'][:12]), 'completed')
        args, kw = self.runner.call_args
        self.assertEqual(args[0], ['/usr/bin/sudo', '-k', '--', str(Path('/usr/bin/printf').resolve()), '%s', '$(touch /tmp/never-created); &'])
        self.assertEqual(kw['cwd'], '/')
        self.assertNotIn('shell', kw)
        self.assertNotIn('SUDO_ASKPASS', kw['env'])
        with self.assertRaises(ValueError):
            self.review(r, 'APPROVE ' + r['sha256'][:12])

    def test_incorrect_hash_denied(self):
        r = self.prepare()
        self.assertEqual(self.review(r, 'APPROVE deadbeefdead'), 'denied')
        self.runner.assert_not_called()

    def test_expired_never_executes(self):
        r = self.prepare()
        with a.connect(self.queue) as db:
            db.execute('UPDATE requests SET expires=?', (time.time() - 1,))
        self.assertEqual(self.review(r, 'APPROVE ' + r['sha256'][:12]), 'expired')
        self.runner.assert_not_called()

    def test_tampered_request_rejected(self):
        r = self.prepare()
        with a.connect(self.queue) as db:
            db.execute("UPDATE requests SET digest='bad'")
        with self.assertRaises(ValueError):
            self.review(r, 'APPROVE ' + r['sha256'][:12])
        self.runner.assert_not_called()

    def test_queue_changes_during_review_do_not_change_execution(self):
        r = self.prepare()
        def answer(prompt):
            with a.connect(self.queue) as db:
                db.execute("UPDATE requests SET payload='{}',digest='changed'")
            return 'APPROVE ' + r['sha256'][:12]
        with contextlib.redirect_stdout(io.StringIO()):
            a.review(self.queue, r['id'], ask=answer, run=self.runner, check=lambda: None)
        self.assertEqual(self.runner.call_args.args[0], ['/usr/bin/sudo', '-k', '--', str(Path('/usr/bin/id').resolve()), '-u'])
        self.assertEqual(a.status(self.queue)[0]['digest'], r['sha256'])

    def test_cancel_never_executes(self):
        r = self.prepare()
        with contextlib.redirect_stdout(io.StringIO()):
            state = a.review(self.queue, r['id'], ask=Mock(side_effect=EOFError),
                             run=self.runner, check=lambda: None)
        self.assertEqual(state, 'cancelled')
        self.runner.assert_not_called()

    def test_auth_failure_is_not_success(self):
        r = self.prepare()
        self.runner.return_value = subprocess.CompletedProcess([], 1, '', '')
        self.assertEqual(self.review(r, 'APPROVE ' + r['sha256'][:12]), 'failed')

    def test_root_probe_requires_uid_zero(self):
        r = self.prepare()
        self.runner.return_value = subprocess.CompletedProcess([], 0, '1000\n', '')
        self.assertEqual(self.review(r, 'APPROVE ' + r['sha256'][:12]), 'failed')

    def test_control_characters_and_relative_commands_rejected(self):
        for argv in [['id'], ['/usr/bin/id', '\x1b[2J'], ['/usr/bin/id', 'a\nAPPROVE']]:
            with self.assertRaises(ValueError):
                self.prepare(argv)

    def test_symlink_database_rejected(self):
        self.queue.mkdir()
        target = Path(self.tmp.name) / 'target'
        target.touch()
        (self.queue / 'requests.sqlite3').symlink_to(target)
        with self.assertRaises(OSError):
            self.prepare()

    def test_service_or_nonterminal_cannot_approve(self):
        with patch.object(a.sys.stdin, 'isatty', return_value=False):
            with self.assertRaises(ValueError):
                a.terminal_ready()


if __name__ == '__main__':
    unittest.main(verbosity=2)
