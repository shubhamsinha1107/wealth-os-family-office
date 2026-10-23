import pandas as pd
import numpy as np
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from models.mutual_funds import MFTransaction
from models.persons import Person
from models.enums import TransactionStatus

def format_amount_label(amount: float) -> str:
    """Formats amounts to shorthand labels, e.g., 250000 -> 2.5L, 15000 -> 15K, 500 -> 500."""
    if amount is None:
        return "0"
    a = int(round(float(amount) / 10.0) * 10)
    if a >= 100000:
        return f"{a/100000:.1f}L".rstrip('0').rstrip('.')
    elif a >= 1000:
        return f"{a//1000}K"
    else:
        return f"{a}"

def infer_frequency(dates) -> str:
    """Infers frequency (D/W/M) based on the median days difference between SIPs."""
    if len(dates) < 2:
        return "M"
    diffs = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
    med = np.median(diffs)
    if med <= 3:
        return "D"
    elif med <= 14:
        return "W"
    else:
        return "M"

def get_monthly_multiplier(frequency: str) -> int:
    """Returns monthly multiplier: Monthly=1, Weekly=4, Daily=20 as defined in cams-reader-and-report.ipynb."""
    return {
        "M": 1,
        "W": 4,
        "D": 20
    }.get(frequency, 1)

