import os
import shutil
import tempfile
import re
from datetime import datetime

fund_type_dict = {
    r'\bGLOBAL\b': 'ROW',
    r'\bCHINA\b': 'ROW',   
    r'\bICICI PRU FOCUSED EQUITY\b': 'FOCUSED', 
    r'\bICICI PRU COMMODITIES\b': 'INFRA', 
    r'\bCANARA ROBECO SAVINGS\b': 'LOW DURATION',
    r'\bQuant Active\b': 'MULTICAP',
    r'\bTata Equity P/E\b': 'VALUE',
    r'\bmanufacture\b': 'MANUFACTURE',
    r'auto': 'AUTO',
    r'LOGISTICS': 'AUTO',
    r'TRANSPORTATION': 'AUTO',
    r'quant active': 'MULTICAP',
    r'BANDHAN CORE EQUITY': 'LARGE & MID',
    r'DSP LARGE & MID CAP': 'LARGE & MID',
    r'DSP LARGE CAP': 'LARGE CAP',
    r'HDFC LARGE AND MID CAP': 'LARGE & MID',
    r'HDFC MID CAP': 'MIDCAP',
    r'CANARA ROBECO EMERGING EQUITIES': 'LARGE & MID',
    r'KOTAK EQUITY OPPORTUNITIES': 'LARGE & MID',
    r'tata equity p/?e': 'VALUE',
    r'manufactur': 'MANUFACTURE',
    r'multicap': 'MULTICAP',
    r'multi\s*cap': 'MULTICAP',
    r'bank': 'FINANCE',
    r'finance': 'FINANCE',
    r'kotak emerging equity': 'MIDCAP',
    r'healthcare': 'HEALTHCARE',
    r'consumption': 'CONSUMER',
    r'silver': 'SILVER',
    r'motilal oswal large and midcap': 'LARGE & MID',
    r'mirae asset large\s*(and|&)\s*midcap': 'LARGE & MID',
    r'large\s*(and|&)\s*mid': 'LARGE & MID',
    r'infrastructure|infra': 'INFRA',
    r'mid\s*cap': 'MIDCAP',
    r'small\s*cap': 'SMALLCAP',
    r'blue\s*chip|bluechip': 'LARGE CAP',
    r'large\s*cap': 'LARGE CAP',
    r'flexi': 'FLEXI',
    r'energy': 'ENERGY',
    r'power\s*(and|&)\s*infra': 'ENERGY & INFRA',
    r'multi\s*asset': 'MULTI-ASSET',
    r'arbitrage': 'ARBITRAGE',
    r'reit|realty|real\s*estate': 'REALTY',
    r'technology|\bit\b': 'IT',
    r'consumer': 'CONSUMER',
    r'defence': 'DEFENCE',
    r'focused': 'FOCUSED FUND',
    r'psu': 'PSU FUND',
    r'value': 'VALUE',
    r'momentum': 'MOMENTUM',
    r'gold': 'GOLD',
    r'overnight': 'OVERNIGHT',
    r'money\s*market': 'MONEY MARKET',
    r'low\s*duration': 'LOW DURATION',
    r'ultra\s*short': 'ULTRA SHORT DURATION',
    r'short\s*term|short\s*duration': 'SHORT DURATION',
    r'floater|floating': 'FLOATER',
    r'corporate\s*bond': 'CORPORATE BOND',
    r'liquid': 'LIQUID',
    r'core equity': 'LARGE & MID',
    r'emerging': 'LARGE & MID',
    r'franklin india prima': 'MIDCAP',
    r'nippon india': 'MIDCAP',
    r'dsp top 100 equity': 'LARGE CAP',
    r'\bMulti-Asset\b': 'MULTI-ASSET',
    r'dsp equity opportunities': 'LARGE & MID',
}

def extract_fund_type(name: str) -> str:
    for pat, ft in fund_type_dict.items():
        if re.search(pat, name, flags=re.IGNORECASE):
            return ft
    return 'UNKNOWN'

def clean_asset_name(name: str) -> str:
    if re.match(r"^nippon india\s+growth\s+fund[-\s]*growth", name, re.IGNORECASE):
        return "NIPPON INDIA GROWTH"
    s = re.sub(r'[-()]', ' ', name)
    # Preserve 'Fund of Fund(s)' as a unit before stripping individual 'fund' words
    s = re.sub(r'\bfund\s+of\s+funds?\b', 'FOF', s, flags=re.IGNORECASE)
    words_to_strip = [
        r'\bfund\b', r'\bgrowth\b', r'\bregular\b', r'\bplan\b', r'\boption\b', 
        r'\bcumulative\b', r'\bscheme\b', r'\bindex\b', r'\bdirect\b', r'\bdir\b', 
        r'\breg\b', r'\bdirect/regular\b', r'\bdividend\b', r'\bidcw\b', r'\bpayout\b', r'\breinvest\b'
    ]
    for pat in words_to_strip:
        s = re.sub(pat, ' ', s, flags=re.IGNORECASE)
    return ' '.join(s.split()).upper()
from fastapi import APIRouter, Depends, Request, Form, File, UploadFile, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models.mutual_funds import MFHolding, MFTransaction
from models.persons import Person
from models.accounts import Account
from models.market_data import MFNav
from models.audit import ImportBatch
from services.cams_ingester import ingest_cams_pdf, ingest_mutual_fund_file, update_holdings_for_person
from services.nav_sync import sync_all_active_navs
from services.performance_engine import get_performance_report
from services.sip_engine import get_sip_report_data
from services.fifo_engine import calculate_fifo_gains, get_financial_year, get_purchase_lots_remaining_units, invalidate_remaining_units_cache
from services.xlsx_exporter import export_portfolio_xlsx
from sqlalchemy import func

router = APIRouter(prefix="/mutual-funds", tags=["mutual-funds"])
templates = Jinja2Templates(directory="templates")

def format_inr(number) -> str:
    """Formats a number in the Indian numbering system (Lakh/Crore).
    Example: 1234567.89 -> 12,34,567.89
    """
    if number is None:
        return "0.00"
    try:
        val = float(number)
    except (ValueError, TypeError):
        return str(number)
        
    is_negative = val < 0
    val = abs(val)
    
    s = f"{val:.2f}"
    parts = s.split('.')
    integer_part = parts[0]
    decimal_part = parts[1]
    
    if len(integer_part) <= 3:
        res = integer_part
    else:
        last_three = integer_part[-3:]
        remaining = integer_part[:-3]
        
        # Group by 2 digits
        groups = []
        while len(remaining) > 0:
            groups.append(remaining[-2:])
            remaining = remaining[:-2]
        groups.reverse()
        
        res = ",".join(groups) + "," + last_three
        
    formatted = f"{res}.{decimal_part}"
    if is_negative:
        formatted = f"-{formatted}"
    return formatted

templates.env.filters["format_inr"] = format_inr

def format_inr_no_paise(number) -> str:
    """Formats a number in the Indian numbering system without paise.
    Example: 1234567.89 -> 12,34,568
    """
    if number is None:
        return "0"
    try:
        val = float(number)
    except (ValueError, TypeError):
        return str(number)
        
    is_negative = val < 0
    val = abs(round(val))
    
    integer_part = str(val)
    if len(integer_part) <= 3:
        res = integer_part
    else:
        last_three = integer_part[-3:]
        remaining = integer_part[:-3]
        
        # Group by 2 digits
        groups = []
        while len(remaining) > 0:
            groups.append(remaining[-2:])
            remaining = remaining[:-2]
        groups.reverse()
        
        res = ",".join(groups) + "," + last_three
        
    formatted = res
    if is_negative:
        formatted = f"-{formatted}"
    return formatted

templates.env.filters["format_inr_no_paise"] = format_inr_no_paise

def format_la_cr(val) -> str:
    """Formats a number using Lakhs (L) and Crores (Cr) suffix.
    Example: 12900000 -> 1.29 Cr, 9750000 -> 97.50 L
    """
    if val is None:
        return "0.00"
    try:
        n = float(val)
    except (ValueError, TypeError):
        return str(val)
        
    is_neg = n < 0
    n = abs(n)
    
    if n >= 10000000: # 1 Crore = 10,000,000
        formatted = f"{n / 10000000:.2f} Cr"
    elif n >= 100000: # 1 Lakh = 100,000
        formatted = f"{n / 100000:.2f} L"
    else:
        formatted = format_inr(n)
        
    return f"-{formatted}" if is_neg else formatted

templates.env.filters["format_la_cr"] = format_la_cr

@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    act: str = "CONSOLIDATED",
    advisor: str = "ALL",
    broker: str = None,
    sync_started: bool = False,
    db: Session = Depends(get_db)
):
    """Main dashboard displaying holdings, valuation, and summary stats with Account and Broker filters."""
    from services.fifo_engine import resolve_advisor

    # Handle broker alias parameter
    active_advisor = broker if broker and broker.strip() else advisor

    # Resolve accounts
    owners = db.query(Person).all()
    portfolio_to_code = {
        "APEX HOLDINGS PVT. LTD.": "APEX",
        "FAMILY MEMBER 1":             "MR",
        "FAMILY PATRIARCH":       "PATRIARCH",
        "FAMILY MEMBER 2":            "VG",
        "FAMILY MEMBER 3":         "SR",
        "NEXUS CAPITAL PRIVATE LIMITED": "NEXUS"
    }
    
    available_accounts = [{"id": 0, "name": "Consolidated Family Portfolio", "code": "CONSOLIDATED"}]
    for p in owners:
        code = portfolio_to_code.get(p.full_name.upper(), p.short_name)
        if code in ["APEX", "PATRIARCH", "VG", "MR", "SR", "NEXUS"]:
            available_accounts.append({"id": p.id, "name": p.full_name, "code": code})
            
    selected_acct = next((a for a in available_accounts if a["code"] == act.upper()), None)
    if not selected_acct:
        selected_acct = available_accounts[0]

    # Query holdings
    query = db.query(MFHolding, Person.full_name.label("owner_name"), Account.account_number.label("acct_num")).join(
        Person, Person.id == MFHolding.person_id
    ).join(
        Account, Account.id == MFHolding.account_id
    )

    if selected_acct["id"] > 0:
        query = query.filter(MFHolding.person_id == selected_acct["id"])

    raw_holdings = query.all()

    # Collect available advisors/brokers
    adv_set = set()
    total_cost = 0.0
    total_value = 0.0
    formatted_holdings = []

    sel_adv = active_advisor.strip().upper() if active_advisor else "ALL"

    for item in raw_holdings:
        h, owner, acct_num = item
        adv = resolve_advisor(h.scheme_name, h.folio_number, "", db, owner_short_name=owner)
        if adv:
            adv_set.add(adv)

        # Apply advisor/broker filter
        if sel_adv != "ALL":
            if adv.upper() != sel_adv:
                continue

        cost = float(h.units) * float(h.average_nav) if h.average_nav else 0.0
        val = float(h.current_value) if h.current_value else 0.0
        
        total_cost += cost
        total_value += val
        
        gain = val - cost
        returns_pct = (gain / cost) if cost > 0 else 0.0
        
        formatted_holdings.append({
            "id": h.id,
            "owner": owner,
            "folio": h.folio_number,
            "scheme": h.scheme_name,
            "advisor": adv,
            "units": float(h.units),
            "avg_nav": float(h.average_nav) if h.average_nav else 0.0,
            "cost_value": cost,
            "current_nav": float(h.current_nav) if h.current_nav else 0.0,
            "current_value": val,
            "gain": gain,
            "returns_pct": returns_pct,
            "as_of_date": h.as_of_date
        })

    available_advisors = ["ALL"] + sorted(list(adv_set))
    total_gain = total_value - total_cost
    total_returns_pct = (total_gain / total_cost) if total_cost > 0 else 0.0
    
    summary = {
        "total_cost": total_cost,
        "total_value": total_value,
        "total_gain": total_gain,
        "total_returns_pct": total_returns_pct
    }
    
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/dashboard.html",
        context={
            "accounts": available_accounts,
            "selected_code": selected_acct["code"],
            "available_advisors": available_advisors,
            "selected_advisor": active_advisor,
            "holdings": formatted_holdings,
            "summary": summary,
            "sync_started": sync_started
        }
    )


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Depends(get_db)):
    """Renders the file upload form."""
    batches = db.query(ImportBatch).filter(
        ImportBatch.asset_class == "Mutual Funds"
    ).order_by(ImportBatch.import_date.desc()).all()
    
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/upload.html",
        context={"batches": batches}
    )

