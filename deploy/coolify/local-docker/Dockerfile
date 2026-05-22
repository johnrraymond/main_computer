ARG COOLIFY_BASE_IMAGE=ghcr.io/coollabsio/coolify:latest
FROM ${COOLIFY_BASE_IMAGE}

USER root

RUN set -eux; \
    if command -v apk >/dev/null 2>&1; then \
        apk add --no-cache bash openssh-server openssh-client docker-cli docker-cli-compose; \
    elif command -v apt-get >/dev/null 2>&1; then \
        apt-get update; \
        if ! DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash openssh-server openssh-client docker.io docker-compose-plugin; then \
            DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash openssh-server openssh-client docker.io docker-compose; \
        fi; \
        rm -rf /var/lib/apt/lists/*; \
    else \
        echo "unsupported Coolify base image: no apk or apt-get found" >&2; \
        exit 1; \
    fi; \
    mkdir -p /root/.ssh /run/sshd /var/run/sshd; \
    chmod 700 /root/.ssh; \
    ssh-keygen -A

