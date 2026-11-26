# Primary authenticated tunnel laboratory. Native Bend owns listener,
# connections, JSON policy, routing and shared admission state. The isolated
# crypto executable supplies only P-256 validation/verification and randomness.
# Client relay and lab channels remain explicitly disabled.
type LabPacket is Data:
  LabPacket{opcode: U32, bytes: List<&2, U32>}

def LabBound.text(pair: PyHeap & PyValue) -> String:
  (h, v) = pair
  LabBound.text_value(v)

def LabBound.text_value(v: PyValue) -> String:
  match v:
    case VStr{s}:
      s
    case _:
      ""

def LabBound.health(ready: Bool) -> String:
  LabBound.text(py3_health(PyHeap.empty(), VBool{ready}))

def LabBound.challenge(nonce: String) -> String:
  LabBound.text(py3_challenge(PyHeap.empty(), VStr{nonce}))

def LabBound.statement(nonce: String) -> String:
  LabBound.text(py3_statement(PyHeap.empty(), VStr{nonce}))

def LabBound.accepted() -> String:
  LabBound.text(py3_accepted(PyHeap.empty()))

def LabBound.proof(raw: String) -> Maybe<&2, String>:
  LabBound.proof_pair(PyJSON.decode(PyHeap.empty(), VStr{raw}))

def LabBound.proof_pair(pair: PyHeap & PyValue) -> Maybe<&2, String>:
  (h, v) = pair
  LabBound.proof_value(h,v)

def LabBound.proof_value(+h: PyHeap,+v: PyValue) -> Maybe<&2,String>:
  LabBound.proof_fields(PyHeap.dict_get(h, v, VStr{"type"}, VNone{}), PyHeap.dict_get(h, v, VStr{"signature"}, VNone{}))

def LabBound.proof_fields(kind: PyValue, sig: PyValue) -> Maybe<&2, String>:
  match kind sig:
    case VStr{k} VStr{s}:
      LabBound.proof_type(String.eq(k, "seh.auth.proof"), s)
    case _ _:
      None{}

def LabBound.proof_type(ok: Bool, sig: String) -> Maybe<&2, String>:
  match ok:
    case True{}:
      Some{sig}
    case False{}:
      None{}

# Process execution uses fixed absolute executable and explicit argv; no shell.
def LabCrypto.call(args: List<&1, String>, input: String) -> IO(Maybe<&2, String>):
  do IO<Maybe<&2, String>>:
    r : Result<&1, &1, U32 & String, U32 & (String & String)> <- Process.run("/usr/local/bin/py3-native-crypto", args, input, 4096, 2000)
    return LabCrypto.result(r)

def LabCrypto.result(r: Result<&1, &1, U32 & String, U32 & (String & String)>) -> Maybe<&2, String>:
  match r:
    case Fail{error}:
      None{}
    case Done{pair}:
      LabCrypto.exit_pair(pair)

def LabCrypto.exit_pair(pair: U32 & (String & String)) -> Maybe<&2, String>:
  (code, output) = pair
  (out, err) = output
  LabCrypto.exit_ok(U32.is_zero(code), out)

def LabCrypto.exit_ok(ok: Bool, out: String) -> Maybe<&2, String>:
  match ok:
    case True{}:
      Some{out}
    case False{}:
      None{}

# Exact receive: never consume bytes from a subsequent WebSocket frame.
# Per-frame absolute deadline, including fragmented TCP reads.
@unsafe def LabWire.read(s: Socket, n: Nat, deadline: Nat, acc: List<&2, U32>) -> IO(Socket & Maybe<&2, List<&2, U32>>):
  match n:
    case 0n:
      IO.pure(Socket & Maybe<&2, List<&2, U32>>, (s, Some{acc}))
    case 1n+rest:
      do IO<Socket & Maybe<&2, List<&2, U32>>>:
        now : Nat <- IO.now()
        LabWire.read_clock(now,deadline,1n+rest,s,acc)

@unsafe def LabWire.read_clock(+now: Nat,+deadline: Nat,n: Nat,s: Socket,acc: List<&2,U32>) -> IO(Socket & Maybe<&2,List<&2,U32>>):
  LabWire.read_time(Nat.is_lt(now,deadline),now,deadline,n,s,acc)

