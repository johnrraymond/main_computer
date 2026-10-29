"""Run a Secure Local Hub managed-worker gateway, optionally with real Stable Hub MSK slots.

Local first proof:
  python -m tools.slh_managed_gateway serve --state-dir %LOCALAPPDATA%/MainComputer/slh
  python -m tools.slh_managed_gateway fingerprint --state-dir ...

Marketplace delegation (dev/test only by default):
  python -m tools.slh_managed_gateway serve --network dev --wallet-file ... --enable-marketplace

No wallet private key, MSK, credential, or prompt is included in CLI output.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from threading import RLock, Thread, Event
from types import SimpleNamespace

from main_computer.slh.authority import SLHAuthority
from main_computer.slh.managed_gateway import SLHManagedGateway,SLHMarketplaceProxy,DEFAULT_MANAGED_PORT
from main_computer.slh.config import MARKET_NETWORKS
from main_computer.hub_networks import load_hub_network_registry


def wallet_from_file(path:Path):
    from main_computer.multisession_key_signing import private_key_to_address
    raw=Path(path).read_text(encoding='utf-8').strip()
    try:
        value=json.loads(raw)
    except ValueError:
        value=raw
    private=value.get('private_key') if isinstance(value,dict) else value
    if not isinstance(private,str) or not private.startswith('0x') or len(private)!=66:
        raise ValueError('Wallet file must contain an existing 0x private_key (64 hex bytes)')
    address=private_key_to_address(private)
    return SimpleNamespace(address=address,private_key=private)


def authenticated_upstream(network:str, wallet_file:Path, *, allow_live:bool=False, registry_path:Path|None=None):
    if network not in MARKET_NETWORKS:raise ValueError('Unknown marketplace network')
    if network in ('mainnet','testnet') and not allow_live:
        raise ValueError('Live marketplace networks require --allow-live-marketplace')
    from main_computer.hub_cli_live_session import identify_hub_protocol,issue_cli_multisession_authorization
    registry=load_hub_network_registry(registry_path) if registry_path else load_hub_network_registry()
    profile=registry.networks[network]
    url=profile.hub_url
    identity=identify_hub_protocol(url)
    if identity.get('service')!='main_computer.stable_hub':
        raise RuntimeError('Upstream endpoint is not a Stable Hub')
    return url,issue_cli_multisession_authorization(url,wallet=wallet_from_file(wallet_file),stable_identity=identity)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['serve','fingerprint'])
    root=Path(os.environ.get('LOCALAPPDATA') or str(Path.home()/'.local/share'))/'MainComputer'/'slh'
    p.add_argument('--state-dir',type=Path,default=root)
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--port',type=int,default=DEFAULT_MANAGED_PORT)
    p.add_argument('--network',choices=MARKET_NETWORKS)
    p.add_argument('--wallet-file',type=Path)
    p.add_argument('--enable-marketplace',action='store_true')
    p.add_argument('--allow-live-marketplace',action='store_true')
    p.add_argument('--scheduler-interval',type=float,default=2.0,
                   help='Seconds between public slot reconciliations (>=0.5)')
    args=p.parse_args(argv)
    if args.scheduler_interval < 0.5:p.error('--scheduler-interval must be >=0.5')
    authority=SLHAuthority(args.state_dir)
    gateway=SLHManagedGateway(authority,host=args.host,port=args.port)
    if args.command=='fingerprint':
        print(json.dumps({'schema':'main-computer.slh-transport.v1','sha256':gateway.fingerprint,
                          'port':args.port,'note':'Verify fingerprint through the administrator before first connection'},indent=2))
        return 0
    if args.enable_marketplace:
        if not args.network or not args.wallet_file:
            p.error('--enable-marketplace requires --network and --wallet-file')
        # Obtain MSK on SLH only. No wallet material reaches a physical worker.
        url,msk=authenticated_upstream(args.network,args.wallet_file,allow_live=args.allow_live_marketplace)
        proxy=SLHMarketplaceProxy(gateway)
        lock=RLock()
        stop_reconcile=Event()
        def reconcile():
            with lock:
                report=proxy.reconcile(hub_url=url,auth=msk)
                proxy.gateway.scheduler.policy_store.write_runtime(report)
                return report
        def join(worker):
            try:reconcile()
            except Exception as exc:
                print(json.dumps({'event':'scheduler_reconcile_failed',
                                  'reason':type(exc).__name__}),flush=True)
        def leave(worker):
            # Fail closed: no upstream slot outlives its physical worker.
            with lock:proxy.disconnect_device(worker.device_id)
            join(worker)
        def reconcile_loop():
            while not stop_reconcile.wait(args.scheduler_interval):
                try:reconcile()
                except Exception as exc:
                    print(json.dumps({'event':'scheduler_reconcile_failed',
                                      'reason':type(exc).__name__}),flush=True)
        gateway.on_worker_registered=join
        gateway.on_worker_disconnected=leave
        reconcile_thread=Thread(target=reconcile_loop,daemon=True,name='slh-policy-reconcile')
        reconcile_thread.start()
    print(json.dumps({'event':'slh_managed_gateway_starting','host':args.host,'port':args.port,
                      'tls_sha256':gateway.fingerprint,'walletless_devices':True,
                      'marketplace':args.network if args.enable_marketplace else 'disabled'}),flush=True)
    try:gateway.serve()
    except KeyboardInterrupt:pass
    finally:
        if args.enable_marketplace:
            stop_reconcile.set()
            proxy.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
