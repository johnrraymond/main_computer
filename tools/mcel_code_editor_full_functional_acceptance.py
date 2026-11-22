"""Run Code Editor functional parity, signal differential and rollback acceptance.

    python -m tools.mcel_code_editor_full_functional_acceptance --mode isolated --profile full --signals integrated

This cannot enable production signals or perform real writes.
"""
from tools.mcel_code_editor_signal_acceptance import main
if __name__=='__main__':raise SystemExit(main())
