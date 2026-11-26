"""Read-only compatibility inventory for repository-owned Coolify services.

Service inventory is derived from the checked-in deployment contracts, not a
live Coolify API. No deployment code is imported or executed. Module emission
and isolated function execution are distinct from service certification.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import sys
import tempfile
import tokenize

from . import translate
from .library_exports import EXPORTS, programs
from .json_decode import REJECTION as JSON_DECODE_REJECTION
from .native_execution import classify_proof_check, preflight, run_standalone

HUB_CONTRACT = 'tools/hub_control/common/deployment.py'
SEH_CONTRACT = 'tools/seh_control/git_deployment.py'
SITE_ENTRY = 'deploy/local-platform/site-server/app.py'
SITE_BUILD = 'deploy/local-platform/site-server/Dockerfile'


@dataclass(frozen=True)
class Service:
    name: str
    modules: tuple[str, ...]
    required: tuple[str, ...]
    missing: tuple[str, ...]
    external: tuple[str, ...]
    dynamic_import_sites: tuple[str, ...]


def read_python(path: Path) -> str:
    with tokenize.open(path) as stream:
        return stream.read()


def literal_setting(root: Path, path: str, name: str):
    tree = ast.parse(read_python(root / path), filename=path)
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
            return ast.literal_eval(stmt.value)
    raise ValueError(f'missing deployment setting {path}:{name}')


def module_index(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted((root / 'main_computer').rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        name = relative[:-3].replace('/', '.').removesuffix('.__init__')
        if name in result:
            raise ValueError(f'ambiguous module {name}')
        result[name] = relative
    return result


def imports(tree: ast.AST, package: str, index: dict[str, str]):
    """Include function-local and conditional imports conservatively."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split('.') if package else []
                if node.level > len(parts):
                    yield '<invalid-relative-import>'
                    continue
                base = '.'.join(parts[:len(parts) - node.level + 1] + ([node.module] if node.module else []))
            else:
                base = node.module or ''
            if base:
                yield base
                for alias in node.names:
                    child = base + '.' + alias.name
                    if child in index:
                        yield child


def dynamic_sites(tree: ast.AST, path: str) -> set[str]:
    return {f'{path}:{n.lineno}' for n in ast.walk(tree) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id in {'__import__', 'import_module'})
                 or (isinstance(n.func, ast.Attribute) and n.func.attr in {'import_module', 'spec_from_file_location'}))}


