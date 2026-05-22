#!/usr/bin/env bash
set -euo pipefail

PORT="18084"
VERBOSE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="${2:?--port requires a value}"
      shift 2
      ;;
    --verbose)
      VERBOSE="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 64
      ;;
  esac
done

BASE_URL="http://127.0.0.1:${PORT}"
ROOT_URL="${BASE_URL}/"
HEALTH_URL="${BASE_URL}/healthcheck"
API_URL="${BASE_URL}/web-apps/apps/api/documents/api.js"

echo "ONLYOFFICE WSL native status"
echo "port: ${PORT}"
echo "base url: ${BASE_URL}"
echo "api url: ${API_URL}"

http_code() {
  local url="$1"
  curl -sS -o /tmp/main-computer-onlyoffice-probe.out -w '%{http_code}' --max-time 5 "$url" 2>/tmp/main-computer-onlyoffice-probe.err || true
}

check_url() {
  local label="$1"
  local url="$2"
  local code
  code="$(http_code "$url")"
  local err=""
  if [[ -s /tmp/main-computer-onlyoffice-probe.err ]]; then
    err="$(tr '\n' ' ' </tmp/main-computer-onlyoffice-probe.err)"
  fi
  echo "${label}: HTTP ${code:-000} ${url}${err:+ (${err})}"
  if [[ "$code" =~ ^2|3 ]]; then
    return 0
  fi
  return 1
}

ROOT_OK=0
HEALTH_OK=0
API_OK=0
check_url "root" "$ROOT_URL" || ROOT_OK=$?
check_url "healthcheck" "$HEALTH_URL" || HEALTH_OK=$?
check_url "editor api" "$API_URL" || API_OK=$?

echo ""
echo "Listening sockets for ${PORT}:"
if command -v ss >/dev/null 2>&1; then
  ss -ltnp 2>/dev/null | grep ":${PORT} " || true
elif command -v netstat >/dev/null 2>&1; then
  netstat -ltnp 2>/dev/null | grep ":${PORT} " || true
else
  echo "ss/netstat not available"
fi

echo ""
echo "Installed package:"
if command -v dpkg-query >/dev/null 2>&1; then
  dpkg-query -W -f='${Package} ${Version} ${Status}\n' onlyoffice-documentserver 2>/dev/null || echo "onlyoffice-documentserver package not found"
else
  echo "dpkg-query not available"
fi

if [[ "$VERBOSE" == "true" ]]; then
  echo ""
  echo "Service snapshots:"
  for service_name in postgresql rabbitmq-server redis-server nginx supervisor; do
    if command -v service >/dev/null 2>&1; then
      service "$service_name" status 2>/dev/null | head -8 || true
    fi
  done
  if command -v supervisorctl >/dev/null 2>&1; then
    echo ""
    echo "supervisorctl:"
    supervisorctl status 2>/dev/null || true
  fi
fi

if [[ "$API_OK" -eq 0 ]]; then
  echo ""
  echo "ONLYOFFICE editor API is reachable."
  exit 0
fi

echo ""
if [[ "$ROOT_OK" -eq 0 || "$HEALTH_OK" -eq 0 ]]; then
  echo "Port ${PORT} is reachable, but it does not look like a ready ONLYOFFICE editor API."
  echo "This can happen if another local service owns the port or ONLYOFFICE is only partially started."
  exit 2
fi

echo "ONLYOFFICE editor API is not reachable on ${BASE_URL}."
exit 1
