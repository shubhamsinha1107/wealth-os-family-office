import os
from datetime import date, datetime
from typing import Dict, List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session
from models.equities import EquityRealizedGain
from models.persons import Person
from models.mutual_funds import MFTransaction
from models.market_data import MFNav
from models.enums import TransactionStatus
from services.equity_fifo_engine import get_financial_year
from services.fifo_engine import calculate_fifo_gains
from services.consolidated_portfolio_engine import (
    get_latest_global_positions_file, parse_global_positions_report,
    parse_unlisted_shares_excel, parse_aif_excel, get_purchase_lots_remaining_units
)


def format_inr_crore(val: float) -> str:
    """Formats float value into Indian Cr / L string format with sign."""
    if abs(val) < 0.01:
        return "₹0.00"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= 1e7:
        return f"{sign}₹{abs_val / 1e7:.2f} Cr"
    elif abs_val >= 1e5:
        return f"{sign}₹{abs_val / 1e5:.2f} L"
    else:
        return f"{sign}₹{abs_val:,.2f}"


def get_account_portfolio_snapshots(db: Session, codes: List[str]):
    """
    Computes current Invested Capital (Net Cost Basis) and Current Market Value (AUM)
    for each member account across SB Equity, SGB (Sovereign Gold Bonds), Mutual Funds,
    Unlisted Shares, and AIF.
    Also returns available Cash / Dry Powder in Liquid and Arbitrage funds.
    """
    snapshots = {
        c: {
            "sb_equity_cost": 0.0,
            "sb_equity_curr": 0.0,
            "sgb_cost": 0.0,
            "sgb_curr": 0.0,
            "mf_cost": 0.0,
            "mf_curr": 0.0,
            "unlisted_cost": 0.0,
            "unlisted_curr": 0.0,
            "aif_cost": 0.0,
            "aif_curr": 0.0,
        }
        for c in codes
    }
    dry_powder = 0.0

    # 1. Stocks, Bullion & SGB from Latest Global Positions Report
    latest_file = get_latest_global_positions_file()
    if latest_file:
        try:
            df = parse_global_positions_report(latest_file)
            for _, row in df.iterrows():
                pname = str(row.get("Portfolio Name", "")).upper()
                code = None
                if "APEX" in pname: code = "APEX"
                elif "PATRIARCH" in pname: code = "PATRIARCH"
                elif "MR" in pname: code = "MR"
                elif "VG" in pname: code = "VG"
                elif "SR" in pname: code = "SR"
                elif "QUEENS" in pname or "NEXUS" in pname: code = "NEXUS"

                if code in snapshots:
                    cost = float(row.get("Amount Invested", 0.0))
                    curr = float(row.get("Current Value", 0.0))
                    asset_name = str(row.get("Asset Name", "")).upper()
                    is_sgb = "SOVEREIGN" in asset_name or "SGB" in asset_name

                    if is_sgb:
                        # Skip SGB from MProfit workbook; real purchase cost & live valuations come directly from Gold module
                        continue
                    else:
                        snapshots[code]["sb_equity_cost"] += cost
                        snapshots[code]["sb_equity_curr"] += curr
        except Exception as e:
            print(f"Error parsing global positions for snapshots: {e}")

    # 1b. Sovereign Gold Bonds (SGB) from Gold Module (/gold/)
    try:
        from routers.gold import get_flat_holdings_list
        sgb_holdings = get_flat_holdings_list(db)
        for h in sgb_holdings:
            p_code = h.get("owner")
            if p_code in snapshots:
                snapshots[p_code]["sgb_cost"] += float(h.get("total_cost", 0.0))
                snapshots[p_code]["sgb_curr"] += float(h.get("current_value", 0.0))
    except Exception as e:
        print(f"Error fetching SGB holdings from gold router: {e}")

    # 2. Mutual Funds from DB (remaining lots * nav / latest nav)
    from models.mutual_funds import MFDetail
    details_rows = db.query(MFDetail).all()
    details_map = {d.scheme_name.upper(): (d.fund_type or "").upper().strip() for d in details_rows}

    person_id_map = {p.id: p.short_name for p in db.query(Person).all()}
    rem_units = get_purchase_lots_remaining_units(db)
    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT']
    txns = db.query(MFTransaction).filter(
        MFTransaction.status == TransactionStatus.ACTIVE,
        MFTransaction.transaction_type.in_(purchase_types)
    ).all()

    amfi_codes = list({str(t.amfi_code).strip().split('.')[0] for t in txns if t.amfi_code})
    latest_navs = {}
    if amfi_codes:
        latest_dates = db.query(
            MFNav.amfi_code,
            func.max(MFNav.nav_date).label('max_date')
        ).filter(MFNav.amfi_code.in_(amfi_codes)).group_by(MFNav.amfi_code).subquery()
        nav_rows = db.query(MFNav.amfi_code, MFNav.nav).join(
            latest_dates,
            (MFNav.amfi_code == latest_dates.c.amfi_code) & (MFNav.nav_date == latest_dates.c.max_date)
        ).all()
        latest_navs = {str(r.amfi_code).strip().split('.')[0]: float(r.nav) for r in nav_rows if r.nav}

    for t in txns:
        rem = rem_units.get(t.id, 0.0)
        if rem > 0.001:
            nav = float(t.nav or 0.0)
            code_clean = str(t.amfi_code).strip().split('.')[0] if t.amfi_code else ''
            cur_nav = latest_navs.get(code_clean, nav)
            cost = rem * nav
            curr = rem * cur_nav

            f_type = details_map.get(t.scheme_name.upper(), "")
            if f_type in ["LIQUID", "ARBITRAGE"]:
                dry_powder += curr

            code = person_id_map.get(t.person_id)
            if code in snapshots:
                snapshots[code]["mf_cost"] += cost
                snapshots[code]["mf_curr"] += curr

    # 3. Unlisted Shares & AIF
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    unlisted_file = os.path.join(base_dir, "DATA", "UNLISTED SHARES.xlsx")
    for u in parse_unlisted_shares_excel(unlisted_file):
        code = u.get("owner")
        if code in snapshots:
            snapshots[code]["unlisted_cost"] += float(u.get("cost", 0.0))
            snapshots[code]["unlisted_curr"] += float(u.get("current_value", 0.0))

    aif_file = os.path.join(base_dir, "DATA", "AIF DATA.xlsx")
    for a in parse_aif_excel(aif_file):
        code = a.get("owner")
        if code in snapshots:
            snapshots[code]["aif_cost"] += float(a.get("cost", 0.0))
            snapshots[code]["aif_curr"] += float(a.get("current_value", 0.0))

    return snapshots, dry_powder


