"""Administer the SLH scheduling policy through the authenticated loopback API.

Policy updates are deliberately opt-in. No marketplace wallet or Hub credentials
are read, printed, or sent by this CLI.

  python -m tools.slh_scheduler_admin show --admin-device admin-pc
  python -m tools.slh_scheduler_admin set --admin-device admin-pc \
      --policy-file .\\scheduler-policy.json --execute-mutations
  python -m tools.slh_scheduler_admin status --admin-device admin-pc
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from main_computer.slh.authenticator import sign_object
from main_computer.slh.ports import DEFAULT_SLH_API_PORT
from tools.slh_authenticator_cli import _default_auth_dir, _request, load_identity


def _session(port:int, directory:Path, device_id:str):
    private,_=load_identity(directory,device_id)
    challenge=_request(port,'POST','/v1/auth/challenge',{'device_id':device_id})
    verified=_request(port,'POST','/v1/auth/verify',{
        'device_id':device_id,'challenge_id':challenge['challenge_id'],
        'signature':sign_object(private,challenge)})
    return verified['access_token']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['show','set','status'])
    parser.add_argument('--admin-device',required=True)
    parser.add_argument('--identity-dir',type=Path,default=_default_auth_dir())
    parser.add_argument('--port',type=int,default=DEFAULT_SLH_API_PORT)
    parser.add_argument('--policy-file',type=Path)
    parser.add_argument('--execute-mutations',action='store_true')
    args=parser.parse_args(argv)
    token=None
    try:
        token=_session(args.port,args.identity_dir,args.admin_device)
        if args.command=='show':
            result=_request(args.port,'GET','/v1/admin/scheduler/policy',token=token)
        elif args.command=='status':
            result=_request(args.port,'GET','/v1/admin/scheduler/status',token=token)
        else:
            if not args.policy_file:parser.error('set requires --policy-file')
            changes=json.loads(args.policy_file.read_text(encoding='utf-8'))
            if not isinstance(changes,dict):raise ValueError('Policy file must be a JSON object')
            current=_request(args.port,'GET','/v1/admin/scheduler/policy',token=token)
            if not args.execute_mutations:
                result={'status':'DRY_RUN','expected_revision':current['revision'],
                        'changes':changes,'instruction':'add --execute-mutations to apply'}
            else:
                result=_request(args.port,'PATCH','/v1/admin/scheduler/policy',
                    {'expected_revision':current['revision'],'changes':changes},token=token)
        print(json.dumps(result,indent=2,sort_keys=True))
        return 0
    except (RuntimeError,ValueError,OSError) as exc:
        print(json.dumps({'status':'ERROR','reason':str(exc)}),file=sys.stderr)
        return 1
    finally:
        if token:
            try:_request(args.port,'POST','/v1/auth/logout',{},token=token)
            except (RuntimeError,OSError):pass

if __name__=='__main__':raise SystemExit(main())
