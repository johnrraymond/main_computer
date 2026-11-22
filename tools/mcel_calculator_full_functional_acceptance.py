"""Run Calculator independent parity, signal differential, rollback and class certification.

Usage:
  python -m tools.mcel_calculator_full_functional_acceptance --mode isolated --profile full --signals integrated

A non-green independent legacy Calculator parity blocks overall certification.
Calculator's production scheduler default is not modified.
"""
from tools.mcel_calculator_signal_acceptance import main
if __name__=='__main__':raise SystemExit(main())
