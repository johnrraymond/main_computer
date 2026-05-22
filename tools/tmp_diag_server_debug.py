import json
import shutil
from pathlib import Path

from main_computer.config import MainComputerConfig
from main_computer.diagnostics import DiagnosticRunner

out = Path("tmp_diag_server_debug")
if out.exists():
    shutil.rmtree(out)

report = DiagnosticRunner(
    config=MainComputerConfig(workspace=Path.cwd().parent),
    level="server",
    output_dir=out,
).run(raise_on_failure=False)

print("ok:", report["ok"])
print("report:", out / "diagnostics_report.json")
print()

for check in report["checks"]:
    status = "PASS" if check["ok"] else "FAIL"
    print(f"[{status}] {check['name']}")
    if not check["ok"]:
        print(json.dumps(check.get("detail"), indent=2, ensure_ascii=False))
