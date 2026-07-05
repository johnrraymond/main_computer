from __future__ import annotations

import base64
import json
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = 8794

# Small valid JPEG, embedded so this needs no Pillow/OpenCV/dependencies.
JPEG_BYTES = base64.b64decode(
    "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"
)

started = time.time()
frame_seq = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}", flush=True)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global frame_seq
        path = urlparse(self.path).path

        if path == "/health":
            frame_seq += 1
            self._send_json({
                "ok": True,
                "renderer_mode": "python-fake",
                "startup_phase": "fake renderer online",
                "stream_ready": True,
                "gl_ready": False,
                "frame_seq": frame_seq,
                "frame_ms": 1.0,
                "width": 64,
                "height": 36,
                "uptime_s": round(time.time() - started, 2),
                "camera": {
                    "radius": 6.5e10,
                    "azimuth": 0.0,
                    "elevation": 0.15
                }
            })
            return

        if path == "/frame.jpg":
            frame_seq += 1
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(JPEG_BYTES)))
            self.end_headers()
            self.wfile.write(JPEG_BYTES)
            return

        if path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    frame_seq += 1
                    part = (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(JPEG_BYTES)}\r\n\r\n".encode("ascii")
                        + JPEG_BYTES
                        + b"\r\n"
                    )
                    self.wfile.write(part)
                    self.wfile.flush()
                    time.sleep(0.25)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        self._send_json({"ok": False, "error": f"unknown path {path}"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"

        if path == "/camera":
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = {}
            self._send_json({
                "ok": True,
                "renderer_mode": "python-fake",
                "received": payload,
                "camera": {
                    "radius": 6.5e10,
                    "azimuth": 0.0,
                    "elevation": 0.15
                }
            })
            return

        self._send_json({"ok": False, "error": f"unknown path {path}"}, 404)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"fake astrometric renderer listening at http://{HOST}:{PORT}", flush=True)

    def stop(*_):
        print("stopping fake renderer", flush=True)
        server.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    server.serve_forever()


if __name__ == "__main__":
    main()