"""Compatibility test entry: template discovery must not consult metrics."""
import copy
from pathlib import Path
from unittest.mock import patch
import sys
import pytest
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from main_computer.mcel_knowledge.workspace_plan import plan_workspace
from main_computer.mcel_knowledge.semantic_template_pipeline import plan_semantic_workspace
from main_computer.mcel_knowledge.code_argument_resolver import match_code_workbench

ROOT=Path(__file__).resolve().parents[1]

def test_graph_match_independent_of_metric_census_and_policy_files(tmp_path):
    ir=compile_semantic_app('code-editor',ROOT)
    with patch.dict(sys.modules, {'main_computer.mcel_knowledge.metric_census': None}):
        plan=plan_semantic_workspace(ir,tmp_path)
    assert plan['semanticTemplate']['profile']=='code'
    assert plan['semanticTemplate']['authority']=='canonical-ir-and-deterministic-graph-match'
    assert len(plan['semanticTemplate']['arguments']['groups']['session'])==7

def test_ir_comparison_applications_keep_existing_plans():
    for app in ('file-manager','task-manager'):
        ir=compile_semantic_app(app,ROOT)
        assert plan_semantic_workspace(ir,ROOT)==plan_workspace(ir)

def test_metric_count_variations_do_not_select_template():
    ir=compile_semantic_app('code-editor',ROOT)
    original=plan_workspace(ir)
    damaged=copy.deepcopy(ir)
    damaged['surfaces'][0]['nodes'].append(copy.deepcopy(next(n for n in damaged['surfaces'][0]['nodes'] if n['nodeKind']=='document-editor')))
    damaged['surfaces'][0]['nodes'][-1]['id']='surface-node:extra-editor'
    with pytest.raises(ValueError):
        match_code_workbench(damaged,original)

def test_no_external_profile_file_needed(tmp_path):
    ir=compile_semantic_app('code-editor',ROOT)
    plan=plan_semantic_workspace(ir,tmp_path)
    assert plan['semanticTemplate']['arguments']['grouping']=='inferred'
