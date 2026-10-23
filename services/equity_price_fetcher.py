import os
import time
import base64
import requests
import pyotp
import pandas as pd
from datetime import datetime, date, timedelta
from urllib.parse import parse_qs, urlparse
from fyers_apiv3 import fyersModel
from sqlalchemy.orm import Session
from models.equities import EquityTransaction, EquityHolding, EquityRemainingLot
from models.enums import TransactionStatus, TransactionType
from sqlalchemy import func, case, literal
from sqlalchemy.orm import aliased
from services.equity_fifo_engine import update_equity_holdings_in_db

# Fyers Credentials
FYERS_CREDS = {
    "username": "XS92257",
    "totp_key": "EHBUF2GYHTEY3II4MOY672ENBFWZS36E",
    "pin": "1107",
    "client_id": "YAARRA7O2Q-100",
    "secret_key": "D85KWW3ECF",
    "redirect_uri": "https://trade.fyers.in/api-login/redirect-uri/index.html"
}

def get_encoded_string(string: str) -> str:
    return base64.b64encode(str(string).encode("ascii")).decode("ascii")

def authenticate_fyers():
    creds = FYERS_CREDS
    try:
        # OTP
        requests.post("https://api-t2.fyers.in/vagator/v2/send_login_otp_v2",
                      json={"fy_id": get_encoded_string(creds['username']), "app_id": "2"})
        if datetime.now().second % 30 > 27: time.sleep(5)
        
        # Verify
        res2 = requests.post("https://api-t2.fyers.in/vagator/v2/verify_otp",
                             json={"request_key": requests.post("https://api-t2.fyers.in/vagator/v2/send_login_otp_v2", json={"fy_id": get_encoded_string(creds['username']), "app_id": "2"}).json()["request_key"], "otp": pyotp.TOTP(creds['totp_key']).now()}).json()
        
        # Pin
        ses = requests.Session()
        res3 = ses.post("https://api-t2.fyers.in/vagator/v2/verify_pin_v2",
                        json={"request_key": res2["request_key"], "identity_type": "pin", "identifier": get_encoded_string(creds['pin'])}).json()
        ses.headers.update({'authorization': f"Bearer {res3['data']['access_token']}"})
        
        # Token
        app_id_clean = creds['client_id'][:-4] if creds['client_id'].endswith("-100") else creds['client_id']
        res3 = ses.post("https://api-t1.fyers.in/api/v3/token",
                        json={"fyers_id": creds['username'], "app_id": app_id_clean, "redirect_uri": creds['redirect_uri'], "appType": "100", "code_challenge": "", "state": "None", "scope": "", "nonce": "", "response_type": "code", "create_cookie": True}).json()
        
        auth_code = parse_qs(urlparse(res3['Url']).query)['auth_code'][0]
        session = fyersModel.SessionModel(client_id=creds['client_id'], secret_key=creds['secret_key'], redirect_uri=creds['redirect_uri'], response_type="code", grant_type="authorization_code")
        session.set_token(auth_code)
        return session.generate_token()['access_token']
    except Exception as e:
        print(f"Fyers Auth Failed: {e}")
        return None

