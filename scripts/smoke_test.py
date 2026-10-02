import json
import urllib.request


for endpoint in ("health", "projects", "index/status"):
    with urllib.request.urlopen(f"http://127.0.0.1:8000/{endpoint}") as response:
        print(endpoint, json.load(response))
