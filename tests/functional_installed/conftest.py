from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import venv
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile

import pytest


pytestmark = pytest.mark.installed_functional


RUN_ENV_VAR = "MAIN_COMPUTER_RUN_INSTALLED_FUNCTIONALS"

SETUP_TIMEOUT_S = float(os.environ.get("MAIN_COMPUTER_FUNCTIONAL_SETUP_TIMEOUT_S", "180"))
SERVER_TIMEOUT_S = float(os.environ.get("MAIN_COMPUTER_FUNCTIONAL_SERVER_TIMEOUT_S", "45"))
BROWSER_TIMEOUT_MS = int(os.environ.get("MAIN_COMPUTER_FUNCTIONAL_TIMEOUT_MS", "15000"))


def _live_line(message: str) -> None:
    """Write a line that is visible even when pytest captures test output."""

    prefix = time.strftime("%H:%M:%S")
    try:
        sys.__stderr__.write(f"\n[installed-functional {prefix}] {message}\n")
        sys.__stderr__.flush()
    except Exception:
        print(f"[installed-functional {prefix}] {message}", flush=True)


def _progress(config: pytest.Config | None, message: str) -> None:
    reporter = None
    if config is not None:
        reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(f"[installed-functional] {message}")
    else:
        _live_line(message)


def pytest_runtest_logstart(nodeid: str, location) -> None:
    if os.environ.get(RUN_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        _live_line(f"START {nodeid}")


def pytest_runtest_logfinish(nodeid: str, location) -> None:
    if os.environ.get(RUN_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        _live_line(f"FINISH {nodeid}")


def _tail(path: Path, *, lines: int = 80) -> str:
    if not path.exists():
        return f"<missing: {path}>"
    text = path.read_text(encoding="utf-8", errors="replace")
    parts = text.splitlines()
    return "\n".join(parts[-lines:]) if parts else "<empty>"


def _command_text(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    config: pytest.Config,
    label: str,
    timeout_s: float = SETUP_TIMEOUT_S,
    env: dict[str, str] | None = None,
) -> None:
    """Run setup commands with live output and a real wall-clock timeout."""

    merged_env = os.environ.copy()
    merged_env.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PIP_PROGRESS_BAR": "off",
            "PYTHONUNBUFFERED": "1",
        }
    )
    if env:
        merged_env.update(env)

    _progress(config, f"{label}: running {_command_text(command)}")
    started = time.monotonic()
    deadline = started + timeout_s
    output_lines: list[str] = []
    output_queue: queue.Queue[str | None] = queue.Queue()

    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
    except OSError as exc:
        pytest.fail(
            f"{label} could not start.\nCommand: {_command_text(command)}\nError: {exc}",
            pytrace=False,
        )

    assert process.stdout is not None

    def _reader() -> None:
        try:
            for raw_line in process.stdout:
                output_queue.put(raw_line.rstrip("\r\n"))
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=_reader, name=f"{label}-output-reader", daemon=True)
    reader.start()

    reader_done = False
    last_idle_notice = started
    while True:
        drained = False
        while True:
            try:
                item = output_queue.get_nowait()
            except queue.Empty:
                break
            drained = True
            if item is None:
                reader_done = True
                continue
            output_lines.append(item)
            if item.strip():
                _progress(config, f"{label}: {item}")

        returncode = process.poll()
        now = time.monotonic()
        if returncode is not None and reader_done:
            break
        if now >= deadline:
            _progress(config, f"{label}: timeout reached; terminating process tree")
            _terminate_process_tree(process)
            tail = "\n".join(output_lines[-80:]) if output_lines else "<no output captured>"
            pytest.fail(
                f"{label} timed out after {timeout_s:.0f}s.\n"
                f"Command: {_command_text(command)}\n"
                f"Captured output tail:\n{tail}",
                pytrace=False,
            )
        if not drained and now - last_idle_notice >= 15.0:
            _progress(config, f"{label}: still running after {now - started:.0f}s with no new output")
            last_idle_notice = now
        time.sleep(0.1)

    elapsed = time.monotonic() - started
    if process.returncode != 0:
        tail = "\n".join(output_lines[-120:]) if output_lines else "<no output captured>"
        pytest.fail(
            f"{label} failed with exit code {process.returncode}.\n"
            f"Command: {_command_text(command)}\n"
            f"Captured output tail:\n{tail}",
            pytrace=False,
        )

    tail = "\n".join(line for line in output_lines[-12:] if line.strip())
    if tail:
        _progress(config, f"{label}: completed in {elapsed:.1f}s\n{tail}")
    else:
        _progress(config, f"{label}: completed in {elapsed:.1f}s")


