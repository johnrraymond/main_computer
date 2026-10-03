#!/bin/sh
set -eu

# Kernel-launched init does not inherit the Docker image PATH.  The official
# Python image installs CPython under /usr/local/bin, so define the guest PATH
# explicitly instead of depending on an ambient init environment.
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON=/usr/local/bin/python3

mkdir -p /proc /sys /dev /dev/pts /tmp
mount -t proc proc /proc 2>/dev/null || true
mount -t sysfs sysfs /sys 2>/dev/null || true
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
mkdir -p /dev/pts
mount -t devpts devpts /dev/pts 2>/dev/null || true

# qemu_fw_cfg is modular on common Alpine linux-virt builds. Loading it here
# keeps the job transport out of networking and writable shared filesystems.
modprobe qemu_fw_cfg 2>/dev/null || true

if [ ! -x "$PYTHON" ]; then
    echo "MC_QEMU_GUEST_FATAL: python_missing path=$PYTHON" >&2
else
    "$PYTHON" /opt/main-computer/qemu_python_guest_runner.py || true
fi
sync 2>/dev/null || true
poweroff -f 2>/dev/null || halt -f 2>/dev/null || reboot -f
