import re
from datetime import datetime, date
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from models.mutual_funds import MFTransaction, MFHolding
from models.persons import Person
from models.accounts import Account
from models.enums import TransactionStatus
from services.fifo_engine import get_purchase_lots_remaining_units, resolve_advisor

def get_fund_type(scheme_name: str) -> str:
    """Classifies a mutual fund scheme name into fund types (Liquid, Arbitrage, Equity, etc.)."""
    name = str(scheme_name).upper()
    
    # Classifications matching processor.py
    if re.search(r'\bCHINA\b|\bGLOBAL\b', name):
        return 'ROW'
    if re.search(r'\bCANARA ROBECO SAVINGS\b', name):
        return 'LIQUID'
    if re.search(r'ARBITRAGE', name):
        return 'ARBITRAGE'
    if re.search(r'LIQUID|ULTRA SHORT|SHORT TERM|LOW DURATION|MONEY MARKET|OVERNIGHT', name):
        return 'LIQUID'
    if re.search(r'GOLD', name):
        return 'GOLD'
    if re.search(r'SILVER', name):
        return 'SILVER'
        
    return 'EQUITY'

def xnpv(rate, dates, values):
    """Calculates Net Present Value for a series of cash flows on specific dates."""
    if rate <= -1.0:
        return float('inf')
    t0 = dates[0]
    return sum([v / (1 + rate)**((d - t0).days / 365.0) for v, d in zip(values, dates)])

def calculate_xirr(dates, values) -> float:
    """Calculates internal rate of return (XIRR) using a robust bracketed solver."""
    if not dates or len(dates) != len(values):
        return None
        
    # Sort chronologically
    combined = sorted(zip(dates, values), key=lambda x: x[0])
    dates = [x[0] for x in combined]
    values = [x[1] for x in combined]
    
    # Check if we have both positive and negative values, otherwise XIRR is undefined
    has_pos = any(v > 0 for v in values)
    has_neg = any(v < 0 for v in values)
    if not (has_pos and has_neg):
        return None
    
    def _xnpv(rate):
        return xnpv(rate, dates, values)
        
    # Use brentq (bracketed solver) — guaranteed to find the correct root
    # Search for the sign change in the interval (-0.999, 10.0) i.e. -99.9% to 1000%
    try:
        from scipy.optimize import brentq
        
        # Find bracket where NPV changes sign
        # Test points from -99% to +1000%
        test_rates = [-0.99, -0.9, -0.8, -0.7, -0.6, -0.5, -0.4, -0.3, -0.2, -0.1,
                      0.0, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0, 5.0, 10.0]
        
        npv_values = []
        for r in test_rates:
            try:
                npv_values.append(_xnpv(r))
            except (ZeroDivisionError, OverflowError, ValueError):
                npv_values.append(None)
        
        # Check if any test rate is exactly a root
        for i, r in enumerate(test_rates):
            if npv_values[i] is not None and abs(npv_values[i]) < 1e-10:
                return float(r)

        # Find first sign change
        for i in range(len(test_rates) - 1):
            if npv_values[i] is not None and npv_values[i+1] is not None:
                if npv_values[i] * npv_values[i+1] < 0:
                    result = brentq(_xnpv, test_rates[i], test_rates[i+1], xtol=1e-10, maxiter=200)
                    if np.isfinite(result):
                        return float(result)
        
        return None  # No sign change found — XIRR undefined
    except ImportError:
        pass
        
    # Secant Method Fallback — use smart initial guess
    total_out = sum(v for v in values if v < 0)
    total_in = sum(v for v in values if v > 0)
    if total_in > abs(total_out):
        r0, r1 = 0.05, 0.15  # likely positive return
    else:
        r0, r1 = -0.3, -0.1  # likely negative return
    
    try:
        f0 = _xnpv(r0)
        f1 = _xnpv(r1)
    except (ZeroDivisionError, OverflowError):
        return None
    
    for _ in range(200):
        if abs(f1 - f0) < 1e-12:
            break
        r_next = r1 - f1 * (r1 - r0) / (f1 - f0)
        # Clamp to prevent divergence
        r_next = max(-0.999, min(r_next, 10.0))
        r0, r1 = r1, r_next
        f0 = f1
        try:
            f1 = _xnpv(r1)
        except (ZeroDivisionError, OverflowError):
            return None
        if abs(f1) < 1e-8:
            if np.isfinite(r1):
                return r1
            return None
            
    # Solver did not converge
    return None