def _wait_for_viewport_ready(
    base_url: str,
    *,
    process: subprocess.Popen[str],
    stdout_log: Path,
    stderr_log: Path,
    config: pytest.Config,
    timeout_s: float = SERVER_TIMEOUT_S,
) -> dict:
    url = f"{base_url}/api/projects"
    _progress(config, f"waiting for installed viewport at {url}")
    deadline = time.monotonic() + timeout_s
    next_notice = time.monotonic() + 5.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(
                f"Installed viewport exited before it became ready; exit code {process.returncode}.\n"
                f"stdout tail:\n{_tail(stdout_log)}\n\nstderr tail:\n{_tail(stderr_log)}",
                pytrace=False,
            )
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=1.0) as response:
                body = response.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {"ok": response.status < 400, "raw": body, "status": response.status}
                _progress(config, f"installed viewport is ready at {base_url}")
                return payload
        except Exception as exc:
            last_error = exc
            now = time.monotonic()
            if now >= next_notice:
                _progress(config, f"still waiting for installed viewport: {last_error}")
                next_notice = now + 5.0
            time.sleep(0.15)
    pytest.fail(
        f"Timed out after {timeout_s:.0f}s waiting for installed viewport at {url}: {last_error}\n"
        f"stdout tail:\n{_tail(stdout_log)}\n\nstderr tail:\n{_tail(stderr_log)}",
        pytrace=False,
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "installed_functional: opt-in functional tests that build/install the package and drive the browser UI.",
    )
    config.addinivalue_line(
        "markers",
        "onlyoffice_functional: installed-package ONLYOFFICE browser golden paths.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(RUN_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    skip = pytest.mark.skip(
        reason=f"installed functional tests are opt-in; set {RUN_ENV_VAR}=1 to run them"
    )
    for item in items:
        if "installed_functional" in item.keywords:
            item.add_marker(skip)


@dataclass(frozen=True)
class InstalledMainComputer:
    repo_root: Path
    install_root: Path
    site_packages: Path
    python: Path
    main_computer: Path


@dataclass(frozen=True)
class RunningViewport:
    base_url: str
    port: int
    workspace: Path
    process: subprocess.Popen[str]
    stdout_log: Path
    stderr_log: Path


@dataclass
class FakeOllamaServer:
    url: str
    requests: list[dict]


@dataclass
class FakeOnlyOfficeServer:
    url: str
    commands: list[dict]
    api_hits: int = 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _venv_python(venv_root: Path) -> Path:
    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _venv_script(venv_root: Path, name: str) -> Path:
    if os.name == "nt":
        return venv_root / "Scripts" / f"{name}.exe"
    return venv_root / "bin" / name


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http_json(url: str, *, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=1.0) as response:
                body = response.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return {"ok": response.status < 400, "raw": body, "status": response.status}
        except Exception as exc:
            last_error = exc
            time.sleep(0.15)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _post_http_json(url: str, payload: dict, *, timeout_s: float = 5.0) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"ok": response.status < 400, "raw": body, "status": response.status}


def _terminate_process_tree(process: subprocess.Popen[str], *, timeout_s: float = 6.0) -> None:
    if process.poll() is not None:
        return
    try:
        import psutil  # type: ignore
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout_s)
        return

    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        parent.terminate()
        for child in children:
            with contextlib.suppress(Exception):
                child.terminate()
        gone, alive = psutil.wait_procs([parent, *children], timeout=timeout_s)
        for proc in alive:
            with contextlib.suppress(Exception):
                proc.kill()
        psutil.wait_procs(alive, timeout=2.0)
    except psutil.NoSuchProcess:
        return


def _minimal_xlsx_bytes() -> bytes:
    """Create a tiny .xlsx package accepted by the ONLYOFFICE upload route."""

    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="inlineStr"><is><t>uploaded through Playwright</t></is></c>
    </row>
  </sheetData>