@unsafe def LabWire.read_time(ok: Bool, now: Nat, +deadline: Nat, +n: Nat, s: Socket, acc: List<&2, U32>) -> IO(Socket & Maybe<&2, List<&2, U32>>):
  match ok:
    case False{}:
      IO.pure(Socket & Maybe<&2, List<&2, U32>>, (s, None{}))
    case True{}:
      do IO<Socket & Maybe<&2, List<&2, U32>>>:
        got : Socket & Poll<&1, &1, Result<&1, &1, U32 & String, Maybe<&1, List<&2, U32>>>, Unit> <- TCP.try_recv_bytes(s, U32.from_nat(n), U32.from_nat(Nat.sub(deadline, now)))
        LabWire.read_pair(got, n, deadline, acc)

@unsafe def LabWire.read_pair(got: Socket & Poll<&1, &1, Result<&1, &1, U32 & String, Maybe<&1, List<&2, U32>>>, Unit>, n: Nat, deadline: Nat, acc: List<&2, U32>) -> IO(Socket & Maybe<&2, List<&2, U32>>):
  (s, poll) = got
  LabWire.read_poll(poll, s, n, deadline, acc)

@unsafe def LabWire.read_poll(poll: Poll<&1, &1, Result<&1, &1, U32 & String, Maybe<&1, List<&2, U32>>>, Unit>, s: Socket, n: Nat, deadline: Nat, acc: List<&2, U32>) -> IO(Socket & Maybe<&2, List<&2, U32>>):
  match poll:
    case Ready{Done{Some{+chunk}}}:
      LabWire.read(s, Nat.sub(n, List.length(&2, U32, chunk)), deadline, List.append(&2, U32, acc, chunk))
    case _:
      IO.pure(Socket & Maybe<&2, List<&2, U32>>, (s, None{}))

def LabWire.frame(s: Socket, timeout: Nat) -> IO(Socket & Maybe<&2, LabPacket>):
  do IO<Socket & Maybe<&2, LabPacket>>:
    now : Nat <- IO.now()
    LabWire.frame_at(s,Nat.add(now,timeout))

def LabWire.frame_at(s: Socket,+deadline: Nat) -> IO(Socket & Maybe<&2,LabPacket>):
  do IO<Socket & Maybe<&2,LabPacket>>:
    pair : Socket & Maybe<&2,List<&2,U32>> <- LabWire.read(s,2n,deadline,[])
    LabWire.header_pair(pair,deadline)

def LabWire.header_pair(pair: Socket & Maybe<&2, List<&2, U32>>, deadline: Nat) -> IO(Socket & Maybe<&2, LabPacket>):
  (s, header) = pair
  LabWire.header(header, s, deadline)

def LabWire.header(header: Maybe<&2, List<&2, U32>>, s: Socket, deadline: Nat) -> IO(Socket & Maybe<&2, LabPacket>):
  match header:
    case Some{Con{+op, Con{+size, Nil{}}}}:
      # Require FIN, no RSV bits, masked client frame. Only single frames in
      # this lab stage. Close/ping/pong and ASCII proof text are supported.
      valid = Bool.and(U32.is_ge(size,128), Bool.or(Bool.or(U32.is_eq(op,129),U32.is_eq(op,130)), Bool.or(U32.is_eq(op,136), Bool.or(U32.is_eq(op,137),U32.is_eq(op,138)))))
      LabWire.header_valid(valid, s, deadline, op, U32.sub(size,128))
    case _:
      IO.pure(Socket & Maybe<&2, LabPacket>, (s, None{}))

def LabWire.header_valid(valid: Bool, s: Socket, deadline: Nat, op: U32, +size: U32) -> IO(Socket & Maybe<&2, LabPacket>):
  match valid:
    case False{}:
      IO.pure(Socket & Maybe<&2, LabPacket>, (s, None{}))
    case True{}:
      LabWire.length_kind(U32.is_lt(size,126), s, deadline, op, size)

