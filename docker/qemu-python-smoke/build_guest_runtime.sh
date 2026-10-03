#!/bin/sh
set -eu

OUT=/out
PYTHON=/usr/local/bin/python3
mkdir -p "$OUT"

if [ ! -x "$PYTHON" ]; then
    echo "guest Python executable was not found at $PYTHON" >&2
    exit 1
fi

KERNEL=""
for candidate in /boot/vmlinuz-virt /boot/vmlinuz-*virt* /boot/vmlinuz-*; do
    if [ -f "$candidate" ]; then
        KERNEL="$candidate"
        break
    fi
done
if [ -z "$KERNEL" ]; then
    echo "linux-virt kernel was not found" >&2
    exit 1
fi

cp "$KERNEL" "$OUT/vmlinuz"

# Pack the complete small Alpine/Python userspace as initramfs. /out is a
# separate bind mount and -xdev keeps the generated artifact out of itself.
(
    cd /
    find . -xdev \
        ! -path './proc/*' \
        ! -path './sys/*' \
        ! -path './dev/pts/*' \
        ! -path './tmp/*' \
        -print0 \
      | cpio --null -o -H newc 2>/dev/null \
      | gzip -9
) > "$OUT/initramfs.cpio.gz"

"$PYTHON" - <<'PY' > "$OUT/runtime.json"
import hashlib, json, platform, pathlib, sys

def h(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()

print(json.dumps({
    "schema": "main-computer-qemu-python-guest-runtime-v1",
    "runtime_revision": 2,
    "python_executable": "/usr/local/bin/python3",
    "python_implementation": platform.python_implementation(),
    "python_version": platform.python_version(),
    "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
    "kernel_sha256": h("/out/vmlinuz"),
    "initramfs_sha256": h("/out/initramfs.cpio.gz"),
}, sort_keys=True, indent=2))
PY

sha256sum "$OUT/vmlinuz" "$OUT/initramfs.cpio.gz" > "$OUT/SHA256SUMS"
