"""Check native MCEL Calculator host generation without rewriting HTML."""
from pathlib import Path
import argparse
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from main_computer.mcel_calculator_host_projection import project

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.parse_args()
    output = project(ROOT)
    if not output.strip(): raise SystemExit("FAIL: Empty Calculator host")
    print("PASS: Native MCEL Calculator host projection")

if __name__ == "__main__": main()