def LabWire.length_kind(short: Bool, s: Socket, deadline: Nat, op: U32, +size: U32) -> IO(Socket & Maybe<&2, LabPacket>):
  match short:
    case True{}:
      LabWire.payload_start(s, deadline, op, U32.to_nat(size))
    case False{}:
      LabWire.extended(U32.is_eq(size,126), s, deadline, op)

def LabWire.extended(ok: Bool, s: Socket, +deadline: Nat, op: U32) -> IO(Socket & Maybe<&2, LabPacket>):
  match ok:
    case False{}:
      IO.pure(Socket & Maybe<&2, LabPacket>, (s, None{}))
    case True{}:
      do IO<Socket & Maybe<&2, LabPacket>>:
        pair : Socket & Maybe<&2, List<&2, U32>> <- LabWire.read(s,2n,deadline,[])
        LabWire.extended_pair(pair, deadline, op)

def LabWire.extended_pair(pair: Socket & Maybe<&2, List<&2, U32>>, deadline: Nat, op: U32) -> IO(Socket & Maybe<&2, LabPacket>):
  (s, size) = pair
  LabWire.extended_size(size,s,deadline,op)

def LabWire.extended_size(size: Maybe<&2, List<&2, U32>>, s: Socket, deadline: Nat, +op: U32) -> IO(Socket & Maybe<&2, LabPacket>):
  match size:
    case Some{Con{hi, Con{lo, Nil{}}}}:
      +n = U32.add(U32.mul(hi,256),lo)
      LabWire.extended_valid(Bool.and(Bool.or(U32.is_eq(op,129),U32.is_eq(op,130)),Bool.and(U32.is_ge(n,126),U32.is_lt(n,8192))),s,deadline,op,U32.to_nat(n))
    case _:
      IO.pure(Socket & Maybe<&2, LabPacket>, (s,None{}))

def LabWire.extended_valid(ok: Bool, s: Socket, deadline: Nat, op: U32, n: Nat) -> IO(Socket & Maybe<&2, LabPacket>):
  match ok:
    case True{}:
      LabWire.payload_start(s,deadline,op,n)
    case False{}:
      IO.pure(Socket & Maybe<&2, LabPacket>,(s,None{}))

def LabWire.payload_start(s: Socket, deadline: Nat, op: U32, n: Nat) -> IO(Socket & Maybe<&2, LabPacket>):
  do IO<Socket & Maybe<&2, LabPacket>>:
    pair : Socket & Maybe<&2, List<&2, U32>> <- LabWire.read(s,Nat.add(n,4n),deadline,[])
    LabWire.payload_pair(pair,op)

def LabWire.payload_pair(pair: Socket & Maybe<&2, List<&2, U32>>, op: U32) -> IO(Socket & Maybe<&2, LabPacket>):
  (s, bytes) = pair
  LabWire.payload(bytes,s,op)

def LabWire.payload(bytes: Maybe<&2, List<&2, U32>>, s: Socket, op: U32) -> IO(Socket & Maybe<&2, LabPacket>):
  match bytes:
    case Some{Con{a,Con{b,Con{c,Con{d,rest}}}}}:
      IO.pure(Socket & Maybe<&2, LabPacket>,(s,Some{LabPacket{op,LabWire.unmask(rest,a,b,c,d)}}))
    case _:
      IO.pure(Socket & Maybe<&2, LabPacket>,(s,None{}))

def LabWire.unmask(bytes: List<&2,U32>, +a: U32,b: U32,c: U32,d: U32) -> List<&2,U32>:
  match bytes:
    case Nil{}:
      []
    case Con{x,rest}:
      U32.xor(x,a) <> LabWire.unmask(rest,b,c,d,a)

def LabWire.ascii(bytes: List<&2,U32>) -> Bool:
  match bytes:
    case Nil{}:
      True{}
    case Con{x,rest}:
      Bool.and(U32.is_lt(x,128),LabWire.ascii(rest))

def LabWire.string(bytes: List<&2,U32>) -> String:
  match bytes:
    case Nil{}:
      ""
    case Con{x,rest}:
      SCon{Chr{x},LabWire.string(rest)}

def LabWire.encode(+op: U32,+bytes: List<&2,U32>) -> List<&2,U32>:
  +n = U32.from_nat(List.length(&2,U32,bytes))
  LabWire.encode_length(U32.is_lt(n,126),op,n,bytes)

