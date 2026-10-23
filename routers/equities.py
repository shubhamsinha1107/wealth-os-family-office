from datetime import datetime, date
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models.equities import EquityHolding, EquityTransaction
from models.enums import TransactionStatus
from models.persons import Person
from models.accounts import Account
from services.equity_fifo_engine import calculate_equity_fifo_gains, update_equity_holdings_in_db, sync_equity_prices_from_feather
from services.equity_importer import run_equity_import
from services.equity_validator import validate_all_equity_files
from sqlalchemy import func

router = APIRouter(prefix="/equities", tags=["equities"])
templates = Jinja2Templates(directory="templates")

# Reuse the INR formatting filter if needed, otherwise register it here
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

def format_la_cr(val) -> str:
    """Formats a number using Lakhs (L) and Crores (Cr) suffix."""
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
def get_dashboard(request: Request, db: Session = Depends(get_db), success_msg: str = None):
    # Fetch active holdings
    holdings_raw = db.query(
        EquityHolding, 
        Person.short_name.label("owner_name"),
        Account.institution_name.label("broker_name")
    ).join(
        Person, Person.id == EquityHolding.person_id
    ).join(
        Account, Account.id == EquityHolding.account_id
    ).order_by(EquityHolding.current_value.desc()).all()

    holdings = []
    total_cost = 0.0
    total_value = 0.0

    for item in holdings_raw:
        h, owner, broker = item
        qty = float(h.quantity)
        avg_cost = float(h.average_cost)
        cost_val = qty * avg_cost
        last_price = float(h.last_price or avg_cost)
        curr_val = float(h.current_value or (qty * last_price))
        gain = curr_val - cost_val
        returns_pct = (gain / cost_val) if cost_val > 0 else 0.0

        total_cost += cost_val
        total_value += curr_val

        holdings.append({
            "owner": owner,
            "broker": broker,
            "symbol": h.symbol,
            "company_name": h.symbol, # Or save full company_name in h if available
            "quantity": qty,
            "avg_cost": avg_cost,
            "cost_value": cost_val,
            "current_price": last_price,
            "current_value": curr_val,
            "gain": gain,
            "returns_pct": returns_pct,
            "as_of_date": h.last_price_date or h.created_at.date()
        })

    total_gain = total_value - total_cost
    total_returns_pct = (total_gain / total_cost) if total_cost > 0 else 0.0

    summary = {
        "total_cost": total_cost,
        "total_value": total_value,
        "total_gain": total_gain,
        "total_returns_pct": total_returns_pct
    }

    from services.equity_validator import validate_all_equity_files
    file_validation = validate_all_equity_files()

    return templates.TemplateResponse(
        request=request,
        name="equities/dashboard.html",
        context={
            "holdings": holdings,
            "summary": summary,
            "success_message": success_msg,
            "file_validation": file_validation
        }
    )