@router.post("/upload")
def upload_file(
    request: Request,
    password: str = Form(None),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    """Processes one or more uploaded CAMS statement PDFs."""
    messages = []
    success_files = 0
    failed_files = 0
    
    for file in files:
        if not file.filename:
            continue
        try:
            # Create a temporary directory to save the file
            temp_dir = tempfile.mkdtemp()
            temp_file_path = os.path.join(temp_dir, file.filename)
            
            with open(temp_file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
                
            # Run ingestion (auto password decrypt is supported for PDFs, Excel checklists auto-detected)
            result = ingest_mutual_fund_file(
                file_path=temp_file_path,
                password=password,
                db=db,
                original_filename=file.filename
            )
            
            # Clean up temp file safely
            try:
                shutil.rmtree(temp_dir)
            except Exception as rmtree_err:
                print(f"Warning: Failed to delete temp directory {temp_dir}: {rmtree_err}")
                
            success_files += 1
            messages.append({
                "type": "success",
                "text": f"Successfully parsed {file.filename}! Imported {result['transactions_imported']} transactions under {result['person_name']}."
            })
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                db.rollback()
            except Exception:
                pass
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as rmtree_err:
                    print(f"Warning: Failed to delete temp directory in exception handler {temp_dir}: {rmtree_err}")
            failed_files += 1
            messages.append({
                "type": "error",
                "text": f"Failed to ingest {file.filename}: {str(e)}"
            })
            
    if success_files > 0:
        # Invalidate both in-memory and database FIFO cache after uploading new transactions
        invalidate_remaining_units_cache(db)
            
    try:
        batches = db.query(ImportBatch).filter(
            ImportBatch.asset_class == "Mutual Funds"
        ).order_by(ImportBatch.import_date.desc()).all()
    except Exception:
        batches = []
    
    # Renders the upload form with a combined summary of success/error messages
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/upload.html",
        context={
            "batches": batches,
            "upload_messages": messages,
            "success_summary": f"Uploaded {success_files} file(s) successfully. Failed {failed_files} file(s)."
        }
    )


@router.post("/sync-outlook")
def sync_outlook_endpoint(
    request: Request,
    db: Session = Depends(get_db)
):
    """Scans local Outlook for CAMS statements and ingests any new files."""
    from services.outlook_scanner import scan_and_sync_cams_from_outlook
    result = scan_and_sync_cams_from_outlook(db=db)
    return result


@router.post("/upload/delete-batch/{batch_id}")

def delete_import_batch(
    request: Request,
    batch_id: int,
    db: Session = Depends(get_db)
):
    """Deletes an import batch and all its associated transactions."""
    from services.cams_ingester import update_holdings_for_person
    from services.fifo_engine import invalidate_remaining_units_cache
    from fastapi.responses import RedirectResponse
    from models.mutual_funds import MFRemainingLot, MFRealizedGain
    
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        return RedirectResponse(url="/mutual-funds/upload", status_code=303)
        
    # Get all transactions for this batch to find unique person_ids
    txns = db.query(MFTransaction).filter(MFTransaction.import_batch_id == batch_id).all()
    txn_ids = [txn.id for txn in txns]
    person_ids = {txn.person_id for txn in txns}
    
    if txn_ids:
        # Delete dependent remaining_lots and realized_gains first to avoid foreign key violations
        db.query(MFRemainingLot).filter(MFRemainingLot.purchase_txn_id.in_(txn_ids)).delete(synchronize_session=False)
        db.query(MFRealizedGain).filter(
            (MFRealizedGain.purchase_txn_id.in_(txn_ids)) | 
            (MFRealizedGain.sale_txn_id.in_(txn_ids))
        ).delete(synchronize_session=False)
        
        # Delete transactions
        db.query(MFTransaction).filter(MFTransaction.import_batch_id == batch_id).delete(synchronize_session=False)
    
    # Delete the batch itself
    db.delete(batch)
    db.commit()
    
    # Recalculate holdings for each affected person
    for person_id in person_ids:
        update_holdings_for_person(person_id, db)
        
    # Invalidate FIFO cache
    invalidate_remaining_units_cache(db)
    
    return RedirectResponse(url="/mutual-funds/upload", status_code=303)

@router.get("/performance", response_class=HTMLResponse)
def performance_report_view(request: Request, db: Session = Depends(get_db)):
    """Displays Fund Type Wise Portfolio Performance Report."""
    from services.performance_engine import get_fund_type_performance_report
    from services.fifo_engine import invalidate_remaining_units_cache
    
    # Ensure cache is invalidated to read fresh lots
    invalidate_remaining_units_cache()
    report = get_fund_type_performance_report(db)
    
    # Calculate overall totals for the bottom row
    total_invested = sum(item["total_invested"] for item in report)
    current_value = sum(item["current_value"] for item in report)
    
    # Weighted average holding period in months for the overall total
    if total_invested > 0:
        total_weighted_period = sum(item["weighted_period"] * item["total_invested"] for item in report) / total_invested
        total_percentage_change = ((current_value - total_invested) / total_invested) * 100.0
        total_years = total_weighted_period / 12.0
        if total_years > 0 and current_value > 0:
            total_cagr = ((current_value / total_invested) ** (1.0 / total_years) - 1.0) * 100.0
        else:
            total_cagr = 0.0
    else:
        total_weighted_period = 0.0
        total_percentage_change = 0.0
        total_cagr = 0.0

    totals = {
        "fund_type": "TOTAL",
        "total_invested": total_invested,
        "current_value": current_value,
        "weighted_period": total_weighted_period,
        "exposure": 100.0,
        "cagr": total_cagr,
        "percentage_change": total_percentage_change
    }

    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/performance.html",
        context={
            "report": report,
            "totals": totals
        }
    )

@router.get("/sips", response_class=HTMLResponse)
def sips_report_view(
    request: Request,
    owner_id: str = Query(None),
    advisor: str = Query(None),
    txn_type: str = Query(None),
    start_date: str = Query("2026-04-01"),
    end_date: str = Query(None),
    db: Session = Depends(get_db)
):
    """Displays inferred active SIPs formatted like the CAMS report with owner, advisor, and date range filters."""
    # Safely convert empty string owner_id to None or integer
    owner_id_int = None
    if owner_id and owner_id.strip():
        try:
            owner_id_int = int(owner_id)
        except ValueError:
            pass

    # Get all sips without filters to extract unique advisors list for dropdown
    all_sips = get_sip_report_data(db)
    advisors = sorted(list({s["advisor"] for s in all_sips if s["advisor"]}))
    
    # Get filtered sips
    sips = get_sip_report_data(
        db=db,
        owner_id=owner_id_int,
        advisor_filter=advisor,
        start_date=start_date,
        end_date=end_date
    )
    
    # Filter by transaction type in Python (SIP / STP / PURCHASE)
    if txn_type and txn_type.strip():
        sips = [s for s in sips if s["txn_type"] == txn_type.upper()]
    
    # Calculate monthly aggregate run-rate
    total_monthly_flow = sum(s["monthly_equivalent"] for s in sips)
    
    # Fetch all owners for dropdown
    owners = db.query(Person).all()
    
    from services.sip_engine import format_amount_label
    
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/sips.html",
        context={
            "sips": sips,
            "total_monthly_flow": total_monthly_flow,
            "total_monthly_flow_formatted": format_amount_label(total_monthly_flow),
            "owners": owners,
            "advisors": advisors,
            "selected_owner_id": owner_id_int,
            "selected_advisor": advisor,
            "selected_txn_type": txn_type,
            "selected_start_date": start_date,
            "selected_end_date": end_date
        }
    )


