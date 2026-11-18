"""G2-0053 real neural feature, scorer, decoder, and callback lifecycle tests."""
import json
import hashlib
import gzip

import pytest
import torch

from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as e
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
from tools.nanojev_gamer_v1_graph_head import GamerGraphHead


def feature_case():
    torch.manual_seed(553)
    head=GamerGraphHead(hidden_size=8,width=16,layers=1,latent_slots=2,
                        memory_slots=2,max_span_tokens=2).eval().requires_grad_(False)
    hidden=torch.randn(4,8)
    prepared={'prompt':'alpha beta gamma delta','prompt_start':0,
        'offsets':[(0,5),(6,10),(11,16),(17,22)],
        'eligible':torch.ones(4,dtype=torch.bool)}
    g1=head(hidden,eligible=prepared['eligible'],active_stage='G1')
    raw={'span_logits':g1['span_logits'],'span_valid':g1['span_valid'],
         'prepared':prepared,'hidden':hidden,'allowed':{('owns','actor','action')}}
    a=(0,5,'actor');b=(6,10,'action');c=(11,16,'action')
    parent={'expanded_anchors':[a,b], 'recovered_typed':{(a,b,'owns')}}
    # The real G1 raw-score path, not a hand-authored list of recovered edges.
    extended=e.construct(raw,parent)
    return raw,extended,a,b,c


def test_raw_g1_then_frozen_0048_encoder_then_learned_ranker():
    raw,extended,a,b,c=feature_case()
    trained=d48.OccurrenceEvidenceScorer(8).eval().requires_grad_(False)
    original={name:v.clone() for name,v in trained.state_dict().items()}
    item=g.frozen_features(raw,extended,trained,'counterfactual_train')
    assert item['node'].shape[1]==g.EMBED_WIDTH
    assert item['edge_count'] if 'edge_count' in item else len(item['edges'])
    assert (a,b,'owns') in item['edges']
    assert item['section']=='counterfactual_train'
    item['labels']={(a,b,'owns'),(a,c,'owns')}
    item['prompt_sha256']='one';item['pair']='p';item['family']='allocation';item['mode']='role_swap'
    g.mark_truth(item)
    assert item['truth_count']==2 and len(item['positive'])>=1
    model=g.EdgeRanker()
    scores=g.score_all(model,item,chunk=5)
    assert scores.shape==(item['edge_count'],)
    assert bool(torch.isfinite(scores).all())
    with torch.enable_grad():
        opt=torch.optim.AdamW(model.parameters(),lr=1e-4)
        opt.zero_grad(set_to_none=True)
        loss=model(item,torch.arange(min(5,item['edge_count']))).square().mean()
        loss.backward();opt.step()
    assert any(p.grad is not None for p in model.parameters())
    assert all(torch.equal(v,trained.state_dict()[k]) for k,v in original.items())
    assert len(g.evaluate(model,[item]))==1


def test_gold_cannot_change_label_blind_embeddings_or_candidates():
    raw,extended,a,b,c=feature_case()
    net=d48.OccurrenceEvidenceScorer(8).eval().requires_grad_(False)
    x=g.frozen_features(raw,extended,net,'counterfactual_probe')
    raw['gold']={(a,b,'owns')}
    y=g.frozen_features(raw,extended,net,'counterfactual_probe')
    assert x['edges']==y['edges']
    assert torch.equal(x['node'],y['node'])


def test_hard_negative_training_and_bounded_decoding():
    raw,extended,a,b,c=feature_case()
    net=d48.OccurrenceEvidenceScorer(8).eval().requires_grad_(False)
    base=g.frozen_features(raw,extended,net,'counterfactual_train')
    samples=[]
    for n in range(4):
        case={k:v.clone() if isinstance(v,torch.Tensor) else v[:] if isinstance(v,list) else v
              for k,v in base.items()}
        case['labels']={(a,b,'owns')}
        case.update(prompt_sha256=f'h{n}',family='allocation',mode='role_swap',pair=f'p{n//2}')
        samples.append(g.mark_truth(case))
    model,history=g.fit_ranker(samples,[],device='cpu',steps=2,batch=2)
    rows=g.evaluate(model,samples)
    assert len(rows)==4 and len(history)==2
    assert all(x['selected_edges']<=g.DECODE_BUDGET and x['ranked_edges']<=g.RANK_BUDGET
               for x in rows)
    assert all(torch.isfinite(v).all() for v in model.state_dict().values())


