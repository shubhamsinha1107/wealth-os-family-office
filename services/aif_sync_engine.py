"""
services/aif_sync_engine.py
Automated ingestion & synchronization engine for AIF / PMS portfolio data
from Excel: DATA/AIF DATA.xlsx into PostgreSQL tables:
  - alternatives.pms_aif
  - alternatives.pms_aif_transactions
  - alternatives.pms_aif_valuations
"""

import os
import json
import logging
from datetime import datetime, date
from decimal import Decimal
import openpyxl
from sqlalchemy.orm import Session

from models.alternatives import Alternative, AlternativeTransaction, AlternativeValuation
from models.accounts import Account
from models.persons import Person
from models.enums import AlternativeType, AccountType

logger = logging.getLogger("aif_sync_engine")
logger.setLevel(logging.INFO)

CACHE_FILE = os.path.join(os.path.dirname(__file__), "aif_sync_cache.json")


def get_aif_file_path() -> str:
    """Finds the path to data/aif_holdings.xlsx or returns default."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    primary_path = os.path.join(base_dir, "data", "aif_holdings.xlsx")
    return primary_path


def get_sync_status() -> dict:
    """Reads sync metadata cache."""
    file_path = get_aif_file_path()
    file_exists = os.path.exists(file_path)
    file_mtime = os.path.getmtime(file_path) if file_exists else 0

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data["file_exists"] = file_exists
                data["file_mtime"] = file_mtime
                data["file_mtime_str"] = (
                    datetime.fromtimestamp(file_mtime).strftime("%d-%b-%Y %I:%M %p")
                    if file_exists
                    else "—"
                )
                data["needs_sync"] = file_exists and (file_mtime > data.get("synced_file_mtime", 0))
                return data
        except Exception as e:
            logger.warning(f"Error reading cache: {e}")

    return {
        "file_exists": file_exists,
        "file_mtime": file_mtime,
        "file_mtime_str": (
            datetime.fromtimestamp(file_mtime).strftime("%d-%b-%Y %I:%M %p")
            if file_exists
            else "—"
        ),
        "last_sync_time": None,
        "synced_file_mtime": 0,
        "needs_sync": file_exists,
        "active_count": 0,
        "total_invested": 0.0,
        "total_current_value": 0.0,
    }


def _resolve_person_id(ac_code: str, db: Session) -> int:
    """Maps owner code (APEX, MEMBER_1, PATRIARCH, etc.) to person_id."""
    clean_code = str(ac_code or "").strip().upper()
    person = db.query(Person).filter(Person.short_name == clean_code).first()
    if person:
        return person.id

    if "PATRIARCH" in clean_code:
        p = db.query(Person).filter(Person.short_name == "PATRIARCH").first()
        if p: return p.id
    if "MEMBER_1" in clean_code:
        p = db.query(Person).filter(Person.short_name == "MEMBER_1").first()
        if p: return p.id
    if "APEX" in clean_code:
        p = db.query(Person).filter(Person.short_name == "APEX").first()
        if p: return p.id

    return 35


def _resolve_manager_and_type(fund_name: str) -> tuple[str, AlternativeType, AccountType]:
    """Determines manager name, AlternativeType, and AccountType from fund name."""
    fn_upper = fund_name.upper()

    if "STALLION" in fn_upper:
        return "Stallion Asset", AlternativeType.PMS, AccountType.PMS
    elif "VALUE QUEST" in fn_upper:
        return "ValueQuest", AlternativeType.PMS, AccountType.PMS
    elif "ALCHEMY" in fn_upper:
        return "Alchemy Capital", AlternativeType.AIF, AccountType.AIF
    elif "KOTAK" in fn_upper:
        return "Kotak Wealth", AlternativeType.AIF, AccountType.AIF
    elif "ICICI" in fn_upper:
        return "ICICI Prudential", AlternativeType.AIF, AccountType.AIF
    elif "NIPPON" in fn_upper:
        return "Nippon India", AlternativeType.AIF, AccountType.AIF
    elif "UNIFI" in fn_upper:
        return "Unifi Capital", AlternativeType.AIF, AccountType.AIF
    elif "ASK" in fn_upper:
        return "ASK Wealth", AlternativeType.PMS, AccountType.PMS

    return "Other Alternative", AlternativeType.AIF, AccountType.AIF


def _get_or_create_account(person_id: int, institution_name: str, acc_type: AccountType, db: Session) -> int:
    """Finds or creates an Account record for the given person and manager."""
    acc = (
        db.query(Account)
        .filter(
            Account.person_id == person_id,
            Account.institution_name == institution_name,
            Account.account_type == acc_type,
        )
        .first()
    )
    if acc:
        return acc.id

    new_acc = Account(
        person_id=person_id,
        institution_name=institution_name,
        account_type=acc_type,
        is_active=True,
        notes=f"Auto-created by AIF Sync Engine for {institution_name} ({acc_type.value})",
    )
    db.add(new_acc)
    db.flush()
    return new_acc.id


def _parse_sheet_valuations(sheet_name: str, wb) -> list[dict]:
    """Extracts monthly historical valuation series from a fund sheet."""
    if sheet_name not in wb.sheetnames:
        return []

    ws = wb[sheet_name]
    valuations = []

    # Look for monthly valuations (date in col 6/7/11/12 and amount in next col)
    for r in range(2, 35):
        for c in range(6, 13):
            d_val = ws.cell(r, c).value
            if isinstance(d_val, datetime):
                amt_val = ws.cell(r, c + 1).value
                if isinstance(amt_val, (int, float)) and amt_val > 50000:
                    valuations.append({
                        "date": d_val.date(),
                        "amount": Decimal(str(amt_val)),
                    })

    unique_vals = {}
    for v in valuations:
        unique_vals[v["date"]] = v["amount"]

    return [{"date": d, "amount": amt} for d, amt in sorted(unique_vals.items())]


def _sync_fund_transactions(aif: Alternative, sheet_name: str, wb, db: Session):
    """
    Carefully synchronizes actual capital cash flows (drawdowns, partial redemptions, interest)
    from the sheet into alternatives.pms_aif_transactions without adding mark-to-market valuations.
    """
    if sheet_name not in wb.sheetnames:
        return

    ws = wb[sheet_name]
    cash_txs = []

    for r in range(6, 32):
        d_val = ws.cell(r, 2).value
        amt_val = ws.cell(r, 3).value
        nav_val = ws.cell(r, 4).value
        units_val = ws.cell(r, 5).value
        rem_val = str(ws.cell(r, 6).value or ws.cell(r, 1).value or "").strip()
        rem_upper = rem_val.upper()

        if not isinstance(d_val, datetime) or not isinstance(amt_val, (int, float)) or amt_val == 0:
            continue

        # Skip valuation rows
        if "VALUATION" in rem_upper or "PRE TAX EXIT" in rem_upper or "EXIT VALUATION" in rem_upper or "CURRENT VALUE" in rem_upper:
            continue

        # Categorize cash flow
        if "DRAWDOWN" in rem_upper:
            tx_type = "DRAWDOWN"
        elif "PARTIAL REDEMPTION" in rem_upper or "COMPLETE REDEMPTION" in rem_upper:
            tx_type = "REDEMPTION"
        elif "INTEREST" in rem_upper:
            tx_type = "INTEREST"
        else:
            continue

        cash_txs.append({
            "date": d_val.date(),
            "type": tx_type,
            "amount": abs(Decimal(str(amt_val))),
            "nav": Decimal(str(nav_val)) if isinstance(nav_val, (int, float)) and nav_val > 0 else None,
            "units": Decimal(str(units_val)) if isinstance(units_val, (int, float)) and units_val > 0 else None,
            "notes": rem_val,
        })

    # For Kotak Credit, interest payments are in col 12 & 13
    if sheet_name == "KOTAK CREDIT - APEX":
        for r in range(6, 12):
            d_val = ws.cell(r, 12).value
            amt_val = ws.cell(r, 13).value
            if isinstance(d_val, datetime) and isinstance(amt_val, (int, float)) and amt_val > 0:
                cash_txs.append({
                    "date": d_val.date(),
                    "type": "INTEREST",
                    "amount": Decimal(str(amt_val)),
                    "nav": None,
                    "units": None,
                    "notes": "INTEREST",
                })

    for tx in cash_txs:
        exists = (
            db.query(AlternativeTransaction)
            .filter(
                AlternativeTransaction.alternative_id == aif.id,
                AlternativeTransaction.transaction_date == tx["date"],
                AlternativeTransaction.transaction_type == tx["type"],
                AlternativeTransaction.amount == tx["amount"],
            )
            .first()
        )
        if not exists:
            db.add(AlternativeTransaction(
                alternative_id=aif.id,
                transaction_date=tx["date"],
                transaction_type=tx["type"],
                amount=tx["amount"],
                nav=tx["nav"],
                units=tx["units"],
                notes=tx["notes"],
            ))


def sync_aif_from_excel(db: Session, file_path: str = None) -> dict:
    """
    Parses DATA/AIF DATA.xlsx and synchronizes:
    - 12 active funds into alternatives.pms_aif matching Sheet main exactly
    - Exited funds marked closed with 0 valuation
    - Historical valuations into alternatives.pms_aif_valuations
    - Drawdown & redemption cash flows into alternatives.pms_aif_transactions
    """
    if file_path is None:
        file_path = get_aif_file_path()

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"AIF Excel file not found at: {file_path}")

    file_mtime = os.path.getmtime(file_path)
    wb = openpyxl.load_workbook(file_path, data_only=True)

    if "main" not in wb.sheetnames:
        raise ValueError("Sheet 'main' not found in AIF DATA.xlsx")

    ws = wb["main"]

    # 1. Parse active rows from Sheet 'main' (Rows 4 to 15)
    active_rows = []
    for r in range(4, 25):
        ac_val = ws.cell(r, 1).value
        if not ac_val or str(ac_val).strip().upper() not in ["APEX", "MEMBER_1", "PATRIARCH", "MEMBER_2", "MEMBER_3"]:
            continue

        inv_date_raw = ws.cell(r, 2).value
        fund_name = str(ws.cell(r, 3).value or "").strip()
        inv_amt = ws.cell(r, 4).value
        cur_val = ws.cell(r, 5).value
        exit_load = ws.cell(r, 8).value
        redeem_info = ws.cell(r, 9).value
        as_of_raw = ws.cell(r, 10).value

        if not fund_name or inv_amt is None or cur_val is None:
            continue

        inv_date = (
            inv_date_raw.date()
            if isinstance(inv_date_raw, datetime)
            else (inv_date_raw if isinstance(inv_date_raw, date) else None)
        )
        as_of_date = (
            as_of_raw.date()
            if isinstance(as_of_raw, datetime)
            else (as_of_raw if isinstance(as_of_raw, date) else None)
        )

        # Fix possible typo where as_of year is earlier than inv_date (e.g. ICICI Pipe)
        if as_of_date and inv_date and as_of_date.year < inv_date.year:
            try:
                as_of_date = as_of_date.replace(year=inv_date.year)
            except Exception:
                pass

        if not as_of_date:
            as_of_date = date.today()

        active_rows.append({
            "row_idx": r,
            "owner_code": str(ac_val).strip().upper(),
            "investment_date": inv_date,
            "fund_name": fund_name,
            "amount_invested": Decimal(str(inv_amt)),
            "current_value": Decimal(str(cur_val)),
            "exit_load": exit_load,
            "redeem_info": redeem_info,
            "as_of_date": as_of_date,
        })

    logger.info(f"Parsed {len(active_rows)} active funds from Sheet 'main'")

    # Sheet mapping for drilldown
    fund_to_sheet_map = {
        "APEX ALPHA QUANT CAT II": "Apex-Alpha-CAT2",
        "NEXUS SPECIAL OPPORTUNITIES III": "Nexus-Special-Opp-3",
        "PRIME WEALTH LONG SHORT FUND": "Prime-LongShort-APEX",
        "HORIZON INFRASTRUCTURE YIELD FUND": "Horizon-Infra-PATRIARCH",
        "CATALYST GROWTH VENTURES CAT I": "Catalyst-Growth-APEX",
        "STELLAR CREDIT OPPORTUNITIES": "Stellar-Credit-APEX",
        "QUANTUM TECH INNOVATION AIF": "Quantum-Tech-PATRIARCH",
        "PINNACLE REAL ESTATE CREDIT": "Pinnacle-RE-APEX",
    }

    existing_aifs = db.query(Alternative).all()
    existing_by_id = {a.id: a for a in existing_aifs}
    matched_db_ids = set()

    synced_records = []
    total_invested_calc = Decimal("0")
    total_current_val_calc = Decimal("0")

    vq_counter = 1

    for row in active_rows:
        owner_code = row["owner_code"]
        fund_name = row["fund_name"]
        inv_date = row["investment_date"]
        inv_amt = row["amount_invested"]
        cur_val = row["current_value"]
        as_of_date = row["as_of_date"]

        person_id = _resolve_person_id(owner_code, db)
        manager, alt_type, acc_type = _resolve_manager_and_type(fund_name)
        account_id = _get_or_create_account(person_id, manager, acc_type, db)

        notes_parts = []
        if row["exit_load"]:
            notes_parts.append(f"Exit Load: {row['exit_load']}")
        if row["redeem_info"]:
            notes_parts.append(f"Redeemable: {row['redeem_info']}")
        notes_str = " | ".join(notes_parts) if notes_parts else None

        target_aif = None
        fn_clean = fund_name.upper()

        if "VALUE QUEST" in fn_clean:
            scheme_display_name = f"Value Quest (Tranche {vq_counter})"
            current_tranche = vq_counter
            vq_counter += 1
            for a in existing_aifs:
                if a.id not in matched_db_ids and a.person_id == person_id:
                    if "VALUE QUEST" in (a.scheme_name or "").upper():
                        if (scheme_display_name in (a.scheme_name or "")) or (a.investment_date == inv_date):
                            target_aif = a
                            break
            if not target_aif and current_tranche == 1:
                legacy_vq = existing_by_id.get(21)
                if legacy_vq and legacy_vq.id not in matched_db_ids:
                    target_aif = legacy_vq
        else:
            scheme_display_name = fund_name
            for a in existing_aifs:
                if a.id in matched_db_ids:
                    continue
                if a.person_id != person_id:
                    continue
                a_name = (a.scheme_name or "").upper()
                if "SMART 250" in fn_clean and "SMART 250" in a_name:
                    target_aif = a
                    break
                elif "ICICI" in fn_clean and "ICICI" in a_name:
                    target_aif = a
                    break
                elif "STALLION" in fn_clean and "STALLION" in a_name:
                    target_aif = a
                    break
                elif "NIPPON" in fn_clean and "NIPPON" in a_name:
                    target_aif = a
                    break
                elif "OPTIMUS" in fn_clean and "OPTIMUS" in a_name:
                    target_aif = a
                    break
                elif "CREDIT" in fn_clean and "CREDIT" in a_name:
                    target_aif = a
                    break
                elif "D1" in fn_clean and "D1" in a_name:
                    target_aif = a
                    break
                elif "D2" in fn_clean and "D2" in a_name:
                    target_aif = a
                    break
                elif "EMERGING" in fn_clean and "EMERGING" in a_name:
                    target_aif = a
                    break

        if target_aif:
            target_aif.account_id = account_id
            target_aif.manager_name = manager
            target_aif.scheme_name = scheme_display_name
            target_aif.alternative_type = alt_type
            target_aif.investment_date = inv_date or target_aif.investment_date
            target_aif.amount_invested = inv_amt
            target_aif.current_value = cur_val
            target_aif.last_valuation_date = as_of_date
            if notes_str:
                target_aif.notes = notes_str
            matched_db_ids.add(target_aif.id)
            current_aif_obj = target_aif
        else:
            new_aif = Alternative(
                person_id=person_id,
                account_id=account_id,
                alternative_type=alt_type,
                manager_name=manager,
                scheme_name=scheme_display_name,
                investment_date=inv_date,
                amount_invested=inv_amt,
                current_value=cur_val,
                last_valuation_date=as_of_date,
                notes=notes_str,
            )
            db.add(new_aif)
            db.flush()
            matched_db_ids.add(new_aif.id)
            current_aif_obj = new_aif

        aif_id = current_aif_obj.id
        total_invested_calc += inv_amt
        total_current_val_calc += cur_val

        # Match detailed sheet
        matched_sheet_name = None
        for key, s_name in fund_to_sheet_map.items():
            if key.upper() in fn_clean:
                matched_sheet_name = s_name
                break

        if matched_sheet_name:
            # Sync valuations from sheet
            sheet_vals = _parse_sheet_valuations(matched_sheet_name, wb)
            for v in sheet_vals:
                existing_val = (
                    db.query(AlternativeValuation)
                    .filter(
                        AlternativeValuation.alternative_id == aif_id,
                        AlternativeValuation.valuation_date == v["date"],
                    )
                    .first()
                )
                if not existing_val:
                    db.add(AlternativeValuation(
                        alternative_id=aif_id,
                        valuation_date=v["date"],
                        valuation_amount=v["amount"],
                        notes="Periodic valuation snapshot from Excel sheet",
                    ))

            # Sync transactions (drawdowns/redemptions) for non-split funds
            # Note: For Value Quest tranches, transactions are managed per tranche
            if "VALUE QUEST" not in fn_clean:
                _sync_fund_transactions(current_aif_obj, matched_sheet_name, wb, db)

        # Always ensure latest valuation from Sheet main is recorded in AlternativeValuation
        latest_val = (
            db.query(AlternativeValuation)
            .filter(
                AlternativeValuation.alternative_id == aif_id,
                AlternativeValuation.valuation_date == as_of_date,
            )
            .first()
        )
        if not latest_val:
            db.add(AlternativeValuation(
                alternative_id=aif_id,
                valuation_date=as_of_date,
                valuation_amount=cur_val,
                notes="Latest valuation from Sheet main",
            ))
        else:
            latest_val.valuation_amount = cur_val

        synced_records.append({
            "id": aif_id,
            "owner": owner_code,
            "scheme_name": scheme_display_name,
            "invested": float(inv_amt),
            "current_value": float(cur_val),
            "as_of_date": as_of_date.strftime("%Y-%m-%d"),
        })

    # Ensure Value Quest tranches have their distinct drawdowns
    vq_tranche1 = db.query(Alternative).filter(Alternative.scheme_name.like("%Value Quest (Tranche 1)%")).first()
    if vq_tranche1:
        tx_t1 = db.query(AlternativeTransaction).filter(
            AlternativeTransaction.alternative_id == vq_tranche1.id,
            AlternativeTransaction.transaction_date == date(2025, 4, 15)
        ).first()
        if not tx_t1:
            db.add(AlternativeTransaction(
                alternative_id=vq_tranche1.id,
                transaction_date=date(2025, 4, 15),
                transaction_type="DRAWDOWN",
                amount=Decimal("20000000.00"),
                nav=Decimal("100"),
                units=Decimal("200000"),
                notes="1ST DRAWDOWN (Tranche 1)",
            ))

    vq_tranche2 = db.query(Alternative).filter(Alternative.scheme_name.like("%Value Quest (Tranche 2)%")).first()
    if vq_tranche2:
        tx_t2 = db.query(AlternativeTransaction).filter(
            AlternativeTransaction.alternative_id == vq_tranche2.id,
            AlternativeTransaction.transaction_date == date(2025, 7, 1)
        ).first()
        if not tx_t2:
            db.add(AlternativeTransaction(
                alternative_id=vq_tranche2.id,
                transaction_date=date(2025, 7, 1),
                transaction_type="DRAWDOWN",
                amount=Decimal("20000000.00"),
                nav=Decimal("100"),
                units=Decimal("200000"),
                notes="2ND DRAWDOWN (Tranche 2)",
            ))

    # 5. Handle closed / exited funds (funds in DB not matched in Sheet 'main')
    closed_count = 0
    for a in existing_aifs:
        if a.id not in matched_db_ids:
            a.current_value = Decimal("0.00")
            old_notes = a.notes or ""
            if "CLOSED" not in old_notes.upper() and "EXITED" not in old_notes.upper():
                a.notes = f"CLOSED / EXITED | {old_notes}".strip(" |")
            closed_count += 1

    db.commit()

    # 6. Save cache metadata
    cache_payload = {
        "last_sync_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "synced_file_mtime": file_mtime,
        "active_count": len(synced_records),
        "closed_count": closed_count,
        "total_invested": float(total_invested_calc),
        "total_current_value": float(total_current_val_calc),
        "status": "SUCCESS",
    }
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_payload, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not write cache file: {e}")

    logger.info(
        f"Sync complete. Active: {len(synced_records)}, Invested: ₹{total_invested_calc:,.2f}, "
        f"Valuation: ₹{total_current_val_calc:,.2f}"
    )
    return cache_payload


def auto_sync_if_needed(db: Session) -> bool:
    """Checks if AIF DATA.xlsx file has been modified since last sync. If so, triggers sync."""
    status = get_sync_status()
    if status.get("needs_sync"):
        try:
            logger.info("AIF Excel file has newer modification timestamp. Triggering auto-sync...")
            sync_aif_from_excel(db)
            return True
        except Exception as e:
            logger.error(f"Auto-sync failed: {e}")
            return False
    return False
