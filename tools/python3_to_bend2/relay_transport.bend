# Restricted relay: one primary session, 16 streams, at most 8 admin streams.
# Per-stream queues contain at most 16 packets of <=4096 bytes. Outbound
# tunnel queue holds <=64 encoded messages. A single task owns each socket.
# Session-scoped channels are never reused after disconnect.
type RelayMessage is Data:
  RelayData{bytes: List<&2,U32>}
  RelayAuth{}
  RelayClose{code: U32, reason: String}

type RelayStream is Data:
  RelayStream{id: String, admin: Bool, inbox: Chan(RelayMessage), stop: Chan(RelayMessage)}

type RelaySession is Data:
  RelaySession{outbox: Chan(String), registry: Chan(List<&2,RelayStream>)}

type RelayRead is Data:
  RelayIdle{}
  RelayEnd{}
  RelayPacket{packet: LabPacket}

# The initial short poll reads precisely one byte. A timeout before any byte
# is idle, but a timeout within a frame terminates that connection.
def RelayIO.poll(s: Socket) -> IO(Socket & RelayRead):
  do IO<Socket & RelayRead>:
    got : Socket & Poll<&1,&1,Result<&1,&1,U32 & String,Maybe<&1,List<&2,U32>>>,Unit> <- TCP.try_recv_bytes(s,1,10)
    RelayIO.poll_pair(got)

def RelayIO.poll_pair(pair: Socket & Poll<&1,&1,Result<&1,&1,U32 & String,Maybe<&1,List<&2,U32>>>,Unit>) -> IO(Socket & RelayRead):
  (s,p) = pair
  RelayIO.poll_result(p,s)

def RelayIO.poll_result(p: Poll<&1,&1,Result<&1,&1,U32 & String,Maybe<&1,List<&2,U32>>>,Unit>,s: Socket) -> IO(Socket & RelayRead):
  match p:
    case Wait{u}:
      IO.pure(Socket & RelayRead,(s,RelayIdle{}))
    case Ready{Done{Some{Con{op,Nil{}}}}}:
      do IO<Socket & RelayRead>:
        now : Nat <- IO.now()
        RelayIO.read_at(s,op,Nat.add(now,2000n))
    case _:
      IO.pure(Socket & RelayRead,(s,RelayEnd{}))

# As in LabWire.frame_at, put the reusable deadline in a typed parameter.
# Bend requires typed local binders in do blocks.
def RelayIO.read_at(s: Socket,op: U32,+deadline: Nat) -> IO(Socket & RelayRead):
  do IO<Socket & RelayRead>:
    header : Socket & Maybe<&2,List<&2,U32>> <- LabWire.read(s,1n,deadline,[op])
    got : Socket & Maybe<&2,LabPacket> <- LabWire.header_pair(header,deadline)
    return RelayIO.convert(got)

def RelayIO.convert(pair: Socket & Maybe<&2,LabPacket>) -> Socket & RelayRead:
  (s,p) = pair
  (s,RelayIO.convert_packet(p))

def RelayIO.convert_packet(p: Maybe<&2,LabPacket>) -> RelayRead:
  match p:
    case Some{packet}:
      RelayPacket{packet}
    case None{}:
      RelayEnd{}

def LabWire.send(s: Socket,bytes: List<&2,U32>) -> IO(Socket & Bool):
  do IO<Socket & Bool>:
    pair : Socket & Poll<&1,&1,Result<&1,&1,(U32 & String) & List<&2,U32>,Unit>,List<&2,U32>> <- TCP.try_send_bytes(s,bytes,1000)
    return RelayIO.send_pair(pair)

def RelayIO.send_pair(pair: Socket & Poll<&1,&1,Result<&1,&1,(U32 & String) & List<&2,U32>,Unit>,List<&2,U32>>) -> Socket & Bool:
  (s,p) = pair
  (s,RelayIO.send_ok(p))

def RelayIO.send_ok(p: Poll<&1,&1,Result<&1,&1,(U32 & String) & List<&2,U32>,Unit>,List<&2,U32>>) -> Bool:
  match p:
    case Ready{Done{u}}:
      True{}
    case _:
      False{}