def parent52_fixture(tmp_path):
    folder=tmp_path/'p52';folder.mkdir()
    with gzip.open(folder/e.LEDGER,'wt',encoding='utf-8') as out:
        out.write('\n'.join([json.dumps({'section':'counterfactual_train'})]))
    digest=hashlib.sha256((folder/e.LEDGER).read_bytes()).hexdigest()
    report={'schema':e.SCHEMA,'status':e.STATUS,
        'production_promotion':False,'sealed_test_used':False,
        'parent50_replay_diagnostic':{'exact_parent50_legacy_replay':True},
        'interface_repair':{
            'decision':{'coverage_targets_met':True},
            'sections':{
                'counterfactual_train':{'repaired_reachable':96,
                    'repaired_both_pairs':48,'missing_gold_edges':0},
                'counterfactual_probe':{'repaired_reachable':31,
                    'repaired_both_pairs':15,'repaired_endpoints_present':863,
                    'required_endpoints':864}}},
        'ledger_sha256':digest}
    (folder/e.REPORT).write_text(json.dumps(report))
    return folder


def test_parent52_report_verification_real_schema_and_hash(tmp_path):
    folder=parent52_fixture(tmp_path)
    report,sha=g.verify_parent52(folder)
    assert report['interface_repair']['decision']['coverage_targets_met'] is True
    assert len(sha['g2_0052_ledger_sha256'])==64
    report['interface_repair']['sections']['counterfactual_probe']['repaired_reachable']=30
    (folder/e.REPORT).write_text(json.dumps(report))
    with pytest.raises(ValueError,match='PARENT52_CONTRACT'):
        g.verify_parent52(folder)


def test_cli_entry_point_is_real_and_has_complete_parent_paths():
    import contextlib,io
    from tools.nanojev_gamer_v1_graph_curriculum import main
    with contextlib.redirect_stdout(io.StringIO()) as result:
        with pytest.raises(SystemExit) as raised:
            main(['frob-g2-repaired-graph-learning','--help'])
    assert raised.value.code==0
    for arg in ('--candidate52-run','--candidate51-run','--occurrence48-run',
                '--counterfactual50-run','--steps','--batch-prompts'):
        assert arg in result.getvalue()