</worksheet>""",
        )
    return buf.getvalue()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _repo_root()


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _dependency_sys_paths(repo_root: Path, install_root: Path) -> list[str]:
    """Capture the active environment's installed dependency paths without exposing the source checkout."""

    paths: list[str] = []
    seen: set[str] = set()
    for raw in sys.path:
        if not raw:
            continue
        path = Path(raw).resolve()
        text = str(path)
        lowered = text.lower()
        if not path.exists():
            continue
        if _path_is_relative_to(path, repo_root) or _path_is_relative_to(path, install_root):
            continue
        if "site-packages" not in lowered and "dist-packages" not in lowered:
            continue
        if text not in seen:
            seen.add(text)
            paths.append(text)
    return paths


def _ignore_staged_install_entries(directory: str, names: list[str]) -> set[str]:
    """Ignore cache files and optional source maps while staging the installed tree.

    Windows without long-path support can fail when copying Revogrid's very long
    ``*.js.map`` filenames into pytest's temp directory. Source maps are not
    runtime assets for these functional tests, so excluding them preserves the
    user-visible app behavior while keeping the staged install portable.
    """

    ignored: set[str] = set()
    for name in names:
        lowered = name.lower()
        if name in {"__pycache__", ".pytest_cache"}:
            ignored.add(name)
        elif lowered.endswith((".pyc", ".pyo", ".map")):
            ignored.add(name)
    return ignored



def _venv_site_packages(venv_root: Path) -> Path:
    if os.name == "nt":
        return venv_root / "Lib" / "site-packages"
    return venv_root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def _create_isolated_python(
    repo_root: Path,
    install_root: Path,
    *,
    config: pytest.Config,
) -> tuple[Path, Path]:
    """Create the one Python interpreter used by the installed functional app.

    The launched Main Computer process and any child Python subprocesses must use
    this interpreter, not the pytest runner's interpreter and not the user's
    bootstrap/debug venv.  Dependencies are made visible through a small .pth
    file that points at the already-installed dependency site-packages, while the
    main_computer package itself is staged into this venv's real site-packages.
    """

    venv_root = install_root / "py"
    _progress(config, f"creating isolated functional Python at {venv_root}")
    started = time.monotonic()
    builder = venv.EnvBuilder(clear=True, with_pip=False, system_site_packages=False)
    builder.create(str(venv_root))

    python = _venv_python(venv_root).resolve()
    site_packages = _venv_site_packages(venv_root).resolve()
    if not python.exists():
        pytest.fail(f"Isolated functional Python was not created: {python}", pytrace=False)
    site_packages.mkdir(parents=True, exist_ok=True)

    dependency_paths = _dependency_sys_paths(repo_root, install_root)
    bad_dependencies = [path for path in dependency_paths if _path_is_relative_to(Path(path), repo_root)]
    if bad_dependencies:
        pytest.fail(
            "Internal error: isolated functional dependency paths include the source checkout:\n"
            + "\n".join(bad_dependencies),
            pytrace=False,
        )

    deps_pth = site_packages / "main_computer_functional_dependencies.pth"
    deps_pth.write_text("\n".join(dependency_paths) + ("\n" if dependency_paths else ""), encoding="utf-8")

    _progress(config, f"isolated functional Python ready in {time.monotonic() - started:.1f}s: {python}")
    if dependency_paths:
        _progress(config, f"isolated functional Python dependency paths: {len(dependency_paths)}")
    return python, site_packages