def LabWire.encode_length(short: Bool,op: U32,+n: U32,bytes: List<&2,U32>) -> List<&2,U32>:
  match short:
    case True{}:
      op <> n <> bytes
    case False{}:
      op <> 126 <> U32.div(n,256) <> U32.mod(n,256) <> bytes

def LabWire.send(s: Socket,bytes: List<&2,U32>) -> IO(Socket & Bool):
  do IO<Socket & Bool>:
    pair : Socket & Result<&1,&1,(U32 & String) & List<&2,U32>,Unit> <- TCP.send_bytes(s,bytes)
    return LabWire.sent(pair)

def LabWire.sent(pair: Socket & Result<&1,&1,(U32 & String) & List<&2,U32>,Unit>) -> Socket & Bool:
  (s,r) = pair
  (s,LabWire.sent_ok(r))

def LabWire.sent_ok(r: Result<&1,&1,(U32 & String) & List<&2,U32>,Unit>) -> Bool:
  match r:
    case Done{u}:
      True{}
    case Fail{e}:
      False{}

def LabWire.close(s: Socket,+code: U32,reason: String) -> IO(Unit):
  do IO<Unit>:
    pair : Socket & Bool <- LabWire.send(s,LabWire.encode(136,U32.div(code,256) <> U32.mod(code,256) <> LabNet.bytes(reason)))
    LabWire.close_pair(pair)

def LabWire.close_pair(pair: Socket & Bool) -> IO(Unit):
  (s,ok) = pair
  Socket.close(s)

# A one-place channel acts as a linear state cell. Read/update operations
# hold its token for two effects only; never hold it across network/crypto IO.
def LabState.ready(+state: Chan(Bool)) -> IO(Bool):
  do IO<Bool>:
    got : Maybe<&1,Bool> <- Chan.recv(Bool,state)
    LabState.ready_value(got,state)

def LabState.ready_value(got: Maybe<&1,Bool>,state: Chan(Bool)) -> IO(Bool):
  match got:
    case Some{+value}:
      do IO<Bool>:
        sent : Result<&1,&1,Bool,Unit> <- Chan.send(Bool,state,value)
        return value
    case None{}:
      IO.pure(Bool,False{})

def LabState.claim(+state: Chan(Bool)) -> IO(Bool):
  do IO<Bool>:
    got : Maybe<&1,Bool> <- Chan.recv(Bool,state)
    LabState.claim_value(got,state)

def LabState.claim_value(got: Maybe<&1,Bool>,state: Chan(Bool)) -> IO(Bool):
  match got:
    case Some{value}:
      do IO<Bool>:
        sent : Result<&1,&1,Bool,Unit> <- Chan.send(Bool,state,True{})
        return Bool.not(value)
    case None{}:
      IO.pure(Bool,False{})

def LabState.release(+state: Chan(Bool)) -> IO(Unit):
  do IO<Unit>:
    got : Maybe<&1,Bool> <- Chan.recv(Bool,state)
    sent : Result<&1,&1,Bool,Unit> <- Chan.send(Bool,state,False{})
    return Unit{}

# Authenticated tunnel lifetime: process ping/pong and close; no application
# traffic is accepted until the full relay state machine has been lowered.
@unsafe def LabBound.tunnel(s: Socket) -> IO(Unit):
  do IO<Unit>:
    pair : Socket & Maybe<&2,LabPacket> <- LabWire.frame(s,45000n)
    LabBound.tunnel_pair(pair)

@unsafe def LabBound.tunnel_pair(pair: Socket & Maybe<&2,LabPacket>) -> IO(Unit):
  (s,packet) = pair
  LabBound.tunnel_packet(packet,s)

@unsafe def LabBound.tunnel_packet(packet: Maybe<&2,LabPacket>,s: Socket) -> IO(Unit):
  match packet:
    case Some{LabPacket{137,bytes}}:
      do IO<Unit>:
        sent : Socket & Bool <- LabWire.send(s,LabWire.encode(138,bytes))
        LabBound.tunnel_sent(sent)
    case Some{LabPacket{138,bytes}}:
      LabBound.tunnel(s)
    case Some{LabPacket{136,bytes}}:
      do IO<Unit>:
        sent : Socket & Bool <- LabWire.send(s,LabWire.encode(136,bytes))
        LabWire.close_pair(sent)
    case Some{LabPacket{op,bytes}}:
      LabWire.close(s,1011,"RELAY_NOT_TRANSLATED")
    case None{}:
      Socket.close(s)

