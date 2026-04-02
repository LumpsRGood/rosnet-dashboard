import os
import requests
import base64
from dotenv import load_dotenv

load_dotenv()
API_USER = os.getenv("ROSNET_API_USER")
API_KEY = os.getenv("ROSNET_API_KEY")
CLIENT_ID = os.getenv("ROSNET_CLIENT_ID")

credentials = f"{API_USER}:{API_KEY}"
encoded = base64.b64encode(credentials.encode()).decode('utf-8')

headers = {
    "Authorization": f"Basic {encoded}",
    "Accept": "application/json",
    "Client": CLIENT_ID
}

url = "https://api.rosnet.com/general/employees"
resp = requests.get(url, headers=headers)
if resp.status_code == 200:
    data = resp.json()
    if data and isinstance(data, list) and len(data) > 0:
        print("KEYS:")
        for k in data[0].keys():
            print(f"- {k}")
        print("\nSAMPLE ITEM:")
        print(data[0])
    else:
        print("Data empty or not a list")
else:
    print(f"Error {resp.status_code}: {resp.text}")
