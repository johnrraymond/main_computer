FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/seh
WORKDIR /opt/seh
RUN groupadd --system seh && useradd --system --gid seh seh && \
    pip install --no-cache-dir 'cryptography>=42,<49' 'websockets>=13,<17'
RUN mkdir -p main_computer/slh main_computer/seh && \
    printf '"""Minimal isolated SEH runtime namespace."""\n' > main_computer/__init__.py && \
    printf '"""SEH shared signature verifier."""\n' > main_computer/slh/__init__.py
COPY main_computer/slh/authenticator.py main_computer/slh/authenticator.py
COPY main_computer/seh/__init__.py main_computer/seh/__init__.py
COPY main_computer/seh/relay.py main_computer/seh/relay.py
USER seh
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('SEH_PORT','8080'),timeout=3)" || exit 1
CMD ["python", "-m", "main_computer.seh.relay"]