def test_0053_reuses_g2_0052_source_capture_through_actual_callbacks(monkeypatch,tmp_path):
    # Exercises 0053 runner wiring, including required parent52 check, an
    # immutable frozen G2-0048 weight loader and the completed report path.
    from safetensors.torch import save_file
    folder=parent52_fixture(tmp_path)
    p48=tmp_path/'p48';p48.mkdir()
    net=d48.OccurrenceEvidenceScorer(8)
    save_file(net.state_dict(),str(p48/d48.WEIGHTS))
    sha=d48.backbone.sha256(p48/d48.WEIGHTS)
    (p48/d48.REPORT).write_text(json.dumps({'schema':d48.SCHEMA,'weights_sha256':sha}))
    p50=tmp_path/'p50';p50.mkdir()
    (p50/g.p52.p51.p50.REPORT).write_text(json.dumps({'schema':g.p52.p51.p50.SCHEMA}))
    p51=tmp_path/'p51';p51.mkdir()
    with gzip.open(p51/e.p51.LEDGER,'wt') as f:f.write('{}\n')
    r51={'schema':e.p51.SCHEMA,'status':e.p51.STATUS,
      'candidate_generation_uses_gold':False,'production_promotion':False,
      'parent50_replay_diagnostic':{'exact_parent50_legacy_replay':True},
      'results':{'counterfactual_probe':{
        'reachable_graphs':{'recovered_typed':8},
        'both_reachable_pairs':{'recovered_typed':1}}},
      'ledger_sha256':hashlib.sha256((p51/e.p51.LEDGER).read_bytes()).hexdigest()}
    (p51/e.p51.REPORT).write_text(json.dumps(r51))
    seen={}
    def mock_run(**kwargs):
        seen.update(kwargs)
        raw,expanded,_,_,_=feature_case()
        frozen=kwargs['interface_case_features'](raw,expanded,'counterfactual_train')
        assert frozen['node'].shape[1]==96
        assert kwargs['interface_finalize'] is not None
        return {'status':g.STATUS,'results':{'counterfactual_probe':{
            'reachable_graphs':{'recovered_typed':8},
            'both_reachable_pairs':{'recovered_typed':1}}}}
    monkeypatch.setattr(e.p51,'run',mock_run)
    result=g.run(candidate52_run=folder,candidate51_run=p51,occurrence48_run=p48,
                 counterfactual50_run=p50,output=str(tmp_path/'new'),device='cpu')
    assert result['status']==g.STATUS
    assert seen['alternate_schema']==g.SCHEMA
    assert seen['alternate_report_filename']==g.REPORT
    assert seen['interface_extension'] is e.construct
    assert 'interface_case_features' in seen and 'interface_finalize' in seen