@router.get("/pnl", response_class=HTMLResponse)
def get_pnl_report(
    request: Request, 
    db: Session = Depends(get_db), 
    fy: str = None, 
    quarter: str = None,
    person_id: str = None,
    asset_class: str = None,
    broker: str = None,
    success_msg: str = None
):
    # Parse person_id safely (HTML forms send empty string for "All")
    pid = None
    if person_id and person_id.strip():
        try:
            pid = int(person_id)
        except ValueError:
            pid = None

    # Get available persons for dropdown filter
    persons = db.query(Person).order_by(Person.full_name).all()
    
    # Extract all available financial years using DISTINCT on dates (optimized)
    from services.equity_fifo_engine import get_financial_year as get_fy
    from sqlalchemy import distinct
    distinct_years = db.query(
        distinct(func.extract('year', EquityTransaction.transaction_date))
    ).filter(EquityTransaction.status == TransactionStatus.ACTIVE).all()
    
    available_fys = set()
    for (yr,) in distinct_years:
        if yr:
            yr = int(yr)
            available_fys.add(f"FY{yr}-{str(yr+1)[2:]}")
            available_fys.add(f"FY{yr-1}-{str(yr)[2:]}")
    available_fys = sorted(available_fys, reverse=True)
    
    # Default to current FY if no filter selected
    if not fy:
        fy = get_fy(datetime.today().date())
    
    # Calculate FIFO gains for the selected scope
    results = calculate_equity_fifo_gains(db, person_id=pid, financial_year=fy)
    gains = results["gains"]
    
    # Extract unique brokers for dropdown before applying filters
    brokers = sorted(list(set(g["broker"] for g in gains if g.get("broker"))))

    # Apply asset_class filter if specified
    if asset_class and asset_class.strip():
        gains = [g for g in gains if g.get("asset_class") == asset_class]

    # Apply broker filter if specified
    if broker and broker.strip():
        gains = [g for g in gains if g.get("broker") == broker]

    # ─── Compute Quarterly Summary (Q1 - Q4) & Advance Tax Summary (Schedule CG) ───
    quarterly_summary = None
    period_summary = None
    start_yr = None
    end_yr = None

    if fy and fy.startswith("FY") and len(fy) == 9:
        try:
            start_yr = int(fy[2:6])
            end_yr = 2000 + int(fy[7:9])
            
            # Standard Financial Year Quarters (Apr-Jun, Jul-Sep, Oct-Dec, Jan-Mar)
            quarters_def = [
                {"key": "Q1", "label": f"Q1 (01/04/{str(start_yr)[2:]} to 30/06/{str(start_yr)[2:]})", "name": "Q1", "months": "Apr - Jun", "start": date(start_yr, 4, 1), "end": date(start_yr, 6, 30)},
                {"key": "Q2", "label": f"Q2 (01/07/{str(start_yr)[2:]} to 30/09/{str(start_yr)[2:]})", "name": "Q2", "months": "Jul - Sep", "start": date(start_yr, 7, 1), "end": date(start_yr, 9, 30)},
                {"key": "Q3", "label": f"Q3 (01/10/{str(start_yr)[2:]} to 31/12/{str(start_yr)[2:]})", "name": "Q3", "months": "Oct - Dec", "start": date(start_yr, 10, 1), "end": date(start_yr, 12, 31)},
                {"key": "Q4", "label": f"Q4 (01/01/{str(end_yr)[2:]} to 31/03/{str(end_yr)[2:]})", "name": "Q4", "months": "Jan - Mar", "start": date(end_yr, 1, 1), "end": date(end_yr, 3, 31)},
            ]
            
            quarter_data = []
            grand_intra_q = 0.0
            grand_stcg_q = 0.0
            grand_ltcg_q = 0.0
            running_cum_q = 0.0
            
            for q in quarters_def:
                q_intra = 0.0
                q_stcg = 0.0
                q_ltcg = 0.0
                for g in gains:
                    s_date = g.get("sale_date")
                    if isinstance(s_date, str):
                        try:
                            s_date = datetime.strptime(s_date, "%Y-%m-%d").date()
                        except:
                            s_date = datetime.strptime(s_date, "%d-%m-%Y").date()
                    
                    if q["start"] <= s_date <= q["end"]:
                        val = float(g["gain_loss"])
                        if g["trade_type"] == "INTRADAY":
                            q_intra += val
                        elif g["gain_type"] == "LTCG":
                            q_ltcg += val
                        else:
                            q_stcg += val
                
                q_total = q_intra + q_stcg + q_ltcg
                running_cum_q += q_total
                grand_intra_q += q_intra
                grand_stcg_q += q_stcg
                grand_ltcg_q += q_ltcg
                
                quarter_data.append({
                    "key": q["key"],
                    "period": q["label"],
                    "name": q["name"],
                    "months": q["months"],
                    "intraday": q_intra,
                    "stcg": q_stcg,
                    "ltcg": q_ltcg,
                    "total": q_total,
                    "cumulative": running_cum_q
                })
            
            quarterly_summary = {
                "quarters": quarter_data,
                "grand_total": {
                    "period": f"Grand total for 01-04-{start_yr} to 31-03-{end_yr}",
                    "intraday": grand_intra_q,
                    "stcg": grand_stcg_q,
                    "ltcg": grand_ltcg_q,
                    "total": grand_intra_q + grand_stcg_q + grand_ltcg_q
                }
            }
            
            # Schedule CG Advance Tax 5-period breakdown
            periods = [
                {"label": f"01/04/{str(start_yr)[2:]} to 15/06/{str(start_yr)[2:]}", "start": date(start_yr, 4, 1), "end": date(start_yr, 6, 15), "adv_tax_due": "15%"},
                {"label": f"16/06/{str(start_yr)[2:]} to 15/09/{str(start_yr)[2:]}", "start": date(start_yr, 6, 16), "end": date(start_yr, 9, 15), "adv_tax_due": "45%"},
                {"label": f"16/09/{str(start_yr)[2:]} to 15/12/{str(start_yr)[2:]}", "start": date(start_yr, 9, 16), "end": date(start_yr, 12, 15), "adv_tax_due": "75%"},
                {"label": f"16/12/{str(start_yr)[2:]} to 15/03/{str(end_yr)[2:]}", "start": date(start_yr, 12, 16), "end": date(end_yr, 3, 15), "adv_tax_due": "100%"},
                {"label": f"16/03/{str(end_yr)[2:]} to 31/03/{str(end_yr)[2:]}", "start": date(end_yr, 3, 16), "end": date(end_yr, 3, 31), "adv_tax_due": "100%"},
            ]
            
            period_data = []
            grand_intra = 0.0
            grand_stcg = 0.0
            grand_ltcg = 0.0
            running_cumulative = 0.0
            
            for p in periods:
                p_intra = 0.0
                p_stcg = 0.0
                p_ltcg = 0.0
                for g in gains:
                    s_date = g.get("sale_date")
                    if isinstance(s_date, str):
                        try:
                            s_date = datetime.strptime(s_date, "%Y-%m-%d").date()
                        except:
                            s_date = datetime.strptime(s_date, "%d-%m-%Y").date()
                    
                    if p["start"] <= s_date <= p["end"]:
                        val = float(g["gain_loss"])
                        if g["trade_type"] == "INTRADAY":
                            p_intra += val
                        elif g["gain_type"] == "LTCG":
                            p_ltcg += val
                        else:
                            p_stcg += val
                
                period_total = p_intra + p_stcg + p_ltcg
                running_cumulative += period_total
                grand_intra += p_intra
                grand_stcg += p_stcg
                grand_ltcg += p_ltcg
                
                period_data.append({
                    "period": p["label"],
                    "intraday": p_intra,
                    "stcg": p_stcg,
                    "ltcg": p_ltcg,
                    "total": period_total,
                    "cumulative": running_cumulative,
                    "adv_tax_due": p["adv_tax_due"]
                })
            
            grand_total_gain = grand_intra + grand_stcg + grand_ltcg
            period_summary = {
                "periods": period_data,
                "grand_total": {
                    "period": f"Grand total for 01-04-{start_yr} to 31-03-{end_yr}",
                    "intraday": grand_intra,
                    "stcg": grand_stcg,
                    "ltcg": grand_ltcg,
                    "total": grand_total_gain
                }
            }
        except Exception as e:
            print(f"Error computing quarterly/period summary: {e}")
            quarterly_summary = None
            period_summary = None

    # Apply quarter filter if specified
    selected_quarter = quarter.strip().upper() if quarter else ""
    if selected_quarter in ("Q1", "Q2", "Q3", "Q4") and start_yr and end_yr:
        q_map = {
            "Q1": (date(start_yr, 4, 1), date(start_yr, 6, 30)),
            "Q2": (date(start_yr, 7, 1), date(start_yr, 9, 30)),
            "Q3": (date(start_yr, 10, 1), date(start_yr, 12, 31)),
            "Q4": (date(end_yr, 1, 1), date(end_yr, 3, 31)),
        }
        q_start, q_end = q_map[selected_quarter]
        gains_q = []
        for g in gains:
            s_date = g.get("sale_date")
            if isinstance(s_date, str):
                try:
                    s_date = datetime.strptime(s_date, "%Y-%m-%d").date()
                except:
                    s_date = datetime.strptime(s_date, "%d-%m-%Y").date()
            if q_start <= s_date <= q_end:
                gains_q.append(g)
        gains = gains_q

    # ─── Compute Summary Metrics & Trade Statistics ───
    total_gain = 0.0
    stcg = 0.0
    ltcg = 0.0
    intraday = 0.0
    total_cost = 0.0
    total_sales = 0.0
    gross_profits = 0.0
    gross_losses = 0.0
    profit_trades = 0
    loss_trades = 0

    for g in gains:
        val = float(g.get("gain_loss", 0))
        c_val = float(g.get("cost_value", 0))
        s_val = float(g.get("sale_value", 0))
        
        total_gain += val
        total_cost += c_val
        total_sales += s_val
        
        if val >= 0:
            gross_profits += val
            profit_trades += 1
        else:
            gross_losses += val
            loss_trades += 1

        if g.get("trade_type") == "INTRADAY":
            intraday += val
        elif g.get("gain_type") == "LTCG":
            ltcg += val
        else:
            stcg += val

    total_trades = len(gains)
    win_rate = (profit_trades / total_trades * 100) if total_trades > 0 else 0.0
    profit_factor = (gross_profits / abs(gross_losses)) if gross_losses != 0 else (gross_profits if gross_profits > 0 else 0.0)
    return_on_cost = (total_gain / total_cost * 100) if total_cost > 0 else 0.0

    summary = {
        "total_gain": total_gain,
        "stcg": stcg,
        "ltcg": ltcg,
        "intraday": intraday,
        "total_cost": total_cost,
        "total_sales": total_sales,
        "gross_profits": gross_profits,
        "gross_losses": gross_losses,
        "total_trades": total_trades,
        "profit_trades": profit_trades,
        "loss_trades": loss_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "return_on_cost": return_on_cost
    }

    # ─── Tax Slab Bifurcation ───
    slab_map = {}
    for g in gains:
        label = g.get("tax_label", "Unknown")
        rate = g.get("tax_rate", "N/A")
        section = g.get("tax_section", "")
        asset_cls = g.get("asset_class", "EQUITY")
        
        # Combine non-intraday Slab Rate categories (Sec 50AA and Sec 112 STCG)
        if rate == "Slab Rate" and section != "Sec 43(5)":
            label = "Taxed at Slab Rate (Debt/Gold/Silver STCG)"
            section = "Sec 50AA / 112"
            rate = "Slab Rate"
        
        if label not in slab_map:
            slab_map[label] = {
                "label": label,
                "rate": rate,
                "section": section,
                "asset_class": asset_cls,
                "total_gain": 0.0,
                "total_cost": 0.0,
                "total_sales": 0.0,
                "profit_count": 0,
                "loss_count": 0,
                "total_profit": 0.0,
                "total_loss": 0.0
            }
        
        val = float(g.get("gain_loss", 0))
        c_val = float(g.get("cost_value", 0))
        s_val = float(g.get("sale_value", 0))
        
        slab_map[label]["total_gain"] += val
        slab_map[label]["total_cost"] += c_val
        slab_map[label]["total_sales"] += s_val
        
        if val >= 0:
            slab_map[label]["profit_count"] += 1
            slab_map[label]["total_profit"] += val
        else:
            slab_map[label]["loss_count"] += 1
            slab_map[label]["total_loss"] += val

    # Calculate percentages for slabs
    for slab in slab_map.values():
        t_cnt = slab["profit_count"] + slab["loss_count"]
        slab["win_rate"] = (slab["profit_count"] / t_cnt * 100) if t_cnt > 0 else 0.0
        slab["margin"] = (slab["total_gain"] / slab["total_cost"] * 100) if slab["total_cost"] > 0 else 0.0

    slab_order = ["Sec 50AA / 112", "Sec 111A", "Sec 43(5)", "Sec 112", "Sec 112A"]
    tax_slabs = sorted(slab_map.values(), key=lambda s: slab_order.index(s["section"]) if s["section"] in slab_order else 99)

    # ─── Entity / Person Breakdown ───
    person_map = {}
    for g in gains:
        owner = g.get("owner", "Unknown")
        if owner not in person_map:
            person_map[owner] = {
                "owner": owner,
                "person_id": g.get("person_id"),
                "trades": 0,
                "cost": 0.0,
                "sales": 0.0,
                "stcg_111a": 0.0,
                "stcg_slab": 0.0,
                "intraday": 0.0,
                "ltcg": 0.0,
                "total_gain": 0.0
            }
        
        val = float(g.get("gain_loss", 0))
        c_val = float(g.get("cost_value", 0))
        s_val = float(g.get("sale_value", 0))
        sec = g.get("tax_section", "")
        g_type = g.get("gain_type", "")
        t_type = g.get("trade_type", "")
        
        person_map[owner]["trades"] += 1
        person_map[owner]["cost"] += c_val
        person_map[owner]["sales"] += s_val
        person_map[owner]["total_gain"] += val
        
        if sec == "Sec 111A":
            person_map[owner]["stcg_111a"] += val
        elif sec in ("Sec 112", "Sec 50AA") and g_type == "STCG":
            person_map[owner]["stcg_slab"] += val
        elif t_type == "INTRADAY" or sec == "Sec 43(5)":
            person_map[owner]["intraday"] += val
        elif g_type == "LTCG":
            person_map[owner]["ltcg"] += val

    # Fetch PAN and person type
    for p_info in person_map.values():
        p_obj = next((p for p in persons if p.short_name == p_info["owner"] or p.id == p_info.get("person_id")), None)
        p_info["full_name"] = p_obj.full_name if p_obj else p_info["owner"]
        p_info["pan"] = getattr(p_obj, "pan", "N/A") if p_obj else "N/A"
        p_info["person_type"] = getattr(p_obj, "person_type", "INDIVIDUAL") if p_obj else "INDIVIDUAL"
        p_info["share_pct"] = (p_info["total_gain"] / total_gain * 100) if total_gain != 0 else 0.0

    entity_summary = sorted(person_map.values(), key=lambda x: x["total_gain"], reverse=True)

    # ─── Broker Breakdown ───
    broker_map = {}
    for g in gains:
        b_name = g.get("broker") or "Direct / Other"
        if b_name not in broker_map:
            broker_map[b_name] = {
                "broker": b_name,
                "trades": 0,
                "cost": 0.0,
                "sales": 0.0,
                "profits": 0.0,
                "losses": 0.0,
                "net_gain": 0.0,
                "profit_trades": 0,
                "loss_trades": 0
            }
        
        val = float(g.get("gain_loss", 0))
        c_val = float(g.get("cost_value", 0))
        s_val = float(g.get("sale_value", 0))
        
        broker_map[b_name]["trades"] += 1
        broker_map[b_name]["cost"] += c_val
        broker_map[b_name]["sales"] += s_val
        broker_map[b_name]["net_gain"] += val
        
        if val >= 0:
            broker_map[b_name]["profits"] += val
            broker_map[b_name]["profit_trades"] += 1
        else:
            broker_map[b_name]["losses"] += val
            broker_map[b_name]["loss_trades"] += 1

    for b in broker_map.values():
        t_cnt = b["trades"]
        b["win_rate"] = (b["profit_trades"] / t_cnt * 100) if t_cnt > 0 else 0.0
        b["profit_factor"] = (b["profits"] / abs(b["losses"])) if b["losses"] != 0 else (b["profits"] if b["profits"] > 0 else 0.0)

    broker_summary = sorted(broker_map.values(), key=lambda x: x["net_gain"], reverse=True)

    return templates.TemplateResponse(
        request=request,
        name="equities/pnl_report.html",
        context={
            "gains": gains,
            "summary": summary,
            "tax_slabs": tax_slabs,
            "persons": persons,
            "available_fys": available_fys,
            "brokers": brokers,
            "selected_fy": fy,
            "selected_quarter": selected_quarter,
            "selected_person": pid,
            "selected_asset_class": asset_class or "",
            "selected_broker": broker or "",
            "quarterly_summary": quarterly_summary,
            "period_summary": period_summary,
            "entity_summary": entity_summary,
            "broker_summary": broker_summary,
            "success_msg": success_msg,
            "file_validation": validate_all_equity_files()
        }
    )

