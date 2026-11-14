"""GAMER flat-register experiment: flat supervised outputs, exact composition."""
import copy
from pathlib import Path
import json
import math

import numpy as np
import pytest

from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_flat_registers as flat
from tools import nanojev_gamer_v1_staircase as stair
from tools import nanojev_gamer_v1_diagnostic_train as training
from tools import nanojev_game_transformation_library as source
from tools import nanojev_gamer_v1_catalog_extensions as extensions
from tools import nanojev_gamer_v1_transition_games as transitions


def _data(games=('captain_crew_risk','bayes_posterior','rock_paper_scissors',
                 'captain_relative_navigation','bertrand_oligopoly')):
    tr=[pairs.generate_case(g,i,47) for g in games for i in (0,1,2,3)]
    dv=[pairs.generate_case(g,4,47) for g in games]
    # This deliberately uses only textual prompt properties, not oracle or
    # generator fields; actual production inputs are frozen LoRA vectors.
    def feat(rows):
        return np.array([[len(r['prompt_08'])%101,
                          sum(map(ord,r['prompt_08']))%113,
                          len(r['prompt_08'].split()),
                          sum(ord(c)**2 for c in r['prompt_08'])%97,
                          r['prompt_08'].count(' '),1.] for r in rows],np.float32)
    return tr,dv,feat(tr),feat(dv)


def test_all_146_training_templates_match_public_oracle_and_use_no_answer_labels():
    games=sorted(set(source.MATRICES)|set(source.SCALAR_MODELS)|
                 set(extensions.EXTRA_IDS)|set(transitions.TRANSITION_IDS))
    assert len(games)==146
    train=[];dev=[]
    for game in games:
        for i in (0,1,2,3,4,5):
            row=pairs.generate_case(game,i,47)
            doc=flat.build_oracle(row)
            plan=flat._plan(doc)
            values={r['id']:r['value']['float'] for r in doc['registers']}
            y,_=flat._eval_plan(plan,values)
            assert y==pytest.approx(float(row['answer']),abs=1e-8,rel=1e-9)
            assert 'final_answer' not in str(plan)
            assert 'result' not in str(plan)
            if i<4:train.append(doc)
            elif i==4:dev.append(doc)
    plans=flat._plans(train,dev)
    assert len(plans)==146
    assert all(p['plan']['required_registers'] for p in plans.values())


def test_deterministic_executor_uses_predictions_not_hidden_oracle():
    doc=flat.build_oracle(pairs.generate_case('captain_fuel_reserve',4,47))
    plan=flat._plan(doc)
    values={r['id']:r['value']['float'] for r in doc['registers']}
    y,_=flat._eval_plan(plan,values)
    name='parameter:fuel_spent'
    values[name]+=3.
    changed,_=flat._eval_plan(plan,values)
    assert changed==pytest.approx(y-3.)
    with pytest.raises(ValueError,match='REGISTER_SET'):
        flat._eval_plan(plan,{**values,'final_answer':y})


def test_train_only_ridge_reproduces_successful_oracle_probe_without_adamw_register_moments():
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    tr,dv,xt,xd=_data()
    td=[flat.build_oracle(r) for r in tr]
    dd=[flat.build_oracle(r) for r in dv]
    games,names,slots=flat._layout(td,dd)
    table,game,reg,mask=flat._training_labels(td,games,slots)
    assert int(mask.sum())==sum(len(d['registers']) for d in td)
    assert set(np.unique(mask))=={0.,1.}
    ridge=flat.fit_register_readouts(xt,td,names,slots)
    selected,per_game,pooled=flat.predict_register_readouts(ridge,xd)
    assert selected.shape==(len(dv),len(slots))
    assert per_game.shape==pooled.shape==selected.shape
    assert ridge['training_labels']==int(mask.sum())
    for pair in names:
        j=slots[pair]
        x=np.array([xt[i] for i,d in enumerate(td) if d['game_id']==pair[0]])
        y=np.array([r['value']['float'] for d in td if d['game_id']==pair[0]
                    for r in d['registers'] if r['id']==pair[1]])
        if len(y)>1:
            from tools.nanojev_gamer_v1_register_oracle import _dual_probe
            prediction,_=_dual_probe(x,y,xd)
            assert np.allclose(flat._inverse(per_game[:,j]),prediction,rtol=1e-5,atol=1e-5)
    # Only the dense classifiers are optimized by AdamW. Register readouts
    # cannot drift on batches where their labels are absent.
    net=flat._build_model(torch,xt.shape[1],len(flat.FAMILIES),len(games),len(slots))
    assert set(n.split('.')[0] for n,_ in net.named_parameters())=={'tables','games'}
    assert all('register' not in n for n,_ in net.named_parameters())


