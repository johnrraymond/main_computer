"""Deterministic off-Docker harness checks, including an intentional broken guard.

    python -m tools.test_mcel_security_correction_lab_offline
"""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
import secrets
import tempfile
from tools import mcel_security_correction_lab as lab


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='mc-security-correction-test-') as temp:
        work=Path(temp)
        baseline,candidate,hashes=lab.generate(lab.ROOT,work)
        assert len(hashes)==len(lab.MODULES)==5
        for name in lab.MODULES:
            assert hashlib.sha256((baseline/name).read_bytes()).hexdigest()==hashes[name]['baseline']
            assert hashlib.sha256((candidate/name).read_bytes()).hexdigest()==hashes[name]['candidate']
            ast.parse((candidate/name).read_text())
        print('MC_CORRECTION_SELFTEST anchored-source-copies PASS',flush=True)
        result=lab.execute_local(work,secrets.token_urlsafe(40))
        if result!=0:
            print('MC_CORRECTION_SELFTEST original-candidate FAIL',flush=True)
            return 1
        print('MC_CORRECTION_SELFTEST original-candidate PASS',flush=True)
        browser=candidate/'mcel_backend_browser_route.py'
        text=browser.read_text()
        guard='''    if not (isinstance(expected, str) and len(expected) >= 32 and
            isinstance(supplied, str) and hmac.compare_digest(expected, supplied)):
        return False
'''
        assert text.count(guard)==1
        browser.write_text(text.replace(guard,'    if False:\n        return False\n',1),encoding='utf-8')
        result=lab.execute_local(work,secrets.token_urlsafe(40))
        if result!=1:
            print('MC_CORRECTION_SELFTEST negative-control-not-detected FAIL exit='+str(result),flush=True)
            return 1
        print('MC_CORRECTION_SELFTEST broken-guard-negative-control PASS',flush=True)
    print('MC_CORRECTION_SELFTEST SUMMARY {"status":"PASS","production_modified":false}',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
