# services/xlsx_exporter.py
import os
import sys
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session



from models.persons import Person
from models.mutual_funds import MFTransaction, MFHolding, MFDetail
from models.enums import TransactionStatus
from services.fifo_engine import resolve_advisor

def resolve_sibling_project_path() -> str:
    from pathlib import Path
    base_dir = Path(__file__).resolve().parent.parent / "exports"
    base_dir.mkdir(parents=True, exist_ok=True)
    return str(base_dir)

def find_pdf_path(person, sibling_project_path, is_opening=False) -> str:
    # Find the CAMS PDF from the DATA/raw_pdfs folder
    short_name = getattr(person, 'short_name', '').upper()
    if not short_name:
        return None
        
    suffix = "_PFY" if is_opening else ""
    local_path = f"DATA/raw_pdfs/{short_name}{suffix}.pdf"
    
    if os.path.exists(local_path):
        return os.path.abspath(local_path)
        
    local_path_lower = f"data/raw_pdfs/{short_name}{suffix}.pdf"
    if os.path.exists(local_path_lower):
        return os.path.abspath(local_path_lower)
        
    return None

from threading import Lock
export_lock = Lock()
_cams_pdf_holdings_cache = {}

def get_cams_holdings_from_pdf(person, pdf_path, db, password=None) -> list:
    """
    Parses a CAMS PDF and returns holdings rows matching the exporter's format.
    Uses in-memory cache keyed by (file_path, mtime, person.id) for near-instant exports.
    """
    import json
    from services.cams_ingester import read_pdf_with_password
    
    global _cams_pdf_holdings_cache
    try:
        mtime = os.path.getmtime(pdf_path)
        cache_key = (pdf_path, mtime, person.id)
        if cache_key in _cams_pdf_holdings_cache:
            print(f"xlsx_exporter: Using cached CAMS holdings for {pdf_path}")
            return _cams_pdf_holdings_cache[cache_key]
    except Exception:
        cache_key = None
    
    try:
        raw_data = read_pdf_with_password(pdf_path, password, output="json")
        data = json.loads(raw_data)
        
        hold_rows = []
        for folio in data.get('folios', []):
            amc = folio.get('amc', '')
            folio_number = folio.get('folio', '')
            for scheme in folio.get('schemes', []):
                units = float(scheme.get('close', 0.0) or 0.0)
                if units <= 0.001:
                    continue
                    
                nav = float(scheme.get('valuation', {}).get('nav', 0.0) or 0.0)
                cost = float(scheme.get('valuation', {}).get('cost', 0.0) or 0.0)
                val = float(scheme.get('valuation', {}).get('value', 0.0) or 0.0)
                
                # Derive advisor using the database helper
                try:
                    advisor = resolve_advisor(scheme.get('scheme', ''), folio_number, '', db)
                except Exception:
                    advisor = ''
                
                hold_rows.append({
                    "Scheme Name": scheme.get('scheme', ''),
                    "Folio": folio_number,
                    "Advisor": advisor,
                    "Units": units,
                    "Cost Value": cost,
                    "NAV": nav,
                    "Fund Type": "EQUITY",
                    "PAN": person.pan,
                    "AMC": amc,
                    "ISIN": scheme.get('isin', ''),
                    "AMFI Code": scheme.get('amfi', ''),
                    "RTA": scheme.get('rta', ''),
                    "Current Value": val
                })
        if cache_key:
            _cams_pdf_holdings_cache[cache_key] = hold_rows
        return hold_rows
    except Exception as e:
        print(f"Error parsing CAMS holdings from PDF {pdf_path}: {e}")
        return []

