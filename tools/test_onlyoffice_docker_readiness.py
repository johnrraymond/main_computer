"""Deterministic regression tests for ONLYOFFICE's transient 502 startup.

Run: python -m tools.test_onlyoffice_docker_readiness
Does not start Docker or touch production MC.
"""
from __future__ import annotations

import io
import inspect
from urllib.error import HTTPError, URLError

from tools import mcel_onlyoffice_docker_probe as probe


class FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _max_bytes: int):
        return self.body


def exercise(values):
    calls = []
    def fetch(url, timeout):
        assert url == 'http://onlyoffice/healthcheck'
        assert timeout == 4
        value = values[len(calls)]
        calls.append(value)
        if isinstance(value, Exception):
            raise value
        return value
    sleeps = []
    output = probe.wait_for_documentserver_health(
        attempts=len(values), delay_seconds=0.01, fetch=fetch, sleep=sleeps.append)
    return output, calls, sleeps


def main() -> int:
    url = 'http://onlyoffice/healthcheck'
    sequence = [HTTPError(url, 502, 'Bad Gateway', {}, io.BytesIO(b'')),
                HTTPError(url, 503, 'Unavailable', {}, io.BytesIO(b'')),
                FakeResponse(200, b'true\n')]
    output, calls, sleeps = exercise(sequence)
    assert output == (True, 3, 'ready'), output
    assert len(calls) == 3 and sleeps == [0.01, 0.01]
    print('ONLYOFFICE_READINESS_TEST transient-502-503-then-ready PASS')

    output, calls, sleeps = exercise([
        HTTPError(url, 502, 'Bad Gateway', {}, io.BytesIO(b'')) for _ in range(3)])
    assert output == (False, 3, 'HTTP 502'), output
    assert len(calls) == 3 and len(sleeps) == 2
    print('ONLYOFFICE_READINESS_TEST persistent-502-fails-closed PASS')

    output, calls, sleeps = exercise([URLError('connection refused'), FakeResponse(200, b'false'),
                                      FakeResponse(200, b' true ' )])
    assert output == (True, 3, 'ready'), output
    assert len(calls) == 3 and len(sleeps) == 2
    print('ONLYOFFICE_READINESS_TEST network-error-false-then-ready PASS')

    output, calls, sleeps = exercise([FakeResponse(200, b'false')])
    assert output[0] is False and len(calls) == 1 and len(sleeps) == 0
    print('ONLYOFFICE_READINESS_TEST false-health-body-rejected PASS')

    # The precise regression: importing HTTPError inside main() marks it as a
    # local throughout main(), making the readiness exception handler crash.
    source = inspect.getsource(probe.main)
    assert 'from urllib.error import HTTPError' not in source
    assert 'HTTPError' not in probe.main.__code__.co_varnames
    print('ONLYOFFICE_READINESS_TEST no-function-local-httperror-shadow PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