@router.get("/sips/export")
def sips_export_excel(
    request: Request,
    owner_id: str = Query(None),
    advisor: str = Query(None),
    txn_type: str = Query(None),
    start_date: str = Query("2026-04-01"),
    end_date: str = Query(None),
    db: Session = Depends(get_db)
):
    """Exports the filtered SIP tracker data as a formatted Excel file."""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import (
        Font, PatternFill, Alignment, Border, Side, GradientFill
    )
    from openpyxl.utils import get_column_letter
    from services.sip_engine import get_sip_report_data, format_amount_label
    from datetime import date

    # --- Fetch & filter data (same logic as HTML view) ---
    owner_id_int = None
    if owner_id and owner_id.strip():
        try:
            owner_id_int = int(owner_id)
        except ValueError:
            pass

    sips = get_sip_report_data(
        db=db,
        owner_id=owner_id_int,
        advisor_filter=advisor,
        start_date=start_date,
        end_date=end_date
    )
    if txn_type and txn_type.strip():
        sips = [s for s in sips if s["txn_type"] == txn_type.upper()]

    # --- Build workbook ---
    wb = Workbook()
    ws = wb.active
    ws.title = "SIP Tracker"

    # Colour palette
    HDR_FILL   = PatternFill("solid", fgColor="1E1E2E")   # dark navy
    HDR_FONT   = Font(name="Calibri", bold=True, color="C4B5FD", size=11)  # purple
    TITLE_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=14)
    TITLE_FILL = PatternFill("solid", fgColor="0F0F1A")
    ALT_FILL   = PatternFill("solid", fgColor="16162A")   # subtle alternate row
    NORM_FILL  = PatternFill("solid", fgColor="12121F")
    NORM_FONT  = Font(name="Calibri", color="E2E2F0", size=10)
    MONO_FONT  = Font(name="Courier New", color="E2E2F0", size=10)
    CENTER     = Alignment(horizontal="center", vertical="center")
    RIGHT      = Alignment(horizontal="right",  vertical="center")
    LEFT       = Alignment(horizontal="left",   vertical="center")
    thin       = Side(style="thin", color="2D2D4A")
    BORDER     = Border(bottom=thin)

    TYPE_COLORS = {"SIP": "A78BFA", "STP": "60A5FA", "PURCHASE": "FBBF24"}

    # ---- Title row ----
    ws.merge_cells("A1:J1")
    title_cell = ws["A1"]
    title_cell.value = f"SIP Tracker Export  —  {date.today().strftime('%d %b %Y')}"
    title_cell.font   = TITLE_FONT
    title_cell.fill   = TITLE_FILL
    title_cell.alignment = CENTER
    ws.row_dimensions[1].height = 28

    # ---- Header row ----
    HEADERS = [
        "S No.", "Fund Name", "Account", "Advisor",
        "Type", "Amount", "Total Monthly Amount",
        "Txns (30D)", "Last Date 1", "Last Date 2"
    ]
    COL_WIDTHS = [7, 55, 12, 22, 12, 16, 22, 12, 14, 14]

    for col_idx, (hdr, width) in enumerate(zip(HEADERS, COL_WIDTHS), 1):
        cell = ws.cell(row=2, column=col_idx, value=hdr)
        cell.font      = HDR_FONT
        cell.fill      = HDR_FILL
        cell.alignment = CENTER
        cell.border    = BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[2].height = 20

    # ---- Data rows ----
    for row_idx, s in enumerate(sips, 3):
        fill = ALT_FILL if row_idx % 2 == 0 else NORM_FILL

        def wc(col, value, font=NORM_FONT, align=LEFT):
            c = ws.cell(row=row_idx, column=col, value=value)
            c.font = font; c.fill = fill; c.alignment = align; c.border = BORDER
            return c

        wc(1,  s["s_no"],                         align=CENTER)
        wc(2,  s["scheme"])
        wc(3,  s["owner"],                         align=CENTER)
        wc(4,  s["advisor"])
        # Type cell — coloured text
        type_color = TYPE_COLORS.get(s["txn_type"], "E2E2F0")
        tc = wc(5, s["txn_type"], align=CENTER)
        tc.font = Font(name="Calibri", bold=True, color=type_color, size=10)
        wc(6,  s["amount_formatted"],              align=RIGHT, font=MONO_FONT)
        wc(7,  s["monthly_equivalent_formatted"],  align=RIGHT,
           font=Font(name="Courier New", bold=True, color="60A5FA", size=10))
        # Txns (30D) — colour coded
        cnt = s.get("txn_count_1m", 0)
        cnt_color = "4ADE80" if cnt >= 4 else ("FBBF24" if cnt >= 2 else ("F87171" if cnt >= 1 else "6B7280"))
        cc = wc(8, cnt if cnt > 0 else "—", align=CENTER)
        cc.font = Font(name="Calibri", bold=True, color=cnt_color, size=10)
        wc(9,  s["last_date_1"] or "—",           align=CENTER, font=MONO_FONT)
        wc(10, s["last_date_2"] or "—",           align=CENTER, font=MONO_FONT)
        ws.row_dimensions[row_idx].height = 18

    # ---- Summary row ----
    summary_row = len(sips) + 3
    total = sum(s["monthly_equivalent"] for s in sips)
    ws.merge_cells(f"A{summary_row}:F{summary_row}")
    label = ws.cell(row=summary_row, column=1,
                    value=f"Monthly Run Rate  ({len(sips)} plans)")
    label.font      = Font(name="Calibri", bold=True, color="C4B5FD", size=11)
    label.fill      = HDR_FILL
    label.alignment = RIGHT

    total_cell = ws.cell(row=summary_row, column=7, value=format_amount_label(total))
    total_cell.font      = Font(name="Courier New", bold=True, color="4ADE80", size=11)
    total_cell.fill      = HDR_FILL
    total_cell.alignment = RIGHT
    ws.row_dimensions[summary_row].height = 22

    # Freeze panes below title+header
    ws.freeze_panes = "A3"

    # ---- Stream to browser ----
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"SIP_Tracker_{date.today().strftime('%d%b%Y')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/pnl", response_class=HTMLResponse)
def pnl_report_view(request: Request, fy: str = None, owner_id: str = None, till_date: str = None, db: Session = Depends(get_db)):
    """Displays realized STCG & LTCG capital gains using FIFO."""
    from models.enums import TransactionStatus
    
    # Safely convert empty string owner_id to None or integer
    owner_id_int = None
    if owner_id and owner_id.strip():
        try:
            owner_id_int = int(owner_id)
        except ValueError:
            pass

    # 1. Fetch unique sale transaction dates to construct dynamic financial years list instantly
    query = db.query(MFTransaction.transaction_date).filter(
        MFTransaction.transaction_type.in_(['REDEMPTION', 'SWITCH_OUT']),
        MFTransaction.status == TransactionStatus.ACTIVE
    )
    if owner_id_int:
        query = query.filter(MFTransaction.person_id == owner_id_int)
        
    sale_dates = query.distinct().all()
    
    # Map transaction dates to financial years list
    fys = sorted(list({get_financial_year(d[0]) for d in sale_dates if d[0]}), reverse=True)
    if not fys:
        fys = ["2026-2027"]
        
    # Default to latest financial year if not specified or not present
    if not fy or fy not in fys:
        fy = fys[0]
        
    # 2. Fetch gains ONLY for the chosen owner & financial year (super fast, minimal memory overhead)
    gains = calculate_fifo_gains(db, person_id=owner_id_int, financial_year=fy, till_date=till_date)
    
    # 3. Retrieve list of active owners for the dropdown filter
    owners = db.query(Person).all()
    
    # Aggregate summary by tax category
    summary = {}
    total_purchase = 0.0
    total_sale = 0.0
    total_gain = 0.0
    
    for g in gains:
        cat = g["tax_category"]
        if cat not in summary:
            summary[cat] = {"purchase": 0.0, "sale": 0.0, "gain": 0.0}
        summary[cat]["purchase"] += g["cost_value"]
        summary[cat]["sale"] += g["sale_value"]
        summary[cat]["gain"] += g["gain_loss"]
        
        total_purchase += g["cost_value"]
        total_sale += g["sale_value"]
        total_gain += g["gain_loss"]
        
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/pnl_report.html",
        context={
            "gains": gains[:500],
            "total_gains_count": len(gains),
            "summary": summary,
            "fy": fy,
            "fys": fys,
            "owner_id": owner_id_int,
            "owners": owners,
            "till_date": till_date,
            "total_purchase": total_purchase,
            "total_sale": total_sale,
            "total_gain": total_gain
        }
    )