@unsafe def LabBound.tunnel_sent(pair: Socket & Bool) -> IO(Unit):
  (s,ok) = pair
  LabBound.tunnel_continue(ok,s)

@unsafe def LabBound.tunnel_continue(ok: Bool,s: Socket) -> IO(Unit):
  match ok:
    case True{}:
      LabBound.tunnel(s)
    case False{}:
      Socket.close(s)

def LabBound.admit(s: Socket,+state: Chan(Bool)) -> IO(Unit):
  do IO<Unit>:
    acquired : Bool <- LabState.claim(state)
    LabBound.admitted(acquired,s,state)

def LabBound.admitted(acquired: Bool,s: Socket,state: Chan(Bool)) -> IO(Unit):
  match acquired:
    case False{}:
      LabWire.close(s,1008,"SLH_ALREADY_CONNECTED")
    case True{}:
      do IO<Unit>:
        sent : Socket & Bool <- LabWire.send(s,LabWire.encode(129,LabNet.bytes(LabBound.accepted())))
        LabBound.tunnel_sent(sent)
        LabState.release(state)

def LabBound.authenticate(s: Socket,key: String,state: Chan(Bool)) -> IO(Unit):
  do IO<Unit>:
    nonce : Maybe<&2,String> <- LabCrypto.call(["nonce",LAB_NONCE_SIZE],"")
    LabBound.challenge_nonce(nonce,s,key,state)

def LabBound.challenge_nonce(nonce: Maybe<&2,String>,s: Socket,key: String,state: Chan(Bool)) -> IO(Unit):
  match nonce:
    case None{}:
      LabWire.close(s,1011,"CRYPTO_RUNTIME_FAILED")
    case Some{+value}:
      do IO<Unit>:
        sent : Socket & Bool <- LabWire.send(s,LabWire.encode(129,LabNet.bytes(LabBound.challenge(value))))
        LabBound.challenge_sent(sent,key,LabBound.statement(value),state)

def LabBound.challenge_sent(pair: Socket & Bool,key: String,statement: String,state: Chan(Bool)) -> IO(Unit):
  (s,ok) = pair
  LabBound.challenge_ok(ok,s,key,statement,state)

def LabBound.challenge_ok(ok: Bool,s: Socket,key: String,statement: String,state: Chan(Bool)) -> IO(Unit):
  match ok:
    case False{}:
      Socket.close(s)
    case True{}:
      do IO<Unit>:
        pair : Socket & Maybe<&2,LabPacket> <- LabWire.frame(s,6000n)
        LabBound.proof_packet_pair(pair,key,statement,state)

def LabBound.proof_packet_pair(pair: Socket & Maybe<&2,LabPacket>,key: String,statement: String,state: Chan(Bool)) -> IO(Unit):
  (s,packet) = pair
  LabBound.proof_packet(packet,s,key,statement,state)

def LabBound.proof_packet(packet: Maybe<&2,LabPacket>,s: Socket,key: String,statement: String,state: Chan(Bool)) -> IO(Unit):
  match packet:
    case Some{LabPacket{129,+bytes}}:
      LabBound.proof_ascii(LabWire.ascii(bytes),bytes,s,key,statement,state)
    case _:
      LabWire.close(s,1008,"SLH_IDENTITY_REJECTED")

def LabBound.proof_ascii(ok: Bool,bytes: List<&2,U32>,s: Socket,key: String,statement: String,state: Chan(Bool)) -> IO(Unit):
  match ok:
    case False{}:
      LabWire.close(s,1008,"SLH_IDENTITY_REJECTED")
    case True{}:
      LabBound.proof_signature(LabBound.proof(LabWire.string(bytes)),s,key,statement,state)

