"""A single experimental candidate contract for seven real semantic targets.

No production layout rule or application-specific placement table participates.
"""
from __future__ import annotations
from copy import deepcopy
import pytest

from tools.mcel_composition_battery_smoke import SEMANTIC_LOADERS
from main_computer.mcel_slh_application import compile_slh_application
from tools.mcel_general_negotiation_smoke import (
    NegotiationRefusal, negotiate_application, semantic_regions,
)


@pytest.fixture(scope='module')
def targets():
    return {**{k:loader[0]() for k,loader in SEMANTIC_LOADERS.items()},
            'slh-local':compile_slh_application()[0],
            'slh-network':compile_slh_application()[0]}


@pytest.mark.parametrize('name',[
    'calculator','file-explorer','task-manager','code-editor','chat-console',
    'slh-local','slh-network'
])
def test_all_applications_have_negotiable_semantic_regions(targets,name):
    ir=targets[name]
    mode='graphing' if name=='calculator' else None
    small=negotiate_application(ir,390,800,mode=mode)
    large=negotiate_application(ir,1260,800,mode=mode)
    assert small['winner']['arrangement']=='stacked'
    assert large['winner']['arrangement'] in {'two-region','three-region'}
    assert all(r['slot'] in {'navigation','main','delta'} for r in large['regions'])
    assert len(large['regions'])>0 and large['candidateCount']>1
    assert large['productionPromotion'] is False and large['experimental']
    assert large==negotiate_application(ir,1260,800,mode=mode)


def test_schema_not_application_identity_determines_arrangement(targets):
    ir=targets['file-explorer']
    renamed=deepcopy(ir)
    renamed['application']['appId']='unrelated-app'
    for r in renamed['surfaces'][0]['interfaceIntent']['regions']:
        for content in r.get('staticContent',[]):
            content['text']='completely unrelated headings'
    a=negotiate_application(ir,1260)
    b=negotiate_application(renamed,1260)
    assert a['winner']==b['winner']


def test_nested_chat_regions_produce_two_working_tracks(targets):
    regions=semantic_regions(targets['chat-console'])
    assert any(r.slot=='navigation' for r in regions)
    assert any(r.slot=='main' for r in regions)
    assert negotiate_application(targets['chat-console'],1260)['winner']['arrangement']=='two-region'


def test_role_based_slh_regions_preserve_inspection_and_navigation(targets):
    regions=semantic_regions(targets['slh-local'])
    assert any(r.purpose=='inspector' and r.slot=='delta' for r in regions)
    assert any(r.purpose=='navigation' and r.slot=='navigation' for r in regions)
    assert negotiate_application(targets['slh-local'],1260)['winner']['arrangement']=='three-region'


@pytest.mark.parametrize('error,edit',[
    ('unknown semantic parent', lambda r:r.update(parentId='interface-region:made-up')),
    ('cyclic semantic-region hierarchy',lambda r:r.update(parentId=r['id'])),
    ('unbound surface-node member',lambda r:r.update(members=[{'ref':'surface-node:forged'}])),
])
def test_corrupted_semantic_region_refuses_negotiation(targets,error,edit):
    changed=deepcopy(targets['file-explorer'])
    region=changed['surfaces'][0]['interfaceIntent']['regions'][1]
    edit(region)
    with pytest.raises(NegotiationRefusal,match=error):
        negotiate_application(changed,1260)


def test_dangling_semantic_relationship_refused(targets):
    changed=deepcopy(targets['slh-local'])
    changed['surfaces'][0]['interfaceIntent']['relations'][0]['to']={'ref':'interface-region:forged'}
    with pytest.raises(NegotiationRefusal,match='missing region'):
        negotiate_application(changed,1260)


def test_small_or_invalid_surface_is_refused(targets):
    ir=targets['calculator']
    with pytest.raises(NegotiationRefusal):
        negotiate_application(ir,120,800,mode='graphing')
    with pytest.raises(NegotiationRefusal):
        negotiate_application(ir,1260,100,mode='graphing')
    broken=deepcopy(ir)
    broken['surfaces'].append(deepcopy(ir['surfaces'][0]))
    with pytest.raises(NegotiationRefusal):
        negotiate_application(broken,1260,mode='graphing')


def test_zero_new_rules_in_production():
    from pathlib import Path
    # Experiment only; production semantic tools MUST NOT import the trial.
    root=Path(__file__).resolve().parents[1]/'main_computer'
    for name in ('mcel_semantic_html.py','mcel_knowledge/workspace_plan.py',
                 'web/mcel-semantic-layout-inference.js',
                 'web/mcel-semantic-operational-runtime.js'):
        p=root/name
        if p.is_file():
            assert 'mcel_general_negotiation_smoke' not in p.read_text(encoding='utf-8')