def discover(root: Path) -> tuple[Service, ...]:
    index = module_index(root)
    hub_entries = literal_setting(root, HUB_CONTRACT, 'HUB_GIT_ENTRY_MODULES')
    hub_required = tuple(literal_setting(root, HUB_CONTRACT, 'HUB_GIT_STATIC_PATHS'))
    seh_entries = literal_setting(root, SEH_CONTRACT, 'SEH_ENTRY_MODULES')
    seh_required = tuple(literal_setting(root, SEH_CONTRACT, 'SOURCES'))
    # The SEH Dockerfile creates these initializers rather than copying the
    # full Main Computer package. Never pull in the local SLH or router.
    generated = {'main_computer/__init__.py', 'main_computer/slh/__init__.py'}

    def closure(name, entries, required, generated_paths=frozenset()):
        paths, missing, external, dynamic = set(), set(), set(), set()
        seen = set()
        queue = list(entries)
        for rel in required:
            if not (root / rel).is_file():
                missing.add(rel)
            elif rel.endswith('.py'):
                paths.add(rel)
                tree = ast.parse(read_python(root / rel), filename=rel)
                module = rel[:-3].replace('/', '.').removesuffix('.__init__')
                package = module if rel.endswith('/__init__.py') else module.rpartition('.')[0]
                queue.extend(n for n in imports(tree, package, index)
                             if n.startswith('main_computer.'))
        while queue:
            module = queue.pop()
            if module in seen:
                continue
            seen.add(module)
            if module not in index:
                missing.add(module.replace('.', '/') + '.py')
                continue
            rel = index[module]
            if rel in generated_paths:
                continue
            paths.add(rel)
            parent = Path(rel).parent
            while parent != Path('.'):
                initializer = (parent / '__init__.py').as_posix()
                if initializer not in generated_paths and (root / initializer).is_file():
                    paths.add(initializer)
                parent = parent.parent
            tree = ast.parse(read_python(root / rel), filename=rel)
            package = module if rel.endswith('/__init__.py') else module.rpartition('.')[0]
            dynamic.update(dynamic_sites(tree, rel))
            for imported in imports(tree, package, index):
                if imported == 'main_computer' or imported.startswith('main_computer.'):
                    queue.append(imported)
                else:
                    external.add(imported.split('.')[0])
        # Inspect launchers and package initializers as well. The root package
        # lazy __getattr__ is a separate conditional surface, not a reason to
        # include the entire desktop router in the Hub image closure.
        for rel in sorted(paths):
            tree = ast.parse(read_python(root / rel), filename=rel)
            dynamic.update(dynamic_sites(tree, rel))
            module = rel[:-3].replace('/', '.').removesuffix('.__init__')
            package = module if rel.endswith('/__init__.py') else module.rpartition('.')[0]
            for imported in imports(tree, package, index):
                if not imported.startswith('main_computer'):
                    external.add(imported.split('.')[0])
        return Service(name, tuple(sorted(paths)), tuple(required), tuple(sorted(missing)),
                       tuple(sorted(external - {'__future__', 'typing'})), tuple(sorted(dynamic)))

    hub = closure('stable_hub', hub_entries, hub_required)
    seh = closure('seh', seh_entries, seh_required, generated)
    site_copies = tuple(sorted(p.relative_to(root).as_posix() for p in
                               (root / 'runtime/websites').glob('*/.main-computer/runtime/app.py')))
    site = closure('website_server_local_or_remote_if_used', (), (SITE_BUILD, SITE_ENTRY, *site_copies))
    return hub, seh, site


# Actual top-level functions, extracted verbatim from deployment source.
# These are isolated function contracts, not replacements for the full module.
# Address lowercasing fixtures intentionally use the EVM ASCII domain.
FUNCTION_CASES = {
    'hub_url': ('main_computer/hub_networks.py', '_default_hub_url', ('str', 'int'), 'str',
                [('example.test', 8790), ('127.0.0.1', 1), ('localhost', 65535)]),
    'bridge_prefix': ('main_computer/hub_credit_bridge_completion.py', '_strip_0x', ('str',), 'str',
                      [('0xABCD',), ('0XABCD',), ('0x',), ('',), ('0',), ('abc',), ('é0x',)]),
    'bridge_address_word': ('main_computer/hub_credit_bridge_completion.py', '_decode_word_address', ('str',), 'str',
                            [('0' * 24 + 'ABCDEF0123' * 4,), ('F' * 64,), ('a' * 64,)]),
    'ledger_normalize_address_ascii': ('main_computer/hub_credit_models.py', 'normalize_address', ('str',), 'str',
                                       [(' 0xABCD ',), ('ABCDEF',), ('',), ('  ',), ('0XAB',)]),
}


# Pin reviewed, side-effect-free function bodies before executing an oracle.
# A changed body is a new review task, not permission to execute arbitrary code.
REVIEWED_FUNCTIONS = {
    name: digest for name, digest in (
        ('_default_hub_url', '57cfe01eae189d1340ae8a62224a5eada54a0923e2852313cbe8ddb400636610'),
        ('_strip_0x', '57afa2576056bfd83d810a19e3d66fbe751bae7d404610cf31c892ea1c6c2531'),
        ('_decode_word_address', '4aba7ce1fb1db444e1ed7351d57a1eef68fe1dbe787e314c25fe5aaaef9930d7'),
        ('normalize_address', '0541063f4d1ba590787d7107344bdc1bee6a7a4033dc125d00427a089ecba646'),
    )
}


