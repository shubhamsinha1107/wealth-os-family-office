# services/aum_performance_engine.py
import calendar
from datetime import date, timedelta
from sqlalchemy import func
from models.mutual_funds import MFTransaction, MFDetail
from models.market_data import MFNav
from models.enums import TransactionStatus

# Maps granular fund types to 3 simplified super-categories for user-facing AUM filters
FUND_TYPE_CATEGORY_MAP = {
    # EQUITY covers all equity sub-types
    'AUTO': 'EQUITY', 'CONSUMER': 'EQUITY', 'DEFENCE': 'EQUITY', 'ENERGY': 'EQUITY',
    'ENERGY & INFRA': 'EQUITY', 'FINANCE': 'EQUITY', 'FLEXI': 'EQUITY',
    'FOCUSED FUND': 'EQUITY', 'FOCUSED': 'EQUITY', 'HEALTHCARE': 'EQUITY',
    'INFRA': 'EQUITY', 'IT': 'EQUITY', 'LARGE & MID': 'EQUITY', 'LARGE CAP': 'EQUITY',
    'MANUFACTURE': 'EQUITY', 'MIDCAP': 'EQUITY', 'MOMENTUM': 'EQUITY',
    'MULTICAP': 'EQUITY', 'MULTI-ASSET': 'EQUITY', 'PSU FUND': 'EQUITY',
    'ROW': 'EQUITY', 'SMALLCAP': 'EQUITY', 'VALUE': 'EQUITY',
    'UNKNOWN': 'EQUITY', 'EQUITY': 'EQUITY',
    'ELSS': 'EQUITY', 'CONTRA': 'EQUITY', 'BALANCED ADVANTAGE': 'EQUITY', 'THEMATIC': 'EQUITY',
    # LIQUID covers liquid + arbitrage + debt categories
    'LIQUID': 'LIQUID', 'ARBITRAGE': 'LIQUID',
    'LOW DURATION': 'LIQUID', 'ULTRA SHORT DURATION': 'LIQUID', 'SHORT DURATION': 'LIQUID',
    'MONEY MARKET': 'LIQUID', 'OVERNIGHT': 'LIQUID', 'FLOATER': 'LIQUID', 'CORPORATE BOND': 'LIQUID', 'DEBT': 'LIQUID',
    # BULLION covers gold + silver
    'GOLD': 'BULLION', 'SILVER': 'BULLION', 'BULLION': 'BULLION',
}

# The 3 user-facing super-categories for AUM filters
AUM_FUND_TYPE_CATEGORIES = ['EQUITY', 'LIQUID', 'BULLION']

def resolve_fund_type_category(granular_type: str) -> str:
    """Resolve a granular fund type to its super-category (EQUITY, LIQUID, BULLION)."""
    return FUND_TYPE_CATEGORY_MAP.get(granular_type.upper().strip(), 'EQUITY')

def expand_category_to_types(categories: set) -> set:
    """Expand super-categories to the set of all granular fund types that belong to them."""
    expanded = set()
    for cat in categories:
        cat_upper = cat.upper().strip()
        for granular, mapped_cat in FUND_TYPE_CATEGORY_MAP.items():
            if mapped_cat == cat_upper:
                expanded.add(granular)
    return expanded

