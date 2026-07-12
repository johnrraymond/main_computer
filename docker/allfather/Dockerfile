FROM python:3.12-slim

WORKDIR /app

ARG FDB_VERSION=7.4.6
ARG FDB_PYTHON_VERSION=7.4.6
ARG BESU_VERSION=24.7.0
ARG TARGETARCH

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV FDB_API_VERSION=740
ENV FOUNDATIONDB_CLIENT_LIBRARY_PATH=/usr/lib/libfdb_c.so
ENV MC_ALLFATHER_MANIFEST_PATH=/opt/main-computer/allfather/manifest.json

LABEL org.opencontainers.image.description="Main Computer guarded all-father function cell"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      openjdk-21-jre-headless \
      procps \
      tar \
      wget \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
COPY docker/allfather/entrypoint.py /opt/main-computer/allfather/entrypoint.py
COPY docker/allfather/healthcheck.py /opt/main-computer/allfather/healthcheck.py

RUN set -eux; \
    python -m pip install --upgrade pip; \
    python -m pip install --no-cache-dir -e . "foundationdb==${FDB_PYTHON_VERSION}"; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
      amd64|x86_64) deb_arch="amd64" ;; \
      arm64|aarch64) deb_arch="arm64" ;; \
      *) echo "Unsupported FoundationDB architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/foundationdb-clients.deb \
      "https://github.com/apple/foundationdb/releases/download/${FDB_VERSION}/foundationdb-clients_${FDB_VERSION}-1_${deb_arch}.deb"; \
    curl -fsSL -o /tmp/foundationdb-server.deb \
      "https://github.com/apple/foundationdb/releases/download/${FDB_VERSION}/foundationdb-server_${FDB_VERSION}-1_${deb_arch}.deb"; \
    apt-get update; \
    apt-get install -y --no-install-recommends /tmp/foundationdb-clients.deb /tmp/foundationdb-server.deb; \
    rm -f /tmp/foundationdb-clients.deb /tmp/foundationdb-server.deb; \
    rm -rf /var/lib/apt/lists/*; \
    test -x /usr/bin/fdbserver; \
    test -x /usr/bin/fdbcli; \
    test -e "${FOUNDATIONDB_CLIENT_LIBRARY_PATH}"; \
    curl -fsSL -o /tmp/besu.tar.gz \
      "https://github.com/hyperledger/besu/releases/download/${BESU_VERSION}/besu-${BESU_VERSION}.tar.gz"; \
    mkdir -p /opt/besu; \
    tar -xzf /tmp/besu.tar.gz -C /opt/besu --strip-components=1; \
    rm -f /tmp/besu.tar.gz; \
    test -x /opt/besu/bin/besu

EXPOSE 41414 4550 4551 4552 8780 8781 8785 8786 8790 8791 8792 8545 8546 8547 30303 30304 30305

ENTRYPOINT ["python", "/opt/main-computer/allfather/entrypoint.py"]
