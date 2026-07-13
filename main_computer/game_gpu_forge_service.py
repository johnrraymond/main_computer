from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_GAME_RENDERER_PORT = 8798
DEFAULT_GAME_RENDERER_HOST = "127.0.0.1"
COMPOSE_FILENAME = "docker-compose.game-renderer.yml"
COMPOSE_PROJECT_NAME = "main-computer-game-renderer"
CONTAINER_NAME = "main-computer-game-renderer"
IMAGE_NAME = "main-computer/game-renderer:local"
CONTRACT_VERSION = 1
PREBUILT_EFFECT_ATLAS_SCENE_ID = "default-empty-scene"
PREBUILT_EFFECT_ATLAS_EFFECT_ID = "arcstorm-nova"
PREBUILT_EFFECT_ATLAS_PATH = "gpu-forge/default-empty-scene-arcstorm-nova-prebuilt.svg"
PREBUILT_EFFECT_ATLAS_METADATA_PATH = "gpu-forge/default-empty-scene-arcstorm-nova-prebuilt.json"

SUPPORTED_EFFECT_MOTIONS = {
    "spell-bolt",
    "spell-swirl",
    "nova-ring",
    "shockwave-ring",
    "impact-burst",
    "starfall",
    "stream",
}


@dataclass(frozen=True)
class GameGpuForgeBakeResult:
    """Full result returned after materializing a browser-playable forge asset."""

    metadata_path: Path
    atlas_path: Path
    metadata: dict[str, Any]
    atlas_text: str


class GameGpuForgeError(RuntimeError):
    """Raised when the Game GPU Forge cannot produce a safe asset payload."""


