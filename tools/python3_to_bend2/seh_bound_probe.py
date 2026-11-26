"""Black-box bound-primary acceptance, independent of the Bend implementation.
Only disposable test identities. No private key enters the candidate container.
"""
from __future__ import annotations
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client as http_client
import inspect
import json
import os
import socket
import subprocess
import sys
import time
from cryptography.hazmat.primitives import serialization
from main_computer.slh.authenticator import generate_identity, sign_object
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


def require(value, why):
    if not value:
        raise RuntimeError(why)


def http(port,path):
    conn=http_client.HTTPConnection('127.0.0.1',port,timeout=3)
    try:
        conn.request('GET',path)
        response=conn.getresponse()
        return [response.status,response.read().decode('utf-8')]
    finally:
        conn.close()


def eventually(port,ready,process=None):
    end=time.monotonic()+12
    while time.monotonic()<end:
        if process is not None and process.poll() is not None:
            raise RuntimeError('reference exited early')
        try:
            result=http(port,'/readyz')
            if result[0]==(200 if ready else 503):
                return result
        except (OSError,http_client.HTTPException):
            pass
        time.sleep(.05)
    raise RuntimeError('readiness deadline')


@contextmanager
def reference(port,encoded):
    env=dict(os.environ,SEH_SLH_PUBLIC_KEY_B64=encoded,PYTHONDONTWRITEBYTECODE='1')
    proc=subprocess.Popen([sys.executable,'-m','main_computer.seh.relay','--host','127.0.0.1','--port',str(port)],env=env,stdout=sys.stderr,stderr=sys.stderr)
    try:
        eventually(port,False,proc)
        yield
    finally:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=4)


def ws(port,path='/v1/seh/slh'):
    options={'proxy':None} if 'proxy' in inspect.signature(connect).parameters else {}
    return connect(f'ws://127.0.0.1:{port}{path}',open_timeout=5,close_timeout=2,**options)


def closed(conn):
    try:
        conn.recv(timeout=8)
    except ConnectionClosed as exc:
        require(exc.rcvd is not None,'missing explicit peer close')
        return [exc.rcvd.code,exc.rcvd.reason]
    raise RuntimeError('unexpected message instead of refusal')


def challenge(conn,seen):
    raw=conn.recv(timeout=5)
    msg=json.loads(raw)
    require(type(raw) is str and msg.get('type')=='seh.auth.challenge','bad challenge type')
    require(raw==json.dumps(msg,sort_keys=True,separators=(',',':')),'noncanonical challenge')
    statement=msg['statement']
    require(set(statement)=={'version','action','nonce'} and statement['version']==2 and statement['action']=='seh.slh.connect','bad statement')
    nonce=statement['nonce']
    require(type(nonce) is str and len(nonce)==43 and len(base64.urlsafe_b64decode(nonce+'='))==32 and nonce not in seen,'invalid or reused nonce')
    seen.add(nonce)
    return statement


def proof(key,statement):
    return json.dumps({'type':'seh.auth.proof','signature':sign_object(key,statement)},sort_keys=True,separators=(',',':'))


def accepted(conn):
    raw=conn.recv(timeout=5)
    require(raw=='{"type":"seh.auth.accepted"}','invalid authentication acceptance')
    return raw


def observe(port,key,*,candidate=False):
    rows={}; seen=set()
    foreign,_=generate_identity()
    rows['bound_health']=http(port,'/healthz')
    rows['bound_not_ready']=eventually(port,False)
    for route in ('/v1/seh/client','/v1/seh/admin','/unknown'):
        with ws(port,route) as conn:
            rows['unavailable:'+route]=closed(conn)
    with ws(port) as conn:
        statement=challenge(conn,seen)
        stale=proof(key,statement)
        conn.send(proof(foreign,statement))
        rows['wrong_signer']=closed(conn)
    with ws(port) as conn:
        challenge(conn,seen); conn.send(stale)
        rows['replay']=closed(conn)
    with ws(port) as conn:
        challenge(conn,seen); conn.send('{')
        rows['malformed_json']=closed(conn)
    with ws(port) as conn:
        statement=challenge(conn,seen)
        bad=json.loads(proof(key,statement)); bad['type']='wrong'
        conn.send(json.dumps(bad)); rows['wrong_type']=closed(conn)
    with ws(port) as conn:
        challenge(conn,seen); conn.send('{"type":"seh.auth.proof","signature":"!"}')
        rows['malformed_signature']=closed(conn)
    with ws(port) as conn:
        challenge(conn,seen); conn.send(b'not text')
        rows['binary_proof']=closed(conn)
    with ws(port) as conn:
        statement=challenge(conn,seen)
        # A second HTTP connection must work while authentication is pending.
        rows['health_during_pending_auth']=http(port,'/healthz')
        conn.send(proof(key,statement)); rows['valid_signer']=accepted(conn)
        rows['ready_after_auth']=eventually(port,True)
        with ws(port) as duplicate:
            duplicate.send(proof(key,challenge(duplicate,seen)))
            rows['duplicate_tunnel']=closed(duplicate)
        with ThreadPoolExecutor(max_workers=6) as pool:
            health=list(pool.map(lambda _: http(port,'/healthz'),range(6)))
        require(all(x==health[0] for x in health),'concurrent health mismatch')
        rows['six_concurrent_health']=health
        pong=conn.ping(b'bound-probe')
        require(pong.wait(4),'no pong on active tunnel'); rows['active_tunnel_ping']=True
        if candidate:
            with ws(port,'/v1/seh/client') as client:
                require(closed(client)==[1013,'RELAY_NOT_TRANSLATED'],'unsupported relay did not fail closed')
    rows['ready_after_disconnect']=eventually(port,False)
    with ws(port) as conn:
        conn.send(proof(key,challenge(conn,seen))); rows['reconnect']=accepted(conn)
        rows['ready_after_reconnect']=eventually(port,True)
    rows['final_not_ready']=eventually(port,False)
    require(len(seen)>=8,'missing unique challenges')
    rows['fresh_challenges']=True
    for k in ('wrong_signer','replay','malformed_json','wrong_type','malformed_signature','binary_proof'):
        require(rows[k]==[1008,'SLH_IDENTITY_REJECTED'],f'unexpected rejection {k}')
    require(rows['duplicate_tunnel']==[1008,'SLH_ALREADY_CONNECTED'],'duplicate admission')
    require(rows['bound_health'][0]==200 and rows['bound_not_ready'][0]==503,'wrong initial health')
    return rows


def run(mode,port,private_pem):
    key=serialization.load_pem_private_key(private_pem.encode('ascii'),password=None)
    pem=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    encoded=base64.b64encode(pem).decode('ascii')
    if mode=='reference':
        with reference(port,encoded):
            return observe(port,key)
    if mode=='candidate':
        eventually(port,False)
        return observe(port,key,candidate=True)
    raise ValueError('unknown probe mode')
