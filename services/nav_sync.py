import requests
import concurrent.futures
from datetime import datetime
from sqlalchemy.orm import Session
from models.market_data import MFNav
from models.mutual_funds import MFHolding, MFTransaction
from database import SessionLocal
from urllib3.util import Retry
from requests.adapters import HTTPAdapter

# Setup a shared thread-safe requests session with connection pooling and retry adapter
session = requests.Session()
retries = Retry(
    total=4,               # 4 retries
    backoff_factor=1,      # sleep 1s, 2s, 4s... between retries
    status_forcelist=[500, 502, 503, 504],
    raise_on_status=False
)
# Pool size matches or exceeds ThreadPool max_workers
adapter = HTTPAdapter(
    pool_connections=25,
    pool_maxsize=25,
    max_retries=retries
)
session.mount('http://', adapter)
session.mount('https://', adapter)

def sync_nav_for_amfi_code(amfi_code: str, db: Session, fetch_history: bool = False) -> int:
    """
    Fetches the NAV data for a single AMFI code from api.mfapi.in,
    inserts missing NAV values into market_data.mf_nav, and returns the number of rows inserted.
    """
    if not amfi_code:
        return 0
        
    amfi_code = str(amfi_code).strip().split('.')[0] # Standardize format
    
    url = f"https://api.mfapi.in/mf/{amfi_code}"
    try:
        print(f"Fetching NAV data from AMFI API for code: {amfi_code}")
        response = session.get(url, timeout=15)
        if response.status_code != 200:
            print(f"  ! Error: AMFI API returned status code {response.status_code}")
            return 0
            
        data = response.json()
        meta = data.get("meta", {})
        nav_list = data.get("data", [])
        
        if not nav_list:
            print(f"  ! No NAV data found for AMFI code {amfi_code}")
            return 0
            
        scheme_name = meta.get("scheme_name", "")
        
        # Check existing dates in DB to avoid duplicates
        existing_rows = db.query(MFNav.nav_date).filter(
            MFNav.amfi_code == amfi_code
        ).all()
        existing_dates = {row.nav_date for row in existing_rows}
        
        # Determine which dates to insert
        to_insert = []
        
        # If we don't want history, we only look at the most recent entries (e.g. latest 5 entries)
        # to ensure we get the latest holiday/weekend updates, otherwise look at everything
        target_nav_list = nav_list if fetch_history else nav_list[:5]
        
        for nav_item in target_nav_list:
            date_str = nav_item.get("date")
            nav_val_str = nav_item.get("nav")
            
            if not date_str or not nav_val_str:
                continue
                
            nav_date = datetime.strptime(date_str, "%d-%m-%Y").date()
            nav_val = float(nav_val_str)
            
            if nav_date not in existing_dates:
                new_nav = MFNav(
                    amfi_code=amfi_code,
                    scheme_name=scheme_name,
                    nav_date=nav_date,
                    nav=nav_val
                )
                to_insert.append(new_nav)
                
        if to_insert:
            db.add_all(to_insert)
            db.commit()
            print(f"  -> Successfully inserted {len(to_insert)} NAV records for {scheme_name}")
            return len(to_insert)
            
    except Exception as e:
        print(f"  ! Failed to sync NAV for AMFI code {amfi_code}: {e}")
        
    return 0

def fetch_json_for_amfi_code(amfi_code: str) -> dict:
    """Fetches raw JSON response from AMFI API for a single code."""
    url = f"https://api.mfapi.in/mf/{amfi_code}"
    try:
        response = session.get(url, timeout=15)
        if response.status_code == 200:
            return {"code": amfi_code, "data": response.json(), "error": None}
        return {"code": amfi_code, "data": None, "error": f"HTTP status {response.status_code}"}
    except Exception as e:
        return {"code": amfi_code, "data": None, "error": str(e)}