@router.get("/transactions", response_class=HTMLResponse)
def get_transactions(
    request: Request, 
    db: Session = Depends(get_db), 
    symbol: str = None,
    person_id: str = None
):
    pid = None
    if person_id and person_id.strip():
        try:
            pid = int(person_id)
        except ValueError:
            pid = None

    query = db.query(
        EquityTransaction, 
        Person.short_name.label("owner_name"),
        Account.institution_name.label("broker_name")
    ).join(
        Person, Person.id == EquityTransaction.person_id
    ).join(
        Account, Account.id == EquityTransaction.account_id
    ).filter(
        EquityTransaction.status == TransactionStatus.ACTIVE
    )

    if symbol:
        query = query.filter(EquityTransaction.symbol.ilike(f"%{symbol.strip()}%"))
    if pid:
        query = query.filter(EquityTransaction.person_id == pid)

    txns = query.order_by(EquityTransaction.transaction_date.desc(), EquityTransaction.id.desc()).limit(200).all()
    persons = db.query(Person).order_by(Person.full_name).all()

    return templates.TemplateResponse(
        request=request,
        name="equities/transactions.html",
        context={
            "transactions": txns,
            "persons": persons,
            "selected_symbol": symbol,
            "selected_person": pid
        }
    )

@router.post("/sync-prices")
def sync_prices(db: Session = Depends(get_db)):
    update_equity_holdings_in_db(db)
    return RedirectResponse("/equities/?success_msg=Live+market+prices+successfully+synced!", status_code=303)