def _compute_holdings_as_of(person, as_of_date, db) -> list:
    """
    Computes mutual fund holdings for a person as of a specific date based on transaction history.
    """
    txns = db.query(MFTransaction).filter(
        MFTransaction.person_id == person.id,
        MFTransaction.status == TransactionStatus.ACTIVE,
        MFTransaction.transaction_date < as_of_date
    ).order_by(MFTransaction.transaction_date, MFTransaction.id).all()
    
    holds_by_key = {}
    for t in txns:
        key = (t.scheme_name, t.folio_number)
        if key not in holds_by_key:
            holds_by_key[key] = {
                "scheme_name": t.scheme_name,
                "folio_number": t.folio_number,
                "isin": t.isin,
                "amfi_code": t.amfi_code,
                "units": 0.0,
                "total_cost": 0.0,
                "nav": float(t.nav) if t.nav else 0.0
            }
        h = holds_by_key[key]
        t_units = abs(float(t.units))
        t_amount = abs(float(t.amount))
        t_type = str(t.transaction_type).upper()
        
        if t_type in ['REDEMPTION', 'SWITCH_OUT']:
            if h["units"] > 0.001:
                avg_cost = h["total_cost"] / h["units"]
                h["units"] -= t_units
                h["total_cost"] -= t_units * avg_cost
            else:
                h["units"] = 0.0
                h["total_cost"] = 0.0
            if h["units"] < 0:
                h["units"] = 0.0
                h["total_cost"] = 0.0
        elif t_type == 'REVERSAL':
            h["units"] -= t_units
            h["total_cost"] -= t_amount
            if h["units"] < 0:
                h["units"] = 0.0
                h["total_cost"] = 0.0
        elif t_type in ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT', 'DIVIDEND_REINVEST', 'DIVIDEND REINVEST']:
            h["units"] += t_units
            h["total_cost"] += t_amount
        
        if t.nav:
            h["nav"] = float(t.nav)
            
    hold_rows = []
    for (scheme_name, folio_number), h in holds_by_key.items():
        if h["units"] > 0.001:
            hold_rows.append({
                "Scheme Name": h["scheme_name"],
                "Folio": h["folio_number"],
                "Advisor": resolve_advisor(h["scheme_name"], h["folio_number"], "", db),
                "Units": h["units"],
                "Cost Value": h["total_cost"],
                "NAV": h["nav"],
                "Fund Type": "EQUITY",
                "PAN": person.pan,
                "AMC": "AMC",
                "ISIN": h["isin"],
                "AMFI Code": h["amfi_code"],
                "RTA": "CAMS",
                "Current Value": h["units"] * h["nav"]
            })
    return hold_rows

def export_portfolio_xlsx(db: Session, financial_year: str, owner_id: int = None, filename: str = None, till_date: str = None) -> str:
    """
    Thread-safe wrapper for compiling and exporting the Consolidated Portfolio Summary Excel workbook.
    """
    with export_lock:
        return _export_portfolio_xlsx_impl(db, financial_year, owner_id, filename, till_date)

