# Bounded, loopback-only laboratory transport. It knows HTTP/WebSocket wire
# mechanics, not application routes, JSON, close reasons or readiness policy.
# No bound/authenticated service path is implemented here.
def LabNet.bytes(s: String) -> List<&2, U32>:
  match s:
    case SNil{}:
      []
    case SCon{Chr{c}, tail}:
      c <> LabNet.bytes(tail)

@unsafe def LabNet.header(+name: String, lines: List<&2, String>) -> String:
  match lines:
    case Nil{}:
      ""
    case Con{+line, tail}:
      LabNet.header_empty(String.is_empty(String.trim(line)), name, line, tail)

@unsafe def LabNet.header_empty(empty: Bool, +name: String, +line: String, tail: List<&2, String>) -> String:
  match empty:
    case True{}:
      ""
    case False{}:
      LabNet.header_hit(String.starts_with(String.to_lower(line), name), name, line, tail)

@unsafe def LabNet.header_hit(hit: Bool, name: String, line: String, tail: List<&2, String>) -> String:
  match hit:
    case True{}:
      String.trim(String.drop(line, String.length(name)))
    case False{}:
      LabNet.header(name, tail)

def LabNet.path(words: List<&2, String>) -> Maybe<&2, String>:
  match words:
    case Con{method, Con{path, Con{version, Nil{}}}}:
      LabNet.path_valid(Bool.and(String.eq(method, "GET"), String.eq(String.trim(version), "HTTP/1.1")), path)
    case _:
      None{}

def LabNet.path_valid(valid: Bool, path: String) -> Maybe<&2, String>:
  match valid:
    case True{}:
      Some{path}
    case False{}:
      None{}

def LabNet.http(status: String, +body: String) -> String:
  "HTTP/1.1 " ++ status ++ "\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: " ++ Nat.show(String.length(body)) ++ "\r\nConnection: close\r\n\r\n" ++ body

def LabNet.write_done(pair: Socket & Result<&1, &1, (U32 & String) & List<&2, U32>, Unit>) -> IO(Unit):
  (s, result) = pair
  Socket.close(s)

def LabNet.write(s: Socket, message: String) -> IO(Unit):
  do IO<Unit>:
    sent : Socket & Result<&1, &1, (U32 & String) & List<&2, U32>, Unit> <- TCP.send(s, message)
    LabNet.write_done(sent)

def LabNet.ws_accept(key: String) -> String:
  LabB64.encode(LabSHA.sha1(LabNet.bytes(key ++ "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")))

def LabNet.ws_send(s: Socket, key: String) -> IO(Unit):
  head = "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " ++ LabNet.ws_accept(key) ++ "\r\n\r\n"
  data = List.append(&2, U32, LabNet.bytes(head), LabPolicy.close_frame())
  do IO<Unit>:
    sent : Socket & Result<&1, &1, (U32 & String) & List<&2, U32>, Unit> <- TCP.send_bytes(s, data)
    LabNet.write_done(sent)

def LabNet.ws_key(s: Socket, key: String, decoded: Result<&1, &1, Pair(U32, String), List<&2, U32>>) -> IO(Unit):
  match decoded:
    case Done{bytes}:
      LabNet.ws_key_valid(Nat.is_eq(List.length(&2, U32, bytes), 16n), s, key)
    case Fail{error}:
      LabNet.write(s, LabNet.http("400 Bad Request", ""))

def LabNet.ws_key_valid(valid: Bool, s: Socket, key: String) -> IO(Unit):
  match valid:
    case True{}:
      LabNet.ws_send(s, key)
    case False{}:
      LabNet.write(s, LabNet.http("400 Bad Request", ""))

@unsafe def LabNet.has_upgrade(tokens: List<&2, String>) -> Bool:
  match tokens:
    case Nil{}:
      False{}
    case Con{token, tail}:
      LabNet.upgrade_token(String.eq(String.trim(String.to_lower(token)), "upgrade"), tail)

@unsafe def LabNet.upgrade_token(hit: Bool, tail: List<&2, String>) -> Bool:
  match hit:
    case True{}:
      True{}
    case False{}:
      LabNet.has_upgrade(tail)

def LabNet.upgrade(s: Socket, +lines: List<&2, String>) -> IO(Unit):
  upgrade = String.eq(String.to_lower(LabNet.header("upgrade:", lines)), "websocket")
  connection = LabNet.has_upgrade(String.split(LabNet.header("connection:", lines), ','))
  version = String.eq(LabNet.header("sec-websocket-version:", lines), "13")
  LabNet.upgrade_valid(Bool.and(upgrade, Bool.and(connection, version)), s, lines)

def LabNet.upgrade_valid(valid: Bool, s: Socket, lines: List<&2, String>) -> IO(Unit):
  match valid:
    case False{}:
      LabNet.write(s, LabNet.http("426 Upgrade Required", ""))
    case True{}:
      +key = LabNet.header("sec-websocket-key:", lines)
      LabNet.ws_key(s, key, LabB64.decode(key))

def LabNet.respond(s: Socket, status: String, body: Maybe<&2, String>) -> IO(Unit):
  match body:
    case Some{text}:
      LabNet.write(s, LabNet.http(status, text))
    case None{}:
      Socket.close(s)