@router.get("/fetch-live-prices")
@router.post("/fetch-live-prices")
def fetch_live_prices(db: Session = Depends(get_db)):
    from urllib.parse import quote_plus
    try:
        from services.equity_price_fetcher import fetch_and_update_prices_from_fyers
        res = fetch_and_update_prices_from_fyers(db)
        msg = res.get("message", "Prices synced successfully!")
    except Exception as e:
        # Fallback to local price sync if Fyers API is unreachable
        try:
            update_equity_holdings_in_db(db)
            msg = f"Holdings refreshed with cached prices. (Fyers note: {str(e)})"
        except Exception as inner_e:
            msg = f"Error syncing prices: {str(e)}"
    return RedirectResponse(f"/equities/?success_msg={quote_plus(msg)}", status_code=303)

@router.post("/import")
def run_excel_import(db: Session = Depends(get_db)):
    res = run_equity_import(db)
    msg = f"Import complete. Imported: {res['records_imported']} rows, Skipped: {res['skipped_duplicates']} duplicates."
    return RedirectResponse(f"/equities/?success_msg={msg}", status_code=303)

@router.post("/import-global")
def run_global_excel_import(db: Session = Depends(get_db)):
    from services.equity_importer import run_global_equity_import
    from urllib.parse import quote_plus
    try:
        res = run_global_equity_import(db)
        msg = f"Global import complete! Imported: {res['records_imported']} rows, Skipped: {res['skipped_duplicates']} duplicates."
    except Exception as e:
        msg = f"Error importing global report: {str(e)}"
    return RedirectResponse(f"/equities/?success_msg={quote_plus(msg)}", status_code=303)



