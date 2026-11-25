"""Audit Python 3 -> Bend 2 feature readiness against a repository ZIP or directory.

This intentionally distinguishes Python parsing, Bend source emission, and actual
compiler-checked differential execution. It cannot certify Python compatibility
by counting AST nodes or generated-source tests.
"""
from __future__ import annotations
import argparse
import ast
import collections
from pathlib import Path
import subprocess
import sys
import tempfile
import warnings
import zipfile
from tools.python3_to_bend2.feature_cases import PROBES, LOCK_BEHAVIOR_PROBE

# Historical syntax-risk patterns, used as repo risk indicators only.
# Some patterns have partial support; these are NOT current blanket rejections.
# A source pattern is not a conclusive assertion that a complete module fails.
UNSUPPORTED_KINDS = (
    'comprehensions', 'with', 'fstrings', 'nonrange_for', 'lambda',
    'assert', 'set_literal', 'yield_from', 'async', 'walrus', 'match',
)


_FLAG_TYPES = {
    ast.ListComp: 'comprehensions', ast.DictComp: 'comprehensions',
    ast.SetComp: 'comprehensions', ast.GeneratorExp: 'comprehensions',
    ast.With: 'with', ast.AsyncWith: 'with', ast.JoinedStr: 'fstrings',
    ast.Lambda: 'lambda', ast.Assert: 'assert', ast.Set: 'set_literal',
    ast.YieldFrom: 'yield_from', ast.AsyncFunctionDef: 'async',
    ast.AsyncFor: 'async', ast.Await: 'async', ast.NamedExpr: 'walrus',
    ast.Match: 'match',
}


def flags(node: ast.AST) -> set[str]:
    # Exact node classification avoids repeated isinstance checks over the
    # millions of AST nodes in the Main Computer repository inventory.
    kind = _FLAG_TYPES.get(type(node))
    if kind:
        return {kind}
    if type(node) is ast.For and not (
        isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name)
        and node.iter.func.id == 'range' and isinstance(node.target, ast.Name)
    ):
        return {'nonrange_for'}
    return set()


def iter_python_sources(path: Path):
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.lower().endswith('.py'):
                    yield name.replace('\\', '/'), archive.read(name)
    elif path.is_dir():
        for f in sorted(path.rglob('*.py')):
            if any(p in {'.venv', 'venv', '__pycache__', 'node_modules'} for p in f.parts):
                continue
            yield str(f.relative_to(path)), f.read_bytes()
    else:
        raise ValueError(f'not a directory or ZIP archive: {path}')


def inventory_repository(path: Path) -> dict:
    scanned=parsed=0
    broken=[]
    occurrence=collections.Counter()
    file_count=collections.Counter()
    files_with_warning=0
    file_by_area=collections.Counter()
    risky_by_area=collections.Counter()
    for name, data in iter_python_sources(path):
        scanned += 1
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', SyntaxWarning)
                tree=ast.parse(data.decode('utf-8-sig'),filename=name)
        except (SyntaxError, UnicodeDecodeError) as exc:
            broken.append((name, str(exc)))
            continue
        parsed+=1
        seen=set()
        for node in ast.walk(tree):
            nodeflags=flags(node)
            occurrence.update(nodeflags)
            seen.update(nodeflags)
        file_count.update(seen)
        area = name.split('/')
        area = area[1] if len(area)>2 and area[0].startswith('main_computer_test') else area[0]
        file_by_area[area]+=1
        if seen:
            files_with_warning+=1
            risky_by_area[area]+=1
    return dict(files_scanned=scanned, files_parsed=parsed, unparsed=broken,
                files_with_warning=files_with_warning,
                feature_occurrences=dict(occurrence), feature_files=dict(file_count),
                area_files=dict(file_by_area), area_files_with_warning=dict(risky_by_area))


def qualified_gate(valid: int, emitted: int, native: int, required_percent: float):
    """Native execution is mandatory: emitted-source coverage cannot certify it."""
    if valid <= 0: return False
    return 100 * emitted / valid >= required_percent and 100 * native / valid >= required_percent