@router.post("/sync-nav")
def trigger_sync(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Triggers the AMFI API NAV synchronization in the background to prevent browser timeouts."""
    background_tasks.add_task(sync_all_active_navs, db, False)
    return RedirectResponse(url="/mutual-funds/?sync_started=true", status_code=303)

@router.post("/recalculate")
def recalculate_portfolio(request: Request, db: Session = Depends(get_db)):
    """Force clears FIFO caches and recalculates all holdings for all persons."""
    from services.cams_ingester import update_holdings_for_person
    from services.fifo_engine import invalidate_remaining_units_cache
    
    # 1. Invalidate FIFO cache
    invalidate_remaining_units_cache(db)
    
    # 2. Recalculate holdings for all persons
    persons = db.query(Person).all()
    for p in persons:
        update_holdings_for_person(p.id, db)
        
    db.commit()
    
    # Redirect back to referring page
    referer = request.headers.get("referer", "/mutual-funds/pnl")
    return RedirectResponse(url=referer, status_code=303)

@router.get("/export-xlsx")
def export_xlsx(fy: str = "2026-2027", owner_id: str = None, till_date: str = None, db: Session = Depends(get_db)):
    """Generates and streams the Consolidated Portfolio Summary Excel workbook."""
    try:
        # Safely convert empty string owner_id to None or integer
        owner_id_int = None
        if owner_id and owner_id.strip():
            try:
                owner_id_int = int(owner_id)
            except ValueError:
                pass
                
        file_path = export_portfolio_xlsx(db, financial_year=fy, owner_id=owner_id_int, till_date=till_date)
        import os
        return FileResponse(
            path=file_path,
            filename=os.path.basename(file_path),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": f"Failed to export Excel report: {str(e)}"}

@router.get("/transactions/export-xlsx")
def export_transactions_xlsx(
    q: str = "", 
    sort: str = "date", 
    order: str = "desc", 
    limit_fy: str = None,
    hide_sold: str = None,
    types: str = "", 
    start_date: str = "",
    end_date: str = "",
    asset_classes: str = "", 
    db: Session = Depends(get_db)
):
    """Generates and downloads the filtered transactions list as an Excel sheet."""
    from models.enums import TransactionStatus
    from sqlalchemy import or_, func, case
    from datetime import date, datetime
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    import tempfile
    from fastapi.responses import FileResponse

    # Determine actual filter values based on query parameter presence
    q_clean = q.strip()
    
    if limit_fy is not None:
        show_only_recent_fy = limit_fy.lower() == "true"
    else:
        show_only_recent_fy = not bool(q_clean)
        
    if hide_sold is not None:
        exclude_sold_lots = hide_sold.lower() == "true"
    else:
        exclude_sold_lots = not bool(q_clean)

    # 1. Calculate remaining units for FIFO lots to identify fully sold ones
    remaining_units_dict = get_purchase_lots_remaining_units(db)
    
    fully_sold_ids = set()
    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT', 'DIVIDEND_REINVEST']
    redemption_types = ['REDEMPTION', 'SWITCH_OUT', 'DIVIDEND_PAYOUT']
    for txn_id, rem in remaining_units_dict.items():
        if rem <= 0.001:
            fully_sold_ids.add(txn_id)

    # SQL Case statement to classify transactions into Asset Classes dynamically
    asset_class_expr = case(
        (MFTransaction.scheme_name.ilike("%GOLD%"), "GOLD"),
        (MFTransaction.scheme_name.ilike("%SILVER%"), "SILVER"),
        (
            MFTransaction.scheme_name.ilike("%LIQUID%") |
            MFTransaction.scheme_name.ilike("%ULTRA SHORT%") |
            MFTransaction.scheme_name.ilike("%SHORT TERM%") |
            MFTransaction.scheme_name.ilike("%LOW DURATION%") |
            MFTransaction.scheme_name.ilike("%MONEY MARKET%") |
            MFTransaction.scheme_name.ilike("%OVERNIGHT%") |
            MFTransaction.scheme_name.ilike("%SAVINGS%") |
            MFTransaction.scheme_name.ilike("%ARBITRAGE%") |
            MFTransaction.scheme_name.ilike("%GILT%") |
            MFTransaction.scheme_name.ilike("%BOND%") |
            MFTransaction.scheme_name.ilike("%DEBT%") |
            MFTransaction.scheme_name.ilike("%INCOME%"),
            "LIQUID"
        ),
        else_="EQUITY"
    )

    # 2. Build database query
    query = db.query(
        MFTransaction, 
        Person.full_name.label("owner_name"), 
        Account.account_number.label("acct_num")
    ).join(
        Person, Person.id == MFTransaction.person_id
    ).join(
        Account, Account.id == MFTransaction.account_id
    ).filter(
        MFTransaction.status == TransactionStatus.ACTIVE,
        ~MFTransaction.transaction_type.in_(['STT_TAX', 'STAMP_DUTY_TAX'])
    )

    # Apply search filter if present
    if q_clean:
        search_filter = f"%{q_clean}%"
        query = query.filter(
            or_(
                MFTransaction.scheme_name.ilike(search_filter),
                Person.full_name.ilike(search_filter),
                MFTransaction.notes.ilike(search_filter),
                MFTransaction.folio_number.ilike(search_filter),
                MFTransaction.transaction_type.ilike(search_filter)
            )
        )

    # Apply dynamic financial year filter
    if show_only_recent_fy:
        today = date.today()
        if today.month >= 4:
            last_fy_start = date(today.year - 1, 4, 1)
        else:
            last_fy_start = date(today.year - 2, 4, 1)
        query = query.filter(MFTransaction.transaction_date >= last_fy_start)

    # Apply fully sold filter
    if exclude_sold_lots and fully_sold_ids:
        query = query.filter(~MFTransaction.id.in_(list(fully_sold_ids)))

    # Apply transaction types filter
    if types:
        selected_types = [t.strip().upper() for t in types.split(",") if t.strip()]
        if selected_types:
            query = query.filter(MFTransaction.transaction_type.in_(selected_types))

    # Apply asset classes filter
    if asset_classes:
        selected_assets = [a.strip().upper() for a in asset_classes.split(",") if a.strip()]
        if selected_assets:
            query = query.filter(asset_class_expr.in_(selected_assets))

    # Apply custom date range filters
    if start_date:
        try:
            parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            query = query.filter(MFTransaction.transaction_date >= parsed_start)
        except ValueError:
            pass
            
    if end_date:
        try:
            parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
            query = query.filter(MFTransaction.transaction_date <= parsed_end)
        except ValueError:
            pass

    # Sort
    sort_column_map = {
        "owner": Person.full_name,
        "folio": MFTransaction.folio_number,
        "scheme": MFTransaction.scheme_name,
        "date": MFTransaction.transaction_date,
        "type": MFTransaction.transaction_type,
        "units": MFTransaction.units,
        "nav": MFTransaction.nav,
        "amount": MFTransaction.amount,
        "asset_class": asset_class_expr
    }
    sort_col = sort_column_map.get(sort, MFTransaction.transaction_date)
    if order == "asc":
        query = query.order_by(sort_col.asc(), MFTransaction.id.asc())
    else:
        query = query.order_by(sort_col.desc(), MFTransaction.id.desc())

    # Fetch all records
    txns = query.all()

    # Calculate subtotals
    total_purchases = 0.0
    total_redemptions = 0.0
    for item in txns:
        t, _, _ = item
        t_type = t.transaction_type.upper()
        if t_type in purchase_types:
            total_purchases += float(t.amount or 0.0)
        elif t_type in redemption_types:
            total_redemptions += float(t.amount or 0.0)

    # Initialize Excel Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions Ledger"
    ws.views.sheetView[0].showGridLines = True

    # Setup styles
    font_family = "Segoe UI"
    title_font = Font(name=font_family, size=14, bold=True, underline="single", color="000000")
    header_font = Font(name=font_family, size=10, bold=True, color="000000")
    bold_font = Font(name=font_family, size=10, bold=True)
    regular_font = Font(name=font_family, size=10)
    
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    gray_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    header_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    
    thin_black = Side(style='thin', color='000000')
    double_black = Side(style='double', color='000000')
    
    thin_border = Border(left=thin_black, right=thin_black, top=thin_black, bottom=thin_black)
    double_bottom_border = Border(left=thin_black, right=thin_black, top=thin_black, bottom=double_black)

    # Title block
    ws.merge_cells(start_row=2, start_column=2, end_row=2, end_column=11)
    title_cell = ws.cell(row=2, column=2, value="MUTUAL FUND TRANSACTIONS LEDGER")
    title_cell.font = title_font
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 25
    ws.row_dimensions[3].height = 10

    # Headers
    headers = ["Owner", "Folio", "Scheme Name", "Asset Class", "Date", "Type", "Lot Status", "Units", "NAV", "Amount"]
    start_row = 4
    ws.row_dimensions[start_row].height = 26
    
    for col_idx, h in enumerate(headers, start=2):
        cell = ws.cell(row=start_row, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    current_row = start_row + 1

    # Populate Data
    for r_idx, item in enumerate(txns):
        t, owner, acct_num = item
        ws.row_dimensions[current_row].height = 20
        row_fill = gray_fill if r_idx % 2 == 0 else white_fill

        # Python-side fallback asset classification for rendering
        name_upper = t.scheme_name.upper()
        if "GOLD" in name_upper:
            asset_cls = "GOLD"
        elif "SILVER" in name_upper:
            asset_cls = "SILVER"
        elif any(w in name_upper for w in ["LIQUID", "ULTRA SHORT", "SHORT TERM", "LOW DURATION", "MONEY MARKET", "OVERNIGHT", "SAVINGS", "ARBITRAGE", "GILT", "BOND", "DEBT", "INCOME"]):
            asset_cls = "LIQUID"
        else:
            asset_cls = "EQUITY"

        txn_type = t.transaction_type.upper()
        is_purchase = txn_type in purchase_types
        
        lot_status = "N/A"
        if is_purchase:
            rem_units = remaining_units_dict.get(t.id, 0.0)
            initial_units = float(t.units)
            if rem_units <= 0.001:
                lot_status = "Fully Sold"
            elif rem_units >= initial_units - 0.001:
                lot_status = "Unsold"
            else:
                lot_status = "Partially Sold"

        # Write cells
        row_data = [
            (owner, "left"),
            (t.folio_number, "center"),
            (t.scheme_name, "left"),
            (asset_cls, "center"),
            (t.transaction_date.strftime("%Y-%m-%d"), "center"),
            (t.transaction_type, "center"),
            (lot_status, "center"),
            (float(t.units), "right"),
            (float(t.nav), "right"),
            (float(t.amount), "right")
        ]

        for c_idx, (val, alignment) in enumerate(row_data, start=2):
            cell = ws.cell(row=current_row, column=c_idx, value=val)
            cell.font = regular_font
            cell.border = thin_border
            cell.fill = row_fill
            cell.alignment = Alignment(horizontal=alignment, vertical="center")
            if c_idx == 9: # Units
                cell.number_format = '0.000'
            elif c_idx == 10: # NAV
                cell.number_format = '0.0000'
            elif c_idx == 11: # Amount
                cell.number_format = '#,##,##0.00'

        current_row += 1

    # Blank row
    ws.row_dimensions[current_row].height = 10
    current_row += 1

    # Subtotals block
    subtotals = [
        ("TOTAL PURCHASES", total_purchases),
        ("TOTAL REDEMPTIONS", total_redemptions),
        ("NET FLOW", total_purchases - total_redemptions)
    ]
    for label, amt in subtotals:
        ws.row_dimensions[current_row].height = 20
        # Label cell
        ws.merge_cells(start_row=current_row, start_column=2, end_row=current_row, end_column=10)
        lbl_cell = ws.cell(row=current_row, column=2, value=label)
        lbl_cell.font = bold_font
        lbl_cell.alignment = Alignment(horizontal="right", vertical="center")
        
        # Apply borders to merged label cells
        for c in range(2, 11):
            ws.cell(row=current_row, column=c).border = double_bottom_border

        # Amount cell
        amt_cell = ws.cell(row=current_row, column=11, value=amt)
        amt_cell.font = bold_font
        amt_cell.alignment = Alignment(horizontal="right", vertical="center")
        amt_cell.number_format = '#,##,##0.00'
        amt_cell.border = double_bottom_border
        
        current_row += 1

    # Set column widths
    ws.column_dimensions['A'].width = 3
    ws.column_dimensions['B'].width = 25 # Owner
    ws.column_dimensions['C'].width = 16 # Folio
    ws.column_dimensions['D'].width = 40 # Scheme Name
    ws.column_dimensions['E'].width = 14 # Asset Class
    ws.column_dimensions['F'].width = 14 # Date
    ws.column_dimensions['G'].width = 16 # Type
    ws.column_dimensions['H'].width = 16 # Lot Status
    ws.column_dimensions['I'].width = 16 # Units
    ws.column_dimensions['J'].width = 14 # NAV
    ws.column_dimensions['K'].width = 18 # Amount

    # Save to a temporary file
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    file_path = temp_file.name
    temp_file.close()
    
    wb.save(file_path)
    return FileResponse(
        path=file_path,
        filename=f"Transactions_Ledger_Export_{date.today().strftime('%Y%m%d')}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@router.get("/transactions", response_class=HTMLResponse)
def transactions_list_view(
    request: Request, 
    page: int = 1, 
    q: str = "", 
    sort: str = "date", 
    order: str = "desc", 
    limit_fy: str = None,
    hide_sold: str = None,
    types: str = "", # Comma-separated list of selected transaction types
    start_date: str = "",
    end_date: str = "",
    asset_classes: str = "", # Comma-separated list of selected asset classes
    db: Session = Depends(get_db)
):
    """Displays a comprehensive ledger of all transactions with support for live searching, sorting, and type/date filters."""
    from models.enums import TransactionStatus
    from sqlalchemy import or_, func, case
    import math
    from datetime import date, datetime

    if page < 1:
        page = 1

    # Determine actual filter values based on query parameter presence
    q_clean = q.strip()
    
    if limit_fy is not None:
        show_only_recent_fy = limit_fy.lower() == "true"
    else:
        show_only_recent_fy = not bool(q_clean) # Default True if not searching, False if searching
        
    if hide_sold is not None:
        exclude_sold_lots = hide_sold.lower() == "true"
    else:
        exclude_sold_lots = not bool(q_clean) # Default True if not searching, False if searching

    # 1. Calculate remaining units for FIFO lots to identify fully sold ones
    remaining_units_dict = get_purchase_lots_remaining_units(db)
    
    fully_sold_ids = set()
    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT', 'DIVIDEND_REINVEST']
    redemption_types = ['REDEMPTION', 'SWITCH_OUT', 'DIVIDEND_PAYOUT']
    for txn_id, rem in remaining_units_dict.items():
        if rem <= 0.001:
            fully_sold_ids.add(txn_id)

    # Fetch unique active transaction types from the database dynamically
    db_txn_types = db.query(MFTransaction.transaction_type).filter(
        MFTransaction.status == TransactionStatus.ACTIVE,
        ~MFTransaction.transaction_type.in_(['STT_TAX', 'STAMP_DUTY_TAX'])
    ).distinct().order_by(MFTransaction.transaction_type).all()
    all_txn_types = [t[0] for t in db_txn_types if t[0]]

    # SQL Case statement to classify transactions into Asset Classes dynamically
    asset_class_expr = case(
        (MFTransaction.scheme_name.ilike("%GOLD%"), "GOLD"),
        (MFTransaction.scheme_name.ilike("%SILVER%"), "SILVER"),
        (
            MFTransaction.scheme_name.ilike("%LIQUID%") |
            MFTransaction.scheme_name.ilike("%ULTRA SHORT%") |
            MFTransaction.scheme_name.ilike("%SHORT TERM%") |
            MFTransaction.scheme_name.ilike("%LOW DURATION%") |
            MFTransaction.scheme_name.ilike("%MONEY MARKET%") |
            MFTransaction.scheme_name.ilike("%OVERNIGHT%") |
            MFTransaction.scheme_name.ilike("%SAVINGS%") |
            MFTransaction.scheme_name.ilike("%ARBITRAGE%") |
            MFTransaction.scheme_name.ilike("%GILT%") |
            MFTransaction.scheme_name.ilike("%BOND%") |
            MFTransaction.scheme_name.ilike("%DEBT%") |
            MFTransaction.scheme_name.ilike("%INCOME%"),
            "LIQUID"
        ),
        else_="EQUITY"
    )

    # 2. Build database query
    query = db.query(
        MFTransaction, 
        Person.full_name.label("owner_name"), 
        Account.account_number.label("acct_num")
    ).join(
        Person, Person.id == MFTransaction.person_id
    ).join(
        Account, Account.id == MFTransaction.account_id
    ).filter(
        MFTransaction.status == TransactionStatus.ACTIVE,
        ~MFTransaction.transaction_type.in_(['STT_TAX', 'STAMP_DUTY_TAX'])
    )

    # Apply search filter if present
    if q_clean:
        search_filter = f"%{q_clean}%"
        query = query.filter(
            or_(
                MFTransaction.scheme_name.ilike(search_filter),
                Person.full_name.ilike(search_filter),
                MFTransaction.notes.ilike(search_filter),
                MFTransaction.folio_number.ilike(search_filter),
                MFTransaction.transaction_type.ilike(search_filter)
            )
        )

    # Apply dynamic financial year filter
    if show_only_recent_fy:
        today = date.today()
        if today.month >= 4:
            last_fy_start = date(today.year - 1, 4, 1)
        else:
            last_fy_start = date(today.year - 2, 4, 1)
        query = query.filter(MFTransaction.transaction_date >= last_fy_start)

    # Apply fully sold filter (excluding purchase transactions that are fully sold)
    if exclude_sold_lots and fully_sold_ids:
        query = query.filter(~MFTransaction.id.in_(list(fully_sold_ids)))

    # Apply transaction types filter
    selected_types = []
    if types:
        selected_types = [t.strip().upper() for t in types.split(",") if t.strip()]
        if selected_types:
            query = query.filter(MFTransaction.transaction_type.in_(selected_types))

    # Apply asset classes filter
    selected_assets = []
    if asset_classes:
        selected_assets = [a.strip().upper() for a in asset_classes.split(",") if a.strip()]
        if selected_assets:
            query = query.filter(asset_class_expr.in_(selected_assets))

    # Apply custom date range filters
    if start_date:
        try:
            parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            query = query.filter(MFTransaction.transaction_date >= parsed_start)
        except ValueError:
            pass
            
    if end_date:
        try:
            parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
            query = query.filter(MFTransaction.transaction_date <= parsed_end)
        except ValueError:
            pass

    # 3. Calculate subtotals for the entire matched dataset before offset/limit
    subtotal_groups = query.with_entities(
        MFTransaction.transaction_type, 
        func.sum(MFTransaction.amount)
    ).group_by(MFTransaction.transaction_type).all()

    total_purchases = 0.0
    total_redemptions = 0.0
    for txn_type, amt_sum in subtotal_groups:
        t_type = txn_type.upper()
        if t_type in purchase_types:
            total_purchases += float(amt_sum or 0)
        elif t_type in redemption_types:
            total_redemptions += float(amt_sum or 0)

    subtotals = {
        "purchases": total_purchases,
        "redemptions": total_redemptions,
        "net": total_purchases - total_redemptions
    }

    total_count = query.count()

    sort_column_map = {
        "owner": Person.full_name,
        "folio": MFTransaction.folio_number,
        "scheme": MFTransaction.scheme_name,
        "date": MFTransaction.transaction_date,
        "type": MFTransaction.transaction_type,
        "units": MFTransaction.units,
        "nav": MFTransaction.nav,
        "amount": MFTransaction.amount,
        "asset_class": asset_class_expr
    }

    sort_col = sort_column_map.get(sort, MFTransaction.transaction_date)

    if order == "asc":
        query = query.order_by(sort_col.asc(), MFTransaction.id.asc())
    else:
        query = query.order_by(sort_col.desc(), MFTransaction.id.desc())

    limit = 100
    total_pages = max(1, math.ceil(total_count / limit))
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * limit
    txns = query.offset(offset).limit(limit).all()

    formatted_txns = []
    for item in txns:
        t, owner, acct_num = item
        
        # Python-side fallback asset classification for rendering
        name_upper = t.scheme_name.upper()
        if "GOLD" in name_upper:
            asset_cls = "GOLD"
        elif "SILVER" in name_upper:
            asset_cls = "SILVER"
        elif any(w in name_upper for w in ["LIQUID", "ULTRA SHORT", "SHORT TERM", "LOW DURATION", "MONEY MARKET", "OVERNIGHT", "SAVINGS", "ARBITRAGE", "GILT", "BOND", "DEBT", "INCOME"]):
            asset_cls = "LIQUID"
        else:
            asset_cls = "EQUITY"

        txn_type = t.transaction_type.upper()
        is_purchase = txn_type in purchase_types
        
        lot_status = "N/A"
        rem_units = 0.0
        
        if is_purchase:
            rem_units = remaining_units_dict.get(t.id, 0.0)
            initial_units = float(t.units)
            if rem_units <= 0.001:
                lot_status = "Fully Sold"
            elif rem_units >= initial_units - 0.001:
                lot_status = "Unsold"
            else:
                lot_status = "Partially Sold"
                
        formatted_txns.append({
            "id": t.id,
            "owner": owner,
            "folio": t.folio_number,
            "scheme": t.scheme_name,
            "date": t.transaction_date.strftime("%Y-%m-%d"),
            "type": t.transaction_type,
            "units": float(t.units),
            "nav": float(t.nav),
            "amount": float(t.amount),
            "notes": t.notes or "",
            "is_purchase": is_purchase,
            "lot_status": lot_status,
            "remaining_units": rem_units,
            "asset_class": asset_cls
        })

    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/transactions.html",
        context={
            "transactions": formatted_txns,
            "total_count": total_count,
            "page": page,
            "total_pages": total_pages,
            "q": q_clean,
            "sort": sort,
            "order": order,
            "limit": limit,
            "show_only_recent_fy": show_only_recent_fy,
            "exclude_sold_lots": exclude_sold_lots,
            "limit_fy": "true" if show_only_recent_fy else "false",
            "hide_sold": "true" if exclude_sold_lots else "false",
            "selected_types": selected_types,
            "types_str": types,
            "all_txn_types": all_txn_types,
            "selected_assets": selected_assets,
            "asset_classes_str": asset_classes,
            "start_date": start_date,
            "end_date": end_date,
            "subtotals": subtotals
        }
    )

@router.post("/transactions/{txn_id}/update-date")
def update_transaction_date(txn_id: int, date_str: str = Form(...), db: Session = Depends(get_db)):
    """Inline API to update a transaction date and persist to database."""
    try:
        new_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        txn = db.query(MFTransaction).filter(MFTransaction.id == txn_id).first()
        if not txn:
            return {"success": False, "error": "Transaction not found."}
            
        old_date = txn.transaction_date
        txn.transaction_date = new_date
        db.commit()
        print(f"Updated transaction {txn_id} date from {old_date} to {new_date}")
        invalidate_remaining_units_cache() # Invalidate global cache on transaction update
        return {"success": True, "old_date": str(old_date), "new_date": str(new_date)}
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}

@router.post("/rerun")
def rerun_engine_trigger(db: Session = Depends(get_db)):
    """Triggers holding updates and re-syncs all client portfolio parameters."""
    try:
        persons = db.query(Person).all()
        for p in persons:
            print(f"Rerunning holdings calculation for: {p.full_name}")
            update_holdings_for_person(p.id, db)
        invalidate_remaining_units_cache() # Invalidate global cache on full rerun
        return {"success": True, "message": f"Successfully recalculated holdings and gains for {len(persons)} owners!"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

# ------------------------------------------------------------
# New Dashboard 1: Account Report Dashboard
# ------------------------------------------------------------
@router.get("/account-report", response_class=HTMLResponse)
def account_report_view(request: Request, act: str = None, db: Session = Depends(get_db)):
    """Displays a consolidated account-wise report (APEX, MR, PATRIARCH, VG)."""
    from services.account_report_engine import get_account_report_data
    
    portfolio_to_code = {
        "APEX HOLDINGS PVT. LTD.": "APEX",
        "FAMILY MEMBER 1":             "MR",
        "FAMILY PATRIARCH":       "PATRIARCH",
        "FAMILY MEMBER 2":            "VG",
        "FAMILY MEMBER 3":         "SR",
        "NEXUS CAPITAL PRIVATE LIMITED": "NEXUS",
        "FAMILY MEMBER 4":            "MEMBER_4",
        "FAMILY MEMBER 5":            "MEMBER_5",
        "FAMILY MEMBER 6":            "MEMBER_6",
        "FAMILY MEMBER 7":            "MEMBER_7"
    }
    
    # Filter accounts that have active holdings or are mapped
    owners = db.query(Person).all()
    available_accounts = [
        {"id": 0, "name": "Consolidated Family Portfolio", "code": "CONSOLIDATED"},
        {"id": "4_ACCOUNTS", "name": "Core 4 Accounts (PATRIARCH, MR, VG, APEX)", "code": "4_ACCOUNTS"}
    ]
    for p in owners:
        code = portfolio_to_code.get(p.full_name.upper(), p.short_name)
        if code in ["APEX", "PATRIARCH", "VG", "MR", "SR", "NEXUS", "MEMBER_4", "MEMBER_5", "MEMBER_6", "MEMBER_7"]:
            available_accounts.append({"id": p.id, "name": p.full_name, "code": code})
            
    # Default to CONSOLIDATED if not specified or not matching
    selected_acct = None
    if act:
        selected_acct = next((a for a in available_accounts if a["code"].upper() == act.upper()), None)
    if not selected_acct:
        selected_acct = available_accounts[0] # Default to CONSOLIDATED
        
    report_data = {}
    if selected_acct:
        report_data = get_account_report_data(db, selected_acct["id"])
        
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/account_report.html",
        context={
            "accounts": available_accounts,
            "selected_code": selected_acct["code"] if selected_acct else "",
            "report": report_data
        }
    )

# ------------------------------------------------------------
# New Dashboard 2: Unique Funds Master Directory
# ------------------------------------------------------------
@router.get("/unique-funds", response_class=HTMLResponse)
def unique_funds_view(request: Request, q: str = "", db: Session = Depends(get_db)):
    """Displays a unique directory of all mutual funds in the database, grouped by Clean Name."""
    from models.mutual_funds import MFDetail
    from models.enums import TransactionStatus
    from sqlalchemy import or_
    from services.fifo_engine import get_purchase_lots_remaining_units
    
    # 1. Automatically scan transactions for missing unique funds and pre-populate mf_details
    from services.fifo_engine import resolve_advisor
    unique_schemes_query = db.query(MFTransaction.scheme_name).filter(
        MFTransaction.status == TransactionStatus.ACTIVE
    ).distinct().all()
    
    existing_schemes = {d.scheme_name.upper() for d in db.query(MFDetail).all()}
    
    added_new = False
    for item in unique_schemes_query:
        scheme_name = item[0]
        if scheme_name.upper() not in existing_schemes:
            # Infer advisor dynamically from transaction data
            sample_txn = db.query(MFTransaction).filter(
                MFTransaction.scheme_name == scheme_name,
                MFTransaction.status == TransactionStatus.ACTIVE
            ).first()
            if sample_txn:
                advisor = resolve_advisor(scheme_name, sample_txn.folio_number, sample_txn.notes, db)
            else:
                advisor = resolve_advisor(scheme_name, None, None, db)
                    
            f_type = extract_fund_type(scheme_name)
            c_name = clean_asset_name(scheme_name)
            
            new_detail = MFDetail(
                scheme_name=scheme_name,
                clean_name=c_name,
                fund_type=f_type,
                advisor=advisor,
                long_term_tenure=365
            )
            db.add(new_detail)
            added_new = True
            
    # Retroactively clean existing details if they were created with the old simple classification
    existing_details = db.query(MFDetail).all()
    updated_any = False
    for d in existing_details:
        new_clean = clean_asset_name(d.scheme_name)
        new_type = extract_fund_type(d.scheme_name)
        
        # If clean_name is None, empty, or equals scheme_name, clean it
        if not d.clean_name or d.clean_name == d.scheme_name:
            d.clean_name = new_clean
            updated_any = True
            
        # If fund_type is old simple classification, let's autotrack with new detailed classification
        if not d.fund_type or d.fund_type in ["EQUITY", "LIQUID", "ARBITRAGE", "BULLION", "Equity", "Liquid"]:
            d.fund_type = new_type
            updated_any = True
            
    if added_new or updated_any:
        db.commit()
    
    # 2. Compute active schemes (those with remaining FIFO units > 0)
    remaining_units = get_purchase_lots_remaining_units(db)
    # Build a set of scheme_names that have remaining units
    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT']
    active_txns = db.query(MFTransaction.id, MFTransaction.scheme_name).filter(
        MFTransaction.status == TransactionStatus.ACTIVE,
        MFTransaction.transaction_type.in_(purchase_types)
    ).all()
    
    active_scheme_names = set()
    for txn_id, scheme_name in active_txns:
        rem = remaining_units.get(txn_id, 0.0)
        if rem > 0.001:
            active_scheme_names.add(scheme_name.upper())
        
    # 3. Query and return master list with search
    query = db.query(MFDetail)
    q_clean = q.strip()
    if q_clean:
        search_filter = f"%{q_clean}%"
        query = query.filter(
            or_(
                MFDetail.scheme_name.ilike(search_filter),
                MFDetail.clean_name.ilike(search_filter),
                MFDetail.fund_type.ilike(search_filter),
                MFDetail.advisor.ilike(search_filter)
            )
        )
        
    funds = query.order_by(MFDetail.clean_name.asc()).all()
    
    # 4. Group by clean_name in Python to implement the "Axis Flexi Cap" direct/regular deduplication
    grouped_funds = {}
    from services.fifo_engine import resolve_advisor
    for f in funds:
        c_name = (f.clean_name or clean_asset_name(f.scheme_name)).strip().upper()
        is_active = f.scheme_name.upper() in active_scheme_names
        
        # Resolve advisor dynamically using active transaction folios/notes
        advisors_set = set()
        if "DIRECT" in f.scheme_name.upper():
            advisors_set.add("DIRECT")
        else:
            sample_txns = db.query(MFTransaction.folio_number, MFTransaction.notes).filter(
                MFTransaction.scheme_name == f.scheme_name,
                MFTransaction.status == TransactionStatus.ACTIVE
            ).distinct().all()
            if sample_txns:
                for folio_no, notes_str in sample_txns:
                    adv = resolve_advisor(f.scheme_name, folio_no, notes_str, db)
                    if adv:
                        advisors_set.add(adv)
            else:
                # If no active transactions, check if there's any transaction at all
                any_txn = db.query(MFTransaction.folio_number, MFTransaction.notes).filter(
                    MFTransaction.scheme_name == f.scheme_name
                ).first()
                if any_txn:
                    adv = resolve_advisor(f.scheme_name, any_txn.folio_number, any_txn.notes, db)
                    if adv:
                        advisors_set.add(adv)
                else:
                    adv = f.advisor or "DIRECT"
                    advisors_set.add(adv)
        resolved_adv = ", ".join(sorted(list(advisors_set))) if advisors_set else (f.advisor or "DIRECT")
        
        if c_name not in grouped_funds:
            grouped_funds[c_name] = {
                "id": f.id,
                "scheme_names": [f.scheme_name],
                "clean_name": f.clean_name or clean_asset_name(f.scheme_name),
                "fund_type": f.fund_type or "EQUITY",
                "advisor": resolved_adv,
                "long_term_tenure": int(f.long_term_tenure or 365),
                "exit_load_pct": float(f.exit_load_pct or 0.0),
                "no_exit_load_tenure": int(f.no_exit_load_tenure or 0),
                "is_active": is_active
            }
        else:
            grouped_funds[c_name]["scheme_names"].append(f.scheme_name)
            # If ANY underlying scheme is active, the group is active
            if is_active:
                grouped_funds[c_name]["is_active"] = True
            # Add advisor if not already there
            current_advisors = [a.strip().upper() for a in grouped_funds[c_name]["advisor"].split(",")]
            if resolved_adv and resolved_adv.strip().upper() not in current_advisors:
                grouped_funds[c_name]["advisor"] += f", {resolved_adv.strip()}"
            # Keep max/non-zero values for exit loads if present
            if f.exit_load_pct and float(f.exit_load_pct) > grouped_funds[c_name]["exit_load_pct"]:
                grouped_funds[c_name]["exit_load_pct"] = float(f.exit_load_pct)
            if f.no_exit_load_tenure and int(f.no_exit_load_tenure) > grouped_funds[c_name]["no_exit_load_tenure"]:
                grouped_funds[c_name]["no_exit_load_tenure"] = int(f.no_exit_load_tenure)
                
    # Flatten grouped_funds into a list of dicts and join scheme names for original name column display
    formatted_funds = []
    for c_name, data in grouped_funds.items():
        # Display clean name nicely capitalized
        clean_disp = data["clean_name"].title() if data["clean_name"] else c_name.title()
        if "Nippon India Growth" in clean_disp:
            clean_disp = "Nippon India Growth"
        
        original_disp = ", ".join(data["scheme_names"])
        
        formatted_funds.append({
            "id": data["id"],
            "scheme_name": original_disp,
            "clean_name": clean_disp,
            "fund_type": data["fund_type"],
            "advisor": data["advisor"],
            "long_term_tenure": data["long_term_tenure"],
            "exit_load_pct": data["exit_load_pct"],
            "no_exit_load_tenure": data["no_exit_load_tenure"],
            "is_active": data["is_active"]
        })
        
    # Sort alphabetically by clean_name
    formatted_funds.sort(key=lambda x: x["clean_name"])
    
    active_count = sum(1 for f in formatted_funds if f["is_active"])
    
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/unique_funds.html",
        context={
            "funds": formatted_funds,
            "q": q_clean,
            "active_count": active_count,
            "total_count": len(formatted_funds)
        }
    )

# ------------------------------------------------------------
# New Inline API: Bulk Import Exit Loads from CSV Screener
# ------------------------------------------------------------
@router.post("/unique-funds/import-csv")
def import_unique_funds_from_screener(db: Session = Depends(get_db)):
    """Bulk imports long term tenures from the local Mutual_Fund_Screener_5_28_2026.csv file."""
    import csv
    from models.mutual_funds import MFDetail
    
    from pathlib import Path
    root_dir = Path(__file__).resolve().parent.parent
    csv_path = str(root_dir / "notes" / "Mutual_Fund_Screener_5_28_2026.csv")
    if not os.path.exists(csv_path):
        return {"success": False, "error": f"Screener CSV file not found at: {csv_path}"}
        
    try:
        # Helper for cleaning names
        def clean_comp(name: str) -> str:
            if not name:
                return ""
            n = name.upper()
            # Normalize synonyms
            n = re.sub(r'\bROBECO\b', 'ROB', n)
            n = re.sub(r'\bPRUDENTIAL\b', 'PRU', n)
            n = re.sub(r'\bEMERGING EQUITIES\b', 'EMERGING', n)
            n = re.sub(r'\bMIDCAP\b', 'MID CAP', n)
            n = re.sub(r'\bSMALLCAP\b', 'SMALL CAP', n)
            n = re.sub(r'\bFLEXICAP\b', 'FLEXI CAP', n)
            n = re.sub(r'\bMULTICAP\b', 'MULTI CAP', n)
            n = re.sub(r'\bLARGECAP\b', 'LARGE CAP', n)
            n = re.sub(r'\bAND\b', ' ', n)
            n = re.sub(r'\bSUN\s*LIFE\b', 'SL', n)
            n = re.sub(r'\bFINANCIAL\b', 'FIN', n)
            n = re.sub(r'\bSERVICES\b', 'SERV', n)
            
            n = re.sub(r'[^A-Z0-9\s]', ' ', n)
            filler = [
                r'\bDIRECT\b', r'\bREGULAR\b', r'\bGROWTH\b', r'\bIDCW\b', r'\bDIVIDEND\b',
                r'\bMUTUAL\b', r'\bFUNDS\b', r'\bFUND\b', r'\bPLAN\b', r'\bOPTION\b', r'\bEQUITY\b',
                r'\bOF\b', r'\bFOF\b'
            ]
            for w in filler:
                n = re.sub(w, '', n)
            return re.sub(r'\s+', ' ', n).strip()
            
        def infer_long_term_tenure(sub_cat: str, name: str) -> int:
            s = (sub_cat or "").lower()
            n = (name or "").lower()
            
            # List of non-equity categories
            non_equity_keywords = [
                "liquid", "arbitrage", "hybrid", "debt", "gold", "silver", "overnight", 
                "conservative", "gilt", "bond", "fixed", "money market", "treasury", "short duration", 
                "ultra short", "low duration", "credit risk", "floater", "multi asset"
            ]
            if any(k in s for k in non_equity_keywords) or any(k in n for k in non_equity_keywords):
                return 730 # 2 Years
            return 365 # 1 Year

        # Read database records
        db_funds = db.query(MFDetail).all()
        cleaned_db_records = []
        for f in db_funds:
            cleaned_db_records.append({
                "record": f,
                "c_cleaned": clean_comp(f.clean_name),
                "s_cleaned": clean_comp(f.scheme_name)
            })
            
        updated_count = 0
        
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            headers = [h.strip() for h in next(reader)]
            
            # Find column indexes
            name_idx = -1
            sub_cat_idx = -1
            
            for idx, h in enumerate(headers):
                h_lower = h.lower()
                if h_lower == "name":
                    name_idx = idx
                elif h_lower == "sub category":
                    sub_cat_idx = idx
                    
            if name_idx == -1:
                name_idx = 0
                
            # Process rows
            for row in reader:
                if not row or len(row) <= name_idx:
                    continue
                    
                csv_name = row[name_idx].strip()
                if not csv_name:
                    continue
                    
                csv_name_cleaned = clean_comp(csv_name)
                
                # Infer long term tenure based on category or name
                sub_cat = ""
                if sub_cat_idx != -1 and len(row) > sub_cat_idx:
                    sub_cat = row[sub_cat_idx].strip()
                
                lt_tenure = infer_long_term_tenure(sub_cat, csv_name)
                        
                # Match
                matched_records = []
                for entry in cleaned_db_records:
                    if csv_name_cleaned == entry["c_cleaned"] or csv_name_cleaned == entry["s_cleaned"]:
                        matched_records.append(entry["record"])
                    elif csv_name_cleaned and (csv_name_cleaned in entry["s_cleaned"] or entry["c_cleaned"] in csv_name_cleaned):
                        matched_records.append(entry["record"])
                        
                matched_records = list(set(matched_records))
                
                for r in matched_records:
                    if r.long_term_tenure != lt_tenure:
                        r.long_term_tenure = lt_tenure
                        updated_count += 1
                        
            if updated_count > 0:
                db.commit()
                from services.fifo_engine import invalidate_remaining_units_cache
                invalidate_remaining_units_cache()
                return {"success": True, "message": f"Successfully updated {updated_count} fund records from the CSV file!"}
            else:
                return {"success": True, "message": "Import completed, but no changes were needed."}
                
    except Exception as e:
        db.rollback()
        return {"success": False, "error": f"Import failed: {str(e)}"}

# ------------------------------------------------------------
# New Inline API: Add New Unique Fund Record
# ------------------------------------------------------------
@router.post("/unique-funds/add")
def add_unique_fund(
    scheme_name: str = Form(...),
    clean_name: str = Form(None),
    fund_type: str = Form(None),
    advisor: str = Form(None),
    long_term_tenure: int = Form(365),
    exit_load_pct: float = Form(0.0),
    no_exit_load_tenure: int = Form(0),
    db: Session = Depends(get_db)
):
    """Manually adds a new unique fund record to the database."""
    from models.mutual_funds import MFDetail
    
    scheme_name = scheme_name.strip()
    if not scheme_name:
        return {"success": False, "error": "Original Scheme Name is required."}
        
    # Check if scheme_name already exists (case-insensitive check)
    from sqlalchemy import func
    existing = db.query(MFDetail).filter(func.lower(MFDetail.scheme_name) == scheme_name.lower()).first()
    if existing:
        return {"success": False, "error": f"A fund with the scheme name '{scheme_name}' already exists in the database."}
        
    # Default values if not specified
    c_name = clean_name.strip() if (clean_name and clean_name.strip()) else clean_asset_name(scheme_name)
    f_type = fund_type.strip() if (fund_type and fund_type.strip()) else extract_fund_type(scheme_name)
    adv = advisor.strip() if (advisor and advisor.strip()) else "DIRECT"
    
    try:
        new_detail = MFDetail(
            scheme_name=scheme_name,
            clean_name=c_name,
            fund_type=f_type,
            advisor=adv,
            long_term_tenure=long_term_tenure,
            exit_load_pct=exit_load_pct,
            no_exit_load_tenure=no_exit_load_tenure
        )
        db.add(new_detail)
        db.commit()
        
        # Invalidate remaining units cache just in case
        from services.fifo_engine import invalidate_remaining_units_cache
        invalidate_remaining_units_cache()
        
        return {"success": True, "message": "New fund master record added successfully!"}
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}

# ------------------------------------------------------------
# New Inline API: Save Unique Fund Settings
# ------------------------------------------------------------
@router.post("/unique-funds/{fund_id}/update")
def update_unique_fund_settings(
    fund_id: int,
    clean_name: str = Form(...),
    fund_type: str = Form(...),
    advisor: str = Form(...),
    long_term_tenure: int = Form(...),
    exit_load_pct: float = Form(0.0),
    no_exit_load_tenure: int = Form(0),
    db: Session = Depends(get_db)
):
    """Saves edited unique fund metadata from the UI grid and invalidates caches."""
    from models.mutual_funds import MFDetail
    from sqlalchemy import or_
    try:
        fund = db.query(MFDetail).filter(MFDetail.id == fund_id).first()
        if not fund:
            return {"success": False, "error": "Fund not found."}
            
        original_clean = fund.clean_name
        
        # Query all MFDetail rows with this original clean_name or id case-insensitively
        matching_funds = db.query(MFDetail).filter(
            or_(
                func.lower(MFDetail.clean_name) == func.lower(original_clean) if original_clean else False,
                MFDetail.id == fund_id
            )
        ).all()
        
        for f in matching_funds:
            f.clean_name = clean_name.strip()
            f.fund_type = fund_type.strip()
            f.long_term_tenure = long_term_tenure
            f.exit_load_pct = exit_load_pct
            f.no_exit_load_tenure = no_exit_load_tenure
            
        db.commit()
        
        # Invalidate FIFO remaining units cache as advisor or fund type can change FIFO mapping!
        from services.fifo_engine import invalidate_remaining_units_cache
        invalidate_remaining_units_cache()
        
        print(f"Updated {len(matching_funds)} fund details for original clean name: {original_clean}")
        return {"success": True, "message": "Fund settings updated successfully!"}
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}

@router.post("/backup-db")
def trigger_database_backup():
    """Triggers the pg_dump backup to OneDrive by calling backup.py."""
    import sys
    import subprocess
    from pathlib import Path
    
    try:
        # Run backup.py in root of project
        script_path = Path(__file__).parent.parent / "backup.py"
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout
        return {"success": True, "details": output}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

@router.post("/monthly-profits/sync")
def sync_monthly_profits():
    """Invalidates the FIFO remaining units cache to trigger fresh profit and AUM recalculation."""
    from services.fifo_engine import invalidate_remaining_units_cache
    try:
        invalidate_remaining_units_cache()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/monthly-profits", response_class=HTMLResponse)
def monthly_profits_view(
    request: Request,
    act: str = "CONSOLIDATED",
    advisor: str = "ALL",
    fund_type: str = "ALL",
    aum_fund_type: list[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Displays realized profits grouped by year and month with filters."""
    from services.monthly_profits_engine import get_monthly_profits_report
    
    # Parse list of AUM fund types, splitting any comma-separated strings
    parsed_aum_fund_types = []
    if aum_fund_type:
        for ft in aum_fund_type:
            for part in ft.split(","):
                part_clean = part.strip()
                if part_clean:
                    parsed_aum_fund_types.append(part_clean)
    if not parsed_aum_fund_types:
        parsed_aum_fund_types = ["EQUITY", "BULLION"]
        
    # Resolve account/person details
    owners = db.query(Person).all()
    
    portfolio_to_code = {
        "APEX HOLDINGS PVT. LTD.": "APEX",
        "FAMILY MEMBER 1":             "MR",
        "FAMILY PATRIARCH":       "PATRIARCH",
        "FAMILY MEMBER 2":            "VG",
        "FAMILY MEMBER 3":         "SR",
        "NEXUS CAPITAL PRIVATE LIMITED": "NEXUS"
    }
    
    core_person_ids = [p.id for p in owners if portfolio_to_code.get(p.full_name.upper(), p.short_name) in ["APEX", "PATRIARCH", "VG", "MR"]]
    
    available_accounts = [
        {"id": 0, "name": "Consolidated (All Entities)", "code": "CONSOLIDATED"},
        {"id": -1, "name": "Core 4 (APEX, MR, VG, PATRIARCH)", "code": "CORE"}
    ]
    for p in owners:
        code = portfolio_to_code.get(p.full_name.upper(), p.short_name)
        if code in ["APEX", "PATRIARCH", "VG", "MR", "SR", "NEXUS"]:
            available_accounts.append({"id": p.id, "name": p.full_name, "code": code})
            
    # Default to CONSOLIDATED if act is invalid
    selected_acct = next((a for a in available_accounts if a["code"] == act.upper()), None)
    if not selected_acct:
        selected_acct = available_accounts[0]
        
    # Determine person filter: None for CONSOLIDATED, core_person_ids list for CORE, or single ID
    if selected_acct["code"] == "CORE":
        target_person_filter = core_person_ids
    elif selected_acct["id"] > 0:
        target_person_filter = selected_acct["id"]
    else:
        target_person_filter = None
        
    # Get the report data for monthly realized profits
    report_data = get_monthly_profits_report(
        db, 
        person_id=target_person_filter, 
        advisor=advisor, 
        fund_type=fund_type
    )
    
    # Get the AUM report data using multi-select AUM fund types
    from services.aum_performance_engine import get_aum_performance_report
    aum_report = get_aum_performance_report(
        db,
        person_id=target_person_filter,
        advisor=advisor,
        fund_type=parsed_aum_fund_types
    )
    
    # Get the Multi-Horizon 3-Metric Benchmark & Large Cap Comparison data
    from services.benchmark_comparison_engine import get_multi_horizon_comparison
    horizon_comparison = get_multi_horizon_comparison(
        db,
        person_id=target_person_filter,
        advisor=advisor,
        fund_type=parsed_aum_fund_types,
        aum_data=aum_report
    )
    
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/monthly_profits.html",
        context={
            "accounts": available_accounts,
            "selected_code": selected_acct["code"],
            "selected_advisor": advisor,
            "selected_fund_type": fund_type,
            "selected_aum_fund_type_str": ",".join(parsed_aum_fund_types),
            "selected_aum_fund_types": parsed_aum_fund_types,
            "report": report_data,
            "aum_report": aum_report,
            "horizon_comparison": horizon_comparison,
            "years": report_data["years"],
            "profits_by_year_month": report_data["profits_by_year_month"],
            "yearly_totals": report_data["yearly_totals"],
            "monthly_totals": report_data["monthly_totals"],
            "grand_total": report_data["grand_total"],
            "total_realized": report_data["total_realized"],
            "total_stcg": report_data["total_stcg"],
            "total_ltcg": report_data["total_ltcg"],
            "advisors": report_data["advisors"],
            "fund_types": report_data["fund_types"],
            "month_names": report_data["month_names"]
        }
    )

