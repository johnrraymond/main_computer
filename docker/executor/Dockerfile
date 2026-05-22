# Dev Docker image for the existing in-process DockerExecutor.
#
# The current executor launches one short-lived container per approved command.
# This image gives those containers a predictable Linux/Python/Bash baseline for
# local smoke tests and RAG/tool-loop development. It is intentionally not the
# final production executor-service image.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        findutils \
        gawk \
        git \
        grep \
        jq \
        sed \
        unzip \
        zip \
    && rm -rf /var/lib/apt/lists/*

COPY main-computer-exec /usr/local/bin/main-computer-exec
RUN chmod 0755 /usr/local/bin/main-computer-exec \
    && mkdir -p /inputs /outputs /workspace \
    && chmod 0777 /outputs /workspace

WORKDIR /workspace

CMD ["bash", "-lc", "python --version && main-computer-exec --version && main-computer-exec run --cwd /workspace --timeout-ms 5000 --artifact-dir /outputs -- 'echo main-computer-exec-ready'"]