# This fixed probe deliberately exits by throwing an exception with an explicit
# cause. Treating every failed Python subprocess as a successful oracle would
# also accept unrelated Python bugs, so opt in only for the expected case.
_CHAIN_PROBES = frozenset({'61_raise_from'})
_FIXTURE_PROBES = frozenset({'40_context_manager', '60_os_environ'})
_CHAIN_MARKER = 'PYTHON_ORACLE_EXPLICIT_CHAIN\n'
_CHAIN_SEPARATOR = '\nThe above exception was the direct cause of the following exception:\n'


def _python_oracle(path: Path, *, expect_chain: bool):
    snippet = (
        'import asyncio, inspect, runpy, sys, traceback\n'
        'mod = runpy.run_path(sys.argv[1])\n'
        'def invoke():\n'
        '    result = mod["main"]()\n'
        '    return asyncio.run(result) if inspect.iscoroutine(result) else result\n'
    )
    if expect_chain:
        # Compute exception classes and messages independently of the Bend
        # emitter. Only the traceback source locations are omitted.
        snippet += (
            'try:\n'
            '    value = invoke()\n'
            'except Exception as exc:\n'
            '    cause = exc.__cause__\n'
            '    if cause is None or cause.__cause__ is not None:\n'
            '        raise\n'
            '    def text(error):\n'
            '        return "".join(traceback.format_exception_only(type(error), error)).removesuffix("\\n")\n'
            f'    sys.stderr.write({_CHAIN_MARKER!r} + text(cause) + {_CHAIN_SEPARATOR!r} + text(exc) + "\\n")\n'
            '    sys.exit(1)\n'
            'else:\n'
            '    print(value)\n'
        )
    else:
        snippet += 'print(invoke())\n'
    return subprocess.run([sys.executable, '-B', '-c', snippet, str(path)],
                          capture_output=True, text=True, timeout=20)


def _compare_chain(oracle, observed):
    """Require the same stdout, explicit cause, outer error and exit status.

    IO.die writes a diagnostic; permit only its optional Error: prefix and
    terminal newline, never a substring of an arbitrary compiler diagnostic.
    """
    expected = oracle.stderr.removeprefix(_CHAIN_MARKER).removesuffix('\n')
    actual = observed.stderr.replace('\r\n', '\n').removesuffix('\n')
    if observed.returncode == 1 and observed.stdout == oracle.stdout and actual in (expected, 'Error: ' + expected):
        return True
    return False


