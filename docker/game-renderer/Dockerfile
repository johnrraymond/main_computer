FROM python:3.12-slim

WORKDIR /app
COPY game_renderer_server.py /app/game_renderer_server.py

ENV GAME_RENDERER_BIND=0.0.0.0
ENV GAME_RENDERER_PORT=8798
ENV GAME_RENDERER_MODE=smoke
ENV GAME_RENDERER_BACKEND=procedural-svg

EXPOSE 8798
CMD ["python", "/app/game_renderer_server.py"]