def _build_symbol_fetch_list(db: Session) -> list:
    """
    Build a smart list of symbols to fetch prices for.
    
    For each symbol, determines:
    - first_date: earliest transaction date (start of price fetch range)
    - is_currently_held: whether the stock has remaining lots (qty > 0)
    - last_sale_date: the most recent SELL transaction date for this symbol
    
    Rules:
    - Currently held stocks → fetch prices from first_date to TODAY
    - Fully sold stocks → fetch prices from first_date to last_sale_date only
    """
    from decimal import Decimal
    
    # Step 1: Get all symbols with their first transaction date
    symbol_dates = db.query(
        EquityTransaction.symbol,
        func.min(EquityTransaction.transaction_date).label("first_date")
    ).filter(
        EquityTransaction.status == TransactionStatus.ACTIVE
    ).group_by(EquityTransaction.symbol).all()
    
    if not symbol_dates:
        return []
    
    # Step 2: Get currently held symbols (those with remaining lots > 0)
    held_symbols = set()
    remaining = db.query(
        EquityTransaction.symbol
    ).join(
        EquityRemainingLot, EquityRemainingLot.purchase_txn_id == EquityTransaction.id
    ).filter(
        EquityRemainingLot.remaining_units > Decimal('0.001')
    ).distinct().all()
    
    for (sym,) in remaining:
        held_symbols.add(sym.strip().upper())
    
    # Step 3: Get last sale date per symbol (for fully sold stocks)
    last_sale_dates = {}
    sales = db.query(
        EquityTransaction.symbol,
        func.max(EquityTransaction.transaction_date).label("last_sale_date")
    ).filter(
        EquityTransaction.status == TransactionStatus.ACTIVE,
        EquityTransaction.transaction_type == TransactionType.SELL
    ).group_by(EquityTransaction.symbol).all()
    
    for sym, last_sale in sales:
        last_sale_dates[sym.strip().upper()] = last_sale
    
    # Step 4: Build the final list
    result = []
    today = datetime.today().date()
    
    for sym, first_date in symbol_dates:
        sym_clean = sym.strip().upper()
        is_held = sym_clean in held_symbols
        
        if is_held:
            # Currently held → fetch till today
            end_dt = today
        else:
            # Fully sold → fetch only till last sale date
            last_sale = last_sale_dates.get(sym_clean)
            if last_sale:
                end_dt = last_sale
            else:
                # No sale found (edge case) → skip entirely
                continue
        
        result.append((sym_clean, first_date, end_dt, is_held))
    
    return result


