import requests
import json
base_url = "http://localhost:8000"
res = requests.get(f"{base_url}/api/logs/token-usage")
print(json.dumps(res.json(), indent=2))
res2 = requests.get(f"{base_url}/api/stats")
print(json.dumps(res2.json(), indent=2))
