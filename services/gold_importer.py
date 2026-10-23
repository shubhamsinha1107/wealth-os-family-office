# services/gold_importer.py
import re
import pandas as pd
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from models.gold import GoldHolding
from models.enums import GoldType
from models.persons import Person
from models.accounts import Account

# Family Name to DB Person short_name or full_name mapping
PERSON_MAPPING = {
    "FAMILY PATRIARCH": "PATRIARCH",
    "FAMILY MEMBER 1": "MR",
    "APEX HOLDINGS PVT. LTD.": "APEX",
    "FAMILY MEMBER 3": "SR",
    "FAMILY MEMBER 2": "VG"
}

# Portfolio Name to DB Account institution mapping
INSTITUTION_MAPPING = {
    "CENTRUM": "Centrum Finverse",
    "JM": "JM Financial",
    "NUVAMA": "Nuvama Wealth"
}

# Regex to parse SGB details from Asset Name
SGB_PATTERN = re.compile(
    r"Sovereign Gold Bond\s+\d+(?:\.\d+)?%?\s+([A-Za-z]+)\s+(\d{4})\s+([Ss]r|SR)-([A-Za-z0-9]+)\s+(\d{4}-\d{2,4}).*?\((?:ISIN\s+)?([A-Z0-9]{12})\)",
    re.IGNORECASE
)

def parse_report_date(excel_path: str) -> date:
    """Parses the report date from the Excel header (usually row 7)."""
    try:
        # Load first 12 rows to find the date cell
        df = pd.read_excel(excel_path, header=None, nrows=12)
        for r_idx in range(len(df)):
            for col_idx in range(df.shape[1]):
                cell_val = str(df.iloc[r_idx, col_idx])
                if "global positions report as on" in cell_val.lower():
                    m = re.search(r"as on\s+(\d{2}/\d{2}/\d{4})", cell_val, re.IGNORECASE)
                    if m:
                        return datetime.strptime(m.group(1), "%d/%m/%Y").date()
    except Exception as e:
        print(f"Error parsing report date from header: {e}")
    
    # Fallback to parsing filename like 'Global Positions Report - As on 17-06-26.xlsx'
    try:
        filename = excel_path.split("\\")[-1].split("/")[-1]
        m = re.search(r"as on\s+(\d{2}-\d{2}-\d{2})", filename, re.IGNORECASE)
        if m:
            return datetime.strptime(m.group(1), "%d-%m-%y").date()
    except Exception as e:
        print(f"Error parsing date from filename: {e}")
        
    return date.today()

def parse_maturity_date(month_str: str, year_str: str) -> date:
    """Helper to convert month and year string (e.g. 'AUG', '2028') to date object."""
    # Convert 'JUNE' to 'JUN' for standard parsing
    m_str = month_str[:3].upper()
    try:
        dt = datetime.strptime(f"01 {m_str} {year_str}", "%d %b %Y")
        return dt.date()
    except Exception:
        # Fallback to January of that year if parsing fails
        return date(int(year_str), 1, 1)