@router.get("/summary", response_class=HTMLResponse)
def get_summary(
    request: Request,
    db: Session = Depends(get_db),
    fy: str = None
):
    from services.equity_fifo_engine import get_financial_year as get_fy, classify_asset_class
    from sqlalchemy import distinct
    from collections import defaultdict

    # Available FYs
    distinct_years = db.query(
        distinct(func.extract('year', EquityTransaction.transaction_date))
    ).filter(EquityTransaction.status == TransactionStatus.ACTIVE).all()
    
    available_fys = set()
    for (yr,) in distinct_years:
        if yr:
            yr = int(yr)
            available_fys.add(f"FY{yr}-{str(yr+1)[2:]}")
            available_fys.add(f"FY{yr-1}-{str(yr)[2:]}")
    available_fys = sorted(available_fys, reverse=True)

    if not fy:
        fy = get_fy(datetime.today().date())

    # Calculate gains for this FY (all persons)
    results = calculate_equity_fifo_gains(db, financial_year=fy)
    gains = results["gains"]

    # Get distinct brokers from accounts linked to equity transactions
    broker_rows = db.query(
        distinct(Account.institution_name)
    ).join(
        EquityTransaction, EquityTransaction.account_id == Account.id
    ).order_by(Account.institution_name).all()
    brokers = [b[0] for b in broker_rows]

    # Get persons who have equity transactions
    person_rows = db.query(Person).join(
        EquityTransaction, EquityTransaction.person_id == Person.id
    ).distinct().order_by(Person.short_name).all()

    # Build account_id -> broker_name lookup
    account_broker_map = {}
    all_accounts = db.query(Account.id, Account.institution_name).all()
    for aid, iname in all_accounts:
        account_broker_map[aid] = iname

    # Build pivot: person_short_name -> gain_type(ST/LT) -> broker -> asset_bucket(EQUITY/DEBT) -> total
    # Asset bucket: EQUITY = listed equity + index ETFs, DEBT = gold/silver/debt/liquid ETFs (slab rate items)
    pivot = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float))))

    for g in gains:
        owner = g["owner"]
        broker = account_broker_map.get(g["account_id"], "Unknown")
        gain_type_label = "LT" if g["gain_type"] == "LTCG" else "ST"
        
        # If intraday, classify as ST
        if g["trade_type"] == "INTRADAY":
            gain_type_label = "ST"

        asset_class = g.get("asset_class", "EQUITY")
        if asset_class in ("GOLD_ETF", "SILVER_ETF", "DEBT_ETF"):
            asset_bucket = "DEBT"
        else:
            asset_bucket = "EQUITY"

        pivot[owner][gain_type_label][broker][asset_bucket] += float(g["gain_loss"])

    # Build structured data for template
    persons_data = []
    grand_totals = {"ST": defaultdict(lambda: defaultdict(float)), "LT": defaultdict(lambda: defaultdict(float))}
    
    for person in person_rows:
        person_entry = {
            "name": person.short_name,
            "rows": {}
        }
        for gt in ["ST", "LT"]:
            row_data = {}
            for broker in brokers:
                eq_val = pivot[person.short_name][gt][broker].get("EQUITY", 0.0)
                debt_val = pivot[person.short_name][gt][broker].get("DEBT", 0.0)
                row_data[broker] = {"EQUITY": eq_val, "DEBT": debt_val}
                grand_totals[gt][broker]["EQUITY"] += eq_val
                grand_totals[gt][broker]["DEBT"] += debt_val
            
            # Compute person-level totals
            total_eq = sum(row_data[b]["EQUITY"] for b in brokers)
            total_debt = sum(row_data[b]["DEBT"] for b in brokers)
            row_data["_TOTAL"] = {"EQUITY": total_eq, "DEBT": total_debt}
            
            person_entry["rows"][gt] = row_data
        persons_data.append(person_entry)

    # Grand total row
    grand_total_row = {}
    for gt in ["ST", "LT"]:
        gt_data = {}
        for broker in brokers:
            gt_data[broker] = {
                "EQUITY": grand_totals[gt][broker]["EQUITY"],
                "DEBT": grand_totals[gt][broker]["DEBT"]
            }
        total_eq = sum(gt_data[b]["EQUITY"] for b in brokers)
        total_debt = sum(gt_data[b]["DEBT"] for b in brokers)
        gt_data["_TOTAL"] = {"EQUITY": total_eq, "DEBT": total_debt}
        grand_total_row[gt] = gt_data

    return templates.TemplateResponse(
        request=request,
        name="equities/summary.html",
        context={
            "brokers": brokers,
            "persons_data": persons_data,
            "grand_total_row": grand_total_row,
            "available_fys": available_fys,
            "selected_fy": fy
        }
    )

