#!/usr/bin/env bash
set -euo pipefail

PORT="18084"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="${2:?--port requires a value}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 64
      ;;
  esac
done

mc_sudo() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "This command requires root privileges. Re-run through onlyoffice-control.ps1, which uses wsl.exe -u root." >&2
    exit 1
  fi
}

echo "Stopping ONLYOFFICE Docs native services in WSL on port ${PORT}..."

if command -v supervisorctl >/dev/null 2>&1; then
  mc_sudo supervisorctl stop all >/dev/null 2>&1 || true
fi

stop_service() {
  local service_name="$1"
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files "$service_name.service" >/dev/null 2>&1; then
    mc_sudo systemctl stop "$service_name" >/dev/null 2>&1 || true
  fi
  if command -v service >/dev/null 2>&1; then
    mc_sudo service "$service_name" stop >/dev/null 2>&1 || true
  fi
}

stop_service nginx
stop_service supervisor
stop_service rabbitmq-server
stop_service redis-server
stop_service postgresql

echo "Stop commands sent."
