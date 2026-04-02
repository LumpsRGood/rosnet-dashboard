import os
import requests
import base64
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
import time
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RateLimitExceeded(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__(f"Rate limited by Rosnet API. Retry after {retry_after} seconds.")


# Try to get credentials from environment
API_USER = os.getenv("ROSNET_API_USER")
API_KEY = os.getenv("ROSNET_API_KEY")
CLIENT_ID = os.getenv("ROSNET_CLIENT_ID")

BASE_URL = "https://api.rosnet.com"

# Check if we should run in mock mode
MOCK_MODE = not (API_USER and API_KEY)
if MOCK_MODE:
    logger.warning("No Rosnet API credentials found in environment. Running in MOCK MODE with simulated data.")

def _get_headers():
    if not API_USER or not API_KEY:
        return {}
    
    # Rosnet requires base64 encoded "username:password"
    credentials = f"{API_USER}:{API_KEY}"
    encoded = base64.b64encode(credentials.encode()).decode('utf-8')
    
    headers = {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json"
    }
    if CLIENT_ID:
        headers["Client"] = CLIENT_ID
        
    return headers

def _make_request(endpoint, params=None):
    """
    Makes a request to the Rosnet API, handling rate limits (429) and pagination automatically.
    Returns a unified list of all results.
    """
    if MOCK_MODE:
        return []

    url = f"{BASE_URL}{endpoint}"
    headers = _get_headers()
    if params is None:
        params = {}
    
    all_results = []
    
    while True:
        try:
            response = requests.get(url, headers=headers, params=params)
            
            # Handle rate limits
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 3600))
                logger.warning(f"Rate limited by Rosnet API. Retry after {retry_after} seconds.")
                raise RateLimitExceeded(retry_after)

                
            response.raise_for_status()
            data = response.json()
            
            # Assuming the response is a direct list as per typical Swagger models in Rosnet
            if isinstance(data, list):
                all_results.extend(data)
            else:
                # If wrapped in an object
                all_results.append(data)
                
            # Check for pagination cursor
            cursor = response.headers.get("Cursor")
            if cursor:
                params["cursor"] = cursor
            else:
                break # No more pages
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching data from {endpoint}: {e}")
            break
            
    return all_results

# --- Specific API Wrappers ---

def get_total_sales(start_date, end_date):
    """Fetches total sales data (Mocked or Real)"""
    if MOCK_MODE:
        # Generate mock sales data over the date range
        dates = pd.date_range(start_date, end_date).strftime("%Y-%m-%d").tolist()
        import random
        return [
            {
                "businessDate": date,
                "locationId": random.choice([101, 102, 103]),
                "netSales": round(random.uniform(2000, 8000), 2),
                "guestCount": random.randint(150, 400),
                "driveThruSales": round(random.uniform(500, 3000), 2)
            }
            for date in dates for _ in range(3)
        ]
        
    return _make_request("/sales/totalSales", params={"startDate": start_date, "endDate": end_date})

def get_labor_shifts(start_date, end_date):
    """Fetches labor shift data (Mocked or Real)"""
    if MOCK_MODE:
        dates = pd.date_range(start_date, end_date).strftime("%Y-%m-%d").tolist()
        import random
        return [
            {
                "businessDate": date,
                "locationId": random.choice([101, 102, 103]),
                "hours": round(random.uniform(6, 8.5), 2),
                "regularPay": round(random.uniform(80, 150), 2),
                "overtimePay": round(random.uniform(0, 30), 2),
                "jobCode": random.choice(["Server", "Cook", "Manager", "Host"])
            }
            for date in dates for _ in range(10)
        ]

    return _make_request("/labor/shifts", params={"startDate": start_date, "endDate": end_date})

def get_inventory_products(inv_period_date, location_id=None):
    """Fetches inventory products for a period (Mocked or Real)"""
    if MOCK_MODE:
        import random
        products = ["Burger Patties", "Buns", "Lettuce", "Tomatoes", "French Fries", "Soda Bags"]
        return [
            {
                "productName": p,
                "category": "Food",
                "onHandQuantity": random.randint(10, 500),
                "totalCost": round(random.uniform(20, 300), 2)
            }
            for p in products
        ]
        
    params = {"invPeriodDate": inv_period_date}
    if location_id:
        params["locationId"] = location_id
    return _make_request("/food/inventoryProducts", params=params)

def get_employees_map(location_id):
    """Fetches employees for a location to resolve EmployeeId -> Name"""
    if MOCK_MODE:
        return {}
    params = {"locationId": location_id}
    employees = _make_request("/general/employees", params=params)
    emp_map = {}
    if isinstance(employees, list):
        for e in employees:
            # We map LocationEmployeeId or Id depending on which matches the ItemSold schema.
            # In our tests, ItemsSold EmployeeId matched the internal 'Id' but POS ids can vary.
            emp_map[e.get('Id')] = e.get('Name')
            emp_map[e.get('LocationEmployeeId')] = e.get('Name')
    return emp_map

