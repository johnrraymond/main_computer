"""Independent browser regression of source identity and shared interaction ownership."""
import json
import subprocess
import sys
from pathlib import Path


def test_mcel_patch14_7_4_lifecycle_integrity(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'mcel1474' / 'report.json'
    completed = subprocess.run(
        [sys.executable, '-m', 'tools.mcel_patch14_7_4_lifecycle_integrity_frob',
         '--repo', str(root), '--output', str(output)],
        cwd=root, capture_output=True, text=True, timeout=90, check=False,
    )
    assert completed.returncode == 0, completed.stdout + '\n' + completed.stderr
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['failed'] == 0, report
    assert report['passed'] == 8, report
    assert all(not case['details'].get('browserErrors') for case in report['cases'])
