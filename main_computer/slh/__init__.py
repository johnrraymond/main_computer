"""Secure Local Hub infrastructure.

Backend-only Phase 1: connection registry and SEH Coolify controller.
No worker admission or relay transport is exposed until device-auth and E2E
transport implementations pass their dedicated acceptance gates.
"""
from .control import SLHControlBackend
from .authority import SLHAuthority, SLHError
from .api import SLHAPI
from .config import SEHTarget, ConnectionProfile, load_connection_profiles
from .coolify import SEHDeployer, SEHDeploymentError

__all__ = ["SLHControlBackend", "SLHAuthority", "SLHError", "SLHAPI", "SEHTarget", "ConnectionProfile", "load_connection_profiles", "SEHDeployer", "SEHDeploymentError"]
