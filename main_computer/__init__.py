"""Main local AI computer router.

Keep package import light.  Console modules are commonly executed with
``python -m main_computer.<module>``; importing the router here eagerly can
preload those submodules before ``runpy`` executes them and causes noisy
RuntimeWarning messages.  Expose the historical package attributes lazily
instead.
"""

from __future__ import annotations

from main_computer.config import MainComputerConfig

__all__ = ["MainComputer", "MainComputerConfig"]


def __getattr__(name: str):
    if name == "MainComputer":
        from main_computer.router import MainComputer

        return MainComputer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
