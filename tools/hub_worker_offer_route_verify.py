"""P5.4h: read-only Worker offer routing and endpoint-retirement verification."""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path

from main_computer.hub_interface import resolve_hub_destination
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin


class _Probe(ViewportEnergyRoutesMixin):
    def __init__(self, network): self.network = network
    def _load_worker_settings(self): return {'selectedNetwork': self.network, 'workerAutoConnectNetwork': self.network}


def verify() -> dict:
    handler = inspect.getsource(ViewportEnergyRoutesMixin._handle_worker_offer_sync)
    offer_source = inspect.getsource(ViewportEnergyRoutesMixin._worker_offer_sync_pending)
    route_source = (Path(__file__).resolve().parents[1] / 'main_computer/viewport_route_dispatch.py').read_text(encoding='utf-8')
    if ('_worker_offer_registration_destination(body)' not in handler or
            '_post_worker_start_to_hub(' not in offer_source or
            '_post_worker_registration_to_hub' in offer_source or
            '/api/applications/worker/register-offer' in route_source):
        raise RuntimeError('Worker offer routing must use selected-network authenticated registration, never legacy REST')
    routes=[]
    for network in ('dev','test','testnet','mainnet'):
        destination=resolve_hub_destination({'selectedNetwork':network})
        assert _Probe(network)._worker_offer_registration_destination({'hub_url':destination.hub_url}) == destination
        foreign=next(url for url in ('http://127.0.0.1:8871','http://127.0.0.1:8780','https://testnet-hub.greatlibrary.io','https://mainnet-hub.greatlibrary.io') if url!=destination.hub_url)
        try:
            _Probe(network)._worker_offer_registration_destination({'hub_url':foreign})
        except ValueError:
            pass
        else:
            raise RuntimeError(f'Cross-network Hub allowed: {network}')
        routes.append({'selected_network':network,'hub_url':destination.hub_url,'mismatch_rejected':True})
    for network in ('none','slh'):
        try:
            _Probe(network)._worker_offer_registration_destination({'hub_url':'http://127.0.0.1:8871'})
        except ValueError:
            pass
        else:
            raise RuntimeError(f'Unsupported Worker offer network: {network}')
    return {'ok':True,'scope':'read-only synthetic routing; no Hub request, Worker launch or payment',
            'handler_validates_before_http':True,'unselected_and_slh_rejected':True,
            'profiles':routes,'obsolete_viewport_rest_implementation_removed':True,
            'worker_offer_authenticated_transport':True,'obsolete_viewport_offer_route_removed':True}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='Read-only Worker offer routing verification')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--viewport-url',default='http://127.0.0.1:8765')
    args=parser.parse_args(argv)
    try:
        report=verify()
        if args.live:
            from tools.hub_interface_verify import verify_live_viewport
            report['live']=verify_live_viewport(args.viewport_url)
            report['live']['note']='Read-only Worker settings GET; no offer registration attempted.'
    except Exception as exc:
        print(f'HUB_WORKER_OFFER_ROUTE_VERIFY_FAIL: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1
    print(json.dumps(report,sort_keys=True,indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
