"""Register oracle is a read-only decomposition of certified public problems."""
from collections import Counter
from pathlib import Path
import copy
import json

import numpy as np
import pytest

from tools import nanojev_game_transformation_library as source
from tools import nanojev_gamer_v1_catalog_extensions as catalog
from tools import nanojev_gamer_v1_transition_games as transitions
from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_register_oracle as oracle
from tools import nanojev_gamer_v1_diagnostic_train as training
from tools import nanojev_gamer_v1_staircase as stair


def test_146_certified_game_oracles_cover_all_table_families_and_exact_arithmetic():
    games=sorted(set(source.MATRICES)|set(source.SCALAR_MODELS)|
                 set(catalog.EXTRA_IDS)|set(transitions.TRANSITION_IDS))
    assert len(games)==146
    sources=Counter();families=set()
    for game in games:
        for case_index in (0,4,5):
            row=pairs.generate_case(game,case_index,47)
            doc=oracle.build_oracle(row)
            sources[doc['source_kind']]+=1
            families.update(doc['table_families'])
            assert doc['final_answer']['float']==pytest.approx(float(row['answer']))
            assert doc['steps'][-1]['id']==doc['final_step']
            assert all(r['visibility']=='PROMPT_LITERAL_VERIFIED' for r in doc['registers'])
            assert all(s['scope']=='SUPERVISION_ONLY_NOT_MODEL_INPUT' for s in doc['steps'])
            assert 'math_prompt' in doc['forbidden_feature_inputs']
    assert len(sources)==5
    assert families=={'calculation','probabilistic_outcomes','strategic_payoff','state_transition'}


def test_matrix_registers_have_no_hidden_affine_metadata_and_select_visible_action():
    row=pairs.generate_case('rock_paper_scissors',0,47)
    doc=oracle.build_oracle(row)
    names={r['id'] for r in doc['registers']}
    assert {'opponent_action_index','payoff:r0:c0:self','payoff:r0:c0:opponent'}<=names
    assert not any('affine' in r or 'variant' in r for r in names)
    assert all('source' not in r for r in doc['registers'])
    assert len(doc['steps'])==2
    assert doc['steps'][1]['operands'][0]['source']=='step'


def test_scalar_labels_are_mathematically_independent_and_zero_line_exact():
    row=pairs.generate_case('captain_fuel_reserve',4,47)
    doc=oracle.build_oracle(row)
    assert float(doc['final_answer']['float'])==float(row['answer'])
    assert any(r['id']=='parameter:reserve_required' for r in doc['registers'])
    assert len(doc['steps'])>=1
    assert any(s['operation']=='subtract' for s in doc['steps'])


def test_tampering_and_hidden_register_information_are_rejected():
    row=pairs.generate_case('captain_crew_risk',0,47)
    tampered=copy.deepcopy(row)
    tampered['prompt_08']=tampered['prompt_08'].replace('103.2','999.0')
    with pytest.raises(ValueError,match='MISMATCH_OR_TAMPER'):
        oracle.build_oracle(tampered)
    copyrow=copy.deepcopy(row)
    copyrow['source']['parameters']['crew_count']=999
    with pytest.raises(ValueError,match='MISMATCH_OR_TAMPER'):
        oracle.build_oracle(copyrow)


def _example_deck():
    games=('captain_crew_risk','bayes_posterior','rock_paper_scissors',
           'captain_relative_navigation','bertrand_oligopoly')
    train=[pairs.generate_case(g,i,47) for g in games for i in (0,1,2,3)]
    dev=[pairs.generate_case(g,4,47) for g in games]
    # Controlled synthetic representations: no target labels or oracle data
    # enter the feature construction.
    def feat(rows):
        return np.asarray([[len(row['prompt_08'])%101, sum(map(ord,row['prompt_08']))%113,
                            len(row['game_id']),len(row['prompt_08'].split()),
                            sum(ord(c)**2 for c in row['prompt_08'])%97,1.]
                           for row in rows],dtype=np.float32)
    return train,dev,feat(train),feat(dev)


def test_probes_produce_per_case_evidence_and_require_observed_registers():
    tr,dev,x,xd=_example_deck()
    t=[oracle.build_oracle(r) for r in tr]
    d=[oracle.build_oracle(r) for r in dev]
    results,cases,skips=oracle.probe_registers(t,d,x,xd,limit=10)
    assert results and cases
    assert {r['routing'] for r in results}=={'oracle_game_and_role','role_only'}
    assert all(np.isfinite(r['probe_raw_mae']) for r in results)
    assert all(r['probe_is_deployable'] is False for r in results)
    assert all(r['target']==pytest.approx(next(v['value']['float']
        for o in d if o['case_sha256']==r['case_sha256']
        for v in o['registers'] if v['id']==r['register_id'])) for r in cases)
    assert all(r['mean_baseline'] is not None for r in cases)
    assert all('step:' not in r['register_id'] for r in cases)
    with pytest.raises(ValueError,match='FEATURE_SHAPE'):
        oracle.probe_registers(t,d,x[:-1],xd)


