"""Synthetic invariants for numerical-distance microscope; no CUDA/download."""
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_number_distance as distance


def _synthetic(kind='shared',contexts=12,width=30,seed=74):
    rng=np.random.default_rng(seed)
    v=np.arange(-8,9,dtype=np.int32)
    form_count=len(distance.FORMS)
    offsets=rng.normal(size=(contexts,form_count,1,width))*1.5
    dirs=rng.normal(size=(contexts,form_count,1,width))
    basis=rng.normal(size=width);basis/=np.linalg.norm(basis)
    a=np.zeros((contexts,form_count,len(v),width),dtype=np.float32)
    for g in range(contexts):
        for f in range(form_count):
            for k,num in enumerate(v):
                if kind=='shared':extra=float(num)*basis
                elif kind=='context':extra=float(num)*dirs[g,0,0]
                elif kind=='lexical':extra=rng.normal(size=width)*3 # arbitrary token identity
                elif kind=='sign':
                    plus=basis;minus=np.roll(basis,1)
                    extra=float(num)*(plus if num>0 else minus)
                else:raise ValueError(kind)
                a[g,f,k]=offsets[g,f,0]+extra
    counts=np.full(a.shape[:3],40,dtype=np.int32)
    games=[f'game_{i}' for i in range(contexts)]
    return a,counts,v,games


def test_format_variants_and_explicit_zero_spellings():
    assert distance.numeral(-6,'digits')=='-6'
    assert distance.numeral(6,'digits')=='6'
    assert distance.numeral(6,'signed')=='+6'
    assert distance.numeral(0,'signed')=='+0'
    assert distance.numeral(0,'digits')=='0'
    assert distance.numeral(-6,'words')=='minus six'
    assert distance.numeral(0,'words')=='zero'
    assert distance.substitute('ship has 17 cargo slots', (9,11),-3,'words')=='ship has minus three cargo slots'
    with pytest.raises(ValueError):distance.numeral(30,'words')


def test_shared_number_line_transfers_games_words_signs_and_zero():
    a,c,v,g=_synthetic()
    result=distance.analyze_displacements(a,c,v,g,folds=4,controls=2)
    for stage in ('heldout_games','heldout_words','positive_digits_to_negative_words',
                  'negative_digits_to_positive_words','unseen_zero_crossing_words'):
        assert result[stage]['status']=='COMPLETE',stage
        assert result[stage]['r2_vs_zero']>.99,(stage,result[stage])
    geom=distance.geometry_checks(a,c,v)
    assert geom['adjacent_step_cosine']>.99
    assert geom['zero_midpoint_curvature']<.01


def test_context_specific_directions_fail_heldout_games():
    a,c,v,g=_synthetic('context')
    result=distance.analyze_displacements(a,c,v,g,folds=4,controls=0)
    assert result['heldout_games']['r2_vs_zero']<.75


def test_lexical_identity_does_not_transfer_to_word_spellings():
    a,c,v,g=_synthetic('lexical')
    result=distance.analyze_displacements(a,c,v,g,folds=4,controls=0)
    assert result['heldout_words']['r2_vs_zero']<.5


def test_sign_specific_representation_fails_sign_transfer():
    a,c,v,g=_synthetic('sign')
    result=distance.analyze_displacements(a,c,v,g,folds=4,controls=0)
    assert result['positive_digits_to_negative_words']['r2_vs_zero']<.9


def test_unmatched_token_counts_excluded_and_control_works():
    a,c,v,g=_synthetic()
    c[:,2,:]+=np.arange(len(v))
    r=distance.analyze_displacements(a,c,v,g,controls=0)
    assert r['heldout_words']['status']=='INSUFFICIENT_MATCHED_PAIRS'
    assert r['heldout_words']['excluded_token_mismatch']>0
    assert r['heldout_games']['cases']>0


def test_constant_vector_has_no_spurious_number_line():
    a,c,v,g=_synthetic()
    a[:]=0.0
    r=distance.analyze_displacements(a,c,v,g,controls=3)
    assert r['heldout_words']['r2_vs_zero']==0.0
    assert r['heldout_words']['sign_accuracy']==0
    geom=distance.geometry_checks(a,c,v)
    assert geom['adjacent_step_cosine'] is None


def test_offline_report_with_source_immutable(tmp_path):
    a,c,v,g=_synthetic(contexts=8,width=8)
    src=tmp_path/'capture';src.mkdir()
    report={'schema':distance.SCHEMA,'status':'COMPLETE_READ_ONLY',
            'source_run':str(tmp_path/'source'),'expanded_run':str(tmp_path/'expanded'),
            'contexts':[{'game_id':x} for x in g]}
    (src/'final_report.json').write_text(json.dumps(report))
    (src/'interventions.jsonl').write_text('dummy\n')
    np.savez_compressed(src/'activations.npz',values=v,token_counts=c,
        **{stage:a for stage in distance.STAGES})
    initial={p.name:p.read_bytes() for p in src.iterdir()}
    result=distance.analyze_capture(src,folds=4,controls=0)
    assert result['status']=='COMPLETE_READ_ONLY'
    assert all(p.read_bytes()==initial[p.name] for p in src.iterdir())
    with pytest.raises(ValueError,match='OVERLAPS'):
        distance.analyze_capture(src,output=src/'analysis',folds=4,controls=0)


