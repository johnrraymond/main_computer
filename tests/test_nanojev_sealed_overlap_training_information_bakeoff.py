"""Offline frozen-data/information bakeoff acceptance, no Qwen or Ollama needed."""
from __future__ import annotations

from hashlib import sha256
import itertools
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_training_information_bakeoff as lab


def sources(tmp_path):
    space=smoke.load_space(smoke.SPACE_DEFAULT)
    axes=[x['id'] for x in space['dimensions']]
    entries=[{'id':f'v{i+1:04d}',
              'description':f'Synthetic diagnostic vocabulary entry number {i+1:04d} about {axes[i%8]} and contract meaning',
              'dimensions':[axes[i%8]]} for i in range(256)]
    # Old growth and exact experiments each used distinct 5/6-axis combinations.
    fives=list(itertools.combinations(axes,5))
    sixes=list(itertools.combinations(axes,6))
    growth_used=fives[:24]+sixes[:12]
    exact_used=fives[24:48]+sixes[12:24]
    base={'schema':smoke.SCHEMA,'status':'COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION',
          'human_space_sha256':smoke.seal(space),'vocabulary':entries[:24],
          'selected_vocabulary_size':24,'candidate_vocabulary_sha256':smoke.seal(entries[:24]),
          'candidate_vocabulary_approved_or_sealed':False,
          'human_dimensions':axes,'model':{'backbone':smoke.MODEL_ID,'frozen':True},
          'calibration':{'train_count':90,'selection_count':24,'final_test_count':36,'seed':41},
          'measurement':{'ridge':.5}}
    growth={'schema':'nanojev.sealed-overlap.vocabulary-growth-bakeoff.v1',
            'acceptance':{'never_authorizes_contract_seal':True},
            'model':{'backbone':smoke.MODEL_ID,'frozen':True},
            'experiment':{'sizes':[24,36,48,72,96],
                          'selection_combinations':[list(x) for x in growth_used[:18]],
                          'final_combinations':[list(x) for x in growth_used[18:]]},
            'all_candidate_vocabulary_unapproved_unsealed':entries[:96]}
    p_base=tmp_path/'base.json';p_growth=tmp_path/'growth.json';p_exact=tmp_path/'exact.json'
    p_base.write_text(json.dumps(base),encoding='utf-8')
    p_growth.write_text(json.dumps(growth),encoding='utf-8')
    e={'schema':lab.exact.SCHEMA,'status':'COMPLETE_EXACT_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION',
       'acceptance':{'never_authorizes_contract_seal':True,'never_updates_model_weights':True},
       'model':{'backbone':smoke.MODEL_ID,'frozen':True},
       'experiment':{'sizes':[96,128,192,256],
                     'selection_combinations':[list(x) for x in exact_used[:18]],
                     'final_combinations':[list(x) for x in exact_used[18:]]},
       'selection':{'selected_entries':256},
       'sources':{'baseline_sha256':sha256(p_base.read_bytes()).hexdigest(),
                  'growth_sha256':sha256(p_growth.read_bytes()).hexdigest(),
                  'human_space_sha256':smoke.seal(space),
                  'retained_vocabulary_96_sha256':smoke.seal(entries[:96])},
       'unapproved_unsealed_candidate_vocabulary':entries,
       'unapproved_vocabulary_sha256':smoke.seal(entries)}
    p_exact.write_text(json.dumps(e),encoding='utf-8')
    return space,p_base,p_growth,p_exact,entries


def test_report_vocabulary_hash_and_source_chain(tmp_path):
    space,b,g,e,entries=sources(tmp_path)
    baseline, prior, vocab, reports=lab.read_sources(e,g,b,space)
    assert vocab==entries and len(reports)==2
    tampered=json.loads(e.read_text());tampered['unapproved_unsealed_candidate_vocabulary'][-1]['description']='malicious change'
    e.write_text(json.dumps(tampered),encoding='utf-8')
    with pytest.raises(ValueError,match='SOURCE_OR_STATUS_MISMATCH'):
        lab.read_sources(e,g,b,space)


