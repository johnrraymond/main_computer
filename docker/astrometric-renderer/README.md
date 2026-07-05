# Astrometric 3D GPU Renderer

This directory contains the C++/CUDA renderer used by the Main Computer **Astrometric 3D** application.

The previous renderer path used headless EGL/OpenGL compute shaders.  The real renderer now uses CUDA kernels instead, because Docker Desktop/WSL GPU containers are much more reliable for CUDA compute than for headless OpenGL/EGL.  It is still a real backend 3D renderer: the browser sends camera events to Main Computer, Main Computer forwards them to the C++ service, and the service traces Schwarzschild rays on the NVIDIA GPU before JPEG/MJPEG streaming the result.

## Diagnostic mode

Set `ASTROMETRIC_RENDERER_MODE=smoke` before `docker compose up` to start the same HTTP/MJPEG service with
a CPU-generated diagnostic frame stream instead of CUDA.  Main Computer exposes this as **Start Port Smoke**.
It is not the astrometric renderer; it is a port/back-end streaming test.  If smoke mode streams but GPU mode does
not, Docker networking and Main Computer proxying are working and the remaining failure is in CUDA/NVIDIA runtime
or kernel startup.

## Runtime contract

The renderer keeps the CUDA context and all GPU work on the renderer thread. The HTTP server runs on a separate thread so `/health`, `/camera`, and MJPEG clients stay responsive while the GPU is tracing frames.

Main Computer owns the browser-facing API. The C++ process only listens inside the Docker-bound loopback port.

- `GET /health` returns renderer, CUDA/GPU, frame, and camera state.
- `GET /frame.jpg` returns the latest rendered frame.
- `GET /stream.mjpg` streams rendered frames as MJPEG.
- `POST /camera` accepts mouse/camera control JSON:
  - `{ "type": "orbit", "dx": 12, "dy": -4 }`
  - `{ "type": "pan", "dx": 10, "dy": 8, "shift": true }`
  - `{ "type": "zoom", "deltaY": -120 }`
  - `{ "type": "reset" }`
  - `{ "type": "quality", "jpeg_quality": 86 }`

The CUDA kernel in `src/astrometric_renderer.cu` is adapted from the uploaded `black_hole-main/geodesic.comp`
source: it keeps the Schwarzschild null-geodesic initialization, RHS integration, event-horizon intercept, and
accretion-disk crossing model, but exposes uniform camera inputs suitable for backend mouse control.

## Tuning knobs

The compose file defaults to an interactive startup profile: 640×360 at 10 FPS with `ASTROMETRIC_RENDERER_IDLE_STEPS=520` and `ASTROMETRIC_RENDERER_MOVING_STEPS=220`. Increase those environment variables for slower, higher-quality traces after the stream is stable.

## GPU container assumptions

`docker-compose.astrometric.yml` requests:

- `gpus: all`
- `NVIDIA_VISIBLE_DEVICES=all`
- `NVIDIA_DRIVER_CAPABILITIES=compute,utility`

The host still needs a working GPU container runtime, such as Docker Desktop/Engine configured with the NVIDIA
Container Toolkit. If CUDA is not visible inside the container, `/health`, Docker logs, and the Main Computer
inspector show the CUDA error instead of pretending the stream is live.

## Manual real-renderer commands

From the repository root:

```bash
docker compose -f docker-compose.astrometric.yml down --remove-orphans
docker compose -f docker-compose.astrometric.yml up -d --build --force-recreate astrometric-renderer
curl -fsS http://127.0.0.1:${ASTROMETRIC_RENDERER_PORT:-8794}/health
curl -fsS http://127.0.0.1:${ASTROMETRIC_RENDERER_PORT:-8794}/frame.jpg -o astrometric_cuda_frame.jpg
```

Then open `/applications/astrometric` in Main Computer.
