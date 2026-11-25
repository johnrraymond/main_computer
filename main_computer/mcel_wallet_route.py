"""Fixed authenticated ORG wallet operations; no arbitrary Hub URL or bearer export."""
from __future__ import annotations
import ipaddress
import json
import re
from main_computer.mcel_slh_first_run import SlhFirstRunController, FirstRunError
from main_computer.mcel_slh_worker_bridge import OrganizationWorkerClient, WorkerBridgeError
from main_computer.mcel_slh_network_client import NetworkSlhClient, NetworkConnectionError
from main_computer.org_credit_transfer import canonical_transfer_intent

OPERATIONS = {'inspect': ('GET', '/v1/wallets'), 'reconcile': ('POST', '/v1/accounting/reconcile'),
              'transfer': ('POST', '/v1/wallets/transfer'),
              'portfolio': ('POST','/v1/wallets/portfolio'),
              'managed-transfer': ('POST','/v1/wallets/managed-transfer'),
              'transfer-status': ('POST','/v1/wallets/transfer-status'),
              'managed-transfer-status': ('POST','/v1/wallets/managed-transfer-status')}


def execute_wallet_operation(data, *, local_controller=None, local_client=None, network_client=None):
    if type(data) is not dict or set(data) != {'target','identity','address','port','pin','operation','arguments'}:
        raise ValueError('INVALID_WALLET_REQUEST')
    target, identity, address, port, pin, operation, args = (data[k] for k in
        ('target','identity','address','port','pin','operation','arguments'))
    if (target not in ('local','network') or not isinstance(identity,str) or
        not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',identity) or
        not isinstance(operation,str) or operation not in OPERATIONS or type(args) is not dict):
        raise ValueError('INVALID_WALLET_REQUEST')
    if operation in ('transfer','transfer-status'):
        if set(args) != {'intent','signature'}:
            raise ValueError('INVALID_WALLET_REQUEST')
        canonical_transfer_intent(args['intent'])
        if not isinstance(args['signature'],str) or not re.fullmatch(r'0x[0-9a-fA-F]{130}',args['signature']):
            raise ValueError('INVALID_WALLET_SIGNATURE')
    elif operation in ('managed-transfer','managed-transfer-status'):
        if set(args) != {'intent'}: raise ValueError('INVALID_WALLET_REQUEST')
        canonical_transfer_intent(args['intent'])
    elif operation == 'portfolio':
        if set(args) != {'limit','before'} or type(args['limit']) is not int or not 1<=args['limit']<=100 or not isinstance(args['before'],str) or len(args['before'])>200:
            raise ValueError('INVALID_WALLET_REQUEST')
    elif args:
        raise ValueError('INVALID_WALLET_REQUEST')
    method,path = OPERATIONS[operation]
    body = None if method == 'GET' else args
    if target == 'local':
        if address != '' or port != '' or pin != '':
            raise ValueError('INVALID_LOCAL_TARGET')
        controller = local_controller or SlhFirstRunController()
        client = local_client or OrganizationWorkerClient()
        session = controller.login(device_id=identity)
        token = session['access_token']
        try:
            if session.get('role') != 'admin':
                raise WorkerBridgeError('ADMIN_REQUIRED',403)
            return client.request(method,path,token,body)
        finally:
            try: client.logout(token)
            except WorkerBridgeError: pass
    if (not isinstance(address,str) or not isinstance(port,str) or not re.fullmatch(r'[0-9]{1,5}',port)
        or not isinstance(pin,str) or (pin and not re.fullmatch(r'[0-9a-fA-F]{64}',pin))):
        raise ValueError('INVALID_NETWORK_TARGET')
    client = network_client or NetworkSlhClient()
    session = client.connect(address,int(port),identity,confirm_pin=pin or None)
    if session.get('pairingRequired') is True:
        # Do not implicitly trust the observed peer or forward a transfer.
        return {'pairingRequired':True,'endpoint':session['endpoint'],'tlsSha256':session['tlsSha256']}
    ticket = session['sessionTicket']
    try:
        if session.get('role') != 'admin':
            raise NetworkConnectionError('ADMIN_REQUIRED',403)
        return client.admin_request(ticket,method,path,body)
    finally:
        try: client.disconnect(ticket)
        except NetworkConnectionError: pass


