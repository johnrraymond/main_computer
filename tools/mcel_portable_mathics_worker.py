"""Isolated Mathics3 invocation for explicitly authorized portable Calculator work."""
from __future__ import annotations

import json
import sys

from main_computer.mathics_bridge import evaluate_mathics_expression
from main_computer.mcel_portable_calculator_external import validate_mathics_expression


def main() -> int:
    try:
        raw = sys.stdin.read(8193)
        if len(raw) > 8192:
            raise ValueError('worker-input-too-long')
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) != {'expression'}:
            raise ValueError('invalid-worker-request')
        expr = validate_mathics_expression(request['expression'])
        result = evaluate_mathics_expression(expr)
        print(json.dumps({k:result.get(k) for k in ('ok','expression','result_text','errors')},ensure_ascii=False))
        return 0 if result.get('ok') is True else 4
    except Exception as exc:
        print(json.dumps({'ok':False,'error':type(exc).__name__}), file=sys.stderr)
        return 4


if __name__ == '__main__':
    raise SystemExit(main())
