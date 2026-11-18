"""Offline verification for frozen external-language diagnostic; NO CUDA claims."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import inspect
import json
from pathlib import Path

import pytest

from tools import nanojev_sealed_overlap_external_generalization_bakeoff as lab
from tests.test_nanojev_sealed_overlap_semantic_interface_bakeoff import synthetic_encoder


def context():
    b = lab.binding.load_fixture(lab.binding.FIXTURE)
    s = lab.stress.load_fixture(lab.stress.FIXTURE, b)
    i = lab.interface.load_fixture(lab.interface.FIXTURE, b, s)
    r = lab.repair.load_fixture(lab.repair.FIXTURE, b, i, s)
    bf = lab.blind.load_fixture(lab.blind.FIXTURE, b, s, i, r)
    sf = lab.scope.load_fixture(lab.scope.FIXTURE, b, s, i, r, bf)
    nf = lab.neg.load_fixture(lab.neg.FIXTURE, b, s, i, r, bf, sf)
    f = lab.load_fixture(lab.FIXTURE, b, (s, i, r, bf, sf, nf))
    return b, s, i, r, bf, sf, nf, f


def rows(groups=8):
    b, s, i, r, bf, sf, nf, f = context()
    data = lab.stress.derive_data(b, s, seed=261117, train_atoms=216, selection_groups=16, final_groups=24)
    return lab.make_holdout(data, b, s, i, r, bf, sf, nf, f, seed=261117, groups=groups)


def test_candidate_code_fingerprint_locked_to_previous_gpu_run():
    assert lab.fingerprint_preflight() == lab.FROZEN_MODULES
    assert len(lab.MODULES) == len(lab.FROZEN_MODULES) == 10


def test_immovable_previous_candidate_fingerprint_detects_mutation(monkeypatch):
    monkeypatch.setitem(lab.FROZEN_MODULES, 'nanojev_sealed_overlap_negative_consistency_bakeoff', '0'*64)
    with pytest.raises(ValueError, match='CANDIDATE_SOURCE_DRIFT_REFUSED'):
        lab.fingerprint_preflight()


def test_holdout_fingerprint_predeclared_and_tamper_refused(tmp_path):
    assert sha256(lab.FIXTURE.read_bytes()).hexdigest() == lab.FROZEN_HOLDOUT_SHA256
    src = json.loads(lab.FIXTURE.read_text(encoding='utf8'))
    src['new_entities']['assets'][0] = 'unregistered novelty'
    altered = tmp_path / 'altered_fixture.json'
    altered.write_text(json.dumps(src), encoding='utf8')
    args = lab.parser().parse_args(['--device', 'cpu', '--fixture', str(altered),
                                    '--output', str(tmp_path / 'report.json')])
    with pytest.raises(ValueError, match='EXTGEN_HOLDOUT_CHANGED_REFUSED'):
        lab.run(args)


def test_new_probes_and_external_authorship_not_conflated():
    s = rows()
    assert len(s) == 8*18+10
    assert len({r['text'] for r in s}) == len(s)
    assert sum(r['origin']=='third_party_legal_excerpt' for r in s) == 10
    assert sum(r['origin']=='authored_probe' for r in s) == 144
    assert {r['origin'] for r in s} == {'authored_probe','third_party_legal_excerpt'}
    assert {r['family'] for r in s} == {'known_control','new_vocabulary','new_grammar','nonoperative','external_legal'}
    for r in s:
        assert (r['truth_atoms'] is not None) == (r['target']=='supported')
        assert r['kind'] not in r['text']
        if r['origin']=='third_party_legal_excerpt':
            assert r['source_url'].startswith('https://')
            assert len(lab.binding.split_document(r['text'])) == 1
        else:
            assert len(lab.binding.split_document(r['text'])) == 2


def test_unseen_literal_slots_are_in_gold_without_invented_canonical_mapping():
    b, s, i, r, bf, sf, nf, f = context()
    sample=rows()
    novel=f['new_entities']
    for kind,index,key in [('unknown_actor',0,'actors'),('unknown_recipient',1,'recipients'),
                           ('unknown_asset',2,'assets'),('unknown_condition',3,'conditions'),
                           ('unknown_consequence',4,'consequences')]:
        row=next(x for x in sample if x['kind']==kind)
        assert row['truth_atoms'][0][index] in novel[key]
        assert row['truth_atoms'][0][index] not in b[key]
    allnew=next(x for x in sample if x['kind']=='all_five_new')
    assert all(allnew['truth_atoms'][0][ix] in novel[key] for ix,key in enumerate(
        ['actors','recipients','assets','conditions','consequences']))
    assert lab.exact_graph([('X','Y','Z','C','K',-1)]) != lab.exact_graph([('X','Y','Z','C','K',1)])


def test_fresh_semantic_pairs_do_not_reuse_previous_holdouts():
    b,s,i,r,bf,sf,nf,f=context()
    data=lab.stress.derive_data(b,s,seed=261117,train_atoms=216,selection_groups=16,final_groups=24)
    old=lab.neg.make_fresh(data,b,s,i,r,bf,sf,nf,seed=261117,groups=24)
    new=lab.make_holdout(data,b,s,i,r,bf,sf,nf,f,seed=261117,groups=24)
    a={tuple(sorted(tuple(x[:5]) for x in row['truth_atoms'])) for row in old if row['kind']=='pos_control'}
    q={tuple(sorted(tuple(x[:5]) for x in row['truth_atoms'])) for row in new if row['kind']=='anchor_positive'}
    assert len(q)==24 and not (a & q)
    assert {x['text'] for x in old}.isdisjoint({x['text'] for x in new})


def test_external_real_contracts_are_not_mislabeled_nonoperative():
    for r in rows():
        if r['origin'] == 'third_party_legal_excerpt':
            assert r['target'] == 'abstain'
            assert r['truth_atoms'] is None
            assert 'missing' in r['annotation_scope']
            assert 'non-operative' not in r['annotation_scope'].lower()


def test_failure_to_ground_vocabulary_fails_closed_not_false_negative_duty():
    b, s, i, r, bf, sf, nf, f = context()
    for row in rows():
        if row['family'] in ('new_vocabulary','external_legal','new_grammar','nonoperative'):
            baseline=[tuple(['Mira','Arlen','sensor',b['conditions'][0],b['consequences'][0],1])]*len(lab.binding.split_document(row['text']))
            ok, revised, reasons, _ = lab.neg.grounded_document(row['text'], baseline,b,r['reference_markers'],repair_mode=True)
            assert not ok, row['id']
            assert revised == baseline
            assert reasons


def test_original_canonical_controls_still_accept_on_text_evidence():
    b,s,i,r,bf,sf,nf,f=context()
    rows_sample=rows()
    for row in rows_sample:
        if row['family'] != 'known_control':continue
        guessed=[tuple(['Mira','Arlen','sensor',b['conditions'][0],b['consequences'][0],1])]*2
        ok, revised, reasons, evidence=lab.neg.grounded_document(row['text'],guessed,b,r['reference_markers'],repair_mode=True)
        assert ok,(row['id'],reasons)
        assert lab.exact_graph(revised)==lab.exact_graph(row['truth_atoms'])


def test_metrics_distinguish_safe_abstention_vs_learned_accuracy():
    b,s,i,r,bf,sf,nf,f=context()
    sample=rows()
    preds=[]
    for x in sample:
        preds.append(x['truth_atoms'] if x['truth_atoms'] is not None else [tuple(('Mira','Bela','sensor','the escrow is funded','payment is withheld',1))])
    scored=lab.evaluate(sample,preds,b,r['reference_markers'])
    a=scored['frozen_evidence_repair']['aggregate']
    assert a['supported_total'] > a['supported_accepted'] > 0
    assert a['abstention_required_total'] > 0 and a['abstention_required_accepted'] == 0
    assert a['false_confident_wrong'] == 0
    assert scored['frozen_evidence_repair']['by_family']['external_legal']['selective_graph_exact'] is None
    assert scored['frozen_evidence_repair']['by_family']['external_legal']['coverage'] == 0


def test_evaluation_never_reads_gold_or_kind_to_extract():
    for fn in (lab.neg.grounded_document, lab.neg.observe_clause_fields, lab.neg.predict_baseline):
        text=inspect.getsource(fn)
        assert "['truth_atoms']" not in text and "['kind']" not in text and "['policy']" not in text
    assert 'blind.train_frozen' in inspect.getsource(lab.run)


def test_fixture_mutation_and_prior_audit_rejected(tmp_path):
    b,s,i,r,bf,sf,nf,f=context()
    bad=deepcopy(f)
    bad['authored_challenges'][0]['template']=nf['challenges'][0]['template']
    p=tmp_path/'fixture.json';p.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='EXTGEN_CHALLENGE_INVALID_OR_OVERLAP'):
        lab.load_fixture(p,b,(s,i,r,bf,sf,nf))
    fake={'schema':lab.neg.SCHEMA,'status':lab.neg.STATUS,
          'acceptance':{'no_model_weight_updates':True,'no_final_based_model_selection':True},
          'design':{'view':'final:mean','frozen_training_examples':648},
          'new_holdout':{'arms':{'evidence_repair':{'aggregate':{'documents':408,'supported_accepted':216,'unsafe_accepted':0,'false_confident_wrong':1}}}},
          'historical_diagnostic':{'arms':{'evidence_repair':{'aggregate':{'supported_accepted':216,'false_confident_wrong':0}}}},
          'sources':{k:sha256(v.read_bytes()).hexdigest() for k,v in (
              ('base_fixture_sha256',lab.binding.FIXTURE),('stress_fixture_sha256',lab.stress.FIXTURE),
              ('interface_fixture_sha256',lab.interface.FIXTURE),('repair_fixture_sha256',lab.repair.FIXTURE),
              ('blind_fixture_sha256',lab.blind.FIXTURE),('scope_fixture_sha256',lab.scope.FIXTURE),
              ('new_fixture_sha256',lab.neg.FIXTURE))}}
    prior=tmp_path/'prior.json';prior.write_text(json.dumps(fake))
    with pytest.raises(ValueError,match='EXTGEN_PRIOR_REPORT_NOT_ACCEPTED'):
        lab.validate_prior(prior,{'base_fixture_sha256':lab.binding.FIXTURE})


def test_full_frozen_default_synthetic_execution_and_no_overwrite(tmp_path):
    b,*_=context()
    # Prior is the actual supplied CUDA report; this exercises authentic
    # provenance against the exact fixture SHA-256 and frozen code hashes.
    path=Path('/mnt/data/Pasted text(20261118-032952).txt')
    if not path.exists():
        pytest.skip('The supplied prior Qwen report is not mounted here')
    p=lab.parser().parse_args(['--device','cpu','--prior-report',str(path),
                               '--output',str(tmp_path/'synthetic.json')])
    report=lab.run(p,encoder=synthetic_encoder(b))
    assert report['new_holdout']['frozen_evidence_repair']['aggregate']['documents'] == 442
    assert report['historical_canary']['aggregate']['documents'] == 600
    assert report['source_pins']['module_sha256'] == lab.FROZEN_MODULES
    assert report['design']['source_verified_external_legal_excerpts'] == 10
    assert p.output.exists()
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS_REFUSE_OVERWRITE'):
        lab.run(p,encoder=synthetic_encoder(b))