def LabBound.proof_signature(sig: Maybe<&2,String>,s: Socket,key: String,statement: String,state: Chan(Bool)) -> IO(Unit):
  match sig:
    case None{}:
      LabWire.close(s,1008,"SLH_IDENTITY_REJECTED")
    case Some{signature}:
      do IO<Unit>:
        verified : Maybe<&2,String> <- LabCrypto.call(["verify",key,signature],statement)
        LabBound.verified(verified,s,state)

def LabBound.verified(verified: Maybe<&2,String>,s: Socket,state: Chan(Bool)) -> IO(Unit):
  match verified:
    case Some{output}:
      LabBound.admit(s,state)
    case None{}:
      LabWire.close(s,1008,"SLH_IDENTITY_REJECTED")

# HTTP upgrade and routing. Unlike the unbound listener, a socket is retained
# after the handshake. Unsupported client/admin/lab paths cannot relay traffic.
def LabBound.ws_route(s: Socket,+path: String,key: String,state: Chan(Bool)) -> IO(Unit):
  LabBound.ws_primary(String.eq(path,"/v1/seh/slh"),s,path,key,state)

def LabBound.ws_primary(primary: Bool,s: Socket,+path: String,key: String,state: Chan(Bool)) -> IO(Unit):
  match primary:
    case True{}:
      LabBound.authenticate(s,key,state)
    case False{}:
      LabBound.ws_client(Bool.or(String.eq(path,"/v1/seh/client"),String.eq(path,"/v1/seh/admin")),s,state)

def LabBound.ws_client(client: Bool,s: Socket,state: Chan(Bool)) -> IO(Unit):
  match client:
    case False{}:
      LabWire.close(s,1008,"UNKNOWN_ROUTE")
    case True{}:
      do IO<Unit>:
        ready : Bool <- LabState.ready(state)
        LabBound.client_ready(ready,s)

def LabBound.client_ready(ready: Bool,s: Socket) -> IO(Unit):
  match ready:
    case False{}:
      LabWire.close(s,1013,"SLH_UNAVAILABLE_OR_BUSY")
    case True{}:
      LabWire.close(s,1013,"RELAY_NOT_TRANSLATED")

def LabBound.ws_handshake(s: Socket,wskey: String,path: String,key: String,state: Chan(Bool)) -> IO(Unit):
  head = "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " ++ LabNet.ws_accept(wskey) ++ "\r\n\r\n"
  do IO<Unit>:
    sent : Socket & Bool <- LabWire.send(s,LabNet.bytes(head))
    LabBound.ws_handshake_pair(sent,path,key,state)

def LabBound.ws_handshake_pair(pair: Socket & Bool,path: String,key: String,state: Chan(Bool)) -> IO(Unit):
  (s,ok) = pair
  LabBound.ws_handshake_ok(ok,s,path,key,state)

def LabBound.ws_handshake_ok(ok: Bool,s: Socket,path: String,key: String,state: Chan(Bool)) -> IO(Unit):
  match ok:
    case True{}:
      LabBound.ws_route(s,path,key,state)
    case False{}:
      Socket.close(s)

def LabBound.ws_key(s: Socket,wskey: String,decoded: Result<&1,&1,Pair(U32,String),List<&2,U32>>,path: String,key: String,state: Chan(Bool)) -> IO(Unit):
  match decoded:
    case Done{bytes}:
      LabBound.ws_key_valid(Nat.is_eq(List.length(&2,U32,bytes),16n),s,wskey,path,key,state)
    case Fail{error}:
      LabNet.write(s,LabNet.http("400 Bad Request",""))

def LabBound.ws_key_valid(ok: Bool,s: Socket,wskey: String,path: String,key: String,state: Chan(Bool)) -> IO(Unit):
  match ok:
    case True{}:
      LabBound.ws_handshake(s,wskey,path,key,state)
    case False{}:
      LabNet.write(s,LabNet.http("400 Bad Request",""))

def LabBound.upgrade(s: Socket,+headers: List<&2,String>,path: String,key: String,state: Chan(Bool)) -> IO(Unit):
  upgrade = String.eq(String.to_lower(LabNet.header("upgrade:",headers)),"websocket")
  connection = LabNet.has_upgrade(String.split(LabNet.header("connection:",headers),','))
  version = String.eq(LabNet.header("sec-websocket-version:",headers),"13")
  LabBound.upgrade_valid(Bool.and(upgrade,Bool.and(connection,version)),s,headers,path,key,state)