def _installed_python_env(python: Path, *, base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    scripts_dir = str(python.parent)
    env["PATH"] = scripts_dir + os.pathsep + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["MAIN_COMPUTER_PYTHON"] = str(python)
    env["MAIN_COMPUTER_FUNCTIONAL_PYTHON"] = str(python)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("__PYVENV_LAUNCHER__", None)
    return env


def _copy_staged_install(
    repo_root: Path,
    install_root: Path,
    *,
    site_packages: Path,
    config: pytest.Config,
) -> tuple[Path, Path]:
    """Stage the package into the isolated functional Python's site-packages."""

    source_package = repo_root / "main_computer"
    assert source_package.is_dir(), f"Missing source package: {source_package}"

    package_target = site_packages / "main_computer"
    scripts_dir = install_root / "scripts"
    dist_info = site_packages / "main_computer_test-0.1.0.dist-info"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    site_packages.mkdir(parents=True, exist_ok=True)

    _progress(config, f"staging installed package tree at {package_target}")
    started = time.monotonic()
    if package_target.exists():
        shutil.rmtree(package_target)
    shutil.copytree(
        source_package,
        package_target,
        ignore=_ignore_staged_install_entries,
    )

    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        "\n".join(
            [
                "Metadata-Version: 2.1",
                "Name: main-computer-test",
                "Version: 0.1.0",
                "Summary: Staged install for installed functional tests",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[console_scripts]\nmain-computer = main_computer.cli:main\n",
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text("", encoding="utf-8")

    script = scripts_dir / "main-computer.py"
    script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "from main_computer.cli import main",
                "",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
                "",
            ]
        ),
        encoding="utf-8",
    )

    critical_assets = [
        package_target / "web" / "applications.html",
        package_target / "web" / "applications" / "scripts" / "onlyoffice-app.js",
        package_target / "web" / "applications" / "styles" / "onlyoffice.css",
    ]
    missing = [str(path) for path in critical_assets if not path.exists()]
    assert not missing, "Staged install is missing critical web assets:\n" + "\n".join(missing)

    _progress(config, f"staged installed package tree in {time.monotonic() - started:.1f}s")
    _progress(config, f"staged console script ready: {script}")
    return site_packages, script


@pytest.fixture(scope="session")
def installed_main_computer(
    tmp_path_factory: pytest.TempPathFactory,
    repo_root: Path,
    request: pytest.FixtureRequest,
) -> InstalledMainComputer:
    """Stage the package outside the checkout and launch it as an installed app.

    The browser tests run the CLI from a temp install tree with the source
    checkout excluded from subprocess import paths. This avoids slow/hung
    per-test wheel builds while still catching missing installed package files.
    """

    config = request.config
    # Keep the staged install path short.  On Windows, deeply nested pytest temp
    # directories plus long vendor filenames can exceed MAX_PATH even though the
    # files are not required at runtime.
    work_root = Path(tempfile.mkdtemp(prefix="mcfit-")).resolve()
    request.addfinalizer(lambda: shutil.rmtree(work_root, ignore_errors=True))
    install_root = work_root / "i"
    functional_python, functional_site_packages = _create_isolated_python(
        repo_root,
        install_root,
        config=config,
    )
    site_packages, main_computer = _copy_staged_install(
        repo_root,
        install_root,
        site_packages=functional_site_packages,
        config=config,
    )

    verification = (
        "import json, sys; "
        "from pathlib import Path; "
        "import main_computer; "
        "import main_computer.cli; "
        f"expected_python = Path({str(functional_python)!r}).resolve(); "
        f"expected_package = Path({str(site_packages / 'main_computer')!r}).resolve(); "
        "actual_python = Path(sys.executable).resolve(); "
        "actual_package = Path(main_computer.__file__).resolve().parent; "
        "assert actual_python == expected_python, (actual_python, expected_python); "
        "assert actual_package == expected_package, (actual_package, expected_package); "
        "print(json.dumps({'python': str(actual_python), 'package': str(actual_package)}))"
    )
    _run_checked(
        [
            str(functional_python),
            "-E",
            "-s",
            "-c",
            verification,
        ],
        cwd=work_root,
        config=config,
        label="verify isolated installed Python import",
        timeout_s=min(SETUP_TIMEOUT_S, 45.0),
        env=_installed_python_env(functional_python),
    )
    _run_checked(
        [
            str(functional_python),
            "-E",
            "-s",
            str(main_computer),
            "--help",
        ],
        cwd=work_root,
        config=config,
        label="verify staged installed CLI import",
        timeout_s=min(SETUP_TIMEOUT_S, 45.0),
        env=_installed_python_env(functional_python),
    )

    return InstalledMainComputer(
        repo_root=repo_root,
        install_root=install_root,
        site_packages=site_packages,
        python=functional_python,
        main_computer=main_computer,
    )