def test_real_0051_0052_0053_capture_callbacks_persist_scoring_artifacts(tmp_path,monkeypatch):
    """Run the ACTUAL p51 callback including p52 constructor and final ZIP inputs.

    The parent-report verification and expensive Qwen extraction are replaced
    with precise synthetic boundaries; candidate construction, neural feature
    extraction, gold-after-inference timing, report writing and model saving are real.
    """
    from types import SimpleNamespace as N
    from safetensors.torch import load_file
    from tools import nanojev_gamer_v1_g2_candidate_construction_repair as p51
    from tools.nanojev_gamer_v1_g2_candidate_construction_repair import SOURCE
    root=tmp_path/'score';A=(0,5,'actor');B=(6,10,'action')
    prompts=['alpha beta '+word+' delta' for word in
        ('gamma','sigma','theta','omega','kappa','alpha','bravo','fable',
         'globe','house','inlet','joker')]
    rows=[{'prompt':p,'split':'train','family':'allocation',
           'annotation_provenance':'AUTHOR_MARKED_IN_SOURCE_GENERATOR'} for p in prompts]
    def pair(i,section):
        chunk=rows[i:i+2]
        return {'id':section+'-pair','rows':chunk,'mode':'role_swap','family':'allocation',
                'sha256':[SOURCE.base._sha(x['prompt']) for x in chunk]}
    cohorts={'counterfactual_train':[pair(0,'counterfactual_train')],
             'counterfactual_probe':[pair(i,'counterfactual_probe') for i in range(2,12,2)]}
    mk=lambda *args,**kwargs:({}, {})
    fake=N(d49=N(g41=N(verify_parent40=mk),
                  g42=N(verify_parent41=lambda *a,**k:({}, {},{})),
                  b43=N(verify_parent42=mk),verify_parent48=lambda *a,**k:
                      ({'cohorts':{'train_learn_prompts':[]}}, {}, [])),
           d48=N(d45=N(d44=N(verify_parent43=mk),verify_parent44=mk),verify_parent47=mk),
           d46=N(verify_parent45=mk),d47=N(verify_parent46=lambda *a,**k:
               ({'cohorts':{'train_learn_prompts':[]}},{})),
           verify_parent49=mk,author_pairs=lambda t,p,**k:{
               'counterfactual_train':cohorts['counterfactual_train'],
               'counterfactual_probe':cohorts['counterfactual_probe'][:p]})
    monkeypatch.setattr(p51,'p50',fake)
    monkeypatch.setattr(p51,'verify_parent50',lambda *a,**k:({
         'cohorts':{'train_learn_prompts':[]},
         'training':{'author_pairs_requested':1,'probe_pairs_requested':1},
         'schema':'fake.valid.parent50'},{}))
    monkeypatch.setattr(p51,'parent50_probe_ledger',lambda *a,**k:
        {SOURCE.base._sha(x['prompt']):0 for x in rows[2:4]})
    monkeypatch.setattr(SOURCE.base,'gold_binding_edges',lambda row:{(A,B,'owns')})
    parent48=d48.OccurrenceEvidenceScorer(8).eval().requires_grad_(False)
    def feature_callback(raw,extension,section):
        return g.frozen_features(raw,extension,parent48,section)
    calls=[]
    def completion(cases,pairs,replay,report):
        calls.append({'count':len(cases),'replay':replay,'report':report['schema']})
        assert len(cases)==12 and replay['exact_parent50_legacy_replay'] is True
        assert sum(x['section']=='fresh_counterfactual_probe' for x in cases)==8
        assert all(x['labels']=={(A,B,'owns')} for x in cases)
        return {'score':'integrated','training':{'steps':2}},g.EdgeRanker().state_dict(),{'enabled':False}
    def fake_capture(material,tag,*,include_raw_g1):
        assert include_raw_g1
        result=[]
        for row in material:
            raw,ext,_,_,_=feature_case()
            prepared=dict(raw['prepared']);prepared['prompt']=row['prompt']
            raw['prepared']=prepared
            baseline={'row':row,'recovered':{'anchors':[A,B]},
               'shortlisted':{'edges':[(A,B,'owns')]}}
            evidence={'anchors':[A,B],'spans':[{'token_first':0,'token_last':0},
                                            {'token_first':1,'token_last':1}],
                'baseline_anchors':[A]}
            baseline['candidate_repair']=p51.candidates_for_record(baseline,evidence,raw['allowed'])
            baseline['g1_raw_interface']=raw
            result.append(baseline)
        return result
    def fake_source_run(*,diagnostic_callback,**kwargs):
        return diagnostic_callback(capture_candidate_repair_rows=fake_capture,
                                   assert_all_frozen=lambda:None,
                                   training=[],fitting=[],auditing=[])
    monkeypatch.setattr(SOURCE,'run',fake_source_run)
    result=p51.run(bakeoff40_run='p40',oracle41_run='p41',rank42_run='p42',
        absolute43_run='p43',memorization44_run='p44',separability45_run='p45',
        multiprompt46_run='p46',crossover47_run='p47',occurrence48_run='p48',
        integrated49_run='p49',counterfactual50_run='p50',output=str(root),
        train_pairs=1,probe_pairs=1,fresh_probe_pairs=4,device='cpu',
        interface_extension=e.construct,interface_audit=e.audit,
        interface_summary=e.summarize,
        interface_case_features=feature_callback,interface_finalize=completion,
        alternate_schema=g.SCHEMA,alternate_status=g.STATUS,
        alternate_report_filename=g.REPORT,alternate_ledger_filename=g.LEDGER)
    assert calls and result['scoring_experiment']['score']=='integrated'
    assert result['new_optimizer_steps']==2
    report=json.loads((root/g.REPORT).read_text())
    assert report['status']==g.STATUS
    assert report['fresh_probe_candidate_audit']['cases']==8
    assert report['fresh_probe_candidate_audit']['pairs']==4
    assert report['scoring_weights_sha256']==SOURCE.backbone.sha256(root/g.WEIGHTS)
    assert report['scoring_policy_sha256']==SOURCE.backbone.sha256(root/g.POLICY)
    assert report['ledger_sha256']==SOURCE.backbone.sha256(root/g.LEDGER)
    assert len(load_file(str(root/g.WEIGHTS)))==len(g.EdgeRanker().state_dict())
    with gzip.open(root/g.LEDGER,'rt') as f:
        assert len([json.loads(line) for line in f])==12


