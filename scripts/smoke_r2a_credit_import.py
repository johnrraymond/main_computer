"""Retired unsafe funding smoke (fail-closed compatibility entry point)."""
from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    print("The unverified receipt-funding smoke is retired. "
          "Use authenticated, chain-verified bridge funding completion.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
