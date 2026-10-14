"""Process-local activation of cached FoundationDB native-client directories.

Each FDB ledger/store can be constructed hundreds of times during one process.
PATH and Windows DLL search-directory registration must not grow per instance.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

_DLL_DIRECTORY_HANDLES: dict[str, Any] = {}
_DLL_DIRECTORY_LOCK = threading.Lock()


def activate_native_client_directory(directory: str | Path) -> None:
    """Register an FDB client directory at most once per process.

    Keep Windows add_dll_directory handles alive: discarding/closing a handle
    removes its registration, and calling the API on every ledger connection
    can exhaust Windows' directory-search resources.
    """
    native_dir = str(directory)
    normalized_native = os.path.normcase(os.path.normpath(native_dir))
    with _DLL_DIRECTORY_LOCK:
        current_path = os.environ.get("PATH", "")
        if not any(
            os.path.normcase(os.path.normpath(entry.strip('"'))) == normalized_native
            for entry in current_path.split(os.pathsep) if entry
        ):
            os.environ["PATH"] = native_dir + (os.pathsep + current_path if current_path else "")
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            if normalized_native not in _DLL_DIRECTORY_HANDLES:
                # Only commit the cache entry once Windows has accepted it.
                _DLL_DIRECTORY_HANDLES[normalized_native] = os.add_dll_directory(native_dir)