# State cell protects admission only. No network effect while holding it.
def LabState.ready(state: Chan(Maybe<&2,RelaySession>)) -> IO(Bool):
  do IO<Bool>:
    value : Maybe<&2,RelaySession> <- RelayState.get(state)
    return RelayState.present(value)

def RelayState.present(value: Maybe<&2,RelaySession>) -> Bool:
  match value:
    case Some{session}:
      True{}
    case None{}:
      False{}

def RelayState.get(+state: Chan(Maybe<&2,RelaySession>)) -> IO(Maybe<&2,RelaySession>):
  do IO<Maybe<&2,RelaySession>>:
    got : Maybe<&1,Maybe<&2,RelaySession>> <- Chan.recv(Maybe<&2,RelaySession>,state)
    RelayState.get_value(got,state)

def RelayState.get_value(got: Maybe<&1,Maybe<&2,RelaySession>>,state: Chan(Maybe<&2,RelaySession>)) -> IO(Maybe<&2,RelaySession>):
  match got:
    case Some{+value}:
      do IO<Maybe<&2,RelaySession>>:
        sent : Result<&1,&1,Maybe<&2,RelaySession>,Unit> <- Chan.send(Maybe<&2,RelaySession>,state,value)
        return value
    case None{}:
      IO.pure(Maybe<&2,RelaySession>,None{})

def LabBound.admit(s: Socket,+state: Chan(Maybe<&2,RelaySession>)) -> IO(Unit):
  do IO<Unit>:
    got : Maybe<&1,Maybe<&2,RelaySession>> <- Chan.recv(Maybe<&2,RelaySession>,state)
    RelayState.admit(got,s,state)

def RelayState.admit(got: Maybe<&1,Maybe<&2,RelaySession>>,s: Socket,+state: Chan(Maybe<&2,RelaySession>)) -> IO(Unit):
  match got:
    case Some{Some{current}}:
      do IO<Unit>:
        sent : Result<&1,&1,Maybe<&2,RelaySession>,Unit> <- Chan.send(Maybe<&2,RelaySession>,state,Some{current})
        LabWire.close(s,1008,"SLH_ALREADY_CONNECTED")
    case Some{None{}}:
      do IO<Unit>:
        +outbox : Chan(String) <- Chan.new(String,64)
        +registry : Chan(List<&2,RelayStream>) <- Chan.new(List<&2,RelayStream>,1)
        seeded : Result<&1,&1,List<&2,RelayStream>,Unit> <- Chan.send(List<&2,RelayStream>,registry,[])
        sent : Result<&1,&1,Maybe<&2,RelaySession>,Unit> <- Chan.send(Maybe<&2,RelaySession>,state,Some{RelaySession{outbox,registry}})
        pair : Socket & Bool <- LabWire.send(s,LabWire.encode(129,LabNet.bytes(LabBound.accepted())))
        RelayTunnel.sent(pair,outbox,registry)
        RelayState.finish(state,outbox,registry)
    case None{}:
      Socket.close(s)

