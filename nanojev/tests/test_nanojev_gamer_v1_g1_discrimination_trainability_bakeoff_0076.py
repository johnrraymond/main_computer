"""G1-0076 fast CPU contracts, with no Qwen/network/GPU invocation."""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import torch

from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as m
from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as cov
from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as prior
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


ACTOR=SPAN_KINDS.index('actor')+1
ACTION=SPAN_KINDS.index('action')+1
QUANTITY=SPAN_KINDS.index('quantity')+1
WEIGHTS=((0.,)*cov.SET_DIM,)


def sample(n=6, *,rep=0):
    def option(a,b,k,base,old):
        name=SPAN_KINDS[k-1]
        return learned.Option(a,b,k,float(base),learned._feature(
            margin=3.0 if (a,b)==(1,4) else 1.2,
            p_cond=.85,width=b-a,n=n,first=a,kind=k,
            original=old,base=base,top=True),(a,b,name),old)
    originals=[option(0,1,ACTOR,2.,True),
               option(1,2,ACTION,1.6,True),
               option(2,4,ACTION,1.6,True),
               option(4,5,QUANTITY,2.,True)]
    extras=[option(1,4,ACTION,.40,False),
            option(1,4,ACTOR,.12,False),
            option(2,3,ACTION,.8,False),
            option(3,4,ACTOR,.10,False)]
    gold={(0,1,'actor'),(1,4,'action'),(4,5,'quantity')}
    base=learned.Case('cutover','train','encounter',f'sample-{rep}',n,originals,
            {(0,1,'actor'),(1,2,'action'),(2,4,'action'),(4,5,'quantity')},
            set(gold),[])
    full=learned.Case('cutover','train','encounter',f'sample-{rep}',n,
        sorted(originals+extras,key=lambda o:(o.end,o.start,o.kind)),
        set(base.control),set(gold),[])
    before=prior._encode(base)
    after=prior._encode(full)
    original_features={(o.start,o.end,o.kind):f for o,f in zip(originals,before.features)}
    for i,o in enumerate(full.options):
        if (o.start,o.end,o.kind) in original_features:
            after.features[i]=original_features[(o.start,o.end,o.kind)]
    scores=tuple(prior._scores(after,cov.CONTROL,WEIGHTS))
    features=tuple(cov._proposal_features(o,n) for o in full.options)
    new=tuple(not o.anchored for o in full.options)
    return cov.Expanded(before,after,features,new,scores,len(extras),0,12,
                        frozenset(gold))


def test_budget_preserves_all_old_candidates_features_scores_and_is_label_blind():
    c=sample()
    assert sum(c.is_new)==4
    for budget in m.BUDGETS:
        for mode in ('frozen','balanced'):
            d=m._restrict(c,budget,mode)
            assert len(d.base.case.options)==4
            assert len(d.added.case.options)==8
            a={(o.start,o.end,o.kind):(f,s) for o,f,s in zip(
                c.added.case.options,c.added.features,c.base_scores)}
            assert all((f,s)==a[(o.start,o.end,o.kind)] for o,f,s in zip(
                d.added.case.options,d.added.features,d.base_scores))
            prior_out=m._predict(c,m.CONTROL,WEIGHTS)
            assert m._predict(d,m.CONTROL,WEIGHTS)==prior_out
    a=m._restrict(c,64)
    c.added.case.gold={(999,999,'action')}
    b=m._restrict(c,64)
    assert a.base_scores==b.base_scores
    assert [o.char_key for o in a.added.case.options]==[o.char_key for o in b.added.case.options]
    with pytest.raises(ValueError,match='BUDGET'):
        m._restrict(c,33)


def test_closed_by_default_novel_candidates_and_zero_residual():
    c=sample()
    net=m.Discriminator()
    features,kinds,new,width,fixed=m._tensor_cache(c)
    with torch.no_grad():
        assert torch.allclose(net(features,kinds,new),torch.zeros(len(new),dtype=torch.float64))
        scores=m._model_scores(c,net)
    assert scores.shape==(len(c.added.case.options),)
    assert all((scores[i]==fixed[i]) if not c.is_new[i] else
               scores[i]==fixed[i]-m.CLOSED_PENALTY*width[i]
               for i in range(len(scores)))
    for new_model in (net,copy.deepcopy(net)):
        first=m._model_scores(c,new_model)
        c.added.case.gold={(45,51,'actor')}
        assert torch.equal(first,m._model_scores(c,new_model))
    assert 'gold' not in inspect.signature(m._model_scores).parameters
    assert 'gold' not in inspect.signature(m._predict).parameters


