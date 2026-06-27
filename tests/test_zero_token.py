import requests
import time
import json

base_url = "http://localhost:8000"

def setup():
    print("Setting up simulation...")
    res = requests.post(f"{base_url}/api/simulations/setup", json={"scene": "ap_deployment"})
    print(res.status_code)
    # Don't print full text to avoid truncation

def launch():
    print("Launching simulation...")
    res = requests.post(f"{base_url}/api/simulations/launch")
    print(res.status_code)
    try:
        data = res.json()
        print("Launch rounds:", data.get("rounds"))
    except:
        pass

def check_tokens():
    print("Checking tokens...")
    res = requests.get(f"{base_url}/api/logs/token-usage")
    print("token-usage:", json.dumps(res.json(), indent=2))
    
    res2 = requests.get(f"{base_url}/api/stats")
    print("stats:", json.dumps(res2.json(), indent=2))

if __name__ == "__main__":
    try:
        setup()
        launch()
        print("Simulation finished. Checking tokens...")
        check_tokens()
    except Exception as e:
        print("Error:", e)