@router.get("/unmatched", response_class=HTMLResponse)
def get_unmatched_trades(
    request: Request,
    db: Session = Depends(get_db)
):
    from models.equities import EquityRealizedGain, EquityTransaction
    from models.accounts import Account
    from models.persons import Person
    
    # 1. Fetch all unmatched sales
    unmatched_gains = db.query(EquityRealizedGain).filter(
        EquityRealizedGain.purchase_txn_id == None
    ).order_by(EquityRealizedGain.sale_date.desc()).all()
    
    # Enrich unmatched gains with account details
    unmatched_list = []
    for g in unmatched_gains:
        account = db.query(Account).filter_by(id=g.account_id).first()
        owner_name = "Unknown"
        if account:
            owner = db.query(Person).filter_by(id=account.person_id).first()
            if owner:
                owner_name = owner.short_name
        
        unmatched_list.append({
            "gain_record": g,
            "owner": owner_name,
            "broker": account.institution_name if account else "Unknown",
            "account_name": f"{owner_name} - {account.institution_name}" if account else "Unknown"
        })
        
    # 2. Fetch all manually added BUY transactions
    manual_buys = db.query(EquityTransaction).filter(
        EquityTransaction.notes.like("Manually matched buy%")
    ).order_by(EquityTransaction.transaction_date.desc()).all()
    
    manual_list = []
    for m in manual_buys:
        account = db.query(Account).filter_by(id=m.account_id).first()
        owner_name = "Unknown"
        if account:
            owner = db.query(Person).filter_by(id=account.person_id).first()
            if owner:
                owner_name = owner.short_name
                
        manual_list.append({
            "txn": m,
            "account_name": f"{owner_name} - {account.institution_name}" if account else "Unknown"
        })
        
    return templates.TemplateResponse(
        request=request,
        name="equities/unmatched.html",
        context={
            "unmatched": unmatched_list,
            "manual_buys": manual_list
        }
    )