class GameGpuForgeService:
    """Game GPU forge / visual coprocessor contract.

    The browser remains the authoritative game surface.  This service talks to the
    optional game-renderer sidecar when it is running, then writes returned atlases
    into normal project assets.  If the sidecar is absent, it safely falls back to
    the same deterministic local SVG generator so the editor workflow still works
    offline and in tests.
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.host = os.environ.get("GAME_RENDERER_HOST", DEFAULT_GAME_RENDERER_HOST).strip() or DEFAULT_GAME_RENDERER_HOST
        self.port = int(os.environ.get("GAME_RENDERER_PORT", str(DEFAULT_GAME_RENDERER_PORT)) or DEFAULT_GAME_RENDERER_PORT)
        self.compose_file = self.repo_root / COMPOSE_FILENAME
        self.request_timeout = float(os.environ.get("GAME_RENDERER_REQUEST_TIMEOUT", "0.45") or "0.45")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def bind_repo_root(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.compose_file = self.repo_root / COMPOSE_FILENAME

    def status(self) -> dict[str, Any]:
        health = self.renderer_health()
        return {
            "ok": True,
            "mode": "game-gpu-forge",
            "contract_version": CONTRACT_VERSION,
            "compose_file": str(self.compose_file),
            "compose_present": self.compose_file.exists(),
            "renderer": {
                "host": self.host,
                "port": self.port,
                "base_url": self.base_url,
                "container": CONTAINER_NAME,
                "compose_project": COMPOSE_PROJECT_NAME,
                "image": IMAGE_NAME,
                "live_stream_required": False,
                **health,
            },
            "capabilities": {
                "screen_repaint": False,
                "effect_atlas_bake": True,
                "background_plate_bake": False,
                "lighting_map_bake": False,
                "local_fallback_backend": True,
                "sidecar_renderer_backend": True,
                "browser_atlas_playback": True,
                "prebuilt_demo_atlas": True,
            },
            "endpoints": {
                "status": "/api/applications/game-editor/gpu-forge/status",
                "bake_effect_atlas": "/api/applications/game-editor/gpu-forge/bake-effect-atlas",
                "container_health": "/health",
                "container_bake_effect_atlas": "/bake/effect-atlas",
            },
        }

    def renderer_health(self, *, timeout: float | None = None) -> dict[str, Any]:
        try:
            payload = self._json_request("GET", "/health", timeout=timeout)
            return {
                "reachable": bool(payload.get("ok")),
                "health": payload,
                "error": "",
            }
        except Exception as exc:
            return {
                "reachable": False,
                "health": None,
                "error": str(exc),
            }


    def apply_prebuilt_atlas_binding(
        self,
        *,
        project_id: str,
        project: dict[str, Any],
        assets_root: Path,
    ) -> dict[str, Any]:
        """Return a project payload with the built-in atlas prelinked when available.

        This is intentionally a read-time overlay: the game can come up with a
        browser-playable forge atlas immediately, without forcing the user to open
        the editor and run a bake first.  Saving the project later may persist the
        binding, but simply reading the project does not rewrite project.json.
        """

        if not isinstance(project, dict):
            return project
        bound_project = copy.deepcopy(project)
        assets_root = Path(assets_root)
        atlas_file = assets_root / PREBUILT_EFFECT_ATLAS_PATH
        metadata_file = assets_root / PREBUILT_EFFECT_ATLAS_METADATA_PATH
        if not atlas_file.is_file() or not metadata_file.is_file():
            return bound_project
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
        binding = self._prebuilt_atlas_binding(metadata)
        scenes = [scene for scene in bound_project.get("scenes", []) if isinstance(scene, dict)] if isinstance(bound_project.get("scenes"), list) else []
        target_object: dict[str, Any] | None = None
        target_scene: dict[str, Any] | None = None
        for scene in scenes:
            if str(scene.get("id") or "") != PREBUILT_EFFECT_ATLAS_SCENE_ID:
                continue
            objects = [obj for obj in scene.get("objects", []) if isinstance(obj, dict)] if isinstance(scene.get("objects"), list) else []
            target_object = next((obj for obj in objects if str(obj.get("id") or "") == PREBUILT_EFFECT_ATLAS_EFFECT_ID), None)
            if target_object is None:
                target_object = next((obj for obj in objects if str(obj.get("type") or "") == "particle-emitter"), None)
            if target_object is not None:
                target_scene = scene
                break
        if target_object is None:
            return bound_project
        props = target_object.get("props") if isinstance(target_object.get("props"), dict) else {}
        target_object["props"] = props
        existing = props.get("gpuForgeAtlas")
        existing_path = ""
        if isinstance(existing, dict):
            existing_path = str(existing.get("path") or "")
        elif existing:
            existing_path = str(existing)
        if not existing_path:
            props["gpuForgeAtlas"] = binding
            props["gpuForgePlayback"] = "sprite-sheet"
            props["gpuForgeSource"] = "prebuilt"
        project_metadata = bound_project.get("metadata") if isinstance(bound_project.get("metadata"), dict) else {}
        bound_project["metadata"] = project_metadata
        project_metadata["gpuForgePrebuilt"] = {
            "enabled": True,
            "project_id": project_id,
            "scene_id": str(target_scene.get("id") or PREBUILT_EFFECT_ATLAS_SCENE_ID) if target_scene else PREBUILT_EFFECT_ATLAS_SCENE_ID,
            "effect_id": str(target_object.get("id") or PREBUILT_EFFECT_ATLAS_EFFECT_ID),
            "atlas_path": PREBUILT_EFFECT_ATLAS_PATH,
            "metadata_path": PREBUILT_EFFECT_ATLAS_METADATA_PATH,
            "live_stream_required": False,
            "browser_playback": "sprite-sheet",
        }
        return bound_project

    def _prebuilt_atlas_binding(self, metadata: dict[str, Any]) -> dict[str, Any]:
        binding = metadata.get("browser_binding", {}).get("particle_emitter_props", {}).get("gpuForgeAtlas") if isinstance(metadata, dict) else {}
        if not isinstance(binding, dict):
            binding = {}
        return {
            "path": str(binding.get("path") or metadata.get("atlas_path") or PREBUILT_EFFECT_ATLAS_PATH),
            "metadataPath": str(binding.get("metadataPath") or metadata.get("metadata_path") or PREBUILT_EFFECT_ATLAS_METADATA_PATH),
            "frameCount": int(binding.get("frameCount") or metadata.get("frame_count") or 8),
            "frameWidth": int(binding.get("frameWidth") or metadata.get("frame_width") or 128),
            "frameHeight": int(binding.get("frameHeight") or metadata.get("frame_height") or 128),
            "columns": int(binding.get("columns") or metadata.get("atlas_columns") or 8),
            "rows": int(binding.get("rows") or metadata.get("atlas_rows") or 1),
            "digest": str(binding.get("digest") or metadata.get("digest") or "prebuilt-arcstorm"),
            "backend": str(binding.get("backend") or metadata.get("backend") or "prebuilt-game-gpu-forge-atlas"),
            "playback": str(binding.get("playback") or "sprite-sheet"),
            "prebuilt": True,
        }

    def bake_effect_atlas(
        self,
        *,
        project_id: str,
        project: dict[str, Any],
        scene_id: str = "",
        effect_id: str = "",
        output_root: Path,
    ) -> GameGpuForgeBakeResult:
        if not isinstance(project, dict):
            raise GameGpuForgeError("project must be an object.")
        scene = self._select_scene(project, scene_id)
        effect = self._select_effect(scene, effect_id)
        bake_input = self._bake_input(project_id=project_id, project=project, scene=scene, effect=effect, scene_id=scene_id)
        scene_key = self._safe_slug(scene.get("id") or scene_id or "scene")
        effect_key = self._safe_slug(effect.get("id") or effect.get("motion") or "scene-vfx")

        renderer_payload = self._try_renderer_bake(bake_input)
        if renderer_payload.get("ok") and isinstance(renderer_payload.get("metadata"), dict):
            atlas_text = str((renderer_payload.get("atlas") or {}).get("text") or "")
            if not atlas_text.lstrip().startswith("<svg"):
                raise GameGpuForgeError("renderer returned an invalid effect atlas payload.")
            metadata = dict(renderer_payload["metadata"])
            renderer_used = True
            renderer_error = ""
        else:
            atlas_text, metadata = self._local_svg_atlas(bake_input)
            renderer_used = False
            renderer_error = str(renderer_payload.get("error") or "")

        digest = str(metadata.get("digest") or self._digest(bake_input))
        asset_dir = output_root / "gpu-forge"
        asset_dir.mkdir(parents=True, exist_ok=True)
        atlas_path = asset_dir / f"{scene_key}-{effect_key}-{digest}.svg"
        metadata_path = asset_dir / f"{scene_key}-{effect_key}-{digest}.json"
        backend = str(metadata.get("backend") or ("container-procedural-svg" if renderer_used else "local-procedural-svg-fallback"))
        frame_count = int(metadata.get("frame_count") or 8)
        frame_width = int(metadata.get("frame_width") or 128)
        frame_height = int(metadata.get("frame_height") or 128)
        atlas_columns = int(metadata.get("atlas_columns") or frame_count)
        atlas_rows = int(metadata.get("atlas_rows") or 1)

        metadata.update(
            {
                "ok": True,
                "kind": "game-gpu-forge-effect-atlas",
                "contract_version": CONTRACT_VERSION,
                "project_id": project_id,
                "scene_id": str(scene.get("id") or scene_id or ""),
                "scene_name": str(scene.get("name") or ""),
                "effect_id": str(effect.get("id") or ""),
                "effect_label": str(metadata.get("effect_label") or self._effect_label(effect, bake_input)),
                "effect_motion": str(metadata.get("effect_motion") or bake_input.get("motion") or ""),
                "effect_color": str(metadata.get("effect_color") or bake_input.get("color") or "#7dd3fc"),
                "digest": digest,
                "frame_count": frame_count,
                "frame_width": frame_width,
                "frame_height": frame_height,
                "atlas_columns": atlas_columns,
                "atlas_rows": atlas_rows,
                "atlas_path": f"gpu-forge/{atlas_path.name}",
                "metadata_path": f"gpu-forge/{metadata_path.name}",
                "backend": backend,
                "renderer": {
                    "used": renderer_used,
                    "base_url": self.base_url,
                    "container": CONTAINER_NAME,
                    "error": renderer_error,
                },
                "future_backend": "game-renderer-container-gpu",
                "browser_usage": "Link metadata.browser_binding.particle_emitter_props onto a particle-emitter object. The scene viewer plays the atlas locally; the backend never repaints the whole game screen.",
                "browser_binding": {
                    "target": "particle-emitter.props.gpuForgeAtlas",
                    "particle_emitter_props": {
                        "gpuForgeAtlas": {
                            "path": f"gpu-forge/{atlas_path.name}",
                            "metadataPath": f"gpu-forge/{metadata_path.name}",
                            "frameCount": frame_count,
                            "frameWidth": frame_width,
                            "frameHeight": frame_height,
                            "columns": atlas_columns,
                            "rows": atlas_rows,
                            "digest": digest,
                            "backend": backend,
                            "playback": "sprite-sheet",
                        }
                    },
                },
                "container_contract": {
                    "compose_file": COMPOSE_FILENAME,
                    "service": "game-renderer",
                    "health": "/health",
                    "bake_effect_atlas": "/bake/effect-atlas",
                    "live_stream_required": False,
                },
                "source": {
                    "project_name": str(project.get("name") or project_id),
                    "effect_object": effect,
                },
            }
        )

        atlas_path.write_text(atlas_text, encoding="utf-8")
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return GameGpuForgeBakeResult(metadata_path=metadata_path, atlas_path=atlas_path, metadata=metadata, atlas_text=atlas_text)

    def _bake_input(self, *, project_id: str, project: dict[str, Any], scene: dict[str, Any], effect: dict[str, Any], scene_id: str) -> dict[str, Any]:
        props = effect.get("props") if isinstance(effect.get("props"), dict) else {}
        color = self._normalize_color(props.get("color") or "#7dd3fc")
        motion = str(props.get("motion") or effect.get("motion") or "effect-atlas").strip() or "effect-atlas"
        return {
            "contract_version": CONTRACT_VERSION,
            "project_id": project_id,
            "project_name": str(project.get("name") or project_id),
            "scene_id": scene.get("id") or scene_id,
            "scene_name": str(scene.get("name") or ""),
            "effect_id": effect.get("id") or "",
            "motion": motion,
            "color": color,
            "particle_count": props.get("particleCount"),
            "selected_effect": effect,
        }

    def _try_renderer_bake(self, bake_input: dict[str, Any]) -> dict[str, Any]:
        disabled = str(os.environ.get("GAME_RENDERER_DISABLE_SIDECAR", "")).strip().lower()
        if disabled in {"1", "true", "yes", "on"}:
            return {"ok": False, "error": "sidecar disabled by GAME_RENDERER_DISABLE_SIDECAR"}
        try:
            payload = self._json_request("POST", "/bake/effect-atlas", payload=bake_input)
            if not payload.get("ok"):
                return {"ok": False, "error": str(payload.get("error") or "renderer rejected bake request")}
            atlas = payload.get("atlas") if isinstance(payload.get("atlas"), dict) else {}
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            if not str(atlas.get("text") or "").lstrip().startswith("<svg"):
                return {"ok": False, "error": "renderer did not return SVG atlas text"}
            return {"ok": True, "atlas": atlas, "metadata": metadata, "raw": payload}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _json_request(self, method: str, path: str, *, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, method=method.upper(), headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.request_timeout if timeout is None else timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GameGpuForgeError(f"{method.upper()} {path} failed with HTTP {exc.code}: {body}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise GameGpuForgeError(f"{method.upper()} {path} failed: {exc}") from exc
        if not isinstance(parsed, dict):
            raise GameGpuForgeError(f"{method.upper()} {path} returned non-object JSON.")
        return parsed

    def _local_svg_atlas(self, bake_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        motion = str(bake_input.get("motion") or "effect-atlas")
        color = self._normalize_color(bake_input.get("color") or "#7dd3fc")
        effect = bake_input.get("selected_effect") if isinstance(bake_input.get("selected_effect"), dict) else {}
        label = self._effect_label(effect, bake_input)
        digest = self._digest(bake_input)
        safe_label = html.escape(label)
        safe_motion = html.escape(motion)
        safe_color = html.escape(color)
        frame_count = 8
        frames = []
        for index in range(frame_count):
            x = index * 128
            phase = index / max(1, frame_count - 1)
            radius = 16 + phase * 46
            opacity = 0.34 + phase * 0.42
            frames.append(
                f"""
  <g transform="translate({x} 0)" data-frame="{index + 1}">
    <rect x="0" y="0" width="128" height="128" rx="18" fill="#020617" opacity="0.2"/>
    <circle cx="64" cy="64" r="{radius:.2f}" fill="none" stroke="{safe_color}" stroke-width="{max(3, 10 - index):.2f}" opacity="{opacity:.2f}"/>
    <circle cx="{36 + index * 8}" cy="{76 - index * 5}" r="{7 + index * 0.65:.2f}" fill="{safe_color}" opacity="{min(0.95, opacity + 0.2):.2f}"/>
    <path d="M22 {92 - index * 6} C 48 {18 + index * 5}, 80 {104 - index * 7}, 106 {34 + index * 10}" fill="none" stroke="{safe_color}" stroke-width="4" stroke-linecap="round" opacity="{opacity:.2f}"/>
    <text x="64" y="119" text-anchor="middle" font-family="monospace" font-size="8" fill="#e2e8f0" opacity="0.7">{index + 1}</text>
  </g>"""
            )
        atlas_text = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="128" viewBox="0 0 1024 128" role="img" aria-label="{safe_label} local GPU forge atlas">
  <title>{safe_label} · {safe_motion} · forge {digest}</title>
  <desc>Local fallback sprite atlas generated by the Main Computer Game GPU Forge contract. A running game-renderer sidecar can replace this with a container-generated texture while preserving the same browser-side contract.</desc>
{''.join(frames)}
</svg>
"""
        metadata = {
            "ok": True,
            "kind": "game-gpu-forge-effect-atlas",
            "contract_version": CONTRACT_VERSION,
            "backend": "local-procedural-svg-fallback",
            "renderer_mode": "local-fallback",
            "effect_label": label,
            "effect_motion": motion,
            "effect_color": color,
            "digest": digest,
            "frame_count": frame_count,
            "frame_width": 128,
            "frame_height": 128,
            "atlas_columns": frame_count,
            "atlas_rows": 1,
            "live_stream_required": False,
        }
        return atlas_text, metadata

    def _effect_label(self, effect: dict[str, Any], bake_input: dict[str, Any]) -> str:
        props = effect.get("props") if isinstance(effect.get("props"), dict) else {}
        return str(props.get("label") or effect.get("id") or bake_input.get("motion") or "GPU Forge Effect").strip() or "GPU Forge Effect"

    def _digest(self, payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]

    def _select_scene(self, project: dict[str, Any], scene_id: str) -> dict[str, Any]:
        scenes = [scene for scene in project.get("scenes", []) if isinstance(scene, dict)] if isinstance(project.get("scenes"), list) else []
        if not scenes:
            raise GameGpuForgeError("project has no scenes to bake.")
        wanted = str(scene_id or project.get("activeSceneId") or "").strip()
        if wanted:
            match = next((scene for scene in scenes if str(scene.get("id") or "") == wanted), None)
            if match is not None:
                return match
        return scenes[0]

    def _select_effect(self, scene: dict[str, Any], effect_id: str) -> dict[str, Any]:
        objects = [obj for obj in scene.get("objects", []) if isinstance(obj, dict)] if isinstance(scene.get("objects"), list) else []
        wanted = str(effect_id or "").strip()
        if wanted:
            match = next((obj for obj in objects if str(obj.get("id") or "") == wanted), None)
            if match is not None:
                return match
        for obj in objects:
            props = obj.get("props") if isinstance(obj.get("props"), dict) else {}
            if str(obj.get("type") or "") == "particle-emitter" and str(props.get("motion") or "") in SUPPORTED_EFFECT_MOTIONS:
                return obj
        if objects:
            return objects[0]
        return {"id": "scene-vfx", "type": "scene", "props": {"label": str(scene.get("name") or "Scene VFX"), "motion": "scene-vfx", "color": "#7dd3fc"}}

    def _normalize_color(self, value: Any) -> str:
        text = str(value or "").strip()
        return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else "#7dd3fc"

    def _safe_slug(self, value: Any) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip()).strip("-._")
        return slug[:80] or "forge-asset"