def test_unrelated_register_role_labels_do_not_affect_ridge_readouts():
    tr,dv,xt,xd=_data()
    docs=[flat.build_oracle(r) for r in tr]
    games,names,slots=flat._layout(docs,[flat.build_oracle(r) for r in dv])
    first=flat.fit_register_readouts(xt,docs,names,slots)
    mutated=copy.deepcopy(docs)
    role='parameter:discount'
    if role not in {r['id'] for d in mutated for r in d['registers']}:
        role=mutated[0]['registers'][0]['id']
    for d in mutated:
        for r in d['registers']:
            if r['id']==role:r['value']['float']+=5
    second=flat.fit_register_readouts(xt,mutated,names,slots)
    for j,(game,name) in enumerate(names):
        if name!=role:
            assert np.array_equal(first['game_coef'][j],second['game_coef'][j])
            assert first['game_bias'][j]==second['game_bias'][j]
            role_ix=int(first['slot_role'][j])
            assert np.array_equal(first['role_coef'][role_ix],second['role_coef'][role_ix])


def test_training_predicts_tables_games_and_registers_without_dev_as_features():
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    tr,dv,xt,xd=_data()
    source={'selected':{'train':{'rows':tr,'features':xt},
                        'dev':{'rows':dv,'features':xd}}}
    docs={k:[flat.build_oracle(r) for r in source['selected'][k]['rows']]
          for k in ('train','dev')}
    learned=flat._train(source,docs,device='cpu',epochs=7,batch_size=8,
                        lr=.003,seed=1729)
    report,details,regs=flat._evaluate('dev',docs['dev'],learned)
    assert report['game']['games']==5
    assert report['table']['micro_f1']>=0.
    assert report['register']['labels']==sum(len(d['registers']) for d in docs['dev'])
    assert len(details)==len(dv) and len(regs)>len(dv)
    assert all('predicted_answer' in d for d in details)
    assert report['learned_route']['coverage']>0.
    # Dev target tampering is detectable, but cannot change learned predictions.
    before=learned['outputs']['dev'][2].copy()
    changed=copy.deepcopy(docs['dev'])
    changed[0]['registers'][0]['value']['float']+=10_000.
    assert np.array_equal(before,learned['outputs']['dev'][2])
    assert changed[0]['registers'][0]['value']['float'] != docs['dev'][0]['registers'][0]['value']['float']


def test_development_register_label_changes_do_not_change_fitted_readouts_or_route():
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    tr,dv,xt,xd=_data()
    source={'selected':{'train':{'rows':tr,'features':xt},
                        'dev':{'rows':dv,'features':xd}}}
    docs={'train':[flat.build_oracle(r) for r in tr],
          'dev':[flat.build_oracle(r) for r in dv]}
    original=flat._train(source,docs,device='cpu',epochs=2,batch_size=16,lr=.003,seed=15)
    changed=copy.deepcopy(docs)
    for doc in changed['dev']:
        for r in doc['registers']:
            r['value']['float']+=1000
    repeated=flat._train(source,changed,device='cpu',epochs=2,batch_size=16,lr=.003,seed=15)
    assert original['ridge']['selection']==repeated['ridge']['selection']
    for component in ('selected','per_game','pooled_role'):
        assert np.array_equal(original['ridge_outputs']['dev'][component],
                              repeated['ridge_outputs']['dev'][component])
    for pred1,pred2 in zip(original['outputs']['dev'],repeated['outputs']['dev']):
        assert np.array_equal(pred1,pred2)


def test_cv_pools_only_compatible_roles_uses_every_training_label():
    tr,dv,xt,xd=_data()
    docs=[flat.build_oracle(r) for r in tr]
    games,names,slots=flat._layout(docs,[flat.build_oracle(r) for r in dv])
    model=flat.fit_register_readouts(xt,docs,names,slots)
    assert model['training_labels']==sum(len(d['registers']) for d in docs)
    for j,(game,role) in enumerate(names):
        assert model['slot_support'][j]==sum(
            r['id']==role for d in docs if d['game_id']==game for r in d['registers'])
        i=int(model['slot_role'][j])
        assert model['roles'][i]==role
        assert model['role_support'][i]==sum(
            r['id']==role for d in docs for r in d['registers'])
        if model['selection'][j]['selected']=='pooled_role':
            assert model['role_support'][i]>model['slot_support'][j]
            assert (model['selection'][j]['train_cv_pooled_role_raw_mae']<
                    .97*model['selection'][j]['train_cv_per_game_raw_mae'])


def test_cli_rejects_mixed_mode_flags():
    p=stair.parser().parse_args(['flat-registers','--run','R','--flat-epochs','9'])
    assert p.flat_epochs==9
    with pytest.raises(ValueError,match='FLAT_FLAGS_ONLY'):
        stair.main(['status','--run','R','--flat-lr','.2'])
    with pytest.raises(ValueError,match='USES_FLAT_EPOCHS'):
        stair.main(['flat-registers','--run','R','--steps','9'])


