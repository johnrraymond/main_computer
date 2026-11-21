"""Read-only smoke for template selection in the canonical MCEL layout pathway."""
from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_knowledge.semantic_template_pipeline import (
    plan_semantic_workspace,
    verify_semantic_workspace_plan,
)
from main_computer.mcel_knowledge.workspace_plan import plan_workspace
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page

ROOT = Path(__file__).resolve().parents[1]


def run(repo: Path = ROOT) -> dict:
    ir = compile_semantic_app('code-editor', repo)
    plan = plan_semantic_workspace(ir, repo)
    verify_semantic_workspace_plan(ir, plan, repo)
    binding = plan['semanticTemplate']
    arguments = binding['arguments']
    if binding['authority'] != 'canonical-ir-and-deterministic-graph-match':
        raise ValueError('Direct graph matching not used by main pathway')
    groups = {key:len(nodes) for key,nodes in arguments['groups'].items()}
    if (binding['profile'] != 'code' or arguments['grouping'] != 'inferred'
            or groups != {'execution':2,'preview':2,'session':7}):
        raise ValueError('Unexpected canonical Code Editor template binding')
    if 'mcel.semantic-template-binding.v1' not in render_semantic_page('code-editor',repo,promoted=True):
        raise ValueError('Native Code Editor route omitted template plan')
    others=[]
    for app in ('file-manager','task-manager'):
        other_ir=compile_semantic_app(app,repo)
        if plan_semantic_workspace(other_ir,repo) != plan_workspace(other_ir):
            raise ValueError('Unrelated application plan changed: '+app)
        others.append(app)
    return {'schema':'mcel.semantic-layout-pipeline-smoke.v1',
            'status':'PASS_DETERMINISTIC_MAIN_PATH',
            'app':'code-editor','template':binding['profile'],
            'arguments':sum(groups.values()),'groups':groups,
            'otherApplicationPlansUnchanged':others,
            'graphDerivedSelection':True,'spectralRuntimeGate':False,'authorizesEffects':False,'writesFiles':False}


if __name__=='__main__':
    report=run()
    print('MCEL_SEMANTIC_LAYOUT_PIPELINE PASS arguments='+str(report['arguments']))
    print(json.dumps(report,indent=2,sort_keys=True))
