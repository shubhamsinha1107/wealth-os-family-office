import os
import glob
import re
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session
from database import SessionLocal
from models.equities import EquityTransaction
from models.audit import ImportBatch
from models.persons import Person
from models.accounts import Account
from models.enums import TransactionStatus, TransactionType, AccountType

def clean_transaction_file(path):
    """
    Parses and cleans the custom Nuvama/CBL/JM Excel transaction sheet format.
    Finds the header row dynamically.
    """
    import tempfile, shutil
    try:
        df = pd.read_excel(path)
    except PermissionError:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp_path = tmp.name
        shutil.copy2(path, tmp_path)
        try:
            df = pd.read_excel(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass

    # Drop first unnamed column if it exists (usually empty margin)
    df = df.iloc[:, 1:]

    expected = ["Date", "Trans. Type", "Asset Name", "Quantity", "Price", "Amount"]

    def is_header_row(row):
        row_str = row.astype(str)
        count = sum(row_str.str.contains(col, case=False, regex=False).any() for col in expected)
        return count >= 3

    header_idx = df.apply(is_header_row, axis=1)

    if not header_idx.any():
        # Fallback: check without dropping first column
        try:
            df = pd.read_excel(path)
        except PermissionError:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                tmp_path = tmp.name
            shutil.copy2(path, tmp_path)
            try:
                df = pd.read_excel(tmp_path)
            finally:
                if os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except Exception: pass
        header_idx = df.apply(is_header_row, axis=1)
        if not header_idx.any():
            raise ValueError(f"Transaction header not found in file: {path}")
        header_row = header_idx[header_idx].index[0]
        header = df.iloc[header_row]
        df = df.iloc[header_row + 1:]
    else:
        header_row = header_idx[header_idx].index[0]
        header = df.iloc[header_row]
        df = df.iloc[header_row + 1:]

    df.columns = [str(c).strip() for c in header]
    df = df.reset_index(drop=True)

    # Remove summary lines at bottom if present
    # Check if 'Total' in first column
    first_col = df.columns[0]
    total_idx = df[df[first_col].astype(str).str.contains("Total", case=False, na=False)].index
    if not total_idx.empty:
        df = df.iloc[:total_idx[0]]

    # Drop fully empty columns
    df = df.dropna(axis=1, how='all')
    return df

def get_asset_mapping():
    """Reads the asset_mapping.csv file and returns a dict mapping Asset Name to Symbol."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mapping_path = os.path.join(base_dir, "DATA", "asset_mapping.csv")
    if not os.path.exists(mapping_path):
        print(f"Warning: asset_mapping.csv not found at {mapping_path}")
        return {}
    
    map_df = pd.read_csv(mapping_path)
    # Map Asset Name to Symbol
    asset_to_symbol = pd.Series(map_df.Symbol.values, index=map_df['Asset Name']).to_dict()
    return asset_to_symbol

def run_equity_import(db: Session = None) -> dict:
    """
    Scans the DATA/DATA directory for CBL, JM, and NUV subdirectories.
    Imports all Excel transaction sheets into PostgreSQL.
    """
    if db is None:
        db = SessionLocal()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "DATA", "DATA")
    
    if not os.path.exists(data_dir):
        raise FileNotFoundError(f"Equities transactions directory not found at: {data_dir}")

    # Load symbol mappings
    asset_to_symbol = get_asset_mapping()

    brokers_map = {
        "NUV": "Nuvama Wealth",
        "CBL": "Centrum Finverse",
        "JM": "JM Financial"
    }

    # Fetch all active transaction signatures to optimize deduplication checks in-memory
    existing_txns = db.query(
        EquityTransaction.account_id,
        EquityTransaction.transaction_date,
        EquityTransaction.symbol,
        EquityTransaction.transaction_type,
        EquityTransaction.quantity,
        EquityTransaction.net_amount
    ).filter(EquityTransaction.status == TransactionStatus.ACTIVE).all()
    
    # Store as a set of tuples: (account_id, transaction_date, symbol, transaction_type_enum, quantity, net_amount)
    existing_set = {
        (t[0], t[1], t[2], t[3], float(t[4]), float(t[5]))
        for t in existing_txns
    }

    imported_count = 0
    skipped_count = 0
    errors_count = 0
    files_processed = 0

    # Search pattern: DATA/DATA/*/*.xlsx
    xlsx_files = glob.glob(os.path.join(data_dir, "*", "*.xlsx"))
    xlsx_files = [f for f in xlsx_files if not os.path.basename(f).startswith("~$")]

    print(f"Found {len(xlsx_files)} equity transaction files to process.")

    for file_path in sorted(xlsx_files):
        filename = os.path.basename(file_path)
        # Parse broker, owner, and range from filename like NUV_APEX_23_25.xlsx
        parts = filename.replace(".xlsx", "").split("_")
        if len(parts) < 3:
            print(f"Skipping file with unrecognized name format: {filename}")
            continue

        broker_code = parts[0].upper()
        owner_code = parts[1].upper()

        # Resolve broker name
        institution_name = brokers_map.get(broker_code)
        if not institution_name:
            print(f"Skipping file with unrecognized broker code '{broker_code}': {filename}")
            continue

        # Resolve Person
        person = db.query(Person).filter(Person.short_name == owner_code).first()
        if not person:
            print(f"Skipping file with unrecognized owner code '{owner_code}': {filename}")
            continue

        # Resolve Account
        account = db.query(Account).filter(
            Account.person_id == person.id,
            Account.institution_name == institution_name
        ).first()
        
        if not account:
            # Auto-create trading account if missing
            from models.enums import AccountType
            account = Account(
                person_id=person.id,
                account_type=AccountType.TRADING,
                institution_name=institution_name,
                is_active=True,
                notes=f"Auto-created from import folder for {person.full_name}"
            )
            db.add(account)
            db.flush()
            print(f"Auto-created Account for {person.short_name} at {institution_name}")

        # Inspect file diagnostics for warnings/notices
        from services.equity_validator import detect_file_metadata
        file_diag = detect_file_metadata(file_path)
        if file_diag.get("warnings"):
            for w in file_diag["warnings"]:
                print(f"  ⚠️ Notice for {filename}: {w}")

        print(f"Processing: {filename} -> Owner: {person.short_name}, Broker: {institution_name}")

        try:
            df = clean_transaction_file(file_path)
        except Exception as e:
            print(f"Notice: {filename} does not contain standard transaction rows ({e}). Skipping.")
            continue

        files_processed += 1

        # Create an Import Batch for auditing
        import_batch = ImportBatch(
            asset_class="Equities",
            source="MPROFIT_EXCEL",
            file_name=filename,
            status="PENDING",
            notes=f"Importing {len(df)} transactions for {person.full_name} at {institution_name}"
        )
        db.add(import_batch)
        db.flush()

        # Mapping dictionary for transaction types
        # Map values to TransactionType enum
        txn_type_map = {
            "Buy": TransactionType.BUY,
            "*DeMerger (New)": TransactionType.BUY,
            "*Merger": TransactionType.BUY,
            "Merger": TransactionType.BUY,
            "Dividend Reinvest": TransactionType.BUY,
            "Buy Off-Market/Op. Bal": TransactionType.BUY,
            "Sell": TransactionType.SELL,
            "Buyback": TransactionType.SELL,
            "Bonus": TransactionType.BONUS,
            "*Split": TransactionType.SPLIT,
            "Split": TransactionType.SPLIT,
            "*Merged": TransactionType.MERGER,
            "Merged": TransactionType.MERGER,
            "*DeMerger": TransactionType.DEMERGER,
            "DeMerger": TransactionType.DEMERGER
        }

        min_date = None
        max_date = None

        file_imported_rows = 0

        for _, row in df.iterrows():
            trans_type_raw = str(row.get("Trans. Type", "")).strip()
            if trans_type_raw not in txn_type_map:
                continue

            txn_date_raw = row.get("Date")
            if pd.isna(txn_date_raw):
                continue
            
            # Parse Date
            if isinstance(txn_date_raw, datetime):
                txn_date = txn_date_raw.date()
            else:
                txn_date = pd.to_datetime(txn_date_raw, dayfirst=True).date()

            if min_date is None or txn_date < min_date: min_date = txn_date
            if max_date is None or txn_date > max_date: max_date = txn_date

            asset_name = str(row.get("Asset Name", "")).strip()
            
            # Match symbol
            symbol_raw = asset_to_symbol.get(asset_name, asset_name)
            # Standardize symbol by stripping exchange prefix (e.g. NSE:360ONE-EQ -> 360ONE)
            symbol = symbol_raw
            if ":" in symbol:
                symbol = symbol.split(":")[1]
            if "-EQ" in symbol:
                symbol = symbol.replace("-EQ", "")
            symbol = symbol.strip().upper()
            symbol = symbol[:50]
            company_name = asset_name[:200]

            qty = float(row.get("Quantity", 0))
            price = float(row.get("Price", 0))
            brokerage = float(row.get("Brokerage", 0))
            amount = float(row.get("Amount", 0))

            # Resolve account (support row-level broker override if row explicitly specifies a different broker)
            target_account = account
            row_broker_val = str(row.get("Broker Name", "")).strip().upper()
            if row_broker_val and row_broker_val not in ("0", "NAN", "NONE", ""):
                row_inst_name = None
                if "CENTRUM" in row_broker_val:
                    row_inst_name = "Centrum Finverse"
                elif "JM" in row_broker_val:
                    row_inst_name = "JM Financial"
                elif "NUVAMA" in row_broker_val or "EDELWEISS" in row_broker_val:
                    row_inst_name = "Nuvama Wealth"
                
                if row_inst_name and row_inst_name != institution_name:
                    row_acc = db.query(Account).filter(
                        Account.person_id == person.id,
                        Account.institution_name == row_inst_name
                    ).first()
                    if not row_acc:
                        row_acc = Account(
                            person_id=person.id,
                            account_type=AccountType.TRADING,
                            institution_name=row_inst_name,
                            is_active=True,
                            notes=f"Auto-created from row-level broker for {person.full_name}"
                        )
                        db.add(row_acc)
                        db.flush()
                    target_account = row_acc

            # Deduplication: Check if this transaction already exists in the database
            # Look for active transaction with same account, date, symbol, type, qty, price, and net amount
            txn_type = txn_type_map[trans_type_raw]
            
            # Check duplication in memory
            key = (target_account.id, txn_date, symbol, txn_type, float(qty), float(amount))
            if key in existing_set:
                skipped_count += 1
                continue

            # Add to memory set to prevent duplicates within the same import run
            existing_set.add(key)

            notes = f"Asset: {asset_name} | Broker: {target_account.institution_name}"
            if trans_type_raw == "*DeMerger (New)":
                notes += " | Demerged share allotment"
            elif trans_type_raw in ("*Merger", "Merger"):
                notes += " | Merger allotment"
            elif trans_type_raw == "Dividend Reinvest":
                notes += " | Dividend Reinvestment"
            elif trans_type_raw in ("*Split", "Split"):
                notes += " | Stock split"
            elif trans_type_raw == "Bonus":
                notes += " | Bonus allotment"
            elif trans_type_raw == "Buyback":
                notes += " | Share Buyback"
            if target_account.id != account.id:
                notes += f" | Row-routed from {institution_name} to {target_account.institution_name}"

            # Create EquityTransaction row
            txn_record = EquityTransaction(
                import_batch_id=import_batch.id,
                person_id=person.id,
                account_id=target_account.id,
                symbol=symbol,
                company_name=company_name,
                transaction_date=txn_date,
                transaction_type=txn_type,
                quantity=qty,
                price=price,
                brokerage=brokerage,
                taxes=0.0, # Will split taxes/STT if available, default to 0
                stt=0.0,
                net_amount=amount,
                status=TransactionStatus.ACTIVE,
                notes=notes
            )
            db.add(txn_record)
            imported_count += 1
            file_imported_rows += 1

        # Complete batch
        import_batch.status = "COMPLETE"
        import_batch.date_range_from = min_date
        import_batch.date_range_to = max_date
        import_batch.records_imported = file_imported_rows
        db.commit()

    print(f"\nImport Finished. Summary:")
    print(f"  Files Processed: {files_processed}")
    print(f"  Records Imported: {imported_count}")
    print(f"  Duplicates Skipped: {skipped_count}")
    print(f"  Errors Encountered: {errors_count}")

    # Re-run FIFO gains calculation and holdings snapshot for all accounts
    from services.equity_fifo_engine import recalculate_and_cache_equity_fifo, update_equity_holdings_in_db
    print("Recalculating FIFO gains cache...")
    recalculate_and_cache_equity_fifo(db)
    print("Recalculating Equity holdings snapshot...")
    update_equity_holdings_in_db(db)

    return {
        "files_processed": files_processed,
        "records_imported": imported_count,
        "skipped_duplicates": skipped_count,
        "errors": errors_count
    }

def run_global_equity_import(db: Session = None) -> dict:
    """
    Imports transactions from the unified global transaction report ./data/rGlobalTransReport.xlsx - Apex Holdings\\family_office_DB\\DATA\\rGlobalTransReport.xlsx
    """
    if db is None:
        db = SessionLocal()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    global_file_path = os.path.join(base_dir, "DATA", "rGlobalTransReport.xlsx")

    if not os.path.exists(global_file_path):
        raise FileNotFoundError(f"Global Transaction Report not found at: {global_file_path}")

    # Load symbol mappings
    asset_to_symbol = get_asset_mapping()

    # Fetch all active transaction signatures to optimize deduplication checks in-memory
    existing_txns = db.query(
        EquityTransaction.account_id,
        EquityTransaction.transaction_date,
        EquityTransaction.symbol,
        EquityTransaction.transaction_type,
        EquityTransaction.quantity,
        EquityTransaction.net_amount
    ).filter(EquityTransaction.status == TransactionStatus.ACTIVE).all()
    
    # Store as a set of tuples: (account_id, transaction_date, symbol, transaction_type_enum, quantity, net_amount)
    existing_set = {
        (t[0], t[1], t[2], t[3], float(t[4]), float(t[5]))
        for t in existing_txns
    }

    # Create an Import Batch for auditing
    import_batch = ImportBatch(
        asset_class="Equities",
        source="GLOBAL_MPROFIT_EXCEL",
        file_name="rGlobalTransReport.xlsx",
        status="PENDING",
        notes="Importing global transaction ledger"
    )
    db.add(import_batch)
    db.flush()

    # Mappings
    def get_owner_code(portfolio_name):
        p_upper = str(portfolio_name).upper()
        if "FAMILY PATRIARCH" in p_upper or "FAMILY PATRIARCH" in p_upper:
            return "PATRIARCH"
        elif "MEMBER_1" in p_upper:
            return "MR"
        elif "APEX" in p_upper:
            return "APEX"
        elif "MEMBER_2" in p_upper:
            return "VG"
        elif "MEMBER_3" in p_upper:
            return "SR"
        elif "NEXUS CAPITAL" in p_upper:
            return "NEXUS"
        return None

    def get_broker_name(portfolio_name):
        p_upper = str(portfolio_name).upper()
        if "CENTRUM" in p_upper:
            return "Centrum Finverse"
        elif "JM" in p_upper:
            return "JM Financial"
        elif "NUVAMA" in p_upper:
            return "Nuvama Wealth"
        if "NEXUS CAPITAL" in p_upper:
            return "Centrum Finverse"
        return None

    txn_type_map = {
        "Buy": TransactionType.BUY,
        "*DeMerger (New)": TransactionType.BUY,
        "*Merger": TransactionType.BUY,
        "Merger": TransactionType.BUY,
        "Dividend Reinvest": TransactionType.BUY,
        "Buy Off-Market/Op. Bal": TransactionType.BUY,
        "Sell": TransactionType.SELL,
        "Buyback": TransactionType.SELL,
        "Bonus": TransactionType.BONUS,
        "Split": TransactionType.SPLIT,
        "*Split": TransactionType.SPLIT,
        "*Merged": TransactionType.MERGER,
        "Merged": TransactionType.MERGER,
        "*DeMerger": TransactionType.DEMERGER,
        "DeMerger": TransactionType.DEMERGER
    }

    df_raw = pd.read_excel(global_file_path, header=None)
    header_row_idx = None
    for i in range(min(50, len(df_raw))):
        row_vals = [str(x).strip().lower() for x in df_raw.iloc[i].tolist() if pd.notna(x)]
        if 'portfolio name' in row_vals and 'date' in row_vals:
            header_row_idx = i
            break

    if header_row_idx is None:
        raise ValueError("Could not locate transaction header row in global report.")

    df = pd.read_excel(global_file_path, header=header_row_idx)
    df.columns = [str(c).strip() for c in df.columns]
    
    # Filter to only Stocks asset type
    if 'Asset Type' in df.columns:
        df = df[df['Asset Type'].astype(str).str.contains('Stocks', case=False, na=False)]

    imported_count = 0
    skipped_count = 0
    errors_count = 0
    min_date = None
    max_date = None

    for idx, row in df.iterrows():
        p_name = row.get("Portfolio Name")
        if pd.isna(p_name):
            continue

        owner_code = get_owner_code(p_name)
        broker_name = get_broker_name(p_name)

        if not owner_code or not broker_name:
            continue

        person = db.query(Person).filter_by(short_name=owner_code).first()
        if not person:
            continue

        account = db.query(Account).filter(
            Account.person_id == person.id,
            Account.institution_name == broker_name,
            Account.account_type == AccountType.TRADING
        ).first()

        if not account:
            continue

        trans_type_raw = str(row.get("Trans. Type", "")).strip()
        if trans_type_raw not in txn_type_map:
            continue

        txn_date_raw = row.get("Date")
        if pd.isna(txn_date_raw):
            continue

        if isinstance(txn_date_raw, datetime):
            txn_date = txn_date_raw.date()
        else:
            txn_date = pd.to_datetime(txn_date_raw, dayfirst=True).date()

        if min_date is None or txn_date < min_date: min_date = txn_date
        if max_date is None or txn_date > max_date: max_date = txn_date

        asset_name = str(row.get("Asset Name", "")).strip()
        symbol_raw = asset_to_symbol.get(asset_name, asset_name)
        symbol = symbol_raw
        if ":" in symbol:
            symbol = symbol.split(":")[1]
        if "-EQ" in symbol:
            symbol = symbol.replace("-EQ", "")
        symbol = symbol.strip().upper()
        symbol = symbol[:50]
        company_name = asset_name[:200]

        qty = float(row.get("Qty.", 0))
        price = float(row.get("Price", 0))
        amount = float(row.get("Amount", 0))

        txn_type = txn_type_map[trans_type_raw]

        key = (account.id, txn_date, symbol, txn_type, float(qty), float(amount))
        if key in existing_set:
            skipped_count += 1
            continue

        existing_set.add(key)

        txn_record = EquityTransaction(
            import_batch_id=import_batch.id,
            person_id=person.id,
            account_id=account.id,
            symbol=symbol,
            company_name=company_name,
            transaction_date=txn_date,
            transaction_type=txn_type,
            quantity=qty,
            price=price,
            brokerage=0.0,
            taxes=0.0,
            stt=0.0,
            net_amount=amount,
            status=TransactionStatus.ACTIVE,
            notes=f"Global Report: {asset_name}"
        )
        db.add(txn_record)
        imported_count += 1

    import_batch.status = "COMPLETE"
    import_batch.date_range_from = min_date
    import_batch.date_range_to = max_date
    import_batch.records_imported = imported_count
    db.commit()

    # Re-run holdings recalculation for affected accounts
    from services.equity_fifo_engine import update_equity_holdings_in_db
    print("Recalculating Equity holdings snapshot...")
    update_equity_holdings_in_db(db)

    # Re-run FIFO gains calculation
    from services.equity_fifo_engine import recalculate_and_cache_equity_fifo
    print("Recalculating FIFO gains cache...")
    recalculate_and_cache_equity_fifo(db)

    return {
        "records_imported": imported_count,
        "skipped_duplicates": skipped_count,
        "errors": errors_count
    }

if __name__ == "__main__":
    run_equity_import()

