"""CPU-only regression tests for v3 campaign restart v2 and Windows subprocess arguments."""
import json
from pathlib import Path
import sys
import pytest
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_altreg_math_campaign_v3 as campaign


def args(runs,*flags):
    return campaign.parser().parse_args(['--stage','pilot','--backbones','qwen',
      '--arms','factorized','attention','--runs-root',str(runs),*flags])


def root(runs,arm):
    return runs/f'independent_altreg_math_qwen_v3_{arm}'


def checkpoint(runs,arm,step,best=False):
    p=root(runs,arm);p.mkdir(parents=True,exist_ok=True)
    torch.save({'step':step,'config':{'backbone':'qwen','arm':arm}},p/'latest.pt')
    if best:(p/'best.pt').write_bytes(b'exists')


class Result:
    returncode=0


def test_resume_mixed_states_and_windows_strings(tmp_path,capsys):
    checkpoint(tmp_path,'factorized',1000)
    calls=[]
    def run(argv,check):calls.append(argv);return Result()
    assert campaign.execute(args(tmp_path,'--steps','2000'),runner=run)==0
    assert len(calls)==2
    assert all(isinstance(item,str) for call in calls for item in call)
    fac,att=calls
    assert fac[-3:]==['--resume','--additional-steps','1000']
    assert '--resume' not in att
    messages=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e['state'] for e in messages if e['event']=='altreg_campaign_pilot_plan']==['resume','start']


def test_resume_flag_also_auto_plans_mixed_states(tmp_path):
    checkpoint(tmp_path,'factorized',1700)
    calls=[]
    def run(argv,check):calls.append(argv);return Result()
    assert campaign.execute(args(tmp_path,'--steps','2000','--resume'),runner=run)==0
    assert calls[0][-3:]==['--resume','--additional-steps','300']
    assert '--resume' not in calls[1]


def test_completed_arm_skipped(tmp_path):
    checkpoint(tmp_path,'factorized',2000,best=True)
    calls=[]
    assert campaign.execute(args(tmp_path),runner=lambda argv,check:(calls.append(argv) or Result()))==0
    assert len(calls)==1
    assert calls[0][calls[0].index('--arm')+1]=='attention'


def test_nonempty_without_checkpoint_remains_untouched(tmp_path):
    p=root(tmp_path,'factorized');p.mkdir(parents=True)
    (p/'fork.json').write_text('{}')
    with pytest.raises(RuntimeError,match='NONEMPTY_WITHOUT_LATEST'):
        campaign.execute(args(tmp_path),runner=lambda *a,**k:Result())
    assert (p/'fork.json').read_text()=='{}'


def test_corrupt_or_ahead_refused(tmp_path):
    p=root(tmp_path,'factorized');p.mkdir(parents=True)
    (p/'latest.pt').write_bytes(b'bad')
    with pytest.raises(RuntimeError,match='LATEST_UNREADABLE'):
        campaign.execute(args(tmp_path),runner=lambda *a,**k:Result())
    checkpoint(tmp_path,'factorized',2001)
    with pytest.raises(RuntimeError,match='TARGET_BELOW_EXISTING_STEP'):
        campaign.execute(args(tmp_path),runner=lambda *a,**k:Result())


def test_relative_extension_requires_one_arm_and_resume(tmp_path):
    checkpoint(tmp_path,'factorized',1000)
    with pytest.raises(RuntimeError,match='SINGLE_ARM_ONLY'):
        campaign.execute(args(tmp_path,'--resume','--additional-steps','100'),runner=lambda *a,**k:Result())
    a=campaign.parser().parse_args(['--stage','pilot','--backbones','qwen','--arms','factorized',
       '--runs-root',str(tmp_path),'--resume','--additional-steps','100'])
    calls=[]
    campaign.execute(a,runner=lambda argv,check:(calls.append(argv) or Result()))
    assert calls[0][-3:]==['--resume','--additional-steps','100']


def test_dry_run_logs_actions_without_modification(tmp_path,capsys):
    checkpoint(tmp_path,'factorized',1000)
    checkpoint(tmp_path,'attention',2000,best=True)
    a=args(tmp_path,'--dry-run')
    assert campaign.execute(a)==0
    events=[json.loads(s) for s in capsys.readouterr().out.splitlines()]
    plans=[e for e in events if e['event']=='altreg_campaign_pilot_plan']
    assert [e['state'] for e in plans]==['resume','already-complete']
    assert len([e for e in events if e['event']=='altreg_campaign_command'])==1
