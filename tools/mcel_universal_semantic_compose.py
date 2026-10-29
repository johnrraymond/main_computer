"""Inspect Patch 14.3 universal composition without starting any web application.

Example:
 python -m tools.mcel_universal_semantic_compose \
   --observation task-manager.processes --purpose monitor \
   --snapshot tools/fixtures/mcel_computer_knowledge/task_manager_snapshot.v1.json \
   --output runtime/reports/mcel-universal-task-manager.json

No network, writes to Main Computer services, active effects or UI promotion.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from main_computer.mcel_knowledge import SemanticCatalogError
from main_computer.mcel_knowledge.ir_bridge import compile_knowledge_ir, resolve_knowledge
from main_computer.mcel_knowledge.universal_layout import compose, PURPOSES


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation',required=True)
    parser.add_argument('--purpose',required=True,choices=sorted(PURPOSES))
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--bindings',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    try:
        snapshot=json.loads(args.snapshot.read_text(encoding='utf-8'))
        bindings=json.loads(args.bindings.read_text(encoding='utf-8')) if args.bindings else {}
        compiled=compile_knowledge_ir('universal-semantic-lab',observation_ids=[args.observation],bindings=bindings)
        view=resolve_knowledge(snapshot,compiled)
        plan=compose(view,args.observation,purpose=args.purpose)
        encoded=json.dumps(plan,indent=2,sort_keys=True,ensure_ascii=False)+'\n'
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(encoded,encoding='utf-8')
            print('MCEL_UNIVERSAL_SEMANTIC_COMPOSE_PASS',args.output)
        else:
            print(encoded,end='')
        return 0
    except (SemanticCatalogError,ValueError,TypeError,OSError,KeyError) as exc:
        print('MCEL_UNIVERSAL_SEMANTIC_COMPOSE_REFUSED:',exc)
        return 3

if __name__=='__main__':raise SystemExit(main())