def get_checks(start_date, end_date, location_id, emp_map=None):
    """Fetches full check details from live API to calculate table turns"""
    if MOCK_MODE:
        dates = pd.date_range(start_date, end_date).strftime("%Y-%m-%d").tolist()
        import random
        from datetime import timedelta
        
        checks = []
        for d in dates:
            for table in range(1, 15): # 14 Tables
                # Randomize turns per table between 2 to 6
                turns = random.randint(2, 6)
                for turn in range(turns):
                    t = datetime.strptime(d, "%Y-%m-%d") + timedelta(hours=random.randint(11, 21), minutes=random.randint(0, 59))
                    # Lower duration to be near the 45 min mark
                    duration_mins = random.randint(25, 65)
                    t_close = t + timedelta(minutes=duration_mins)
                    
                    order_type = random.choices(["Eat In", "To Go", "Delivery"], weights=[0.8, 0.15, 0.05])[0]
                    payment = random.choices(["Credit Card", "Cash", "Gift Card"], weights=[0.75, 0.20, 0.05])[0]
                    server = random.choice(["Sarah J.", "Michael T.", "David R.", "Emma W.", "Chris L."])
                    
                    checks.append({
                        "businessDate": d,
                        "locationId": location_id,
                        "checkNumber": f"CHK-{d}-{table}-{turn}",
                        "tableNumber": table,
                        "serverName": server,
                        "orderType": order_type,
                        "paymentType": payment,
                        "openTime": t.strftime("%H:%M:%S"),
                        "closeTime": t_close.strftime("%H:%M:%S"),
                        "guestCount": random.randint(1, 6),
                        "netSales": round(random.uniform(15, 120), 2)
                    })
        return checks
        
    # Needs to loop over start_date to end_date:
    dates = pd.date_range(start_date, end_date).strftime("%Y-%m-%d").tolist()
    all_live_checks = []
    
    def _normalize_live_checks(live_data):
        normalized = []
        for c in live_data:
            # 1. Determine Payment (Focus on Credit Card)
            is_cc = False
            for p in c.get('Payments', []):
                if p.get('IsCreditCard'):
                    is_cc = True
                    break
            payment_type = "Credit Card" if is_cc else "Other"
            
            # 2. Determine Server
            server = "Unknown Server"
            if c.get('ItemsSold') and len(c.get('ItemsSold')) > 0:
                emp_id = c['ItemsSold'][0].get('EmployeeId', 'Unknown')
                if emp_map and emp_id in emp_map:
                    server = emp_map[emp_id]
                else:
                    server = f"Emp {emp_id}"
            
            # 3. Determine Order Type (Heuristic based on Table Name)
            # Table 0 usually implies Takeout/Delivery, others imply Eat In
            tbl = str(c.get('TableName', '0')).strip()
            order_type = "Eat In" if tbl != '0' and tbl != '' else "Delivery"
            
            # 4. Format Times to HH:MM:SS
            o_time = c.get('OpenTime', '')
            c_time = c.get('CloseTime', '')
            open_str = o_time.split('T')[-1] + ":00" if 'T' in o_time else "00:00:00"
            close_str = c_time.split('T')[-1] + ":00" if 'T' in c_time else "00:00:00"
            
            net_sales = sum(item.get("SoldPrice", 0) for item in c.get('ItemsSold', []))
            if server == "Unknown Server" and net_sales == 0:
                continue # Ghost Check / Voids
                
            normalized.append({
                "businessDate": c.get("BusinessDate"),
                "locationId": c.get("LocationId"),
                "checkNumber": c.get("Id"),
                "tableNumber": tbl,
                "serverName": server,
                "orderType": order_type,
                "paymentType": payment_type,
                "openTime": open_str,
                "closeTime": close_str,
                "guestCount": c.get("TrafficCount", 0),
                "netSales": net_sales
            })
        return normalized
    
    for d in dates:
        params = {"businessDate": d, "locationId": location_id}
        day_checks = _make_request("/sales/checks", params=params)
        if isinstance(day_checks, list):
            all_live_checks.extend(_normalize_live_checks(day_checks))
            
    return all_live_checks

def get_locations():
    """Fetches available locations (Mocked or Real)"""
    if MOCK_MODE:
        return [
            {"id": 101, "name": "Downtown Store"},
            {"id": 102, "name": "Uptown Kiosk"},
            {"id": 103, "name": "Suburban Drive-Thru"}
        ]
    return _make_request("/general/locations")