def run_probes(*, check_bend=False, bend='bend', wsl=False, translator='current', feature=None):
    if translator == 'current':
        from tools.python3_to_bend2 import Compiler as HeapTranslator
    elif translator == 'v16':
        from tools.python3_to_bend2.numeric import Compiler as HeapTranslator
    elif translator == 'v15':
        from tools.python3_to_bend2.scopes_async import ScopeAsyncLowerer as HeapTranslator
    elif translator == 'v14':
        from tools.python3_to_bend2.collections import CollectionLowerer as HeapTranslator
    elif translator == 'v13':
        from tools.python3_to_bend2.normalization import NormalizationLowerer as HeapTranslator
    elif translator == 'v12':
        from tools.python3_to_bend2.inheritance import InheritanceLowerer as HeapTranslator
    else:
        raise ValueError('unknown translator version')
    from tools.python3_to_bend2.native_execution import (
        classify_proof_check, preflight, run_standalone,
        run_fixture_bend, run_fixture_oracle,
    )
    native = preflight(bend, wsl=wsl) if check_bend else None
    cases = PROBES if feature is None else {feature: PROBES[feature]}
    results=[]
    with tempfile.TemporaryDirectory(prefix='bend-feature-audit-') as d:
        for name, source in cases.items():
            outcome=dict(feature=name, source='REJECTED', native='NOT_TESTED', proof='NOT_TESTED', detail='')
            try:
                compile(source,name,'exec')
            except Exception as exc:
                outcome.update(source='INVALID_PROBE',detail=f'{type(exc).__name__}: {exc}')
                results.append(outcome)
                continue
            try:
                emitted=HeapTranslator(source,name,show_return=True).compile()
                outcome['source']='EMITTED'
            except Exception as exc:
                outcome['detail']=str(exc).replace('\n',' ')[:400]
                results.append(outcome)
                continue
            if not check_bend:
                outcome['native']='NOT_REQUESTED'
            elif not native.ready:
                outcome.update(native='PREFLIGHT_BLOCKED', detail=native.detail)
            else:
                try:
                    pyfile=Path(d)/(name+'.py'); pyfile.write_text(source,encoding='utf-8')
                    bendfile=Path(d)/(name+'.bend'); bendfile.write_text(emitted,encoding='utf-8',newline='\n')
                    checked = run_standalone(bendfile, bend=bend, wsl=wsl, check_only=True)
                    proof = classify_proof_check(checked)
                    outcome['proof'] = proof.status
                    if not proof.permits_execution:
                        outcome.update(native='CHECK_FAILED', detail=proof.detail)
                    elif name in _FIXTURE_PROBES:
                        # Two different real inputs; the Python oracle runs in
                        # the same environment contract as the native program.
                        for variant in (0, 1):
                            oracle = run_fixture_oracle(pyfile, wsl=wsl, variant=variant)
                            if oracle.returncode != 0:
                                outcome.update(native='ORACLE_FAILED', detail=(
                                    f'fixture={variant} rc={oracle.returncode}\n' + oracle.stderr[:4000]))
                                break
                            observed = run_fixture_bend(bendfile, bend=bend, wsl=wsl,
                                                        feature=name, variant=variant)
                            if observed.returncode != 0:
                                outcome.update(native='RUN_FAILED', detail=(
                                    f'fixture={variant} rc={observed.returncode}\n' +
                                    (observed.stderr + '\n' + observed.stdout)[:4000]))
                                break
                            if observed.stdout != oracle.stdout:
                                outcome.update(native='DIFFERENTIAL_FAILED', detail=(
                                    f'fixture={variant} expected={oracle.stdout[:400]!r} '
                                    f'got={observed.stdout[:400]!r} error={observed.stderr[:400]!r}'))
                                break
                        else:
                            outcome.update(native='VERIFIED', detail=(
                                f'proof={proof.status} {proof.detail}; both external-state fixtures matched'))
                    else:
                        expect_chain = name in _CHAIN_PROBES
                        oracle = _python_oracle(pyfile, expect_chain=expect_chain)
                        oracle_ok = (
                            oracle.returncode == 1 and oracle.stderr.startswith(_CHAIN_MARKER)
                            if expect_chain else oracle.returncode == 0
                        )
                        if not oracle_ok:
                            outcome.update(native='ORACLE_FAILED', detail=(
                                f'rc={oracle.returncode}\n' + oracle.stderr[:4000]))
                        else:
                            # A normal Bend invocation checks and executes the same
                            # emitted source; proof rejection is not runtime success.
                            observed=run_standalone(bendfile,bend=bend,wsl=wsl,timeout=60)
                            if expect_chain:
                                if _compare_chain(oracle, observed):
                                    outcome.update(native='VERIFIED', detail=(
                                        f'proof={proof.status} {proof.detail}; explicit exception chain matched'))
                                else:
                                    outcome.update(native='DIFFERENTIAL_FAILED', detail=(
                                        f'proof={proof.status} expected rc=1 stdout={oracle.stdout[:400]!r} '
                                        f'chain={oracle.stderr.removeprefix(_CHAIN_MARKER)[:1000]!r}\n'
                                        f'got rc={observed.returncode} stdout={observed.stdout[:400]!r} '
                                        f'stderr={observed.stderr[:4000]!r}'))
                            elif observed.returncode != 0:
                                outcome.update(native='RUN_FAILED', detail=(
                                    f'proof={proof.status} rc={observed.returncode}\n'
                                    + (observed.stderr + '\n' + observed.stdout)[:4000]))
                            elif observed.stdout != oracle.stdout:
                                outcome.update(native='DIFFERENTIAL_FAILED',detail=(
                                    f'proof={proof.status} expected={oracle.stdout[:400]!r} '
                                    f'got={observed.stdout[:400]!r} error={observed.stderr[:400]!r}'))
                            else:
                                outcome.update(native='VERIFIED', detail=f'proof={proof.status} {proof.detail}')
                    if name == '56_threading' and outcome['native'] == 'VERIFIED':
                        # Keep the original inventory case and additionally
                        # require acquire/release/alias behavior to match Python.
                        pyfile.write_text(LOCK_BEHAVIOR_PROBE, encoding='utf-8')
                        lock_source = HeapTranslator(LOCK_BEHAVIOR_PROBE,
                                                     'lock_behavior', show_return=True).compile()
                        bendfile.write_text(lock_source, encoding='utf-8', newline='\n')
                        checked_lock = run_standalone(bendfile, bend=bend, wsl=wsl, check_only=True)
                        lock_proof = classify_proof_check(checked_lock)
                        if not lock_proof.permits_execution:
                            outcome.update(native='CHECK_FAILED', proof=lock_proof.status,
                                           detail='lock behavior: ' + lock_proof.detail)
                        else:
                            lock_oracle = _python_oracle(pyfile, expect_chain=False)
                            if lock_oracle.returncode != 0:
                                outcome.update(native='ORACLE_FAILED', detail='lock behavior: ' + lock_oracle.stderr[:4000])
                            else:
                                lock_run = run_standalone(bendfile, bend=bend, wsl=wsl, timeout=60)
                                if lock_run.returncode != 0:
                                    outcome.update(native='RUN_FAILED', detail='lock behavior: ' + (lock_run.stderr + '\n' + lock_run.stdout)[:4000])
                                elif lock_run.stdout != lock_oracle.stdout:
                                    outcome.update(native='DIFFERENTIAL_FAILED', detail=(
                                        f'lock behavior: expected={lock_oracle.stdout[:400]!r} got={lock_run.stdout[:400]!r}'))
                                else:
                                    outcome['detail'] += '; lock acquire/release/alias behavior matched'
                except Exception as exc:
                    outcome.update(native='EXECUTION_FAILED',detail=f'{type(exc).__name__}: {exc}'[:400])
            results.append(outcome)
    return results