def calculate_cagr(beginning_value: float, ending_value: float, years: float) -> float:
    """Calculates Compound Annual Growth Rate."""
    if beginning_value <= 0 or ending_value <= 0 or years <= 0:
        return 0.0
    return (ending_value / beginning_value)**(1 / years) - 1

def get_performance_report(db: Session, person_id: int = None) -> dict:
    """
    Computes XIRR & CAGR for mutual fund holdings, excluding Liquid and Arbitrage funds.
    Aggregates results by Scheme, Account, and Advisor level.
    """
    today_date = date.today()
    
    # 1. Fetch transactions
    query = db.query(MFTransaction, Person.short_name.label("owner_name"), Account.account_number.label("acct_num")).join(
        Person, Person.id == MFTransaction.person_id
    ).join(
        Account, Account.id == MFTransaction.account_id
    ).filter(
        MFTransaction.status == TransactionStatus.ACTIVE
    )
    
    if person_id:
        query = query.filter(MFTransaction.person_id == person_id)
        
    txns = query.all()
    if not txns:
        return {"schemes": [], "accounts": [], "advisors": []}
        
    # Build dataframe for processing
    txn_rows = []
    for item in txns:
        t, owner, acct_num = item
        
        # Check fund type to exclude Liquid / Arbitrage
        fund_type = get_fund_type(t.scheme_name)
        if fund_type in ["LIQUID", "ARBITRAGE"]:
            continue
            
        # Determine Advisor
        advisor = resolve_advisor(t.scheme_name, t.folio_number, t.notes, db, owner_short_name=owner)
            
        txn_rows.append({
            "scheme": t.scheme_name,
            "owner": owner,
            "account": acct_num or "Main",
            "advisor": advisor,
            "amount": float(t.amount),
            "date": t.transaction_date,
            "isin": t.isin,
            "amfi": t.amfi_code,
            "type": t.transaction_type
        })
        
    if not txn_rows:
        return {"schemes": [], "accounts": [], "advisors": []}
        
    df_txns = pd.DataFrame(txn_rows)
    
    # 2. Fetch current holdings AUM
    hold_query = db.query(MFHolding, Person.short_name.label("owner_name"), Account.account_number.label("acct_num")).join(
        Person, Person.id == MFHolding.person_id
    ).join(
        Account, Account.id == MFHolding.account_id
    )
    
    if person_id:
        hold_query = hold_query.filter(MFHolding.person_id == person_id)
        
    holdings = hold_query.all()
    
    hold_rows = []
    for item in holdings:
        h, owner, acct_num = item
        fund_type = get_fund_type(h.scheme_name)
        if fund_type in ["LIQUID", "ARBITRAGE"]:
            continue
            
        # Determine Advisor
        advisor = resolve_advisor(h.scheme_name, h.folio_number, "", db, owner_short_name=owner)
            
        hold_rows.append({
            "scheme": h.scheme_name,
            "owner": owner,
            "account": acct_num or "Main",
            "advisor": advisor,
            "units": float(h.units),
            "current_value": float(h.current_value) if h.current_value else 0.0,
            "cost_value": float(h.units) * float(h.average_nav) if h.average_nav else 0.0,
            "isin": h.isin,
            "amfi": h.amfi_code
        })
        
    df_holdings = pd.DataFrame(hold_rows) if hold_rows else pd.DataFrame(columns=["scheme", "owner", "account", "advisor", "units", "current_value", "cost_value", "isin", "amfi"])
    
    def run_performance_calculations(transactions_df, holdings_df, group_cols, level_name):
        results = []
        for group_keys, group_txns in transactions_df.groupby(group_cols):
            # Resolve filters for holdings
            hold_filter = True
            for col, val in zip(group_cols, group_keys if isinstance(group_keys, tuple) else [group_keys]):
                hold_filter = hold_filter & (holdings_df[col] == val)
                
            group_holdings = holdings_df[hold_filter]
            curr_val = group_holdings["current_value"].sum() if not group_holdings.empty else 0.0
            
            # Setup cash flows: negative of amount for investments (outflows), positive for withdrawals (inflows)
            # PURCHASE, SWITCH_IN are outflows (negative)
            # REDEMPTION, SWITCH_OUT, IDCW_PAYOUT are inflows (positive)
            flows = []
            dates = []
            
            total_purchases = 0.0
            total_withdrawals = 0.0
            
            from datetime import date as dt_date, datetime as dt_datetime
            group_txns_list = group_txns.to_dict('records')
            for row in group_txns_list:
                t_type = row["type"].upper()
                amt = row["amount"]
                
                if t_type in ["PURCHASE", "PURCHASE_SIP", "SWITCH_IN", "DIVIDEND_REINVESTMENT"]:
                    flows.append(-abs(amt))
                    total_purchases += abs(amt)
                elif t_type in ["REDEMPTION", "SWITCH_OUT", "DIVIDEND_PAYOUT", "IDCW_PAYOUT"]:
                    flows.append(abs(amt))
                    total_withdrawals += abs(amt)
                else:
                    # Ignore taxes or fees for standard cash flow XIRR
                    continue
                    
                dates.append(row["date"])
                
            if not flows:
                continue
                
            # If there is current value, add it as a final positive cash flow on TODAY
            if curr_val > 0.001:
                flows.append(curr_val)
                dates.append(today_date)
                
            # Convert dates to datetime.date objects for subtraction
            resolved_dates = []
            for d in dates:
                if isinstance(d, dt_datetime):
                    resolved_dates.append(d.date())
                elif isinstance(d, dt_date):
                    resolved_dates.append(d)
                else:
                    resolved_dates.append(pd.to_datetime(d).date())
            dates = resolved_dates
            
            first_invest_date = min(dates)
            last_invest_date = max(dates)
            years = (today_date - first_invest_date).days / 365.25
            
            # Net investment
            total_net_investment = total_purchases - total_withdrawals
            
            # Compute XIRR
            xirr_val = calculate_xirr(dates, flows)
            # Compute CAGR
            cagr_val = calculate_cagr(total_purchases, curr_val + total_withdrawals, years)
            
            if level_name == "Scheme":
                name = group_keys[2]
            elif level_name == "Account":
                name = group_keys[1]
            else:
                name = group_keys[0] if isinstance(group_keys, tuple) else group_keys
            
            summary_dict = {
                "name": name,
                "first_date": first_invest_date,
                "last_date": last_invest_date,
                "years": round(years, 2),
                "total_purchases": total_purchases,
                "total_withdrawals": total_withdrawals,
                "net_investment": total_net_investment,
                "current_value": curr_val,
                "pnl": curr_val - total_net_investment,
                "cagr": cagr_val,
                "xirr": xirr_val
            }
            
            # Add secondary descriptors if applicable
            if level_name == "Scheme":
                summary_dict.update({
                    "owner": group_keys[1],
                    "advisor": group_keys[0]
                })
            elif level_name == "Account":
                summary_dict.update({
                    "owner": group_keys[1],
                    "advisor": group_keys[0]
                })
                
            results.append(summary_dict)
            
        # Sort results by current value descending
        return sorted(results, key=lambda x: x["current_value"], reverse=True)
        
    scheme_perf = run_performance_calculations(df_txns, df_holdings, ["advisor", "owner", "scheme"], "Scheme")
    account_perf = run_performance_calculations(df_txns, df_holdings, ["advisor", "owner"], "Account")
    advisor_perf = run_performance_calculations(df_txns, df_holdings, ["advisor"], "Advisor")
    
    return {
        "schemes": scheme_perf,
        "accounts": account_perf,
        "advisors": advisor_perf
    }