def LabBound.upgrade_valid(ok: Bool,s: Socket,headers: List<&2,String>,path: String,key: String,state: Chan(Bool)) -> IO(Unit):
  match ok:
    case True{}:
      +wskey = LabNet.header("sec-websocket-key:",headers)
      LabBound.ws_key(s,wskey,LabB64.decode(wskey),path,key,state)
    case False{}:
      LabNet.write(s,LabNet.http("426 Upgrade Required",""))

def LabBound.route(s: Socket,+path: String,headers: List<&2,String>,key: String,state: Chan(Bool)) -> IO(Unit):
  +health = String.eq(path,"/healthz")
  LabBound.route_http(Bool.or(health,String.eq(path,"/readyz")),health,s,path,headers,key,state)

def LabBound.route_http(http: Bool,health: Bool,s: Socket,path: String,headers: List<&2,String>,key: String,state: Chan(Bool)) -> IO(Unit):
  match http:
    case True{}:
      do IO<Unit>:
        ready : Bool <- LabState.ready(state)
        LabBound.http_ready(health,ready,s)
    case False{}:
      LabBound.upgrade(s,headers,path,key,state)

def LabBound.http_ready(health: Bool,+ready: Bool,s: Socket) -> IO(Unit):
  LabBound.http_status(Bool.or(health,ready),ready,s)

def LabBound.http_status(ok: Bool,ready: Bool,s: Socket) -> IO(Unit):
  match ok:
    case True{}:
      LabNet.write(s,LabNet.http("200 OK",LabBound.health(ready)))
    case False{}:
      LabNet.write(s,LabNet.http("503 Service Unavailable",LabBound.health(ready)))

def LabBound.parse(s: Socket,lines: List<&2,String>,key: String,state: Chan(Bool)) -> IO(Unit):
  match lines:
    case Nil{}:
      Socket.close(s)
    case Con{line,headers}:
      LabBound.request(LabNet.path(String.split(line,' ')),s,headers,key,state)

def LabBound.request(path: Maybe<&2,String>,s: Socket,headers: List<&2,String>,key: String,state: Chan(Bool)) -> IO(Unit):
  match path:
    case Some{value}:
      LabBound.route(s,value,headers,key,state)
    case None{}:
      LabNet.write(s,LabNet.http("400 Bad Request",""))

@unsafe def LabBound.read(fuel: Nat,s: Socket,+acc: String,key: String,state: Chan(Bool)) -> IO(Unit):
  LabBound.read_bounded(Nat.is_le(String.length(acc),8192n),fuel,s,acc,key,state)

@unsafe def LabBound.read_bounded(ok: Bool,fuel: Nat,s: Socket,+acc: String,key: String,state: Chan(Bool)) -> IO(Unit):
  match ok:
    case True{}:
      LabBound.read_complete(String.contains(acc,"\r\n\r\n"),fuel,s,acc,key,state)
    case False{}:
      Socket.close(s)

@unsafe def LabBound.read_complete(complete: Bool,fuel: Nat,s: Socket,acc: String,key: String,state: Chan(Bool)) -> IO(Unit):
  match complete:
    case True{}:
      LabBound.parse(s,String.lines(acc),key,state)
    case False{}:
      LabBound.read_more(fuel,s,acc,key,state)

@unsafe def LabBound.read_more(fuel: Nat,s: Socket,acc: String,key: String,state: Chan(Bool)) -> IO(Unit):
  match fuel:
    case 0n:
      Socket.close(s)
    case 1n+rest:
      do IO<Unit>:
        got : Socket & Poll<&1,&1,Result<&1,&1,U32 & String,Maybe<&1,String>>,Unit> <- TCP.try_recv(s,8192,1000)
        LabBound.read_pair(got,rest,acc,key,state)

@unsafe def LabBound.read_pair(got: Socket & Poll<&1,&1,Result<&1,&1,U32 & String,Maybe<&1,String>>,Unit>,fuel: Nat,acc: String,key: String,state: Chan(Bool)) -> IO(Unit):
  (s,poll) = got
  LabBound.read_poll(poll,fuel,s,acc,key,state)