def test_reject_tampered_upstream_growth_report(tmp_path):
    space,b,g,e,entries=sources(tmp_path)
    data=json.loads(g.read_text());data['all_candidate_vocabulary_unapproved_unsealed'][0]['description']='changed'
    g.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='SOURCE_OR_STATUS_MISMATCH'):
        lab.read_sources(e,g,b,space)


def test_new_wording_probes_are_disjoint_and_polarized(tmp_path):
    space,b,g,e,entries=sources(tmp_path)
    probes=lab.read_probe_sets(lab.DEFAULT_PROBES,space)
    assert set(probes)=={'train','selection','final'}
    assert len(probes['train']['authority']['positive'])==3
    bad=json.loads(lab.DEFAULT_PROBES.read_text())
    bad['phases']['final']['obligation']['negative'][0]=bad['phases']['train']['obligation']['positive'][0]
    file=tmp_path/'bad.json';file.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='LEXICAL_LEAKAGE'):
        lab.read_probe_sets(file,space)


def test_strict_unused_combinations_selection_final_split(tmp_path):
    space,b,g,e,entries=sources(tmp_path)
    _,_,_,reports=lab.read_sources(e,g,b,space)
    combos,design=lab.unused_holdout_combinations(space,reports,seed=1229,selection_count=10,final_count=11)
    assert design['strict_unused_before_this_bakeoff']==21
    assert len(combos['selection'])==10 and len(combos['final'])==11
    assert not set(map(frozenset,combos['selection']))&set(map(frozenset,combos['final']))
    old={frozenset(x) for r in reports for key in ('selection_combinations','final_combinations') for x in r['experiment'][key]}
    assert all(frozenset(x) not in old for arr in combos.values() for x in arr)
    with pytest.raises(ValueError,match='INSUFFICIENT_STRICT_UNUSED'):
        lab.unused_holdout_combinations(space,reports,seed=5,selection_count=11,final_count=11)


def test_training_contrasts_differ_in_one_axis_and_budgets_are_nested(tmp_path):
    space,b,g,e,entries=sources(tmp_path)
    probes=lab.read_probe_sets(lab.DEFAULT_PROBES,space)
    combos,_=lab.unused_holdout_combinations(space,lab.read_sources(e,g,b,space)[3],seed=1229,selection_count=10,final_count=11)
    forbidden={frozenset(x) for v in combos.values() for x in v}
    tr=lab.build_training_pairs(space,probes,forbidden,seed=9,pair_count=315)
    assert len(tr['paraphrase_only'])==len(tr['paired_contrasts'])==630
    for k in range(0,630,2):
        a,b=tr['paired_contrasts'][k:k+2]
        assert len(set(a['dimensions']) ^ set(b['dimensions'])) == 1
        assert set(b['dimensions'])==set(a['dimensions'])-{a['intervention_axis']}
        p,q=tr['paraphrase_only'][k:k+2]
        assert p['dimensions']==q['dimensions']
        assert p['prompt'] != q['prompt']
        assert frozenset(p['dimensions']) not in forbidden


def test_counterfactual_test_pairs_keep_other_clauses_fixed(tmp_path):
    space,b,g,e,entries=sources(tmp_path)
    probes=lab.read_probe_sets(lab.DEFAULT_PROBES,space)
    combos,_=lab.unused_holdout_combinations(space,lab.read_sources(e,g,b,space)[3],seed=1229,selection_count=10,final_count=11)
    for phase in lab.PHASES:
        rows=lab.build_eval_cases(phase,combos[phase],probes,seed=1229)
        assert len(rows)==4*len(combos[phase])
        assert len(set(r['prompt'] for r in rows))==len(rows)
        for a,negative in zip(rows[::2],rows[1::2]):
            assert a['group_id']==negative['group_id']
            assert set(a['dimensions'])-set(negative['dimensions'])=={a['intervention_axis']}
            assert all(len(r['dimensions'])<=8 for r in (a,negative))


