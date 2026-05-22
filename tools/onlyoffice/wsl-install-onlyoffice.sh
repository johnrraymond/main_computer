#!/usr/bin/env bash
set -euo pipefail

PORT="18084"
JWT_SECRET="${MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET:-main-computer-onlyoffice-local-secret}"
SKIP_HARDWARE_CHECK="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="${2:?--port requires a value}"
      shift 2
      ;;
    --jwt-secret)
      JWT_SECRET="${2:?--jwt-secret requires a value}"
      shift 2
      ;;
    --no-skip-hardware-check)
      SKIP_HARDWARE_CHECK="false"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 64
      ;;
  esac
done

echo "ONLYOFFICE WSL native install"
echo "port: ${PORT}"
echo "install method: native packages (not Docker)"

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

if ! command -v curl >/dev/null 2>&1; then
  echo "Installing curl/ca-certificates..."
  mc_sudo apt-get update
  mc_sudo apt-get install -y curl ca-certificates
fi

mkdir -p .main-computer/onlyoffice
INSTALLER=".main-computer/onlyoffice/docs-install.sh"

echo "Downloading ONLYOFFICE Docs installer..."
curl -fsSL https://download.onlyoffice.com/docs/docs-install.sh -o "$INSTALLER"
chmod +x "$INSTALLER"

ARGS=(
  "--docsport" "$PORT"
  "--jwtenabled" "true"
  "--jwtsecret" "$JWT_SECRET"
)

if [[ "$SKIP_HARDWARE_CHECK" == "true" ]]; then
  ARGS+=("--skiphardwarecheck" "true")
fi

echo "Running ONLYOFFICE installer as WSL root/local admin."
echo "The installer prompt is answered with N to select native DEB/RPM packages instead of Docker."
printf 'N\n' | mc_sudo bash "$INSTALLER" "${ARGS[@]}"

echo "Install command completed. Running status check..."
bash "./tools/onlyoffice/wsl-status-onlyoffice.sh" --port "$PORT" --verbose
