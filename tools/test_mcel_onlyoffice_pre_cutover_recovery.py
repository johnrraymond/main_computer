"""Negative controls for the disposable Windows proof-reissue test deadline."""
import io
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout

from tools.mcel_onlyoffice_pre_cutover_recovery import (
    await_reissue_http, stop_disposable_fixture,
)


class RecoveryWaitContract(unittest.TestCase):
    def test_completed_operation_returns_actual_http_result(self):
        with redirect_stdout(io.StringIO()) as log:
            result = await_reissue_http(lambda: (401, {'Proof': 'reissued'}),
                label='TEST', deadline_seconds=0.5, progress_seconds=0.1)
        self.assertEqual(result, (401, {'Proof': 'reissued'}))
        self.assertIn('COMPLETED', log.getvalue())

    def test_delayed_operation_is_observable_not_silently_accepted(self):
        def delayed():
            time.sleep(0.11)
            return 401
        with redirect_stdout(io.StringIO()) as log:
            result = await_reissue_http(delayed, label='TEST',
                deadline_seconds=1, progress_seconds=0.04)
        self.assertEqual(result, 401)
        self.assertIn('WAITING', log.getvalue())

    def test_request_error_never_counts_as_success(self):
        def broken():
            raise OSError('deliberate network failure')
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'REQUEST_FAILED error=OSError'):
                await_reissue_http(broken, label='TEST',
                    deadline_seconds=0.3, progress_seconds=0.1)

    def test_deadline_fails_closed(self):
        def blocked():
            time.sleep(0.2)
            return 200
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(TimeoutError, 'TIMEOUT.*certification BLOCKED'):
                await_reissue_http(blocked, label='TEST',
                    deadline_seconds=0.05, progress_seconds=0.02)

    def test_disposable_child_stopped_before_cleanup(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            stop_disposable_fixture(process, grace_seconds=3)
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