def test_nontrivial_hard_negative_optimizer_and_structured_stage():
    c=sample()
    initial=m._predict(c,m.CONTROL,WEIGHTS)
    assert initial!=c.added.case.gold
    one,diag=m._fit_hard([c],steps=12)
    assert diag['positive']>0 and diag['hard_negative']>0
    assert all(torch.isfinite(p).all() for p in one.parameters())
    assert diag['loss_samples'][-1] < diag['loss_samples'][0]
    two,ledger=m._fit_structured([c],one,epochs=3)
    assert ledger['epochs_run']>=1
    assert ledger['best_train_exact'] in (0,1)
    assert all(torch.isfinite(p).all() for p in two.parameters())
    assert m._fit_hard([c],steps=12)[1]['loss_samples']==diag['loss_samples']


def test_one_and_eight_trainability_passes_on_learnable_synthetic_cohort():
    cases=[sample(rep=i) for i in range(8)]
    ledger,model=m._trainability(cases,WEIGHTS,one_steps=90,eight_steps=100)
    assert ledger['status']=='PASS_1_AND_8'
    assert ledger['stages']['1']['best_exact']==1
    assert ledger['stages']['8']['best_exact']==8
    assert model is not None
    assert all(m._predict(c,'hard_negative_1024',WEIGHTS,model)==c.added.case.gold
               for c in cases)
    exported=m._export_model(model)
    assert len(exported['sha256'])==64
    assert len(exported['parameters']['hidden'])==m.DIM


def test_unreachable_training_target_fails_closed():
    c=sample();c.added.case.gold={(9,10,'action')}
    with pytest.raises(ValueError,match='UNREACHABLE_STRUCTURED'):
        m._fit_structured([c],m.Discriminator(),epochs=1)


def test_budgeted_structured_target_reachability_is_reported_not_falsely_failed():
    c=sample()
    c.added.case.gold.add((11,12,'actor'))  # This gold is not in the budgeted lattice.
    with pytest.raises(ValueError,match='UNREACHABLE_STRUCTURED'):
        m._fit_structured([c],m.Discriminator(),epochs=1,strict_targets=True)
    model,diag=m._fit_structured([c],m.Discriminator(),epochs=1,strict_targets=False)
    assert diag['unreachable_gold_under_budget']==1
    assert diag['strict_targets'] is False


def test_one_then_eight_memorization_is_sequential_and_blocks_further_fit(monkeypatch):
    cases=[sample(rep=i) for i in range(8)]
    observations=[]
    def train(rows,*,steps,**kwargs):
        observations.append((len(rows),steps))
        return m.Discriminator(),{'steps':steps}
    monkeypatch.setattr(m,'_fit_hard',train)
    monkeypatch.setattr(m,'_fit_structured',lambda rows,start,*,epochs:
                        (start,{'epochs_run':epochs}))
    memo,seed=m._trainability(cases,WEIGHTS,one_steps=3,eight_steps=4)
    assert seed is None and memo['status']=='BLOCKED_1_CASE_TRAINABILITY'
    assert observations[0]==(1,3)
    assert all(n==1 for n,steps in observations)  # never attempts eight after one-case block
    assert '8' not in memo['stages']


def test_oracle_and_metrics_are_separate():
    c=sample()
    allset=m._oracle_budget([c])['oracle_not_inference']
    assert allset['exact_prompts']==1
    model=m.Discriminator()
    inference=m._metric([c], 'hard_negative_64',WEIGHTS,model)[0]
    assert inference['exact_prompts']<=allset['exact_prompts']
    assert m._choose({arm:inference for arm in m.ARMS})==m.CONTROL
    assert set(m._variants([c],{**{b:[c] for b in m.BUDGETS},'balanced_256':[c]},WEIGHTS,
                   {'hard_negative':model,'staged_joint':model}))==set(m.ARMS)