def get_booked_profits_matrix(
    db: Session,
    from_date: Optional[date] = None,
    till_date: Optional[date] = None,
    fys: Optional[List[str]] = None
) -> dict:
    """
    Computes realized capital gains (booked profits) across Stock Brokers and Mutual Funds
    grouped by Family Member / Account and Financial Year.
    
    Supports 'from_date' and 'till_date' to filter trades booked within a specific timeframe,
    and dynamically adjusts financial year columns.
    """
    if fys is None:
        all_fys = [
            ("FY2024-25", date(2024, 4, 1), date(2025, 3, 31)),
            ("FY2025-26", date(2025, 4, 1), date(2026, 3, 31)),
            ("FY2026-27", date(2026, 4, 1), date(2027, 3, 31)),
        ]
        f_start = from_date if from_date else date(2024, 4, 1)
        f_end = till_date if till_date else date(2027, 3, 31)

        active_fys = [
            fy_name for fy_name, s_date, e_date in all_fys
            if s_date <= f_end and e_date >= f_start
        ]
        fys = active_fys if active_fys else ["FY2026-27"]

    fy_short_map = {
        "FY2024-25": "FY25",
        "FY2025-26": "FY26",
        "FY2026-27": "FY27",
        "FY2023-24": "FY24",
        "FY2022-23": "FY23",
    }

    # Primary member codes requested by user
    primary_members = ["APEX", "PATRIARCH", "MR", "VG", "SR", "NEXUS"]

    # Fetch all persons
    persons = db.query(Person).all()
    id_to_person = {p.id: p for p in persons}
    code_to_name = {p.short_name: p.full_name for p in persons}

    # 1. Fetch Stock Broker Realized Gains (Aggregated in SQL for 20x speedup)
    eq_query = db.query(
        EquityRealizedGain.person_id,
        EquityRealizedGain.financial_year,
        func.sum(EquityRealizedGain.gain_loss)
    )
    if from_date:
        eq_query = eq_query.filter(EquityRealizedGain.sale_date >= from_date)
    if till_date:
        eq_query = eq_query.filter(EquityRealizedGain.sale_date <= till_date)
    eq_gains = eq_query.group_by(EquityRealizedGain.person_id, EquityRealizedGain.financial_year).all()

    stock_data: Dict[str, Dict[str, float]] = {}
    for person_id, fy, sum_gain in eq_gains:
        p = id_to_person.get(person_id)
        if not p:
            continue
        p_code = p.short_name
        stock_data.setdefault(p_code, {}).setdefault(fy, 0.0)
        stock_data[p_code][fy] += float(sum_gain or 0.0)

    # 2. Fetch Mutual Fund Realized Gains
    mf_gains = calculate_fifo_gains(db, from_date=from_date, till_date=till_date)
    mf_data: Dict[str, Dict[str, float]] = {}
    for g in mf_gains:
        p_code = g.get("owner")
        if not p_code:
            continue
        s_date = g.get("sale_date")
        fy = get_financial_year(s_date)
        mf_data.setdefault(p_code, {}).setdefault(fy, 0.0)
        mf_data[p_code][fy] += float(g.get("gain_loss", 0.0))

    # Process primary members only (APEX, PATRIARCH, MR, VG, SR, NEXUS)
    members_to_process = primary_members
    
    # Fetch current Portfolio Snapshots (Invested Capital and Current AUM) and Dry Powder
    snapshots, dry_powder = get_account_portfolio_snapshots(db, primary_members)

    t0 = from_date if from_date else date(2024, 4, 1)
    end_calc_date = till_date if till_date else date.today()
    elapsed_years = max(0.08, (end_calc_date - t0).days / 365.25)

    rows = []
    grand_totals = {
        fy: {"stock": 0.0, "mf": 0.0, "total": 0.0}
        for fy in fys
    }
    grand_cum = {"stock": 0.0, "mf": 0.0, "total": 0.0}

    for m in members_to_process:
        row_data = {
            "code": m,
            "name": code_to_name.get(m, m),
            "is_primary": True,
            "fys": {},
            "total_stock": 0.0,
            "total_mf": 0.0,
            "total_gain": 0.0,
        }

        for fy in fys:
            s_val = stock_data.get(m, {}).get(fy, 0.0)
            m_val = mf_data.get(m, {}).get(fy, 0.0)
            tot = s_val + m_val

            row_data["fys"][fy] = {
                "stock": s_val,
                "mf": m_val,
                "total": tot,
                "stock_fmt": format_inr_crore(s_val),
                "mf_fmt": format_inr_crore(m_val),
                "total_fmt": format_inr_crore(tot),
            }

            row_data["total_stock"] += s_val
            row_data["total_mf"] += m_val
            row_data["total_gain"] += tot

            grand_totals[fy]["stock"] += s_val
            grand_totals[fy]["mf"] += m_val
            grand_totals[fy]["total"] += tot

        row_data["total_stock_fmt"] = format_inr_crore(row_data["total_stock"])
        row_data["total_mf_fmt"] = format_inr_crore(row_data["total_mf"])
        row_data["total_gain_fmt"] = format_inr_crore(row_data["total_gain"])

        # Portfolio Snapshot with PnL-derived Invested Capital & explicit SGB
        snap = snapshots.get(m, {})
        sb_curr = snap.get("sb_equity_curr", 0.0)
        sb_cost = snap.get("sb_equity_cost", 0.0)
        sgb_curr = snap.get("sgb_curr", 0.0)
        sgb_cost = snap.get("sgb_cost", 0.0)
        mf_curr = snap.get("mf_curr", 0.0)
        mf_cost = snap.get("mf_cost", 0.0)
        unlisted_curr = snap.get("unlisted_curr", 0.0)
        unlisted_cost = snap.get("unlisted_cost", 0.0)
        aif_curr = snap.get("aif_curr", 0.0)
        aif_cost = snap.get("aif_cost", 0.0)

        total_current = sb_curr + sgb_curr + mf_curr + unlisted_curr + aif_curr

        # Derived Invested Capital based on PnL
        s_gain = row_data["total_stock"]
        m_gain = row_data["total_mf"]
        tot_gain = row_data["total_gain"]

        # Derived Invested Capital based on PnL: Cost Basis minus Realized Profits
        # This guarantees: Total Diff (AUM - Invested) == Open Book Unrealized + Reinvested Profit
        sb_equity_inv = max(0.0, sb_cost - s_gain)
        sgb_inv = sgb_cost  # Real SGB purchase cost from /gold/
        mf_inv = mf_cost    # Real purchase cost of active mutual fund units from DB
        unlisted_inv = unlisted_cost
        aif_inv = aif_cost
        total_invested = sb_equity_inv + sgb_inv + mf_inv + unlisted_inv + aif_inv

        # True Open Book Paper Gains (Unrealized)
        sb_unrealized = sb_curr - sb_cost
        sgb_unrealized = sgb_curr - sgb_cost
        mf_unrealized = mf_curr - mf_cost
        unlisted_unrealized = unlisted_curr - unlisted_cost
        aif_unrealized = aif_curr - aif_cost
        open_unrealized = sb_unrealized + sgb_unrealized + mf_unrealized + unlisted_unrealized + aif_unrealized

        # Reinvested Booked Profit (Stock profits working inside brokerage accounts as active capital)
        reinvested_profit = s_gain

        # Total Surplus / Diff between AUM and Invested Capital
        aum_diff = total_current - total_invested

        # Total Wealth Created (Booked Gains + Open Book Unrealized)
        row_total_wealth = tot_gain + open_unrealized

        has_sgb = sgb_curr > 0.001 or sgb_inv > 0.001
        if has_sgb:
            inv_subtext = f"SB: {format_inr_crore(sb_equity_inv)} • SGB: {format_inr_crore(sgb_inv)} • MF: {format_inr_crore(mf_inv)}"
            curr_subtext = f"SB: {format_inr_crore(sb_curr)} • SGB: {format_inr_crore(sgb_curr)} • MF: {format_inr_crore(mf_curr)}"
        else:
            inv_subtext = f"SB: {format_inr_crore(sb_equity_inv)} • MF: {format_inr_crore(mf_inv)}"
            curr_subtext = f"SB: {format_inr_crore(sb_curr)} • MF: {format_inr_crore(mf_curr)}"

        row_data["snapshot"] = {
            "invested": total_invested,
            "current": total_current,
            "invested_fmt": format_inr_crore(total_invested),
            "current_fmt": format_inr_crore(total_current),
            "invested_subtext": inv_subtext,
            "current_subtext": curr_subtext,
            "sb_equity_inv": sb_equity_inv,
            "sb_equity_inv_fmt": format_inr_crore(sb_equity_inv),
            "sgb_inv": sgb_inv,
            "sgb_inv_fmt": format_inr_crore(sgb_inv),
            "mf_inv": mf_inv,
            "mf_inv_fmt": format_inr_crore(mf_inv),
            "sb_equity_curr": sb_curr,
            "sb_equity_curr_fmt": format_inr_crore(sb_curr),
            "sgb_curr": sgb_curr,
            "sgb_curr_fmt": format_inr_crore(sgb_curr),
            "mf_curr": mf_curr,
            "mf_curr_fmt": format_inr_crore(mf_curr),
            "has_sgb": has_sgb,
            "open_unrealized": open_unrealized,
            "open_unrealized_fmt": format_inr_crore(open_unrealized),
            "sb_unrealized": sb_unrealized,
            "sb_unrealized_fmt": format_inr_crore(sb_unrealized),
            "sgb_unrealized": sgb_unrealized,
            "sgb_unrealized_fmt": format_inr_crore(sgb_unrealized),
            "mf_unrealized": mf_unrealized,
            "mf_unrealized_fmt": format_inr_crore(mf_unrealized),
            "reinvested_profit": reinvested_profit,
            "reinvested_profit_fmt": format_inr_crore(reinvested_profit),
            "aum_diff": aum_diff,
            "aum_diff_fmt": format_inr_crore(aum_diff),
            "total_wealth": row_total_wealth,
            "total_wealth_fmt": format_inr_crore(row_total_wealth),
        }

        # Calculate Realized ROI (Booked Profits on active trading capital)
        active_cap = (sb_equity_inv + mf_inv) or total_invested
        gain = row_data["total_gain"]
        if active_cap > 0 and (1.0 + (gain / active_cap)) > 0:
            row_data["realized_xirr"] = round(((1.0 + (gain / active_cap)) ** (1.0 / elapsed_years) - 1.0) * 100.0, 1)
        elif active_cap > 0 and gain < 0:
            row_data["realized_xirr"] = round(-((1.0 + (abs(gain) / active_cap)) ** (1.0 / elapsed_years) - 1.0) * 100.0, 1)
        else:
            row_data["realized_xirr"] = 0.0

        # Total AUM Growth (Compounded CAGR including Current Value & SGB)
        if total_invested > 0 and (total_current / total_invested) > 0:
            row_data["total_xirr"] = round(((total_current / total_invested) ** (1.0 / elapsed_years) - 1.0) * 100.0, 1)
        else:
            row_data["total_xirr"] = row_data["realized_xirr"]

        row_data["xirr"] = row_data["total_xirr"]

        grand_cum["stock"] += row_data["total_stock"]
        grand_cum["mf"] += row_data["total_mf"]
        grand_cum["total"] += row_data["total_gain"]

        rows.append(row_data)

    # Format grand totals
    formatted_grand_totals = {}
    for fy in fys:
        gt = grand_totals[fy]
        formatted_grand_totals[fy] = {
            "stock": gt["stock"],
            "mf": gt["mf"],
            "total": gt["total"],
            "stock_fmt": format_inr_crore(gt["stock"]),
            "mf_fmt": format_inr_crore(gt["mf"]),
            "total_fmt": format_inr_crore(gt["total"]),
        }

    formatted_grand_cum = {
        "stock": grand_cum["stock"],
        "mf": grand_cum["mf"],
        "total": grand_cum["total"],
        "stock_fmt": format_inr_crore(grand_cum["stock"]),
        "mf_fmt": format_inr_crore(grand_cum["mf"]),
        "total_fmt": format_inr_crore(grand_cum["total"]),
    }

    grand_curr = sum(r["snapshot"]["current"] for r in rows)
    grand_inv = sum(r["snapshot"]["invested"] for r in rows)
    grand_sb_inv = sum(r["snapshot"]["sb_equity_inv"] for r in rows)
    grand_sgb_inv = sum(r["snapshot"]["sgb_inv"] for r in rows)
    grand_mf_inv = sum(r["snapshot"]["mf_inv"] for r in rows)
    grand_sb_curr = sum(r["snapshot"]["sb_equity_curr"] for r in rows)
    grand_sgb_curr = sum(r["snapshot"]["sgb_curr"] for r in rows)
    grand_mf_curr = sum(r["snapshot"]["mf_curr"] for r in rows)
    grand_open_unrealized = sum(r["snapshot"]["open_unrealized"] for r in rows)
    grand_reinvested = sum(r["snapshot"]["reinvested_profit"] for r in rows)
    grand_aum_diff = grand_curr - grand_inv
    grand_total_wealth = grand_cum["total"] + grand_open_unrealized

    grand_snapshot = {
        "invested": grand_inv,
        "current": grand_curr,
        "invested_fmt": format_inr_crore(grand_inv),
        "current_fmt": format_inr_crore(grand_curr),
        "sb_equity_inv": grand_sb_inv,
        "sb_equity_inv_fmt": format_inr_crore(grand_sb_inv),
        "sgb_inv": grand_sgb_inv,
        "sgb_inv_fmt": format_inr_crore(grand_sgb_inv),
        "mf_inv": grand_mf_inv,
        "mf_inv_fmt": format_inr_crore(grand_mf_inv),
        "sb_equity_curr": grand_sb_curr,
        "sb_equity_curr_fmt": format_inr_crore(grand_sb_curr),
        "sgb_curr": grand_sgb_curr,
        "sgb_curr_fmt": format_inr_crore(grand_sgb_curr),
        "mf_curr": grand_mf_curr,
        "mf_curr_fmt": format_inr_crore(grand_mf_curr),
        "invested_subtext": f"SB: {format_inr_crore(grand_sb_inv)} • SGB: {format_inr_crore(grand_sgb_inv)} • MF: {format_inr_crore(grand_mf_inv)}",
        "current_subtext": f"SB: {format_inr_crore(grand_sb_curr)} • SGB: {format_inr_crore(grand_sgb_curr)} • MF: {format_inr_crore(grand_mf_curr)}",
        "open_unrealized": grand_open_unrealized,
        "open_unrealized_fmt": format_inr_crore(grand_open_unrealized),
        "reinvested_profit": grand_reinvested,
        "reinvested_profit_fmt": format_inr_crore(grand_reinvested),
        "aum_diff": grand_aum_diff,
        "aum_diff_fmt": format_inr_crore(grand_aum_diff),
        "total_wealth": grand_total_wealth,
        "total_wealth_fmt": format_inr_crore(grand_total_wealth),
    }

    # Grand total Realized XIRR across active trading capital
    tot_active_cap = sum(
        (r["snapshot"]["sb_equity_inv"] + r["snapshot"]["mf_inv"]) or r["snapshot"]["invested"]
        for r in rows
    )
    if tot_active_cap > 0 and (1.0 + (grand_cum["total"] / tot_active_cap)) > 0:
        grand_realized_xirr = round(((1.0 + (grand_cum["total"] / tot_active_cap)) ** (1.0 / elapsed_years) - 1.0) * 100.0, 1)
    else:
        grand_realized_xirr = 0.0

    # Grand total Portfolio CAGR / Total AUM Growth (including Current Value & SGB)
    if grand_inv > 0 and (grand_curr / grand_inv) > 0:
        grand_total_xirr = round(((grand_curr / grand_inv) ** (1.0 / elapsed_years) - 1.0) * 100.0, 1)
    else:
        grand_total_xirr = grand_realized_xirr

    grand_xirr = grand_total_xirr

    # Realized vs Unrealized Breakdown for Tax Planning
    realized_profit = grand_cum["total"]
    tot_sb_cost = sum(snapshots[m].get("sb_equity_cost", 0.0) for m in primary_members)
    tot_sb_curr = grand_sb_curr
    sb_unrealized = tot_sb_curr - tot_sb_cost

    tot_mf_cost = sum(snapshots[m].get("mf_cost", 0.0) for m in primary_members)
    tot_mf_curr = grand_mf_curr
    mf_unrealized = tot_mf_curr - tot_mf_cost

    tot_sgb_cost = sum(snapshots[m].get("sgb_cost", 0.0) for m in primary_members)
    tot_sgb_curr = grand_sgb_curr
    sgb_unrealized = tot_sgb_curr - tot_sgb_cost

    unrealized_profit = sb_unrealized + mf_unrealized + sgb_unrealized
    total_wealth = realized_profit + unrealized_profit

    year_headers = [
        {"key": fy, "short_label": fy_short_map.get(fy, fy), "full_label": fy}
        for fy in fys
    ]

    return {
        "years": year_headers,
        "rows": rows,
        "grand_snapshot": grand_snapshot,
        "grand_totals": formatted_grand_totals,
        "grand_cum": formatted_grand_cum,
        "grand_xirr": grand_xirr,
        "grand_total_xirr": grand_total_xirr,
        "grand_realized_xirr": grand_realized_xirr,
        "dry_powder": dry_powder,
        "dry_powder_fmt": format_inr_crore(dry_powder),
        "realized_profit": realized_profit,
        "realized_profit_fmt": format_inr_crore(realized_profit),
        "unrealized_profit": unrealized_profit,
        "unrealized_profit_fmt": format_inr_crore(unrealized_profit),
        "sb_unrealized": sb_unrealized,
        "sb_unrealized_fmt": format_inr_crore(sb_unrealized),
        "sgb_unrealized": sgb_unrealized,
        "sgb_unrealized_fmt": format_inr_crore(sgb_unrealized),
        "mf_unrealized": mf_unrealized,
        "mf_unrealized_fmt": format_inr_crore(mf_unrealized),
        "total_wealth": total_wealth,
        "total_wealth_fmt": format_inr_crore(total_wealth),
        "from_date": from_date.strftime("%Y-%m-%d") if from_date else None,
        "from_date_display": from_date.strftime("%d-%b-%Y") if from_date else "01-Apr-2024",
        "till_date": till_date.strftime("%Y-%m-%d") if till_date else None,
        "till_date_display": till_date.strftime("%d-%b-%Y") if till_date else "Today",
        "is_filtered": bool(from_date or till_date),
    }
