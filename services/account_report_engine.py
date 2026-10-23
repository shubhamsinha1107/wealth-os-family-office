import pandas as pd
import numpy as np
from datetime import date
from sqlalchemy.orm import Session
from models.mutual_funds import MFTransaction, MFHolding, MFDetail
from models.persons import Person
from models.enums import TransactionStatus
from services.fifo_engine import get_purchase_lots_remaining_units, get_fund_type, resolve_advisor
from services.performance_engine import calculate_xirr

def get_account_report_data(db: Session, person_id: any = None) -> dict:
    """
    Calculates Asset Class Summary, Fund Type Summary, and Detailed Asset table
    for a given person, Core 4 Accounts (PATRIARCH, MEMBER_1, MEMBER_2, APEX), or Consolidated family portfolio
    dynamically from active database transactions.
    """
    # 1. Map person IDs to account codes
    portfolio_to_code = {
        "APEX HOLDINGS PVT. LTD.": "APEX",
        "FAMILY MEMBER 1":             "MEMBER_1",
        "FAMILY PATRIARCH":       "PATRIARCH",
        "FAMILY MEMBER 2":            "MEMBER_2",
        "FAMILY MEMBER 3":         "MEMBER_3",
        "FAMILY MEMBER 4":            "MEMBER_4",
        "FAMILY MEMBER 5":            "MEMBER_5",
        "FAMILY MEMBER 6":            "MEMBER_6",
        "FAMILY MEMBER 7":            "MEMBER_7"
    }

    all_persons = db.query(Person).all()
    person_code_map = {}
    for p in all_persons:
        code = portfolio_to_code.get(p.full_name.upper(), p.short_name)
        person_code_map[p.id] = code

    four_codes = ["PATRIARCH", "MEMBER_1", "MEMBER_2", "APEX"]
    is_four_accounts = (person_id == "4_ACCOUNTS" or str(person_id).upper() in ["4_ACCOUNTS", "4ACCOUNTS", "4_ACCOUNT", "4"])
    is_consolidated = (person_id is None or person_id == 0 or str(person_id).upper() == "CONSOLIDATED")

    four_person_ids = [pid for pid, code in person_code_map.items() if code in four_codes]

    if is_four_accounts:
        acct_code = "4_ACCOUNTS"
    elif is_consolidated:
        acct_code = "CONSOLIDATED"
    else:
        try:
            pid_int = int(person_id)
            person = db.query(Person).filter(Person.id == pid_int).first()
            if not person:
                return {"error": "Person not found"}
            acct_code = person_code_map.get(person.id, person.short_name)
            person_id = pid_int
        except (ValueError, TypeError):
            matching_pid = next((pid for pid, code in person_code_map.items() if code.upper() == str(person_id).upper()), None)
            if matching_pid:
                acct_code = person_code_map[matching_pid]
                person_id = matching_pid
            else:
                acct_code = "CONSOLIDATED"

    # 2. Get holdings mapping for latest AMFI-synced NAVs and ISINs
    holdings_query = db.query(MFHolding)
    if is_four_accounts:
        holdings_query = holdings_query.filter(MFHolding.person_id.in_(four_person_ids))
    elif not is_consolidated:
        holdings_query = holdings_query.filter(MFHolding.person_id == person_id)
    holdings = holdings_query.all()
    holdings_map = {h.scheme_name.upper(): h for h in holdings}

    # 3. Retrieve remaining units dictionary for all transactions
    remaining_units_dict = get_purchase_lots_remaining_units(db)

    # 4. Fetch all active purchase transactions
    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT']
    txns_query = db.query(MFTransaction).filter(
        MFTransaction.status == TransactionStatus.ACTIVE,
        MFTransaction.transaction_type.in_(purchase_types)
    )
    if is_four_accounts:
        txns_query = txns_query.filter(MFTransaction.person_id.in_(four_person_ids))
    elif not is_consolidated:
        txns_query = txns_query.filter(MFTransaction.person_id == person_id)
    txns = txns_query.all()

    # Get exit load mappings and customizations from mf_details table
    details_rows = db.query(MFDetail).all()
    details_map = {d.scheme_name.upper(): d for d in details_rows}

    # Bulk pre-fetch latest NAV for all schemes and AMFI codes in txns to avoid N+1 queries in loop
    fallback_navs_map = {}
    amfi_navs_map = {}
    if txns:
        from sqlalchemy import func
        from models.market_data import MFNav
        schemes_in_txns = list({t.scheme_name for t in txns})
        
        latest_dates = db.query(
            MFNav.scheme_name,
            func.max(MFNav.nav_date).label("max_date")
        ).filter(
            MFNav.scheme_name.in_(schemes_in_txns)
        ).group_by(MFNav.scheme_name).subquery()

        latest_nav_rows = db.query(
            MFNav.scheme_name,
            MFNav.nav
        ).join(
            latest_dates,
            (MFNav.scheme_name == latest_dates.c.scheme_name) & (MFNav.nav_date == latest_dates.c.max_date)
        ).all()

        fallback_navs_map = {row.scheme_name.upper(): float(row.nav) for row in latest_nav_rows if row.nav}

        amfi_codes_in_txns = list({t.amfi_code for t in txns if t.amfi_code})
        if amfi_codes_in_txns:
            latest_amfi_dates = db.query(
                MFNav.amfi_code,
                func.max(MFNav.nav_date).label("max_date")
            ).filter(
                MFNav.amfi_code.in_(amfi_codes_in_txns)
            ).group_by(MFNav.amfi_code).subquery()

            latest_amfi_nav_rows = db.query(
                MFNav.amfi_code,
                MFNav.nav
            ).join(
                latest_amfi_dates,
                (MFNav.amfi_code == latest_amfi_dates.c.amfi_code) & (MFNav.nav_date == latest_amfi_dates.c.max_date)
            ).all()

            amfi_navs_map = {row.amfi_code: float(row.nav) for row in latest_amfi_nav_rows if row.nav}

    # 5. Extract active lots (remaining_units > 0.001)
    active_lots = []
    today_date = date.today()

    for t in txns:
        rem_units = remaining_units_dict.get(t.id, 0.0)
        if rem_units <= 0.001:
            continue

        # Determine NAV and Cost
        cost_value = rem_units * float(t.nav)
        lot_acct_code = person_code_map.get(t.person_id, "OTHER")
        
        # Resolve Current NAV and Fund Type
        scheme_upper = t.scheme_name.upper()
        current_nav = float(t.nav)
        db_detail = details_map.get(scheme_upper)
        
        # Override Fund Type and Advisor if present in mf_details
        if db_detail:
            fund_type = db_detail.fund_type or get_fund_type(t.scheme_name)
            advisor = resolve_advisor(t.scheme_name, t.folio_number, t.notes, db, db_detail, owner_short_name=lot_acct_code)
            clean_name = db_detail.clean_name or t.scheme_name
            long_term_tenure = int(db_detail.long_term_tenure or 365)
            exit_load_tenure = int(db_detail.no_exit_load_tenure or 0)
            exit_load_pct = float(db_detail.exit_load_pct or 0.0)
        else:
            fund_type = get_fund_type(t.scheme_name)
            advisor = resolve_advisor(t.scheme_name, t.folio_number, t.notes, db, None, owner_short_name=lot_acct_code)
            clean_name = t.scheme_name
            long_term_tenure = 365
            exit_load_tenure = 0
            exit_load_pct = 0.0

        # Resolve Current NAV
        holding_row = holdings_map.get(scheme_upper)
        if holding_row and holding_row.current_nav:
            current_nav = float(holding_row.current_nav)
        elif t.amfi_code and t.amfi_code in amfi_navs_map:
            current_nav = float(amfi_navs_map[t.amfi_code])
        else:
            current_nav = fallback_navs_map.get(scheme_upper, float(t.nav))

        current_value = rem_units * current_nav
        age_days = (today_date - t.transaction_date).days
        age_months = age_days / 30.4368

        active_lots.append({
            "id": t.id,
            "scheme_name": t.scheme_name,
            "clean_name": clean_name,
            "folio": t.folio_number,
            "units": rem_units,
            "purchase_nav": float(t.nav),
            "cost_value": cost_value,
            "current_nav": current_nav,
            "current_value": current_value,
            "transaction_date": t.transaction_date,
            "age_days": age_days,
            "age_months": age_months,
            "fund_type": fund_type,
            "advisor": advisor,
            "long_term_tenure": long_term_tenure,
            "exit_load_tenure": exit_load_tenure,
            "exit_load_pct": exit_load_pct,
            "account": lot_acct_code,
            "amfi_code": t.amfi_code
        })

    if not active_lots:
        return {
            "account_code": acct_code,
            "total_cost": 0.0,
            "total_value": 0.0,
            "total_gain": 0.0,
            "return_pct": 0.0,
            "total_xirr": 0.0,
            "asset_class_summary": [],
            "fund_type_summary": [],
            "detailed_assets": []
        }

    # Load lots into a DataFrame
    df = pd.DataFrame(active_lots)
    tot_purchase = df["cost_value"].sum()
    tot_value = df["current_value"].sum()

    # ---- 1) Asset Class Summary ----
    def classify_asset_class(fund_type):
        ft = str(fund_type).upper()
        if ft in ("LIQUID", "ARBITRAGE", "LOW DURATION", "ULTRA SHORT DURATION", "SHORT DURATION", "MONEY MARKET", "OVERNIGHT", "FLOATER", "CORPORATE BOND", "DEBT"):
            return "LIQUID/ARBITRAGE"
        elif ft == "GOLD":
            return "GOLD"
        elif ft == "SILVER":
            return "SILVER"
        return "EQUITY"

    df["Asset_Class"] = df["fund_type"].apply(classify_asset_class)

    asset_class_summary = []
    for asset_cls, g in df.groupby("Asset_Class"):
        p_val = float(g["cost_value"].sum())
        c_val = float(g["current_value"].sum())
        gain_loss_pct = ((c_val - p_val) / p_val * 100) if p_val > 0 else 0.0
        alloc_pct = (p_val / tot_purchase * 100) if tot_purchase > 0 else 0.0
        
        # Calculate XIRR for asset class
        dates = list(g["transaction_date"]) + [today_date]
        values = [-float(v) for v in g["cost_value"]] + [c_val]
        raw_xirr = calculate_xirr(dates, values)
        xirr_pct = round(raw_xirr * 100, 2) if raw_xirr is not None else None

        asset_class_summary.append({
            "asset_class": asset_cls,
            "purchase_value": p_val,
            "current_value": c_val,
            "return_pct": gain_loss_pct,
            "xirr_pct": xirr_pct,
            "allocation_pct": alloc_pct
        })

    # ---- 2) Fund Type Summary ----
    fund_type_summary = []
    for ft, g in df.groupby("fund_type"):
        p_val = float(g["cost_value"].sum())
        c_val = float(g["current_value"].sum())
        gain_loss_pct = ((c_val - p_val) / p_val * 100) if p_val > 0 else 0.0
        alloc_pct = (p_val / tot_purchase * 100) if tot_purchase > 0 else 0.0
        
        # Calculate XIRR for fund type
        dates = list(g["transaction_date"]) + [today_date]
        values = [-float(v) for v in g["cost_value"]] + [c_val]
        raw_xirr = calculate_xirr(dates, values)
        xirr_pct = round(raw_xirr * 100, 2) if raw_xirr is not None else None

        fund_type_summary.append({
            "fund_type": ft,
            "purchase_value": p_val,
            "current_value": c_val,
            "return_pct": gain_loss_pct,
            "xirr_pct": xirr_pct,
            "allocation_pct": alloc_pct
        })

    # ---- 3) Detailed Asset Table (Bifurcated by Advisor + Fund Name + Account + Folio) ----
    detailed_assets = []
    grouped = df.groupby(["advisor", "clean_name", "fund_type", "account", "folio"])
    
    hist_returns_cache = {}
    s_no = 1
    for (adv, name, f_type, act_c, folio), g in grouped:
        cost_val = float(g["cost_value"].sum())
        curr_val = float(g["current_value"].sum())
        
        # Resolve AMFI code for historical returns lookup
        first_row = g.iloc[0]
        amfi_code = first_row.get("amfi_code")
        if amfi_code not in hist_returns_cache:
            hist_returns_cache[amfi_code] = get_scheme_historical_returns(amfi_code, db)
        hist_returns = hist_returns_cache[amfi_code]
        
        # Calculate XIRR for this holding/group
        dates = list(g["transaction_date"]) + [today_date]
        values = [-float(v) for v in g["cost_value"]] + [curr_val]
        raw_xirr = calculate_xirr(dates, values)
        xirr_pct = round(raw_xirr * 100, 2) if raw_xirr is not None else None
        
        # Weighted period in months
        weighted_period = float((g["cost_value"] * g["age_months"]).sum() / cost_val) if cost_val > 0 else 0.0
        
        # Long-term vs Short-term metrics based on configured long_term_tenure
        first_row = g.iloc[0]
        lt_tenure = int(first_row.get("long_term_tenure", 365))
        
        units_lt = float(g[g["age_days"] > lt_tenure]["units"].sum())
        total_units = float(g["units"].sum())
        pct_units_lt = (units_lt / total_units * 100) if total_units > 0 else 0.0
        long_term_value = float(g[g["age_days"] > lt_tenure]["current_value"].sum())
        short_term_value = float(g[g["age_days"] <= lt_tenure]["current_value"].sum())
        
        # Exit load metrics based on specific lot age
        el_tenure = int(first_row.get("exit_load_tenure", 0))
        el_pct = float(first_row.get("exit_load_pct", 0.0))
        
        free_value = float(g[g["age_days"] > el_tenure]["current_value"].sum())
        subject_value = float(g[g["age_days"] <= el_tenure]["current_value"].sum())
        exit_load_amt = subject_value * (el_pct / 100.0)
        
        gain_loss_pct = ((curr_val - cost_val) / cost_val * 100) if cost_val > 0 else 0.0
        alloc_pct = (cost_val / tot_purchase * 100) if tot_purchase > 0 else 0.0
        
        detailed_assets.append({
            "s_no": s_no,
            "advisor": adv,
            "scheme_name": name,
            "folio": folio,
            "fund_type": f_type,
            "cost_value": cost_val,
            "current_value": curr_val,
            "raw_profit": curr_val - cost_val,
            "period": weighted_period,
            "xirr_pct": xirr_pct,
            
            "long_term_tenure": lt_tenure,
            "pct_long_term": pct_units_lt,
            "long_term_value": long_term_value,
            "short_term_value": short_term_value,
            
            "exit_load_tenure": el_tenure,
            "free_value": free_value,
            "subject_value": subject_value,
            "exit_load_amt": exit_load_amt,
            "exit_load_pct": el_pct,
            
            "return_pct": gain_loss_pct,
            "allocation_pct": alloc_pct,
            "account": act_c,
            
            # Historical Point-to-Point Returns
            "r_1m": hist_returns["r_1m"],
            "r_3m": hist_returns["r_3m"],
            "r_6m": hist_returns["r_6m"],
            "r_1y": hist_returns["r_1y"]
        })
        s_no += 1

    # Sort detailed assets by Allocation % descending
    detailed_assets = sorted(detailed_assets, key=lambda x: x["allocation_pct"], reverse=True)
    # Re-index S NO
    for idx, item in enumerate(detailed_assets):
        item["s_no"] = idx + 1

    total_gain = tot_value - tot_purchase
    total_return_pct = (total_gain / tot_purchase * 100) if tot_purchase > 0 else 0.0

    tot_dates = list(df["transaction_date"]) + [today_date]
    tot_values = [-float(v) for v in df["cost_value"]] + [tot_value]
    tot_xirr_raw = calculate_xirr(tot_dates, tot_values)
    total_xirr = round(tot_xirr_raw * 100, 2) if tot_xirr_raw is not None else None

    return {
        "account_code": acct_code,
        "total_cost": tot_purchase,
        "total_value": tot_value,
        "total_gain": total_gain,
        "return_pct": total_return_pct,
        "total_xirr": total_xirr,
        "asset_class_summary": asset_class_summary,
        "fund_type_summary": fund_type_summary,
        "detailed_assets": detailed_assets
    }