def get_aum_performance_report(db, person_id=None, advisor="ALL", fund_type="ALL") -> dict:
    """
    Computes monthly AUM performance (Opening AUM, Net Cash Flows, Organic Gain, 
    Dietz Return %, and Ending AUM) for a given portfolio/account, advisor, and fund type.
    """
    # 1. Fetch all active transactions for this owner
    txns_query = db.query(MFTransaction).filter(
        MFTransaction.status == TransactionStatus.ACTIVE
    )
    if person_id:
        if isinstance(person_id, (list, tuple, set)):
            txns_query = txns_query.filter(MFTransaction.person_id.in_(person_id))
        else:
            txns_query = txns_query.filter(MFTransaction.person_id == person_id)
    txns = txns_query.order_by(MFTransaction.transaction_date.asc(), MFTransaction.id.asc()).all()

    if not txns:
        return {"monthly_data": [], "advisors": [], "fund_types": []}

    # 2. Fetch scheme details to map advisor and fund type
    details = db.query(MFDetail).all()
    details_map = {d.scheme_name.upper(): d for d in details}

    # 3. Filter transactions based on advisor and fund type
    filtered_txns = []
    sel_adv = advisor.strip().upper()
    
    # Normalize fund types to a set of uppercase strings
    if fund_type is None:
        selected_types = {"ALL"}
    elif isinstance(fund_type, str):
        selected_types = {x.strip().upper() for x in fund_type.split(",") if x.strip()}
    else:
        selected_types = {str(x).strip().upper() for x in fund_type if str(x).strip()}
        
    if not selected_types:
        selected_types = {"ALL"}

    # Expand super-categories (EQUITY, LIQUID, BULLION) to granular types for matching
    if "ALL" not in selected_types:
        expanded_types = expand_category_to_types(selected_types)
        # Also keep original selected types for direct matching
        expanded_types.update(selected_types)
    else:
        expanded_types = selected_types

    for t in txns:
        scheme_upper = t.scheme_name.upper()
        db_detail = details_map.get(scheme_upper)
        
        adv = (db_detail.advisor if db_detail else "DIRECT") or "DIRECT"
        ft = (db_detail.fund_type if db_detail else "EQUITY") or "EQUITY"

        # Apply Advisor Filter
        if sel_adv != "ALL":
            adv_list = [a.strip().upper() for a in adv.split(",")]
            if sel_adv not in adv_list:
                continue

        # Apply Fund Type Filter (supports super-categories expanded to granular types)
        if "ALL" not in selected_types:
            if ft.upper() not in expanded_types:
                continue

        filtered_txns.append(t)

    if not filtered_txns:
        return {"monthly_data": [], "advisors": [], "fund_types": []}

    # Convert to plain dictionaries to avoid SQLAlchemy ORM property access overhead in double loops
    txn_records = []
    for t in filtered_txns:
        txn_records.append({
            "person_id": t.person_id,
            "account_id": t.account_id,
            "folio_number": t.folio_number,
            "scheme_name": t.scheme_name,
            "amfi_code": t.amfi_code,
            "units": float(t.units) if t.units else 0.0,
            "transaction_type": t.transaction_type,
            "transaction_date": t.transaction_date,
            "amount": float(t.amount) if t.amount else 0.0,
            "nav": float(t.nav) if t.nav else 0.0
        })

    # 4. Resolve years/months to compute
    first_date = txn_records[0]["transaction_date"]
    today = date.today()
    
    # Generate list of (year, month) from first transaction date to current date
    start_year, start_month = first_date.year, first_date.month
    end_year, end_month = today.year, today.month
    
    months_to_compute = []
    curr_y, curr_m = start_year, start_month
    while (curr_y < end_year) or (curr_y == end_year and curr_m <= end_month):
        months_to_compute.append((curr_y, curr_m))
        curr_m += 1
        if curr_m > 12:
            curr_m = 1
            curr_y += 1

    # 5. Build bulk cache of NAVs for efficiency
    # Cache format: (scheme_name, target_date) -> nav
    nav_cache = {}
    
    # Pre-fetch all NAVs for relevant schemes to eliminate N+1 queries
    schemes_to_fetch = set()
    amfis_to_fetch = set()
    for t in txn_records:
        schemes_to_fetch.add(t["scheme_name"])
        if t["amfi_code"] and t["amfi_code"] != "0":
            amfis_to_fetch.add(t["amfi_code"])

    navs_by_amfi = {}
    navs_by_scheme = {}
    if schemes_to_fetch or amfis_to_fetch:
        from sqlalchemy import or_
        nav_rows = db.query(
            MFNav.scheme_name,
            MFNav.amfi_code,
            MFNav.nav_date,
            MFNav.nav
        ).filter(
            or_(
                MFNav.amfi_code.in_(list(amfis_to_fetch)),
                MFNav.scheme_name.in_(list(schemes_to_fetch))
            )
        ).all()
        for row in nav_rows:
            s_name, amfi, n_date, nav_val = row
            if amfi and amfi != "0":
                navs_by_amfi.setdefault(amfi, []).append((n_date, float(nav_val)))
            if s_name:
                navs_by_scheme.setdefault(s_name, []).append((n_date, float(nav_val)))

        # Sort histories chronologically
        for amfi in navs_by_amfi:
            navs_by_amfi[amfi].sort(key=lambda x: x[0])
        for s_name in navs_by_scheme:
            navs_by_scheme[s_name].sort(key=lambda x: x[0])

    # Bulk-fetch txn NAV fallbacks from txn_records in memory
    txn_navs_by_scheme = {}
    for t in txn_records:
        if t["scheme_name"] and t["nav"]:
            txn_navs_by_scheme.setdefault(t["scheme_name"], []).append((t["transaction_date"], t["nav"]))
    for s_name in txn_navs_by_scheme:
        txn_navs_by_scheme[s_name].sort(key=lambda x: x[0])

    from bisect import bisect_right
    def find_latest_in_history(history, target_dt):
        dates_list = [x[0] for x in history]
        idx = bisect_right(dates_list, target_dt)
        if idx > 0:
            return history[idx - 1][1]
        return 0.0

    def get_scheme_nav_on_date(scheme_name: str, amfi_code: str, target_date: date) -> float:
        cache_key = (scheme_name, amfi_code, target_date)
        if cache_key in nav_cache:
            return nav_cache[cache_key]

        val = 0.0
        # 1. AMFI lookup
        if amfi_code and amfi_code != "0" and amfi_code in navs_by_amfi:
            val = find_latest_in_history(navs_by_amfi[amfi_code], target_date)

        # 2. Scheme lookup fallback
        if val == 0.0 and scheme_name in navs_by_scheme:
            val = find_latest_in_history(navs_by_scheme[scheme_name], target_date)

        # 3. Transaction NAV fallback
        if val == 0.0 and scheme_name in txn_navs_by_scheme:
            val = find_latest_in_history(txn_navs_by_scheme[scheme_name], target_date)

        nav_cache[cache_key] = val
        return val

    def get_holdings_on_date(target_date: date) -> dict:
        # Replicate holdings calculation: group by person/folio/scheme/amfi and clamp redemptions to 0
        holdings_dict = {}
        for t in txn_records:
            if t["transaction_date"] <= target_date:
                key = (t["person_id"], t["account_id"], t["folio_number"], t["scheme_name"], t["amfi_code"])
                if key not in holdings_dict:
                    holdings_dict[key] = 0.0
                
                units = t["units"]
                txn_type = t["transaction_type"].upper()
                
                # Replicate update_holdings_for_person's exact filter
                if txn_type in ["PURCHASE", "PURCHASE_SIP", "SWITCH_IN", "DIVIDEND_REINVESTMENT"]:
                    holdings_dict[key] += abs(units)
                elif txn_type in ["REDEMPTION", "SWITCH_OUT"]:
                    holdings_dict[key] -= abs(units)
                    if holdings_dict[key] < 0.0:
                        holdings_dict[key] = 0.0
        return {k: u for k, u in holdings_dict.items() if u > 0.0001}

    def calculate_holdings_value_on_date(holdings: dict, val_date: date) -> float:
        total_value = 0.0
        for key, units in holdings.items():
            person_id, account_id, folio, scheme, amfi = key
            nav = get_scheme_nav_on_date(scheme, amfi, val_date)
            total_value += units * nav
        return total_value

    def calculate_aum_on_date(target_date: date) -> float:
        holdings = get_holdings_on_date(target_date)
        return calculate_holdings_value_on_date(holdings, target_date)

    def calculate_subset_static_return(start_holdings: dict, prev_end_date: date, end_date: date, allowed_types: set) -> float:
        filtered_holdings = {}
        for key, units in start_holdings.items():
            person_id, account_id, folio, scheme, amfi = key
            db_detail = details_map.get(scheme.upper())
            ft = (db_detail.fund_type if db_detail else "EQUITY") or "EQUITY"
            if ft.upper().strip() in allowed_types:
                filtered_holdings[key] = units
                
        if not filtered_holdings:
            return None
            
        opening_value = calculate_holdings_value_on_date(filtered_holdings, prev_end_date)
        ending_value = calculate_holdings_value_on_date(filtered_holdings, end_date)
        
        return ((ending_value - opening_value) / opening_value * 100.0) if opening_value > 0.0001 else 0.0

    # 6. Compute metrics for each month
    monthly_data = []
    
    # Store the calculated ending AUMs to avoid redundant recalculations
    aum_cache = {}

    for year, month in months_to_compute:
        _, last_day = calendar.monthrange(year, month)
        end_date = date(year, month, last_day)
        start_date = date(year, month, 1)
        prev_end_date = start_date - timedelta(days=1)

        # Get initial holdings at start of month (end of previous month)
        start_holdings = get_holdings_on_date(prev_end_date)

        # Retrieve or compute opening AUM
        if prev_end_date not in aum_cache:
            aum_cache[prev_end_date] = calculate_holdings_value_on_date(start_holdings, prev_end_date)
        opening_aum = aum_cache[prev_end_date]

        # Retrieve or compute ending AUM
        if end_date not in aum_cache:
            aum_cache[end_date] = calculate_aum_on_date(end_date)
        ending_aum = aum_cache[end_date]

        # Calculate static value of the opening holdings at ending NAVs
        static_ending_value = calculate_holdings_value_on_date(start_holdings, end_date)
        static_return = ((static_ending_value - opening_aum) / opening_aum * 100.0) if opening_aum > 0.0001 else 0.0

        # Calculate returns of specific subsets
        large_cap_return = calculate_subset_static_return(start_holdings, prev_end_date, end_date, {'LARGE CAP'})
        gold_return = calculate_subset_static_return(start_holdings, prev_end_date, end_date, {'GOLD', 'BULLION'})
        midcap_return = calculate_subset_static_return(start_holdings, prev_end_date, end_date, {'MIDCAP'})

        # Extract transactions in the month for cash flow calculations
        month_txns = [t for t in txn_records if t["transaction_date"] >= start_date and t["transaction_date"] <= end_date]

        total_cash_flow = 0.0
        weighted_cash_flow = 0.0
        total_days = float(last_day)

        for t in month_txns:
            # Positive for inflows (purchases), negative for outflows (redemptions)
            inflow_types = ["PURCHASE", "PURCHASE_SIP", "SWITCH_IN", "STAMP_DUTY_TAX", "DIVIDEND_REINVESTMENT", "DIVIDEND_REINVEST", "MISC"]
            is_inflow = t["transaction_type"].upper() in inflow_types
            amount_val = t["amount"]
            # Use abs() to ensure correct signed cash flow direction regardless of database sign
            cf_val = abs(amount_val) if is_inflow else -abs(amount_val)

            day = float(t["transaction_date"].day)
            # Modified Dietz weight: (Total Days - Day of cash flow) / Total Days
            weight = (total_days - day) / total_days

            total_cash_flow += cf_val
            weighted_cash_flow += cf_val * weight

        # Organic Net Investment gain/loss
        organic_gain = ending_aum - opening_aum - total_cash_flow

        # Modified Dietz rate of return
        denom = opening_aum + weighted_cash_flow
        dietz_return = (organic_gain / denom * 100.0) if denom > 0.0001 else 0.0

        # Only display months where opening_aum, ending_aum, or cash flow is non-zero
        if opening_aum > 0.01 or ending_aum > 0.01 or abs(total_cash_flow) > 0.01:
            monthly_data.append({
                "year": year,
                "month": month,
                "month_name": calendar.month_abbr[month],
                "opening_aum": opening_aum,
                "ending_aum": ending_aum,
                "net_cash_flow": total_cash_flow,
                "organic_gain": organic_gain,
                "dietz_return": dietz_return,
                "static_return": static_return,
                "large_cap_return": large_cap_return,
                "gold_return": gold_return,
                "midcap_return": midcap_return
            })

    # Sort monthly_data descending (latest month first)
    monthly_data.sort(key=lambda x: (x["year"], x["month"]), reverse=True)

    # Collect list of advisors for filter display
    all_advisors = set()
    for d in details:
        adv = (d.advisor or "DIRECT").strip()
        if adv:
            for a in adv.split(","):
                val = a.strip()
                if val: all_advisors.add(val)

    return {
        "monthly_data": monthly_data,
        "advisors": sorted(list(all_advisors)),
        "fund_types": AUM_FUND_TYPE_CATEGORIES  # Simplified 3-category filter: EQUITY, LIQUID, BULLION
    }