def import_sgb_from_excel(db: Session, excel_path: str) -> dict:
    """Parses the Excel file and imports Sovereign Gold Bonds into gold.holdings."""
    report_date = parse_report_date(excel_path)
    print(f"Parsed report date: {report_date}")
    
    try:
        # Load main data starting from row 12 (0-indexed 11 is row 12 where 'Family Name' resides)
        df = pd.read_excel(excel_path, header=11)
    except Exception as e:
        return {"success": False, "error": f"Failed to read Excel file starting from row 12: {e}"}
    
    # Check that required columns exist
    required_cols = ["Family Name", "Portfolio Name", "Asset Class", "Asset Name", "Qty", "Avg Purchase Price", "Amount Invested", "Current Price"]
    for col in required_cols:
        if col not in df.columns:
            return {"success": False, "error": f"Missing required column in spreadsheet: '{col}'"}
            
    # Load all Persons and Accounts into cache for quick lookup
    persons = db.query(Person).all()
    person_by_short = {p.short_name.upper(): p for p in persons}
    person_by_full = {p.full_name.upper(): p for p in persons}
    
    accounts = db.query(Account).all()
    accounts_by_key = {}
    for a in accounts:
        # key: (person_id, institution_name.upper())
        key = (a.person_id, a.institution_name.upper())
        accounts_by_key[key] = a
        
    imported_count = 0
    errors = []
    
    # Filter for SGB rows where Asset Class is 'Traded Bonds' and Asset Name contains 'Sovereign' or 'SGB'
    sgb_rows = df[
        (df["Asset Class"].astype(str).str.contains("Traded Bonds", case=False, na=False)) &
        (df["Asset Name"].astype(str).str.contains("Sovereign|SGB", case=False, na=False))
    ]
    
    print(f"Found {len(sgb_rows)} potential SGB rows in Excel.")
    
    # Group by person to clear existing entries for this report date before inserting new ones
    persons_to_clear = set()
    rows_to_insert = []
    
    for idx, row in sgb_rows.iterrows():
        family_name = str(row["Family Name"]).strip()
        portfolio_name = str(row["Portfolio Name"]).strip()
        asset_name = str(row["Asset Name"]).strip()
        
        # 1. Resolve Person
        mapped_short = PERSON_MAPPING.get(family_name.upper())
        person = None
        if mapped_short:
            person = person_by_short.get(mapped_short.upper())
        if not person:
            # Try matching directly by full name or short name in DB
            person = person_by_full.get(family_name.upper()) or person_by_short.get(family_name.upper())
            
        if not person:
            errors.append(f"Row {idx+13}: Person '{family_name}' not found in database.")
            continue
            
        # 2. Resolve Account
        # Identify institution from portfolio name (e.g. 'PATRIARCH - CENTRUM' -> CENTRUM -> Centrum Finverse)
        inst_key = None
        for k, v in INSTITUTION_MAPPING.items():
            if k in portfolio_name.upper():
                inst_key = v.upper()
                break
                
        account = None
        if inst_key:
            account = accounts_by_key.get((person.id, inst_key))
            
        if not account:
            # Try finding any trading account for this person
            trading_accts = [a for a in accounts if a.person_id == person.id and a.account_type.name == "TRADING"]
            if trading_accts:
                account = trading_accts[0] # Default to first trading account if institution doesn't match
                
        if not account:
            errors.append(f"Row {idx+13}: No active TRADING account found for {person.full_name} and portfolio '{portfolio_name}'.")
            continue
            
        # 3. Parse SGB details
        m = SGB_PATTERN.search(asset_name)
        if not m:
            errors.append(f"Row {idx+13}: SGB name '{asset_name}' does not match standard RBI SGB format.")
            continue
            
        month, year, sr_prefix, series_num, fy_str, isin = m.groups()
        sgb_series = f"{sr_prefix.upper()}-{series_num.upper()} {fy_str}"
        sgb_maturity = parse_maturity_date(month, year)
        
        # 4. Map values
        qty = Decimal(str(row["Qty"])).copy_abs()
        avg_price = Decimal(str(row["Avg Purchase Price"]))
        total_cost = Decimal(str(row["Amount Invested"]))
        current_price = Decimal(str(row["Current Price"]))
        current_value = current_price * qty
        
        # SGB units are equivalent to gold grams
        quantity_grams = qty
        
        purchase_date = None
        if sgb_maturity:
            try:
                purchase_date = sgb_maturity.replace(year=sgb_maturity.year - 8)
            except ValueError:
                # Handle Feb 29 leap year cases
                purchase_date = sgb_maturity - timedelta(days=8*365)
                
        file_name = excel_path.replace("\\", "/").split("/")[-1]
        holding = GoldHolding(
            person_id=person.id,
            account_id=account.id,
            gold_type=GoldType.SGB,
            description=asset_name,
            quantity_grams=quantity_grams,
            purchase_date=purchase_date,
            purchase_price_per_gram=avg_price,
            total_cost=total_cost,
            purity="999",  # SGB is 99.9% pure gold
            storage_location=f"Demat: {account.institution_name}",
            sgb_series=sgb_series,
            sgb_maturity_date=sgb_maturity,
            sgb_units=qty,
            sgb_issue_price=None, # Not specified in positions report
            isin=isin,
            last_price=current_price,
            current_value=current_value,
            as_of_date=report_date,
            notes=f"Imported from {file_name} (Portfolio: {portfolio_name})"
        )
        
        persons_to_clear.add(person.id)
        rows_to_insert.append(holding)
        
    # Clear and insert in database
    if rows_to_insert:
        try:
            # Delete existing SGB holdings for these persons on this exact report date
            for pid in persons_to_clear:
                db.query(GoldHolding).filter(
                    GoldHolding.person_id == pid,
                    GoldHolding.gold_type == GoldType.SGB,
                    GoldHolding.as_of_date == report_date
                ).delete()
            db.commit()
            
            # Insert new ones
            db.add_all(rows_to_insert)
            db.commit()
            imported_count = len(rows_to_insert)
        except Exception as e:
            db.rollback()
            return {"success": False, "error": f"Database transaction failed: {e}"}
            
    return {
        "success": True,
        "imported_count": imported_count,
        "errors_occurred": errors,
        "as_of_date": report_date.strftime("%d-%b-%Y")
    }
