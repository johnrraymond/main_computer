"""Corpus/gating tests run on CPU; no Qwen or checkpoints needed."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
import pytest

from tools import nanojev_qw5_balance_ladder as l
from tools import nanojev_hawk_dove_million as hd


@pytest.mark.parametrize('rung', l.RUNG_ORDER)
def test_all_rungs_have_disjoint_family_splits_and_balanced_strata(rung):
    cards=l.make_cards(rung,20261027)
    audit=l.audit_splits(cards)
    assert audit['families'] >= 25
    for split in ('train','dev','test'):
        sampled=l.sample_split(cards,split,40,20261027)
        assert len(sampled)==40
        pair_ids={x.pair_id for x in sampled}
        assert len(pair_ids)==20
        assert all(sum(x.pair_id==pid for x in sampled)==2 for pid in pair_ids)
        assert len([c for c in sampled if c.zero]) >= 2
        assert len([c for c in sampled if c.answer > 0]) >= 2
        assert len([c for c in sampled if c.answer < 0]) >= 2
        assert len([c for c in sampled if c.near and c.answer > 0]) >= 2
        assert len([c for c in sampled if c.near and c.answer < 0]) >= 2
        assert all(c.split==split for c in sampled)



@pytest.mark.parametrize('rung', l.RUNG_ORDER)
def test_sampler_matches_scored_signed_residual_bands_at_all_splits(rung):
    """Regression: Z0 independent TEST previously had zero far-positive cards.

    The sampler must stratify by the EXACT same signed target boundaries as
    assess(), not by Card.near (which measures source displacement instead).
    """
    cards=l.make_cards(rung,20261027)
    for split in ('train','dev','test'):
        for size in (40,64,128):
            sampled=l.sample_split(cards,split,size,20261027)
            targets=[c.answer for c in sampled]
            strata={
                'zero': sum(d==0 for d in targets),
                'near_positive': sum(0<d<=16 for d in targets),
                'near_negative': sum(-16<=d<0 for d in targets),
                'far_positive': sum(d>16 for d in targets),
                'far_negative': sum(d < -16 for d in targets),
            }
            assert all(count>=2 for count in strata.values()), (rung, split, size, strata)
            assert all(count%2==0 for count in strata.values()), (rung, split, size, strata)


def test_z0_original_seed_test_has_far_positive_examples_in_scored_band():
    cards=l.make_cards('Z0',20261027)
    sampled=l.sample_split(cards,'test',64,20261027)
    counts=l.band_counts(sampled)
    assert counts['far_positive']>=2, counts
    assert counts['far_negative']>=2, counts
    assert counts['near_positive']>=2, counts
    assert counts['near_negative']>=2, counts
    assert counts['zero']>=2, counts


def test_preview_includes_scored_band_coverage_for_every_split():
    args=SimpleNamespace(seed=20261027,train_cards=128,dev_cards=64,test_cards=64)
    for row in l.preview(args):
        assert set(row['scored_bands']) == {'train','dev','test'}
        for split, counts in row['scored_bands'].items():
            assert sum(counts.values()) == row['selected'][split]
            assert all(n>=2 for n in counts.values()), (row['rung'],split,counts)


def test_story_tiers_never_receive_the_oracle_totals_as_numeric_operands():
    for tier in ('T01','T02','T03','T04','T05','T06','T07','T08'):
        cards=l.game_cards(tier,20261027)
        assert all(c.operands==c.source for c in cards)
        assert all(c.operands[0]+c.operands[1]==l.TOTAL for c in cards)


def test_exposed_totals_are_used_only_in_B0_and_T00():
    for rung in ('B0','T00'):
        assert all(c.operands[0]+c.operands[1]==c.answer for c in l.game_cards(rung,20261027))


def test_exact_payoff_balance_and_signed_neighborhoods():
    for p,q in l.PAYOFF_PAIRS:
        center=l.TOTAL*p//(p+q)
        for ch in l.SHIFTS:
            for cd in l.SHIFTS:
                pp=l.payoffs(p,q,ch,cd)
                for delta in (-2,-1,0,1,2):
                    a=center+delta
                    x,y,_=hd.oracle(a,l.TOTAL-a,pp)
                    assert int(x-y)==-(p+q)*delta
                # Same common offset to BOTH strategies is a payoff
                # invariance, not an additional label or oracle input.
                shifted={k: v+Decimal(3) for k,v in pp.items()}
                assert hd.oracle(center, l.TOTAL-center, shifted)[0] - hd.oracle(center,l.TOTAL-center,shifted)[1]==0


def test_raw_T08_has_no_structured_argmax_and_supplies_payoff_semantics():
    c=l.game_cards('T08',20261027)[0]
    assert 'ARGMAX' not in c.prompt and '(MUL' not in c.prompt
    assert 'If you play Hawk' in c.prompt
    assert 'Signed Hawk-minus-Dove difference' in c.prompt


def test_tier_exposes_one_or_both_or_no_payoff_totals_by_contract():
    for rung in hd.TIERS:
        c=l.game_cards(rung,20261027)[0]
        p,q,ch,cd=map(int,c.family.split(':')[1:])
        pp=l.payoffs(p,q,ch,cd)
        x,y,_=hd.oracle(*c.source,pp)
        if rung=='T00':
            assert f'  {int(x)}\n' in c.prompt
            assert f'  {int(y)})' in c.prompt
        if rung=='T01':
            assert f'  {int(x)}\n' in c.prompt
            assert 'DOVE_HAWK_PAYOFF' in c.prompt
        if rung=='T02':
            assert f'  {int(y)})' in c.prompt
            assert 'HAWK_HAWK_PAYOFF' in c.prompt


def test_gate_demands_positive_qwen_gain_only_when_computation_hidden():
    args=SimpleNamespace(min_exact=.8,min_balance=.8,min_retention=.8,min_conditioning_gain=.02)
    def assessment(exact=1., gain=0.):
        return {'groups':{name:{'n':4,'exact':exact} for name in
                  ('total','zero','near_positive','near_negative','far_positive','far_negative')},
                'conditioning_gain':gain,'mismatch_gain':gain,
                'paired_invariant_accuracy':exact}
    ok,issues=l.pass_gate(assessment(),{},args,False)
    assert ok and not issues
    ok,issues=l.pass_gate(assessment(),{},args,True)
    assert not ok and 'qwen_conditioning' in issues
    ok,issues=l.pass_gate(assessment(gain=.08),{'Z0':assessment(exact=.79)},args,True)
    assert not ok and 'retention:Z0:zero' in issues
    ok,issues=l.pass_gate(assessment(gain=.08),{'Z0':assessment(exact=.9)},args,True)
    assert ok


def test_cli_smoke_advances_one_rung_without_touching_qw5_source(monkeypatch,tmp_path):
    import torch
    from torch import nn
    from tools import nanojev_qw5_join as join
    from tools import nanojev_qw5 as qw
    from tools import nanojev_qwen_hybridagent as hybrid
    original=tmp_path/'qw5-original';original.mkdir()
    (original/'config.json').write_text('{}')
    (original/'latest.pt').write_bytes(b'unmodified-source')
    class FakeQW5(nn.Module):
        def __init__(self):
            super().__init__()
            self.bias=nn.Parameter(torch.tensor(0.1))
        def forward(self,x,h):
            return {'answer':{'x':self.bias.expand(len(x))}}
    monkeypatch.setattr(join,'restore',lambda *_args:(FakeQW5(),{'step':500},{'qwen':{'model':'fake','revision':'v1'}}))
    monkeypatch.setattr(hybrid,'load_qwen',lambda *_args,**_kw:(nn.Linear(1,1),None,{'hidden_size':1024}))
    monkeypatch.setattr(l,'encode',lambda _q,_t,rows,_d:torch.zeros(len(rows),4,1024))
    monkeypatch.setattr(qw,'qw5_loss',lambda out,labels: (out['answer']['x']-1).square().mean())
    def pass_assessment(*_args):
        return {'groups':{name:{'n':4,'exact':1.0} for name in
                ('total','zero','near_positive','near_negative','far_positive','far_negative')},
                'conditioning_gain':.1,'mismatch_gain':.1,
                'paired_invariant_accuracy':1.0}
    monkeypatch.setattr(l,'assess',pass_assessment)
    target=tmp_path/'ladder'
    args=['run','--qw5-run',str(original),'--output',str(target),
          '--device','cpu','--train-cards','20','--dev-cards','20',
          '--test-cards','20','--steps-per-rung','1','--probe-every','1',
          '--batch-size','4','--through','Z0']
    assert l.main(args)==0
    ledger=l.json.loads((target/'ledger.json').read_text())
    assert ledger['passed']==['Z0']
    assert ledger['attempts'][0]['test']['groups']['total']['exact']==1.
    assert (original/'latest.pt').read_bytes()==b'unmodified-source'
    assert l.main(args)==2  # through a rung already accepted: no silent replay


def test_game_payoff_translation_changes_totals_but_preserves_difference():
    for tier in ('B0','T00','T01','T08'):
        rows=l.game_cards(tier,20261027)
        pairs={}
        for c in rows:
            pairs.setdefault(c.pair_id,[]).append(c)
        for pair in pairs.values():
            assert len(pair)==2
            one,two=pair
            assert one.answer==two.answer
            assert one.prompt!=two.prompt
            if tier in ('B0','T00'):
                assert one.operands!=two.operands
                assert one.operands[0]+one.operands[1]==two.operands[0]+two.operands[1]
            else:
                assert one.operands==two.operands


def test_independent_test_failure_locks_campaign(monkeypatch,tmp_path):
    import torch
    from torch import nn
    from tools import nanojev_qw5_join as join
    from tools import nanojev_qw5 as qw
    from tools import nanojev_qwen_hybridagent as hybrid
    original=tmp_path/'qw5-original';original.mkdir()
    (original/'config.json').write_text('{}')
    (original/'latest.pt').write_bytes(b'unmodified-source')
    class FakeQW5(nn.Module):
        def __init__(self): super().__init__();self.w=nn.Parameter(torch.tensor(.1))
        def forward(self,x,h):return {'answer':{'w':self.w.expand(len(x))}}
    monkeypatch.setattr(join,'restore',lambda *_args:(FakeQW5(),{'step':500},{'qwen':{'model':'fake','revision':'v1'}}))
    monkeypatch.setattr(hybrid,'load_qwen',lambda *_args,**_kw:(nn.Linear(1,1),None,{'hidden_size':1024}))
    monkeypatch.setattr(l,'encode',lambda _q,_t,rows,_d:torch.zeros(len(rows),4,1024))
    monkeypatch.setattr(qw,'qw5_loss',lambda out,labels:(out['answer']['w']-1).square().mean())
    def assessment(_model,_pack,rows):
        exact=.5 if rows[0].split=='test' else 1.0
        return {'groups':{name:{'n':4,'exact':exact} for name in
                  ('total','zero','near_positive','near_negative','far_positive','far_negative')},
                'conditioning_gain':.1,'mismatch_gain':.1,
                'paired_invariant_accuracy':exact}
    monkeypatch.setattr(l,'assess',assessment)
    target=tmp_path/'ladder'
    args=['run','--qw5-run',str(original),'--output',str(target),'--device','cpu',
          '--train-cards','20','--dev-cards','20','--test-cards','20',
          '--steps-per-rung','1','--probe-every','1','--batch-size','4','--through','Z0']
    assert l.main(args)==0
    ledger=l.json.loads((target/'ledger.json').read_text())
    assert ledger['independent_test_failed'] is True
    assert ledger['passed']==[]
    assert ledger['attempts'][0]['gate']=='DEV_PASS_TEST_FAIL'
    assert l.main(args)==2

@pytest.mark.parametrize('rung', (*l.R0_RUNGS,'R1','R2'))
def test_transition_rungs_preserve_split_and_balance_classes(rung):
    cards=l.make_cards(rung,20261027)
    assert len(cards)>200
    l.audit_splits(cards)
    for split,size in (('train',128),('dev',64),('test',64)):
        picked=l.sample_split(cards,split,size,20261027)
        assert len(picked)==size
        assert all(c.split==split for c in picked)
        bands=l.band_counts(picked)
        assert all(value>=2 for value in bands.values()),(rung,split,bands)
        pairs={}
        for c in picked:
            pairs.setdefault(c.pair_id, []).append(c)
        assert all(len(group)==2 and len({c.answer for c in group})==1 for group in pairs.values())
        if l.is_r0(rung):
            mags={}
            for c in picked:
                mags[l.residual_scale_band(c.answer)]=mags.get(l.residual_scale_band(c.answer),0)+1
            assert set(mags)==set(l.rung_scale_names(rung)) and min(mags.values())>=2
        if rung=='R2':
            contrasts={}
            for c in picked:
                contrasts.setdefault(c.contrast_id,[]).append(c)
            assert len(contrasts)==size//4
            for group in contrasts.values():
                assert len(group)==4
                assert len({c.operands for c in group})==1
                assert len({c.answer for c in group}) == (1 if group[0].answer==0 else 2)
                assert all(c.operands==c.source for c in group)


@pytest.mark.parametrize('rung, max_residual', [('R0a',4096),('R0b',100000),('R0c',1000000)])
def test_r0_expands_cancellation_residuals_progressively(rung,max_residual):
    cards=l.transition_cards(rung,20261027)
    assert max(abs(c.answer) for c in cards)<=max_residual
    assert max(abs(c.answer) for c in cards)>=(max_residual*.85)
    assert all(sum(c.operands)==c.answer for c in cards)
    assert all(max(abs(x) for x in c.operands)<=l.TRANSITION_MAX for c in cards)
    for split in ('train','dev','test'):
        sampled=l.sample_split(cards,split,128,20261027)
        assert max(abs(c.answer) for c in sampled) > max_residual*.5
        for key in l.rung_scale_names(rung):
            assert sum(l.residual_scale_band(c.answer)==key for c in sampled)>=2


def test_r0_far_residual_magnitudes_disjoint_between_train_dev_test():
    for rung in l.R0_RUNGS:
        cards=l.transition_cards(rung,20261027)
        heldout={split:{abs(c.answer) for c in cards if c.split==split and abs(c.answer)>16}
                 for split in ('train','dev','test')}
        for a,b in (('train','dev'),('train','test'),('dev','test')):
            assert heldout[a].isdisjoint(heldout[b]), (rung,a,b,heldout[a]&heldout[b])
        assert all(len(v)>=12*l.R0_BANDS[rung] for v in heldout.values())


def test_new_r0_defaults_are_large_balanced_and_reproducible():
    args=l.parser().parse_args(['preview'])
    assert (args.r0_train_cards,args.r0_dev_cards,args.r0_test_cards)==(512,128,128)
    first=l.preview(args)
    second=l.preview(args)
    assert first==second
    for row in first:
        if l.is_r0(row['rung']):
            assert row['selected']=={'train':512,'dev':128,'test':128}
            for split, groups in row['residual_scale_bands'].items():
                assert sum(groups.values())==row['selected'][split]
                assert set(groups)==set(l.rung_scale_names(row['rung']))
                assert min(groups.values()) >= 2


def test_each_new_r0_scale_gate_remains_eighty_percent():
    from types import SimpleNamespace
    args=SimpleNamespace(min_exact=.8,min_balance=.8,min_retention=.8,min_conditioning_gain=.02)
    for rung in l.R0_RUNGS:
        group_names=l.rung_scale_names(rung)
        report={'groups':{name:{'n':4,'exact':1.0} for name in
                ('total','zero','near_positive','near_negative','far_positive','far_negative')},
                'paired_invariant_accuracy':1.0,
                'residual_scale_groups':{name:{'n':4,'exact':1.0} for name in group_names},
                'conditioning_gain':0.0,'mismatch_gain':0.0}
        assert l.pass_gate(report,{},args,False)[0]
        report['residual_scale_groups'][group_names[-1]]['exact']=.75
        ok,issues=l.pass_gate(report,{},args,False)
        assert not ok and 'residual_scale:'+group_names[-1] in issues


def test_r1_symbolic_variation_does_not_change_oracle_target():
    cards=l.transition_cards('R1',20261027)
    pairs={}
    for c in cards:
        pairs.setdefault(c.pair_id,[]).append(c)
        assert sum(c.operands)==c.answer
    assert all(len(group)==2 and group[0].prompt!=group[1].prompt
               and group[0].operands==group[1].operands for group in pairs.values())
    text=' '.join(c.prompt for c in cards)
    assert 'plus' in text or '+ (' in text
    assert 'minus' in text and 'signed difference' in text and 'advantage' in text


def test_r2_same_numeric_inputs_must_yield_opposite_answers():
    pairs={}
    for c in l.transition_cards('R2',20261027):
        pairs.setdefault(c.contrast_id,[]).append(c)
        assert c.operands==c.source
    assert len(pairs)>100
    for members in pairs.values():
        assert len(members)==4
        assert len({c.operands for c in members})==1
        assert sum(c.answer for c in members)==0
        assert len({c.pair_id for c in members})==2
    args=SimpleNamespace(min_exact=.8,min_balance=.8,min_retention=.8,min_conditioning_gain=.02)
    sample={'groups':{name:{'n':8,'exact':1.} for name in
           ('total','zero','near_positive','near_negative','far_positive','far_negative')},
           'paired_invariant_accuracy':1.0,'directional_contrasts':8,
           'directional_contrast_accuracy':.5,'conditioning_gain':.8,'mismatch_gain':.8}
    ok,issues=l.pass_gate(sample,{},args,True)
    assert not ok and 'directional_contrast' in issues
    sample['directional_contrast_accuracy']=1.0
    sample['conditioning_gain']=0.
    ok,issues=l.pass_gate(sample,{},args,True)
    assert not ok and 'qwen_conditioning' in issues
    sample['conditioning_gain']=.8
    assert l.pass_gate(sample,{},args,True)[0]


def test_generated_examples_are_real_training_rows_and_grouped():
    data=l.example_cards(SimpleNamespace(seed=20261027,examples_per_rung=12))
    assert set(data)=={*l.R0_RUNGS,'R1','R2'}
    for rung,examples in data.items():
        train=l.make_cards(rung,20261027)
        assert all(row['split']=='train' for row in examples)
        assert len(examples)==12
        by_identity={(c.pair_id,c.prompt,c.answer,c.operands) for c in train}
        assert all((x['pair_id'],x['prompt'],x['target'],tuple(x['operands'])) in by_identity for x in examples)


def test_warm_start_from_accepted_z2_preserves_prior_run(monkeypatch,tmp_path):
    import torch
    from torch import nn
    from tools import nanojev_qw5_join as join
    from tools import nanojev_qwen_hybridagent as hybrid
    from tools import nanojev_qw5 as qw
    source=tmp_path/'qw5';source.mkdir()
    (source/'config.json').write_text('{}')
    (source/'latest.pt').write_bytes(b'original-qw5')
    class FakeQW5(nn.Module):
        def __init__(self):super().__init__();self.bias=nn.Parameter(torch.tensor(.1))
        def forward(self,x,h):return {'answer':{'v':self.bias.expand(len(x))}}
    monkeypatch.setattr(join,'restore',lambda *_a:(FakeQW5(),{'step':500},{'qwen':{'model':'fake','revision':'r'}}))
    monkeypatch.setattr(hybrid,'load_qwen',lambda *_a,**_k:(nn.Linear(1,1),None,{'hidden_size':1024}))
    monkeypatch.setattr(l,'encode',lambda _a,_b,c,_d:torch.zeros(len(c),4,1024))
    monkeypatch.setattr(qw,'qw5_loss',lambda output,lab:(output['answer']['v']-1).square().mean())
    monkeypatch.setattr(l,'assess',lambda model,pack,cards:{'groups':{b:{'n':4,'exact':1.0}
        for b in ('total','zero','near_positive','near_negative','far_positive','far_negative')},
        'paired_invariant_accuracy':1.0,'directional_contrasts':0,
        'residual_scale_groups':{},'conditioning_gain':.1,'mismatch_gain':.1})
    old=tmp_path/'old';old.mkdir()
    origin={'qw5_config_sha256':l.sha(source/'config.json'),
            'qw5_checkpoint_sha256':l.sha(source/'latest.pt'),
            'qw5_run':str(source),'source_step':500}
    legacy=list(('Z0','Z1','Z2','B0',*(f'T{i:02d}' for i in range(9))))
    (old/'config.json').write_text(l.json.dumps({'run_order':legacy,'source':origin,'seed':20261027}))
    (old/'ledger.json').write_text(l.json.dumps({'passed':['Z0','Z1','Z2'],'schema':'legacy'}))
    torch.save({'passed':['Z0','Z1','Z2'],'trainable':{'bias':torch.tensor(.5)}},old/'accepted.pt')
    new=tmp_path/'new'
    args=['run','--seed-accepted-from',str(old),'--qw5-run',str(source),'--output',str(new),
          '--device','cpu','--through','R0a','--steps-per-rung','1','--probe-every','1',
          '--train-cards','64','--dev-cards','64','--test-cards','64']
    assert l.main(args)==0
    ledger=l.json.loads((new/'ledger.json').read_text())
    assert ledger['passed']==['Z0','Z1','Z2','R0a']
    assert ledger['attempts'][0]['rung']=='R0a'
    assert torch.load(old/'accepted.pt',weights_only=True)['passed']==['Z0','Z1','Z2']
    assert (source/'latest.pt').read_bytes()==b'original-qw5'
    # The seed SHA is part of the run identity and cannot silently drift.
    (old/'ledger.json').write_text('{}')
    assert l.main(['status','--output',str(new)])==0
    assert l.main(args)==2


def test_r0a_warm_start_from_failed_v2_r0_uses_accepted_z2_only(monkeypatch,tmp_path):
    """Real user's history: v2 passed Z0-Z2, then failed R0 on DEV.

    A v3 output is seeded solely from v2 accepted.pt, not the rejected R0
    attempt, not a TEST result, and never alters the v2 campaign.
    """
    import torch
    from torch import nn
    from tools import nanojev_qw5_join as join
    from tools import nanojev_qwen_hybridagent as hybrid
    from tools import nanojev_qw5 as qw
    source=tmp_path/'qw5-cutover';source.mkdir()
    (source/'config.json').write_text('{}')
    (source/'latest.pt').write_bytes(b'frozen-stage1-QW5')

    class FakeQW5(nn.Module):
        def __init__(self):
            super().__init__()
            self.bias=nn.Parameter(torch.tensor(.1))
        def forward(self,x,h):
            return {'answer':{'v':self.bias.expand(len(x))}}

    monkeypatch.setattr(join,'restore',lambda *_a:(FakeQW5(),{'step':500},
                     {'qwen':{'model':'fake','revision':'r'}}))
    monkeypatch.setattr(hybrid,'load_qwen',lambda *_a,**_k:
                     (nn.Linear(1,1),None,{'hidden_size':1024}))
    monkeypatch.setattr(l,'encode',lambda _a,_b,c,_d:torch.zeros(len(c),4,1024))
    monkeypatch.setattr(qw,'qw5_loss',lambda output,lab:(output['answer']['v']-1).square().mean())
    monkeypatch.setattr(l,'assess',lambda model,pack,cards:{
        'groups':{b:{'n':4,'exact':1.0} for b in
            ('total','zero','near_positive','near_negative','far_positive','far_negative')},
        'paired_invariant_accuracy':1.,'directional_contrasts':0,
        'residual_scale_groups':{key:{'n':4,'exact':1.}
            for key in l.rung_scale_names(cards[0].rung)},
        'conditioning_gain':0.,'mismatch_gain':0.})
    v2=tmp_path/'v2';v2.mkdir()
    source_id={'qw5_config_sha256':l.sha(source/'config.json'),
               'qw5_checkpoint_sha256':l.sha(source/'latest.pt'),
               'qw5_run':str(source),'source_step':500}
    old_order=('Z0','Z1','Z2','R0','R1','R2','B0',*(f'T{i:02d}' for i in range(9)))
    (v2/'config.json').write_text(l.json.dumps({'schema':'v2','run_order':list(old_order),
                                'source':source_id,'seed':20261027}))
    (v2/'ledger.json').write_text(l.json.dumps({'passed':['Z0','Z1','Z2'],
          'stopped_at':'R0','attempts':[{'rung':'R0','gate':'FAIL','step':1000}]}))
    torch.save({'passed':['Z0','Z1','Z2'],'trainable':{'bias':torch.tensor(.55)}},
               v2/'accepted.pt')
    prior_files={n:(v2/n).read_bytes() for n in ('config.json','ledger.json','accepted.pt')}
    v3=tmp_path/'v3'
    argv=['run','--seed-accepted-from',str(v2),'--qw5-run',str(source),
          '--output',str(v3),'--device','cpu','--through','R0a',
          '--steps-per-rung','1','--probe-every','1',
          '--train-cards','64','--dev-cards','64','--test-cards','64',
          '--r0-train-cards','128','--r0-dev-cards','128','--r0-test-cards','128']
    assert l.main(argv)==0
    accepted=torch.load(v3/'accepted.pt',weights_only=True)
    ledger=l.json.loads((v3/'ledger.json').read_text())
    assert accepted['passed']==ledger['passed']==['Z0','Z1','Z2','R0a']
    assert ledger['attempts'][0]['rung']=='R0a'
    assert all((v2/n).read_bytes()==b for n,b in prior_files.items())
    assert (source/'latest.pt').read_bytes()==b'frozen-stage1-QW5'


def test_rejected_test_ancestor_is_not_a_valid_warm_start(monkeypatch,tmp_path):
    # A once-opened TEST failure must NEVER be imported as a valid seed.
    import torch
    from torch import nn
    from tools import nanojev_qw5_join as join
    source=tmp_path/'qw5';source.mkdir()
    (source/'config.json').write_text('{}')
    (source/'latest.pt').write_bytes(b'qw5')
    monkeypatch.setattr(join,'restore',lambda *_args:(nn.Linear(1,1),{'step':1},
                     {'qwen':{'model':'fake','revision':'r'}}))
    old=tmp_path/'old';old.mkdir()
    prov={'qw5_config_sha256':l.sha(source/'config.json'),
          'qw5_checkpoint_sha256':l.sha(source/'latest.pt'),
          'qw5_run':str(source),'source_step':1}
    old_order=('Z0','Z1','Z2','R0','R1','R2','B0',*(f'T{i:02d}' for i in range(9)))
    (old/'config.json').write_text(l.json.dumps({'run_order':list(old_order),
                            'source':prov,'seed':20261027}))
    (old/'ledger.json').write_text(l.json.dumps({'passed':['Z0','Z1','Z2'],
                       'independent_test_failed':True}))
    torch.save({'passed':['Z0','Z1','Z2'],'trainable':{}},old/'accepted.pt')
    assert l.main(['run','--qw5-run',str(source),'--seed-accepted-from',str(old),
                   '--output',str(tmp_path/'new'),'--device','cpu',
                   '--through','R0a'])==2