@router.get("/category-alpha", response_class=HTMLResponse)
def category_alpha_view(
    request: Request,
    act: str = "CONSOLIDATED",
    advisor: str = "ALL",
    benchmark: str = "CATEGORY",
    as_of_date: str = None,
    db: Session = Depends(get_db)
):
    """Renders the Category-Wise Alpha Tracker and Tactical Rotation Matrix with Point-in-Time & Multi-Benchmark support."""
    from datetime import datetime, date
    from models.mutual_funds import MFDetail
    from services.category_alpha_engine import get_category_alpha_matrix

    # Parse point-in-time as_of_date if provided
    parsed_date = None
    if as_of_date and as_of_date.strip():
        try:
            parsed_date = datetime.strptime(as_of_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_date = None

    # Resolve account/person details
    owners = db.query(Person).all()
    portfolio_to_code = {
        "APEX HOLDINGS PVT. LTD.": "APEX",
        "FAMILY MEMBER 1":             "MR",
        "FAMILY PATRIARCH":       "PATRIARCH",
        "FAMILY MEMBER 2":            "VG",
        "FAMILY MEMBER 3":         "SR",
        "NEXUS CAPITAL PRIVATE LIMITED": "NEXUS"
    }
    
    available_accounts = [{"id": 0, "name": "Consolidated Family Portfolio", "code": "CONSOLIDATED"}]
    for p in owners:
        code = portfolio_to_code.get(p.full_name.upper(), p.short_name)
        if code in ["APEX", "PATRIARCH", "VG", "MR", "SR", "NEXUS"]:
            available_accounts.append({"id": p.id, "name": p.full_name, "code": code})
            
    selected_acct = next((a for a in available_accounts if a["code"] == act.upper()), None)
    if not selected_acct:
        selected_acct = available_accounts[0]

    # Fetch available advisors
    details = db.query(MFDetail).all()
    adv_set = set()
    for d in details:
        if d.advisor:
            for a in d.advisor.split(","):
                if a.strip():
                    adv_set.add(a.strip())
    available_advisors = ["ALL"] + sorted(list(adv_set))

    # Benchmark options
    benchmark_mode = (benchmark or "CATEGORY").upper()
    if benchmark_mode not in ["CATEGORY", "NIFTY", "HURDLE_12"]:
        benchmark_mode = "CATEGORY"

    # Compute category alpha matrix
    alpha_data = get_category_alpha_matrix(
        db,
        person_id=selected_acct["id"] if selected_acct["id"] > 0 else None,
        advisor=advisor,
        as_of_date=parsed_date,
        benchmark_mode=benchmark_mode
    )

    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/category_alpha.html",
        context={
            "accounts": available_accounts,
            "selected_code": selected_acct["code"],
            "selected_advisor": advisor,
            "available_advisors": available_advisors,
            "benchmark_mode": benchmark_mode,
            "as_of_date": parsed_date.strftime("%Y-%m-%d") if parsed_date else date.today().strftime("%Y-%m-%d"),
            "is_historical": alpha_data["is_historical"],
            "eval_date": alpha_data["eval_date"],
            "alpha_data": alpha_data,
            "categories": alpha_data["categories"],
            "counts": alpha_data["counts"],
            "total_portfolio_value": alpha_data["total_portfolio_value"],
            "bullion_combined_weight": alpha_data["bullion_combined_weight"]
        }
    )

