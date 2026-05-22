from __future__ import annotations

from main_computer.viewport_routes_ollama_debug import ViewportOllamaDebugRoutesMixin
from main_computer.viewport_routes_debug_assets import ViewportDebugAssetRoutesMixin
from main_computer.viewport_routes_revision import ViewportRevisionRoutesMixin


class ViewportDebugRoutesMixin(
    ViewportOllamaDebugRoutesMixin,
    ViewportDebugAssetRoutesMixin,
    ViewportRevisionRoutesMixin,
):
    pass