def function_program(root: Path, case: str, args: tuple) -> str:
    rel, name, types, result, _ = FUNCTION_CASES[case]
    source = read_python(root / rel)
    tree = ast.parse(source, filename=rel)
    matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(matches) != 1 or matches[0].decorator_list:
        raise ValueError(f'function contract changed: {rel}:{name}')
    node = matches[0]
    fragment = ast.get_source_segment(source, node)
    if fragment is None:
        raise ValueError(f'could not extract {rel}:{name}')
    digest = hashlib.sha256(fragment.encode()).hexdigest()
    if REVIEWED_FUNCTIONS[name] != digest:
        raise ValueError(f'function body changed; review required before oracle execution: {rel}:{name}')
    if tuple(ast.unparse(a.annotation) if a.annotation else '' for a in node.args.args) != types or ast.unparse(node.returns) != result:
        raise ValueError(f'function signature changed: {rel}:{name}')
    # No substitute algorithm, specialized AST rewrite or expected answer.
    # The Python oracle and Bend compiler receive exactly the same function.
    return fragment + f'\n\ndef main() -> {result}:\n    return {name}({", ".join(map(repr, args))})\n'


def audit_modules(root: Path, services: tuple[Service, ...]):
    for rel in sorted({p for service in services for p in service.modules}):
        try:
            translate(read_python(root / rel), rel)
        except Exception as exc:
            yield rel, 'REJECTED', str(exc).replace('\n', ' ')[:300]
        else:
            yield rel, 'EMITTED', 'standalone source only; no service execution'


def audit_functions(root: Path, *, native=False, bend='bend', wsl=False):
    readiness = preflight(bend, wsl=wsl) if native else None
    with tempfile.TemporaryDirectory(prefix='bend-coolify-') as directory:
        folder = Path(directory)
        for case, (_, _, _, _, fixtures) in FUNCTION_CASES.items():
            for i, args in enumerate(fixtures):
                try:
                    source = function_program(root, case, args)
                    emitted = translate(source, case, show_return=True)
                except Exception as exc:
                    yield case, i, 'REJECTED', str(exc)
                    continue
                if not native:
                    yield case, i, 'EMITTED', 'native not requested'
                    continue
                if not readiness.ready:
                    yield case, i, 'PREFLIGHT_BLOCKED', readiness.detail
                    continue
                try:
                    py = folder / 'case.py'; py.write_text(source, encoding='utf-8')
                    target = folder / 'case.bend'; target.write_text(emitted, encoding='utf-8', newline='\n')
                    oracle = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c',
                                             'import runpy,sys; print(runpy.run_path(sys.argv[1])["main"]())', str(py)],
                                            capture_output=True, text=True, encoding='utf-8',
                                            timeout=20)
                    if oracle.returncode or oracle.stderr:
                        yield case, i, 'ORACLE_FAILED', oracle.stderr[:1000]
                        continue
                    checked = classify_proof_check(run_standalone(target, bend=bend, wsl=wsl, check_only=True))
                    if not checked.permits_execution:
                        yield case, i, 'CHECK_FAILED', checked.detail
                        continue
                    observed = run_standalone(target, bend=bend, wsl=wsl, timeout=60)
                    if observed.returncode or observed.stdout != oracle.stdout:
                        yield case, i, 'RUN_FAILED', f'rc={observed.returncode} expected={oracle.stdout!r} actual={observed.stdout!r} stderr={observed.stderr[:1000]}'
                    else:
                        yield case, i, 'VERIFIED', f'proof={checked.status}'
                except Exception as exc:
                    yield case, i, 'EXECUTION_ERROR', str(exc)


_ERROR_MARKER = 'LINKED_EXPORT_ORACLE_ERROR\n'


def export_oracle(path: Path, expected_error: str | None):
    snippet = (
        'import runpy,sys,traceback\n'
        'fn = runpy.run_path(sys.argv[1])["main"]\n'
        'try:\n'
        '    value = fn()\n'
        'except Exception as exc:\n'
        '    if type(exc).__name__ != sys.argv[2]: raise\n'
        f'    sys.stderr.write({_ERROR_MARKER!r} + "".join(traceback.format_exception_only(type(exc), exc)))\n'
        '    sys.exit(1)\n'
        'else:\n'
        '    print(value)\n'
    )
    return subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', snippet,
                           str(path), expected_error or ''], capture_output=True,
                          text=True, encoding='utf-8', timeout=20)


