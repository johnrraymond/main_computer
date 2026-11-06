"""Patch 1: local reserve, credit recovery, and delayed-release Hub Lab gates."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
from main_computer.slh.managed_gateway import ManagedScheduler, ManagedWorker
from main_computer.slh.scheduler_policy import DEFAULT_POLICY, compute_plan, normalize_policy
from tools.slh_seh_lab.decision_stress import run_worker_bank_acceptance


class _Socket:
    def send(self, frame):pass


class _Policy:
    def __init__(self):self.policy=dict(DEFAULT_POLICY)
    def get(self):return {'policy':self.policy}


def _bank(n, credits=0):
    policy=_Policy()
    balance={'wei':int(credits*CREDIT_WEI_PER_CREDIT)}
    scheduler=ManagedScheduler(policy_store=policy,credit_provider=lambda:{
        'state':'CURRENT','balance':{'available_credit_wei':str(balance['wei'])}})
    for number in range(n):
        scheduler.register(ManagedWorker(f'w{number}',_Socket(),{'models':['model'], 'capabilities':['chat.completions']},['model']))
    return scheduler,balance,policy


def test_lab_worker_bank_acceptance():
    result=run_worker_bank_acceptance()
    assert result['status']=='PASS'
    assert all(result['gates'].values())


def test_low_balance_recovery_then_funded_reserve():
    bank,balance,_=_bank(1,0)
    assert bank.plan().desired==('w0',)
    balance['wei']=CREDIT_WEI_PER_CREDIT
    assert bank.plan().desired==()
    bank2,balance2,_=_bank(2,0)
    assert set(bank2.plan().desired)=={'w0','w1'}
    balance2['wei']=CREDIT_WEI_PER_CREDIT
    assert len(bank2.plan().desired)==1


def test_credit_balance_stale_fails_toward_earning():
    healthy=[{'device_id':'w0','active':True,'authorized':True}]
    for observation in (None,{'state':'STALE','available_credit_wei':str(10*CREDIT_WEI_PER_CREDIT)}):
        plan=compute_plan(DEFAULT_POLICY,healthy,set(),credit_meter=observation)
        assert plan.desired==('w0',)
        assert plan.reason=='CREDIT_RECOVERY'


def test_withdrawal_pending_prevents_both_kinds_of_reservation():
    bank,_,_=_bank(2,0)
    assert bank.mark_advertised('w0',True)
    assert bank.mark_advertised('w1',True)
    assert bank.request_private_capacity(model='model')=='w0'
    assert bank.withdrawals()==('w0',)
    try:bank.reserve_private('private',model='model')
    except RuntimeError:pass
    else:assert False,'premature local execution'
    assert bank.reserve_public('public',device_id='w1',work={'model':'model','operation':'chat.completions'}).device_id=='w1'
    bank.release('public')
    assert bank.mark_advertised('w0',False)
    assert bank.reserve_private('private-2',model='model').device_id=='w0'


def test_concurrent_local_reservations_cannot_duplicate_worker():
    bank,_,_=_bank(1,10)
    started=Event();finish=Event()
    def task(n):
        started.wait()
        try:return bank.reserve_private(f'job-{n}',model='model')
        except RuntimeError:return None
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures=[executor.submit(task,i) for i in range(12)]
        started.set()
        selected=[f.result() for f in futures]
    assert sum(w is not None for w in selected)==1


def test_reallocation_preserves_busy_public_and_requires_ack():
    bank,balance,policy=_bank(1,0)
    bank.mark_advertised('w0',True)
    worker=bank.reserve_public('public',device_id='w0',work={'model':'model','operation':'chat.completions'})
    balance['wei']=CREDIT_WEI_PER_CREDIT
    plan=bank.plan()
    assert 'w0' in plan.desired and 'w0' in plan.occupied_public
    bank.release('public')
    assert 'w0' in bank.plan().draining
    assert bank.snapshot()[0]['public_advertised']
    bank.mark_advertised('w0',False)  # simulated release confirmation
    assert bank.reserve_private('local',model='model') is worker


def test_invalid_credit_reserve_policy():
    for invalid in (-1,'NaN','Infinity',float('inf'),object()):
        try:normalize_policy({'local_credit_reserve_credits':invalid})
        except Exception:pass
        else:assert False,invalid


def test_custom_dedicated_private_worker_preserved_in_recovery():
    bank,balance,policy=_bank(2,0)
    policy.policy['dedicated_private']=['w0']
    assert bank.plan().desired==('w1',)
    assert bank.reserve_private('reserved',model='model').device_id=='w0'


def test_recovery_respects_explicit_admin_public_cap():
    bank,_,policy=_bank(10,0)
    policy.policy['max_public_percent']=50
    assert len(bank.plan().desired)==5
    policy.policy['max_public_percent']=0
    assert bank.plan().desired==()


def test_custom_credit_reserve_changes_recovery_mode():
    bank,balance,policy=_bank(1,0.5)
    assert bank.plan().desired==('w0',)
    policy.policy['local_credit_reserve_credits']='0.4'
    assert bank.plan().desired==()
