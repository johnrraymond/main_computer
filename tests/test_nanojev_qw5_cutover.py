"""Source-pinned new HybridBeast cutover; no old certificate transfer."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_qw5 as qw
from tools import nanojev_qw5_join as join
from tools import nanojev_qw5_cutover as cut


class TinyBank(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm = nn.LayerNorm(16)
        self.sign_head = nn.Linear(16, 3)
        self.digit_head = nn.Linear(16, 70)

    def forward(self, x):
        return None


def fixture(monkeypatch, tmp_path):
    root = tmp_path / 'campaign'
    (root / 'stages' / 'outward_64').mkdir(parents=True)
    (root / 'hard_emp_frontier_v2').mkdir()
    files = {'stage':root/'stages'/'outward_64'/'latest.pt',
             'hard':root/'hard_emp_frontier_v2'/'latest.pt',
             'left':root/'source_left.pt','right':root/'source_right.pt'}
    for name,p in files.items():
        p.write_bytes(('new:'+name).encode())
    ancestor = tmp_path/'ancestor';ancestor.mkdir()
    (ancestor/'config.json').write_text(json.dumps({'schema':join.SCHEMA,'source':{
      'sha256':{k:'old:'+k for k in files}}}))
    (ancestor/'latest.pt').write_bytes(b'old-adapters')
    accepted = tmp_path/'old_ladder';accepted.mkdir()
    old_src = {'qw5_run':str(ancestor.resolve()),'qw5_config_sha256':qw.sha(ancestor/'config.json'),
               'qw5_checkpoint_sha256':qw.sha(ancestor/'latest.pt')}
    (accepted/'config.json').write_text(json.dumps({'source':old_src}))
    (accepted/'ledger.json').write_text(json.dumps({'passed':['Z0','Z1','Z2']}))
    torch.manual_seed(113)
    donor_model = qw.QW5({name:TinyBank() for name in qw.HEAD_NAMES})
    donor_state = join.learned_state(donor_model)
    donor_state['gains'] = torch.arange(5,dtype=torch.float32) * .123
    torch.save({'schema':'nanojev.qw5.zero-balance-hawk-dove-ladder.v1.accepted',
                'trainable':donor_state,'passed':['Z0','Z1','Z2']},accepted/'accepted.pt')
    def fake_sources(campaign,stage=None,*,hard_state_dir='hard_emp',allow_missing=False):
        assert Path(campaign).resolve()==root.resolve()
        return files
    def fake_load(campaign, ladder_output, *, stage=None, device='cpu', hard_state_dir='hard_emp', source_files=None):
        paths = source_files or files
        hashes={name:qw.sha(path) for name,path in paths.items()}
        source={'campaign':str(root.resolve()),'ladder_output':str(Path(ladder_output).resolve()),
                'stage_radius':64,'stage_step':19500,'hard_state_dir':hard_state_dir,
                'specialist_source':'right.right','sha256':hashes}
        return {name:TinyBank() for name in qw.HEAD_NAMES},source
    monkeypatch.setattr(qw,'source_paths',fake_sources)
    monkeypatch.setattr(qw,'load_five',fake_load)
    output = tmp_path/'new_hybrid'
    args = SimpleNamespace(campaign=root,ladder_output=tmp_path/'ladder',hard_state_dir='hard_emp_frontier_v2',
            stage=None,output=output, legacy_qw5=ancestor, warm_start_accepted=None,
            model='test-model',revision='fixed',radius=128,train_cards=16,dev_cards=8,seed=20261026)
    return args,files,ancestor,accepted,donor_state


def test_cutover_creates_four_immutable_snapshots_and_preserves_old_model(monkeypatch,tmp_path):
    args, files, old, accepted, donor = fixture(monkeypatch,tmp_path)
    old_bytes=(old/'latest.pt').read_bytes()
    manifest=cut.prepare(args)
    assert manifest['historical_certifications_transferred']==[]
    assert manifest['new_ladder_starts_at']=='Z0'
    assert manifest['initialization']=='fresh'
    assert cut.verify(args)['new_stage1_trained'] is False
    assert (old/'latest.pt').read_bytes()==old_bytes
    assert not (args.output/'ledger.json').exists()
    for key,path in files.items():
        assert (args.output/'source_archive'/(key+'.pt')).read_bytes()==path.read_bytes()
    files['hard'].write_bytes(b'reverse-progress-again')
    files['stage'].write_bytes(b'later-ordinary-stage')
    assert cut.verify(args)['source_snapshots_verified'] is True
    assert join.verify_sources(json.loads((args.output/'config.json').read_text()),args.output)['hard'].parent.name=='source_archive'
    with pytest.raises(FileExistsError,match='OUTPUT_ALREADY_EXISTS'):
        cut.prepare(args)


def test_cutover_opt_in_donor_is_only_trainable_initialization(monkeypatch,tmp_path):
    args, files, old, accepted, state=fixture(monkeypatch,tmp_path)
    args.warm_start_accepted=accepted
    donor_hash=qw.sha(accepted/'accepted.pt')
    manifest=cut.prepare(args)
    cp=torch.load(args.output/'latest.pt',map_location='cpu',weights_only=True)
    assert cp['step']==0
    assert cp['optimizer'] is None
    assert manifest['initialization']=='experimental_donor_adapter_only'
    assert manifest['donor_witness']['accepted_sha256']==donor_hash
    assert torch.equal(cp['trained_state']['gains'],state['gains'])
    assert manifest['historical_certifications_transferred']==[]
    assert cut.verify(args)['fresh_ladder_starts_at']=='Z0'
    assert (accepted/'accepted.pt').is_file()


def test_cutover_rejects_forged_acceptance_and_never_marks_old_rungs_passed(monkeypatch,tmp_path):
    args,files,old,accepted,state=fixture(monkeypatch,tmp_path)
    args.warm_start_accepted=accepted
    (accepted/'ledger.json').write_text(json.dumps({'passed':['Z0','Z1','Z2','R0']}))
    with pytest.raises(RuntimeError,match='DONOR_NOT_AUTHENTIC'):
        cut.prepare(args)
    assert not args.output.exists()


def test_cutover_rejects_unsafe_destination_and_source_drift(monkeypatch,tmp_path):
    args,files,old,accepted,state=fixture(monkeypatch,tmp_path)
    args.output=args.campaign/'nested'
    with pytest.raises(ValueError,match='OUTSIDE_LIVE_EMP'):
        cut.prepare(args)
    args.output=tmp_path/'new_hybrid'
    base=qw.load_five
    def racing(*a,**kw):
        models,source=base(*a,**kw)
        if kw.get('source_files') is None:
            files['hard'].write_bytes(b'race')
        return models,source
    monkeypatch.setattr(qw,'load_five',racing)
    with pytest.raises(RuntimeError,match='LIVE_SOURCE_CHANGED_DURING_INSPECTION'):
        cut.inspect(args)


def test_cutover_detects_archive_corruption_and_disabled_stages(monkeypatch,tmp_path):
    args, files, old, accepted, state=fixture(monkeypatch,tmp_path)
    cut.prepare(args)
    (args.output/'source_archive'/'hard.pt').write_bytes(b'corrupt')
    with pytest.raises(RuntimeError,match='SOURCE_ARCHIVE_CORRUPT'):
        cut.verify(args)


def test_cutover_trained_status_does_not_claim_certification(monkeypatch,tmp_path):
    args, files, old, accepted, state=fixture(monkeypatch,tmp_path)
    cut.prepare(args)
    cp_path=args.output/'latest.pt'; cp=torch.load(cp_path,weights_only=True)
    cp['step']=500;join.write_state(cp_path,cp)
    status=cut.verify(args)
    assert status['new_stage1_trained']
    assert not status['old_certifications_carried']
    assert status['fresh_ladder_starts_at']=='Z0'
    assert status['hybridlink_feedback']=='DISABLED_UNCERTIFIED'


def test_cutover_rejects_wrong_legacy_run_identity(monkeypatch,tmp_path):
    args, files, old, accepted, state=fixture(monkeypatch,tmp_path)
    config=old/'config.json'; cfg=json.loads(config.read_text());cfg['schema']='other';config.write_text(json.dumps(cfg))
    with pytest.raises(RuntimeError,match='LEGACY_SCHEMA_DRIFT'):
        cut.prepare(args)
    assert not args.output.exists()
