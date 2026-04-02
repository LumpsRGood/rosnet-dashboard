import os
import requests
import base64
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()
API_USER = os.getenv('ROSNET_API_USER')
API_KEY = os.getenv('ROSNET_API_KEY')
credentials = f'{API_USER}:{API_KEY}'
encoded = base64.b64encode(credentials.encode()).decode('utf-8')
headers = {'Authorization': f'Basic {encoded}', 'Accept': 'application/json'}

print(f"--- ROSNET API DIAGNOSTIC SCRIPT ---")

# Step 1: Ensure Locations work and grab a valid location ID
loc_resp = requests.get('https://api.rosnet.com/general/locations', headers=headers)
if loc_resp.status_code != 200:
    print(f"Failed to fetch locations: {loc_resp.status_code}")
    exit(1)

locations = loc_resp.json()
target_loc = None
for loc in locations:
    if loc.get('Id'):
        target_loc = loc['Id']
        break

print(f"Found valid location for testing: {target_loc}")

# Step 2: Try various historical dates to see if data exists deeper in the past
test_offsets_days = [7, 30] # Test a week ago, a month ago
endpoints = ["/sales/totalSales"]
test_clients = [
    "fdc0ed30-d069-4e25-8b87-a0bd4569a8f7",
    "LIIHOP",
    "PRP",
    "LIIHOP/PRP",
    "LIIHOP/PRP Reporting User"
]

print(f"\n--- HUNTING FOR CLIENT HEADER ---")
today = datetime.now()

for client_str in test_clients:
    print(f"\nTesting Client Header: '{client_str}'")
    headers['Client'] = client_str
    
    for endpoint in endpoints:
        for offset in test_offsets_days:
            test_date = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            url = f"https://api.rosnet.com{endpoint}?startDate={test_date}&endDate={test_date}&locationId={target_loc}"
            
            try:
                resp = requests.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    print(f"✅ BINGO! CLIENT '{client_str}' WORKED! 200 OK. {len(data) if isinstance(data, list) else 'Object'} Records.")
                else:
                    print(f"❌ {test_date} failed: {resp.status_code}")
            except Exception as e:
                print(f"Fatal error: {e}")