def build_report(repo: dict, results: list[dict], required_percent: float, *, check_bend: bool):
    n=len(results)
    emitted=sum(r['source']=='EMITTED' for r in results)
    verified=sum(r['native']=='VERIFIED' for r in results)
    invalid=sum(r['source']=='INVALID_PROBE' for r in results)
    eligible=n-invalid
    repo_risk_rate=100*repo['files_with_warning']/max(repo['files_parsed'],1)
    probe_pass=qualified_gate(eligible,emitted,verified,required_percent)
    repo_pass=repo['files_parsed']>0 and repo_risk_rate <= 100-required_percent
    approved=probe_pass and repo_pass and not repo['unparsed']
    lines=[
        'PYTHON3_TO_BEND2_FEATURE_AUDIT',
        f'PYTHON_REPO parsed={repo["files_parsed"]}/{repo["files_scanned"]}',
        f'STATIC_GAP_WARNING files={repo["files_with_warning"]}/{repo["files_parsed"]} (historical pattern flags; some partially supported by the compiler)',
        f'PROBES valid={eligible} source_emitted={emitted} source_rate={100*emitted/max(eligible,1):.1f}%',
        f'NATIVE_EXECUTION verified={verified} rate={100*verified/max(eligible,1):.1f}% requested={check_bend}',
        f'PROBE_THRESHOLD required={required_percent:.1f}% status={"PASS" if probe_pass else "FAIL"}',
        f'REPO_STATIC_RISK rate={repo_risk_rate:.1f}% maximum={100-required_percent:.1f}% status={"PASS" if repo_pass else "FAIL"} (conservative, not an observed rejection rate)',
        f'COMBINED_AUDIT status={"PASS" if approved else "FAIL"}',
        'WARNING: Passing this finite probe gate would still not establish complete Python 3 compatibility.',
        'WARNING: Generated Bend source is not execution-certified.',
        '', 'FEATURE_USAGE file_count occurrence_count',
    ]
    for name,num in sorted(repo['feature_files'].items(),key=lambda x:-x[1]):
        lines.append(f'{name:24} {num:6d} {repo["feature_occurrences"].get(name,0):9d}')
    lines.extend(['', 'AREA file_count warned_files'])
    for area,num in sorted(repo['area_files'].items(),key=lambda x:-x[1]):
        lines.append(f'{area:24} {num:6d} {repo["area_files_with_warning"].get(area,0):9d}')
    lines.extend(['','PROBES feature emission native reason'])
    for r in results:
        lines.append(f'{r["feature"]:32} {r["source"]:14} {r["native"]:22} {r["detail"]}')
    if repo['unparsed']:
        lines.append(f'UNPARSED_FILES {len(repo["unparsed"])}')
    return '\n'.join(lines)+'\n',approved


