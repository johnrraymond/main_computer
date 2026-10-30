"""Compatibility entry point: the 14.7.6.3 collection-hover contract is retired.
The correct 14.7.7 gate proves *individual original rows* own hover.
"""
from tools.mcel_row_atomic_hover_20_frob import verify

if __name__ == '__main__':
    raise SystemExit(0 if verify()['status'] == 'PASS' else 1)
