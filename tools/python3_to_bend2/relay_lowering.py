"""Restricted primary relay lowering, extending the certified bound program.
The source-linked control flow is deliberately guarded; not a general linker.
"""
from pathlib import Path
import ast
import json
import hashlib
import re
from .bound_lowering import lower_bound_listener
from .listener_lowering import require, bend_string, function
from .declaration_order import reorder_bend_declarations

WRITER_SHA = '724bef6def9f25e6d9edb1960aaa2c2d03b800d90edac5bc3d0f94d154e5b6c1'



def frame_lowering(source):
    """Compile the three actual outgoing dictionary expressions, not fixture results.

    This narrow lowering accepts only constant strings and the reviewed dynamic
    id/purpose/base64 fields, whose runtime inputs are URL-safe/ASCII.
    """
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'SEHRelay')
    method = function(cls.body, '_handle_client')
    defs = {}
    for call in ast.walk(method):
        if not isinstance(call, ast.Call) or ast.unparse(call.func) != 'self._send_slh':
            continue
        require(len(call.args) == 1 and isinstance(call.args[0], ast.Dict), 'unsupported relay frame')
        fields = {ast.literal_eval(k): v for k, v in zip(call.args[0].keys, call.args[0].values)}
        op = ast.literal_eval(fields['op'])
        params = {'open':'id: String,purpose: String', 'data':'id: String,bytes: List<&2,U32>', 'close':'id: String'}
        require(op in params and op not in defs, 'unsupported relay operation')
        parts = [bend_string('{')]
        for i,(key,value) in enumerate(sorted(fields.items())):
            parts.append(bend_string((',' if i else '') + json.dumps(key) + ':'))
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(bend_string(json.dumps(value.value)))
            else:
                expr = {'sid':'id','purpose':'purpose',"base64.b64encode(raw).decode('ascii')":'LabB64.encode(bytes)'}.get(ast.unparse(value))
                require(expr is not None, 'unsupported dynamic relay field')
                parts.extend([bend_string('"'), expr, bend_string('"')])
        parts.append(bend_string('}'))
        defs[op] = f'def RelayFrame.{op}({params[op]}) -> String:\n  ' + ' ++ '.join(parts)
    require(set(defs)=={'open','data','close'}, 'missing relay frame operation')
    return '\n\n'.join(defs[k] for k in sorted(defs))


def lower_relay(relay_source, auth_source, writer_source, *, port=18081):
    # Enforce the reviewed source including global admission limits and writer
    # policy. Normalize newline convention only (Windows checkout support).
    require(hashlib.sha256(relay_source.encode()).hexdigest() == '0f103055e221477110b2ae9162236561450ee18efe46c541cf580f0c36a58d12', 'relay source drift')
    require(hashlib.sha256(writer_source.encode()).hexdigest() == WRITER_SHA, 'relay writer source drift')
    base = lower_bound_listener(relay_source, auth_source, port=port)
    # Retain the tested handshake, authentication and TCP frame machinery.
    # Replace the primary tunnel lifetime and the bool cell with a session cell.
    base = base.replace('Chan(Bool)', 'Chan(Maybe<&2,RelaySession>)')
    base = base.replace('Chan.new(Bool,1)', 'Chan.new(Maybe<&2,RelaySession>,1)')
    base = base.replace('sent : Result<&1,&1,Bool,Unit> <- Chan.send(Bool,state,False{})', 'sent : Result<&1,&1,Maybe<&2,RelaySession>,Unit> <- Chan.send(Maybe<&2,RelaySession>,state,None{})')
    # Client frame sizes are bounded below the certified JSON decoder limit.
    # Also enforce bounded socket sends rather than an indefinite blocking send.
    overrides = (Path(__file__).parent / 'relay_transport.bend').read_text(encoding='utf-8')
    names = set(re.findall(r'(?m)^(?:@unsafe )?def ([\w.]+)\(', overrides))
    old = {'LabState.ready_value','LabState.claim','LabState.claim_value','LabState.release',
           'LabBound.tunnel','LabBound.tunnel_pair','LabBound.tunnel_packet','LabBound.tunnel_sent',
           'LabBound.tunnel_continue','LabBound.admitted','LabBound.client_ready','LabBound.ws_client'}
    heads = list(re.finditer(r'(?m)^(?:@unsafe )?def ([\w.]+)\(|^type \w+ is Data:', base))
    keep = [base[:heads[0].start()]]
    for i, m in enumerate(heads):
        if m[1] not in names | old:
            keep.append(base[m.start():heads[i+1].start() if i+1<len(heads) else len(base)])
    return reorder_bend_declarations(''.join(keep)+'\n'+frame_lowering(relay_source)+'\n'+overrides)