@router.post("/unmatched/{gain_id}/fix")
def fix_unmatched_trade(
    gain_id: int,
    buy_date: str = Form(...),
    buy_rate: float = Form(...),
    db: Session = Depends(get_db)
):
    from models.equities import EquityRealizedGain, EquityTransaction
    from models.accounts import Account
    from services.equity_fifo_engine import recalculate_and_cache_equity_fifo
    from datetime import datetime
    
    gain = db.query(EquityRealizedGain).filter_by(id=gain_id).first()
    if not gain:
        from urllib.parse import quote_plus
        return RedirectResponse(f"/equities/unmatched?success_msg={quote_plus('Realized gain record not found')}", status_code=303)
        
    account = db.query(Account).filter_by(id=gain.account_id).first()
    if not account:
        from urllib.parse import quote_plus
        return RedirectResponse(f"/equities/unmatched?success_msg={quote_plus('Account not found for realized gain')}", status_code=303)
        
    try:
        parsed_buy_date = datetime.strptime(buy_date, "%Y-%m-%d").date()
    except Exception:
        from urllib.parse import quote_plus
        return RedirectResponse(f"/equities/unmatched?success_msg={quote_plus('Invalid date format')}", status_code=303)
        
    # Create the new dummy BUY transaction
    new_txn = EquityTransaction(
        account_id=gain.account_id,
        person_id=account.person_id,
        symbol=gain.symbol,
        transaction_date=parsed_buy_date,
        transaction_type="BUY",
        quantity=gain.units_sold,
        price=buy_rate,
        net_amount=float(gain.units_sold) * buy_rate,
        brokerage=0.0,
        status="ACTIVE",
        notes=f"Manually matched buy for sale on {gain.sale_date}",
        import_batch_id=None
    )
    db.add(new_txn)
    db.commit()
    
    # Recalculate FIFO engine
    recalculate_and_cache_equity_fifo(db)
    
    from urllib.parse import quote_plus
    msg = f"Successfully matched buy for {gain.symbol} ({gain.units_sold} qty at ₹{buy_rate:,.2f})!"
    return RedirectResponse(f"/equities/unmatched?success_msg={quote_plus(msg)}", status_code=303)