def test_complete_48_pair_learning_and_16_pair_probe_after_fixed_training():
    raw,expanded,a,b,c=feature_case()
    base=g.frozen_features(raw,expanded,
        d48.OccurrenceEvidenceScorer(8).eval().requires_grad_(False),
        'counterfactual_train')
    pairs={'counterfactual_train':[],'counterfactual_probe':[],
           'fresh_counterfactual_probe':[]}
    samples=[]
    for section,num in (('counterfactual_train',48),('counterfactual_probe',16),
                        ('fresh_counterfactual_probe',16)):
        for i in range(num):
            names=[]
            for side in (0,1):
                item={key:(value.clone() if isinstance(value,torch.Tensor) else
                      list(value) if isinstance(value,list) else value)
                      for key,value in base.items()}
                sha=f'{section}-{i}-{side}'
                item.update(section=section,prompt_sha256=sha,pair=f'{section}-p{i}',
                            family='allocation' if i%2==0 else 'encounter',
                            mode='role_swap',labels={(a,b,'owns')})
                samples.append(item)
                names.append(sha)
            pairs[section].append({'id':f'{section}-p{i}','sha256':names,
                'family':'allocation' if i%2==0 else 'encounter',
                'mode':('role_swap','renaming')[(i//2)%2]})
    parent50={'results':{'replay_bce':{'counterfactual_probe':{'all':{
        'exact':0,'cases':32,'missing':100,'extra':100}}}}}
    experiment,weights,policy=g.finalize(samples,pairs,
        {'exact_parent50_legacy_replay':True},
        {'interface_repair':{'decision':{'coverage_targets_met':True}}},
        device='cpu',steps=2,batch=2,parent_hashes={'dummy':'sha'},
        parent50=parent50,parent48_sha='0'*64)
    assert experiment['training']['training_graphs']==80
    assert experiment['training']['train_fit_graphs']==16
    assert experiment['training']['probe_graphs']==32
    assert experiment['training']['fresh_holdout_graphs']==32
    assert experiment['results']['fresh_counterfactual_probe']['cases']==32
    assert experiment['results']['counterfactual_probe']['cases']==32
    assert experiment['results']['train_fit']['cases']==16
    assert len(weights)>0 and policy['enabled'] is False
    assert experiment['gates']['production_promotion'] is False
    assert experiment['gates']['fresh_sealed_generalization_verified'] is False


def test_actual_cli_dispatch_passes_every_required_parent_without_cuda(tmp_path,monkeypatch):
    """The real CLI branch reaches the real 0053 module with correct defaults."""
    from tools.nanojev_gamer_v1_graph_curriculum import main
    seen={}
    def runner(**kwargs):
        seen.update(kwargs)
        return {'status':g.STATUS,
                'scoring_experiment':{'results':{},
                    'historical_g2_0050_replay_bce_probe':{},
                    'gates':{'production_promotion':False}}}
    monkeypatch.setattr(g,'run',runner)
    import io,contextlib
    with contextlib.redirect_stdout(io.StringIO()) as output:
        main(['frob-g2-repaired-graph-learning',
              '--root',str(tmp_path),'--device','cpu'])
    assert seen['device']=='cpu'
    assert seen['train_pairs']==48 and seen['probe_pairs']==16
    assert seen['steps']==240 and seen['batch_prompts']==4
    assert seen['candidate51_run'].endswith('graph-g2-candidate-construction-repair-0051')
    assert seen['candidate52_run'].endswith('graph-g2-endpoint-interface-repair-0052')
    assert seen['occurrence48_run'].endswith('graph-g2-occurrence-evidence-repair-0048')
    assert seen['counterfactual50_run'].endswith('graph-g2-counterfactual-learning-0050')
    assert seen['output'].endswith('graph-g2-repaired-graph-learning-0053')
    assert 'G2_0053_TRAIN_ONLY_SCORING_AUDITED_NOT_CERTIFIED' in output.getvalue()