def compare_export(oracle, observed, expected_error: str | None, *, json_decode=False):
    if expected_error:
        if (oracle.returncode != 1 or not oracle.stderr.startswith(_ERROR_MARKER)):
            return 'ORACLE_FAILED'
        expected = oracle.stderr.removeprefix(_ERROR_MARKER).removesuffix('\n')
        actual = observed.stderr.removesuffix('\n')
        if json_decode and expected_error == 'JSONDecodeError':
            if not expected.startswith('json.decoder.JSONDecodeError: '):
                return 'ORACLE_FAILED'
            # A decoder syntax failure has no Python offset/line diagnostic.
            # Require a real CPython JSONDecodeError plus the exact native
            # rejection sentinel. Do NOT count unrelated errors as rejection.
            if (observed.returncode == 1 and observed.stdout == oracle.stdout
                    and actual in (JSON_DECODE_REJECTION, 'Error: ' + JSON_DECODE_REJECTION)):
                return 'VERIFIED_REJECTION'
            return 'DIFFERENTIAL_FAILED'
        if (observed.returncode == 1 and observed.stdout == oracle.stdout and
                actual in (expected, 'Error: ' + expected)):
            return 'VERIFIED'
        return 'DIFFERENTIAL_FAILED'
    if oracle.returncode or oracle.stderr:
        return 'ORACLE_FAILED'
    if observed.returncode:
        return 'RUN_FAILED'
    if observed.stdout != oracle.stdout or observed.stderr:
        return 'DIFFERENTIAL_FAILED'
    return 'VERIFIED'


