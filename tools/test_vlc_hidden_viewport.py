#!/usr/bin/env python3
"""
Standalone VLC localhost viewport probe.

Tests:
- Launch VLC as a hidden backend-style process.
- Avoid the RC interface that caused the visible VLC control window.
- Use VLC HTTP control on 127.0.0.1 with a generated backend-only password.
- Use VLC MJPEG stream output on 127.0.0.1.
- Start paused.
- Attach to the stream first.
- Resume playback through HTTP control.
- Capture first complete JPEG frame.
- Pause again.
- Check whether VLC created any visible top-level windows on Windows.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_VLC = r"C:\Program Files\VideoLAN\VLC\vlc.exe"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def auth_header(password: str) -> dict[str, str]:
    token = base64.b64encode(f":{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def http_get_json(url: str, password: str, timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=auth_header(password))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8", errors="replace"))


def http_control(control_base: str, password: str, command: str, timeout: float = 5.0) -> dict[str, Any]:
    url = f"{control_base}?command={urllib.parse.quote(command)}"
    return http_get_json(url, password, timeout=timeout)


def wait_for_control(control_base: str, password: str, timeout: float = 12.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            return http_get_json(control_base, password, timeout=2.0)
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)

    raise RuntimeError(f"VLC HTTP control did not become ready: {last_error!r}")


def open_stream(stream_url: str, timeout: float = 15.0):
    req = urllib.request.Request(stream_url, headers={"Cache-Control": "no-cache"})
    return urllib.request.urlopen(req, timeout=timeout)


def read_first_jpeg(resp, timeout: float = 12.0) -> bytes:
    deadline = time.monotonic() + timeout
    buf = bytearray()
    soi = b"\xff\xd8"
    eoi = b"\xff\xd9"

    while time.monotonic() < deadline:
        chunk = resp.read(8192)
        if not chunk:
            time.sleep(0.05)
            continue

        buf.extend(chunk)

        start = buf.find(soi)
        if start >= 0:
            end = buf.find(eoi, start + 2)
            if end >= 0:
                return bytes(buf[start : end + 2])

        if len(buf) > 5_000_000:
            del buf[:2_500_000]

    raise TimeoutError("Timed out waiting for a complete JPEG frame from VLC stream.")


def visible_windows_for_pid(pid: int) -> list[str]:
    if os.name != "nt":
        return []

    user32 = ctypes.windll.user32

    EnumWindows = user32.EnumWindows
    IsWindowVisible = user32.IsWindowVisible
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetWindowTextW = user32.GetWindowTextW
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId

    titles: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        proc_id = ctypes.c_ulong()
        GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))

        if proc_id.value == pid and IsWindowVisible(hwnd):
            length = GetWindowTextLengthW(hwnd)
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value.strip()
                if title:
                    titles.append(title)
            else:
                titles.append("<visible untitled window>")

        return True

    EnumWindows(callback, 0)
    return titles


def launch_vlc_hidden(
    vlc_path: str,
    src: str,
    stream_port: int,
    control_port: int,
    password: str,
    width: int,
    height: int,
    fps: int,
    log_path: Path,
) -> subprocess.Popen:
    sout = (
        f"#transcode{{vcodec=MJPG,vb=2000,scale=1,width={width},height={height},"
        f"fps={fps},acodec=none}}:"
        f"std{{access=http,mux=mpjpeg,dst=:{stream_port}/stream.mjpg}}"
    )

    args = [
        vlc_path,
        "--no-one-instance",
        "-I",
        "dummy",
        "--loop",
        "--start-paused",
        "--avcodec-hw=none",
        "--verbose=2",
        "--file-logging",
        f"--logfile={str(log_path)}",
        "--no-video-title-show",
        "--no-audio",
        "--extraintf=http",
        "--http-host=127.0.0.1",
        f"--http-port={control_port}",
        f"--http-password={password}",
        f"--sout={sout}",
        "--sout-keep",
        src,
    ]

    creationflags = 0
    startupinfo = None

    if os.name == "nt":
        creationflags |= subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

    print("[launch] " + " ".join(f'"{a}"' if " " in a else a for a in args))

    return subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )


def tail_file(path: Path, max_lines: int = 80) -> str:
    if not path.exists():
        return "<log file does not exist>"

    try:
        lines = path.read_text(errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as exc:
        return f"<failed to read log: {exc!r}>"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vlc", default=DEFAULT_VLC, help="Path to vlc.exe")
    parser.add_argument("--src", required=True, help="Video file to stream")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=200)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--keep-running", action="store_true", help="Leave VLC running after the test")
    args = parser.parse_args()

    vlc_path = Path(args.vlc)
    src_path = Path(args.src)

    if not vlc_path.exists():
        print(f"[fail] VLC not found: {vlc_path}")
        return 2

    if not src_path.exists():
        print(f"[fail] source video not found: {src_path}")
        return 2

    stream_port = find_free_port()
    control_port = find_free_port()
    password = secrets.token_urlsafe(18)

    temp_dir = Path(tempfile.gettempdir())
    log_path = temp_dir / "vlc-hidden-viewport-probe.log"
    frame_path = temp_dir / "vlc-hidden-first-frame.jpg"

    for p in (log_path, frame_path):
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    control_base = f"http://127.0.0.1:{control_port}/requests/status.json"
    stream_url = f"http://127.0.0.1:{stream_port}/stream.mjpg"

    proc: subprocess.Popen | None = None
    stream_resp = None

    try:
        proc = launch_vlc_hidden(
            str(vlc_path),
            str(src_path),
            stream_port,
            control_port,
            password,
            args.width,
            args.height,
            args.fps,
            log_path,
        )

        print(f"[pid] {proc.pid}")
        print(f"[stream] {stream_url}")
        print(f"[control] {control_base}")

        print("[wait] waiting for HTTP control endpoint...")
        status = wait_for_control(control_base, password)
        print(f"[ok] control ready: state={status.get('state')!r}, loop={status.get('loop')!r}")

        time.sleep(0.5)
        windows = visible_windows_for_pid(proc.pid)
        if windows:
            print("[warn] VLC has visible top-level window(s):")
            for title in windows:
                print(f"       - {title}")
        else:
            print("[ok] no visible VLC top-level windows detected")

        print("[stream] opening MJPEG stream while VLC is paused...")
        stream_resp = open_stream(stream_url, timeout=15.0)
        print("[ok] stream HTTP connection opened")

        print("[control] sending pl_forceresume...")
        status = http_control(control_base, password, "pl_forceresume")
        print(f"[ok] resume sent: state={status.get('state')!r}, position={status.get('position')!r}")

        print("[frame] waiting for first complete JPEG...")
        jpeg = read_first_jpeg(stream_resp, timeout=12.0)
        frame_path.write_bytes(jpeg)
        print(f"[ok] captured first JPEG: {len(jpeg):,} bytes")
        print(f"[file] {frame_path}")

        print("[control] sending pl_forcepause...")
        status = http_control(control_base, password, "pl_forcepause")
        print(f"[ok] pause sent: state={status.get('state')!r}, position={status.get('position')!r}")

        time.sleep(0.5)
        windows = visible_windows_for_pid(proc.pid)
        if windows:
            print("[warn] visible VLC window(s) after test:")
            for title in windows:
                print(f"       - {title}")
        else:
            print("[ok] still no visible VLC top-level windows detected")

        print("[pass] VLC hidden localhost feature probe succeeded")
        return 0

    except Exception as exc:
        print(f"[fail] {exc!r}")
        print("\n--- VLC log tail ---")
        print(tail_file(log_path))
        return 1

    finally:
        if stream_resp is not None:
            try:
                stream_resp.close()
            except Exception:
                pass

        if proc is not None and not args.keep_running:
            try:
                print("[cleanup] asking VLC to stop through HTTP control...")
                http_control(control_base, password, "pl_stop", timeout=2.0)
            except Exception:
                pass

            try:
                proc.terminate()
                proc.wait(timeout=400.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

            print("[cleanup] VLC stopped")


if __name__ == "__main__":
    raise SystemExit(main())