def test_contrast_metric_requires_correct_flip_and_invariance():
    axes=list('abcdefgh')
    rows=[{'group_id':'g00:p0','polarity':'affirmative','intervention_axis':'a','dimensions':['a','b']},
          {'group_id':'g00:p0','polarity':'negated','intervention_axis':'a','dimensions':['b']}]
    correct=np.array([[1,1,0,0,0,0,0,0],[0,1,0,0,0,0,0,0]])
    broken=np.array([[1,1,0,0,0,0,0,0],[1,0,0,0,0,0,0,0]])
    assert lab.contrast_metrics(rows,correct,axes)['both_entire_patterns_correct_fraction']==1.0
    assert lab.contrast_metrics(rows,broken,axes)['target_polarity_recognized_fraction']==0.0
    with pytest.raises(ValueError,match='INVALID_OR_OUT_OF_ORDER'):
        lab.contrast_metrics(rows[::-1],correct,axes)


def test_winner_uses_exact_and_contrast_and_never_final():
    def cell(exact,polarity,balanced):
        return {'exact_semantic_pattern_accuracy':exact,'macro_balanced_accuracy':balanced,
                'macro_information_bits':.1,'contrast':{'target_polarity_recognized_fraction':polarity}}
    grid={('paraphrase_only',90):cell(.02,.2,.99),('paired_contrasts',360):cell(.05,.5,.3),
          ('paired_contrasts',720):cell(.05,.7,.2)}
    winner=lab.choose_winner(grid)
    assert (winner['recipe'],winner['training_examples'])==('paired_contrasts',720)
    grid[('paraphrase_only',90)]['final_exact']=1.0
    assert lab.choose_winner(grid)==winner


def test_training_budgets_validation():
    assert lab.parse_budgets('90,180,360,720')==(90,180,360,720)
    assert lab.parse_budgets('90,94,98')==(90,94,98)
    for value in ['90,93','90,90','100,200','90,88','90,1600','abc']:
        with pytest.raises(ValueError): lab.parse_budgets(value)


def synthetic_encoder(space, probes):
    axes=[d['id'] for d in space['dimensions']]
    positive={a:[x for phase in ('train','selection','final') for x in probes[phase][a]['positive']]
              +space['dimensions'][j]['phrases'] for j,a in enumerate(axes)}
    negative={a:[x for phase in ('train','selection','final') for x in probes[phase][a]['negative']]
              for a in axes}
    def encode(texts):
        out=np.zeros((len(texts),32),dtype=float)
        for i,text in enumerate(texts):
            for j,a in enumerate(axes):
                polarity=(-1. if any(p in text for p in negative[a]) else
                          1. if any(p in text for p in positive[a]) else 0.)
                if a in text and 'Synthetic diagnostic vocabulary entry' in text:
                    polarity=1.
                out[i,j]=polarity
                out[i,8+j]=0.5*polarity
            # Predictable text variation. No knowledge of final labels is used.
            out[i,16+(i%16)]=.03
        return out
    return encode


def test_offline_run_full_grid_selection_locked_and_no_ollama(tmp_path):
    space,b,g,e,_=sources(tmp_path)
    probes=lab.read_probe_sets(lab.DEFAULT_PROBES,space)
    args=lab.parser().parse_args(['--baseline-report',str(b),'--growth-report',str(g),
        '--exact-report',str(e),'--output',str(tmp_path/'report.json'),
        '--budgets','90,94,98','--null-trials','5','--bootstrap-trials','10'])
    report=lab.run(args,encoder=synthetic_encoder(space,probes))
    assert len(report['arms'])==6
    assert report['model']['ollama_called'] is False
    assert report['acceptance']['never_authorizes_contract_seal']
    assert report['selection']['final_labels_used_for_selection'] is False
    assert report['experiment']['fixed_codec_calibrated_on_original_90']
    assert len(report['arms'][0]['cells']['final_vocabulary']['per_dimension_information_bits'])==8
    assert report['arms'][0]['cells']['final_vocabulary']['contrast']['contrast_pairs']==22
    with pytest.raises(FileExistsError): lab.run(args,encoder=synthetic_encoder(space,probes))


def test_import_has_no_provider_call_or_backbone_load():
    assert 'ollama' not in lab.run.__code__.co_names
    assert lab.RECIPES==('paraphrase_only','paired_contrasts')