@unsafe def LabBound.read_poll(poll: Poll<&1,&1,Result<&1,&1,U32 & String,Maybe<&1,String>>,Unit>,fuel: Nat,s: Socket,acc: String,key: String,state: Chan(Bool)) -> IO(Unit):
  match poll:
    case Ready{Done{Some{chunk}}}:
      LabBound.read(fuel,s,acc ++ chunk,key,state)
    case _:
      Socket.close(s)

# 80 concurrent connections maximum; only one owns the authenticated slot.
# A capacity token is reserved before spawning a task and returned on exit.
def LabBound.task(s: Socket,key: String,state: Chan(Bool),slots: Chan(Unit)) -> IO(Unit):
  do IO<Unit>:
    LabBound.read(16n,s,"",key,state)
    token : Maybe<&1,Unit> <- Chan.recv(Unit,slots)
    return Unit{}

@unsafe def LabBound.serve(fuel: Nat,listener: Listener,+key: String,+state: Chan(Bool),+slots: Chan(Unit)) -> IO(Unit):
  match fuel:
    case 0n:
      Listener.close(listener)
    case 1n+rest:
      do IO<Unit>:
        pair : Listener & Result<&1,&1,U32 & String,Socket> <- TCP.accept(listener)
        LabBound.serve_pair(pair,rest,key,state,slots)

@unsafe def LabBound.serve_pair(pair: Listener & Result<&1,&1,U32 & String,Socket>,fuel: Nat,key: String,state: Chan(Bool),slots: Chan(Unit)) -> IO(Unit):
  (listener,result) = pair
  LabBound.serve_result(result,listener,fuel,key,state,slots)

@unsafe def LabBound.serve_result(result: Result<&1,&1,U32 & String,Socket>,listener: Listener,fuel: Nat,+key: String,+state: Chan(Bool),+slots: Chan(Unit)) -> IO(Unit):
  match result:
    case Fail{error}:
      Listener.close(listener)
    case Done{sock}:
      do IO<Unit>:
        admitted : Poll<&1,&1,Result<&1,&1,Unit,Unit>,Unit> <- Chan.try_send(Unit,slots,Unit{},0)
        LabBound.spawn(admitted,sock,key,state,slots)
        LabBound.serve(fuel,listener,key,state,slots)

def LabBound.spawn(admitted: Poll<&1,&1,Result<&1,&1,Unit,Unit>,Unit>,s: Socket,key: String,state: Chan(Bool),slots: Chan(Unit)) -> IO(Unit):
  match admitted:
    case Ready{Done{u}}:
      IO.spawn(Unit,LabBound.task(s,key,state,slots))
    case _:
      Socket.close(s)

def LabBound.start(validated: Maybe<&2,String>,key: String) -> IO(Unit):
  match validated:
    case None{}:
      IO.die(Unit,1,"invalid bound public key")
    case Some{out}:
      do IO<Unit>:
        state : Chan(Bool) <- Chan.new(Bool,1)
        LabBound.start_state(state,key)

def LabBound.start_state(+state: Chan(Bool),key: String) -> IO(Unit):
  do IO<Unit>:
    sent : Result<&1,&1,Bool,Unit> <- Chan.send(Bool,state,False{})
    slots : Chan(Unit) <- Chan.new(Unit,80)
    listener : Listener <- IO.try(Listener,TCP.listen("127.0.0.1",LAB_PORT))
    LabBound.serve(4096n,listener,key,state,slots)

def LabBound.config(config: Result<&1,&1,U32 & String,String>) -> IO(Unit):
  match config:
    case Fail{err}:
      IO.die(Unit,1,"bound public key required")
    case Done{+key}:
      do IO<Unit>:
        checked : Maybe<&2,String> <- LabCrypto.call(["validate",key],"")
        LabBound.start(checked,key)

def main() -> IO(Unit):
  do IO<Unit>:
    config : Result<&1,&1,U32 & String,String> <- IO.get_env("SEH_SLH_PUBLIC_KEY_B64")
    LabBound.config(config)