def test_experiment_output_complete_and_original_source_readonly(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    tr,dv,xt,xd=_data()
    source={'games':sorted({r['game_id'] for r in tr}),
            'manifest':{'dataset_sha256':'controlled-dataset'},
            'head_sha256':'controlled-head',
            'head_state':{'adapter_sha256':'frozen-adapter'},
            'selected':{'train':{'rows':tr,'features':xt},
                        'dev':{'rows':dv,'features':xd}}}
    run=tmp_path/'source';run.mkdir()
    (run/'marker').write_text('unchanged')
    monkeypatch.setattr(training,'_source_inputs',lambda *a,**kw:source)
    monkeypatch.setattr(flat,'_baseline',lambda *a,**kw:{'raw_mae':6.05})
    result=stair.main(['flat-registers','--run',str(run),'--device','cpu',
                       '--flat-epochs','5','--flat-batch-size','16'])
    assert result['status']=='TRAINED_DIAGNOSTIC_NOT_PROMOTED'
    assert result['training']['games']==5
    assert result['training']['register_slots']>=10
    assert result['dev']['register']['labels']>=10
    output=Path(result['output'])
    for fn in result['artifacts']:
        assert (output/fn).exists(),fn
    checkpoint=torch.load(output/'flat_heads.pt',map_location='cpu',weights_only=True)
    assert checkpoint['schema']==flat.SCHEMA
    assert checkpoint['plans'] and checkpoint['model']
    assert not (run/'flat_heads.pt').exists()
    assert (run/'marker').read_text()=='unchanged'
    with pytest.raises(FileExistsError,match='EXISTS'):
        flat.run_flat_registers(run,output=output,device='cpu',epochs=1)


def test_matrix_selector_is_predicted_not_oracle_case_selected():
    d=flat.build_oracle(pairs.generate_case('rock_paper_scissors',4,47))
    plan=flat._plan(d)
    v={r['id']:r['value']['float'] for r in d['registers']}
    v['opponent_action_index']=0.
    a,t=flat._eval_plan(plan,v)
    assert t['selected_column']==0
    v['opponent_action_index']=1.
    b,t=flat._eval_plan(plan,v)
    assert t['selected_column']==1
    assert math.isfinite(a) and math.isfinite(b)


def test_real_staircase_lineage_and_saved_c0_comparison(tmp_path,monkeypatch):
    """Real certified dataset/checkpoint/provenance loader; HF capture is mocked."""
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    from safetensors.torch import save_file
    from tools import nanojev_gamer_v1_handoff as handoff
    run=tmp_path/'certified_real';run.mkdir()
    data=run/'certified_pairs.jsonl'
    pairs.write_dataset(data,cases_per_game=6,seed=47,include_transition_games=True)
    _,manifest=handoff.ensure_manifest(run,data,'Qwen/Qwen3-0.6B',False,
                                      include_transition_games=True)
    best=run/'lora'/'best_adapter';best.mkdir(parents=True)
    (best/'adapter_config.json').write_text(json.dumps({
        'r':16,'task_type':'CAUSAL_LM',
        'base_model_name_or_path':'Qwen/Qwen3-0.6B'}))
    save_file({'foo.weight':torch.tensor([.5,1.])},str(best/'adapter_model.safetensors'))
    hp={'model':'Qwen/Qwen3-0.6B','seed':47,'max_length':1024,'max_new_tokens':512,
        'lora_rank':16,'lora_batch':1,'lora_accum':8,'lora_lr':.0001,'lora_eval_every':200}
    torch.save({'schema':handoff.SCHEMA,'phase':'lora','step':1800,'pending_eval':False,
        'dataset_sha256':manifest['dataset_sha256'],
        'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
        'hyperparameters':hp,'adapter':{'foo.weight':torch.tensor([3.,4.])},
        'best_step':1000},run/'lora'/'latest.pt')
    class FakeModel:
        def eval(self):return self
    monkeypatch.setattr(handoff,'model_components',lambda *a,**kw:(torch,object(),FakeModel()))
    monkeypatch.setattr(handoff,'capture_original_features',
        lambda _t,_m,_tok,rows,_device,*,batch_size,max_length:
          np.asarray([[len(r['prompt_08'])%7/7.,len(r['prompt_08'])%13/13.,
                       len(r['prompt_08'])%17/17.,.1,.2,.3,.4,.5] for r in rows],
                     dtype=np.float32))
    stair.head(run,steps=1,device='cpu',head_eval_every=1)
    checkpoint=run/'staircase'/'head_cycle_0001'/'to_numerical'/'latest.pt'
    before=checkpoint.read_bytes()
    report=flat.run_flat_registers(run,device='cpu',max_games=3,
                epochs=2,batch_size=8)
    assert report['training']['games']==3
    assert 'raw_mae' in report['baseline_saved_C0_Wide']
    assert checkpoint.read_bytes()==before
    assert report['source']['dataset_sha256']==manifest['dataset_sha256']
    # Full 146-game inference path, including templates and payoffs, with all
    # 146 formula choices competing. Not a claim of numerical training quality.
    full=flat.run_flat_registers(run,device='cpu',max_games=0,
                epochs=1,batch_size=128)
    assert full['training']['games']==146
    assert full['training']['register_slots']>500
    assert full['dev']['learned_route']['valid_cases']>0
    assert checkpoint.read_bytes()==before