def handle_wallet_org(self):
    try:
        host = self.headers.get('Host','').lower()
        local = ipaddress.ip_address(self.client_address[0]).is_loopback
        port = int(self.server.server_address[1])
    except (ValueError,AttributeError,IndexError,TypeError):
        local=False
        host=''
        port=0
    if (not local or host not in (f'127.0.0.1:{port}',f'localhost:{port}') or
        self.headers.get('Origin') not in ('http://'+host,'https://'+host) or
        self.headers.get('Sec-Fetch-Site','same-origin') != 'same-origin' or
        self.headers.get('X-MCEL-Wallet-Operation') != 'org-wallet' or
        self.headers.get('Content-Type','').split(';')[0].strip() != 'application/json'):
        self._send_json({'error':{'code':'LOCAL_ORIGIN_REQUIRED'}},403)
        return
    try:
        length=int(self.headers.get('Content-Length','-1'))
        if not 2<=length<=4096:
            raise ValueError('INVALID_WALLET_REQUEST')
        data=json.loads(self.rfile.read(length))
        result=execute_wallet_operation(data)
    except (FirstRunError,WorkerBridgeError,NetworkConnectionError) as exc:
        self._send_json({'error':{'code':exc.code}},exc.status)
        return
    except (ValueError,TypeError,KeyError,UnicodeError):
        self._send_json({'error':{'code':'INVALID_WALLET_REQUEST'}},400)
        return
    self._send_json({'ok':True,'result':result})


def local_wallet_binding(state_dir=None):
    """Read only the public projection of the installation-owned ORG binding.

    Do not instantiate OrganizationWallet: its constructor creates/migrates DBs.
    The local MC authentication boundary already protects this route.
    """
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from main_computer.slh.storage import slh_state_dir
    from main_computer.slh.organization_wallet import DB_NAME
    from main_computer.hub_credit_indexer import wallet_account_id
    from main_computer.org_credit_transfer import ADDRESS
    path = (Path(state_dir) if state_dir is not None else slh_state_dir()) / DB_NAME
    if not path.is_file():
        return {'state':'NOT_CONFIGURED','binding':None}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT hub_url, cluster_id, hub_id, chain_id, wallet_address, account_id, revision FROM binding WHERE singleton=1').fetchone()
        if row is None:
            return {'state':'UNBOUND','binding':None}
        binding = dict(row)
        if (not ADDRESS.fullmatch(binding['wallet_address']) or
                wallet_account_id(binding['wallet_address']) != binding['account_id']):
            raise ValueError('INVALID_LOCAL_ORG_BINDING')
        return {'state':'BOUND','binding':binding}
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return {'state':'LOCAL_ORG_BINDING_UNAVAILABLE','binding':None}


