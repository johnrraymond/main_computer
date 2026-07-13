from __future__ import annotations

import hashlib
import html
import json
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


CONTRACT_VERSION = 1


def _safe_slug(value: Any) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip()).strip("-._")
    return slug[:80] or "forge-asset"


def _normalize_color(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else "#7dd3fc"


def _effect_seed(payload: dict[str, Any]) -> str:
    compact = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()[:12]


def _effect_label(payload: dict[str, Any]) -> str:
    selected = payload.get("selected_effect") if isinstance(payload.get("selected_effect"), dict) else {}
    props = selected.get("props") if isinstance(selected.get("props"), dict) else {}
    return str(props.get("label") or selected.get("id") or payload.get("motion") or "GPU Forge Effect")


def _procedural_svg_atlas(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    selected = payload.get("selected_effect") if isinstance(payload.get("selected_effect"), dict) else {}
    props = selected.get("props") if isinstance(selected.get("props"), dict) else {}
    motion = str(payload.get("motion") or props.get("motion") or "effect-atlas").strip() or "effect-atlas"
    color = _normalize_color(payload.get("color") or props.get("color") or "#7dd3fc")
    label = _effect_label(payload)
    digest = _effect_seed(payload)
    frame_count = 12 if motion == "spell-bolt" else 8
    frame_width = 128
    frame_height = 128
    safe_label = html.escape(label)
    safe_motion = html.escape(motion)
    safe_color = html.escape(color)
    safe_digest = html.escape(digest)
    try:
        particle_count = int(float(props.get("particleCount") or payload.get("particle_count") or 24))
    except (TypeError, ValueError):
        particle_count = 24
    particles = max(8, min(48, particle_count // 4))

    frames: list[str] = []
    for index in range(frame_count):
        x = index * frame_width
        phase = index / max(1, frame_count - 1)
        radius = 14 + phase * 48
        ring_opacity = 0.84 - phase * 0.36
        bolt_x = 18 + phase * 82
        bolt_y = 78 - phase * 28
        dash = 12 + index * 3
        dots: list[str] = []
        for particle in range(particles):
            raw = int(hashlib.sha256(f"{digest}:{index}:{particle}".encode("utf-8")).hexdigest()[:8], 16)
            px = 64 + (((raw % 1000) / 1000.0) - 0.5) * (72 + phase * 30)
            py = 64 + ((((raw // 1000) % 1000) / 1000.0) - 0.5) * (54 + phase * 20)
            pr = 1.6 + ((raw // 1000000) % 30) / 10
            po = 0.24 + ((raw // 100000000) % 60) / 100
            dots.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="{pr:.2f}" fill="{safe_color}" opacity="{min(0.94, po):.2f}"/>')
        if motion == "spell-bolt":
            head_x = 24 + phase * 82
            head_y = 76 - phase * 22
            fork_y = 28 + ((index % 4) * 9)
            main_shape = f'''
    <path d="M12 84 L32 58 L48 72 L66 34 L80 56 L112 18" fill="none" stroke="#e0f2fe" stroke-width="13" stroke-linejoin="round" stroke-linecap="round" opacity="0.18"/>
    <path d="M10 86 L30 58 L48 72 L66 34 L80 56 L114 18" fill="none" stroke="{safe_color}" stroke-width="5" stroke-linejoin="round" stroke-linecap="round" opacity="{ring_opacity + 0.12:.2f}"/>
    <path d="M36 62 L54 {fork_y:.2f} L62 66" fill="none" stroke="#fef3c7" stroke-width="3" stroke-linecap="round" opacity="{0.45 + phase * 0.35:.2f}"/>
    <path d="M68 38 L94 {92 - index * 3:.2f} L106 66" fill="none" stroke="#fef3c7" stroke-width="3" stroke-linecap="round" opacity="{0.28 + phase * 0.32:.2f}"/>
    <polygon points="{head_x:.2f},{head_y - 18:.2f} {head_x + 26:.2f},{head_y:.2f} {head_x:.2f},{head_y + 18:.2f} {head_x + 8:.2f},{head_y:.2f}" fill="#e0f2fe" opacity="0.92"/>
    <polygon points="86,24 92,39 108,42 95,52 98,68 84,58 70,66 75,49 62,39 79,38" fill="#facc15" opacity="{0.18 + phase * 0.38:.2f}"/>'''
        elif motion in {"nova-ring", "shockwave-ring", "rune-ring"}:
            main_shape = f'''
    <ellipse cx="64" cy="68" rx="{radius:.2f}" ry="{max(8, radius * 0.38):.2f}" fill="none" stroke="{safe_color}" stroke-width="{max(3, 10 - index):.2f}" opacity="{ring_opacity:.2f}"/>
    <ellipse cx="64" cy="68" rx="{max(4, radius * 0.62):.2f}" ry="{max(3, radius * 0.22):.2f}" fill="none" stroke="#e0f2fe" stroke-width="2" opacity="{max(0.18, ring_opacity - 0.22):.2f}"/>'''
        elif motion == "starfall":
            main_shape = f'''
    <path d="M32 {24 + index * 4} L52 {78 + index * 2}" stroke="{safe_color}" stroke-width="4" stroke-linecap="round" opacity="0.8"/>
    <path d="M76 {10 + index * 6} L96 {72 + index * 2}" stroke="{safe_color}" stroke-width="5" stroke-linecap="round" opacity="0.74"/>
    <circle cx="52" cy="{78 + index * 2}" r="6" fill="{safe_color}" opacity="0.85"/>
    <circle cx="96" cy="{72 + index * 2}" r="8" fill="{safe_color}" opacity="0.7"/>'''
        else:
            main_shape = f'''
    <circle cx="64" cy="64" r="{radius:.2f}" fill="none" stroke="{safe_color}" stroke-width="{max(3, 9 - index):.2f}" opacity="{ring_opacity:.2f}"/>
    <path d="M28 {92 - index * 5} C 46 {24 + index * 6}, 84 {100 - index * 4}, 102 {32 + index * 7}" fill="none" stroke="{safe_color}" stroke-width="4" stroke-linecap="round" opacity="{max(0.3, ring_opacity):.2f}"/>'''
        frames.append(f'''
  <g transform="translate({x} 0)" data-frame="{index + 1}">
    <rect x="0" y="0" width="128" height="128" rx="18" fill="#020617" opacity="0.18"/>
    <circle cx="64" cy="64" r="58" fill="url(#forge-glow-{safe_digest})" opacity="{0.32 + phase * 0.24:.2f}"/>
    {main_shape}
    {''.join(dots)}
    <text x="64" y="119" text-anchor="middle" font-family="monospace" font-size="8" fill="#e2e8f0" opacity="0.68">{index + 1}</text>
  </g>''')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{frame_width * frame_count}" height="{frame_height}" viewBox="0 0 {frame_width * frame_count} {frame_height}" role="img" aria-label="{safe_label} GPU forge atlas">
  <title>{safe_label} · {safe_motion} · forge {safe_digest}</title>
  <desc>Procedural sprite atlas generated by the Main Computer Game Renderer contract. The browser plays this as a local effect layer; the renderer never repaints the full game screen.</desc>
  <defs>
    <radialGradient id="forge-glow-{safe_digest}" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{safe_color}" stop-opacity="0.82"/>
      <stop offset="48%" stop-color="{safe_color}" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="{safe_color}" stop-opacity="0"/>
    </radialGradient>
  </defs>
{''.join(frames)}
</svg>
'''
    metadata = {
        "ok": True,
        "kind": "game-gpu-forge-effect-atlas",
        "contract_version": CONTRACT_VERSION,
        "backend": f"container-{os.environ.get('GAME_RENDERER_BACKEND', 'procedural-svg')}",
        "renderer_mode": os.environ.get("GAME_RENDERER_MODE", "smoke"),
        "project_id": str(payload.get("project_id") or ""),
        "scene_id": str(payload.get("scene_id") or ""),
        "effect_id": str(payload.get("effect_id") or ""),
        "effect_label": label,
        "effect_motion": motion,
        "effect_color": color,
        "digest": digest,
        "frame_count": frame_count,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "atlas_columns": frame_count,
        "atlas_rows": 1,
        "live_stream_required": False,
        "playback": "storm-lash" if motion == "spell-bolt" else "sprite-sheet",
    }
    return svg, metadata


class GameRendererHandler(BaseHTTPRequestHandler):
    server_version = "MainComputerGameRenderer/0.2"

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                {
                    "ok": True,
                    "mode": os.environ.get("GAME_RENDERER_MODE", "smoke"),
                    "backend": os.environ.get("GAME_RENDERER_BACKEND", "procedural-svg"),
                    "contract": "game-gpu-forge",
                    "contract_version": CONTRACT_VERSION,
                    "screen_repaint": False,
                    "effect_atlas_bake": True,
                    "background_plate_bake": False,
                    "live_stream_required": False,
                }
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            if self.path == "/bake/effect-atlas":
                payload = self._read_json()
                atlas_svg, metadata = _procedural_svg_atlas(payload)
                self._send_json(
                    {
                        "ok": True,
                        "mode": os.environ.get("GAME_RENDERER_MODE", "smoke"),
                        "backend": os.environ.get("GAME_RENDERER_BACKEND", "procedural-svg"),
                        "contract": "game-gpu-forge-effect-atlas",
                        "contract_version": CONTRACT_VERSION,
                        "live_stream_required": False,
                        "metadata": metadata,
                        "atlas": {
                            "media_type": "image/svg+xml",
                            "extension": ".svg",
                            "text": atlas_svg,
                            "encoding": "utf-8",
                        },
                    }
                )
                return
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    host = os.environ.get("GAME_RENDERER_BIND", "0.0.0.0")
    port = int(os.environ.get("GAME_RENDERER_PORT", "8798"))
    server = ThreadingHTTPServer((host, port), GameRendererHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
