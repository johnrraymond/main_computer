"""Terminal payload expiry must preserve durable accounting and idempotency."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore, StableHubAcceptedWorkSessionDirectory,
)


def test_old_terminal_payloads_scrubbed_without_deleting_economic_facts():
    now=datetime.now(timezone.utc)
    old=(now-timedelta(hours=25)).isoformat()
    recent=(now-timedelta(hours=2)).isoformat()
    sample={'status':'succeeded','completed_at':old,'request_id':'r1',
            'requester_account_id':'acct1','worker_result':{'result':{'response':{'content':'SECRET'}}},
            'work':{'input':{'value':'PRIVATE PROMPT'}},'stream_events':[{'content':'SECRET'}],
            'continuation_token':'SUPERSECRET','requester_msk_id':'MSK','payout':{'status':'charged'},
            'worker_connection_id':'conn1','requester_wallet_address':'0x'+'1'*40}
    records={'expired':sample,'recent':{**sample,'completed_at':recent,'request_id':'r2'},
             'running':{**sample,'status':'running','request_id':'r3'},
             'malformed':{**sample,'completed_at':'bad','request_id':'r4'}}
    store=InMemoryStableWorkerSessionStore({'accepted_sessions':records,
                                            'payout_charges':{'c1':{'amount':'3'}},
                                            'work_request_index':{'i1':'expired'}})
    topo=load_stable_hub_topology(Path('deploy/hub-topology/dev-topology.json'))
    svc=StableHubAcceptedWorkSessionDirectory(topology=topo,hub_id='dev-hub1',store=store)
    assert svc.purge_expired_terminal_payloads(now=now) == 1
    result=store.load()
    old=result['accepted_sessions']['expired']
    assert 'worker_result' not in old and 'work' not in old and 'continuation_token' not in old
    assert old['request_id']=='r1' and old['requester_account_id']=='acct1'
    assert old['payout']=={'status':'charged'}
    assert result['work_request_index']=={'i1':'expired'}
    assert result['payout_charges']['c1']=={'amount':'3'}
    assert result['accepted_sessions']['recent']['work']['input']['value']=='PRIVATE PROMPT'
    assert result['accepted_sessions']['running']['work']['input']['value']=='PRIVATE PROMPT'
    assert result['accepted_sessions']['malformed']['work']['input']['value']=='PRIVATE PROMPT'
    assert svc.purge_expired_terminal_payloads(now=now) == 0