def observe_local_wallet(wallet, before, state_dir=None, reader_factory=None):
    """Inspect only the exact saved binding, with existing Hub read authority.

    No local ORG login, no service startup, no DB writes, no credential export.
    A changed binding or failed Hub authorization never becomes a current balance.
    """
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from main_computer.slh.storage import slh_state_dir
    from main_computer.slh.organization_wallet import DB_NAME, HubCreditReader, _digest_key
    from main_computer.slh.marketplace_auto import read_settings, load_authorization
    from main_computer.slh.authority import SLHError
    root = Path(state_dir) if state_dir is not None else slh_state_dir()
    status = local_wallet_binding(root)
    binding = status['binding']
    if not binding or binding['wallet_address'] != wallet:
        return {'state':'LOCAL_ORG_BINDING_CHANGED','binding':binding}
    result = {'state':'HUB_READ_UNAVAILABLE_OR_NOT_AUTHORIZED','binding':binding}
    try:
        with closing(sqlite3.connect((root / DB_NAME).resolve().as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            private = db.execute('SELECT revision, msk_digest, tls_sha256, trust_mode FROM binding WHERE singleton=1').fetchone()
        auth = load_authorization(root)
        if (private is None or private['revision'] != binding['revision'] or not auth or
                read_settings(root)['hub_url'] != binding['hub_url'] or
                _digest_key(auth['multisession_key_id']) != private['msk_digest']):
            return result
        reader = (reader_factory or HubCreditReader)(binding['hub_url'], private['tls_sha256'])
        if reader.trust_mode != private['trust_mode']:
            return result
        view = reader.request('POST','/api/hub/v1/credits/wallet-portfolio',
                              {'wallet_address':wallet,'limit':50,'before':before},
                              bearer_token=auth['multisession_key_id'])
        if (view.get('ok') is not True or any(view.get(k)!=binding[k] for k in
                ('wallet_address','account_id','cluster_id','chain_id')) or
                local_wallet_binding(root) != status):
            return result
        return {'state':'CURRENT','binding':binding,'portfolio':view}
    except (SLHError, sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        return result


def execute_portfolio_operation(handler, data):
    from main_computer.hub_networks import load_hub_network_registry
    from main_computer.slh.organization_wallet import HubCreditReader
    from main_computer.slh.authority import SLHError
    from main_computer.wallet_portfolio import canonical_read
    from main_computer.org_credit_transfer import ADDRESS
    from main_computer.energy_chain import EnergyChainClient
    from main_computer.hub_credit_indexer import wallet_account_id
    import time
    registry=load_hub_network_registry()
    op=data.get('operation')
    if op=='inventory' and set(data)=={'operation'}:
        from main_computer.mcel_slh_network_client import _read_trust
        networks=[]
        for name,p in registry.networks.items():
            wallets=handler._worker_runtime_cached_multisession_wallets_for_hub(p.hub_url)
            networks.append({'network':name,'chain_id':str(p.chain_id or ''),'hub':p.hub_url,'currency':p.native_currency.get('symbol','MCXLAG'),'wallets':wallets})
        # Only public endpoint labels, never bearer credentials or private identity keys.
        targets=[]
        for endpoint in _read_trust(NetworkSlhClient().trust_path):
            host,sep,port=endpoint.rpartition(':')
            if sep and port.isdigit():
                targets.append({'address':host.strip('[]'),'port':port})
        return {'networks':networks,'orgTargets':targets,'localOrg':local_wallet_binding()}
    if op == 'local-observe' and set(data)=={'operation','wallet','before'}:
        if (not isinstance(data['wallet'],str) or not ADDRESS.fullmatch(data['wallet']) or
                not isinstance(data['before'],str) or len(data['before'])>200):
            raise ValueError('INVALID_WALLET_REQUEST')
        return observe_local_wallet(data['wallet'],data['before'])
    if op not in ('observe','send','transfer-status') or data.get('network') not in registry.networks:
        raise ValueError('INVALID_WALLET_REQUEST')
    p=registry.get(data['network'])
    wallet=data.get('wallet')
    if not isinstance(wallet,str) or not ADDRESS.fullmatch(wallet):
        raise ValueError('INVALID_WALLET_ADDRESS')
    reader=HubCreditReader(p.hub_url)
    # Health is deliberately read through the same origin, with no redirect.
    # The /health endpoint is outside the normal credit reader prefix.
    # A public fixed-path scope endpoint binds a wallet signature to this Hub.
    if op in ('send','transfer-status'):
        if set(data)!={'operation','network','wallet','intent','signature'}: raise ValueError('INVALID_WALLET_REQUEST')
        intent=data['intent'];canonical_transfer_intent(intent)
        if intent['wallet_address']!=wallet or intent['chain_id']!=str(p.chain_id): raise ValueError('WALLET_SCOPE_MISMATCH')
        scope=reader.request('GET','/api/hub/v1/credits/wallet-scope')
        if scope.get('cluster_id')!=intent['cluster_id'] or scope.get('chain_id')!=intent['chain_id']: raise ValueError('WALLET_SCOPE_MISMATCH')
        return reader.request('POST','/api/hub/v1/credits/'+('org-transfers' if op=='send' else 'org-transfer-status'),{'intent':intent,'signature':data['signature']})
    if set(data)!={'operation','network','wallet','proof','before'}: raise ValueError('INVALID_WALLET_REQUEST')
    if not isinstance(data['before'],str) or len(data['before'])>200: raise ValueError('INVALID_WALLET_REQUEST')
    result={'network':p.network_key,'wallet_address':wallet,'hub':p.hub_url,'chain_id':str(p.chain_id or ''),'cluster_id':'','creditState':'UNAVAILABLE','nativeState':'UNAVAILABLE','currency':p.native_currency.get('symbol','MCXLAG')}
    # Chain observation does not require an MSK or expose its value.
    if p.chain_rpc_url:
        try:
            rpc=EnergyChainClient(p.chain_rpc_url,p.chain_id,timeout_s=4)
            if int(rpc.rpc('eth_chainId'),16)!=p.chain_id: raise ValueError('RPC_CHAIN_MISMATCH')
            block=rpc.rpc('eth_getBlockByNumber',['latest',False])
            result.update(nativeWei=str(int(rpc.rpc('eth_getBalance',[wallet,block['number']]),16)),checkpoint={'number':block['number'],'hash':block['hash']},nativeState='CURRENT')
            # Inspect the configured native escrow at the same verified block. Its
            # collateral and cumulative completed deposits are not extra credits.
            result['escrowState']='NOT_CONFIGURED'
            contract=p.contracts.get('hub_credit_bridge_escrow')
            if contract:
                try:
                    from main_computer.multisession_key_signing import keccak256
                    code=rpc.rpc('eth_getCode',[contract,block['number']])
                    if not isinstance(code,str) or code in ('0x','0x0'): raise ValueError('NO_ESCROW_CODE')
                    def call(name):
                        calldata='0x'+keccak256((name+'(address)').encode())[:4].hex()+wallet[2:].rjust(64,'0')
                        value=rpc.rpc('eth_call',[{'to':contract,'data':calldata},block['number']])
                        if not isinstance(value,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',value): raise ValueError('INVALID_ESCROW_RESPONSE')
                        return str(int(value,16))
                    result['escrow']={name:call(method) for name,method in [('deposited','depositedUnits'),('completed','completedDepositUnits'),('rectified','rectifiedSpentUnits'),('withdrawn','withdrawnUnits'),('remaining','withdrawableUnits')]}
                    result['escrowState']='CURRENT'
                except Exception: result['escrowState']='ESCROW_UNAVAILABLE'

        except Exception:
            result['nativeState']='RPC_UNAVAILABLE_OR_WRONG_CHAIN'
    try:
        scope=reader.request('GET','/api/hub/v1/credits/wallet-scope')
        if scope.get('chain_id')!=str(p.chain_id) or not scope.get('cluster_id'): raise ValueError('WALLET_SCOPE_MISMATCH')
        result['cluster_id']=scope['cluster_id']
        payload={'limit':50,'before':data['before']}
        proof=data['proof']
        if proof is not None:
            if type(proof) is not dict or set(proof)!={'intent','signature'}: raise ValueError('INVALID_READ_PROOF')
            canonical_read(proof['intent'])
            if any(proof['intent'][k]!=result[k] for k in ('wallet_address','cluster_id','chain_id')): raise ValueError('WALLET_READ_SCOPE_MISMATCH')
            view=reader.request('POST','/api/hub/v1/credits/wallet-portfolio',{**payload,**proof})
        else:
            try:
                records=handler._worker_multisession_keys_for_wallet(wallet_address=wallet,hub_url=p.hub_url)
                key=next(r['id'] for r in records if r.get('status')=='active' and r.get('hub_url','').rstrip('/')==p.hub_url.rstrip('/'))
            except (ValueError,StopIteration,KeyError):
                result['creditState']='READ_SIGNATURE_REQUIRED';return result
            view=reader.request('POST','/api/hub/v1/credits/wallet-portfolio',{**payload,'wallet_address':wallet},bearer_token=key)
        if view.get('ok') is not True or view.get('account_id')!=wallet_account_id(wallet) or any(view.get(k)!=result[k] for k in ('wallet_address','cluster_id','chain_id')): raise ValueError('WALLET_READ_SCOPE_MISMATCH')
        result.update(creditState='CURRENT',portfolio=view)
    except SLHError as exc:
        result['creditState']='HUB_READ_UNAVAILABLE_OR_NOT_AUTHORIZED'
    except (ValueError,KeyError,TypeError):
        result['creditState']='HUB_SCOPE_UNVERIFIED'
    return result


def handle_wallet_portfolio(self):
    from main_computer.slh.authority import SLHError
    try:
        host=self.headers.get('Host','').lower();port=int(self.server.server_address[1])
        local=ipaddress.ip_address(self.client_address[0]).is_loopback
    except (ValueError,AttributeError,IndexError,TypeError): local=False;host='';port=0
    if (not local or host not in (f'127.0.0.1:{port}',f'localhost:{port}') or self.headers.get('Origin') not in ('http://'+host,'https://'+host) or self.headers.get('Sec-Fetch-Site','same-origin')!='same-origin' or self.headers.get('X-MCEL-Wallet-Operation')!='portfolio' or self.headers.get('Content-Type','').split(';')[0].strip()!='application/json'):
        self._send_json({'error':{'code':'LOCAL_ORIGIN_REQUIRED'}},403);return
    try:
        n=int(self.headers.get('Content-Length','-1'))
        if not 2<=n<=8192: raise ValueError('INVALID_WALLET_REQUEST')
        data=json.loads(self.rfile.read(n))
        if type(data) is not dict: raise ValueError('INVALID_WALLET_REQUEST')
        result=execute_portfolio_operation(self,data)
    except (SLHError,NetworkConnectionError) as exc:
        self._send_json({'error':{'code':exc.code}},exc.status);return
    except (ValueError,TypeError,KeyError,UnicodeError):
        self._send_json({'error':{'code':'INVALID_WALLET_REQUEST'}},400);return
    self._send_json({'ok':True,'result':result})
