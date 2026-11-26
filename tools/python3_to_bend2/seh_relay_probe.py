"""Appended to the independent bound probe inside the disposable Python image."""


def relay_receive(tunnel):
    raw=tunnel.recv(timeout=8)
    require(isinstance(raw,str),'expected tunnel text')
    data=json.loads(raw)
    require(raw==json.dumps(data,sort_keys=True,separators=(',',':')),'noncanonical tunnel frame')
    return data


def relay_send(tunnel,**fields):
    tunnel.send(json.dumps(fields,sort_keys=True,separators=(',',':')))


def relay_open(tunnel,port,stack,purpose='worker'):
    conn=stack.enter_context(ws(port,'/v1/seh/admin' if purpose=='admin' else '/v1/seh/client'))
    msg=relay_receive(tunnel)
    require(set(msg)=={'op','id','purpose'} and msg['op']=='open' and msg['purpose']==purpose,'bad stream open')
    sid=msg['id']
    require(type(sid) is str and len(sid)==24 and len(base64.urlsafe_b64decode(sid))==18,'bad stream id')
    return conn,sid


def relay_observe(port,key):
    from contextlib import ExitStack
    rows=observe(port,key) # all 22 authentication regressions on this candidate
    seen=set()
    with ws(port) as tunnel:
        tunnel.send(proof(key,challenge(tunnel,seen))); accepted(tunnel)
        eventually(port,True)
        with ExitStack() as stack:
            first,a=relay_open(tunnel,port,stack)
            second,b=relay_open(tunnel,port,stack)
            admin,c=relay_open(tunnel,port,stack,'admin')
            require(len({a,b,c})==3,'stream id collision')
            rows['relay_unique_streams']=True
            for sid in (a,b,c): relay_send(tunnel,op='authenticated',id=sid)
            rows['relay_worker_admin_open']=True
            # Test all byte values, embedded zeros and high bits, plus extended
            # WS frames. The independent oracle checks exact payload bytes.
            payloads=[bytes(range(256))*16,b'\x00second\xff',b'\x02admin\x00']
            for conn,payload in zip((first,second,admin),payloads): conn.send(payload)
            frames=[relay_receive(tunnel) for _ in range(3)]
            expected={sid:base64.b64encode(p).decode('ascii') for sid,p in zip((a,b,c),payloads)}
            require(all(set(x)=={'op','id','data'} and x['op']=='data' for x in frames),'bad upstream data')
            require({x['id']:x['data'] for x in frames}==expected,'upstream cross-talk or data loss')
            rows['relay_three_upstream']=True
            for sid,payload in reversed(list(zip((a,b,c),payloads))):
                relay_send(tunnel,op='data',id=sid,data=base64.b64encode(payload[::-1]).decode('ascii'))
            require(all(conn.recv(timeout=8)==payload[::-1] for conn,payload in zip((first,second,admin),payloads)),'downstream cross-talk or data loss')
            rows['relay_three_downstream']=True
            # Independent stream failure cannot destroy another connection.
            relay_send(tunnel,op='data',id=a,data='!!!!')
            rows['relay_invalid_data_close']=closed(first)
            require(rows['relay_invalid_data_close']==[1008,'INVALID_STREAM_FRAME'],'invalid frame not rejected')
            require(relay_receive(tunnel)=={'op':'close','id':a},'invalid stream cleanup')
            rows['relay_invalid_cleanup']=True
            relay_send(tunnel,op='data',id=b,data='AAH/')
            require(second.recv(timeout=8)==b'\x00\x01\xff','other stream interrupted')
            rows['relay_survivor']=True
            # Unknown ids must be ignored, never delivered to another stream.
            relay_send(tunnel,op='data',id='nonexistent',data='YQ==')
            relay_send(tunnel,op='data',id=c,data='Yg==')
            require(admin.recv(timeout=8)==b'b','unknown id misrouted')
            rows['relay_unknown_id_ignored']=True
            second.close()
            require(relay_receive(tunnel)=={'op':'close','id':b},'client close not forwarded')
            rows['relay_client_close']=True
            relay_send(tunnel,op='close',id=c,cause='write_queue_full')
            rows['relay_peer_close']=closed(admin)
            require(rows['relay_peer_close']==[1013,''],'wrong peer close')
            require(relay_receive(tunnel)=={'op':'close','id':c},'peer close cleanup')
            rows['relay_peer_cleanup']=True
            # FIFO across multiple records within one stream.
            fourth,d=relay_open(tunnel,port,stack)
            relay_send(tunnel,op='authenticated',id=d)
            for i in range(6): fourth.send(bytes([i])*257)
            for i in range(6):
                require(relay_receive(tunnel)=={'op':'data','id':d,'data':base64.b64encode(bytes([i])*257).decode('ascii')},'upstream FIFO')
            rows['relay_upstream_fifo']=True
            for i in range(6): relay_send(tunnel,op='data',id=d,data=base64.b64encode(bytes([i])*257).decode('ascii'))
            for i in range(6): require(fourth.recv(timeout=8)==bytes([i])*257,'downstream FIFO')
            rows['relay_downstream_fifo']=True
            rows['relay_ready_during_streams']=eventually(port,True)
            tunnel.close()
            rows['relay_tunnel_loss']=closed(fourth)
            require(rows['relay_tunnel_loss']==[1012,'SLH_DISCONNECTED'],'tunnel loss not propagated')
            rows['relay_not_ready_after_loss']=eventually(port,False)
    with ws(port) as tunnel:
        tunnel.send(proof(key,challenge(tunnel,seen))); accepted(tunnel)
        eventually(port,True)
        with ExitStack() as stack:
            client,sid=relay_open(tunnel,port,stack)
            require(sid not in {a,b,c,d},'stream id reused')
            relay_send(tunnel,op='data',id=d,data='YQ==')
            relay_send(tunnel,op='authenticated',id=sid)
            relay_send(tunnel,op='data',id=sid,data='Yg==')
            require(client.recv(timeout=8)==b'b','old session leaked into new session')
            rows['relay_reconnect_isolated']=True
            client.close(); require(relay_receive(tunnel)=={'op':'close','id':sid},'reconnect close')
    rows['relay_final_not_ready']=eventually(port,False)
    return rows


def run_relay(mode,port,private_pem):
    key=serialization.load_pem_private_key(private_pem.encode('ascii'),password=None)
    pem=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    encoded=base64.b64encode(pem).decode('ascii')
    if mode=='reference':
        with reference(port,encoded): return relay_observe(port,key)
    if mode=='candidate':
        eventually(port,False); return relay_observe(port,key)
    raise ValueError('unknown relay probe mode')
