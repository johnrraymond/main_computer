FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    CC=gcc

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip \
    && python -m pip install huggingface_hub==1.32.0

# Keep the legacy unified-games-v1 release in the generic image as an explicit
# fallback. The default entrypoint now serves the public Main Computer CLEF
# champion instead.
COPY docker/nanojev/download_release.py /tmp/download_release.py
RUN python /tmp/download_release.py \
    && rm /tmp/download_release.py

# Install the exact NanoJev release dependencies, but force the CUDA 12.6
# PyTorch wheel instead of allowing pip to select a CPU-only torch build.
RUN grep -v -E '^[[:space:]]*torch([=<>!~]|$)' /opt/nanojev/source/requirements-toy.txt > /tmp/requirements-no-torch.txt \
    && python -m pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cu126 \
    && python -m pip install -r /tmp/requirements-no-torch.txt \
    && rm /tmp/requirements-no-torch.txt

# The CLEF runtime imports the exact inference architecture/evidence path used by
# the trainer and holdout evaluator. These are source only; no training state is
# copied into the image.
RUN mkdir -p /opt/main-computer-tools /opt/nanojev-clef-service /opt/nanojev-web
COPY tools/nanojev_three_backbone_clef_sized_live_train_smoke.py /opt/main-computer-tools/
COPY tools/nanojev_three_backbone_clef_sized_live_train.py /opt/main-computer-tools/
COPY tools/nanojev_three_backbone_clef_tinystories_structured_supervision_train.py /opt/main-computer-tools/
COPY tools/nanojev_objective_api.py /opt/main-computer-tools/
COPY docker/nanojev/clef_service.py /opt/nanojev-clef-service/clef_service.py
COPY docker/nanojev/entrypoint.py /opt/nanojev-clef-service/entrypoint.py

# Upstream serve_decisions.py requires a web root even when callers only use /api/*.
RUN printf '%s\n' '<!doctype html><html><body>NanoJev local inference service</body></html>' > /opt/nanojev-web/index.html

WORKDIR /opt/nanojev-clef-service
EXPOSE 9765

CMD ["python", "/opt/nanojev-clef-service/entrypoint.py"]
