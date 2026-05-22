# raw_ollama_stream.py
import json
import sys
import urllib.request

model = sys.argv[1] if len(sys.argv) > 1 else "gemma4:26b"
prompt = sys.argv[2] if len(sys.argv) > 2 else "Return only this exact text: hello"

body = json.dumps({
    "model": model,
    "prompt": prompt,
    "stream": True,
    "options": {
        "temperature": 0,
        "num_predict": 200
    }
}).encode("utf-8")

req = urllib.request.Request(
    "http://127.0.0.1:11434/api/generate",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(req, timeout=None) as r:
    while True:
        chunk = r.read(1)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()