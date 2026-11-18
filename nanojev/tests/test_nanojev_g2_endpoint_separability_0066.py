"""G2-0066 separability safety, synthetic learning, and integration tests."""
import math
from types import SimpleNamespace
import pytest
from tools import nanojev_g2_endpoint_separability_0066 as b


def _toy(gold_index=1, pair=0, family='allocation', mode='role_swap'):
    import torch
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    rel=torch.full((p55.SLOTS,3), -11.)
    rel[:,0]=12.
    rel[0,0]=0.;rel[0,1]=15.
    src=torch.zeros(p55.SLOTS,3)
    dst=torch.zeros(p55.SLOTS,3)
    src[0,0]=4.;src[0,1]=2.
    dst[0,2]=8.
    features={'node':torch.zeros(3,96),
              'kinds':torch.zeros(3,dtype=torch.long),
              'margin':torch.zeros(3),
              'src':torch.tensor([0,1,2]),
              'dst':torch.tensor([2,2,1]),
              'relation':torch.zeros(3,dtype=torch.long),
              'geometry':torch.zeros(3,5)}
    q=torch.zeros(p55.SLOTS,64);q[0,0]=8.
    context=torch.zeros(3,64);context[0,0]=-8.;context[1,0]=8.
    return {'logits':(rel,src,dst),'features':features,'q':q,'context':context,
            'gold':{gold_index},'truth_count':1,'family':family,'mode':mode,
            'pair_index':pair,'id':f'prompt-{pair}-{gold_index}-{family}-{mode}',
            'max_edge':p55.MAX_EDGE}


def test_slot_endpoint_feature_shape_and_parity():
    import torch
    c=_toy()
    for arch in b.ARCHS:
        head=b.EndpointReadout.create(arch)
        d=head(c['q'],c['context'])
        assert d.shape==(64,3)
        assert torch.isfinite(d).all()
    assert b._pred(c)=={0}
    assert b._pred(c,alpha=0,src=b.EndpointReadout.create('bilinear'))=={0}
    with pytest.raises(ValueError, match='architecture'):
        b.EndpointReadout.create('unknown')


def test_gold_train_slot_matching_is_not_inference_dependency():
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    c=_toy()
    matches=b._match_train([c],p55)
    assert matches[0][1]==[(0,1)]
    assert b._pred({k:v for k,v in c.items() if k not in ('gold','truth_count')})=={0}
    with pytest.raises(ValueError,match='UNREACHABLE_TRAIN'):
        b._match_train([{**c,'truth_count':2}],p55)


def test_linear_endpoint_can_learn_on_toy():
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    c=_toy()
    aligned=b._match_train([c]*8,p55)
    frozen=b._model_score(c,'source')[0]
    w,receipt=b._fit('source','bilinear',aligned,epochs=16)
    learned=b._model_score(c,'source',w)[0]
    assert receipt['gold_aligned_slot_examples']==8
    assert math.isfinite(receipt['final_mean_loss'])
    assert float(learned[1]-learned[0])>float(frozen[1]-frozen[0])


def test_nonlinear_endpoint_learns_on_toy():
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    c=_toy()
    aligned=b._match_train([c]*8,p55)
    initial=b._model_score(c,'source')[0]
    net,receipt=b._fit('source','nonlinear',aligned,epochs=5)
    after=b._model_score(c,'source',net)[0]
    assert float(after[1]-after[0])>float(initial[1]-initial[0])


def test_oracle_conditional_rank_is_disclosed_and_separate_from_graph_inference():
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    c=_toy()
    result=b._endpoint_ranks([c],p55,'source',{'frozen':None})
    assert result['oracle_slot_alignment'] is True
    assert result['conditional_endpoint_ranking']['frozen']['top1']==0.
    assert b._pred(c)=={0}
    unreachable={**c,'truth_count':2}
    assert b._endpoint_ranks([unreachable],p55,'source',{'frozen':None})['skipped_unreachable_graphs']==1


def test_selection_ties_prefer_original_policy():
    c=_toy()
    policy,score=b._select([c])
    assert policy['alpha']==0
    assert score['graphs']==1
    assert policy['margin'] in b.MARGINS


