"""Actual cross-process, durable MC session/AAA-file lifecycle acceptance.

All state is newly created under TemporaryDirectory. Separate Python processes
exercise the unchanged repository's BootstrapAuthority, not a mock. This does
NOT simulate a different Windows SID or a browser hostname handoff.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from main_computer import auth_bootstrap as auth


def child(action):
    root=Path(os.environ['MC_PRECUTOVER_TEST_ROOT'])
    token=os.environ.get('MC_PRECUTOVER_TEST_TOKEN','')
    authority=auth.BootstrapAuthority(root)
    if action=='valid':result=authority.sessions.valid(token)
    elif action=='revoke':authority.sessions.revoke(token);result=True
    elif action=='issue':result=authority.sessions.issue()
    elif action=='all':authority.sessions.revoke_all();result=True
    elif action=='generation':result=authority.sessions.current_generation()
    else:raise ValueError('unknown child operation')
    print('MC_OO_SESSION_CHILD '+json.dumps({'value':result}),flush=True)
    return 0


def run():
    evidence=[]
    def check(name,ok):
        evidence.append({'name':name,'status':'PASS' if ok else 'FAIL'})
        print('MC_OO_SESSIONS '+name+' '+('PASS' if ok else 'FAIL'),flush=True)
    with tempfile.TemporaryDirectory(prefix='mc-onlyoffice-sessions-') as tmp:
        root=Path(tmp)/'isolated-authority'
        auth.provision(root)
        initial=auth.BootstrapAuthority(root)
        def spawn(action, token=''):
            env=dict(os.environ,MC_PRECUTOVER_TEST_ROOT=str(root),MC_PRECUTOVER_TEST_TOKEN=token)
            started=time.monotonic()
            # Each child constructs the real authority and performs Windows
            # ACL checks. Per-child and suite deadlines are separate; neither
            # can silently turn a hung child into a passing test.
            try:
                result=subprocess.run([sys.executable,'-m','tools.mcel_onlyoffice_pre_cutover_sessions','--child',action],
                    cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
            except subprocess.TimeoutExpired as exc:
                print(f'MC_OO_SESSIONS child-{action} TIMEOUT elapsed={time.monotonic()-started:.1f}s limit=120s',flush=True)
                raise RuntimeError('SESSION_CHILD_TIMEOUT action='+action) from exc
            elapsed=time.monotonic()-started
            print(f'MC_OO_SESSIONS child-{action} COMPLETED elapsed={elapsed:.1f}s exit={result.returncode}',flush=True)
            if result.returncode:
                raise RuntimeError('child '+action+' failed '+(result.stderr+' '+result.stdout)[-600:])
            items=[line for line in result.stdout.splitlines() if line.startswith('MC_OO_SESSION_CHILD ')]
            if len(items)!=1:raise RuntimeError('child '+action+' result missing; '+result.stdout[-400:])
            return json.loads(items[0].removeprefix('MC_OO_SESSION_CHILD '))['value']
        token=initial.sessions.issue()
        check('session-valid-in-independent-process',spawn('valid',token) is True)
        check('forged-session-invalid-in-independent-process',spawn('valid','forged-token-value-of-correct-length-12345') is False)
        token2=spawn('issue')
        check('independent-process-session-visible-to-original',initial.sessions.valid(token2))
        check('original-session-survives-independent-issuance',initial.sessions.valid(token))
        check('session-survives-reconstructed-auth-process',spawn('valid',token) is True)
        # Concurrent session writers must not erase each other's commits.
        print('MC_OO_SESSIONS concurrent-issuance START workers=4 processes=8 per_child_limit=120s',flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            produced=list(pool.map(lambda _:spawn('issue'),range(8)))
        print('MC_OO_SESSIONS concurrent-issuance COMPLETED processes=8',flush=True)
        check('eight-concurrent-process-issuances-unique',len(set(produced))==8)
        check('concurrent-issued-sessions-all-persist',all(initial.sessions.valid(t) for t in produced))
        spawn('revoke',token)
        check('cross-process-revocation-propagates',not initial.sessions.valid(token))
        check('revoke-does-not-delete-other-sessions',all(initial.sessions.valid(t) for t in produced))
        # Generations are durable. AAA deletion must wipe the current store
        # and reissue without restoring any existing session.
        before=spawn('generation')
        (root/auth.PROOF_NAME).unlink()
        check('proof-delete-revokes-eight-sessions',not any(initial.sessions.valid(t) for t in produced))
        check('missing-proof-reissued-using-original-key',(root/auth.PROOF_NAME).exists())
        after=spawn('generation')
        check('proof-reissue-changes-generation',bool(before and after and before!=after))
        check('revoked-old-session-stays-invalid-in-new-process',spawn('valid',produced[0]) is False)
        fresh=spawn('issue')
        check('new-authenticated-session-after-reissue',initial.sessions.valid(fresh))
        spawn('all')
        check('global-revocation-visible-in-all-processes',not initial.sessions.valid(fresh))
    failed=[x['name'] for x in evidence if x['status']=='FAIL']
    summary={'status':'PASS' if not failed else 'FAIL','checks':len(evidence),
        'passed':len(evidence)-len(failed),'failed':failed,'processes_separate':True,
        'same_windows_sid':True,'production_modified':False}
    print('MC_OO_SESSIONS SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    return summary


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--child',choices=['valid','revoke','issue','all','generation'])
    args=p.parse_args(argv)
    if args.child:return child(args.child)
    return 0 if run()['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
