"""Restricted source-linked HTTP/WebSocket refusal listener lowering.

The application policy is extracted from a Python websockets.sync.server
unbound branch, not implemented in the transport. Only the recognized callback
shape is admitted; the bound branch is deliberately NOT translated. Native TCP
and the pinned pure SHA1/base64 modules supply transport, not SEH policy.
"""
from __future__ import annotations

import ast
from http import HTTPStatus
import json
from pathlib import Path

from . import translate
from .syntax import TranslationError
from .declaration_order import reorder_bend_declarations

PACKAGE = '0xd66ee682d4ce8c656782ca9e0e4634cb'


def shape(node):
    return ast.dump(node, include_attributes=False)


def stmt(text):
    return ast.parse(text).body[0]


def require(condition, message):
    if not condition:
        raise TranslationError('listener lowering: ' + message, source='<listener>')


def same(node, text):
    return shape(node) == shape(stmt(text))


def literal(node, typ):
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        value = None
    require(type(value) is typ, f'expected {typ.__name__} literal')
    return value


def function(nodes, name):
    found = [n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name]
    require(len(found) == 1, f'expected one function {name}')
    return found[0]


def extract_policy(source):
    """Recognize the exact control/effect contract; permit data changes.

    No source execution, application imports, or evaluation of callback results.
    The health body and log use the normal JSON compiler. The transport's
    response/close calls are lowered from their actual AST arguments.
    """
    tree = ast.parse(source)
    main = function(tree.body, 'main')
    frame = function(tree.body, '_frame')
    # Validate the source's configuration/guard and the effect shell. The lab
    # specializes host/port and requires the unbound environment at runtime.
    prefix = '''p = argparse.ArgumentParser(description='Main Computer SEH opaque relay service')
p.add_argument('--host', default=os.getenv('SEH_HOST','0.0.0.0'))
p.add_argument('--port', type=int, default=int(os.getenv('SEH_PORT','8080')))
args = p.parse_args(argv)
encoded = os.getenv('SEH_SLH_PUBLIC_KEY_B64','')'''
    expected = ast.parse(prefix).body
    require(len(main.body) > len(expected) and all(shape(a) == shape(b) for a,b in zip(main.body, expected)), 'unsupported entry configuration')
    branch = main.body[len(expected)]
    require(isinstance(branch, ast.If) and shape(branch.test) == shape(ast.parse('not encoded', mode='eval').body) and not branch.orelse, 'missing unbound guard')
    require(len(branch.body) == 6, 'unsupported unbound effect sequence')
    imp, reject, health, log, listener, ret = branch.body
    require(same(imp, 'from websockets.sync.server import serve') and same(ret, 'return'), 'unsupported listener import/return')
    require(isinstance(reject, ast.FunctionDef) and isinstance(health, ast.FunctionDef), 'expected local callbacks')
    require(reject.name == 'reject' and health.name == 'health', 'unexpected callback names')
    require(shape(reject.args) == shape(stmt('def reject(ws): pass').args) and not reject.decorator_list and reject.returns is None and len(reject.body) == 1, 'unsupported close callback')
    close = reject.body[0]
    require(isinstance(close, ast.Expr) and isinstance(close.value, ast.Call), 'expected close call')
    call = close.value
    require(shape(call.func) == shape(ast.parse('ws.close', mode='eval').body) and not call.args and [k.arg for k in call.keywords] == ['code','reason'], 'unsupported close effect')
    code, reason = literal(call.keywords[0].value, int), literal(call.keywords[1].value, str)
    require((code in (1000,1001,1002,1003,1007,1008,1009,1010,1011,1012,1013,1014) or 3000 <= code <= 4999) and reason.isascii() and len(reason) <= 123, 'unsupported close payload')
    require(shape(health.args) == shape(stmt('def health(conn, request): pass').args) and not health.decorator_list and health.returns is None and len(health.body) == 2 and same(health.body[1], 'return None'), 'unsupported HTTP callback')
    route = health.body[0]
    require(isinstance(route, ast.If) and not route.orelse and len(route.body) == 2 and isinstance(route.test, ast.Compare) and len(route.test.ops) == 1 and isinstance(route.test.ops[0], ast.In) and shape(route.test.left) == shape(ast.parse('request.path',mode='eval').body), 'unsupported route guard')
    paths = literal(route.test.comparators[0], tuple)
    require(len(paths) == 2 and len(set(paths)) == 2 and all(type(p) is str and p.startswith('/') and p.isascii() and not any(c.isspace() for c in p) for p in paths), 'unsupported route paths')
    selector, response = route.body
    require(isinstance(selector, ast.Assign) and len(selector.targets) == 1 and isinstance(selector.targets[0],ast.Name) and isinstance(selector.value, ast.Compare) and len(selector.value.ops) == 1 and isinstance(selector.value.ops[0], ast.Eq) and shape(selector.value.left) == shape(route.test.left), 'unsupported status selector')
    yes = literal(selector.value.comparators[0],str)
    require(yes in paths, 'status selector outside route set')
    require(isinstance(response,ast.Return) and isinstance(response.value,ast.Call), 'expected HTTP response')
    respond = response.value
    require(shape(respond.func) == shape(ast.parse('conn.respond',mode='eval').body) and len(respond.args) == 2 and not respond.keywords, 'unsupported HTTP response effect')
    status, body = respond.args
    require(isinstance(status,ast.IfExp) and isinstance(status.test,ast.Name) and status.test.id == selector.targets[0].id, 'unsupported HTTP status expression')
    def http_status(n):
        require(isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id == 'HTTPStatus' and n.attr in HTTPStatus.__members__, 'unsupported HTTP status')
        v = HTTPStatus[n.attr]
        return f'{v.value} {v.phrase}'
    statuses = [http_status(status.body), http_status(status.orelse)]
    require(isinstance(log,ast.Expr) and isinstance(log.value,ast.Call) and shape(log.value.func) == shape(ast.parse('print',mode='eval').body) and len(log.value.args)==1 and len(log.value.keywords)==1 and log.value.keywords[0].arg=='flush' and literal(log.value.keywords[0].value,bool), 'unsupported startup log')
    require(same(listener, '''with serve(reject, args.host, args.port, process_request=health,
 compression=None, max_size=8192, max_queue=MAX_WS_BUFFERED_MESSAGES) as server:
    server.serve_forever()'''), 'unsupported serve options or lifecycle')
    # Refuse additional globals/unknown calls through the existing JSON frontend.
    require(not frame.decorator_list, 'decorated frame helper unsupported')
    return frame, body, log.value.args[0], paths, yes, statuses, code, reason


