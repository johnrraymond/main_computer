"""Link reviewed, closed library exports without executing module initialization.

A bundle is an explicit dependency contract, not a claim that an entire module
is importable under Bend. The oracle receives the original source fragments;
the compiler receives the same functions with read-only globals lowered and
explicit parameter-domain specialization. No production module is imported.
"""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
import tokenize


@dataclass(frozen=True)
class Fragment:
    path: str
    name: str
    sha256: str
    string_parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fixture:
    args: tuple = ()
    kwargs: tuple[tuple[str, object], ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class Export:
    fragments: tuple[Fragment, ...]
    result: str
    fixtures: tuple[Fixture, ...]
    json_tree: bool = False
    json_decode: bool = False

    @property
    def name(self):
        return self.fragments[-1].name


BRIDGE = 'main_computer/hub_credit_bridge_completion.py'
HEX_SET = Fragment(BRIDGE, '_HEX_CHARS', '7d0492a117b42ac4cdfdc2a0b1f630d763094203f903e9c3bc5b077f76a5e634')
BRIDGE_HEX = Fragment(BRIDGE, '_is_hex', '714acfc1cbcdc982a5089aa510600febe2d9f73dbb82b00e60bbc1a8103f880f')
BYTES32 = Fragment(BRIDGE, 'normalize_bytes32', 'ccfab4e0c066a3bb25734b4f87ce29f130b1f46e0d04aa83eddf6dcea038e13d', ('value',))
EVM = Fragment(BRIDGE, 'normalize_evm_address', '2b22d1144f11d3a046113116ae8a751887ec92e9dd2a28bde56ee3db40bb99aa', ('value',))
STRIP = Fragment(BRIDGE, '_strip_0x', '57afa2576056bfd83d810a19e3d66fbe751bae7d404610cf31c892ea1c6c2531')
ENCODE = Fragment(BRIDGE, '_encode_bytes32', '9cca673c169f92ffc5611675cc20cb2002d821f516ccb7bb6e7f3873ab50e23f')
ADDRESS = Fragment('main_computer/hub_credit_models.py', 'normalize_address', '0541063f4d1ba590787d7107344bdc1bee6a7a4033dc125d00427a089ecba646')
INDEX_HEX = Fragment('main_computer/hub_credit_indexer.py', '_is_hex', '97736b7e07435c3bd15095ed05f47c9ca2ed3dfc9e26b6f53f8077982b2bb074')
CONFIG = 'main_computer/contract_config.py'
ALIASES = Fragment(CONFIG, 'CONTRACT_KEY_ALIASES', '2428c9b82a9ad3b92c72348983cc10a92745ab85349879219354762b236a4bbf')
KEY = Fragment(CONFIG, 'canonical_contract_key', 'c206397cc25066060c1c4b2f70a7a1d762bd03b9dfaf46900f0054a93c9866a0')
WEBSITE = Fragment('deploy/local-platform/site-server/app.py', '_normalize_blog_content_runtime', '027ca8d25ec92db27fbe6a410ca6e0cdb215ae4ac2d65052baa5d99b356eda9e', ('value',))
SEH_FRAME = Fragment('main_computer/seh/relay.py', '_frame', '1605f99d2b01b87f7e07db98a1a2eb3d490a88c0717c14aead055999adcf4daf')
MSK = Fragment('main_computer/stable_hub_msk.py', '_multisession_key_id', '4b67d3f3e31804fbcfc4e8da6f6985353d40407b7bb2eb1a3b69efd8169cb70c')


def _hex_fixtures():
    return tuple(Fixture((text,), (('chars', n),)) for text, n in (
        ('0x', 0), ('0xABcd', 4), (' 0x1f ', 2), ('0Xab', 2),
        ('0xaz', 2), ('0x123', 2), ('', 0), ('0x' + 'f' * 40, 40),
        ('0x' + 'f' * 39 + 'g', 40), ('0x' + 'aB' * 32, 64),
    ))


# These are bounded ASCII-string API contracts. The original Python functions
# may accept other objects and Unicode; those domains are NOT certified here.
EXPORTS = {
    'bridge_hex_validation': Export((HEX_SET, BRIDGE_HEX), 'bool', _hex_fixtures()),
    'indexer_hex_validation': Export((INDEX_HEX,), 'bool', _hex_fixtures()),
    'bridge_normalize_bytes32': Export((HEX_SET, BRIDGE_HEX, BYTES32), 'str', (
        Fixture(('0x' + 'aB' * 32,)), Fixture((' 0X' + 'F' * 64 + ' ',)),
        Fixture(('0x' + '0' * 64,)), Fixture(('0x1',), error='ValueError'),
        Fixture(('0x' + 'g' * 64,), error='ValueError'),
        Fixture(('',), (('field_name', 'fixture_id'),), 'ValueError'),
    )),
    'bridge_normalize_evm_address': Export((HEX_SET, ADDRESS, BRIDGE_HEX, EVM), 'str', (
        Fixture(('0x' + 'AB' * 20,)), Fixture((' 0X' + 'F' * 40 + ' ',)),
        Fixture(('0x' + '0' * 40,)), Fixture(('0x1',), error='ValueError'),
        Fixture(('0x' + 'g' * 40,), error='ValueError'),
        Fixture(('not-an-address',), (('field_name', 'wallet'),), 'ValueError'),
    )),
    'bridge_encode_bytes32': Export((HEX_SET, BRIDGE_HEX, BYTES32, STRIP, ENCODE), 'str', (
        Fixture(('0x' + 'Ab' * 32,)), Fixture((' 0X' + 'C' * 64 + ' ',)),
        Fixture(('0x' + '0' * 64,)), Fixture(('bad',), error='ValueError'),
    )),
    'canonical_contract_key': Export((ALIASES, KEY), 'str', tuple(Fixture((key,)) for key in (
        'alpha_beta_lockout', 'alpha-beta-lockout', 'xlag_bridge_reserve',
        ' HubCreditBridgeEscrow ', 'hub_credit_bridge_escrow', 'other-key', '',
    ))),
    'website_blog_runtime': Export((WEBSITE,), 'str', tuple(Fixture((text,)) for text in (
        '', 'deployed', ' DEPLOYED ', 'Directus', 'other', '  ',
    ))),
    # SEH wire-format contract: sorted compact JSON, including mixed nested
    # data, arbitrary-size integers and Python's ensure_ascii escape policy.
    'seh_frame_json': Export((SEH_FRAME,), 'str', tuple(Fixture((payload,)) for payload in (
        {},
        {'type': 'seh.auth.accepted'},
        {'type': 'seh.auth.challenge', 'statement': {'version': 2, 'action': 'seh.slh.connect', 'nonce': 'fixture-nonce'}},
        {'id': 'worker-test', 'op': 'data', 'data': 'AAEC'},
        {'service': 'main_computer.seh', 'live': True, 'ready': False, 'active_clients': 0},
        {'z': [None, True, False, -42, 'text', {'b': 1, 'a': []}], 'a': {}},
        {'positive': 2**256 - 1, 'negative': -(2**128)},
        {'escapes': '\x00\b\t\n\f\r\x1f\x7f"\\/'},
        {'unicode': 'é中😀', 'é': 'accent', 'z': 'last ASCII', '😀': 'astral'},
        {'a': 1, 'aa': 2, 'A': 3, '': 4, 'ab': 5},
        {'x': [[], {}, [1, 2, 3]], 'none': None},
        {'first': {'b': 2, 'a': 1}, 'second': {'a': 1, 'b': 2}},
    )), json_tree=True),
    # Wire input is raw text, never json.loads evaluated by the translator.
    # The original _frame implementation remains pinned and is called on the
    # decoded object by both runtimes. These are codec tests, not relay tests.
    'seh_frame_json_decode': Export((SEH_FRAME,), 'str', tuple(Fixture((raw,)) for raw in (
        '{}',
        '{ "type" : "seh.auth.proof", "signature": "fixture-signature" }',
        '{"z":[null,true,false,-42,"text",{"b":1,"a":[]}],"a":{}}',
        '{"n":' + str(2**256 - 1) + ',"negative":' + str(-(2**128)) + '}',
        '{"x":1,"x":2,"a":3}',
        r'{"a":1,"\u0061":2,"b":3}',
        r'{"unicode":"\u00e9\u4e2d\ud83d\ude00","\u00e9":"accent"}',
        '{"unicode":"é中😀"}',
        r'{"escapes":"\u0000\b\t\n\f\r\u001f\u007f\"\\\/"}',
        '{"a":[[],{},[1,2,3]],"none":null}',
        '{"negative_zero":-0,"zero":0,"negative":-10}',
        ' \t\r\n{"z":true,"a":false}\n',
        '{"first":{"b":2,"a":1},"second":{"a":1,"b":2}}',
        '[]', '[1,true,null,"hello"]', '"text"', '42', 'false', 'null',
    )) + tuple(Fixture((raw,), error='JSONDecodeError') for raw in (
        '', '{', '{"a":}', '{"a":1,}', '[1,]', '{"a":01}',
        '{"a":1} {"b":2}', r'{"a":"\q"}', '{"a":"line\nbreak"}',
    )), json_tree=True, json_decode=True),
    'multisession_key_id': Export((MSK,), 'str', (
        Fixture(kwargs=(('user_slug', 'user-a'), ('hub_slug', 'hub-b'))),
        Fixture(kwargs=(('hub_slug', 'HUB_2'), ('user_slug', 'user_1'))),
    )),
}


def _read_fragment(root: Path, fragment: Fragment):
    path = (root / fragment.path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('fragment path escapes repository')
    with tokenize.open(path) as stream:
        source = stream.read()
    tree = ast.parse(source, filename=fragment.path)
    matches = [n for n in tree.body if (
        isinstance(n, ast.FunctionDef) and n.name == fragment.name or
        isinstance(n, ast.Assign) and len(n.targets) == 1 and
        isinstance(n.targets[0], ast.Name) and n.targets[0].id == fragment.name)]
    if len(matches) != 1:
        raise ValueError(f'export binding changed: {fragment.path}:{fragment.name}')
    node = matches[0]
    text = ast.get_source_segment(source, node)
    if text is None or hashlib.sha256(text.encode()).hexdigest() != fragment.sha256:
        raise ValueError(f'export source changed; review required: {fragment.path}:{fragment.name}')
    if isinstance(node, ast.FunctionDef) and node.decorator_list:
        raise ValueError('decorated export needs actual module initialization')
    return node, text, tree


def _constant(node):
    """Only bounded literal char sets and string maps are supported here."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
            node.func.id == 'set' and len(node.args) == 1 and not node.keywords and
            isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is str):
        chars = sorted(set(node.args[0].value))
        if not chars or len(chars) > 256:
            raise ValueError('character set outside supported domain')
        return 'chars', ast.Set(elts=[ast.Constant(c) for c in chars])
    value = ast.literal_eval(node)
    if isinstance(value, dict) and all(type(k) is type(v) is str for k, v in value.items()):
        if len(value) > 256:
            raise ValueError('string map outside supported domain')
        return 'map', ast.parse(repr(value), mode='eval').body
    if type(value) in (int, str, bool):
        return 'scalar', ast.Constant(value)
    raise ValueError('unsupported module initializer in export bundle')


def _lower_globals(function: ast.FunctionDef, constants: dict):
    """Lower only nonescaping, read-only references to reviewed constants."""
    import copy
    parent = {id(child): owner for owner in ast.walk(function) for child in ast.iter_child_nodes(owner)}
    local_maps = set()
    for node in ast.walk(function):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise ValueError('export needs shared module state')
        if isinstance(node, ast.arg) and node.arg in constants:
            raise ValueError('constant is shadowed by a parameter')
        if not isinstance(node, ast.Name) or node.id not in constants:
            continue
        if not isinstance(node.ctx, ast.Load):
            raise ValueError('constant is rebound')
        kind, value = constants[node.id]
        owner = parent[id(node)]
        if kind == 'chars':
            if not (isinstance(owner, ast.Compare) and node in owner.comparators and
                    len(owner.ops) == 1 and isinstance(owner.ops[0], (ast.In, ast.NotIn))):
                raise ValueError('character set escapes its read-only membership contract')
        elif kind == 'map':
            call = parent.get(id(owner))
            if not (isinstance(owner, ast.Attribute) and owner.value is node and owner.attr == 'get' and
                    isinstance(call, ast.Call) and call.func is owner and not call.keywords and len(call.args) == 2):
                raise ValueError('string map escapes its read-only lookup contract')
            local_maps.add(node.id)
    class Lower(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id in constants and constants[node.id][0] != 'map':
                return ast.copy_location(copy.deepcopy(constants[node.id][1]), node)
            return node
    function = Lower().visit(function)
    # A fresh private copy is equivalent for read-only string lookup, because
    # neither the map nor its identity can escape. Do not apply to mutable data.
    assignments = [ast.Assign(targets=[ast.Name(id=n, ctx=ast.Store())],
                              value=copy.deepcopy(constants[n][1])) for n in sorted(local_maps)]
    index = int(bool(function.body and isinstance(function.body[0], ast.Expr)
                     and isinstance(function.body[0].value, ast.Constant)
                     and isinstance(function.body[0].value.value, str)))
    function.body[index:index] = assignments
    return function


def _validate_json_fixture(value, depth=0):
    if depth > 32:
        raise ValueError('JSON fixture depth limit or cycle')
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        if len(value) > 256 or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError('JSON fixture string outside native domain')
        return
    if type(value) is list and len(value) <= 128:
        for item in value:
            _validate_json_fixture(item, depth+1)
        return
    if type(value) is dict and len(value) <= 128:
        for k, v in value.items():
            if type(k) is not str:
                raise ValueError('JSON fixture key must be str')
            _validate_json_fixture(k, depth+1)
            _validate_json_fixture(v, depth+1)
        return
    raise ValueError('unsupported JSON fixture value')


def programs(root: Path, export: Export, fixture: Fixture) -> tuple[str, str]:
    """Return independent original-fragment oracle and lowered compiler input."""
    constants = {}
    nodes = []
    original = ['from __future__ import annotations']
    if export.json_tree:
        original.append('import json')
    seen = set()
    origins = {f.name: f.path for f in export.fragments}
    # The chosen functions use their original import names. This linker rejects
    # collisions instead of silently resolving ambiguous cross-module symbols.
    for fragment in export.fragments:
        if fragment.name in seen or fragment.name == 'main':
            raise ValueError('duplicate export binding')
        seen.add(fragment.name)
        node, text, module = _read_fragment(root, fragment)
        # Every cross-module reference to another bundled symbol must have
        # the real, matching explicit import in its source module. Do not
        # infer the import binding merely from matching function names.
        for ref in {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}:
            if ref not in origins or origins[ref] == fragment.path:
                continue
            expected_module = origins[ref].removesuffix('.py').replace('/', '.')
            imports = [i for i in module.body if isinstance(i, ast.ImportFrom)
                       and any((a.asname or a.name) == ref for a in i.names)]
            if len(imports) != 1 or imports[0].level or imports[0].module != expected_module or not any(
                    a.name == ref and (a.asname or a.name) == ref for a in imports[0].names):
                raise ValueError(f'cross-module import changed: {fragment.path}:{ref}')
        if export.json_tree:
            bindings = [a for stmt in module.body if isinstance(stmt, ast.Import)
                        for a in stmt.names if (a.asname or a.name) == 'json']
            if len(bindings) != 1 or bindings[0].name != 'json':
                raise ValueError('reviewed JSON import binding changed')
        original.append(text)
        if isinstance(node, ast.Assign):
            constants[fragment.name] = _constant(node.value)
            continue
        for param in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if param.arg in fragment.string_parameters:
                if not isinstance(param.annotation, ast.Name) or param.annotation.id not in ('Any', 'object'):
                    raise ValueError('specialization annotation changed')
                param.annotation = ast.Name(id='str', ctx=ast.Load())
        if export.json_decode:
            # The reviewed _frame body calls dumps without requiring a dict.
            # A decoded root can be any JSON scalar/container. Widen only this
            # annotation; the original oracle retains the unmodified source.
            if fragment != SEH_FRAME or len(node.args.args) != 1 or not isinstance(node.args.args[0].annotation, ast.Name) or node.args.args[0].annotation.id != 'dict':
                raise ValueError('reviewed decoder serializer signature changed')
            node.args.args[0].annotation = ast.Name(id='object', ctx=ast.Load())
        nodes.append(node)
    # Validate data before constructing source. A JSON fixture is an acyclic
    # tree of built-in values, never a custom object with an executable repr.
    values = (*fixture.args, *(v for _, v in fixture.kwargs))
    if export.json_tree:
        for value in values:
            _validate_json_fixture(value)
    else:
        if not all(type(x) in (str, int, bool) for x in values):
            raise ValueError('fixture outside supported primitive domain')
        if any(type(x) is str and not x.isascii() for x in values):
            raise ValueError('export fixtures require ASCII input')
    args = [repr(a) for a in fixture.args]
    for key, value in fixture.kwargs:
        if not key.isidentifier():
            raise ValueError('invalid fixture keyword')
        args.append(f'{key}={value!r}')
    main = f'def main() -> {export.result}:\n    return {export.name}({", ".join(args)})\n'
    if export.json_decode:
        if len(fixture.args) != 1 or type(fixture.args[0]) is not str or fixture.kwargs:
            raise ValueError('JSON decoding fixture requires one raw string')
        main = (f'def main() -> {export.result}:\n'
                f'    raw = {fixture.args[0]!r}\n'
                f'    return {export.name}(json.loads(raw))\n')
    native = [ast.unparse(ast.fix_missing_locations(_lower_globals(n, constants))) for n in nodes]
    return '\n\n'.join([*original, main]), '\n\n'.join([*(['import json'] if export.json_tree else []), *native, main])
