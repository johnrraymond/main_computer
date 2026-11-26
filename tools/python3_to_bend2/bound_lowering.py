"""Source-linked primary-tunnel lowering (not a general service linker).

Recognize reviewed ASTs, compile actual JSON expressions, and use generic
native socket, channel and crypto primitives. Refuse changed control flow.
The candidate admits one authenticated primary SLH; client relay is disabled.
"""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import re
from . import translate
from .declaration_order import reorder_bend_declarations
from .listener_lowering import bend_string, function, require, PACKAGE

# AST fingerprints bind this restricted lowering to reviewed control/effect
# shapes. Comments/formatting do not affect admission. No application import.
RELAY = {
    '_frame':'3a54641b3f844c53c17f3dbc547145a1771042a384c5676be768a0647eb94344',
    'main':'d79d5ea15dadaddce25c7360bcc5b2669586fad1550aa6da2d516b51b2646cf9',
    '__init__':'c15bac51992547a61d24754c82a632b79701baa39e5be2bec2aca7eca30d8c7f',
    'tunnel_ready':'0b64e936a1ce6095f9c39614e41d36c9865a19e917e96144bd4c2b3ef7e29f35',
    '_handle_slh':'b6e0a92a14c9f11dca21e1f4b87f1aeacebb3a2695cfbbf96cc24086911fbd5f',
    '_handle_client':'2b6dfbb7647f28bbd675f2346e2bfc8cca855c8461c31618f34bfdc7a01e4564',
    'handler':'c62a30be3caa2c852479ef0f0f7e896f76f1ce51c21eff327aa8bb88d69eb960',
    'process_request':'71b6b71fd26561932983feadc424fe36ca1cbb40fac7d19447842d518a2bedd2',
    'serve':'276cbf062eacf4853df9144630c5ed860b6220d65e91c44f1fe6e6be862fc4f1',
}
AUTH = {
    'canonical_bytes':'e695a3f6e2a06871ead665c9db2b23d4b908c9a8ae08c641a02c5250a60bf4e2',
    'b64decode':'960d0154a24e8d9d8e12fba5e545f6eb748cc33170c706725264958a1ad2722b',
    'load_public_key':'3e43dfb71d5d3a80b27b00cb236f6aca714a839612a4efaadbba734c5a01d7ab',
    'verify_object':'aa5d7562286037916d594ac24d54fff3f2e3484a8d54830b5c7578e82eabedaf',
}

def fingerprint(node):
    # ast.dump defaults vary by Python version (notably 3.13 show_empty).
    # Preserve all semantic fields; omit only the empty generic type list
    # introduced in Python 3.12, which is absent on earlier versions.
    def canonical(n):
        if isinstance(n, ast.AST):
            return [type(n).__name__, {k: canonical(v) for k,v in ast.iter_fields(n)
                    if not (k == 'type_params' and not v)}]
        if isinstance(n, list):
            return [canonical(v) for v in n]
        return n
    return hashlib.sha256(json.dumps(canonical(node), sort_keys=True, separators=(',',':')).encode()).hexdigest()


def check(nodes, expected):
    f = {}
    for name, digest in expected.items():
        node = function(nodes, name)
        actual = fingerprint(node)
        require(actual == digest, f'bound lowering unsupported control flow: {name}')
        f[name] = node
    return f


def assignment(node, name):
    rows = [n.value for n in ast.walk(node) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    require(len(rows)==1, f'bound assignment {name}')
    return rows[0]


def policy(relay_source, auth_source):
    tree = ast.parse(relay_source)
    cls = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=='SEHRelay']
    require(len(cls)==1, 'bound relay class')
    f = check([*tree.body, *cls[0].body], RELAY)
    check(ast.parse(auth_source).body, AUTH)
    slh = f['_handle_slh']
    statement = assignment(slh, 'statement')
    nonce = assignment(slh, 'nonce')
    sends = [n for n in ast.walk(slh) if isinstance(n,ast.Call) and ast.unparse(n.func)=='ws.send']
    require(len(sends)==2, 'bound challenge/accept sends')
    challenge, accepted = [n.args[0] for n in sends]
    # Replace the primary challenge's statement reference by the real AST.
    class Statement(ast.NodeTransformer):
        def visit_Name(self,n):
            return statement if n.id=='statement' else n
    challenge = Statement().visit(challenge)
    body = assignment(f['process_request'], 'body')
    # Compile JSON values with runtime parameters, not host-evaluated samples.
    program = ('import json\n'+ast.unparse(f['_frame'])+'\n'
        +'def health(ready: bool) -> str:\n    return '+ast.unparse(body)+'\n'
        +'def statement(nonce: str) -> str:\n    return _frame('+ast.unparse(statement)+')\n'
        +'def challenge(nonce: str) -> str:\n    return '+ast.unparse(challenge)+'\n'
        +'def accepted() -> str:\n    return '+ast.unparse(accepted)+'\n'
        +"def main() -> str:\n    return json.dumps(json.loads('{}'))\n")
    # Include the certified decoder runtime; proof input is decoded at runtime.
    compiled = translate(program,'bound_primary_policy',show_return=True)
    entry='def main() -> IO(Unit):\n  PyHeap.finish_io(py3_main(PyHeap.empty()), True{})'
    require(compiled.count(entry)==1, 'bound JSON entry ABI')
    return compiled.replace(entry,''), ast.literal_eval(nonce.args[0])


def lower_bound_listener(relay_source, auth_source, *, port=18081):
    require(type(port) is int and 1024<=port<=65535,'bound lab port')
    compiled, nonce_size = policy(relay_source,auth_source)
    directory = Path(__file__).parent
    # Reuse the certified transport's pure wire utilities, not its policy or
    # sequential accept loop. Verify every requested definition is present.
    src=(directory/'listener_transport.bend').read_text(encoding='utf-8')
    pattern=r'(?m)^(?:@unsafe )?def ([\w.]+)\('
    matches=list(re.finditer(pattern,src))
    utilities={m[1]:src[m.start():matches[i+1].start() if i+1<len(matches) else len(src)]
               for i,m in enumerate(matches)}
    names=('bytes','header','header_empty','header_hit','path','path_valid','http',
           'write_done','write','ws_accept','has_upgrade','upgrade_token')
    require(all('LabNet.'+n in utilities for n in names),'certified transport utility missing')
    shared='\n'.join(utilities['LabNet.'+n] for n in names)
    shell=(directory/'bound_transport.bend').read_text(encoding='utf-8')
    shell=shell.replace('LAB_PORT',str(port)).replace('LAB_NONCE_SIZE',bend_string(str(nonce_size)))
    imports=f'import {PACKAGE}/sha1.bend as LabSHA\nimport {PACKAGE}/b64.bend as LabB64\n'
    require(compiled.startswith('import Base\n'),'bound compiler prelude')
    return reorder_bend_declarations(compiled.replace('import Base\n','import Base\n'+imports,1)+'\n'+shared+'\n'+shell)
