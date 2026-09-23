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

COPY download_release.py /tmp/download_release.py
RUN python /tmp/download_release.py \
    && rm /tmp/download_release.py

# Install the exact NanoJev release dependencies, but force the CUDA 12.6
# PyTorch wheel instead of allowing pip to select a CPU-only torch build.
RUN grep -v -E '^[[:space:]]*torch([=<>!~]|$)' /opt/nanojev/source/requirements-toy.txt > /tmp/requirements-no-torch.txt \
    && python -m pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cu126 \
    && python -m pip install -r /tmp/requirements-no-torch.txt \
    && rm /tmp/requirements-no-torch.txt

# serve_decisions.py requires a web root even when callers only use /api/*.
RUN mkdir -p /opt/nanojev-web \
    && printf '%s\n' '<!doctype html><html><body>NanoJev local inference service</body></html>' > /opt/nanojev-web/index.html

WORKDIR /opt/nanojev/source
EXPOSE 9765

# Deliberately omit --disable-native-triton. On the target RTX 3060 host the
# compatibility fallback made steady inference seconds slower; the native path
# produced repeatable ~55 ms decisions after warm-up.
CMD ["python", "/opt/nanojev/source/scripts/serve_decisions.py", \
     "--checkpoint-dir", "/opt/nanojev", \
     "--web-root", "/opt/nanojev-web", \
     "--host", "0.0.0.0", \
     "--port", "9765", \
     "--precision", "bf16"]