def get_sip_report_data(
    db: Session,
    min_amount: float = 500.0,
    owner_id: int = None,
    advisor_filter: str = None,
    start_date: str = None,
    end_date: str = None
) -> list:
    """
    Analyzes SIP, STP, and sequenced Purchase transactions in the database.
    Supports filtering by Owner ID, Advisor, and latest payment Date Range.
    """
    # 1. Retrieve active PURCHASE_SIP, PURCHASE, and SWITCH_IN transactions from the database
    txns = db.query(MFTransaction, Person.short_name.label("owner_name")).join(
        Person, Person.id == MFTransaction.person_id
    ).filter(
        MFTransaction.transaction_type.in_(['PURCHASE_SIP', 'PURCHASE', 'SWITCH_IN']),
        MFTransaction.amount >= min_amount,
        MFTransaction.status == TransactionStatus.ACTIVE
    )
    
    # Filter by owner_id in the query if provided
    if owner_id:
        txns = txns.filter(MFTransaction.person_id == owner_id)
        
    txns = txns.all()
    
    if not txns:
        return []
        
    # Convert to pandas DataFrame for quick grouping/calculations
    rows = []
    from services.fifo_engine import resolve_advisor
    for item in txns:
        t, owner = item
        advisor = resolve_advisor(t.scheme_name, t.folio_number, t.notes, db)
            
        rows.append({
            "scheme": t.scheme_name,
            "owner": owner,
            "owner_id": t.person_id,
            "advisor": advisor or "DIRECT",
            "amount": float(t.amount),
            "date": pd.to_datetime(t.transaction_date),
            "type": t.transaction_type
        })
        
    df = pd.DataFrame(rows)
    df = df.sort_values(by=["scheme", "owner", "advisor", "amount", "date"])
    
    sip_summary = []
    
    # Default date range checks (cutoff defaults to 2026-04-01 if start_date is not specified)
    cutoff = pd.to_datetime(start_date) if start_date else pd.to_datetime("2026-04-01")
    limit_end = pd.to_datetime(end_date) if end_date else None
    
    # Group by scheme, owner, advisor, SIP amount, and raw transaction type
    for (scheme, owner, advisor, amt, raw_type), group in df.groupby(["scheme", "owner", "advisor", "amount", "type"]):
        # Sort group by date
        sorted_group = group.sort_values("date")
        last_dates = sorted_group["date"].tolist()
        
        if not last_dates:
            continue

        # Skip cash-management / liquid-type instruments — these are not meaningful
        # recurring investment plans worth tracking in the SIP Tracker.
        scheme_upper = scheme.upper()
        EXCLUDED_KEYWORDS = [
            "LIQUID",
            "ARBITRAGE",
            "OVERNIGHT",
            "MONEY MARKET",
            "LOW DURATION",
            "ULTRA SHORT",
            "SHORT TERM",
            "SHORT DURATION",
        ]
        if any(kw in scheme_upper for kw in EXCLUDED_KEYWORDS):
            continue

        # Skip large one-off purchases: instalment amount > ₹15L is not a SIP/STP plan
        SIP_MAX_AMOUNT = 15_00_000   # ₹15 Lakhs
        if amt > SIP_MAX_AMOUNT:
            continue

        # Determine the user-facing Transaction Type label: SIP, STP, or PURCHASE
        if raw_type == 'PURCHASE_SIP':
            txn_type_label = "SIP"
        elif raw_type == 'SWITCH_IN':
            txn_type_label = "STP"
        else:
            # For regular PURCHASE, check if there is sequencing (at least 2 transactions)
            # This filters out one-off lump sums and keeps recurring weekly/monthly purchases.
            if len(last_dates) < 2:
                continue
            txn_type_label = "PURCHASE"
            
        # 2. Apply Date Range Filters on the latest payment date (LAST_DATE_1)
        latest_payment_date = last_dates[-1]
        
        # Filter: latest payment must be >= start_date (cutoff)
        if latest_payment_date < cutoff:
            continue
            
        # Filter: latest payment must be <= end_date (if specified)
        if limit_end and latest_payment_date > limit_end:
            continue
            
        # 3. Apply Advisor Filter (if specified)
        if advisor_filter and advisor_filter.strip():
            if advisor_filter.upper() not in advisor.upper():
                continue
                
        # Infer frequency
        frequency = infer_frequency(last_dates)
        monthly_multiplier = get_monthly_multiplier(frequency)
        monthly_amt = amt * monthly_multiplier
        
        # Count transactions in the last 30 days from today
        today = pd.Timestamp.today().normalize()
        thirty_days_ago = today - pd.Timedelta(days=30)
        txn_count_1m = sum(1 for d in last_dates if d >= thirty_days_ago)

        # Format payment dates
        last_date_1_str = latest_payment_date.strftime("%d/%m/%Y")
        last_date_2_str = last_dates[-2].strftime("%d/%m/%Y") if len(last_dates) > 1 else None
        
        sip_summary.append({
            "scheme": scheme,
            "owner": owner,
            "owner_id": group["owner_id"].iloc[0],
            "advisor": advisor,
            "txn_type": txn_type_label,
            "amount_val": amt,
            "amount_formatted": f"{format_amount_label(amt)} /{frequency}",
            "frequency": frequency,
            "monthly_equivalent": monthly_amt,
            "monthly_equivalent_formatted": format_amount_label(monthly_amt),
            "last_date_1": last_date_1_str,
            "last_date_2": last_date_2_str,
            "txn_count_1m": txn_count_1m
        })
        
    # --- Priority deduplication ---
    # For the same (scheme, owner, amount), only keep the highest-priority type:
    # SIP > STP > PURCHASE  (e.g. Axis Silver STP + PURCHASE → show only STP)
    TYPE_PRIORITY = {"SIP": 0, "STP": 1, "PURCHASE": 2}
    
    seen: dict = {}  # key -> best entry so far
    for entry in sip_summary:
        key = (entry["scheme"], entry["owner"], entry["amount_val"])
        if key not in seen:
            seen[key] = entry
        else:
            # Replace if this entry has a higher priority (lower number)
            if TYPE_PRIORITY[entry["txn_type"]] < TYPE_PRIORITY[seen[key]["txn_type"]]:
                seen[key] = entry
    
    sip_summary = list(seen.values())
        
    # Sort summary by owner (ACCOUNT) then scheme (FUND NAME) as done in cams-reader.ipynb
    sip_summary = sorted(sip_summary, key=lambda x: (x["owner"], x["scheme"]))
    
    # Add S NO index to match Excel
    for idx, item in enumerate(sip_summary, 1):
        item["s_no"] = idx
        
    return sip_summary
