"""Network-target SLH application: distinct MCEL semantic app and read-only port."""
from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition, verify_composition
from main_computer.mcel_slh_target_navigation import TAB_STYLE, target_tabs

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('mcel_apps/slh/network/application.js')
STYLE=Path('main_computer/web/applications/styles/mcel-semantic-workbench.css')
ASSETS=(
    'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js',
    'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js',
    'main_computer/web/applications/scripts/mcel-semantic-slh-network-ports.js',
)


def compile_network_slh_application(repo:Path=ROOT):
    if (repo/SOURCE).with_name('application.html').exists():
        raise ValueError('Network SLH is semantic-only; authored layout prohibited')
    compiled=compile_dsl_application(repo/SOURCE)
    if not compiled.valid or compiled.normalized_ir is None:
        raise ValueError('Network SLH semantic source invalid: '+str(compiled.diagnostics))
    ir=compiled.normalized_ir
    if (ir['application']['appId']!='slh-network' or ir.get('layouts') or
        {c['id'] for c in ir['capabilities']}!={'capability:network-slh-identity'} or
        {i['id'] for i in ir['intents']}!={'intent:inspect-network-slh'} or
        any(x['risk']!='external-read' for x in ir['effects'])):
        raise ValueError('Network SLH source exceeds read-only identity authority')
    composition=synthesize_composition(ir)
    verify_composition(ir,composition)
    return ir,composition


def render_network_slh_page(repo:Path=ROOT)->str:
    """Canonical remote URL mounts the same SLH semantic application as local."""
    from main_computer.mcel_slh_application import render_slh_page
    return render_slh_page("network", repo)
