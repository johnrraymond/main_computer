import json
import urllib.request

url = "http://127.0.0.1:8767/v1/chat"
token = "make-a-new-token"

body = json.dumps({
    "prompt": "Reply with exactly OK."
}).encode("utf-8")

req = urllib.request.Request(
    url,
    data=body,
    method="POST",
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
)

with urllib.request.urlopen(req, timeout=20) as response:
    print(response.read().decode("utf-8"))
