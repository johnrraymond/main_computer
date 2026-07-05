# Upstream black-hole source reference

This directory carries the original `geodesic.comp` shader from the uploaded `black_hole-main.zip` snapshot.
The runtime shader in `../shaders/astrometric_service.comp` keeps the same Schwarzschild ray initialization,
geodesic RHS, disk interception, and event-horizon logic but exposes a backend service contract for Main Computer:

- headless EGL/OpenGL compute rendering
- `/stream.mjpg` frame streaming
- `/camera` mouse/camera control
- `/health` GPU and camera inspection

The uploaded `black_hole.cpp` remains the source reference for camera orbit/pan/zoom behavior and the original
GPU compute dispatch model.
