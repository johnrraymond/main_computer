"""Training-only fixed Hawk–Dove teacher binding.

Source: the established million-count T08→T00 curriculum, NOT model input.
In the source-export snapshot the original demo-only fixture was absent; this
file records the explicit published historical payoff values instead of
pretending to restore an unverifiable fixture. Only acquisition.train imports
this module. Never import this module from inference or a checkpoint loader.
"""
from __future__ import annotations

DOCUMENTED_DEMO_BINDINGS = ('-1', '3', '0', '1.5')
