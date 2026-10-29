"""Patch 14.7 cross-viewport real Task Manager capability/fallback verification."""
from __future__ import annotations
from pathlib import Path
from tools.mcel_patch14_7_capability_negotiation_frob import WIDTHS, STATES, SCHEMA, verify


def test_real_task_manager_negotiation_and_fallbacks(tmp_path:Path):
    report=verify(tmp_path/'capability-negotiation',screenshots=False)
    assert report['engineeringStatus']=='PASS',report['errors']
    assert report['schema']==SCHEMA
    assert report['readOnly'] and not report['productionPromotion']
    assert report['operatorActionsExecuted'] is False
    assert len(report['cases'])==len(STATES)*len(WIDTHS)
    assert len(report['fallback'])==len(report['cases'])
    assert len(report['lifecycle'])==len(report['cases'])
    assert len(report['pointer'])==len(WIDTHS)
    assert len(report['scrollFocus'])==len(report['cases'])
    assert all(s['revealed'] and s['mode']=='inline-expand' for s in report['scrollFocus'])
    for case in report['cases']:
        assert case['status']=='PASS',case
        assert case['inlineProof']['mode']=='inline-expand'
        assert case['inlineProof']['detailsInsideList']
        assert case['inlineProof']['canonicalMatch']
        assert case['inlineProof']['copyMatches']
        assert case['inlineProof']['evidenceCollapsed']
        assert case['continuity']['refusesUnprovenIdentity']
        assert case['floatProof']['overlayOutsideScroll']
        assert case['floatProof']['listGeometryStable']
        assert case['fade']['fading'] and case['fade']['closedByUserScroll']
        assert case['originalControls']==5
        assert not case['browserErrors'] and not case['networkRequests']
        assert not case['overflow']
    for trial in report['fallback']:
        assert all(item['ok'] for item in trial['cases'])
        assert trial['rejectsInvalid']
        assert trial['cleanup'] and trial['productionRowsUnchanged']
        assert next(x for x in trial['cases'] if x['label']=='scroll-denied-floating')['actual']=='floating-over-source'
        assert next(x for x in trial['cases'] if x['label']=='scroll-overlay-denied-inline')['actual']=='inline-expand'
        assert next(x for x in trial['cases'] if x['label']=='all-denied')['actual'] is None
    for trial in report['lifecycle']:
        assert all(v for k,v in trial.items() if k not in {'state','width'}),trial
    for trial in report['pointer']:
        assert trial['underPointer'] and trial['escapeCloses']
    assert (tmp_path/'capability-negotiation/report.json').is_file()


def test_contract_is_generic_and_production_remains_opt_in():
    from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS
    engine=(SCRIPTS/'mcel-presentation-negotiation.js').read_text(encoding='utf-8')
    hosts=(SCRIPTS/'mcel-presentation-hosts.js').read_text(encoding='utf-8')
    lab=(SCRIPTS/'mcel-universal-semantic-layout-lab.js').read_text(encoding='utf-8')
    for text in (engine,hosts):
        for forbidden in ('processes','cpu_percent','resident_memory','task-manager','worker-market'):
            assert forbidden not in text.lower()
    assert 'presentationNegotiation:opts.presentationNegotiation===true' in lab
    assert "opts.lab!==true" in lab
    assert "plan.productionPromotion!==false" in lab
