import os
import glob
import re
from datetime import datetime
import pandas as pd
from decimal import Decimal
from sqlalchemy.orm import Session
from models.persons import Person
from models.alternatives import UnlistedShare, Alternative
from models.mutual_funds import MFTransaction, MFHolding, MFDetail
from models.enums import TransactionStatus
from services.fifo_engine import get_purchase_lots_remaining_units

def get_latest_global_positions_file() -> str:
    """
    Locates the latest Global Positions Report Excel file inside the DATA/GLOBAL REPORT directory.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report_dir = os.path.join(base_dir, "DATA", "GLOBAL REPORT")
    if not os.path.exists(report_dir):
        return None
        
    files = glob.glob(os.path.join(report_dir, "Global Positions Report - As on *.xlsx"))
    files = [f for f in files if not os.path.basename(f).startswith("~$")]
    
    if not files:
        return None
        
    def parse_file_date(filepath):
        basename = os.path.basename(filepath)
        match = re.search(r"As on (\d{2}-\d{2}-\d{2})", basename)
        if match:
            try:
                # format DD-MM-YY (e.g. 24-07-26 -> 2026-07-24)
                return datetime.strptime(match.group(1), "%d-%m-%y")
            except Exception:
                pass
        return datetime.fromtimestamp(os.path.getmtime(filepath))

    return max(files, key=parse_file_date)

def parse_unlisted_shares_excel(file_path: str) -> list:
    if not os.path.exists(file_path):
        print(f"Warning: Unlisted Shares Excel file not found at: {file_path}")
        return []
    try:
        df = pd.read_excel(file_path, sheet_name="UNLISTED (2)")
        df.columns = [str(x).strip() for x in df.iloc[1]]
        df = df.iloc[2:].reset_index(drop=True)
        df = df[df["S NO"].notna()]
        df = df[df["S NO"].astype(str).str.strip().str.isdigit()]

        def get_owner(ac_str):
            s = str(ac_str).upper()
            if "PATRIARCH" in s: return "PATRIARCH"
            if "MEMBER_1" in s: return "MEMBER_1"
            if "APEX" in s: return "APEX"
            if "MEMBER_2" in s: return "MEMBER_2"
            if "MEMBER_3" in s: return "MEMBER_3"
            return "APEX" # Default fallback

        def get_broker(ac_str):
            s = str(ac_str).upper()
            if "JM" in s or "BROKER 1" in s or "BROKER_1" in s or "BROKER1" in s: return "Broker 1"
            if "CENTRUM" in s or "CBL" in s or "BROKER 2" in s or "BROKER_2" in s or "BROKER2" in s: return "Broker 2"
            if "NUVAMA" in s or "NUV" in s or "BROKER 3" in s or "BROKER_3" in s or "BROKER3" in s: return "Broker 3"
            return "Broker 1"

        results = []
        for _, row in df.iterrows():
            ac = row["A/C"]
            owner = get_owner(ac)
            broker = get_broker(ac)
            qty = pd.to_numeric(row["QTY"], errors='coerce') or 0.0
            avg_price = pd.to_numeric(row["AVG PRICE"], errors='coerce') or 0.0
            cmp = pd.to_numeric(row["CMP"], errors='coerce') or 0.0
            cost = qty * avg_price
            curr = qty * cmp
            results.append({
                "owner": owner,
                "broker": broker,
                "company_name": str(row["STOCK"]),
                "cost": cost,
                "current_value": curr
            })
        return results
    except Exception as e:
        print(f"Error parsing UNLISTED SHARES.xlsx: {e}")
        return []

def parse_aif_excel(file_path: str) -> list:
    if not os.path.exists(file_path):
        print(f"Warning: AIF Data Excel file not found at: {file_path}")
        return []
    try:
        df = pd.read_excel(file_path, sheet_name="main")
        df.columns = [str(x).strip() for x in df.iloc[1]]
        df = df.iloc[2:].reset_index(drop=True)
        
        # Filter out empty or total rows
        df = df[df["A/C"].notna()]
        df = df[df["A/C"].astype(str).str.strip().isin(["APEX", "MEMBER_1", "PATRIARCH", "MEMBER_2", "MEMBER_3"])]
        
        results = []
        for _, row in df.iterrows():
            owner = str(row["A/C"]).strip().upper()
            scheme = str(row["FUND NAME"]).strip()
            cost = pd.to_numeric(row["INVESTMENT"], errors='coerce') or 0.0
            curr = pd.to_numeric(row["CURRENT VALUE"], errors='coerce') or 0.0
            results.append({
                "owner": owner,
                "scheme_name": scheme,
                "cost": cost,
                "current_value": curr
            })
        return results
    except Exception as e:
        print(f"Error parsing AIF DATA.xlsx: {e}")
        return []

def parse_global_positions_report(file_path: str) -> pd.DataFrame:
    """
    Parses and cleans the Global Positions Report Excel file using the exact notebook logic.
    """
    report = pd.read_excel(file_path)
    
    # Clean up the DataFrame
    report = report.dropna(axis=1, how='all')
    report = report.dropna(axis=0, how='all')
    report = report[~report.iloc[:, -1].astype(str).str.contains("Powered by MProfit", na=False)]
    report = report[report.notna().sum(axis=1) >= 4]
    report.reset_index(drop=True, inplace=True)

    # Clean columns using robust logic
    expected_cols = ["portfolio name", "asset class", "asset name", "qty", "amount invested"]
    col_str = [str(c).strip().lower() for c in report.columns]
    match_count = sum(col in col_str for col in expected_cols)

    if match_count < 3:
        header_row_idx = None
        for i in range(min(50, len(report))):
            row_vals = [str(x).strip().lower() for x in report.iloc[i].tolist() if pd.notna(x)]
            if sum(col in row_vals for col in expected_cols) >= 3:
                header_row_idx = i
                break
        if header_row_idx is not None:
            report.columns = [str(x).strip() for x in report.iloc[header_row_idx]]
            report = report.iloc[header_row_idx + 1:].reset_index(drop=True)

    # Exclude specified asset classes
    exclude_asset_classes = ["Other F & O", "Insurance", "Mutual Funds", "AIF", "Private Equity"]
    report = report[~report['Asset Class'].isin(exclude_asset_classes)]

    report = report.drop(columns=["Folio / Ref #", "Is F&O Expired?"], errors='ignore')

    # Clean "Portfolio Name" column
    pattern = re.compile(r'\s*-?\s*(m\.?f\.?|mutual fund)', flags=re.IGNORECASE)
    report["Portfolio Name"] = report["Portfolio Name"].apply(lambda s: pattern.sub('', s).strip())

    # Convert numeric columns
    report["Amount Invested"] = pd.to_numeric(report["Amount Invested"], errors='coerce').fillna(0.0)
    report["Qty"] = pd.to_numeric(report["Qty"], errors='coerce').fillna(0.0)
    report["Current Price"] = pd.to_numeric(report["Current Price"], errors='coerce').fillna(0.0)

    # Filter out rows where "Amount Invested" is below 1000
    report = report[report["Amount Invested"] >= 1000]
    report.reset_index(drop=True, inplace=True)
    report['Current Value'] = report['Current Price'] * report['Qty']

    return report

def get_consolidated_portfolio_data(db: Session) -> dict:
    """
    Dynamically fetches and aggregates asset cost and current valuations.
    Stocks, Gold ETF, Silver ETF, and SGB are parsed from the latest Global Positions Report Excel file.
    Unlisted Shares, AIF/PMS, and Mutual Funds are queried directly from the database.
    """
    # 1. Map person IDs to family codes
    portfolio_to_code = {
        "APEX HOLDINGS PVT. LTD.": "APEX",
        "APEX HOLDINGS PRIVATE LIMITED": "APEX",
        "APEX": "APEX",
        "FAMILY MEMBER 1": "MEMBER_1",
        "MEMBER_1": "MEMBER_1",
        "FAMILY PATRIARCH": "PATRIARCH",
        "FAMILY PATRIARCH": "PATRIARCH",
        "PATRIARCH": "PATRIARCH",
        "FAMILY MEMBER 2": "MEMBER_2",
        "MEMBER_2": "MEMBER_2",
        "FAMILY MEMBER 3": "MEMBER_3",
        "MEMBER_3": "MEMBER_3"
    }

    all_persons = db.query(Person).filter(Person.is_active == True).all()
    person_code_map = {}
    for p in all_persons:
        code = portfolio_to_code.get(p.full_name.upper(), p.short_name)
        if code in ["APEX", "MEMBER_1", "PATRIARCH", "MEMBER_2", "MEMBER_3"]:
            person_code_map[p.id] = code

    # Target family codes in order
    target_codes = ["PATRIARCH", "MEMBER_1", "APEX", "MEMBER_3", "MEMBER_2"]

    # Helper structure to initialize category dictionary
    def init_cat_dict():
        d = {code: {"invested": 0.0, "current": 0.0} for code in target_codes}
        d["TOTAL"] = {"invested": 0.0, "current": 0.0}
        return d

    # Initialize all category accumulators
    categories = {
        "Stocks": init_cat_dict(),
        "Unlisted Shares": init_cat_dict(),
        "Equity MF": init_cat_dict(),
        "Gold (MF & ETF)": init_cat_dict(),
        "Gold – SGB": init_cat_dict(),
        "Silver (MF & ETF)": init_cat_dict(),
        "AIF": init_cat_dict(),
        "Cash": init_cat_dict()
    }

    # ────────────────────────────────────────────────────────
    # 1. LOAD EXCEL REPORT FOR STOCKS, GOLD ETF, SILVER ETF, AND SGB
    # ────────────────────────────────────────────────────────
    latest_report_file = get_latest_global_positions_file()
    excel_loaded = False
    
    if latest_report_file:
        try:
            print(f"Loading latest Excel report: {os.path.basename(latest_report_file)}")
            report_df = parse_global_positions_report(latest_report_file)
            
            for _, row in report_df.iterrows():
                portfolio_name = str(row.get("Portfolio Name", "")).upper()
                
                # Resolve owner code from Portfolio Name in Excel
                code = None
                if 'PATRIARCH' in portfolio_name:
                    code = 'PATRIARCH'
                elif 'MEMBER_1' in portfolio_name:
                    code = 'MEMBER_1'
                elif 'APEX' in portfolio_name:
                    code = 'APEX'
                elif 'MEMBER_3' in portfolio_name:
                    code = 'MEMBER_3'
                elif 'MEMBER_2' in portfolio_name:
                    code = 'MEMBER_2'
                    
                if not code or code not in target_codes:
                    continue

                asset_name = str(row.get("Asset Name", "")).lower()
                asset_class = str(row.get("Asset Class", ""))
                
                cost = float(row.get("Amount Invested", 0.0))
                curr_val = float(row.get("Current Value", 0.0))
                
                is_gold = ("gold" in asset_name) and ("senco" not in asset_name) and ("sovereign" not in asset_name)
                is_sgb = "sovereign gold bond" in asset_name
                is_silver = "silver" in asset_name

                if is_gold:
                    categories["Gold (MF & ETF)"][code]["invested"] += cost
                    categories["Gold (MF & ETF)"][code]["current"] += curr_val
                elif is_sgb:
                    categories["Gold – SGB"][code]["invested"] += cost
                    categories["Gold – SGB"][code]["current"] += curr_val
                elif is_silver:
                    categories["Silver (MF & ETF)"][code]["invested"] += cost
                    categories["Silver (MF & ETF)"][code]["current"] += curr_val
                elif asset_class == "Stocks":
                    categories["Stocks"][code]["invested"] += cost
                    categories["Stocks"][code]["current"] += curr_val
            excel_loaded = True
        except Exception as excel_err:
            print(f"Error parsing Excel file {latest_report_file}: {excel_err}. Falling back to database holdings.")

    # FALLBACK: If Excel report not found or failed to load, fetch Stocks/SGB from DB
    if not excel_loaded:
        from models.equities import EquityHolding
        from models.gold import GoldHolding
        from models.enums import GoldType
        
        # Listed Equities from database
        eq_holdings = db.query(EquityHolding).all()
        for h in eq_holdings:
            code = person_code_map.get(h.person_id)
            if not code or code not in target_codes:
                continue

            qty = float(h.quantity or 0)
            avg_cost = float(h.average_cost or 0)
            curr_val = float(h.current_value or 0)
            cost = qty * avg_cost if avg_cost > 0 else curr_val

            symbol_lower = (h.symbol or "").lower()
            company_lower = (h.company_name or "").lower()
            
            is_gold = ("gold" in symbol_lower or "gold" in company_lower) and "senco" not in company_lower and "sovereign" not in company_lower
            is_silver = "silver" in symbol_lower or "silver" in company_lower

            if is_gold:
                categories["Gold (MF & ETF)"][code]["invested"] += cost
                categories["Gold (MF & ETF)"][code]["current"] += curr_val
            elif is_silver:
                categories["Silver (MF & ETF)"][code]["invested"] += cost
                categories["Silver (MF & ETF)"][code]["current"] += curr_val
            else:
                categories["Stocks"][code]["invested"] += cost
                categories["Stocks"][code]["current"] += curr_val

        # Gold SGB from database
        gold_holdings = db.query(GoldHolding).all()
        for g in gold_holdings:
            code = person_code_map.get(g.person_id)
            if not code or code not in target_codes:
                continue

            cost = float(g.total_cost or 0)
            curr_val = float(g.current_value or 0)

            if g.gold_type == GoldType.SGB or g.gold_type == GoldType.PHYSICAL or g.gold_type == GoldType.DIGITAL:
                categories["Gold – SGB"][code]["invested"] += cost
                categories["Gold – SGB"][code]["current"] += curr_val

    # ────────────────────────────────────────────────────────
    # 2. UNLISTED SHARES (FROM EXCEL)
    # ────────────────────────────────────────────────────────
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    unlisted_file = os.path.join(base_dir, "DATA", "UNLISTED SHARES.xlsx")
    unlisted_data = parse_unlisted_shares_excel(unlisted_file)
    for u in unlisted_data:
        code = u["owner"]
        if code not in target_codes:
            continue

        cost = u["cost"]
        curr_val = u["current_value"]

        categories["Unlisted Shares"][code]["invested"] += cost
        categories["Unlisted Shares"][code]["current"] += curr_val

    if not unlisted_data:
        db_unlisted = db.query(UnlistedShare).all()
        for u in db_unlisted:
            code = person_code_map.get(u.person_id)
            if code in target_codes:
                cost = float(u.total_cost or 0)
                curr_val = float(u.current_value or 0)
                categories["Unlisted Shares"][code]["invested"] += cost
                categories["Unlisted Shares"][code]["current"] += curr_val

    # ────────────────────────────────────────────────────────
    # 3. AIF / PMS (FROM EXCEL OR DATABASE FALLBACK)
    # ────────────────────────────────────────────────────────
    aif_file = os.path.join(base_dir, "DATA", "AIF DATA.xlsx")
    aif_data = parse_aif_excel(aif_file)
    for a in aif_data:
        code = a["owner"]
        if code not in target_codes:
            continue

        cost = a["cost"]
        curr_val = a["current_value"]

        categories["AIF"][code]["invested"] += cost
        categories["AIF"][code]["current"] += curr_val

    if not aif_data:
        db_aif = db.query(Alternative).all()
        for a in db_aif:
            code = person_code_map.get(a.person_id)
            if code in target_codes:
                cost = float(a.amount_invested or 0)
                curr_val = float(a.current_value or 0)
                categories["AIF"][code]["invested"] += cost
                categories["AIF"][code]["current"] += curr_val

    # ────────────────────────────────────────────────────────
    # 4. MUTUAL FUNDS (FROM DB - EQUITY MF, GOLD MF, SILVER MF, LIQUID/CASH MF)
    # ────────────────────────────────────────────────────────
    details_rows = db.query(MFDetail).all()
    details_map = {d.scheme_name.upper(): d for d in details_rows}
    remaining_units_dict = get_purchase_lots_remaining_units(db)

    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT']
    txns = db.query(MFTransaction).filter(
        MFTransaction.status == TransactionStatus.ACTIVE,
        MFTransaction.transaction_type.in_(purchase_types)
    ).all()

    fallback_navs_map = {}
    fallback_names_map = {}
    if txns:
        from sqlalchemy import func
        from models.market_data import MFNav
        
        amfi_codes = list({str(t.amfi_code).strip().split('.')[0] for t in txns if t.amfi_code})
        schemes = list({t.scheme_name for t in txns})
        
        if amfi_codes:
            latest_dates_code = db.query(
                MFNav.amfi_code,
                func.max(MFNav.nav_date).label("max_date")
            ).filter(
                MFNav.amfi_code.in_(amfi_codes)
            ).group_by(MFNav.amfi_code).subquery()

            latest_navs_code = db.query(
                MFNav.amfi_code,
                MFNav.nav
            ).join(
                latest_dates_code,
                (MFNav.amfi_code == latest_dates_code.c.amfi_code) & (MFNav.nav_date == latest_dates_code.c.max_date)
            ).all()
            
            fallback_navs_map = {str(row.amfi_code).strip().split('.')[0]: float(row.nav) for row in latest_navs_code if row.nav}

        if schemes:
            latest_dates_name = db.query(
                MFNav.scheme_name,
                func.max(MFNav.nav_date).label("max_date")
            ).filter(
                MFNav.scheme_name.in_(schemes)
            ).group_by(MFNav.scheme_name).subquery()

            latest_navs_name = db.query(
                MFNav.scheme_name,
                MFNav.nav
            ).join(
                latest_dates_name,
                (MFNav.scheme_name == latest_dates_name.c.scheme_name) & (MFNav.nav_date == latest_dates_name.c.max_date)
            ).all()
            
            fallback_names_map = {row.scheme_name.upper(): float(row.nav) for row in latest_navs_name if row.nav}

    for t in txns:
        code = person_code_map.get(t.person_id)
        if not code or code not in target_codes:
            continue

        rem_units = remaining_units_dict.get(t.id, 0.0)
        if rem_units <= 0.001:
            continue

        nav = float(t.nav or 0)
        code_clean = str(t.amfi_code).strip().split('.')[0] if t.amfi_code else ""
        latest_nav = fallback_navs_map.get(code_clean) or fallback_names_map.get(t.scheme_name.upper(), nav)
        
        cost = rem_units * nav
        curr_val = rem_units * latest_nav

        db_detail = details_map.get(t.scheme_name.upper())
        f_type = (db_detail.fund_type if db_detail else "EQUITY") or "EQUITY"
        f_type_upper = f_type.upper().strip()

        if f_type_upper == "GOLD":
            categories["Gold (MF & ETF)"][code]["invested"] += cost
            categories["Gold (MF & ETF)"][code]["current"] += curr_val
        elif f_type_upper == "SILVER":
            categories["Silver (MF & ETF)"][code]["invested"] += cost
            categories["Silver (MF & ETF)"][code]["current"] += curr_val
        elif f_type_upper in ["LIQUID", "ARBITRAGE", "LOW DURATION", "ULTRA SHORT DURATION", "SHORT DURATION", "MONEY MARKET", "OVERNIGHT", "FLOATER", "CORPORATE BOND", "DEBT"]:
            categories["Cash"][code]["invested"] += cost
            categories["Cash"][code]["current"] += curr_val
        else:
            categories["Equity MF"][code]["invested"] += cost
            categories["Equity MF"][code]["current"] += curr_val

    # ────────────────────────────────────────────────────────
    # 5. CALCULATE TOTALS AND EXPOSURES
    # ────────────────────────────────────────────────────────
    grand_total_invested = 0.0
    grand_total_current = 0.0
    for cat_name, cat_data in categories.items():
        row_total_inv = 0.0
        row_total_cur = 0.0
        for code in target_codes:
            row_total_inv += cat_data[code]["invested"]
            row_total_cur += cat_data[code]["current"]
            
        cat_data["TOTAL"]["invested"] = row_total_inv
        cat_data["TOTAL"]["current"] = row_total_cur
        grand_total_invested += row_total_inv
        grand_total_current += row_total_cur

    rows = []
    for cat_name in [
        "Stocks", "Unlisted Shares", "Equity MF", 
        "Gold (MF & ETF)", "Gold – SGB", "Silver (MF & ETF)", 
        "AIF", "Cash"
    ]:
        cat_data = categories[cat_name]
        tot_inv = cat_data["TOTAL"]["invested"]
        tot_cur = cat_data["TOTAL"]["current"]
        pct_change = ((tot_cur - tot_inv) / tot_inv * 100) if tot_inv > 0 else 0.0
        exposure = (tot_cur / grand_total_current * 100) if grand_total_current > 0 else 0.0

        row_dict = {
            "category": cat_name,
            "pct_change": pct_change,
            "exposure": exposure
        }
        
        for code in target_codes:
            row_dict[code] = {
                "invested": cat_data[code]["invested"],
                "current": cat_data[code]["current"]
            }
            
        row_dict["TOTAL"] = {
            "invested": tot_inv,
            "current": tot_cur
        }
        
        rows.append(row_dict)

    grand_total_row = {
        "category": "TOTAL",
        "pct_change": 0.0,
        "exposure": 100.0
    }
    
    overall_inv = 0.0
    overall_cur = 0.0
    for code in target_codes:
        code_inv = sum(categories[cat][code]["invested"] for cat in categories)
        code_cur = sum(categories[cat][code]["current"] for cat in categories)
        grand_total_row[code] = {
            "invested": code_inv,
            "current": code_cur
        }
        overall_inv += code_inv
        overall_cur += code_cur

    grand_total_row["TOTAL"] = {
        "invested": overall_inv,
        "current": overall_cur
    }
    
    grand_total_row["pct_change"] = ((overall_cur - overall_inv) / overall_inv * 100) if overall_inv > 0 else 0.0

    # Calculate TOTAL (EX-SGB) row
    grand_total_ex_sgb_row = {
        "category": "TOTAL (EX-SGB)",
        "pct_change": 0.0,
        "exposure": 100.0
    }
    overall_ex_sgb_inv = 0.0
    overall_ex_sgb_cur = 0.0
    for code in target_codes:
        code_inv = sum(categories[cat][code]["invested"] for cat in categories if cat != "Gold – SGB")
        code_cur = sum(categories[cat][code]["current"] for cat in categories if cat != "Gold – SGB")
        grand_total_ex_sgb_row[code] = {
            "invested": code_inv,
            "current": code_cur
        }
        overall_ex_sgb_inv += code_inv
        overall_ex_sgb_cur += code_cur

    grand_total_ex_sgb_row["TOTAL"] = {
        "invested": overall_ex_sgb_inv,
        "current": overall_ex_sgb_cur
    }
    grand_total_ex_sgb_row["pct_change"] = ((overall_ex_sgb_cur - overall_ex_sgb_inv) / overall_ex_sgb_inv * 100) if overall_ex_sgb_inv > 0 else 0.0

    return {
        "rows": rows,
        "grand_total": grand_total_row,
        "grand_total_ex_sgb": grand_total_ex_sgb_row,
        "grand_total_invested": overall_inv,
        "grand_total_current": overall_cur
    }

def get_broker_portfolio_data(db: Session) -> dict:
    """
    Groups portfolio holdings by Broker / Source / Advisor (rows) and by asset category (columns).
    Rows: PRIME - MF, NEXUS - MF, APEX CUSTODY, GLOBAL INVEST, SUMMIT SECURITIES, DIRECT - MF
    Columns: EQUITY, GOLD, SILVER, DEBT, SGB
    """
    from models.alternatives import UnlistedShare, Alternative
    from models.mutual_funds import MFTransaction, MFDetail
    from models.market_data import MFNav
    from sqlalchemy import text, func

    rows_list = ["PRIME - MF", "NEXUS - MF", "BROKER 1", "BROKER 2", "BROKER 3", "DIRECT - MF"]
    cols_list = ["EQUITY", "UNLISTED", "GOLD", "SILVER", "DEBT", "SGB"]

    # Initialize matrix
    matrix = {r: {c: {"invested": 0.0, "current": 0.0} for c in cols_list} for r in rows_list}

    def get_broker(portfolio_name):
        pf = str(portfolio_name).upper()
        if 'JM' in pf or 'GLOBAL' in pf or 'BROKER 1' in pf or 'BROKER_1' in pf or 'BROKER1' in pf:
            return 'BROKER 1'
        elif 'CENTRUM' in pf or 'CBL' in pf or 'APEX' in pf or 'BROKER 2' in pf or 'BROKER_2' in pf or 'BROKER2' in pf:
            return 'BROKER 2'
        elif 'NUVAMA' in pf or 'SUMMIT' in pf or 'BROKER 3' in pf or 'BROKER_3' in pf or 'BROKER3' in pf:
            return 'BROKER 3'
        return None

    # 1. Excel Positions Report for BROKER 1, BROKER 2, BROKER 3
    latest_report_file = get_latest_global_positions_file()
    if latest_report_file:
        try:
            report_df = parse_global_positions_report(latest_report_file)
            for _, row in report_df.iterrows():
                portfolio_name = row["Portfolio Name"]
                broker = get_broker(portfolio_name)
                if not broker:
                    continue

                asset_name = str(row["Asset Name"]).lower()
                asset_class = str(row["Asset Class"])
                cost = float(row["Amount Invested"])
                curr = float(row["Current Value"])

                is_gold = ("gold" in asset_name) and ("senco" not in asset_name) and ("sovereign" not in asset_name)
                is_sgb = "sovereign gold bond" in asset_name
                is_silver = "silver" in asset_name
                is_reit = any(x in asset_name for x in ["reit", "trust", "brookfield", "embassy", "mindspace", "nexus"])

                # Skip AIF and Mutual Funds in Excel (handled via DB / advisors)
                if asset_class in ["Mutual Funds", "AIF"]:
                    continue

                if is_gold:
                    matrix[broker]["GOLD"]["invested"] += cost
                    matrix[broker]["GOLD"]["current"] += curr
                elif is_silver:
                    matrix[broker]["SILVER"]["invested"] += cost
                    matrix[broker]["SILVER"]["current"] += curr
                elif is_sgb:
                    matrix[broker]["SGB"]["invested"] += cost
                    matrix[broker]["SGB"]["current"] += curr
                elif is_reit:
                    matrix[broker]["DEBT"]["invested"] += cost
                    matrix[broker]["DEBT"]["current"] += curr
                elif asset_class == "Stocks":
                    matrix[broker]["EQUITY"]["invested"] += cost
                    matrix[broker]["EQUITY"]["current"] += curr
        except Exception as e:
            print(f"Error parsing Excel for broker matrix: {e}")

    # 2. Excel Unlisted Shares (add to respective Broker's UNLISTED)
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        unlisted_file = os.path.join(base_dir, "DATA", "UNLISTED SHARES.xlsx")
        unlisted_data = parse_unlisted_shares_excel(unlisted_file)
        for u in unlisted_data:
            broker = u["broker"]
            cost = u["cost"]
            curr = u["current_value"]
            matrix[broker]["UNLISTED"]["invested"] += cost
            matrix[broker]["UNLISTED"]["current"] += curr
    except Exception as e:
        print(f"Error mapping unlisted shares from Excel: {e}")

    # 3. Excel AIF / PMS (all map to VARUN)
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        aif_file = os.path.join(base_dir, "DATA", "AIF DATA.xlsx")
        aif_data = parse_aif_excel(aif_file)
        for a in aif_data:
            cost = a["cost"]
            curr = a["current_value"]
            scheme = str(a["scheme_name"]).lower()

            is_debt = "credit" in scheme or "kotak credit" in scheme
            if is_debt:
                matrix["VARUN"]["DEBT"]["invested"] += cost
                matrix["VARUN"]["DEBT"]["current"] += curr
            else:
                matrix["VARUN"]["EQUITY"]["invested"] += cost
                matrix["VARUN"]["EQUITY"]["current"] += curr
    except Exception as e:
        print(f"Error mapping AIF from Excel: {e}")

    # 4. Database Mutual Funds (PRIME, NEXUS, DIRECT)
    try:
        from services.fifo_engine import get_purchase_lots_remaining_units
        remaining_units_dict = get_purchase_lots_remaining_units(db)
        txns = db.query(MFTransaction).all()
        details = db.query(MFDetail).all()
        details_map = {d.scheme_name.upper(): d for d in details}

        # Cache latest NAVs by amfi_code and scheme name fallback
        fallback_navs_map = {}
        fallback_names_map = {}
        if txns:
            amfi_codes = list({str(t.amfi_code).strip().split('.')[0] for t in txns if t.amfi_code})
            schemes = list({t.scheme_name for t in txns})

            if amfi_codes:
                latest_dates_code = db.query(
                    MFNav.amfi_code,
                    func.max(MFNav.nav_date).label("max_date")
                ).filter(
                    MFNav.amfi_code.in_(amfi_codes)
                ).group_by(MFNav.amfi_code).subquery()

                latest_navs_code = db.query(
                    MFNav.amfi_code,
                    MFNav.nav
                ).join(
                    latest_dates_code,
                    (MFNav.amfi_code == latest_dates_code.c.amfi_code) & (MFNav.nav_date == latest_dates_code.c.max_date)
                ).all()

                fallback_navs_map = {str(row.amfi_code).strip().split('.')[0]: float(row.nav) for row in latest_navs_code if row.nav}

            if schemes:
                latest_dates_name = db.query(
                    MFNav.scheme_name,
                    func.max(MFNav.nav_date).label("max_date")
                ).filter(
                    MFNav.scheme_name.in_(schemes)
                ).group_by(MFNav.scheme_name).subquery()

                latest_navs_name = db.query(
                    MFNav.scheme_name,
                    MFNav.nav
                ).join(
                    latest_dates_name,
                    (MFNav.scheme_name == latest_dates_name.c.scheme_name) & (MFNav.nav_date == latest_dates_name.c.max_date)
                ).all()

                fallback_names_map = {row.scheme_name.upper(): float(row.nav) for row in latest_navs_name if row.nav}

        for t in txns:
            rem_units = remaining_units_dict.get(t.id, 0.0)
            if rem_units <= 0.001:
                continue

            nav = float(t.nav or 0)
            code_clean = str(t.amfi_code).strip().split('.')[0] if t.amfi_code else ""
            latest_nav = fallback_navs_map.get(code_clean) or fallback_names_map.get(t.scheme_name.upper(), nav)

            cost = rem_units * nav
            curr = rem_units * latest_nav

            detail = details_map.get(t.scheme_name.upper())
            
            from services.fifo_engine import resolve_advisor
            advisor_resolved = resolve_advisor(t.scheme_name, t.folio_number, t.notes, db, detail)
            advisor = str(advisor_resolved or "DIRECT").upper().strip()
            
            f_type = str(detail.fund_type if detail else "EQUITY").upper()

            # Resolve advisor row key
            row_key = "DIRECT - MF"
            if "PRIME" in advisor:
                row_key = "PRIME - MF"
            elif "NEXUS" in advisor:
                row_key = "NEXUS - MF"

            # Resolve category column
            is_gold = f_type == "GOLD"
            is_silver = f_type == "SILVER"
            is_debt = f_type in ["LIQUID", "ARBITRAGE", "LOW DURATION", "ULTRA SHORT DURATION", "SHORT DURATION", "MONEY MARKET", "OVERNIGHT", "FLOATER", "CORPORATE BOND", "DEBT"]

            if is_gold:
                matrix[row_key]["GOLD"]["invested"] += cost
                matrix[row_key]["GOLD"]["current"] += curr
            elif is_silver:
                matrix[row_key]["SILVER"]["invested"] += cost
                matrix[row_key]["SILVER"]["current"] += curr
            elif is_debt:
                matrix[row_key]["DEBT"]["invested"] += cost
                matrix[row_key]["DEBT"]["current"] += curr
            else:
                matrix[row_key]["EQUITY"]["invested"] += cost
                matrix[row_key]["EQUITY"]["current"] += curr
    except Exception as e:
        print(f"Error mapping mutual funds: {e}")

    # Build final structured output rows
    rows = []
    grand_total_invested = 0.0
    for r in rows_list:
        row_total_inv = 0.0
        row_total_cur = 0.0
        for c in cols_list:
            row_total_inv += matrix[r][c]["invested"]
            row_total_cur += matrix[r][c]["current"]

        pct_change = ((row_total_cur - row_total_inv) / row_total_inv * 100) if row_total_inv > 0 else 0.0
        grand_total_invested += row_total_inv

        row_dict = {
            "category": r,
            "pct_change": pct_change,
            "TOTAL": {"invested": row_total_inv, "current": row_total_cur}
        }
        for c in cols_list:
            row_dict[c] = {"invested": matrix[r][c]["invested"], "current": matrix[r][c]["current"]}
        rows.append(row_dict)

    # Compute grand total row
    grand_total_row = {
        "category": "TOTAL",
        "pct_change": 0.0,
        "exposure": 100.0
    }
    overall_inv = 0.0
    overall_cur = 0.0

    for c in cols_list:
        col_inv = sum(matrix[r][c]["invested"] for r in rows_list)
        col_cur = sum(matrix[r][c]["current"] for r in rows_list)
        grand_total_row[c] = {"invested": col_inv, "current": col_cur}
        overall_inv += col_inv
        overall_cur += col_cur

    grand_total_row["TOTAL"] = {"invested": overall_inv, "current": overall_cur}
    grand_total_row["pct_change"] = ((overall_cur - overall_inv) / overall_inv * 100) if overall_inv > 0 else 0.0

    # Calculate exposures for each row based on current value
    grand_total_current = overall_cur
    for row in rows:
        row_cur = row["TOTAL"]["current"]
        row["exposure"] = (row_cur / grand_total_current * 100) if grand_total_current > 0 else 0.0

    # Calculate TOTAL (EX-SGB) row for broker data
    grand_total_ex_sgb_row = {
        "category": "TOTAL (EX-SGB)",
        "pct_change": 0.0,
        "exposure": 100.0
    }
    overall_ex_sgb_inv = 0.0
    overall_ex_sgb_cur = 0.0
    for c in cols_list:
        if c == "SGB":
            grand_total_ex_sgb_row[c] = {"invested": 0.0, "current": 0.0}
        else:
            col_inv = grand_total_row[c]["invested"]
            col_cur = grand_total_row[c]["current"]
            grand_total_ex_sgb_row[c] = {"invested": col_inv, "current": col_cur}
            overall_ex_sgb_inv += col_inv
            overall_ex_sgb_cur += col_cur

    grand_total_ex_sgb_row["TOTAL"] = {"invested": overall_ex_sgb_inv, "current": overall_ex_sgb_cur}
    grand_total_ex_sgb_row["pct_change"] = ((overall_ex_sgb_cur - overall_ex_sgb_inv) / overall_ex_sgb_inv * 100) if overall_ex_sgb_inv > 0 else 0.0

    return {
        "rows": rows,
        "grand_total": grand_total_row,
        "grand_total_ex_sgb": grand_total_ex_sgb_row
    }

def export_portfolio_overview_xlsx(db: Session) -> str:
    import os
    from datetime import date
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    # Fetch data
    portfolio_data = get_consolidated_portfolio_data(db)
    broker_data = get_broker_portfolio_data(db)

    # Initialize workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Portfolio Overview"
    
    # Ensure grid lines are visible
    ws.views.sheetView[0].showGridLines = True

    # Setup styles
    font_family = "Segoe UI"
    title_font = Font(name=font_family, size=14, bold=True, underline="single", color="000000")
    section_font = Font(name=font_family, size=11, bold=True, color="000000")
    header_font = Font(name=font_family, size=10, bold=True, color="000000")
    bold_font = Font(name=font_family, size=10, bold=True)
    regular_font = Font(name=font_family, size=10)
    
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    gray_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    header_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    
    thin_black = Side(style='thin', color='000000')
    medium_black = Side(style='medium', color='000000')
    double_black = Side(style='double', color='000000')
    
    thin_border = Border(left=thin_black, right=thin_black, top=thin_black, bottom=thin_black)
    header_border = Border(left=thin_black, right=thin_black, top=thin_black, bottom=thin_black)
    double_bottom_border = Border(left=thin_black, right=thin_black, top=thin_black, bottom=double_black)

    # Title block (merged row 2 columns 2-10, centered, bold, underlined)
    ws.merge_cells(start_row=2, start_column=2, end_row=2, end_column=10)
    title_cell = ws.cell(row=2, column=2, value="CONSOLIDATED PORTFOLIO - APEX GROUP")
    title_cell.font = title_font
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 25
    
    # Row 3 is a blank row for spacing
    ws.row_dimensions[3].height = 10

    def fmt_pair(inv, cur):
        if inv == 0 and cur == 0:
            return "-"
        def fmt_val(v):
            if v == 0:
                return "0.00 L"
            return f"{v / 1e7:.2f} Cr" if v >= 1e7 else f"{v / 1e5:.2f} L"
        return f"{fmt_val(inv)} / {fmt_val(cur)}"

    # ────────────────────────────────────────────────────────
    # TABLE 1: Asset Allocation & Performance Overview
    # ────────────────────────────────────────────────────────
    # Headers
    headers_1 = ["Category", "PATRIARCH", "MEMBER_1", "APEX", "MEMBER_3", "MEMBER_2", "TOTAL", "%Chg", "Expo"]
    start_row = 4
    ws.row_dimensions[start_row].height = 26
    
    for col_idx, h in enumerate(headers_1, start=2):
        cell = ws.cell(row=start_row, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = header_border
    
    current_row = start_row + 1
    
    # Rows
    for r_idx, r in enumerate(portfolio_data["rows"]):
        ws.row_dimensions[current_row].height = 36
        row_fill = gray_fill if r_idx % 2 == 0 else white_fill
        
        # Category
        cell = ws.cell(row=current_row, column=2, value=r["category"])
        cell.font = bold_font
        cell.border = thin_border
        cell.fill = row_fill
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        
        # PATRIARCH to MEMBER_2 columns (centered, wrap text!)
        for col_idx, code in enumerate(["PATRIARCH", "MEMBER_1", "APEX", "MEMBER_3", "MEMBER_2"], start=3):
            val_str = fmt_pair(r[code]["invested"], r[code]["current"])
            cell = ws.cell(row=current_row, column=col_idx, value=val_str)
            cell.font = regular_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border
            cell.fill = row_fill
            
        # TOTAL column (bold, centered, wrap text!)
        val_str = fmt_pair(r["TOTAL"]["invested"], r["TOTAL"]["current"])
        cell = ws.cell(row=current_row, column=8, value=val_str)
        cell.font = bold_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        cell.fill = row_fill
        
        # %Chg column
        cell = ws.cell(row=current_row, column=9, value=r['pct_change'] / 100.0)
        cell.number_format = '0.00%'
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        cell.fill = row_fill
        if r['pct_change'] >= 0:
            cell.font = Font(name=font_family, size=10, bold=True, color="166534")
        else:
            cell.font = Font(name=font_family, size=10, bold=True, color="991b1b")
            
        # Expo column
        cell = ws.cell(row=current_row, column=10, value=r['exposure'] / 100.0)
        cell.number_format = '0.00%'
        cell.font = regular_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        cell.fill = row_fill
        
        current_row += 1
        
    # Grand Total Table 1 (TOTAL)
    gt = portfolio_data["grand_total"]
    ws.row_dimensions[current_row].height = 36
    total_row_fill = gray_fill if len(portfolio_data["rows"]) % 2 == 0 else white_fill
    
    cell = ws.cell(row=current_row, column=2, value="TOTAL")
    cell.font = bold_font
    cell.fill = total_row_fill
    cell.border = double_bottom_border
    cell.alignment = Alignment(horizontal="left", vertical="center")
    
    for col_idx, code in enumerate(["PATRIARCH", "MEMBER_1", "APEX", "MEMBER_3", "MEMBER_2"], start=3):
        val_str = fmt_pair(gt[code]["invested"], gt[code]["current"])
        cell = ws.cell(row=current_row, column=col_idx, value=val_str)
        cell.font = bold_font
        cell.fill = total_row_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = double_bottom_border
        
    val_str = fmt_pair(gt["TOTAL"]["invested"], gt["TOTAL"]["current"])
    cell = ws.cell(row=current_row, column=8, value=val_str)
    cell.font = bold_font
    cell.fill = total_row_fill
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = double_bottom_border
    
    cell = ws.cell(row=current_row, column=9, value=gt['pct_change'] / 100.0)
    cell.number_format = '0.00%'
    cell.fill = total_row_fill
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = double_bottom_border
    if gt['pct_change'] >= 0:
        cell.font = Font(name=font_family, size=10, bold=True, color="166534")
    else:
        cell.font = Font(name=font_family, size=10, bold=True, color="991b1b")
        
    cell = ws.cell(row=current_row, column=10, value=1.0)
    cell.number_format = '0.00%'
    cell.font = bold_font
    cell.fill = total_row_fill
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = double_bottom_border

    current_row += 1

    # Grand Total Table 1 EX-SGB (TOTAL (EX-SGB))
    gt_ex = portfolio_data["grand_total_ex_sgb"]
    ws.row_dimensions[current_row].height = 36
    ex_sgb_row_fill = white_fill if len(portfolio_data["rows"]) % 2 == 0 else gray_fill
    
    cell = ws.cell(row=current_row, column=2, value="TOTAL (EX-SGB)")
    cell.font = bold_font
    cell.fill = ex_sgb_row_fill
    cell.border = double_bottom_border
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    
    for col_idx, code in enumerate(["PATRIARCH", "MEMBER_1", "APEX", "MEMBER_3", "MEMBER_2"], start=3):
        val_str = fmt_pair(gt_ex[code]["invested"], gt_ex[code]["current"])
        cell = ws.cell(row=current_row, column=col_idx, value=val_str)
        cell.font = bold_font
        cell.fill = ex_sgb_row_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = double_bottom_border
        
    val_str = fmt_pair(gt_ex["TOTAL"]["invested"], gt_ex["TOTAL"]["current"])
    cell = ws.cell(row=current_row, column=8, value=val_str)
    cell.font = bold_font
    cell.fill = ex_sgb_row_fill
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = double_bottom_border
    
    cell = ws.cell(row=current_row, column=9, value=gt_ex['pct_change'] / 100.0)
    cell.number_format = '0.00%'
    cell.fill = ex_sgb_row_fill
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = double_bottom_border
    if gt_ex['pct_change'] >= 0:
        cell.font = Font(name=font_family, size=10, bold=True, color="166534")
    else:
        cell.font = Font(name=font_family, size=10, bold=True, color="991b1b")
        
    cell = ws.cell(row=current_row, column=10, value=1.0)
    cell.number_format = '0.00%'
    cell.font = bold_font
    cell.fill = ex_sgb_row_fill
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = double_bottom_border

    # ────────────────────────────────────────────────────────
    # TABLE 2: Broker & Advisor Consolidated Overview
    # ────────────────────────────────────────────────────────
    current_row += 3
    ws.cell(row=current_row, column=2, value="Broker & Advisor Consolidated Overview").font = section_font
    ws.row_dimensions[current_row].height = 20
    
    headers_2 = ["Category", "EQUITY", "UNLISTED", "GOLD", "SILVER", "DEBT", "SGB", "TOTAL", "%Chg", "Expo"]
    current_row += 1
    ws.row_dimensions[current_row].height = 26
    
    # Headers
    for col_idx, h in enumerate(headers_2, start=2):
        cell = ws.cell(row=current_row, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = header_border
        
    current_row += 1
    
    for r_idx, r in enumerate(broker_data["rows"]):
        ws.row_dimensions[current_row].height = 36
        row_fill = gray_fill if r_idx % 2 == 0 else white_fill

        cell = ws.cell(row=current_row, column=2, value=r["category"])
        cell.font = bold_font
        cell.border = thin_border
        cell.fill = row_fill
        cell.alignment = Alignment(horizontal="left", vertical="center")
        
        for col_idx, col_name in enumerate(["EQUITY", "UNLISTED", "GOLD", "SILVER", "DEBT", "SGB"], start=3):
            val_str = fmt_pair(r[col_name]["invested"], r[col_name]["current"])
            cell = ws.cell(row=current_row, column=col_idx, value=val_str)
            cell.font = regular_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border
            cell.fill = row_fill
            
        val_str = fmt_pair(r["TOTAL"]["invested"], r["TOTAL"]["current"])
        cell = ws.cell(row=current_row, column=9, value=val_str)
        cell.font = bold_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        cell.fill = row_fill
        
        cell = ws.cell(row=current_row, column=10, value=r['pct_change'] / 100.0)
        cell.number_format = '0.00%'
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        cell.fill = row_fill
        if r['pct_change'] >= 0:
            cell.font = Font(name=font_family, size=10, bold=True, color="166534")
        else:
            cell.font = Font(name=font_family, size=10, bold=True, color="991b1b")
            
        cell = ws.cell(row=current_row, column=11, value=r['exposure'] / 100.0)
        cell.number_format = '0.00%'
        cell.font = regular_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        cell.fill = row_fill
        
        current_row += 1
        
    # Grand Total Table 2 (TOTAL)
    bgt = broker_data["grand_total"]
    ws.row_dimensions[current_row].height = 36
    total_row_fill_2 = gray_fill if len(broker_data["rows"]) % 2 == 0 else white_fill
    
    ws.cell(row=current_row, column=2, value="TOTAL").font = bold_font
    cell = ws.cell(row=current_row, column=2)
    cell.fill = total_row_fill_2
    cell.border = double_bottom_border
    cell.alignment = Alignment(horizontal="left", vertical="center")
    
    for col_idx, col_name in enumerate(["EQUITY", "UNLISTED", "GOLD", "SILVER", "DEBT", "SGB"], start=3):
        val_str = fmt_pair(bgt[col_name]["invested"], bgt[col_name]["current"])
        cell = ws.cell(row=current_row, column=col_idx, value=val_str)
        cell.font = bold_font
        cell.fill = total_row_fill_2
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = double_bottom_border
        
    val_str = fmt_pair(bgt["TOTAL"]["invested"], bgt["TOTAL"]["current"])
    cell = ws.cell(row=current_row, column=9, value=val_str)
    cell.font = bold_font
    cell.fill = total_row_fill_2
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = double_bottom_border
    
    cell = ws.cell(row=current_row, column=10, value=bgt['pct_change'] / 100.0)
    cell.number_format = '0.00%'
    cell.fill = total_row_fill_2
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = double_bottom_border
    if bgt['pct_change'] >= 0:
        cell.font = Font(name=font_family, size=10, bold=True, color="166534")
    else:
        cell.font = Font(name=font_family, size=10, bold=True, color="991b1b")
        
    cell = ws.cell(row=current_row, column=11, value=1.0)
    cell.number_format = '0.00%'
    cell.font = bold_font
    cell.fill = total_row_fill_2
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = double_bottom_border

    current_row += 1

    # Grand Total Table 2 EX-SGB (TOTAL (EX-SGB))
    bgt_ex = broker_data["grand_total_ex_sgb"]
    ws.row_dimensions[current_row].height = 36
    ex_sgb_row_fill_2 = white_fill if len(broker_data["rows"]) % 2 == 0 else gray_fill

    ws.cell(row=current_row, column=2, value="TOTAL (EX-SGB)").font = bold_font
    cell = ws.cell(row=current_row, column=2)
    cell.fill = ex_sgb_row_fill_2
    cell.border = double_bottom_border
    cell.alignment = Alignment(horizontal="left", vertical="center")
    
    for col_idx, col_name in enumerate(["EQUITY", "UNLISTED", "GOLD", "SILVER", "DEBT", "SGB"], start=3):
        val_str = fmt_pair(bgt_ex[col_name]["invested"], bgt_ex[col_name]["current"])
        cell = ws.cell(row=current_row, column=col_idx, value=val_str)
        cell.font = bold_font
        cell.fill = ex_sgb_row_fill_2
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = double_bottom_border
        
    val_str = fmt_pair(bgt_ex["TOTAL"]["invested"], bgt_ex["TOTAL"]["current"])
    cell = ws.cell(row=current_row, column=9, value=val_str)
    cell.font = bold_font
    cell.fill = ex_sgb_row_fill_2
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = double_bottom_border
    
    cell = ws.cell(row=current_row, column=10, value=bgt_ex['pct_change'] / 100.0)
    cell.number_format = '0.00%'
    cell.fill = ex_sgb_row_fill_2
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = double_bottom_border
    if bgt_ex['pct_change'] >= 0:
        cell.font = Font(name=font_family, size=10, bold=True, color="166534")
    else:
        cell.font = Font(name=font_family, size=10, bold=True, color="991b1b")
        
    cell = ws.cell(row=current_row, column=11, value=1.0)
    cell.number_format = '0.00%'
    cell.font = bold_font
    cell.fill = ex_sgb_row_fill_2
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = double_bottom_border

    # Set exact column sizes as requested
    # Column A: thin spacer
    ws.column_dimensions['A'].width = 3
    # Column B: Category
    ws.column_dimensions['B'].width = 18
    # Columns C to H / I: Value columns wrapping text
    ws.column_dimensions['C'].width = 14
    ws.column_dimensions['D'].width = 14
    ws.column_dimensions['E'].width = 14
    ws.column_dimensions['F'].width = 14
    ws.column_dimensions['G'].width = 14
    ws.column_dimensions['H'].width = 14
    ws.column_dimensions['I'].width = 11
    ws.column_dimensions['J'].width = 11
    ws.column_dimensions['K'].width = 11

    # Save
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report_dir = os.path.join(base_dir, "DATA", "GLOBAL REPORT")
    os.makedirs(report_dir, exist_ok=True)
    filename = os.path.join(report_dir, f"Portfolio_Overview_As_On_{date.today().strftime('%Y%m%d')}.xlsx")
    wb.save(filename)
    return filename