def test_cli_exposes_new_command_and_rejects_training():
    from tools.nanojev_gamer_v1_staircase import parser,main
    p=parser().parse_args(['head-100-number-distance','--run','X',
                             '--distance-contexts','24','--distance-width','8'])
    assert (p.distance_contexts,p.distance_width)==(24,8)
    with pytest.raises(ValueError,match='READ_ONLY'):
        main(['head-100-number-distance','--run','X','--steps','1'])
    with pytest.raises(ValueError,match='ONLY_NUMBER_DISTANCE'):
        main(['head-100-radius','--run','X','--distance-width','9'])


def test_capture_to_analysis_smoke_without_weights_or_cuda(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    import re
    from types import SimpleNamespace
    from tools import nanojev_gamer_v1_radius_activations as radius
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_expanded_head as exp
    from tools import nanojev_gamer_v1_expanded_diagnostic_train as expdiag
    from tools import nanojev_gamer_v1_diagnostic_train as diag
    expanded=tmp_path/'expanded';expanded.mkdir()
    source=tmp_path/'source';source.mkdir()
    adapter=tmp_path/'adapter';adapter.mkdir()
    (expanded/'provenance.json').write_text('{}')
    (expanded/'best_model.pt').write_bytes(b'checkpoint')
    (source/'certified_pairs.jsonl').write_text('certified')
    cfg={'samples':100,'curvature':4.0,'max_length':512,'input_width':8,'hidden':[6,5,4,3]}
    identity={'model':'test-fake','configuration':cfg,'adapter_sha256':'FAKE'}
    rows=[{'game_id':f'game_{i}','case_sha256':f'{i}','split':'dev',
           'prompt_08':f'Ship has 17 cargo slots. Game {i} determine final result.',
           'source':{'kind':'scalar','parameters':{'cargo':17}}} for i in range(8)]
    monkeypatch.setattr(diag,'_source_inputs',lambda path:{'source_adapter':adapter,
          'full_split_rows':{'dev':rows,'train':[]}})
    monkeypatch.setattr(expdiag,'_source_run_from_expanded',lambda path:source)
    monkeypatch.setattr(exp,'_json',lambda path:identity)
    monkeypatch.setattr(exp,'_identity',lambda src,cfg:identity)
    monkeypatch.setattr(expdiag,'_load_checkpoint',lambda t,path:{'identity':identity,
        'schema':exp.SCHEMA,'model':{},'step':500})
    monkeypatch.setattr(gamer,'file_tree_sha256',lambda path:'FAKE')
    class FakeQwen:
        model=SimpleNamespace(layers=[None]*28)
        def eval(self):pass
        def get_base_model(self):return self
    monkeypatch.setattr(gamer,'model_components',lambda *args,**kw:(None,None,FakeQwen()))
    class FakeHead:
        def load_state_dict(self,weights):pass
        def to(self,**kw):return self
        def eval(self):pass
    monkeypatch.setattr(exp,'make_expanded_head',lambda *args:FakeHead())
    def fake_qwen(torch,model,tok,prompt,**kw):
        literal=re.search(r'has (.*?) cargo slots',prompt).group(1)
        lex={distance.numeral(v,form):v for form in distance.FORMS for v in range(-8,9)}
        lex['-0']=0
        value=lex[literal]
        x=np.float32(value)
        record={stage:np.full(8,x,dtype=np.float32) for stage in distance.STAGES
                if not stage.startswith('c0_')}
        return record,{'token_count':40,'last_token_id':11,'token_ids_sha256':'FAKE'}
    monkeypatch.setattr(radius,'capture_qwen',fake_qwen)
    def fake_c0(torch,net,x,**kw):
        return {stage:np.full(8,x[0],dtype=np.float32) for stage in distance.STAGES
                if stage.startswith('c0_')}
    monkeypatch.setattr(radius,'capture_c0',fake_c0)
    result=distance.run_distance(expanded,device='cpu',contexts=8,width=6,folds=4,controls=0)
    assert result['status']=='COMPLETE_READ_ONLY'
    assert result['layer_comparison']['c0_hidden_1']['heldout_words_r2']>.99
    captures=[p for p in tmp_path.glob('expanded-number-distance-*') if p.name=='expanded-number-distance-0001']
    assert len(captures)==1
    assert (captures[0]/'activations.npz').exists()
    assert (expanded/'best_model.pt').read_bytes()==b'checkpoint'
    assert (source/'certified_pairs.jsonl').read_text()=='certified'