def sync_all_active_navs(db: Session, fetch_history: bool = False) -> dict:
    """
    Finds all active AMFI codes in transactions or holdings, syncs their NAVs in parallel,
    and updates values in mutual_funds.holdings.
    """
    # 1. Get all unique active AMFI codes
    amfi_txn = db.query(MFTransaction.amfi_code).filter(
        MFTransaction.amfi_code.isnot(None),
        MFTransaction.amfi_code != "",
        MFTransaction.amfi_code != "0"
    ).distinct().all()
    
    amfi_hold = db.query(MFHolding.amfi_code).filter(
        MFHolding.amfi_code.isnot(None),
        MFHolding.amfi_code != "",
        MFHolding.amfi_code != "0"
    ).distinct().all()
    
    active_codes = {row.amfi_code for row in amfi_txn}.union({row.amfi_code for row in amfi_hold})
    active_codes = {code.strip().split('.')[0] for code in active_codes if code}
    
    print(f"Found {len(active_codes)} unique active AMFI codes in database.")
    
    records_inserted = 0
    codes_to_sync_api = set(active_codes)
    
    # Try fetching official daily AMFI text file first if not fetching history
    if not fetch_history:
        print("Attempting to fetch latest NAVs from official AMFI daily feed...")
        try:
            url = "https://www.amfiindia.com/spages/NAVAll.txt"
            response = session.get(url, timeout=20)
            if response.status_code == 200:
                lines = response.text.split("\n")
                print(f"Successfully downloaded official AMFI feed. Parsing {len(lines)} lines...")
                
                # Fetch existing max dates per code from DB to avoid duplicate insertion queries
                amfi_date_pairs = set()
                existing_dates_rows = db.query(MFNav.amfi_code, MFNav.nav_date).filter(
                    MFNav.amfi_code.in_(list(active_codes))
                ).all()
                for amfi_code_db, nav_date_db in existing_dates_rows:
                    amfi_date_pairs.add((amfi_code_db, nav_date_db))
                
                to_insert = []
                for line in lines:
                    parts = line.strip().split(";")
                    if len(parts) >= 8:
                        code = parts[0].strip()
                        if code in active_codes:
                            scheme_name = parts[3].strip()
                            nav_val_str = parts[6].strip()
                            date_str = parts[7].strip()
                            try:
                                nav_val = float(nav_val_str)
                                nav_date = datetime.strptime(date_str, "%d-%b-%Y").date()
                                
                                if (code, nav_date) not in amfi_date_pairs:
                                    new_nav = MFNav(
                                        amfi_code=code,
                                        scheme_name=scheme_name,
                                        nav_date=nav_date,
                                        nav=nav_val
                                    )
                                    to_insert.append(new_nav)
                                    amfi_date_pairs.add((code, nav_date))
                                
                                # Remove from API fallback list since we successfully synced it
                                if code in codes_to_sync_api:
                                    codes_to_sync_api.remove(code)
                            except Exception:
                                pass
                                
                if to_insert:
                    db.add_all(to_insert)
                    db.commit()
                    print(f"  -> Successfully inserted {len(to_insert)} latest NAV records from official AMFI feed.")
                    records_inserted += len(to_insert)
            else:
                print(f"  ! Official AMFI feed returned HTTP status code {response.status_code}. Falling back entirely to API.")
        except Exception as e:
            print(f"  ! Official AMFI feed fetch/parse generated an exception: {e}. Falling back to API.")
            
    # For any codes not found in official feed or if fetch_history is True, fetch concurrently using ThreadPoolExecutor
    if codes_to_sync_api:
        if fetch_history:
            print(f"Fetching NAV history concurrently for all {len(codes_to_sync_api)} active codes via API...")
        else:
            print(f"Falling back to fetch latest NAV for {len(codes_to_sync_api)} codes via API...")
            
        fetched_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_code = {executor.submit(fetch_json_for_amfi_code, code): code for code in codes_to_sync_api}
            for future in concurrent.futures.as_completed(future_to_code):
                code = future_to_code[future]
                try:
                    res = future.result()
                    fetched_results.append(res)
                except Exception as exc:
                    print(f"  ! Code {code} generated an exception: {exc}")
                    
        # Process API results
        for item in fetched_results:
            code = item["code"]
            data = item["data"]
            error = item["error"]
            
            if error or not data:
                print(f"  ! Failed to fetch AMFI code {code} via API: {error}")
                continue
                
            meta = data.get("meta", {})
            nav_list = data.get("data", [])
            
            if not nav_list:
                continue
                
            scheme_name = meta.get("scheme_name", "")
            
            existing_rows = db.query(MFNav.nav_date).filter(
                MFNav.amfi_code == code
            ).all()
            existing_dates = {row.nav_date for row in existing_rows}
            
            to_insert = []
            target_nav_list = nav_list if fetch_history else nav_list[:5]
            
            for nav_item in target_nav_list:
                date_str = nav_item.get("date")
                nav_val_str = nav_item.get("nav")
                
                if not date_str or not nav_val_str:
                    continue
                    
                nav_date = datetime.strptime(date_str, "%d-%m-%Y").date()
                nav_val = float(nav_val_str)
                
                if nav_date not in existing_dates:
                    new_nav = MFNav(
                        amfi_code=code,
                        scheme_name=scheme_name,
                        nav_date=nav_date,
                        nav=nav_val
                    )
                    to_insert.append(new_nav)
                    existing_dates.add(nav_date)
                    
            if to_insert:
                db.add_all(to_insert)
                db.commit()
                print(f"  -> Successfully inserted {len(to_insert)} NAV records via API fallback for {scheme_name}")
                records_inserted += len(to_insert)
            
    # 2. Update holdings with the latest NAVs
    holdings = db.query(MFHolding).all()
    updated_holdings_count = 0
    
    for holding in holdings:
        if not holding.amfi_code:
            continue
            
        code = holding.amfi_code.strip().split('.')[0]
        # Find latest NAV for this AMFI code
        latest_nav_row = db.query(MFNav).filter(
            MFNav.amfi_code == code
        ).order_by(MFNav.nav_date.desc()).first()
        
        if latest_nav_row:
            holding.current_nav = latest_nav_row.nav
            holding.current_value = float(holding.units) * float(latest_nav_row.nav)
            holding.as_of_date = latest_nav_row.nav_date
            updated_holdings_count += 1
            
    db.commit()
    print(f"NAV sync complete. Updated {updated_holdings_count} holdings AUM based on latest API prices.")
    
    return {
        "active_codes_found": len(active_codes),
        "new_nav_records_added": records_inserted,
        "holdings_valuations_updated": updated_holdings_count
    }
