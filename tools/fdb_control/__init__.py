"""FoundationDB control-surface package.

The package is intentionally named ``fdb_control`` so it cannot shadow the
FoundationDB Python client package named ``fdb``.
"""

from .common.errors import FdbControlError

__all__ = ["FdbControlError"]