def fetch_and_update_prices_from_fyers(db: Session) -> dict:
    """
    Connects to Fyers, fetches daily prices for stock symbols in the DB,
    and updates DATA/portfolio_prices.feather. Then recalculates holdings.
    
    Smart fetching:
    - Currently held stocks → fetch prices till today
    - Fully sold stocks → fetch prices only till last sale date
    """
    token = authenticate_fyers()
    if not token:
        return {"success": False, "message": "Failed to authenticate with Fyers API. Check credentials or TOTP key."}
        
    fyers = fyersModel.FyersModel(client_id=FYERS_CREDS['client_id'], is_async=False, token=token)
    
    # 1. Build smart symbol list with per-symbol end dates
    symbol_fetch_list = _build_symbol_fetch_list(db)
    
    if not symbol_fetch_list:
        return {"success": False, "message": "No equity symbols found in database to fetch prices for."}
        
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    price_file = os.path.join(base_dir, "DATA", "portfolio_prices.feather")
    
    # 2. Load existing feather prices
    existing_prices = pd.DataFrame()
    if os.path.exists(price_file):
        try:
            existing_prices = pd.read_feather(price_file)
            if 'ts' in existing_prices.columns:
                existing_prices = existing_prices.set_index('ts')
            elif 'index' in existing_prices.columns:
                existing_prices = existing_prices.set_index('index')
            existing_prices.index = pd.to_datetime(existing_prices.index)
        except Exception as e:
            print(f"Could not load existing prices: {e}")

    price_data = {}
    for col in existing_prices.columns:
        price_data[col] = existing_prices[col]

    success_count = 0
    skipped_count = 0
    failed_symbols = []

    # Count currently held vs fully sold
    held_count = sum(1 for _, _, _, is_held in symbol_fetch_list if is_held)
    sold_count = len(symbol_fetch_list) - held_count
    print(f"Fetching stock prices for {len(symbol_fetch_list)} symbols ({held_count} held, {sold_count} fully sold)...")

    for symbol, first_date, symbol_end_date, is_currently_held in symbol_fetch_list:
        if not symbol:
            continue
            
        # Reconstruct the Fyers symbol
        # Try multiple suffix variations to find the one that works
        if "-" in symbol:
            # Already has a suffix (like -RR), use as-is
            fyers_candidates = [f"NSE:{symbol}", f"BSE:{symbol}"]
        else:
            # Try EQ first (most common), then RR (REITs), then BE (trade-to-trade)
            fyers_candidates = [
                f"NSE:{symbol}-EQ",
                f"NSE:{symbol}-RR",
                f"NSE:{symbol}-BE",
                f"BSE:{symbol}-EQ",
                f"BSE:{symbol}-A",
                f"BSE:{symbol}-B",
            ]
        
        # Check if we already have price data under any of these symbol names
        fyers_symbol = None
        for candidate in fyers_candidates:
            if candidate in price_data and not price_data[candidate].empty:
                fyers_symbol = candidate
                break
        
        if not fyers_symbol:
            fyers_symbol = fyers_candidates[0]  # Default to first candidate

        # Use per-symbol end date
        end_date = datetime.combine(symbol_end_date, datetime.min.time())
        target_end_date = pd.Timestamp(end_date).normalize()

        # Determine start date
        start_date = None
        if fyers_symbol in price_data and not price_data[fyers_symbol].empty:
            sym_series = price_data[fyers_symbol].dropna()
            if not sym_series.empty:
                last_date = sym_series.index.max()
                if last_date >= target_end_date - timedelta(days=1):
                    # Already up to date for this symbol's needed range
                    success_count += 1
                    if not is_currently_held:
                        skipped_count += 1
                    continue
                start_date = last_date
        
        if not start_date:
            start_date = pd.to_datetime(first_date or date(2018, 1, 1))
            
        start_date = start_date - timedelta(days=10) # Overlap to ensure continuity
        
        curr = start_date
        fetched_candles = []
        
        # Track which candidate index we're on for fallback
        candidate_idx = fyers_candidates.index(fyers_symbol) if fyers_symbol in fyers_candidates else 0

        while curr < end_date:
            frm = curr.strftime("%Y-%m-%d")
            to = min(curr + timedelta(days=300), end_date).strftime("%Y-%m-%d")
            if curr > datetime.now(): 
                break
                
            chunk_success = False
            for attempt in range(3):
                try:
                    data = {
                        "symbol": fyers_symbol,
                        "resolution": "D",
                        "date_format": "1",
                        "range_from": frm,
                        "range_to": to
                    }
                    resp = fyers.history(data=data)
                    
                    if 'candles' in resp and resp['candles']:
                        fetched_candles.extend(resp['candles'])
                        chunk_success = True
                        break
                    elif 'message' in resp and resp.get('s') == 'error':
                        # Try next candidate symbol variation
                        if not fetched_candles:
                            candidate_idx += 1
                            if candidate_idx < len(fyers_candidates):
                                fyers_symbol = fyers_candidates[candidate_idx]
                                continue
                except Exception:
                    pass
                time.sleep(1)
                
            if not chunk_success and not fetched_candles:
                break
                
            curr += timedelta(days=301)
            time.sleep(0.1)

        if fetched_candles:
            df = pd.DataFrame(fetched_candles, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
            df['ts'] = pd.to_datetime(df['ts'], unit='s').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
            df = df.set_index('ts')
            df = df[~df.index.duplicated(keep='last')]
            
            if fyers_symbol in price_data and not price_data[fyers_symbol].empty:
                combined = pd.concat([price_data[fyers_symbol], df['c']])
                combined = combined[~combined.index.duplicated(keep='last')]
                price_data[fyers_symbol] = combined
            else:
                price_data[fyers_symbol] = df['c']
                
            success_count += 1
            status = "held" if is_currently_held else f"sold, prices till {symbol_end_date}"
            print(f"  [SUCCESS] Fetched {fyers_symbol} ({status})")
        else:
            failed_symbols.append(symbol)
            print(f"  [FAILED] Could not fetch prices for {fyers_symbol}")

    # Save to Feather file
    if price_data:
        full_df = pd.DataFrame(price_data).sort_index()
        full_df.index.name = 'ts'
        os.makedirs(os.path.dirname(price_file), exist_ok=True)
        full_df.reset_index().to_feather(price_file)
        
        # Trigger recalculation of active holdings and live price sync
        update_equity_holdings_in_db(db)
        
        msg = f"Successfully synced live prices for {success_count} stock symbols."
        if skipped_count:
            msg += f" {skipped_count} fully-sold symbols already had complete price data."
        if failed_symbols:
            msg += f" Failed for: {', '.join(failed_symbols[:5])}."
        return {"success": True, "message": msg}
    else:
        return {"success": False, "message": "No price data fetched. Verify Fyers credentials."}