def audit_exports(root: Path, *, native=False, bend='bend', wsl=False, selected=None):
    """Check linked dependency bundles, never import the deployed service."""
    readiness = preflight(bend, wsl=wsl) if native else None
    with tempfile.TemporaryDirectory(prefix='bend-coolify-export-') as directory:
        folder = Path(directory)
        cases = EXPORTS if selected is None else {selected: EXPORTS[selected]}
        for name, export in cases.items():
            for i, fixture in enumerate(export.fixtures):
                try:
                    original, lowered = programs(root, export, fixture)
                    emitted = translate(lowered, name, show_return=True)
                except Exception as exc:
                    yield name, i, 'REJECTED', str(exc)
                    continue
                if not native:
                    yield name, i, 'EMITTED', 'native not requested'
                    continue
                if not readiness.ready:
                    yield name, i, 'PREFLIGHT_BLOCKED', readiness.detail
                    continue
                try:
                    py = folder / 'case.py'
                    py.write_text(original, encoding='utf-8')
                    target = folder / 'case.bend'
                    target.write_text(emitted, encoding='utf-8', newline='\n')
                    oracle = export_oracle(py, fixture.error)
                    # A failed oracle is not a valid execution target.
                    if ((fixture.error and (oracle.returncode != 1 or not oracle.stderr.startswith(_ERROR_MARKER)))
                            or (not fixture.error and (oracle.returncode or oracle.stderr))):
                        yield name, i, 'ORACLE_FAILED', oracle.stderr[:1000]
                        continue
                    checked = classify_proof_check(run_standalone(target, bend=bend, wsl=wsl, check_only=True))
                    if not checked.permits_execution:
                        yield name, i, 'CHECK_FAILED', checked.detail
                        continue
                    observed = run_standalone(target, bend=bend, wsl=wsl, timeout=60)
                    verdict = compare_export(oracle, observed, fixture.error, json_decode=export.json_decode)
                    detail = f'proof={checked.status}'
                    if verdict not in ('VERIFIED', 'VERIFIED_REJECTION'):
                        detail += (f' expected rc={oracle.returncode} out={oracle.stdout!r} '
                                   f'error={oracle.stderr.removeprefix(_ERROR_MARKER)!r}; '
                                   f'actual rc={observed.returncode} out={observed.stdout!r} '
                                   f'error={observed.stderr[:2000]!r}')
                    elif verdict == 'VERIFIED_REJECTION':
                        detail += '; invalid JSON rejected (diagnostic parity not claimed)'
                    elif fixture.error:
                        detail += f'; {fixture.error} matched'
                    yield name, i, verdict, detail
                except Exception as exc:
                    yield name, i, 'EXECUTION_ERROR', str(exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--wsl', action='store_true')
    parser.add_argument('--bend', default='bend')
    parser.add_argument('--functions-only', action='store_true', help='Skip whole-module emission attempts, not the inventory')
    parser.add_argument('--exports-only', action='store_true', help='Run linked export fixtures only')
    parser.add_argument('--export', choices=tuple(EXPORTS), help='Run a single linked export contract')
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    if args.exports_only or args.export:
        totals = Counter()
        for case, i, status, detail in audit_exports(root, native=args.native or args.wsl, bend=args.bend, wsl=args.wsl, selected=args.export):
            totals[status] += 1
            print(f'LINKED_EXPORT {case}[{i}] {status}: {detail}')
        print('LINKED_EXPORT_FIXTURES ' + ' '.join(f'{k}={v}' for k,v in sorted(totals.items())))
        expected = len(EXPORTS[args.export].fixtures) if args.export else sum(len(e.fixtures) for e in EXPORTS.values())
        passed = (totals['VERIFIED'] + totals['VERIFIED_REJECTION'] if args.native or args.wsl else totals['EMITTED']) == expected
        print('LINKED_EXPORT_GATE ' + ('PASS' if passed else 'FAIL') + ' (not a service gate)')
        return 0 if passed else 2
    services = discover(root)
    for service in services:
        print(f'SERVICE {service.name} modules={len(service.modules)} missing={len(service.missing)} dynamic_import_sites={len(service.dynamic_import_sites)}')
        for path in service.modules:
            copy = (path.startswith('runtime/websites/') and (root / SITE_ENTRY).is_file()
                    and (root / path).read_bytes() == (root / SITE_ENTRY).read_bytes())
            print(f'  MODULE {path}' + (' (byte-identical to website template)' if copy else ''))
        for path in service.missing:
            print(f'  MISSING {path}')
        print('  EXTERNAL ' + ', '.join(service.external))
        for site in service.dynamic_import_sites:
            print('  DYNAMIC_IMPORT ' + site)
    module_totals = Counter()
    if not args.functions_only:
        totals = module_totals
        for rel, status, detail in audit_modules(root, services):
            totals[status] += 1
            print(f'MODULE_TRANSLATION {status} {rel}: {detail}')
        print('MODULE_STANDALONE_SOURCE ' + ' '.join(f'{k}={v}' for k,v in sorted(totals.items())))
    totals = Counter()
    for case, i, status, detail in audit_functions(root, native=args.native or args.wsl, bend=args.bend, wsl=args.wsl):
        totals[status] += 1
        print(f'FUNCTION {case}[{i}] {status}: {detail}')
    print('FUNCTION_FIXTURES ' + ' '.join(f'{k}={v}' for k,v in sorted(totals.items())))
    export_totals = Counter()
    for case, i, status, detail in audit_exports(root, native=args.native or args.wsl, bend=args.bend, wsl=args.wsl):
        export_totals[status] += 1
        print(f'LINKED_EXPORT {case}[{i}] {status}: {detail}')
    print('LINKED_EXPORT_FIXTURES ' + ' '.join(f'{k}={v}' for k,v in sorted(export_totals.items())))
    print('SERVICE_CERTIFICATION NOT_PERFORMED: no full server, protocol, or dependency execution')
    expected = sum(len(case[-1]) for case in FUNCTION_CASES.values())
    wanted = 'VERIFIED' if args.native or args.wsl else 'EMITTED'
    function_pass = (totals[wanted] == expected and
                     export_totals[wanted] + (export_totals['VERIFIED_REJECTION'] if wanted == 'VERIFIED' else 0)
                     == sum(len(e.fixtures) for e in EXPORTS.values()))
    if args.functions_only:
        print('FUNCTION_GATE ' + ('PASS' if function_pass else 'FAIL') + ' (not a service gate)')
        return 0 if function_pass else 2
    # Full service certification has not been implemented. Fail closed even
    # if all source probes happen to emit.
    print('SERVICE_READINESS BLOCKED')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