@router.post("/unmatched/delete-manual/{txn_id}")
def delete_manual_buy(
    txn_id: int,
    db: Session = Depends(get_db)
):
    from models.equities import EquityTransaction, EquityRealizedGain, EquityRemainingLot
    from services.equity_fifo_engine import recalculate_and_cache_equity_fifo
    
    txn = db.query(EquityTransaction).filter_by(id=txn_id).first()
    if txn:
        # Clear realized gains and remaining lots tables first
        db.query(EquityRealizedGain).delete()
        db.query(EquityRemainingLot).delete()
        
        db.delete(txn)
        db.commit()
        
        # Recalculate FIFO
        recalculate_and_cache_equity_fifo(db)
        msg = "Manually added buy transaction deleted successfully."
    else:
        msg = "Transaction not found."
        
    from urllib.parse import quote_plus
    return RedirectResponse(f"/equities/unmatched?success_msg={quote_plus(msg)}", status_code=303)

@router.get("/performance", response_class=HTMLResponse)
def get_stocks_performance_ledger(
    request: Request,
    db: Session = Depends(get_db)
):
    from services.stocks_performance_engine import generate_stocks_performance_history
    history = generate_stocks_performance_history()
    return templates.TemplateResponse(
        request=request,
        name="equities/performance.html",
        context={
            "history": history
        }
    )

@router.get("/recalculate")
@router.post("/recalculate")
def recalculate_equities(
    request: Request,
    db: Session = Depends(get_db),
    fy: str = None,
    quarter: str = None,
    person_id: str = None,
    broker: str = None,
    asset_class: str = None,
    redirect_to: str = "pnl"
):
    from urllib.parse import urlencode
    
    # 1. Run Excel directory import (scans DATA/DATA/ for CBL, JM, NUV)
    res = run_equity_import(db)
    
    files = res.get("files_processed", 0)
    imported = res.get("records_imported", 0)
    skipped = res.get("skipped_duplicates", 0)
    
    msg = f"Recalculation Complete! Scanned {files} files ({imported:,} new trades imported, {skipped:,} existing verified). FIFO gains and holdings refreshed."
    
    # Prepare redirect query parameters
    params = {}
    if fy and fy.strip():
        params["fy"] = fy.strip()
    if quarter and quarter.strip():
        params["quarter"] = quarter.strip()
    if person_id and person_id.strip():
        params["person_id"] = person_id.strip()
    if broker and broker.strip():
        params["broker"] = broker.strip()
    if asset_class and asset_class.strip():
        params["asset_class"] = asset_class.strip()
    params["success_msg"] = msg
    
    target_base = "/equities/pnl" if redirect_to == "pnl" else "/equities/"
    redirect_url = f"{target_base}?{urlencode(params)}"
    return RedirectResponse(redirect_url, status_code=303)

@router.get("/diagnostics", response_class=HTMLResponse)
def get_file_diagnostics(
    request: Request,
    db: Session = Depends(get_db)
):
    from services.equity_validator import validate_all_equity_files
    file_validation = validate_all_equity_files()
    return templates.TemplateResponse(
        request=request,
        name="equities/diagnostics.html",
        context={
            "file_validation": file_validation
        }
    )



