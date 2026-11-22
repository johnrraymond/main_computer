"""Run File Explorer incumbent parity, integrated signals, class and rollback acceptance.

Usage:
  python -m tools.mcel_file_manager_full_functional_acceptance --mode isolated --profile full --signals integrated

The existing mcel_file_manager_functional_parity_frob command remains valid,
but alone it cannot certify the signal scheduler.
"""
from tools.mcel_file_manager_signal_acceptance import main

if __name__ == '__main__':
    raise SystemExit(main())