def RelayState.finish(+state: Chan(Maybe<&2,RelaySession>),outbox: Chan(String),+registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  do IO<Unit>:
    old : Maybe<&1,Maybe<&2,RelaySession>> <- Chan.recv(Maybe<&2,RelaySession>,state)
    sent : Result<&1,&1,Maybe<&2,RelaySession>,Unit> <- Chan.send(Maybe<&2,RelaySession>,state,None{})
    Chan.close(String,outbox)
    rows : Maybe<&1,List<&2,RelayStream>> <- Chan.recv(List<&2,RelayStream>,registry)
    Chan.close(List<&2,RelayStream>,registry)
    RelayState.retire(rows)

def RelayState.retire(rows: Maybe<&1,List<&2,RelayStream>>) -> IO(Unit):
  match rows:
    case Some{items}:
      RelayState.retire_list(items)
    case None{}:
      IO.pure(Unit,Unit{})

@unsafe def RelayState.retire_list(items: List<&2,RelayStream>) -> IO(Unit):
  match items:
    case Nil{}:
      IO.pure(Unit,Unit{})
    case Con{RelayStream{id,admin,inbox,stop},rest}:
      do IO<Unit>:
        RelayState.signal(stop,RelayClose{1012,"SLH_DISCONNECTED"})
        Chan.close(RelayMessage,inbox)
        RelayState.retire_list(rest)

def RelayState.signal(stop: Chan(RelayMessage),msg: RelayMessage) -> IO(Unit):
  do IO<Unit>:
    r : Poll<&1,&1,Result<&1,&1,RelayMessage,Unit>,RelayMessage> <- Chan.try_send(RelayMessage,stop,msg,0)
    return Unit{}

def RelayState.offer(outbox: Chan(String),text: String) -> IO(Bool):
  do IO<Bool>:
    r : Poll<&1,&1,Result<&1,&1,String,Unit>,String> <- Chan.try_send(String,outbox,text,0)
    return RelayState.offered(r)

def RelayState.offered(r: Poll<&1,&1,Result<&1,&1,String,Unit>,String>) -> Bool:
  match r:
    case Ready{Done{u}}:
      True{}
    case _:
      False{}

# Registry helpers. Stream ids are unpredictable URL-safe nonces and unique
# within one session. Queues are isolated and never moved between sessions.
@unsafe def RelayState.find(items: List<&2,RelayStream>,+id: String) -> Maybe<&2,RelayStream>:
  match items:
    case Nil{}:
      None{}
    case Con{+row,rest}:
      RelayState.find_row(row,rest,id)

@unsafe def RelayState.find_row(+row: RelayStream,rest: List<&2,RelayStream>,+id: String) -> Maybe<&2,RelayStream>:
  match row:
    case RelayStream{+sid,admin,inbox,stop}:
      RelayState.find_hit(String.eq(sid,id),RelayStream{sid,admin,inbox,stop},rest,id)

@unsafe def RelayState.find_hit(ok: Bool,row: RelayStream,rest: List<&2,RelayStream>,id: String) -> Maybe<&2,RelayStream>:
  match ok:
    case True{}:
      Some{row}
    case False{}:
      RelayState.find(rest,id)

@unsafe def RelayState.remove(items: List<&2,RelayStream>,+id: String) -> List<&2,RelayStream>:
  match items:
    case Nil{}:
      []
    case Con{row,rest}:
      RelayState.remove_row(row,rest,id)

@unsafe def RelayState.remove_row(row: RelayStream,rest: List<&2,RelayStream>,+id: String) -> List<&2,RelayStream>:
  match row:
    case RelayStream{+sid,admin,inbox,stop}:
      RelayState.remove_hit(String.eq(sid,id),RelayStream{sid,admin,inbox,stop},rest,id)

@unsafe def RelayState.remove_hit(ok: Bool,row: RelayStream,rest: List<&2,RelayStream>,id: String) -> List<&2,RelayStream>:
  match ok:
    case True{}:
      rest
    case False{}:
      row <> RelayState.remove(rest,id)

@unsafe def RelayState.admin_count(items: List<&2,RelayStream>) -> Nat:
  match items:
    case Nil{}:
      0n
    case Con{RelayStream{id,admin,inbox,stop},rest}:
      Nat.add(RelayState.one(admin),RelayState.admin_count(rest))

def RelayState.one(value: Bool) -> Nat:
  match value:
    case True{}:
      1n
    case False{}:
      0n

# Client entry uses the selected primary session, never the global state
# after admission. Both worker and admin paths retain end-to-end SLH auth.
def LabBound.ws_primary(primary: Bool,s: Socket,+path: String,key: String,state: Chan(Maybe<&2,RelaySession>)) -> IO(Unit):
  match primary:
    case True{}:
      LabBound.authenticate(s,key,state)
    case False{}:
      +admin = String.eq(path,"/v1/seh/admin")
      RelayClient.route(Bool.or(admin,String.eq(path,"/v1/seh/client")),admin,s,state)

def RelayClient.route(valid: Bool,admin: Bool,s: Socket,state: Chan(Maybe<&2,RelaySession>)) -> IO(Unit):
  match valid:
    case False{}:
      LabWire.close(s,1008,"UNKNOWN_ROUTE")
    case True{}:
      do IO<Unit>:
        session : Maybe<&2,RelaySession> <- RelayState.get(state)
        RelayClient.session(session,admin,s)

def RelayClient.session(session: Maybe<&2,RelaySession>,admin: Bool,s: Socket) -> IO(Unit):
  match session:
    case None{}:
      LabWire.close(s,1013,"SLH_UNAVAILABLE_OR_BUSY")
    case Some{RelaySession{outbox,registry}}:
      do IO<Unit>:
        nonce : Maybe<&2,String> <- LabCrypto.call(["nonce","18"],"")
        RelayClient.nonce(nonce,admin,s,outbox,registry)

def RelayClient.nonce(nonce: Maybe<&2,String>,admin: Bool,s: Socket,outbox: Chan(String),+registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match nonce:
    case None{}:
      LabWire.close(s,1011,"CRYPTO_RUNTIME_FAILED")
    case Some{id}:
      do IO<Unit>:
        rows : Maybe<&1,List<&2,RelayStream>> <- Chan.recv(List<&2,RelayStream>,registry)
        RelayClient.register(rows,id,admin,s,outbox,registry)

def RelayClient.register(rows: Maybe<&1,List<&2,RelayStream>>,+id: String,+admin: Bool,s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match rows:
    case None{}:
      LabWire.close(s,1013,"SLH_UNAVAILABLE_OR_BUSY")
    case Some{+items}:
      RelayClient.capacity(Bool.and(RelayState.absent(RelayState.find(items,id)),Bool.and(Nat.is_lt(List.length(&2,RelayStream,items),16n),Bool.or(Bool.not(admin),Nat.is_lt(RelayState.admin_count(items),8n)))),items,id,admin,s,outbox,registry)

def RelayClient.capacity(ok: Bool,items: List<&2,RelayStream>,+id: String,+admin: Bool,s: Socket,+outbox: Chan(String),+registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match ok:
    case False{}:
      do IO<Unit>:
        sent : Result<&1,&1,List<&2,RelayStream>,Unit> <- Chan.send(List<&2,RelayStream>,registry,items)
        LabWire.close(s,1013,"SLH_UNAVAILABLE_OR_BUSY")
    case True{}:
      do IO<Unit>:
        +inbox : Chan(RelayMessage) <- Chan.new(RelayMessage,16)
        +stop : Chan(RelayMessage) <- Chan.new(RelayMessage,1)
        sent : Result<&1,&1,List<&2,RelayStream>,Unit> <- Chan.send(List<&2,RelayStream>,registry,RelayStream{id,admin,inbox,stop} <> items)
        opened : Bool <- RelayState.offer(outbox,RelayFrame.open(id,RelayClient.purpose(admin)))
        now : Nat <- IO.now()
        RelayClient.opened(opened,s,id,outbox,inbox,stop,Nat.add(now,RelayClient.timeout(admin)))
        Chan.close(RelayMessage,inbox)
        Chan.close(RelayMessage,stop)
        RelayClient.cleanup(id,outbox,registry)

def RelayClient.purpose(admin: Bool) -> String:
  match admin:
    case True{}:
      "admin"
    case False{}:
      "worker"

def RelayClient.timeout(admin: Bool) -> Nat:
  match admin:
    case True{}:
      90000n
    case False{}:
      30000n

def RelayClient.opened(ok: Bool,s: Socket,id: String,outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat) -> IO(Unit):
  match ok:
    case True{}:
      RelayClient.loop(s,id,outbox,inbox,stop,deadline,False{})
    case False{}:
      LabWire.close(s,1013,"STREAM_BACKPRESSURE")

def RelayClient.cleanup(+id: String,+outbox: Chan(String),+registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  do IO<Unit>:
    rows : Maybe<&1,List<&2,RelayStream>> <- Chan.recv(List<&2,RelayStream>,registry)
    RelayClient.remove(rows,id,registry)
    sent : Bool <- RelayState.offer(outbox,RelayFrame.close(id))
    RelayClient.close_queued(sent,outbox)

def RelayClient.remove(rows: Maybe<&1,List<&2,RelayStream>>,id: String,registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match rows:
    case None{}:
      IO.pure(Unit,Unit{})
    case Some{items}:
      do IO<Unit>:
        sent : Result<&1,&1,List<&2,RelayStream>,Unit> <- Chan.send(List<&2,RelayStream>,registry,RelayState.remove(items,id))
        return Unit{}

@unsafe def RelayClient.loop(s: Socket,id: String,outbox: Chan(String),inbox: Chan(RelayMessage),+stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  do IO<Unit>:
    halt : Poll<&1,&1,Maybe<&1,RelayMessage>,Unit> <- Chan.try_recv(RelayMessage,stop,0)
    RelayClient.check_stop(halt,s,id,outbox,inbox,stop,deadline,authenticated)

@unsafe def RelayClient.check_stop(halt: Poll<&1,&1,Maybe<&1,RelayMessage>,Unit>,s: Socket,id: String,outbox: Chan(String),+inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  match halt:
    case Ready{Some{RelayClose{code,reason}}}:
      LabWire.close(s,code,reason)
    case Wait{u}:
      do IO<Unit>:
        message : Poll<&1,&1,Maybe<&1,RelayMessage>,Unit> <- Chan.try_recv(RelayMessage,inbox,0)
        RelayClient.incoming(message,s,id,outbox,inbox,stop,deadline,authenticated)
    case _:
      LabWire.close(s,1012,"SLH_DISCONNECTED")

@unsafe def RelayClient.incoming(message: Poll<&1,&1,Maybe<&1,RelayMessage>,Unit>,s: Socket,id: String,outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  match message:
    case Ready{Some{RelayData{bytes}}}:
      do IO<Unit>:
        sent : Socket & Bool <- LabWire.send(s,LabWire.encode(130,bytes))
        RelayClient.sent(sent,id,outbox,inbox,stop,deadline,authenticated)
    case Ready{Some{RelayAuth{}}}:
      RelayClient.poll(s,id,outbox,inbox,stop,deadline,True{})
    case Ready{Some{RelayClose{code,reason}}}:
      LabWire.close(s,code,reason)
    case Ready{None{}}:
      LabWire.close(s,1012,"SLH_DISCONNECTED")
    case Wait{u}:
      RelayClient.poll(s,id,outbox,inbox,stop,deadline,authenticated)

@unsafe def RelayClient.sent(pair: Socket & Bool,id: String,outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  (s,ok) = pair
  RelayClient.sent_ok(ok,s,id,outbox,inbox,stop,deadline,authenticated)

@unsafe def RelayClient.sent_ok(ok: Bool,s: Socket,id: String,outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  match ok:
    case True{}:
      RelayClient.poll(s,id,outbox,inbox,stop,deadline,authenticated)
    case False{}:
      Socket.close(s)

@unsafe def RelayClient.poll(s: Socket,id: String,outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),+deadline: Nat,+authenticated: Bool) -> IO(Unit):
  do IO<Unit>:
    now : Nat <- IO.now()
    RelayClient.deadline(Bool.or(authenticated,Nat.is_lt(now,deadline)),s,id,outbox,inbox,stop,deadline,authenticated)

@unsafe def RelayClient.deadline(ok: Bool,s: Socket,id: String,outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  match ok:
    case False{}:
      LabWire.close(s,1008,"AUTH_TIMEOUT")
    case True{}:
      do IO<Unit>:
        pair : Socket & RelayRead <- RelayIO.poll(s)
        RelayClient.read_pair(pair,id,outbox,inbox,stop,deadline,authenticated)

@unsafe def RelayClient.read_pair(pair: Socket & RelayRead,id: String,outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  (s,r) = pair
  RelayClient.read(r,s,id,outbox,inbox,stop,deadline,authenticated)

@unsafe def RelayClient.read(r: RelayRead,s: Socket,+id: String,+outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  match r:
    case RelayIdle{}:
      RelayClient.loop(s,id,outbox,inbox,stop,deadline,authenticated)
    case RelayEnd{}:
      Socket.close(s)
    case RelayPacket{LabPacket{130,+bytes}}:
      RelayClient.binary(Nat.is_le(List.length(&2,U32,bytes),4096n),bytes,s,id,outbox,inbox,stop,deadline,authenticated)
    case RelayPacket{LabPacket{137,bytes}}:
      do IO<Unit>:
        pair : Socket & Bool <- LabWire.send(s,LabWire.encode(138,bytes))
        RelayClient.sent(pair,id,outbox,inbox,stop,deadline,authenticated)
    case RelayPacket{LabPacket{138,bytes}}:
      RelayClient.loop(s,id,outbox,inbox,stop,deadline,authenticated)
    case RelayPacket{LabPacket{136,bytes}}:
      do IO<Unit>:
        pair : Socket & Bool <- LabWire.send(s,LabWire.encode(136,bytes))
        LabWire.close_pair(pair)
    case _:
      LabWire.close(s,1008,"INVALID_STREAM_FRAME")

@unsafe def RelayClient.binary(ok: Bool,bytes: List<&2,U32>,s: Socket,+id: String,+outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  match ok:
    case False{}:
      LabWire.close(s,1008,"INVALID_STREAM_FRAME")
    case True{}:
      do IO<Unit>:
        sent : Bool <- RelayState.offer(outbox,RelayFrame.data(id,bytes))
        RelayClient.queued(sent,s,id,outbox,inbox,stop,deadline,authenticated)

@unsafe def RelayClient.queued(ok: Bool,s: Socket,id: String,outbox: Chan(String),inbox: Chan(RelayMessage),stop: Chan(RelayMessage),deadline: Nat,authenticated: Bool) -> IO(Unit):
  match ok:
    case True{}:
      RelayClient.loop(s,id,outbox,inbox,stop,deadline,authenticated)
    case False{}:
      LabWire.close(s,1013,"STREAM_BACKPRESSURE")

# One tunnel owner serializes all writes and reads. Incoming data is offered
# nonblocking to independent queues; a slow client cannot stall the tunnel.
@unsafe def RelayTunnel.sent(pair: Socket & Bool,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  (s,ok) = pair
  RelayTunnel.sent_ok(ok,s,outbox,registry)

@unsafe def RelayTunnel.sent_ok(ok: Bool,s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match ok:
    case True{}:
      RelayTunnel.poll(s,outbox,registry)
    case False{}:
      Socket.close(s)

@unsafe def RelayTunnel.loop(s: Socket,+outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  do IO<Unit>:
    got : Poll<&1,&1,Maybe<&1,String>,Unit> <- Chan.try_recv(String,outbox,0)
    RelayTunnel.outgoing(got,s,outbox,registry)

@unsafe def RelayTunnel.outgoing(got: Poll<&1,&1,Maybe<&1,String>,Unit>,s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match got:
    case Ready{Some{text}}:
      do IO<Unit>:
        sent : Socket & Bool <- LabWire.send(s,LabWire.encode(129,LabNet.bytes(text)))
        RelayTunnel.sent(sent,outbox,registry)
    case Wait{u}:
      RelayTunnel.poll(s,outbox,registry)
    case Ready{None{}}:
      Socket.close(s)

@unsafe def RelayTunnel.poll(s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  do IO<Unit>:
    pair : Socket & RelayRead <- RelayIO.poll(s)
    RelayTunnel.read_pair(pair,outbox,registry)

@unsafe def RelayTunnel.read_pair(pair: Socket & RelayRead,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  (s,r) = pair
  RelayTunnel.read(r,s,outbox,registry)

@unsafe def RelayTunnel.read(r: RelayRead,s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match r:
    case RelayIdle{}:
      RelayTunnel.loop(s,outbox,registry)
    case RelayEnd{}:
      Socket.close(s)
    case RelayPacket{LabPacket{137,bytes}}:
      do IO<Unit>:
        sent : Socket & Bool <- LabWire.send(s,LabWire.encode(138,bytes))
        RelayTunnel.sent(sent,outbox,registry)
    case RelayPacket{LabPacket{138,bytes}}:
      RelayTunnel.loop(s,outbox,registry)
    case RelayPacket{LabPacket{136,bytes}}:
      do IO<Unit>:
        sent : Socket & Bool <- LabWire.send(s,LabWire.encode(136,bytes))
        LabWire.close_pair(sent)
    case RelayPacket{LabPacket{129,+bytes}}:
      RelayTunnel.text(LabWire.ascii(bytes),LabWire.string(bytes),s,outbox,registry)
    case _:
      Socket.close(s)

@unsafe def RelayTunnel.text(ok: Bool,raw: String,s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match ok:
    case False{}:
      Socket.close(s)
    case True{}:
      RelayTunnel.decoded(PyJSON.decode(PyHeap.empty(),VStr{raw}),s,outbox,registry)

@unsafe def RelayTunnel.decoded(pair: PyHeap & PyValue,s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  (h,v) = pair
  RelayTunnel.decoded_fields(h,v,s,outbox,registry)

@unsafe def RelayTunnel.decoded_fields(+h: PyHeap,+v: PyValue,s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  RelayTunnel.fields(PyHeap.dict_get(h,v,VStr{"id"},VNone{}),PyHeap.dict_get(h,v,VStr{"op"},VNone{}),PyHeap.dict_get(h,v,VStr{"data"},VNone{}),PyHeap.dict_get(h,v,VStr{"cause"},VNone{}),s,outbox,registry)

@unsafe def RelayTunnel.fields(id: PyValue,op: PyValue,data: PyValue,cause: PyValue,s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match id:
    case VStr{+sid}:
      RelayTunnel.valid_id(Nat.is_le(String.length(sid),80n),sid,op,data,cause,s,outbox,registry)
    case _:
      Socket.close(s)

@unsafe def RelayTunnel.valid_id(ok: Bool,id: String,op: PyValue,data: PyValue,cause: PyValue,s: Socket,outbox: Chan(String),+registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match ok:
    case False{}:
      Socket.close(s)
    case True{}:
      do IO<Unit>:
        rows : Maybe<&1,List<&2,RelayStream>> <- Chan.recv(List<&2,RelayStream>,registry)
        RelayTunnel.lookup(rows,id,op,data,cause,s,outbox,registry)

@unsafe def RelayTunnel.lookup(rows: Maybe<&1,List<&2,RelayStream>>,id: String,op: PyValue,data: PyValue,cause: PyValue,s: Socket,outbox: Chan(String),+registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match rows:
    case None{}:
      Socket.close(s)
    case Some{+items}:
      do IO<Unit>:
        sent : Result<&1,&1,List<&2,RelayStream>,Unit> <- Chan.send(List<&2,RelayStream>,registry,items)
        RelayTunnel.dispatch(RelayState.find(items,id),op,data,cause,s,outbox,registry)

@unsafe def RelayTunnel.dispatch(row: Maybe<&2,RelayStream>,op: PyValue,data: PyValue,cause: PyValue,s: Socket,+outbox: Chan(String),+registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match row:
    case None{}:
      RelayTunnel.loop(s,outbox,registry)
    case Some{RelayStream{id,admin,inbox,stop}}:
      do IO<Unit>:
        valid : Bool <- RelayTunnel.operation(op,data,cause,inbox,stop)
        RelayTunnel.dispatched(valid,s,outbox,registry)

@unsafe def RelayTunnel.dispatched(ok: Bool,s: Socket,outbox: Chan(String),registry: Chan(List<&2,RelayStream>)) -> IO(Unit):
  match ok:
    case True{}:
      RelayTunnel.loop(s,outbox,registry)
    case False{}:
      Socket.close(s)

def RelayTunnel.operation(op: PyValue,data: PyValue,cause: PyValue,inbox: Chan(RelayMessage),stop: Chan(RelayMessage)) -> IO(Bool):
  match op:
    case VStr{+kind}:
      RelayTunnel.op_auth(String.eq(kind,"authenticated"),kind,data,cause,inbox,stop)
    case _:
      IO.pure(Bool,False{})

def RelayTunnel.op_auth(auth: Bool,+kind: String,data: PyValue,cause: PyValue,inbox: Chan(RelayMessage),stop: Chan(RelayMessage)) -> IO(Bool):
  match auth:
    case True{}:
      RelayTunnel.deliver(inbox,stop,RelayAuth{})
    case False{}:
      RelayTunnel.op_data(String.eq(kind,"data"),kind,data,cause,inbox,stop)

def RelayTunnel.op_data(is_data: Bool,kind: String,data: PyValue,cause: PyValue,inbox: Chan(RelayMessage),stop: Chan(RelayMessage)) -> IO(Bool):
  match is_data:
    case True{}:
      RelayTunnel.decode_data(data,inbox,stop)
    case False{}:
      RelayTunnel.op_close(String.eq(kind,"close"),cause,inbox,stop)

def RelayTunnel.op_close(close: Bool,cause: PyValue,inbox: Chan(RelayMessage),stop: Chan(RelayMessage)) -> IO(Bool):
  match close:
    case True{}:
      RelayTunnel.deliver(inbox,stop,RelayClose{RelayTunnel.close_code(cause),""})
    case False{}:
      IO.pure(Bool,False{})

def RelayTunnel.close_code(cause: PyValue) -> U32:
  match cause:
    case VStr{+value}:
      RelayTunnel.close_pressure(Bool.or(String.eq(value,"write_queue_full"),Bool.or(String.eq(value,"write_byte_budget"),String.eq(value,"write_aggregate_budget"))),value)
    case _:
      1000

def RelayTunnel.close_pressure(pressure: Bool,+value: String) -> U32:
  match pressure:
    case True{}:
      1013
    case False{}:
      RelayTunnel.close_error(Bool.or(String.eq(value,"write_error"),String.eq(value,"local_read_error")))

def RelayTunnel.close_error(error: Bool) -> U32:
  match error:
    case True{}:
      1011
    case False{}:
      1000

def RelayTunnel.decode_data(data: PyValue,inbox: Chan(RelayMessage),stop: Chan(RelayMessage)) -> IO(Bool):
  match data:
    case VStr{text}:
      RelayTunnel.decoded_data(LabB64.decode(text),inbox,stop)
    case _:
      RelayTunnel.invalid(stop)

def RelayTunnel.decoded_data(result: Result<&1,&1,Pair(U32,String),List<&2,U32>>,inbox: Chan(RelayMessage),stop: Chan(RelayMessage)) -> IO(Bool):
  match result:
    case Done{+bytes}:
      RelayTunnel.data_size(Nat.is_le(List.length(&2,U32,bytes),4096n),bytes,inbox,stop)
    case Fail{error}:
      RelayTunnel.invalid(stop)

def RelayTunnel.data_size(ok: Bool,bytes: List<&2,U32>,inbox: Chan(RelayMessage),stop: Chan(RelayMessage)) -> IO(Bool):
  match ok:
    case True{}:
      RelayTunnel.deliver(inbox,stop,RelayData{bytes})
    case False{}:
      RelayTunnel.invalid(stop)

def RelayTunnel.invalid(stop: Chan(RelayMessage)) -> IO(Bool):
  do IO<Bool>:
    RelayState.signal(stop,RelayClose{1008,"INVALID_STREAM_FRAME"})
    return True{}

def RelayTunnel.deliver(inbox: Chan(RelayMessage),stop: Chan(RelayMessage),msg: RelayMessage) -> IO(Bool):
  do IO<Bool>:
    r : Poll<&1,&1,Result<&1,&1,RelayMessage,Unit>,RelayMessage> <- Chan.try_send(RelayMessage,inbox,msg,0)
    RelayTunnel.delivered(r,stop)

def RelayTunnel.delivered(r: Poll<&1,&1,Result<&1,&1,RelayMessage,Unit>,RelayMessage>,stop: Chan(RelayMessage)) -> IO(Bool):
  match r:
    case Wait{msg}:
      do IO<Bool>:
        RelayState.signal(stop,RelayClose{1013,"STREAM_BACKPRESSURE"})
        return True{}
    case _:
      IO.pure(Bool,True{})

def RelayClient.close_queued(ok: Bool,outbox: Chan(String)) -> IO(Unit):
  match ok:
    case True{}:
      IO.pure(Unit,Unit{})
    case False{}:
      Chan.close(String,outbox)

def RelayState.absent(row: Maybe<&2,RelayStream>) -> Bool:
  match row:
    case None{}:
      True{}
    case Some{value}:
      False{}