def test_actual_0075_reference_schema_and_provenance(tmp_path):
    zipped=Path('/mnt/data/oracle-fabric-g1-candidate-coverage-bakeoff-0001.zip')
    if not zipped.exists():pytest.skip('real G1-0075 reference not mounted')
    with ZipFile(zipped) as z:
        report=json.loads(z.read('oracle-fabric-g1-candidate-coverage-bakeoff-0001/report.json'))
    target=tmp_path/'ref';target.mkdir()
    (target/'report.json').write_text(json.dumps(report))
    entries={n:{'manifest':{'head_sha256':s}}
             for n,s in report['lineage']['checkpoint_sha256'].items()}
    phases={**{p:[None]*n for p,n in report['design']['cohorts'].items()},
            'challenge':[None]*report['design']['historical_challenge_cases']}
    previous,digest=m._reference(target,entries,report['lineage']['dataset_sha256'],
                    phases,report['lineage']['structural_reference_sha256'],
                    report['lineage']['learned_reference_sha256'])
    assert previous==report and len(digest)==64
    report['selected']['legacy']='joint_proposal_structured_set'
    (target/'report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='REFERENCE_LINEAGE'):
        m._reference(target,entries,report['lineage']['dataset_sha256'],
                    phases,report['lineage']['structural_reference_sha256'],
                    report['lineage']['learned_reference_sha256'])


def test_fail_closed_output_and_no_mutating_action():
    source=inspect.getsource(m.run)
    assert "'g1_head_weights_modified':False" in source
    assert "'production_promotion':False" in source
    assert "'g2_unlocked':False" in source
    assert "'certification_authorized':False" in source
    assert 'head.optimizer' not in source
    assert '_checked_preflight' in source
    assert m.ARMS[0]==cov.CONTROL
    assert len(set(m.ARMS))==len(m.ARMS)
    assert 'gold' not in inspect.signature(m._selection_priority).parameters


def all_span_kinds_case():
    """Include conditions, outcomes, states and other, absent in the tiny fixture."""
    c = sample(n=12)
    for k in range(4, len(SPAN_KINDS) + 1):
        first = k + 1
        kind = SPAN_KINDS[k - 1]
        base = -0.3
        option = learned.Option(first, first + 1, k, base,
            learned._feature(margin=0.8, p_cond=.55, width=1, n=12,
                first=first, kind=k, original=False, base=base, top=False),
            (first, first + 1, kind), False)
        c.added.case.options.append(option)
    c.added.case.options.sort(key=lambda o: (o.end, o.start, o.kind))
    c.added.features = prior._encode(c.added.case).features
    c.prop_features = tuple(cov._proposal_features(o, c.added.case.n_tokens)
                            for o in c.added.case.options)
    c.is_new = tuple(not o.anchored for o in c.added.case.options)
    c.base_scores = tuple(float(o.base) * (o.end - o.start)
                          for o in c.added.case.options)
    return c


def test_full_seven_kind_contract_for_features_type_offsets_and_training():
    c = all_span_kinds_case()
    assert m.KIND_COUNT == len(SPAN_KINDS) == 7
    assert m.DIM == cov.PROP_DIM + cov.SET_DIM + 1 + len(SPAN_KINDS)
    features, kinds, new, width, fixed = m._tensor_cache(c)
    assert set(kinds.tolist()) == set(range(len(SPAN_KINDS)))
    # Every candidate has a unique canonical semantic kind in the last K slots.
    assert (features[:, -m.KIND_COUNT:] > 0).sum(dim=1).tolist() == [1]*len(kinds)
    model = m.Discriminator()
    assert model.type_offset.numel() == len(SPAN_KINDS)
    scores = m._model_scores(c, model)
    assert scores.shape == (len(kinds),)
    assert torch.isfinite(scores).all()
    # This training path was exactly where the CUDA run crashed on kind=5.
    fitted, log = m._fit_hard([c], steps=2)
    assert log['positive'] >= 1 and log['hard_negative'] >= 1
    assert all(torch.isfinite(p).all() for p in fitted.parameters())
    balanced = m._restrict(c, 256, 'balanced')
    assert {o.kind for o in balanced.added.case.options} == set(range(1, len(SPAN_KINDS) + 1))


def test_unknown_semantic_kind_fails_closed_instead_of_index_crash():
    c = all_span_kinds_case()
    from dataclasses import replace
    c.added.case.options[-1] = replace(c.added.case.options[-1],
                                       kind=len(SPAN_KINDS) + 1)
    with pytest.raises(ValueError, match='G1_0076_UNKNOWN_SPAN_KIND'):
        m._tensor_cache(c)
    with pytest.raises(ValueError, match='G1_0076_UNKNOWN_SPAN_KIND'):
        m._features(c)


def test_balanced_budget_reserves_quota_for_all_canonical_kinds():
    c = sample(n=100)
    for k, label in enumerate(SPAN_KINDS, 1):
        for first in range(10, 55):
            base = 0.2 + k * 0.03
            option = learned.Option(first, first + 1, k, base,
                learned._feature(margin=2.0, p_cond=.7, width=1, n=100,
                    first=first, kind=k, original=False, base=base, top=False),
                (first, first + 1, label), False)
            c.added.case.options.append(option)
    c.added.case.options.sort(key=lambda o:(o.end, o.start, o.kind))
    c.added.features = prior._encode(c.added.case).features
    c.prop_features = tuple(cov._proposal_features(o, c.added.case.n_tokens)
                            for o in c.added.case.options)
    c.is_new = tuple(not o.anchored for o in c.added.case.options)
    c.base_scores = tuple(o.base * (o.end - o.start) for o in c.added.case.options)
    assert len([new for new in c.is_new if new]) >= 7 * 45
    selected = m._restrict(c, 256, 'balanced')
    assert sum(selected.is_new) == 256
    extras = {k:sum(o.kind == k for o,new in zip(
        selected.added.case.options, selected.is_new) if new)
        for k in range(1, len(SPAN_KINDS) + 1)}
    assert all(count >= 256 // len(SPAN_KINDS) for count in extras.values()), extras


def test_forced_negative_mining_adds_unseen_decoded_false_candidate():
    c=sample()
    cache=m._tensor_cache(c)
    pos,neg=m._hard_rows(c,cache,per_case=1)
    wrong=[j for j,o in enumerate(c.added.case.options)
           if o.char_key not in c.added.case.gold and j not in neg]
    assert wrong
    pos2,neg2=m._hard_rows(c,cache,per_case=1,forced=[wrong[0]])
    assert pos2==pos and wrong[0] in neg2
    with pytest.raises(ValueError, match='INVALID_FORCED_NEGATIVE'):
        m._hard_rows(c,cache,per_case=1,forced=[pos[0]])
    with pytest.raises(ValueError, match='FORCED_NEGATIVE_COUNT'):
        m._fit_hard([c],steps=1,forced_by_case=[(),()])


def test_structured_snapshots_track_partial_progress_instead_of_first_zero_exact():
    c=sample()
    model=m.Discriminator()
    _,detail=m._fit_structured([c],model,epochs=8)
    assert detail['best_quality']==max(detail['quality_by_epoch']+[
        list(m._training_quality([c],model)[0])])
    assert detail['best_epoch'] in range(9)
    assert len(detail['quality_by_epoch'])==detail['epochs_run']
    assert detail['remaining_train_object_errors'][0]['expected']==len(c.added.case.gold)
    assert all(len(q)==4 for q in detail['quality_by_epoch'])


def test_counterexample_mining_has_no_label_in_inference_and_retains_best():
    c=sample()
    baseline=m.Discriminator()
    initial,_=m._training_quality([c],baseline)
    mined,detail=m._mine_counterexamples([c],baseline,WEIGHTS,rounds=2,steps=15)
    result,_=m._training_quality([c],mined)
    assert result >= initial
    assert detail['best_quality']==list(result)
    assert len(detail['rounds'])<=2
    assert all('round' in line for line in detail['rounds'])
    assert 'gold' not in inspect.signature(m._predict).parameters
    assert 'gold' not in inspect.signature(m._model_scores).parameters