def get_fund_type_performance_report(db: Session) -> list:
    """
    Computes performance metrics grouped by Fund Type based on the formulas in the notebook:
    - Total Invested (Cost Value)
    - Current Value
    - Weighted Period (weighted by cost value, in months)
    - Exposure (% of grand total invested)
    - CAGR (%) = (Current Value / Total Invested) ** (1 / (Weighted Period / 12)) - 1
    - Percentage Change (%) = (Current Value - Total Invested) / Total Invested * 100
    """
    # 1. Fetch remaining units
    remaining_units_dict = get_purchase_lots_remaining_units(db)

    # 2. Fetch all active purchase transactions
    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT']
    txns = db.query(MFTransaction).filter(
        MFTransaction.status == TransactionStatus.ACTIVE,
        MFTransaction.transaction_type.in_(purchase_types)
    ).all()

    # 3. Get holdings maps for latest NAVs
    holdings = db.query(MFHolding).all()
    holdings_map = {h.scheme_name.upper(): h for h in holdings}

    # 4. Get mf_details for custom fund types
    from models.mutual_funds import MFDetail
    details_rows = db.query(MFDetail).all()
    details_map = {d.scheme_name.upper(): d for d in details_rows}

    active_lots = []
    today_date = date.today()

    for t in txns:
        rem_units = remaining_units_dict.get(t.id, 0.0)
        if rem_units <= 0.001:
            continue

        cost_value = rem_units * float(t.nav)
        
        # Resolve Current NAV and Fund Type
        scheme_upper = t.scheme_name.upper()
        current_nav = float(t.nav)
        db_detail = details_map.get(scheme_upper)
        
        if db_detail:
            fund_type = db_detail.fund_type or get_fund_type(t.scheme_name)
        else:
            fund_type = get_fund_type(t.scheme_name)

        holding_row = holdings_map.get(scheme_upper)
        if holding_row and holding_row.current_nav:
            current_nav = float(holding_row.current_nav)
        else:
            from models.market_data import MFNav
            nav_row = db.query(MFNav).filter(MFNav.scheme_name == t.scheme_name).order_by(MFNav.nav_date.desc()).first()
            if nav_row and nav_row.nav:
                current_nav = float(nav_row.nav)

        current_value = rem_units * current_nav
        age_days = (today_date - t.transaction_date).days
        age_months = age_days / 30.4368

        active_lots.append({
            "units": rem_units,
            "cost_value": cost_value,
            "current_value": current_value,
            "age_months": age_months,
            "fund_type": fund_type.strip().upper() if fund_type else "OTHER"
        })

    if not active_lots:
        return []

    df = pd.DataFrame(active_lots)
    grand_total_invested = df["cost_value"].sum()

    grouped = df.groupby("fund_type")
    report_data = []

    for f_type, g in grouped:
        total_invested = float(g["cost_value"].sum())
        current_val = float(g["current_value"].sum())
        
        # Weighted average period in months (weights are cost_value)
        weighted_period = float((g["cost_value"] * g["age_months"]).sum() / total_invested) if total_invested > 0 else 0.0
        
        # Exposure % relative to grand total
        exposure = (total_invested / grand_total_invested * 100.0) if grand_total_invested > 0 else 0.0
        
        # Percentage change (Absolute Return %)
        percentage_change = ((current_val - total_invested) / total_invested * 100.0) if total_invested > 0 else 0.0
        
        # CAGR calculation
        years = weighted_period / 12.0
        if total_invested > 0 and current_val > 0 and years > 0:
            cagr = ((current_val / total_invested) ** (1.0 / years) - 1.0) * 100.0
        else:
            cagr = 0.0

        report_data.append({
            "fund_type": f_type,
            "total_invested": total_invested,
            "current_value": current_val,
            "weighted_period": weighted_period,
            "exposure": exposure,
            "cagr": cagr,
            "percentage_change": percentage_change
        })

    # Sort alphabetically by Fund Type as shown in the Excel sheet
    return sorted(report_data, key=lambda x: x["fund_type"])