def get_scheme_historical_returns(amfi_code: str, db: Session) -> dict:
    """
    Calculates 1m, 3m, 6m, 1yr point-to-point returns for a given AMFI code
    with self-healing dynamic backfill from the AMFI API.
    """
    from datetime import date, timedelta
    from models.market_data import MFNav
    from services.nav_sync import sync_nav_for_amfi_code
    from sqlalchemy import func
    
    res = {"r_1m": None, "r_3m": None, "r_6m": None, "r_1y": None}
    if not amfi_code or str(amfi_code).strip() in ["", "0", "None"]:
        return res
        
    code_clean = str(amfi_code).strip().split('.')[0]
    today = date.today()
    
    try:
        # Check earliest date in database for this AMFI code
        min_date = db.query(func.min(MFNav.nav_date)).filter(MFNav.amfi_code == code_clean).scalar()
        
        # Disabled synchronous network fetch on page load to prevent slow load times/timeouts.
        # Historical sync should be performed via explicit sync operations or background seed tasks.
        # if not min_date or min_date > (today - timedelta(days=360)):
        #     print(f"Self-healing: Missing historical NAV for {code_clean} in report. Syncing history...")
        #     try:
        #         sync_nav_for_amfi_code(code_clean, db, fetch_history=True)
        #     except Exception as e:
        #         print(f"Self-healing failed: {e}")
                
        # Fetch all NAVs for this AMFI code sorted by date desc
        navs = db.query(MFNav.nav_date, MFNav.nav).filter(
            MFNav.amfi_code == code_clean
        ).order_by(MFNav.nav_date.desc()).all()
        
        if not navs:
            return res
            
        # Latest NAV is the first one
        latest_date, latest_nav = navs[0]
        latest_nav = float(latest_nav)
        
        # Helper to find NAV closest to a target date
        def find_closest_nav(target_date):
            best_nav = None
            min_diff = timedelta(days=15) # Only allow NAV within 15 days of target date
            for d, val in navs:
                diff = abs(d - target_date)
                if diff < min_diff:
                    min_diff = diff
                    best_nav = float(val)
            return best_nav

        nav_1m = find_closest_nav(today - timedelta(days=30))
        nav_3m = find_closest_nav(today - timedelta(days=90))
        nav_6m = find_closest_nav(today - timedelta(days=180))
        nav_1y = find_closest_nav(today - timedelta(days=365))
        
        if nav_1m: res["r_1m"] = ((latest_nav - nav_1m) / nav_1m) * 100
        if nav_3m: res["r_3m"] = ((latest_nav - nav_3m) / nav_3m) * 100
        if nav_6m: res["r_6m"] = ((latest_nav - nav_6m) / nav_6m) * 100
        if nav_1y: res["r_1y"] = ((latest_nav - nav_1y) / nav_1y) * 100
    except Exception as e:
        print(f"Error calculating historical returns for {code_clean}: {e}")
        
    return res
