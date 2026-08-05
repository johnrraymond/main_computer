#!/usr/bin/env python3
"""Compatibility wrapper for the generic MCEL app IR-native proof authority."""
from __future__ import annotations
import sys
from pathlib import Path
if __package__ in {None, ""}: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.mcel_app_ir_native_prove import main as generic_main


def main(argv=None):
    args=list(argv if argv is not None else sys.argv[1:])
    return generic_main(["--app","contract-counter",*args])

if __name__=="__main__": raise SystemExit(main())