@router.get("/consolidated")
def consolidated_alias_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/mutual-funds/", status_code=302)

@router.get("/underperforming", response_class=HTMLResponse)
def underperforming_view(
    request: Request,
    timeframe: str = "3y",
    category: str = "ALL",
    status: str = "ALL",
    scope: str = "PORTFOLIO",
    timing: str = "ALL",
    sort: str = "SCORE_ASC",
    as_of_date: str = None,
    db: Session = Depends(get_db)
):
    """Renders the Mutual Funds Underperformance & Risk Diagnostics dashboard."""
    from datetime import date
    from services.underperformance_engine import get_underperformance_diagnostics

    data = get_underperformance_diagnostics(
        db,
        timeframe=timeframe,
        category_filter=category,
        status_filter=status,
        scope_filter=scope,
        timing_filter=timing,
        sort_by=sort,
        as_of_date=as_of_date
    )

    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/underperformance.html",
        context={
            "data": data,
            "selected_timeframe": timeframe,
            "selected_category": category,
            "selected_status": status,
            "selected_scope": scope,
            "selected_timing": timing,
            "selected_sort": sort,
            "selected_as_of_date": data["as_of_date"],
            "max_date": date.today().strftime("%Y-%m-%d")
        }
    )

@router.get("/ter-cost", response_class=HTMLResponse)
def ter_cost_view(request: Request, person_id: int = None, advisor: str = None, period: str = "CY_2026", engine_mode: str = "AMFI_NAV", asset_class: str = "ALL", as_of_date: str = None, db: Session = Depends(get_db)):
    """Renders the real-time TER & Broker Cost Drag Analysis page with period-specific quarterly TER master data."""
    from services.ter_engine import calculate_realtime_ter_cost
    from models.persons import Person
    from datetime import date
    
    persons = db.query(Person).all()
    ter_data = calculate_realtime_ter_cost(db, person_id=person_id, period=period, advisor=advisor, engine_mode=engine_mode, asset_class=asset_class, as_of_date=as_of_date)
    schemes = ter_data["schemes"]
        
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/ter_cost.html",
        context={
            "active_tab": "ter-cost",
            "persons": persons,
            "selected_person_id": person_id,
            "selected_advisor": advisor or "ALL",
            "selected_period": period,
            "selected_engine_mode": engine_mode,
            "selected_asset_class": asset_class or "ALL",
            "selected_as_of_date": as_of_date or ter_data.get("as_of_date") or date.today().strftime("%Y-%m-%d"),
            "max_date": date.today().strftime("%Y-%m-%d"),
            "ter_data": ter_data,
            "quarterly_summary": ter_data.get("quarterly_summary"),
            "schemes": schemes
        }
    )

