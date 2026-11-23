"""Run prior ONLYOFFICE/gateway security regression modules with an explicit verdict."""
from __future__ import annotations
import json
import unittest

MODULES=(
    'tools.test_onlyoffice_docker_lab_offline',
    'tools.test_mcel_security_unified_session_offline',
    'tools.test_mcel_security_hostname_smoke',
    'tools.test_mcel_security_correction_lab_offline',
)

def main():
    suite=unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    status='PASS' if result.wasSuccessful() and result.testsRun>=13 else 'FAIL'
    print('MC_OO_EXISTING_REGRESSIONS SUMMARY '+json.dumps({
        'status':status,'tests':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),
        'production_modified':False},sort_keys=True),flush=True)
    return 0 if status=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