class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, body: bytes, *, status: HTTPStatus = HTTPStatus.OK, content_type: str = "application/json") -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _FakeOllamaHandler(_QuietHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/api/tags"):
            body = json.dumps({"models": [{"name": "functional-fast"}]}).encode("utf-8")
            self._send(body)
            return
        self._send(b'{"error":"not found"}', status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        self.server.requests.append(payload)  # type: ignore[attr-defined]
        prompt_text = "\n".join(
            str(message.get("content", ""))
            for message in payload.get("messages", [])
            if isinstance(message, dict)
        )
        if "ONLYOFFICE_FORMULA_SUM_A1_A10" in prompt_text or ("A1:A10" in prompt_text and "A11" in prompt_text):
            content = """const sheet = Api.GetActiveSheet();
const target = sheet.GetRange("A11");
target.SetFormula("=SUM(A1:A10)");
return "A11=" + target.GetValue() + " formula=" + target.GetFormula();
"""
        else:
            content = (
                "ONLYOFFICE_AI_OK: workbook tools, embedded chat, and installed AI subprocess path responded."
            )
        chunks = [
            {
                "model": payload.get("model") or "functional-fast",
                "created_at": "2026-05-14T00:00:00Z",
                "message": {"role": "assistant", "content": content},
                "done": False,
            },
            {
                "model": payload.get("model") or "functional-fast",
                "created_at": "2026-05-14T00:00:00Z",
                "message": {"role": "assistant", "content": ""},
                "done": True,
            },
        ]
        raw = "".join(json.dumps(chunk) + "\n" for chunk in chunks).encode("utf-8")
        self._send(raw, content_type="application/x-ndjson")


class _FakeOnlyOfficeHandler(_QuietHandler):
    DOCS_API_STUB = r"""
(() => {
  const records = window.__onlyofficeFunctional = window.__onlyofficeFunctional || {
    docEditorConstructed: 0,
    destroyed: 0,
    connectorCreated: 0,
    comments: [],
    configs: [],
    hostIds: []
  };

  records.cells = records.cells && typeof records.cells === "object" ? records.cells : {};
  records.selectedAddress = records.selectedAddress || "B2:C3";
  records.selectionChanges = Array.isArray(records.selectionChanges) ? records.selectionChanges : [];
  records.formulaExecutions = Array.isArray(records.formulaExecutions) ? records.formulaExecutions : [];

  function normalizeCellAddress(address) {
    const match = String(address || "").trim().toUpperCase().match(/^([A-Z]+)([1-9][0-9]*)$/);
    if (!match) throw new Error(`Invalid fake ONLYOFFICE cell address: ${address}`);
    return `${match[1]}${match[2]}`;
  }

  function parseRangeAddress(address) {
    const text = String(address || "").trim().toUpperCase();
    const parts = text.split(":").map((part) => part.trim()).filter(Boolean);
    if (parts.length === 1) {
      const cell = normalizeCellAddress(parts[0]);
      return {address: cell, start: cell, end: cell};
    }
    if (parts.length !== 2) throw new Error(`Invalid fake ONLYOFFICE range address: ${address}`);
    const start = normalizeCellAddress(parts[0]);
    const end = normalizeCellAddress(parts[1]);
    return {address: `${start}:${end}`, start, end};
  }

  function splitCellAddress(address) {
    const match = normalizeCellAddress(address).match(/^([A-Z]+)([1-9][0-9]*)$/);
    return {column: match[1], row: Number(match[2])};
  }

  function rangeCells(address) {
    const range = parseRangeAddress(address);
    const start = splitCellAddress(range.start);
    const end = splitCellAddress(range.end);
    if (start.column !== end.column) return [range.start, range.end];
    const low = Math.min(start.row, end.row);
    const high = Math.max(start.row, end.row);
    const cells = [];
    for (let row = low; row <= high; row += 1) cells.push(`${start.column}${row}`);
    return cells;
  }

  function ensureCell(address) {
    const clean = normalizeCellAddress(address);
    if (!records.cells[clean] || typeof records.cells[clean] !== "object") {
      records.cells[clean] = {value: null, formula: ""};
    }
    return records.cells[clean];
  }

  function numericCellValue(address) {
    const value = ensureCell(address).value;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : 0;
  }

  function calculateFormula(formula) {
    const text = String(formula || "").trim().toUpperCase();
    const match = text.match(/^=?SUM\(([A-Z]+[1-9][0-9]*):([A-Z]+[1-9][0-9]*)\)$/);
    if (!match) return null;
    return rangeCells(`${match[1]}:${match[2]}`).reduce((total, address) => total + numericCellValue(address), 0);
  }

  function makeCell(address) {
    const clean = normalizeCellAddress(address);
    return {
      GetAddress() { return clean; },
      SetValue(value) {
        const cell = ensureCell(clean);
        cell.value = value;
        cell.formula = "";
        return value;
      },
      GetValue() {
        return ensureCell(clean).value;
      },
      SetFormula(formula) {
        const cell = ensureCell(clean);
        const text = String(formula || "");
        cell.formula = text;
        const calculated = calculateFormula(text);
        if (calculated !== null) cell.value = calculated;
        records.formulaExecutions.push({address: clean, formula: text, value: cell.value});
        return cell.value;
      },
      GetFormula() {
        return ensureCell(clean).formula || "";
      },
      AddComment(text, author) {
        records.comments.push({
          text: String(text || ""),
          author: String(author || ""),
          address: clean
        });
      }
    };
  }

  function makeRange(address) {
    const parsed = parseRangeAddress(address);
    return {
      GetAddress() { return parsed.address; },
      ForEach(callback) {
        rangeCells(parsed.address).forEach((cellAddress) => callback(makeCell(cellAddress)));
      },
      AddComment(text, author) {
        records.comments.push({
          text: String(text || ""),
          author: String(author || ""),
          address: parsed.address
        });
      }
    };
  }

  function makeSelection() {
    const selected = records.selectedAddress || "B2:C3";
    return makeRange(selected);
  }

  function makeSheet() {
    return {
      GetSelection() { return makeSelection(); },
      GetActiveCell() {
        const firstAddress = rangeCells(records.selectedAddress || "B2:C3")[0] || "B2";
        return makeCell(firstAddress);
      },
      GetRange(address) {
        const text = String(address || "").trim();
        return text.includes(":") ? makeRange(text) : makeCell(text);
      }
    };
  }

  function runAutomationSource(source) {
    if (!records.__currentEditor || typeof records.__currentEditor.createConnector !== "function") {
      throw new Error("No fake ONLYOFFICE editor is available for automation source execution.");
    }
    const text = String(source || "");
    const match = text.match(/```(?:javascript|js)?\s*([\s\S]*?)```/i);
    let code = (match ? match[1] : text).trim();
    if (!match) {
      const codeStart = code.search(/\b(?:const|let|var)\s+\w+\s*=\s*Api\.GetActiveSheet\s*\(/);
      if (codeStart > 0) code = code.slice(codeStart).trim();
    }
    const command = new Function(code);
    let commandResult = null;
    records.__currentEditor.createConnector().callCommand(command, (result) => {
      commandResult = result;
    });
    return {
      result: commandResult,
      cells: JSON.parse(JSON.stringify(records.cells)),
      selectedAddress: records.selectedAddress,
      formulaExecutions: JSON.parse(JSON.stringify(records.formulaExecutions))
    };
  }

  if (!records.__sheetHelpersInstalled) {
    Object.defineProperties(records, {
      __sheetHelpersInstalled: {value: true, enumerable: false, configurable: true},
      __currentEditor: {value: null, writable: true, enumerable: false, configurable: true},
      setCellValue: {value(address, value) {
        return makeCell(address).SetValue(value);
      }, enumerable: false, configurable: true},
      selectRange: {value(address) {
        records.selectedAddress = parseRangeAddress(address).address;
        records.selectionChanges.push(records.selectedAddress);
        return records.selectedAddress;
      }, enumerable: false, configurable: true},
      cellSnapshot: {value() {
        return {
          cells: JSON.parse(JSON.stringify(records.cells)),
          selectedAddress: records.selectedAddress,
          selectionChanges: JSON.parse(JSON.stringify(records.selectionChanges)),
          formulaExecutions: JSON.parse(JSON.stringify(records.formulaExecutions))
        };
      }, enumerable: false, configurable: true},
      executeAutomationSource: {value(source) {
        return runAutomationSource(source);
      }, enumerable: false, configurable: true}
    });
  }

  window.DocsAPI = {
    DocEditor: function(hostId, config) {
      records.docEditorConstructed += 1;
      records.hostIds.push(hostId);
      records.configs.push(config);
      records.__currentEditor = this;
      this.hostId = hostId;
      this.config = config;
      this.destroyEditor = function() {
        records.destroyed += 1;
      };
      this.createConnector = function() {
        records.connectorCreated += 1;
        return {
          callCommand(command, callback) {
            const previousApi = window.Api;
            window.Api = {
              GetSelection() { return makeSelection(); },
              GetActiveSheet() { return makeSheet(); }
            };
            let result = "";
            try {
              result = command();
            } finally {
              window.Api = previousApi;
            }
            if (typeof callback === "function") callback(result);
          }
        };
      };

      const host = document.getElementById(hostId);
      if (host) {
        host.textContent = "";
        const frame = document.createElement("iframe");
        frame.title = "Fake ONLYOFFICE spreadsheet editor";
        frame.src = "about:blank#fake-onlyoffice-spreadsheet";
        frame.style.width = "720px";
        frame.style.height = "420px";
        host.appendChild(frame);
      }
      return this;
    }
  };
})();
""".strip()

    def do_GET(self) -> None:
        if self.path.startswith("/web-apps/apps/api/documents/api.js"):
            self.server.api_hits += 1  # type: ignore[attr-defined]
            self._send(self.DOCS_API_STUB.encode("utf-8"), content_type="application/javascript; charset=utf-8")
            return
        self._send(b"OK", content_type="text/plain; charset=utf-8")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        self.server.commands.append(payload)  # type: ignore[attr-defined]
        self._send(json.dumps({"error": 0, "key": payload.get("key", "")}).encode("utf-8"))


@pytest.fixture
def fake_ollama_server() -> Iterator[FakeOllamaServer]:
    requests: list[dict] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
    server.requests = requests  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, name="fake-ollama", daemon=True)
    thread.start()
    try:
        yield FakeOllamaServer(url=f"http://127.0.0.1:{server.server_port}", requests=requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def fake_onlyoffice_server() -> Iterator[FakeOnlyOfficeServer]:
    commands: list[dict] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOnlyOfficeHandler)
    server.commands = commands  # type: ignore[attr-defined]
    server.api_hits = 0  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, name="fake-onlyoffice", daemon=True)
    thread.start()
    try:
        yield FakeOnlyOfficeServer(
            url=f"http://127.0.0.1:{server.server_port}",
            commands=commands,
            api_hits=0,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def functional_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("Functional test workspace.\n", encoding="utf-8")
    return workspace


@pytest.fixture
def viewport_app(
    installed_main_computer: InstalledMainComputer,
    functional_workspace: Path,
    fake_ollama_server: FakeOllamaServer,
    fake_onlyoffice_server: FakeOnlyOfficeServer,
    request: pytest.FixtureRequest,
) -> Iterator[RunningViewport]:
    port = _free_port()
    log_root = functional_workspace / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_log = log_root / "viewport.stdout.log"
    stderr_log = log_root / "viewport.stderr.log"
    env = _installed_python_env(installed_main_computer.python)
    env.update(
        {
            "MAIN_COMPUTER_CONTROL_ROOT": str(functional_workspace / "control"),
            "MAIN_COMPUTER_ONLYOFFICE_ENABLED": "1",
            "MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL": fake_onlyoffice_server.url,
            "MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL": fake_onlyoffice_server.url,
            "MAIN_COMPUTER_ONLYOFFICE_DOCUMENT_SERVER_URL": fake_onlyoffice_server.url,
            "MAIN_COMPUTER_ONLYOFFICE_STORAGE_ROOT": str(Path("runtime") / "onlyoffice-functional" / "workbooks"),
            "MAIN_COMPUTER_EXECUTOR_ENABLED": "0",
            "MAIN_COMPUTER_RAG_DOCKER_ENABLED": "0",
        }
    )
    command = [
        str(installed_main_computer.python),
        "-E",
        "-s",
        str(installed_main_computer.main_computer),
        "viewport",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workspace",
        str(functional_workspace),
        "--provider",
        "ollama",
        "--model",
        "functional-fast",
        "--ollama-base-url",
        fake_ollama_server.url,
        "--ollama-timeout-s",
        "5",
        "--noverbose",
    ]
    config = request.config
    _progress(config, "starting installed viewport process")
    _progress(config, f"viewport stdout log: {stdout_log}")
    _progress(config, f"viewport stderr log: {stderr_log}")
    with stdout_log.open("w", encoding="utf-8") as stdout, stderr_log.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            command,
            cwd=str(functional_workspace),
            stdout=stdout,
            stderr=stderr,
            text=True,
            env=env,
        )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_viewport_ready(
            base_url,
            process=process,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            config=config,
        )
        runtime_probe = _post_http_json(
            f"{base_url}/api/applications/calculator/mathics/evaluate",
            {"expression": "2+2", "timeout_s": 1},
            timeout_s=3.0,
        )
        runtime_python_raw = str((runtime_probe.get("diagnostics") or {}).get("python") or "")
        runtime_python = Path(runtime_python_raw).resolve() if runtime_python_raw else Path()
        expected_python = installed_main_computer.python.resolve()
        if runtime_python != expected_python:
            pytest.fail(
                "Installed functional viewport is not running under the isolated Python.\n"
                f"Expected: {expected_python}\n"
                f"Actual: {runtime_python_raw or '<missing>'}\n"
                f"Probe payload: {runtime_probe}",
                pytrace=False,
            )
        _progress(config, f"viewport runtime Python verified: {expected_python}")
        yield RunningViewport(
            base_url=base_url,
            port=port,
            workspace=functional_workspace,
            process=process,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )
    finally:
        _progress(request.config, "shutting down installed viewport process")
        try:
            halt_request = Request(f"{base_url}/system/hard-halt", data=b"{}", method="POST")
            urlopen(halt_request, timeout=1.5).read()
        except Exception:
            pass
        _terminate_process_tree(process)
        _progress(request.config, "installed viewport process stopped")