@router.post("/ter-cost/sync-cache")
def sync_ter_cache_view(request: Request):
    """Syncs and rebuilds periodic_ter_cache.json from all excel files in TER directory."""
    from services.ter_engine import sync_and_rebuild_ter_cache
    res = sync_and_rebuild_ter_cache()
    return res


@router.get("/compare-nav", response_class=HTMLResponse)
def compare_nav_view(request: Request, plan: str = "REGULAR", db: Session = Depends(get_db)):
    """Renders the NAV comparison chart page grouped by Fund Type."""
    from services.fifo_engine import get_fund_type
    from models.mutual_funds import MFDetail
    from models.market_data import MFNav
    from sqlalchemy import func
    import re
    
    # 1. Fetch unique schemes from transactions that have valid AMFI codes
    schemes = db.query(
        MFTransaction.scheme_name,
        MFTransaction.amfi_code
    ).filter(
        MFTransaction.amfi_code.isnot(None),
        MFTransaction.amfi_code != "0",
        MFTransaction.amfi_code != ""
    ).distinct().all()
    
    # 2. Fetch scheme details to get mapped fund types
    details = db.query(MFDetail.scheme_name, MFDetail.fund_type).all()
    details_map = {d.scheme_name.upper(): d.fund_type for d in details if d.fund_type}
    
    # 3. Query the latest NAV for all AMFI codes to display current prices
    subq = db.query(
        MFNav.amfi_code,
        func.max(MFNav.nav_date).label('max_date')
    ).group_by(MFNav.amfi_code).subquery()
    
    latest_navs = db.query(
        MFNav.amfi_code,
        MFNav.nav
    ).join(
        subq,
        (MFNav.amfi_code == subq.c.amfi_code) & (MFNav.nav_date == subq.c.max_date)
    ).all()
    
    nav_map = {row.amfi_code: float(row.nav) for row in latest_navs if row.nav}
    
    # 4. Classify plan type and filter
    plan_filter = plan.upper() if plan else "REGULAR"
    
    def get_plan_type(name: str) -> str:
        upper = name.upper()
        if re.search(r'\bDIRECT\b', upper):
            return "DIRECT"
        return "REGULAR"
    
    # 5. Group schemes by Fund Type, filtering by plan type
    grouped_schemes = {}
    for s in schemes:
        scheme_plan = get_plan_type(s.scheme_name)
        if scheme_plan != plan_filter:
            continue
            
        ft = details_map.get(s.scheme_name.upper()) or get_fund_type(s.scheme_name)
        ft_name = ft.upper() if ft else "EQUITY"
        
        code_clean = str(s.amfi_code).strip().split('.')[0]
        latest_price = nav_map.get(code_clean, 0.0)
        
        grouped_schemes.setdefault(ft_name, []).append({
            "name": s.scheme_name,
            "amfi_code": s.amfi_code,
            "latest_nav": latest_price
        })
        
    # Sort groups and names
    sorted_groups = {
        ft: sorted(items, key=lambda x: x["name"])
        for ft, items in sorted(grouped_schemes.items())
    }
    
    return templates.TemplateResponse(
        request=request,
        name="mutual_funds/compare_nav.html",
        context={
            "grouped_schemes": sorted_groups,
            "selected_plan": plan_filter
        }
    )