def test_feature_ablation_is_deterministic_and_label_blind():
    import torch
    c=_toy()
    w=b.EndpointReadout.create('bilinear')
    with torch.no_grad():
        w.weight[0,0]=1.
    a=b._model_score(c,'source',w,ablate=True)
    d=b._model_score(c,'source',w,ablate=True)
    assert torch.equal(a,d)
    no_gold={k:v for k,v in c.items() if k not in ('gold','truth_count')}
    assert torch.equal(a,b._model_score(no_gold,'source',w,ablate=True))


def test_pair_partition_and_report_refusal_controls(tmp_path):
    c=_toy()
    with pytest.raises(ValueError,match='Positive'):
        b._fit('source','bilinear',[(c,[(0,1)])],0)
    import inspect
    src=inspect.getsource(b.main)
    assert "a.output.exists()" in src
    assert "a.output.open('x'" in src
    assert 'sealed_test_used' in inspect.getsource(b.run)
    assert 'production_promotion' in inspect.getsource(b.run)


def test_capture_real_model_recreates_decoder_and_exposes_slot_context():
    import torch
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    model=p55.model_for('set_prediction').eval().requires_grad_(False)
    c=_toy()
    record={**c['features'],'positive':torch.tensor([1]),'truth_count':1,
            'family':c['family'],'mode':c['mode'],'prompt_sha256':'real-test'}
    class Corpus:
        def pair(self,idx,with_labels=True):
            return [record],None
    results=b._capture_activations(model,Corpus(),[0],p55)
    x=results[0]
    assert x['q'].shape==(p55.SLOTS,64) and x['context'].shape==(3,64)
    assert x['q'].is_inference() is False
    assert x['context'].is_inference() is False
    assert x['gold']=={1}
    assert b._pred(x)==p55.infer_edges(model,c['features'],'set_prediction')


def test_full_orchestration_uses_distinct_evaluation_and_no_mutations(monkeypatch,tmp_path):
    from tools import nanojev_g2_learning_bakeoff_0065 as b65
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    class Corpus:
        def __init__(self,split,n):
            self.split=split
            self.pairs=[]
            for i in range(n):
                f=('allocation','encounter')[i%2]
                m=('renaming','role_swap')[(i//2)%2]
                self.pairs.append((i,0,{'family':f,'mode':m},{'family':f,'mode':m}))
            self.indices=list(range(n))
    td=Corpus('train',8);vd=Corpus('validation',12)
    def fake_load(*args):
        return (object(),p55,td,vd,{'weights_sha256':'frozen-checkpoint'},
                {'capture_config_sha256':'capture','plan_sha256':'plan','budget':{}})
    def fake_capture(model,ds,indices,p55):
        result=[]
        for i in indices:
            meta=ds.pairs[i][2]
            for side in (0,1):
                c=_toy(gold_index=(i+side)%2, pair=i,**meta)
                c['id']=f'{ds.split}-{i}-{side}'
                result.append(c)
        return result
    monkeypatch.setattr(b65,'_load_corpus_and_model',fake_load)
    monkeypatch.setattr(b,'_capture_activations',fake_capture)
    monkeypatch.setattr(b,'_paired_ci',lambda *args,**kwargs:[-1.,1.])
    report=b.run(training_dir=tmp_path,plan_dir=tmp_path,capture_dir=tmp_path,
                 train_pairs=8,calibration_pairs=4,evaluation_pairs=4,epochs=1)
    assert report['split']['train_pairs']==8
    assert report['split']['evaluation_pairs']==4
    assert report['split']['calibration_pairs']==4
    assert report['evaluation_graph']['calibrated_baseline']['overall']['graphs']==8
    assert set(report['evaluation_graph'])=={
        'calibrated_baseline','bilinear_source','bilinear_destination','bilinear_joint',
        'nonlinear_source','nonlinear_destination','nonlinear_joint'}
    assert report['sealed_test_used'] is False and report['production_promotion'] is False
    assert 'G2-0066' in b.format_report(report)