def LabNet.route(s: Socket, status: Maybe<&2, String>, headers: List<&2, String>) -> IO(Unit):
  match status:
    case Some{value}:
      LabNet.respond(s, value, LabPolicy.body())
    case None{}:
      LabNet.upgrade(s, headers)

def LabNet.request(s: Socket, path: Maybe<&2, String>, headers: List<&2, String>) -> IO(Unit):
  match path:
    case Some{value}:
      LabNet.route(s, LabPolicy.status(value), headers)
    case None{}:
      LabNet.write(s, LabNet.http("400 Bad Request", ""))

def LabNet.parse(s: Socket, lines: List<&2, String>) -> IO(Unit):
  match lines:
    case Nil{}:
      Socket.close(s)
    case Con{line, headers}:
      LabNet.request(s, LabNet.path(String.split(line, ' ')), headers)

# Limit total header size and the number of reads, including slow clients.
# A single connection is serviced at a time in this first lab slice.
@unsafe def LabNet.read(fuel: Nat, s: Socket, +acc: String) -> IO(Unit):
  LabNet.read_bounded(Nat.is_le(String.length(acc), 8192n), fuel, s, acc)

@unsafe def LabNet.read_bounded(bounded: Bool, fuel: Nat, s: Socket, +acc: String) -> IO(Unit):
  match bounded:
    case False{}:
      Socket.close(s)
    case True{}:
      LabNet.read_complete(String.contains(acc, "\r\n\r\n"), fuel, s, acc)

@unsafe def LabNet.read_complete(complete: Bool, fuel: Nat, s: Socket, acc: String) -> IO(Unit):
  match complete:
    case True{}:
      LabNet.parse(s, String.lines(acc))
    case False{}:
      LabNet.read_more(fuel, s, acc)

@unsafe def LabNet.read_more(fuel: Nat, s: Socket, acc: String) -> IO(Unit):
  match fuel:
    case 0n:
      Socket.close(s)
    case 1n+rest:
      do IO<Unit>:
        got : Socket & Poll<&1, &1, Result<&1, &1, U32 & String, Maybe<&1, String>>, Unit> <- TCP.try_recv(s, 8192, 1000)
        LabNet.read_pair(rest, acc, got)

@unsafe def LabNet.read_pair(fuel: Nat, acc: String, got: Socket & Poll<&1, &1, Result<&1, &1, U32 & String, Maybe<&1, String>>, Unit>) -> IO(Unit):
  (sock, poll) = got
  LabNet.read_poll(fuel, acc, sock, poll)

@unsafe def LabNet.read_poll(fuel: Nat, acc: String, sock: Socket, poll: Poll<&1, &1, Result<&1, &1, U32 & String, Maybe<&1, String>>, Unit>) -> IO(Unit):
  match poll:
    case Wait{unused}:
      Socket.close(sock)
    case Ready{result}:
      LabNet.read_result(fuel, acc, sock, result)

@unsafe def LabNet.read_result(fuel: Nat, acc: String, sock: Socket, result: Result<&1, &1, U32 & String, Maybe<&1, String>>) -> IO(Unit):
  match result:
    case Done{Some{chunk}}:
      LabNet.read(fuel, sock, acc ++ chunk)
    case _:
      Socket.close(sock)

@unsafe def LabNet.serve(fuel: Nat, listener: Listener) -> IO(Unit):
  match fuel:
    case 0n:
      Listener.close(listener)
    case 1n+rest:
      do IO<Unit>:
        accepted : Listener & Result<&1, &1, U32 & String, Socket> <- TCP.accept(listener)
        LabNet.serve_pair(rest, accepted)

@unsafe def LabNet.serve_pair(fuel: Nat, accepted: Listener & Result<&1, &1, U32 & String, Socket>) -> IO(Unit):
  (next, result) = accepted
  LabNet.serve_result(fuel, next, result)

@unsafe def LabNet.serve_result(fuel: Nat, next: Listener, result: Result<&1, &1, U32 & String, Socket>) -> IO(Unit):
  match result:
    case Done{sock}:
      do IO<Unit>:
        LabNet.read(16n, sock, "")
        LabNet.serve(fuel, next)
    case Fail{err}:
      Listener.close(next)

def LabNet.start(log: Maybe<&2, String>) -> IO(Unit):
  match log:
    case None{}:
      IO.die(Unit, 1, "startup JSON lowering failed")
    case Some{text}:
      do IO<Unit>:
        listener : Listener <- IO.try(Listener, TCP.listen("127.0.0.1", LAB_PORT))
        IO.print(text)
        LabNet.serve(4096n, listener)

def LabNet.guard(config: Result<&1, &1, U32 & String, String>) -> IO(Unit):
  match config:
    case Done{value}:
      LabNet.guard_empty(String.is_empty(value))
    case Fail{error}:
      LabNet.start(LabPolicy.startup())

def LabNet.guard_empty(empty: Bool) -> IO(Unit):
  match empty:
    case True{}:
      LabNet.start(LabPolicy.startup())
    case False{}:
      IO.die(Unit, 1, "bound service is not translated")

def main() -> IO(Unit):
  do IO<Unit>:
    config : Result<&1, &1, U32 & String, String> <- IO.get_env("SEH_SLH_PUBLIC_KEY_B64")
    LabNet.guard(config)