def _export_portfolio_xlsx_impl(db: Session, financial_year: str, owner_id: int = None, filename: str = None, till_date: str = None) -> str:
    # Import generating method from helpers package
    from helpers.analysis import generate_multi_account_report
    
    # 1. Resolve owners to export
    if owner_id:
        persons = db.query(Person).filter(Person.id == owner_id).all()
    else:
        # Default to all seeded individual/company owners with transaction records
        persons = db.query(Person).filter(Person.short_name.in_(['PATRIARCH', 'MR', 'VG', 'APEX'])).all()
        if not persons:
            persons = db.query(Person).all()
            
    accounts_data = []
    
    # Pre-fetch mf_details for custom tenure and advisor overrides
    details_rows = db.query(MFDetail).all()
    details_map = {d.scheme_name.upper(): d for d in details_rows}

    # 2. Reconstruct DataFrames matching analysis processor schemas
    for person in persons:
        # Fetch active transactions
        txns_query = db.query(MFTransaction).filter(
            MFTransaction.person_id == person.id,
            MFTransaction.status == TransactionStatus.ACTIVE
        )
        if till_date:
            try:
                till_date_parsed = datetime.strptime(till_date, "%Y-%m-%d").date()
            except ValueError:
                till_date_parsed = datetime.strptime(till_date, "%d-%m-%Y").date()
            txns_query = txns_query.filter(MFTransaction.transaction_date <= till_date_parsed)
            
        txns = txns_query.order_by(MFTransaction.transaction_date, MFTransaction.id).all()
        
        txn_rows = []
        for t in txns:
            db_detail = details_map.get(t.scheme_name.upper())
            advisor = resolve_advisor(t.scheme_name, t.folio_number, t.notes, db, db_detail)
            lt_tenure = int(db_detail.long_term_tenure) if (db_detail and db_detail.long_term_tenure) else 365
                
            txn_rows.append({
                "id": t.id,
                "owner": person.short_name,
                "folio": t.folio_number,
                "scheme": t.scheme_name,
                "advisor": advisor,
                "isin": t.isin,
                "amfi": t.amfi_code,
                "date": pd.to_datetime(t.transaction_date),
                "amount": float(t.amount),
                "units": float(t.units),
                "nav": float(t.nav),
                "type": t.transaction_type.upper(),
                "description": t.notes or "",
                "pan": person.pan,
                "long_term_tenure": lt_tenure
            })
            
        df_txns = pd.DataFrame(txn_rows) if txn_rows else pd.DataFrame(columns=["id", "owner", "folio", "scheme", "advisor", "isin", "amfi", "date", "amount", "units", "nav", "type", "description", "pan", "long_term_tenure"])
        
        # Sort so that for same-day transactions, purchases come before redemptions, and assign temp_order_id
        if not df_txns.empty:
            purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT', 'DIVIDEND_REINVEST', 'DIVIDEND REINVEST']
            sale_types = ['REDEMPTION', 'SWITCH_OUT']
            
            def get_type_priority(row_type):
                rt = str(row_type).upper()
                if rt in purchase_types:
                    return 0
                elif rt in sale_types:
                    return 1
                return 2
                
            df_txns['type_priority'] = df_txns['type'].apply(get_type_priority)
            df_txns = df_txns.sort_values(by=['date', 'type_priority', 'id']).reset_index(drop=True)
            df_txns.drop(columns=['type_priority'], inplace=True)
            df_txns = df_txns.reset_index().rename(columns={'index': 'temp_order_id'})
        
        # Fetch or compute holdings
        if till_date:
            holds_by_key = {}
            for t in txns:
                key = (t.scheme_name, t.folio_number)
                if key not in holds_by_key:
                    holds_by_key[key] = {
                        "scheme_name": t.scheme_name,
                        "folio_number": t.folio_number,
                        "isin": t.isin,
                        "amfi_code": t.amfi_code,
                        "units": 0.0,
                        "total_cost": 0.0,
                        "nav": float(t.nav) if t.nav else 0.0
                    }
                h = holds_by_key[key]
                t_units = abs(float(t.units))
                t_amount = abs(float(t.amount))
                t_type = str(t.transaction_type).upper()
                
                if t_type in ['REDEMPTION', 'SWITCH_OUT']:
                    if h["units"] > 0.001:
                        avg_cost = h["total_cost"] / h["units"]
                        h["units"] -= t_units
                        h["total_cost"] -= t_units * avg_cost
                    else:
                        h["units"] = 0.0
                        h["total_cost"] = 0.0
                    if h["units"] < 0:
                        h["units"] = 0.0
                        h["total_cost"] = 0.0
                elif t_type == 'REVERSAL':
                    h["units"] -= t_units
                    h["total_cost"] -= t_amount
                    if h["units"] < 0:
                        h["units"] = 0.0
                        h["total_cost"] = 0.0
                elif t_type in ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT', 'DIVIDEND_REINVEST', 'DIVIDEND REINVEST']:
                    h["units"] += t_units
                    h["total_cost"] += t_amount
                
                if t.nav:
                    h["nav"] = float(t.nav)
            
            hold_rows = []
            for (scheme_name, folio_number), h in holds_by_key.items():
                if h["units"] > 0.001:
                    hold_rows.append({
                        "Scheme Name": h["scheme_name"],
                        "Folio": h["folio_number"],
                        "Advisor": resolve_advisor(h["scheme_name"], h["folio_number"], "", db),
                        "Units": h["units"],
                        "Cost Value": h["total_cost"],
                        "NAV": h["nav"],
                        "Fund Type": "EQUITY",
                        "PAN": person.pan,
                        "AMC": "AMC",
                        "ISIN": h["isin"],
                        "AMFI Code": h["amfi_code"],
                        "RTA": "CAMS",
                        "Current Value": h["units"] * h["nav"]
                    })
        else:
            holds = db.query(MFHolding).filter(
                MFHolding.person_id == person.id
            ).all()
            
            hold_rows = []
            for h in holds:
                hold_rows.append({
                    "Scheme Name": h.scheme_name,
                    "Folio": h.folio_number,
                    "Advisor": resolve_advisor(h.scheme_name, h.folio_number, "", db),
                    "Units": float(h.units),
                    "Cost Value": float(h.units) * float(h.average_nav) if h.average_nav else 0.0,
                    "NAV": float(h.current_nav) if h.current_nav else 0.0,
                    "Fund Type": "EQUITY",
                    "PAN": person.pan,
                    "AMC": "AMC",
                    "ISIN": h.isin,
                "AMFI Code": h.amfi_code,
                    "RTA": "CAMS",
                    "Current Value": float(h.current_value) if h.current_value else 0.0
                })
            
        # Determine dynamic sibling path for PDF files
        sibling_project_path = resolve_sibling_project_path()
        
        # Try to load actual CAMS closing holdings from PDF
        pdf_holds = []
        closing_pdf_path = find_pdf_path(person, sibling_project_path, is_opening=False)
        if closing_pdf_path:
            print(f"xlsx_exporter: Parsing closing holdings from latest CAMS PDF: {closing_pdf_path}")
            pdf_holds = get_cams_holdings_from_pdf(person, closing_pdf_path, db)
            
        if pdf_holds:
            df_holds = pd.DataFrame(pdf_holds)
        else:
            print("xlsx_exporter: Fallback to calculated/database for closing holdings.")
            df_holds = pd.DataFrame(hold_rows) if hold_rows else pd.DataFrame(columns=["Scheme Name", "Folio", "Advisor", "Units", "Cost Value", "NAV", "Fund Type", "PAN", "AMC", "ISIN", "AMFI Code", "RTA", "Current Value"])
        
        # Determine the start of the financial year for opening holdings
        try:
            start_year = int(financial_year.split('-')[0])
            fy_start_date = datetime(start_year, 4, 1).date()
        except Exception:
            fy_start_date = datetime(2026, 4, 1).date()
            
        opening_hold_rows = _compute_holdings_as_of(person, fy_start_date, db)
        
        # Try to load actual CAMS opening holdings from PDF
        pdf_opening_holds = []
        opening_pdf_path = find_pdf_path(person, sibling_project_path, is_opening=True)
        if opening_pdf_path:
            print(f"xlsx_exporter: Parsing opening holdings from CAMS PDF: {opening_pdf_path}")
            pdf_opening_holds = get_cams_holdings_from_pdf(person, opening_pdf_path, db)
            
        if pdf_opening_holds:
            df_opening_holds = pd.DataFrame(pdf_opening_holds)
        else:
            print("xlsx_exporter: Fallback to database/transactions for opening holdings.")
            df_opening_holds = pd.DataFrame(opening_hold_rows) if opening_hold_rows else pd.DataFrame(columns=["Scheme Name", "Folio", "Advisor", "Units", "Cost Value", "NAV", "Fund Type", "PAN", "AMC", "ISIN", "AMFI Code", "RTA", "Current Value"])
        
        accounts_data.append({
            "name": person.full_name,
            "pan": person.pan,
            "df": df_txns,
            "cams_df": df_holds,
            "cams_opening_df": df_opening_holds,
            "merger_map": {}
        })
        
    # Determine sibling project path for map files and output
    sibling_project_path = resolve_sibling_project_path()
    from pathlib import Path
    root_dir = Path(__file__).resolve().parent.parent

    # Determine folder mapping file path
    folder_file_path = None
    if sibling_project_path:
        folder_file_path = os.path.join(sibling_project_path, "folio_folder.xlsx")
        
    if not folder_file_path or not os.path.exists(folder_file_path):
        # Fallback to local workspace default
        folder_file_path = "folio_folder.xlsx"
        
    # Generate default output filename
    if not filename:
        date_str = datetime.now().strftime("%Y-%m-%d")
        if sibling_project_path:
            filename = os.path.join(sibling_project_path, f"Consolidated_Portfolio_Summary_{date_str}.xlsx")
        else:
            filename = str(root_dir / f"Consolidated_Portfolio_Summary_{date_str}.xlsx")
        
    # Recreate parent directories if missing
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    
    # Release database transaction locks before long-running CPU-bound Excel generation
    try:
        db.rollback()
    except Exception as e:
        print(f"xlsx_exporter: Warning: Failed to rollback db session before report compile: {e}")

    print(f"xlsx_exporter: Compiling report to filename: {filename}")
    generate_multi_account_report(
        accounts_data,
        financial_year,
        folder_file_path,
        filename=filename
    )
    
    return filename
