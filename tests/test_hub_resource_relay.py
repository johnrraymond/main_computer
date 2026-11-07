"""Patch 2E relay R7: bounded aggregate writers, isolation and recovery."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from main_computer.seh.stream_writer import SharedWriteBudget, StreamWriter
from tools.hub_resource_lab.relay_lab import run_relay_lab


def _wait(condition, seconds=2):
    limit=time.monotonic()+seconds
    while time.monotonic()<limit:
        if condition():return True
        time.sleep(.01)
    return bool(condition())


def test_aggregate_budget_is_atomic_under_competing_workers():
    shared=SharedWriteBudget(1024)
    def try_reserve(_):
        return shared.reserve(64)
    with ThreadPoolExecutor(max_workers=24) as pool:
        accepted=list(pool.map(try_reserve,range(128)))
    assert sum(accepted)==16
    assert shared.snapshot()=={'used_bytes':1024,'peak_bytes':1024,'limit_bytes':1024}
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(shared.release,[64]*16))
    assert shared.snapshot()['used_bytes']==0


def test_shared_budget_validates_inputs_and_prevents_underflow():
    with pytest.raises(ValueError):SharedWriteBudget(0)
    with pytest.raises(ValueError):SharedWriteBudget(-1)
    shared=SharedWriteBudget(12)
    with pytest.raises(ValueError):shared.reserve(-1)
    with pytest.raises(RuntimeError):shared.release(1)
    assert shared.reserve(12)
    assert not shared.reserve(1)
    shared.release(12)
    assert shared.snapshot()['used_bytes']==0


def test_blocked_writer_keeps_inflight_bytes_accounted_until_send_finishes():
    shared=SharedWriteBudget(20)
    blocking=threading.Event();release=threading.Event()
    def blocked(_):
        blocking.set();release.wait(3)
    writer=StreamWriter(blocked,lambda:None,shared_budget=shared,max_pending_bytes=80)
    try:
        assert writer.offer(b'a'*10)
        assert blocking.wait(1)
        assert writer.offer(b'b'*10)
        writer.close()
        assert shared.snapshot()['used_bytes']==10
        assert shared.snapshot()['peak_bytes']==20
    finally:
        release.set();writer.close()
    assert _wait(lambda:shared.snapshot()['used_bytes']==0)


def test_stalled_stream_closes_on_shared_budget_without_harming_neighbor():
    shared=SharedWriteBudget(20)
    blocking=threading.Event();release=threading.Event();done=threading.Event()
    failures=[]
    slow=StreamWriter(lambda data:(blocking.set(),release.wait(3)),
                      lambda:failures.append('slow'),shared_budget=shared,max_pending_bytes=60)
    fast=StreamWriter(lambda data:done.set(),lambda:failures.append('fast'),shared_budget=shared)
    try:
        assert slow.offer(b'x'*16) and blocking.wait(1)
        assert fast.offer(b'y'*4) and done.wait(1)
        assert not slow.offer(b'z'*8)
        assert slow.failure_reason=='write_aggregate_budget'
        assert failures==['slow']
        assert shared.snapshot()['peak_bytes']<=20
    finally:
        release.set();slow.close();fast.close()
    assert _wait(lambda:shared.snapshot()['used_bytes']==0)


def test_failed_sender_drains_queued_bytes_and_releases_budget():
    shared=SharedWriteBudget(32)
    entered=threading.Event();proceed=threading.Event();failed=threading.Event()
    def broken(_):
        entered.set();proceed.wait(3);raise BrokenPipeError('socket lost')
    writer=StreamWriter(broken,failed.set,shared_budget=shared)
    try:
        assert writer.offer(b'a'*12) and entered.wait(1)
        assert writer.offer(b'b'*12)
        assert shared.snapshot()['used_bytes']==24
        proceed.set()
        assert failed.wait(1)
        assert _wait(lambda:shared.snapshot()['used_bytes']==0)
        assert writer.failure_reason=='write_error'
    finally:
        proceed.set();writer.close()


def test_real_relay_boundary_campaign_passes_without_production():
    result=run_relay_lab()
    assert result['result']=='PASS', result
    assert result['passed']==result['total']
    assert result['total']>=24
    assert result['financial_settlement']=='NOT_TESTED'
