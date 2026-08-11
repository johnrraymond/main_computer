#!/usr/bin/env python3
"""Retired Allfather entrypoint.

Allfather lifecycle scripts are intentionally disabled. The Mother control
surface is the supported lifecycle authority.
"""

from __future__ import annotations

import sys


_ERROR_MESSAGE = 'ERROR: Allfather scripts are retired. Allfather has been replaced by the Mother scripts. Use tools/mother_deploy.py and tools/mother/* for lifecycle operations.'


def main() -> int:
    print(_ERROR_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
