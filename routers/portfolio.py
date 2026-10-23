import os
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from services.consolidated_portfolio_engine import get_consolidated_portfolio_data, get_latest_global_positions_file, get_broker_portfolio_data, export_portfolio_overview_xlsx
from services.booked_profit_engine import get_booked_profits_matrix

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
templates = Jinja2Templates(directory="templates")

# Register the format_inr filter
from routers.mutual_funds import format_inr
templates.env.filters["format_inr"] = format_inr

def format_inr_value(value):
    if value == 0:
        return "0.00 L"
    return f"{value / 1e7:.2f} Cr" if value >= 1e7 else f"{value / 1e5:.2f} L"

def format_pair(inv, cur):
    if inv == 0 and cur == 0:
        return "-"
    return f"{format_inr_value(inv)} / {format_inr_value(cur)}"

@router.get("/", response_class=HTMLResponse)
def portfolio_dashboard(request: Request, from_date: str = None, till_date: str = None, sync_success: bool = False, error_message: str = None, db: Session = Depends(get_db)):
    # Parse from_date safely if provided
    from_date_parsed = None
    if from_date and from_date.strip():
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                from_date_parsed = datetime.strptime(from_date.strip(), fmt).date()
                break
            except ValueError:
                pass

    # Parse till_date safely if provided
    till_date_parsed = None
    if till_date and till_date.strip():
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                till_date_parsed = datetime.strptime(till_date.strip(), fmt).date()
                break
            except ValueError:
                pass

    data = get_consolidated_portfolio_data(db)
    booked_profits = get_booked_profits_matrix(db, from_date=from_date_parsed, till_date=till_date_parsed)
    
    # Generate formatted representations for the UI table
    formatted_rows = []
    for row in data["rows"]:
        f_row = {
            "category": row["category"],
            "pct_change": row["pct_change"],
            "exposure": row["exposure"],
            "TOTAL": format_pair(row["TOTAL"]["invested"], row["TOTAL"]["current"]),
            "TOTAL_raw": row["TOTAL"]
        }
        for code in ["PATRIARCH", "MR", "APEX", "SR", "VG"]:
            f_row[code] = format_pair(row[code]["invested"], row[code]["current"])
            f_row[f"{code}_raw"] = row[code]
        formatted_rows.append(f_row)

    # Format grand totals
    gt = data["grand_total"]
    formatted_gt = {
        "category": "TOTAL",
        "pct_change": gt["pct_change"],
        "exposure": 100.0,
        "TOTAL": format_pair(gt["TOTAL"]["invested"], gt["TOTAL"]["current"]),
        "TOTAL_raw": gt["TOTAL"]
    }
    for code in ["PATRIARCH", "MR", "APEX", "SR", "VG"]:
        formatted_gt[code] = format_pair(gt[code]["invested"], gt[code]["current"])
        formatted_gt[f"{code}_raw"] = gt[code]

    # Format grand totals EX-SGB
    gt_ex_sgb = data["grand_total_ex_sgb"]
    formatted_gt_ex_sgb = {
        "category": "TOTAL (EX-SGB)",
        "pct_change": gt_ex_sgb["pct_change"],
        "exposure": 100.0,
        "TOTAL": format_pair(gt_ex_sgb["TOTAL"]["invested"], gt_ex_sgb["TOTAL"]["current"]),
        "TOTAL_raw": gt_ex_sgb["TOTAL"]
    }
    for code in ["PATRIARCH", "MR", "APEX", "SR", "VG"]:
        formatted_gt_ex_sgb[code] = format_pair(gt_ex_sgb[code]["invested"], gt_ex_sgb[code]["current"])
        formatted_gt_ex_sgb[f"{code}_raw"] = gt_ex_sgb[code]

    # Fetch and Format Broker Data
    broker_data = get_broker_portfolio_data(db)
    formatted_broker_rows = []
    for row in broker_data["rows"]:
        f_row = {
            "category": row["category"],
            "pct_change": row["pct_change"],
            "exposure": row["exposure"],
            "TOTAL": format_pair(row["TOTAL"]["invested"], row["TOTAL"]["current"]),
            "TOTAL_raw": row["TOTAL"]
        }
        for col in ["EQUITY", "UNLISTED", "GOLD", "SILVER", "DEBT", "SGB"]:
            f_row[col] = format_pair(row[col]["invested"], row[col]["current"])
            f_row[f"{col}_raw"] = row[col]
        formatted_broker_rows.append(f_row)

    # Format Broker Grand Totals
    bgt = broker_data["grand_total"]
    formatted_bgt = {
        "category": "TOTAL",
        "pct_change": bgt["pct_change"],
        "exposure": 100.0,
        "TOTAL": format_pair(bgt["TOTAL"]["invested"], bgt["TOTAL"]["current"]),
        "TOTAL_raw": bgt["TOTAL"]
    }
    for col in ["EQUITY", "UNLISTED", "GOLD", "SILVER", "DEBT", "SGB"]:
        formatted_bgt[col] = format_pair(bgt[col]["invested"], bgt[col]["current"])
        formatted_bgt[f"{col}_raw"] = bgt[col]

    # Format Broker Grand Totals EX-SGB
    bgt_ex_sgb = broker_data["grand_total_ex_sgb"]
    formatted_bgt_ex_sgb = {
        "category": "TOTAL (EX-SGB)",
        "pct_change": bgt_ex_sgb["pct_change"],
        "exposure": 100.0,
        "TOTAL": format_pair(bgt_ex_sgb["TOTAL"]["invested"], bgt_ex_sgb["TOTAL"]["current"]),
        "TOTAL_raw": bgt_ex_sgb["TOTAL"]
    }
    for col in ["EQUITY", "UNLISTED", "GOLD", "SILVER", "DEBT", "SGB"]:
        formatted_bgt_ex_sgb[col] = format_pair(bgt_ex_sgb[col]["invested"], bgt_ex_sgb[col]["current"])
        formatted_bgt_ex_sgb[f"{col}_raw"] = bgt_ex_sgb[col]

    success_msg = None
    if sync_success:
        success_msg = "Successfully synchronized AMFI Mutual Fund NAVs and read the latest Global Positions Report!"

    latest_file = get_latest_global_positions_file()
    latest_filename = os.path.basename(latest_file) if latest_file else None

    return templates.TemplateResponse(
        request=request,
        name="portfolio/dashboard.html",
        context={
            "rows": formatted_rows,
            "grand_total": formatted_gt,
            "grand_total_ex_sgb": formatted_gt_ex_sgb,
            "broker_rows": formatted_broker_rows,
            "broker_grand_total": formatted_bgt,
            "broker_grand_total_ex_sgb": formatted_bgt_ex_sgb,
            "booked_profits": booked_profits,
            "selected_from_date": from_date_parsed.strftime("%Y-%m-%d") if from_date_parsed else "",
            "selected_till_date": till_date_parsed.strftime("%Y-%m-%d") if till_date_parsed else "",
            "today_date": datetime.today().strftime("%Y-%m-%d"),
            "success_message": success_msg,
            "error_message": error_message,
            "latest_report_file": latest_filename,
            "summary": {
                "total_cost": data["grand_total_invested"],
                "total_value": data["grand_total_current"],
                "total_gain": data["grand_total_current"] - data["grand_total_invested"],
                "total_returns_pct": gt["pct_change"]
            }
        }
    )

@router.post("/sync")
def sync_portfolio(request: Request, db: Session = Depends(get_db)):
    """Triggers the Mutual Fund NAV sync and refreshes the database session."""
    from services.nav_sync import sync_all_active_navs
    from fastapi.responses import RedirectResponse
    from urllib.parse import quote_plus
    
    try:
        # Sync AMFI NAVs
        sync_all_active_navs(db, fetch_history=False)
        return RedirectResponse("/portfolio/?sync_success=true", status_code=303)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(f"/portfolio/?error_message={quote_plus(str(e))}", status_code=303)

@router.get("/export")
def export_portfolio_overview(db: Session = Depends(get_db)):
    """Generates and downloads the Consolidated Portfolio Overview Excel sheet."""
    try:
        file_path = export_portfolio_overview_xlsx(db)
        from fastapi.responses import FileResponse
        return FileResponse(
            path=file_path,
            filename=os.path.basename(file_path),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": f"Failed to export portfolio overview Excel: {str(e)}"}
