"""Run standalone Bend feature probes without relying on Windows drive mounts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import sys


# Only fixed shell code is interpolated. The executable is passed as an argv
# value and the generated source as bytes on stdin, never as shell source.
_RESOLVE = '''
compiler=$1
shift
if [ "$compiler" = bend ] && ! command -v bend >/dev/null 2>&1; then
    if [ -x "$HOME/.bend/bin/bend" ]; then
        compiler="$HOME/.bend/bin/bend"
    fi
fi
if ! command -v "$compiler" >/dev/null 2>&1; then
    printf 'BEND_EXECUTABLE_NOT_FOUND: %s\\n' "$compiler" >&2
    exit 127
fi
'''
_VERSION = 'set -eu\n' + _RESOLVE + 'exec "$compiler" version\n'
_RUN = 'set -eu\n' + _RESOLVE + '''
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
cat > "$work/probe.bend"
cd "$work"
"$compiler" "$work/probe.bend" "$@"
'''


@dataclass(frozen=True)
class Preflight:
    ready: bool
    detail: str


@dataclass(frozen=True)
class ProofCheck:
    status: str
    detail: str

    @property
    def permits_execution(self) -> bool:
        return self.status in {'CHECKED', 'UNAVAILABLE_UNSAFE_OR_FOREIGN'}


# Bend 2.0.40's cli_verdict emits this specific diagnostic *after* loading
# the book. It is a proof-policy rejection, not an executable verdict.
# Match the whole message, not a substring of an arbitrary compiler error.
_DEPENDENCY_NAMES = r'[\w.$:/&-]+(?:, [\w.$:/&-]+)*'
_PROOF_POLICY = re.compile(
    r'SOME PROOFS FAIL\nError: ([1-9][0-9]*) (?:def relies|defs rely) on unsafe or foreign code:\n'
    rf'(?:defs that depend on (?:@unsafe|foreign effects): {_DEPENDENCY_NAMES}\n?)+'
)


def classify_proof_check(result: subprocess.CompletedProcess[str]) -> ProofCheck:
    """Keep proof rejection separate from all other compiler failures.

    No outcome here certifies execution. The ordinary Bend run must still
    exit successfully and match the independent Python oracle.
    """
    output = '\n'.join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    output = output.replace('\r\n', '\n')
    if result.returncode == 0 and output in {
        'ALL PROOFS CHECK',
        'ALL PROOFS CHECK\nUse --verdict for mathematical validity.',
    }:
        return ProofCheck('CHECKED', 'Bend check-only passed; kernel verdict not requested')
    policy = _PROOF_POLICY.fullmatch(output)
    if result.returncode == 1 and policy:
        return ProofCheck('UNAVAILABLE_UNSAFE_OR_FOREIGN', f'{policy[1]} definitions depend on unsafe or foreign code')
    return ProofCheck('FAILED', f'CHECK_EXIT={result.returncode}\n{output[:4000]}')


def _decode(data: bytes) -> str:
    # Some versions of the Windows WSL launcher emit UTF-16LE diagnostics.
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', errors='replace')
    if b'\x00' in data:
        return data.decode('utf-16-le', errors='replace')
    return data.decode('utf-8', errors='replace')


def _wsl(script: str, bend: str, *args: str, source: bytes = b'',
         timeout: float) -> subprocess.CompletedProcess[str]:
    cmd = ['wsl.exe', '--cd', '/', '--exec', 'sh', '-c', script,
           'python3-to-bend2', bend, *args]
    result = subprocess.run(cmd, input=source, capture_output=True, timeout=timeout,
                            check=False)
    return subprocess.CompletedProcess(cmd, result.returncode,
                                       _decode(result.stdout), _decode(result.stderr))


def preflight(bend: str, *, wsl: bool = False) -> Preflight:
    if wsl and sys.platform != 'win32':
        return Preflight(False, 'WSL_ONLY_AVAILABLE_ON_WINDOWS')
    if not shutil.which('wsl.exe' if wsl else bend):
        return Preflight(False, 'WSL_LAUNCHER_NOT_FOUND' if wsl else 'BEND_EXECUTABLE_NOT_FOUND')
    try:
        if wsl:
            result = _wsl(_VERSION, bend, timeout=30)
        else:
            result = subprocess.run([bend, 'version'], capture_output=True, text=True,
                                    timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return Preflight(False, f'NATIVE_PREFLIGHT_EXCEPTION {error}')
    output = (result.stdout + '\n' + result.stderr).strip()
    if result.returncode != 0:
        return Preflight(False, f'NATIVE_PREFLIGHT_EXIT={result.returncode} {output[-1600:]}')
    return Preflight(True, output[-1600:] or 'BEND_VERSION_COMMAND_SUCCEEDED')


def run_standalone(path: Path, *, bend: str = 'bend', wsl: bool = False,
                   check_only: bool = False, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    """Run a self-contained source file; WSL stages it in a Linux temp directory.

    Package imports are resolved by Bend itself. Relative companion imports and
    interactive input are outside this probe transport's contract.
    """
    args = ['--check-only'] if check_only else []
    if wsl:
        if sys.platform != 'win32':
            raise OSError('WSL_ONLY_AVAILABLE_ON_WINDOWS')
        return _wsl(_RUN, bend, *args, source=path.read_bytes(), timeout=timeout)
    return subprocess.run([bend, str(path), *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


# External-state probes must compare equivalent environments. In particular,
# the Windows Python process and the WSL Bend process do not inherit the same
# environment. Two independently staged fixtures check that the program reads
# runtime state rather than a compile-time value. No expected result is sent
# to Bend. The environment probe runs a compiled executable to keep any
# variables added by the compiler/interpreter out of the measured process.
_FIXTURE_SETUP = r'''
variant=$1
shift
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
cd "$work"
if [ "$variant" = 0 ]; then
    printf 'alpha\r\nbeta\rfinal\n' > example.txt
elif [ "$variant" = 1 ]; then
    printf 'second fixture\n' > example.txt
else
    exit 64
fi
fixture_env() {
    if [ "$variant" = 0 ]; then
        env -i PATH="$PATH" HOME="$HOME" LC_ALL=C.UTF-8 PYTHONUTF8=1 PYTHONCOERCECLOCALE=0 PY3_BEND_FIXTURE_A=alpha "$@"
    else
        env -i PATH="$PATH" HOME="$HOME" LC_ALL=C.UTF-8 PYTHONUTF8=1 PYTHONCOERCECLOCALE=0 PY3_BEND_FIXTURE_A=beta PY3_BEND_FIXTURE_B=extra "$@"
    fi
}
'''
_FIXTURE_BEND = 'set -eu\n' + _RESOLVE + '''
compiler=$(command -v "$compiler")
mode=$1
shift
''' + _FIXTURE_SETUP + '''
cat > probe.bend
if [ "$mode" = environment ]; then
    "$compiler" "$work/probe.bend" -o "$work/probe-native" >&2
    fixture_env "$work/probe-native"
else
    fixture_env "$compiler" "$work/probe.bend"
fi
'''
_FIXTURE_PYTHON = 'set -eu\n' + '''
python=$1
shift
''' + _FIXTURE_SETUP + '''
cat > probe.py
fixture_env "$python" -B -c 'import runpy; print(runpy.run_path("probe.py")["main"]())'
'''


def _fixture_command(script: str, executable: str, *args: str, source: bytes,
                     wsl: bool, timeout: float) -> subprocess.CompletedProcess[str]:
    if wsl:
        if sys.platform != 'win32':
            raise OSError('WSL_ONLY_AVAILABLE_ON_WINDOWS')
        return _wsl(script, executable, *args, source=source, timeout=timeout)
    if sys.platform != 'linux':
        raise OSError('EXTERNAL_STATE_PROBES_REQUIRE_LINUX_OR_WSL')
    cmd = ['sh', '-c', script, 'python3-to-bend2', executable, *args]
    result = subprocess.run(cmd, input=source, capture_output=True, timeout=timeout, check=False)
    return subprocess.CompletedProcess(cmd, result.returncode,
                                       _decode(result.stdout), _decode(result.stderr))


def run_fixture_bend(path: Path, *, bend: str, wsl: bool, feature: str,
                     variant: int, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    mode = 'environment' if feature == '60_os_environ' else 'file'
    return _fixture_command(_FIXTURE_BEND, bend, mode, str(variant),
                            source=path.read_bytes(), wsl=wsl, timeout=timeout)


def run_fixture_oracle(path: Path, *, wsl: bool, variant: int,
                       timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return _fixture_command(_FIXTURE_PYTHON, 'python3' if wsl else sys.executable,
                            str(variant), source=path.read_bytes(), wsl=wsl, timeout=timeout)
