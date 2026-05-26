FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace \
    MAIN_COMPUTER_WORKSPACE=/workspace

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Keep the remote hub image intentionally narrow. Do not COPY the whole
# repository: local diagnostics, runtime state, reports, and tool caches can be
# very large and are not needed to run the hub service.
COPY pyproject.toml README.md /workspace/
COPY main_computer /workspace/main_computer

RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir .

EXPOSE 8770

CMD ["python", "-m", "main_computer.cli", "hub", "--host", "0.0.0.0", "--port", "8770", "--hub-root", "/runtime/hub", "-noverbose"]