@pytest.fixture
def playwright_page(
    viewport_app: RunningViewport,
    fake_onlyoffice_server: FakeOnlyOfficeServer,
    fake_ollama_server: FakeOllamaServer,
    request: pytest.FixtureRequest,
):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - depends on local tool install
        pytest.fail(f"Playwright is required for installed functional tests: {exc}")

    config = request.config
    _progress(config, "starting Playwright Chromium")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=os.environ.get("MAIN_COMPUTER_FUNCTIONAL_HEADLESS", "1") != "0")
        except Exception as exc:  # pragma: no cover - depends on local browser install
            pytest.fail(
                "Could not launch Playwright Chromium. Run: python -m playwright install chromium. "
                f"Original error: {exc}",
                pytrace=False,
            )
        context = browser.new_context(ignore_https_errors=True)
        context.set_default_timeout(BROWSER_TIMEOUT_MS)
        context.set_default_navigation_timeout(BROWSER_TIMEOUT_MS)
        page = context.new_page()
        page.set_default_timeout(BROWSER_TIMEOUT_MS)
        page.set_default_navigation_timeout(BROWSER_TIMEOUT_MS)

        # The app page references optional CDN assets used by apps outside this
        # ONLYOFFICE path. Keep this suite network-independent and prevent
        # Windows/browser DNS stalls from looking like a hung test run.
        allowed_prefixes = (
            viewport_app.base_url,
            fake_onlyoffice_server.url,
            fake_ollama_server.url,
            "about:",
            "blob:",
            "data:",
        )

        def offline_external_assets(route, routed_request):
            url = routed_request.url
            if url.startswith(allowed_prefixes):
                route.continue_()
                return
            if "grapesjs" in url and not url.endswith(".css"):
                route.fulfill(
                    status=200,
                    content_type="application/javascript",
                    body="window.grapesjs = window.grapesjs || {init(){return {destroy(){}};}};",
                )
                return
            if url.endswith(".css"):
                route.fulfill(status=200, content_type="text/css", body="")
                return
            route.fulfill(status=200, content_type="application/javascript", body="/* external test asset stub */")

        page.route("**/*", offline_external_assets)
        _progress(config, "Playwright Chromium page ready")

        page_errors: list[str] = []
        local_request_failures: list[str] = []

        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        def on_request_failed(request):
            failure = request.failure
            failure_text = failure or "request failed"
            if request.url.startswith(viewport_app.base_url):
                local_request_failures.append(f"{request.method} {request.url}: {failure_text}")

        page.on("requestfailed", on_request_failed)
        try:
            yield page
            assert not page_errors, "Browser page errors:\n" + "\n".join(page_errors)
            assert not local_request_failures, "Local app request failures:\n" + "\n".join(local_request_failures)
        finally:
            _progress(config, "closing Playwright Chromium")
            context.close()
            browser.close()


@pytest.fixture
def uploaded_xlsx(tmp_path: Path) -> Path:
    path = tmp_path / "UploadedFunctional.xlsx"
    path.write_bytes(_minimal_xlsx_bytes())
    return path