@router.get("/api/nav-history")
def get_nav_history_api(
    amfi_codes: list[str] = Query(...),
    timeframe: str = "3m",
    db: Session = Depends(get_db)
):
    """Returns historical NAV data for a list of AMFI codes over a timeframe with automatic self-healing backfill."""
    from datetime import date, timedelta
    from models.market_data import MFNav
    from services.nav_sync import sync_nav_for_amfi_code
    from sqlalchemy import func
    
    today = date.today()
    if timeframe == "1m":
        start_date = today - timedelta(days=30)
    elif timeframe == "3m":
        start_date = today - timedelta(days=90)
    elif timeframe == "6m":
        start_date = today - timedelta(days=180)
    elif timeframe == "1y":
        start_date = today - timedelta(days=365)
    else:
        start_date = today - timedelta(days=90)
        
    res = {}
    for code in amfi_codes:
        code_clean = str(code).strip().split('.')[0]
        
        # Check earliest date in database for this AMFI code
        min_date = db.query(func.min(MFNav.nav_date)).filter(MFNav.amfi_code == code_clean).scalar()
        
        # Disabled synchronous network fetch on page load to prevent slow load times/timeouts.
        # Historical sync should be performed via explicit sync operations or background seed tasks.
        # if not min_date or min_date > (start_date + timedelta(days=10)):
        #     print(f"Self-healing: Missing historical NAV for {code_clean} (earliest in DB: {min_date}, requested: {start_date}). Triggering historical sync...")
        #     try:
        #         sync_nav_for_amfi_code(code_clean, db, fetch_history=True)
        #     except Exception as e:
        #         print(f"Self-healing: Failed to sync NAV for {code_clean}: {e}")
                
        navs = db.query(MFNav).filter(
            MFNav.amfi_code == code_clean,
            MFNav.nav_date >= start_date
        ).order_by(MFNav.nav_date.asc()).all()
        
        res[code] = [
            {"date": n.nav_date.strftime("%Y-%m-%d"), "nav": float(n.nav)}
            for n in navs
        ]
        
    return res


@router.post("/self-heal-history")
def self_heal_history(db: Session = Depends(get_db)):
    """
    Scans all active AMFI codes and forcefully pulls their entire historical NAV data
    from the AMFI API for any scheme currently missing 1-year historical data.
    """
    from datetime import date, timedelta
    from models.market_data import MFNav
    from models.mutual_funds import MFHolding, MFTransaction
    from services.nav_sync import sync_nav_for_amfi_code
    from sqlalchemy import func
    import time
    
    amfi_txn = db.query(MFTransaction.amfi_code).filter(
        MFTransaction.amfi_code.isnot(None),
        MFTransaction.amfi_code != "",
        MFTransaction.amfi_code != "0"
    ).distinct().all()
    
    amfi_hold = db.query(MFHolding.amfi_code).filter(
        MFHolding.amfi_code.isnot(None),
        MFHolding.amfi_code != "",
        MFHolding.amfi_code != "0"
    ).distinct().all()
    
    active_codes = {row.amfi_code for row in amfi_txn}.union({row.amfi_code for row in amfi_hold})
    active_codes = {code.strip().split('.')[0] for code in active_codes if code}
    
    today = date.today()
    target_start_date = today - timedelta(days=365)
    
    healed_codes = []
    errors = []
    
    for code in active_codes:
        min_date = db.query(func.min(MFNav.nav_date)).filter(MFNav.amfi_code == code).scalar()
        if not min_date or min_date > target_start_date:
            print(f"Self-healing: Syncing full history for AMFI code {code} (earliest: {min_date})...")
            try:
                rows_added = sync_nav_for_amfi_code(code, db, fetch_history=True)
                healed_codes.append({"code": code, "records_added": rows_added})
                time.sleep(0.1)
            except Exception as e:
                errors.append({"code": code, "error": str(e)})
                
    return {
        "success": True,
        "scanned_codes_count": len(active_codes),
        "healed_codes_count": len(healed_codes),
        "healed_codes": healed_codes,
        "errors": errors
    }

