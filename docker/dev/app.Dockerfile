# Dev-only Main Computer container image.
#
# This image intentionally runs the source tree directly instead of installing a
# production wheel. It is for compose-based hub/worker/UI development while the
# production packaging and executor-service split are still being designed.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace \
    MAIN_COMPUTER_WORKSPACE=/workspace \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install "playwright>=1.40.0" \
    && python -m playwright install --with-deps chromium \
    && chmod -R 755 /ms-playwright

COPY . /workspace

EXPOSE 8765 8767 8770 8771

CMD ["python", "-m", "main_computer.cli", "viewport", "--host", "0.0.0.0", "--port", "8765", "-noverbose"]