def test_read_only_oracle_cli_and_complete_output(tmp_path,monkeypatch):
    train,dev,x,xd=_example_deck()
    run=tmp_path/'certified';run.mkdir()
    (run/'marker').write_bytes(b'unchanged')
    source={'games':sorted({r['game_id'] for r in train}),
            'manifest':{'dataset_sha256':'test-dataset'},
            'head_sha256':'test-head',
            'head_state':{'adapter_sha256':'frozen-adapter'},
            'selected':{'train':{'rows':train,'features':x},
                        'dev':{'rows':dev,'features':xd}}}
    monkeypatch.setattr(training,'_source_inputs',lambda *a,**kw:source)
    args=stair.parser().parse_args(['register-oracle','--run',str(run),
                                    '--register-max-games','5'])
    assert args.register_max_games==5
    report=stair.main(['register-oracle','--run',str(run),'--register-probe-limit','8'])
    assert report['status']=='COMPLETE_BOUNDED_DECODABILITY_PROBE_NOT_PROMOTED'
    out=Path(report['output'])
    for file in ('train_register_oracles.jsonl','dev_register_oracles.jsonl',
                 'register_catalog.json','probe_groups.json','probe_predictions.csv',
                 'events.jsonl','final_report.json'):
        assert (out/file).is_file()
    assert (run/'marker').read_bytes()==b'unchanged'
    assert not (run/'final_report.json').exists()
    assert report['game_count']==5
    assert report['probe']['oracle_game_and_role']['evaluated_register_predictions']>0
    with pytest.raises(ValueError,match='REGISTER_FLAGS_ONLY'):
        stair.main(['status','--run',str(run),'--register-max-games','3'])
    with pytest.raises(ValueError,match='REGISTER_DOES_NOT_TRAIN'):
        stair.main(['register-oracle','--run',str(run),'--steps','4'])
    with pytest.raises(FileExistsError,match='EXISTS'):
        oracle.run_register_oracle(run,output=out)


def test_invalid_probe_oracle_checks_fail_without_source_mutation(tmp_path,monkeypatch):
    tr,dev,x,xd=_example_deck()
    run=tmp_path/'run';run.mkdir()
    def broken(*a,**kw):
        raise ValueError('GAMER_CERTIFIED_INPUTS_INVALID')
    monkeypatch.setattr(training,'_source_inputs',broken)
    with pytest.raises(ValueError,match='CERTIFIED_INPUTS_INVALID'):
        oracle.run_register_oracle(run)
    assert not list(tmp_path.glob('run-register-oracle-*'))
    with pytest.raises(ValueError,match='INVALID_CONTROLS'):
        oracle.run_register_oracle(run,probe_limit=1)


def test_real_staircase_lineage_register_probe_without_hf_or_gpu(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    from safetensors.torch import save_file
    from tools import nanojev_gamer_v1_handoff as gamer
    run=tmp_path/'real';run.mkdir()
    data=run/'certified_pairs.jsonl'
    pairs.write_dataset(data,cases_per_game=6,seed=47,include_transition_games=True)
    _,manifest=gamer.ensure_manifest(run,data,'Qwen/Qwen3-0.6B',False,
                                     include_transition_games=True)
    best=run/'lora'/'best_adapter';best.mkdir(parents=True)
    (best/'adapter_config.json').write_text(json.dumps({
        'r':16,'task_type':'CAUSAL_LM','base_model_name_or_path':'Qwen/Qwen3-0.6B'}))
    save_file({'foo.weight':torch.tensor([.5,1.])},str(best/'adapter_model.safetensors'))
    hp={'model':'Qwen/Qwen3-0.6B','seed':47,'max_length':1024,'max_new_tokens':512,
        'lora_rank':16,'lora_batch':1,'lora_accum':8,'lora_lr':.0001,'lora_eval_every':200}
    torch.save({'schema':gamer.SCHEMA,'phase':'lora','step':1800,'pending_eval':False,
        'dataset_sha256':manifest['dataset_sha256'],
        'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
        'hyperparameters':hp,'adapter':{'foo.weight':torch.tensor([3.,4.])},
        'best_step':1000},run/'lora'/'latest.pt')
    class Model:
        def eval(self):return self
    monkeypatch.setattr(gamer,'model_components',lambda *a,**kw:(torch,object(),Model()))
    monkeypatch.setattr(gamer,'capture_original_features',
        lambda _t,_m,_tok,rows,_device,*,batch_size,max_length:
          np.asarray([[len(r['prompt_08'])%7/7.,len(r['prompt_08'])%13/13.,
                       len(r['prompt_08'])%17/17.,.1,.2,.3,.4,.5] for r in rows],
                     dtype=np.float32))
    stair.head(run,steps=1,device='cpu',head_eval_every=1)
    checkpoint=run/'staircase'/'head_cycle_0001'/'to_numerical'/'latest.pt'
    before=checkpoint.read_bytes()
    report=oracle.run_register_oracle(run, max_games=3,probe_limit=8)
    assert report['game_count']==3
    assert report['cases']['dev']==3
    assert checkpoint.read_bytes()==before
    assert report['frozen_adapter_sha256']==gamer.file_tree_sha256(
        run/'staircase'/'head_cycle_0001'/'lora'/'best_adapter')
