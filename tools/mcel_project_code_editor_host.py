"""Validate native MCEL Code Editor UI projection without rewriting HTML."""
from pathlib import Path
import argparse
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from main_computer.mcel_code_editor_host_projection import project

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.parse_args()
    output = project(ROOT)
    if not output.strip():
        raise SystemExit("FAIL: Empty Code Editor host")
    print("PASS: Native MCEL Code Editor host projection")

if __name__ == "__main__":
    main()
