import os
import json
import re
import casparser
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session
from models.mutual_funds import MFTransaction, MFHolding
from models.audit import ImportBatch
from models.persons import Person
from models.accounts import Account
from models.enums import AccountType, TransactionStatus
from database import SessionLocal
from threading import RLock

ingestion_lock = RLock()

def _normalize_scheme_for_comparison(name: str) -> str:
    if not name:
        return ""
    name = str(name).upper()
    name = re.sub(r'\s*\(\s*NON\b[^)]*\)?', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bFUND\b', '', name)
    name = re.sub(r'\bMF\b', '', name)
    name = re.sub(r'[^A-Z0-9]', '', name)
    return name.strip()

def read_pdf_with_password(file_path: str, password: str = None, output: str = "json") -> str:
    """
    Reads a password-protected CAS PDF using provided credentials or standard showcase fallback.
    Thread-safe via ingestion_lock to prevent pypdfium2 concurrent memory corruption.
    """
    with ingestion_lock:
        return _read_pdf_with_password_impl(file_path, password, output)

def _read_pdf_with_password_impl(file_path: str, password: str = None, output: str = "json") -> str:
    passwords = []
    if password:
        for suffix in ["", "@"]:
            for pwd in [password, password.upper(), password.lower()]:
                candidate = pwd + suffix
                if candidate not in passwords:
                    passwords.append(candidate)
    # Open-Core Showcase Edition: Parameter-driven password handling
    if not passwords:
        if password:
            passwords = [password]
        else:
            passwords = ["SHOWCASE_DEMO"]

    last_err = None
    for p in passwords:
        try:
            print(f"Attempting to decrypt CAMS PDF with provided credentials...")
            return casparser.read_cas_pdf(file_path, p, output=output)
        except Exception as e:
            last_err = e
            continue
            
    raise last_err or ValueError("Failed to decrypt PDF with any known passwords. Please double check password.")

def ingest_cams_pdf(file_path: str, password: str = None, db: Session = None, original_filename: str = None) -> dict:
    """
    Parses a CAMS PDF statement, creates accounts/transactions/holdings in database,
    and returns a summary of the ingestion process. Supports automatic password decryption.
    """
    with ingestion_lock:
        return _ingest_cams_pdf_impl(file_path, password, db, original_filename)

def _ingest_cams_pdf_impl(file_path: str, password: str = None, db: Session = None, original_filename: str = None) -> dict:
    if db is None:
        db = SessionLocal()
        
    print(f"Ingesting CAMS statement: {file_path}")
    raw_data = read_pdf_with_password(file_path, password, output="json")
    data = json.loads(raw_data)
    
    folios = data.get("folios", [])
    if not folios:
        raise ValueError("No folios found in the CAMS statement.")
        
    # 1. Identify the Person using the first valid PAN found in the statement
    pan = None
    for folio in folios:
        p_val = folio.get("PAN")
        p = str(p_val).strip().upper() if p_val else ""
        if p and p != "PAN NOT AVAILABLE" and len(p) == 10:
            pan = p
            break
            
    if not pan:
        raise ValueError("Could not find a valid PAN in the CAMS statement to map to a person.")
        
    # Find person in database
    person = db.query(Person).filter(Person.pan.ilike(pan)).first()
    if not person:
        raise ValueError(f"No registered person found in the database with PAN: {pan}")
        
    print(f"Mapped CAMS statement (PAN: {pan}) to person: {person.full_name}")
    
    # Save a copy of the CAMS PDF for reconciliation reports
    try:
        import os
        import shutil
        local_dir = os.path.join(os.getcwd(), "DATA", "raw_pdfs")
        os.makedirs(local_dir, exist_ok=True)
        
        orig_lower = (original_filename or "").lower()
        is_pfy = "pfy" in orig_lower or "prev" in orig_lower or "opening" in orig_lower
        suffix = "_PFY" if is_pfy else ""
        
        local_dest = os.path.join(local_dir, f"{person.short_name.upper()}{suffix}.pdf")
        shutil.copy2(file_path, local_dest)
        print(f"Saved copy of CAMS PDF to local cache: {local_dest}")
        
        # Also copy to sibling project if it exists
        from services.xlsx_exporter import resolve_sibling_project_path
        sibling_path = resolve_sibling_project_path()
        if sibling_path:
            sib_dir = os.path.join(sibling_path, "DATA", "raw_pdfs")
            os.makedirs(sib_dir, exist_ok=True)
            sib_dest = os.path.join(sib_dir, f"{person.short_name.upper()}{suffix}.pdf")
            shutil.copy2(file_path, sib_dest)
            print(f"Saved copy of CAMS PDF to sibling project: {sib_dest}")
    except Exception as copy_err:
        print(f"Warning: Failed to save copy of ingested CAMS PDF: {copy_err}")
        
    # 2. Create an Import Batch
    import_batch = ImportBatch(
        asset_class="Mutual Funds",
        source="CAMS_PDF",
        file_name=original_filename or os.path.basename(file_path),
        status="PENDING",
        notes=f"Uploaded for {person.full_name} (PAN: {pan})"
    )
    db.add(import_batch)
    db.flush() # Get import_batch.id
    
    transactions_added = 0
    transactions_skipped = 0
    accounts_created = 0
    matched_db_txn_ids = set()
    
    # Track the date range of the statement
    min_date = None
    max_date = None
    
    # Dictionary to track processed transactions count in this batch to support multiple identical transactions
    processed_in_batch = {}
    
    # 3. Iterate through folios and ingest transactions
    for folio_data in folios:
        folio_num_val = folio_data.get("folio")
        # Strip trailing check digits / slashes to canonicalize folio numbers
        folio_num = str(folio_num_val).split('/')[0].strip() if folio_num_val else ""
        amc_name_val = folio_data.get("amc")
        amc_name = str(amc_name_val).strip() if amc_name_val else ""
        
        if not folio_num:
            continue
            
        # Ensure a matching Account of type MF_FOLIO exists in the database
        account = db.query(Account).filter(
            Account.person_id == person.id,
            Account.account_type == AccountType.MF_FOLIO,
            Account.account_number == folio_num
        ).first()

        if not account:
            account = Account(
                person_id=person.id,
                account_type=AccountType.MF_FOLIO,
                institution_name=amc_name or "CAMS Mutual Fund",
                account_number=folio_num,
                is_active=True,
                notes=f"Auto-created from CAMS statement for {person.full_name}"
            )
            db.add(account)
            db.flush()
            accounts_created += 1
            print(f"Auto-created MF_FOLIO account: {folio_num} for AMC: {amc_name}")
            
        for scheme_data in folio_data.get("schemes", []):
            scheme_name_val = scheme_data.get("scheme")
            scheme_name = str(scheme_name_val).strip().upper() if scheme_name_val else ""
            # Clean/normalize scheme name to prevent duplicate records from variations like "(NON DEMAT)" or truncated "(NON"
            scheme_name = re.sub(r'\s*\(\s*NON\b.*$', '', scheme_name, flags=re.IGNORECASE)
            scheme_name = re.sub(r'\s*NON\s*-?\s*DEMAT\s*', '', scheme_name, flags=re.IGNORECASE)
            scheme_name = re.sub(r'\s+', ' ', scheme_name).strip()
            isin_val = scheme_data.get("isin")
            isin = str(isin_val).strip() if isin_val else ""
            
            # Align name with any existing canonical transactions in database for this ISIN
            if isin:
                existing_txn = db.query(MFTransaction).filter(
                    MFTransaction.isin == isin,
                    MFTransaction.status == TransactionStatus.ACTIVE
                ).first()
                if existing_txn:
                    scheme_name = existing_txn.scheme_name
                    
            amfi_code_val = scheme_data.get("amfi")
            amfi_code = str(amfi_code_val).strip() if amfi_code_val else ""
            
            # Read transactions and advisor
            cams_adv = str(scheme_data.get("advisor") or "").strip()
            txns = scheme_data.get("transactions", [])
            for txn in txns:
                txn_date_str_val = txn.get("date")
                txn_date_str = str(txn_date_str_val).strip() if txn_date_str_val else ""
                if not txn_date_str:
                    continue
                
                txn_date = datetime.strptime(txn_date_str, "%Y-%m-%d").date()
                
                # Track statement date range
                if min_date is None or txn_date < min_date:
                    min_date = txn_date
                if max_date is None or txn_date > max_date:
                    max_date = txn_date
                
                txn_type_val = txn.get("type")
                txn_type = str(txn_type_val).strip().upper() if txn_type_val else "PURCHASE"
                
                # Normalize / reclassify switch and STP transactions based on description
                desc_upper = str(txn.get('description', '')).upper()
                if any(w in desc_upper for w in ["SHIFT IN", "SWITCH IN", "SWITCH-IN", "STP IN", "STP-IN", "TRANSFER IN"]):
                    txn_type = "SWITCH_IN"
                elif any(w in desc_upper for w in ["SHIFT OUT", "SWITCH OUT", "SWITCH-OUT", "STP OUT", "STP-OUT", "TRANSFER OUT"]):
                    txn_type = "SWITCH_OUT"
                
                # Check if this is an opening balance line on a partial period statement
                if txn_type == "OPENING_BALANCE" or "OPENING" in desc_upper:
                    # Check if prior active transactions exist in database for this account before this date
                    prior_exists = db.query(MFTransaction).filter(
                        MFTransaction.account_id == account.id,
                        MFTransaction.scheme_name == scheme_name,
                        MFTransaction.transaction_date < txn_date,
                        MFTransaction.status == TransactionStatus.ACTIVE
                    ).first()
                    if prior_exists:
                        transactions_skipped += 1
                        continue

                # units and nav can be null for stamp duty or tax rows
                units = txn.get("units")
                nav = txn.get("nav")
                amount = txn.get("amount")
                
                if amount is None:
                    amount = 0.0
                if units is None:
                    units = 0.0
                if nav is None:
                    nav = 0.0
                    
                # Prevent duplicates within the same batch (e.g. CAMS parser page overlap)
                norm_scheme = _normalize_scheme_for_comparison(scheme_name)
                txn_key = (txn_date, folio_num, norm_scheme, txn_type, round(float(units), 4), round(float(amount), 2))
                processed_in_batch[txn_key] = processed_in_batch.get(txn_key, 0) + 1
                seen_count = processed_in_batch[txn_key]
                
                # Deduplication logic: Check if transaction already exists in database.
                # Query potential duplicates by folio and date (ignoring type field to catch reclassifications)
                # and compare categories (BUY / SELL / TAX) and values in Python.
                potential_duplicates = db.query(MFTransaction).filter(
                    MFTransaction.account_id == account.id,
                    MFTransaction.folio_number == folio_num,
                    MFTransaction.transaction_date == txn_date,
                    MFTransaction.status == TransactionStatus.ACTIVE,
                    MFTransaction.import_batch_id != import_batch.id
                ).all()
                
                # Category resolver to group compatible transaction types
                def get_txn_category(u, a, t):
                    u_float = float(u or 0.0)
                    a_float = float(a or 0.0)
                    t_upper = str(t or "").upper()
                    if u_float > 0.0001:
                        return "BUY"
                    elif u_float < -0.0001:
                        return "SELL"
                    if "TAX" in t_upper or "DUTY" in t_upper:
                        return "TAX"
                    if a_float < 0:
                        return "SELL"
                    elif a_float > 0:
                        return "BUY"
                    return "OTHER"

                txn_category = get_txn_category(units, amount, txn_type)
                
                # Find all matching active transactions already in the database
                matching_db_duplicates = []
                for d in potential_duplicates:
                    d_category = get_txn_category(d.units, d.amount, d.transaction_type)
                    if d_category != txn_category:
                        continue
                    if (abs(float(d.units) - float(units)) < 0.0001 and 
                        abs(float(d.amount) - float(amount)) < 0.01 and
                        _normalize_scheme_for_comparison(d.scheme_name) == norm_scheme):
                        matching_db_duplicates.append(d)
                
                if len(matching_db_duplicates) >= seen_count:
                    transactions_skipped += 1
                    continue
                    
                # Insert new transaction
                new_txn = MFTransaction(
                    import_batch_id=import_batch.id,
                    person_id=person.id,
                    account_id=account.id,
                    folio_number=folio_num,
                    scheme_name=scheme_name,
                    amfi_code=amfi_code if amfi_code and amfi_code != "0" else None,
                    isin=isin,
                    transaction_date=txn_date,
                    transaction_type=txn_type,
                    units=units,
                    nav=nav,
                    amount=amount,
                    status=TransactionStatus.ACTIVE,
                    notes=f"[{cams_adv}] {txn.get('description', '')}".strip() if cams_adv else txn.get("description", "")
                )
                db.add(new_txn)
                transactions_added += 1

            # If scheme has positive open units and no active transactions in DB cover them,
            # create an opening balance transaction so the holding is recognized.
            open_units = float(scheme_data.get("open") or 0.0)
            if open_units > 0.001:
                stmt_period = data.get("statement_period") or {}
                stmt_from_str = stmt_period.get("from")
                try:
                    stmt_start_date = datetime.strptime(stmt_from_str, "%d-%b-%Y").date() if stmt_from_str else datetime(2026, 4, 1).date()
                except:
                    stmt_start_date = datetime(2026, 4, 1).date()

                prior_txns = db.query(MFTransaction).filter(
                    MFTransaction.account_id == account.id,
                    MFTransaction.folio_number == folio_num,
                    MFTransaction.scheme_name == scheme_name,
                    MFTransaction.transaction_date <= stmt_start_date,
                    MFTransaction.status == TransactionStatus.ACTIVE
                ).all()
                prior_unit_sum = sum(float(t.units or 0.0) for t in prior_txns if t.transaction_type in ["PURCHASE", "PURCHASE_SIP", "SWITCH_IN", "OPENING_BALANCE"])
                
                if prior_unit_sum < (open_units - 0.01):
                    needed_open_units = open_units - prior_unit_sum
                    val_dict = scheme_data.get("valuation") or {}
                    cost_val = float(val_dict.get("cost") or scheme_data.get("cost") or 0.0)
                    if needed_open_units == open_units and cost_val > 0:
                        open_amount = cost_val
                    else:
                        open_amount = (needed_open_units / open_units) * cost_val if open_units > 0 else 0.0
                    open_nav = (open_amount / needed_open_units) if needed_open_units > 0 else 0.0
                    
                    adv_note = f"[{cams_adv}]" if cams_adv else ""
                    open_txn = MFTransaction(
                        import_batch_id=import_batch.id,
                        account_id=account.id,
                        person_id=person.id,
                        folio_number=folio_num,
                        scheme_name=scheme_name,
                        amfi_code=amfi_code if amfi_code and amfi_code != "0" else None,
                        isin=isin,
                        transaction_date=stmt_start_date,
                        transaction_type="PURCHASE",
                        units=needed_open_units,
                        nav=open_nav,
                        amount=open_amount,
                        status=TransactionStatus.ACTIVE,
                        notes=f"{adv_note} Opening Balance from CAMS Statement".strip()
                    )
                    db.add(open_txn)
                    transactions_added += 1
                
    # Update holdings dynamically after successful parsing
    import_batch.date_range_from = min_date
    import_batch.date_range_to = max_date
    import_batch.records_imported = transactions_added
    import_batch.status = "COMPLETE"
    
    db.commit()
    
    # Trigger holdings calculation
    update_holdings_for_person(person.id, db)
    
    return {
        "person_name": person.full_name,
        "import_batch_id": import_batch.id,
        "accounts_created": accounts_created,
        "transactions_imported": transactions_added,
        "transactions_skipped": transactions_skipped,
        "date_range": f"{min_date} to {max_date}" if min_date else "N/A"
    }

def update_holdings_for_person(person_id: int, db: Session):
    """
    Recalculates the holdings for a specific person by aggregating all active transactions,
    and updates the mutual_funds.holdings table.
    """
    # Delete existing holdings for this person
    db.query(MFHolding).filter(MFHolding.person_id == person_id).delete()
    
    # Retrieve all active transactions
    txns = db.query(MFTransaction).filter(
        MFTransaction.person_id == person_id,
        MFTransaction.status == TransactionStatus.ACTIVE
    ).order_by(MFTransaction.transaction_date, MFTransaction.id).all()
    
    holdings_dict = {}
    for txn in txns:
        key = (txn.account_id, txn.folio_number, txn.scheme_name, txn.amfi_code, txn.isin)
        if key not in holdings_dict:
            holdings_dict[key] = {"units": 0.0, "cost": 0.0}
            
        units = float(txn.units)
        amount = float(txn.amount)
        txn_type = txn.transaction_type.upper()
        
        if txn_type in ["PURCHASE", "PURCHASE_SIP", "SWITCH_IN", "DIVIDEND_REINVESTMENT", "DIVIDEND_REINVEST", "OPENING_BALANCE"]:
            holdings_dict[key]["units"] += abs(units)
            holdings_dict[key]["cost"] += abs(amount)
        elif txn_type == "REVERSAL":
            holdings_dict[key]["units"] -= abs(units)
            holdings_dict[key]["cost"] -= abs(amount)
            if holdings_dict[key]["units"] < 0:
                holdings_dict[key]["units"] = 0
                holdings_dict[key]["cost"] = 0.0
        elif txn_type in ["REDEMPTION", "SWITCH_OUT"]:
            prev_units = holdings_dict[key]["units"]
            if prev_units > 0.001:
                avg_cost_rate = holdings_dict[key]["cost"] / prev_units
                holdings_dict[key]["cost"] -= abs(units) * avg_cost_rate
            holdings_dict[key]["units"] -= abs(units)
            if holdings_dict[key]["units"] < 0:
                holdings_dict[key]["units"] = 0
                holdings_dict[key]["cost"] = 0.0
                
    # Save the updated holdings
    today_date = datetime.today().date()
    for key, values in holdings_dict.items():
        units = values["units"]
        if units <= 0.001:
            continue
            
        account_id, folio, scheme, amfi, isin = key
        avg_nav = values["cost"] / units if units > 0 else 0.0
        
        from models.market_data import MFNav
        latest_nav_row = None
        if amfi:
            latest_nav_row = db.query(MFNav).filter(
                MFNav.amfi_code == amfi
            ).order_by(MFNav.nav_date.desc()).first()
        
        if not latest_nav_row and scheme:
            latest_nav_row = db.query(MFNav).filter(
                MFNav.scheme_name.ilike(f"%{scheme}%")
            ).order_by(MFNav.nav_date.desc()).first()
        
        current_nav = float(latest_nav_row.nav) if latest_nav_row else None
        current_val = units * current_nav if current_nav else None
        
        holding = MFHolding(
            person_id=person_id,
            account_id=account_id,
            folio_number=folio,
            scheme_name=scheme,
            amfi_code=amfi,
            isin=isin,
            units=units,
            average_nav=avg_nav,
            current_nav=current_nav,
            current_value=current_val,
            as_of_date=today_date
        )
        db.add(holding)
        
    db.commit()


def ingest_mutual_fund_file(file_path: str, password: str = None, db: Session = None, original_filename: str = None) -> dict:
    """
    Unified ingestion entry point that auto-detects whether the file is a CAMS CAS PDF
    or a Children/Investmart 'Transactions Checklist Report' Excel workbook.
    """
    from services.children_ingester import is_transactions_checklist_excel, ingest_children_excel

    orig_name = (original_filename or file_path).lower()
    if orig_name.endswith(('.xlsx', '.xls')) or is_transactions_checklist_excel(file_path):
        return ingest_children_excel(file_path, db=db, original_filename=original_filename)
    else:
        return ingest_cams_pdf(file_path, password=password, db=db, original_filename=original_filename)