def main(argv=None):
    """Run the fixed feature gate without writing report files."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, help='optional repository directory or ZIP for static inventory')
    ap.add_argument('--threshold', type=float, default=95.0)
    ap.add_argument('--check-bend', action='store_true', help='compile and execute emitted cases')
    ap.add_argument('--bend', default='bend')
    ap.add_argument('--wsl', action='store_true')
    ap.add_argument('--require-95', action='store_true', help='require native execution; also require the static repository gate when --repo is provided')
    ap.add_argument('--require-source-95', action='store_true', help='require source emission only; not runtime certification')
    args = ap.parse_args(argv)
    if not 0 <= args.threshold <= 100:
        ap.error('--threshold must be within 0..100')
    if args.require_95 and args.require_source_95:
        ap.error('choose one acceptance threshold')
    check = args.check_bend or args.require_95
    results = run_probes(check_bend=check, bend=args.bend, wsl=args.wsl)
    valid = sum(r['source'] != 'INVALID_PROBE' for r in results)
    emitted = sum(r['source'] == 'EMITTED' for r in results)
    verified = sum(r['native'] == 'VERIFIED' for r in results)
    source_ready = valid == len(PROBES) and 100 * emitted / max(valid, 1) >= args.threshold
    native_ready = valid == len(PROBES) and qualified_gate(valid, emitted, verified, args.threshold)
    print(f'SOURCE_EMISSION {emitted}/{len(PROBES)} ({100 * emitted / len(PROBES):.1f}%)')
    print(f'NATIVE_EXECUTION {verified}/{len(PROBES)} ({100 * verified / len(PROBES):.1f}%) requested={check}')
    print(f'SOURCE_READINESS {"PASS" if source_ready else "FAIL"} threshold={args.threshold:.1f}%')
    print(f'NATIVE_READINESS {"PASS" if native_ready else "FAIL"} threshold={args.threshold:.1f}%')
    if check:
        proofs = collections.Counter(r['proof'] for r in results)
        print('BEND_PROOF_CHECK ' + ' '.join(f'{key}={value}' for key, value in sorted(proofs.items())))
    blocked = [result for result in results if result['native'] == 'PREFLIGHT_BLOCKED']
    if blocked:
        print(f'NATIVE_PREFLIGHT BLOCKED cases={len(blocked)} {blocked[0]["detail"]}')
    elif check:
        print('NATIVE_PREFLIGHT PASSED' if emitted else 'NATIVE_PREFLIGHT no emitted cases')
    for result in results:
        if result['native'] == 'PREFLIGHT_BLOCKED':
            continue
        if result['source'] != 'EMITTED' or (check and result['native'] != 'VERIFIED'):
            print(f'{result["feature"]}: {result["source"]} / {result["native"]} {result["detail"]}')
    if args.repo is not None:
        repo = inventory_repository(args.repo)
        _, approved = build_report(repo, results, args.threshold, check_bend=check)
        native_ready = native_ready and approved
        print(f'REPOSITORY parsed={repo["files_parsed"]}/{repo["files_scanned"]} static_warnings={repo["files_with_warning"]} (not observed failures)')
        print(f'COMBINED_NATIVE_GATE {"PASS" if native_ready else "FAIL"}')
    return 2 if ((args.require_95 and not native_ready) or
                 (args.require_source_95 and not source_ready)) else 0