def bend_string(value):
    require(value.isascii() and all(ord(c)>=32 or c in '\r\n\t' for c in value), 'unsupported Bend string')
    return json.dumps(value, ensure_ascii=True)


def lower_unbound_listener(source: str, *, port=18080):
    require(type(port) is int and 1024 <= port <= 65535, 'lab port must be unprivileged')
    frame, body, log, paths, yes, statuses, code, reason = extract_policy(source)
    class SpecializePort(ast.NodeTransformer):
        def visit_Attribute(self, node):
            if shape(node) == shape(ast.parse('args.port',mode='eval').body):
                return ast.copy_location(ast.Constant(port),node)
            return self.generic_visit(node)
    log = SpecializePort().visit(log)
    # Two real compiled JSON functions; the native effect shell consumes their
    # heap results. No response JSON is serialized by the host compiler.
    program = ('import json\n' + ast.unparse(frame) + '\n'
               + 'def startup() -> str:\n    return ' + ast.unparse(log) + '\n'
               + 'def main() -> str:\n    return ' + ast.unparse(body) + '\n')
    compiled = translate(program, 'unbound_listener_policy', show_return=True)
    old = 'def main() -> IO(Unit):\n  PyHeap.finish_io(py3_main(PyHeap.empty()), True{})'
    require(compiled.count(old)==1 and 'def py3_startup(' in compiled, 'unexpected JSON compiler entry ABI')
    compiled = compiled.replace(old,'')
    other = next(p for p in paths if p != yes)
    payload = [code//256, code%256, *reason.encode('ascii')]
    # WebSocket server frames are unmasked. Encode framing, not a response
    # fixture. The code and reason are extracted from the source callback.
    close_bytes = [136,len(payload),*payload]
    policy = f'''def LabPolicy.text(pair: PyHeap & PyValue) -> Maybe<&2, String>:
  (heap, value) = pair
  LabPolicy.text_value(value)

def LabPolicy.text_value(value: PyValue) -> Maybe<&2, String>:
  match value:
    case VStr{{s}}:
      Some{{s}}
    case _:
      None{{}}

def LabPolicy.body() -> Maybe<&2, String>:
  LabPolicy.text(py3_main(PyHeap.empty()))

def LabPolicy.startup() -> Maybe<&2, String>:
  LabPolicy.text(py3_startup(PyHeap.empty()))

def LabPolicy.status(+path: String) -> Maybe<&2, String>:
  LabPolicy.status_first(String.eq(path, {bend_string(yes)}), path)

def LabPolicy.status_first(first: Bool, path: String) -> Maybe<&2, String>:
  match first:
    case True{{}}:
      Some{{{bend_string(statuses[0])}}}
    case False{{}}:
      LabPolicy.status_second(String.eq(path, {bend_string(other)}))

def LabPolicy.status_second(second: Bool) -> Maybe<&2, String>:
  match second:
    case True{{}}:
      Some{{{bend_string(statuses[1])}}}
    case False{{}}:
      None{{}}

def LabPolicy.close_frame() -> List<&2, U32>:
  {close_bytes}
'''
    transport = Path(__file__).with_name('listener_transport.bend').read_text(encoding='utf-8').replace('LAB_PORT',str(port))
    imports = f'import {PACKAGE}/sha1.bend as LabSHA\nimport {PACKAGE}/b64.bend as LabB64\n'
    require(compiled.startswith('import Base\n'), 'unexpected compiler prelude')
    return reorder_bend_declarations(compiled.replace('import Base\n','import Base\n'+imports,1)+'\n'+policy+'\n'+transport)
