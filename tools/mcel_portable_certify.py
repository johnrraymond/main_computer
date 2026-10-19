#!/usr/bin/env python3
"""Admit a live source-bound 19-intent proof into canonical MCEL evidence.

Does not run models or alter source authority. Run mcel_portable_intent_probe.py
with --authorize-external first, *after* applying all source changes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO=Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:sys.path.insert(0,str(REPO))
from main_computer.mcel_portable_certification import (
    DEFAULT_CERTIFICATE, build_certificate,
)


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path,default=REPO)
    parser.add_argument('--proof',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=DEFAULT_CERTIFICATE)
    parser.add_argument('--max-age-hours',type=float,default=168)
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args(argv)
    repo=args.repo_root.resolve()
    try:
        if args.max_age_hours<0:raise ValueError('negative-max-age-hours')
        # Native probe --output is UTF-8; also admit redirected PowerShell UTF-16.
        data=args.proof.read_bytes()
        try: proof=json.loads(data.decode('utf-8-sig'))
        except UnicodeError: proof=json.loads(data.decode('utf-16'))
        certificate=build_certificate(proof,repo,max_age_hours=args.max_age_hours)
    except (ValueError,OSError,TypeError,KeyError) as exc:
        certificate={'schema':'mcel.portable-repository-certification.v1',
                     'status':'refused','passed':False,'eligibleAppIds':[],
                     'verification':{'status':'refused','reasons':[str(exc)]}}
    output=args.output if args.output.is_absolute() else repo/args.output
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(certificate,sort_keys=True,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    if args.json:print(json.dumps(certificate['verification'],sort_keys=True,indent=2))
    else:print(f"portable certificate: {certificate['status']} ({output})")
    return 0 if certificate.get('passed') is True else 4

if __name__=='__main__':raise SystemExit(main())
