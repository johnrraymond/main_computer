# Astrometric 3D GPU Renderer

This directory contains the C++ renderer used by the Main Computer **Astrometric 3D** application.

## Runtime contract

The renderer intentionally keeps all EGL/OpenGL calls on the renderer thread. The HTTP server runs on a separate thread so `/health`, `/camera`, and MJPEG clients stay responsive while the GPU is tracing frames.


Main Computer owns the browser-facing API. The C++ process only listens inside the Docker-bound loopback port.

- `GET /health` returns renderer, GPU/OpenGL, frame, and camera state.
- `GET /frame.jpg` returns the latest rendered frame.
- `GET /stream.mjpg` streams rendered frames as MJPEG.
- `POST /camera` accepts mouse/camera control JSON:
  - `{ "type": "orbit", "dx": 12, "dy": -4 }`
  - `{ "type": "pan", "dx": 10, "dy": 8, "shift": true }`
  - `{ "type": "zoom", "deltaY": -120 }`
  - `{ "type": "reset" }`
  - `{ "type": "quality", "jpeg_quality": 86 }`

The shader in `shaders/astrometric_service.comp` is adapted from the uploaded `black_hole-main/geodesic.comp`
source: it keeps the Schwarzschild null-geodesic initialization, RHS integration, event-horizon intercept, and
accretion-disk crossing model, but exposes uniform inputs suitable for a backend camera.

## Tuning knobs

The compose file defaults to an interactive startup profile: 800×450 at 12 FPS with `ASTROMETRIC_RENDERER_IDLE_STEPS=960` and `ASTROMETRIC_RENDERER_MOVING_STEPS=520`. Increase those environment variables for slower, higher-quality traces after the stream is stable.

## GPU container assumptions

`docker-compose.astrometric.yml` requests:

- `gpus: all`
- `NVIDIA_VISIBLE_DEVICES=all`
- `NVIDIA_DRIVER_CAPABILITIES=graphics,compute,utility`

The host still needs a working GPU container runtime, such as Docker Desktop/Engine configured with the NVIDIA
Container Toolkit. If the container falls back to a software Mesa path or fails EGL/OpenGL 4.3 context creation,
the `/health` endpoint and Main Computer inspector show the failure instead of pretending the stream is live.

## Manual smoke commands

From the repository root:

```bash
docker compose -f docker-compose.astrometric.yml up -d --build astrometric-renderer
curl -fsS http://127.0.0.1:${ASTROMETRIC_RENDERER_PORT:-8794}/health
```

Then open `/applications/astrometric` in Main Computer.
