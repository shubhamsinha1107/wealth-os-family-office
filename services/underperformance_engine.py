# services/underperformance_engine.py
import re
from datetime import datetime, date, timedelta
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from models.mutual_funds import MFHolding, MFTransaction, MFDetail
from models.market_data import MFNav
from services.fifo_engine import get_fund_type

RISK_FREE_RATE = 0.065  # 6.5% per annum (RBI T-Bill benchmark)


def calculate_fund_horizon_metrics(nav_series: list[tuple[date, float]], horizon_days: int, eval_end_date: date = None) -> dict | None:
    """
    Computes risk & return metrics for a specific horizon (e.g. 180, 365, 730, 1095 days)
    ending on or before eval_end_date.
    Returns None if the fund's NAV history is shorter than 80% of horizon_days.
    """
    if not nav_series or len(nav_series) < 10:
        return None

    # Filter NAV series up to eval_end_date if specified
    if eval_end_date:
        nav_series = [x for x in nav_series if x[0] <= eval_end_date]

    if not nav_series or len(nav_series) < 10:
        return None

    # Sort chronologically
    nav_series = sorted(nav_series, key=lambda x: x[0])
    dates = [x[0] for x in nav_series]
    navs = np.array([x[1] for x in nav_series], dtype=float)

    if navs[0] <= 0 or navs[-1] <= 0:
        return None

    end_date = dates[-1]
    target_start_date = end_date - timedelta(days=horizon_days)

    # Filter NAV series within horizon window
    window_indices = [i for i, d in enumerate(dates) if d >= target_start_date]
    if len(window_indices) < 8:
        return None

    window_dates = [dates[i] for i in window_indices]
    window_navs = navs[window_indices]

    start_date = window_dates[0]
    days_elapsed = (end_date - start_date).days

    # Enforce minimum history requirement (80% of horizon)
    min_required_days = int(horizon_days * 0.80)
    if days_elapsed < min_required_days:
        return None

    years = days_elapsed / 365.0
    start_nav = window_navs[0]
    end_nav = window_navs[-1]

    if start_nav <= 0 or end_nav <= 0:
        return None

    # Annualized Return / CAGR (or simple return if horizon < 1 year)
    if years >= 0.95:
        cagr = (end_nav / start_nav) ** (1.0 / years) - 1.0
    else:
        cagr = (end_nav - start_nav) / start_nav

    daily_returns = np.diff(window_navs) / window_navs[:-1]
    if len(daily_returns) == 0:
        return None

    daily_vol = np.std(daily_returns, ddof=1) if len(daily_returns) > 1 else 0.0
    annual_vol = daily_vol * np.sqrt(252)

    # Sharpe Ratio
    excess_return = cagr - RISK_FREE_RATE
    sharpe = (excess_return / annual_vol) if annual_vol > 1e-6 else 0.0

    # Downside Risk & Sortino
    rf_daily = (1.0 + RISK_FREE_RATE) ** (1.0 / 252.0) - 1.0
    downside_diff = np.minimum(0.0, daily_returns - rf_daily)
    downside_vol_daily = np.sqrt(np.mean(downside_diff ** 2)) if len(downside_diff) > 0 else 0.0
    downside_vol_annual = downside_vol_daily * np.sqrt(252)
    sortino = (excess_return / downside_vol_annual) if downside_vol_annual > 1e-6 else 0.0

    # Max Drawdown
    running_max = np.maximum.accumulate(window_navs)
    drawdowns = (running_max - window_navs) / running_max
    max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    calmar = (cagr / max_drawdown) if max_drawdown > 1e-4 else (cagr / 0.0001 if cagr > 0 else 0.0)

    return {
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "days_eval": days_elapsed,
        "years_eval": round(years, 2),
        "start_nav": round(float(start_nav), 4),
        "current_nav": round(float(end_nav), 4),
        "cagr": round(float(cagr) * 100.0, 2),
        "sharpe": round(float(sharpe), 2),
        "sortino": round(float(sortino), 2),
        "max_drawdown": round(float(max_drawdown) * 100.0, 2),
        "calmar": round(float(calmar), 2),
        "volatility": round(float(annual_vol) * 100.0, 2)
    }


def get_underperformance_diagnostics(
    db: Session,
    timeframe: str = "3y",
    category_filter: str = "ALL",
    status_filter: str = "ALL",
    scope_filter: str = "PORTFOLIO",
    timing_filter: str = "ALL",
    sort_by: str = "SCORE_ASC",
    as_of_date: str | date = None
) -> dict:
    """
    Scans all mutual funds, computes multi-horizon metrics (6M, 12M, 24M, 36M),
    relative momentum (6M/12M relative ROC vs category), multi-period persistence matrix,
    5-pillar composite 0-100 cut scores, and 4-quadrant personal entry timing overlay
    as of a specific historical evaluation date.
    """
    # Parse as_of_date
    if isinstance(as_of_date, str) and as_of_date.strip():
        try:
            eval_end_date = datetime.strptime(as_of_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            eval_end_date = date.today()
    elif isinstance(as_of_date, date):
        eval_end_date = as_of_date
    else:
        eval_end_date = date.today()

    if timeframe == "1y":
        primary_days = 365
        primary_tf_years = 1.0
    elif timeframe == "5y":
        primary_days = 5 * 365
        primary_tf_years = 5.0
    else:  # default 3y
        timeframe = "3y"
        primary_days = 3 * 365
        primary_tf_years = 3.0

    query_start_date = eval_end_date - timedelta(days=max(primary_days, 3 * 365) + 30)

    # 1. Fetch scheme details mapping
    details = db.query(MFDetail.scheme_name, MFDetail.fund_type, MFDetail.advisor).all()
    details_map = {d.scheme_name.upper(): (d.fund_type, d.advisor) for d in details}

    # 2. Fetch all holdings with units & cost NAV to compute personal entry PnL
    holdings_all = db.query(
        MFHolding.scheme_name,
        MFHolding.amfi_code,
        MFHolding.units,
        MFHolding.average_nav,
        MFHolding.current_nav,
        MFHolding.current_value,
        MFHolding.person_id
    ).filter(
        MFHolding.amfi_code.isnot(None),
        MFHolding.amfi_code != "",
        MFHolding.amfi_code != "0"
    ).all()

    grouped_holdings = {}
    for h in holdings_all:
        code_clean = str(h.amfi_code).strip().split('.')[0]
        grouped_holdings.setdefault(code_clean, []).append(h)

    scheme_meta = {}
    for code_clean, h_list in grouped_holdings.items():
        scheme_name = h_list[0].scheme_name
        meta_ft, meta_adv = details_map.get(scheme_name.upper(), (None, None))
        ft = meta_ft or get_fund_type(scheme_name)

        tot_units = sum(float(x.units or 0.0) for x in h_list)
        tot_value = sum(float(x.current_value or 0.0) for x in h_list)
        tot_cost = sum((float(x.units or 0.0) * float(x.average_nav or 0.0)) for x in h_list if x.average_nav)
        person_ids = {x.person_id for x in h_list}

        weighted_avg_nav = (tot_cost / tot_units) if tot_units > 0 else 0.0

        scheme_meta[code_clean] = {
            "scheme_name": scheme_name,
            "amfi_code": code_clean,
            "category": ft.upper() if ft else "EQUITY",
            "total_value": tot_value,
            "total_cost": tot_cost,
            "total_units": tot_units,
            "weighted_avg_nav": round(weighted_avg_nav, 4),
            "latest_nav": 0.0, # Will be populated from NAV history as of eval_end_date
            "personal_pnl_pct": 0.0,
            "holder_count": len(person_ids),
            "advisor": meta_adv or "UNKNOWN",
            "in_portfolio": tot_value > 0
        }

    txn_rows = db.query(
        MFTransaction.scheme_name,
        MFTransaction.amfi_code
    ).filter(
        MFTransaction.amfi_code.isnot(None),
        MFTransaction.amfi_code != "",
        MFTransaction.amfi_code != "0"
    ).distinct().all()

    for t in txn_rows:
        code_clean = str(t.amfi_code).strip().split('.')[0]
        if code_clean not in scheme_meta:
            meta_ft, meta_adv = details_map.get(t.scheme_name.upper(), (None, None))
            ft = meta_ft or get_fund_type(t.scheme_name)
            scheme_meta[code_clean] = {
                "scheme_name": t.scheme_name,
                "amfi_code": code_clean,
                "category": ft.upper() if ft else "EQUITY",
                "total_value": 0.0,
                "total_cost": 0.0,
                "total_units": 0.0,
                "weighted_avg_nav": 0.0,
                "latest_nav": 0.0,
                "personal_pnl_pct": 0.0,
                "holder_count": 0,
                "advisor": meta_adv or "UNKNOWN",
                "in_portfolio": False
            }

    # 3. Pull NAV time series for each code in bulk up to eval_end_date
    all_amfi_codes = list(scheme_meta.keys())
    nav_rows = db.query(
        MFNav.amfi_code,
        MFNav.nav_date,
        MFNav.nav
    ).filter(
        MFNav.amfi_code.in_(all_amfi_codes),
        MFNav.nav_date >= query_start_date,
        MFNav.nav_date <= eval_end_date
    ).order_by(MFNav.nav_date.asc()).all()

    nav_by_code = {}
    for r in nav_rows:
        nav_by_code.setdefault(r.amfi_code, []).append((r.nav_date, float(r.nav)))

    # Compute Multi-Horizon Metrics (6M, 12M, 24M, 36M) for each scheme
    fund_results = []
    category_groups = {}

    horizons = {
        "6m": 180,
        "12m": 365,
        "24m": 730,
        "36m": 1095
    }

    for code, meta in scheme_meta.items():
        nav_series = nav_by_code.get(code, [])
        if not nav_series:
            continue

        # Set latest_nav as of eval_end_date
        latest_as_of_nav = nav_series[-1][1]
        meta["latest_nav"] = round(latest_as_of_nav, 4)
        if meta["weighted_avg_nav"] > 0:
            meta["personal_pnl_pct"] = round(((latest_as_of_nav - meta["weighted_avg_nav"]) / meta["weighted_avg_nav"]) * 100.0, 2)

        h_metrics = {}
        for h_key, h_days in horizons.items():
            h_metrics[h_key] = calculate_fund_horizon_metrics(nav_series, horizon_days=h_days, eval_end_date=eval_end_date)

        primary_metrics = calculate_fund_horizon_metrics(nav_series, horizon_days=primary_days, eval_end_date=eval_end_date)
        if not primary_metrics:
            primary_metrics = h_metrics.get("12m") or h_metrics.get("6m")
            if not primary_metrics:
                continue

        item = {
            **meta,
            **primary_metrics,
            "h_metrics": h_metrics
        }
        fund_results.append(item)

        cat = meta["category"]
        category_groups.setdefault(cat, []).append(item)

    # 4. Compute Category Benchmarks for each horizon
    category_benchmarks = {}
    for cat, items in category_groups.items():
        if not items:
            continue

        avg_cagr = float(np.mean([x["cagr"] for x in items]))
        avg_sharpe = float(np.mean([x["sharpe"] for x in items]))
        avg_calmar = float(np.mean([x["calmar"] for x in items]))
        avg_sortino = float(np.mean([x["sortino"] for x in items]))
        avg_mdd = float(np.mean([x["max_drawdown"] for x in items]))
        avg_vol = float(np.mean([x["volatility"] for x in items]))

        h_avgs = {}
        for h_key in ["6m", "12m", "24m", "36m"]:
            valid_cagrs = [x["h_metrics"][h_key]["cagr"] for x in items if x.get("h_metrics", {}).get(h_key)]
            h_avgs[h_key] = round(float(np.mean(valid_cagrs)), 2) if valid_cagrs else round(avg_cagr, 2)

        category_benchmarks[cat] = {
            "category": cat,
            "fund_count": len(items),
            "avg_cagr": round(avg_cagr, 2),
            "avg_sharpe": round(avg_sharpe, 2),
            "avg_calmar": round(avg_calmar, 2),
            "avg_sortino": round(avg_sortino, 2),
            "avg_max_drawdown": round(avg_mdd, 2),
            "avg_volatility": round(avg_vol, 2),
            "h_avgs": h_avgs
        }

    # 5. Evaluate Multi-Horizon Matrix, Relative Momentum, 5-Pillar Score, & 4-Quadrant Timing Matrix
    evaluated_funds = []
    for fund in fund_results:
        cat = fund["category"]
        bm = category_benchmarks.get(cat)
        if not bm:
            continue

        cagr_fail = fund["cagr"] < bm["avg_cagr"]
        sharpe_fail = fund["sharpe"] < bm["avg_sharpe"]
        calmar_fail = fund["calmar"] < bm["avg_calmar"]
        sortino_fail = fund["sortino"] < bm["avg_sortino"]
        mdd_fail = fund["max_drawdown"] > bm["avg_max_drawdown"]
        vol_fail = fund["volatility"] > bm["avg_volatility"]

        failed_count = sum([cagr_fail, sharpe_fail, calmar_fail, sortino_fail, mdd_fail, vol_fail])

        # A. Multi-Horizon Persistence Matrix [ 6M | 12M | 24M | 36M ]
        h_matrix = {}
        horizon_pass_count = 0
        horizon_total_count = 0

        for h_key in ["6m", "12m", "24m", "36m"]:
            h_data = fund.get("h_metrics", {}).get(h_key)
            bm_h_avg = bm["h_avgs"].get(h_key, 0.0)
            if h_data:
                horizon_total_count += 1
                diff = round(h_data["cagr"] - bm_h_avg, 2)
                is_outperforming = diff >= 0
                if is_outperforming:
                    horizon_pass_count += 1
                h_matrix[h_key] = {
                    "cagr": h_data["cagr"],
                    "bm_avg": bm_h_avg,
                    "diff": diff,
                    "status": "PASS" if is_outperforming else "FAIL"
                }
            else:
                h_matrix[h_key] = {"cagr": None, "bm_avg": bm_h_avg, "diff": 0.0, "status": "N/A"}

        # B. Relative Momentum (6M & 12M Relative ROC)
        rel_roc_6m = h_matrix["6m"]["diff"] if h_matrix["6m"]["status"] != "N/A" else round(fund["cagr"] - bm["avg_cagr"], 2)
        rel_roc_12m = h_matrix["12m"]["diff"] if h_matrix["12m"]["status"] != "N/A" else round(fund["cagr"] - bm["avg_cagr"], 2)

        # C. 5-Pillar Score Calculation (0 to 100 points)
        cagr_diff = fund["cagr"] - bm["avg_cagr"]
        p1 = 10.0 + min(10.0, max(-10.0, cagr_diff * 1.0))

        sharpe_diff = fund["sharpe"] - bm["avg_sharpe"]
        sortino_diff = fund["sortino"] - bm["avg_sortino"]
        p2 = 10.0 + min(5.0, max(-5.0, sharpe_diff * 5.0)) + min(5.0, max(-5.0, sortino_diff * 4.0))

        mdd_diff = bm["avg_max_drawdown"] - fund["max_drawdown"]
        p3 = 10.0 + min(10.0, max(-10.0, mdd_diff * 1.0))

        p4 = 10.0 + min(10.0, max(-10.0, rel_roc_6m * 1.5))

        if horizon_total_count > 0:
            p5 = (horizon_pass_count / horizon_total_count) * 20.0
        else:
            p5 = 10.0

        composite_score = round(max(0.0, min(100.0, p1 + p2 + p3 + p4 + p5)), 1)

        # Assign 4-Tier Institutional Action Status based on Composite Score
        if composite_score >= 80.0:
            status = "HEALTHY"
            status_code = "HOLD"
            status_label = "GREEN - HOLD"
        elif composite_score >= 65.0:
            status = "WATCHLIST"
            status_code = "WATCH"
            status_label = "YELLOW - WATCH"
        elif composite_score >= 50.0:
            status = "REVIEW"
            status_code = "REVIEW"
            status_label = "ORANGE - REVIEW"
        else:
            status = "SEVERE"
            status_code = "CUT"
            status_label = "RED - CUT CANDIDATE"

        # D. 4-Quadrant Personal Entry & Timing Matrix (Fund Quality vs Personal PnL)
        is_good_fund = composite_score >= 65.0
        is_personal_profit = (fund.get("personal_pnl_pct", 0.0) >= 0.0) if fund.get("in_portfolio") else True

        if is_good_fund and is_personal_profit:
            timing_code = "WINNING_HOLD"
            timing_label = "🟢 WINNING HOLD"
            timing_sub = "Good Fund + Profit (Compound)"
        elif is_good_fund and not is_personal_profit:
            timing_code = "BUY_DIP"
            timing_label = "🔵 BUY THE DIP"
            timing_sub = "Good Fund + Entry Loss (SIP Candidate)"
        elif not is_good_fund and is_personal_profit:
            timing_code = "TACTICAL_EXIT"
            timing_label = "🟡 TACTICAL EXIT"
            timing_sub = "Bad Fund + Profit (Switch Opportunity)"
        else:
            timing_code = "STOP_LOSS"
            timing_label = "🔴 STOP LOSS"
            timing_sub = "Bad Fund + Entry Loss (Cut & Harvest)"

        fund["cat_avg_cagr"] = bm["avg_cagr"]
        fund["cat_avg_sharpe"] = bm["avg_sharpe"]
        fund["cat_avg_calmar"] = bm["avg_calmar"]
        fund["cat_avg_sortino"] = bm["avg_sortino"]
        fund["cat_avg_mdd"] = bm["avg_max_drawdown"]
        fund["cat_avg_vol"] = bm["avg_volatility"]

        fund["cagr_fail"] = cagr_fail
        fund["sharpe_fail"] = sharpe_fail
        fund["calmar_fail"] = calmar_fail
        fund["sortino_fail"] = sortino_fail
        fund["mdd_fail"] = mdd_fail
        fund["vol_fail"] = vol_fail

        fund["failed_count"] = failed_count
        fund["total_params"] = 6
        fund["h_matrix"] = h_matrix
        fund["rel_roc_6m"] = rel_roc_6m
        fund["rel_roc_12m"] = rel_roc_12m
        fund["composite_score"] = composite_score
        fund["pillars"] = {
            "p1_excess_return": round(p1, 1),
            "p2_risk_adjusted": round(p2, 1),
            "p3_downside_protection": round(p3, 1),
            "p4_relative_momentum": round(p4, 1),
            "p5_persistence": round(p5, 1)
        }
        fund["status"] = status
        fund["status_code"] = status_code
        fund["status_label"] = status_label
        fund["timing_code"] = timing_code
        fund["timing_label"] = timing_label
        fund["timing_sub"] = timing_sub

        evaluated_funds.append(fund)

    # 6. Apply Scope Filter (Portfolio Holdings Only vs All Tracked)
    scoped_funds = evaluated_funds
    if scope_filter and scope_filter.upper() == "PORTFOLIO":
        scoped_funds = [f for f in scoped_funds if f.get("in_portfolio") and f.get("total_value", 0) > 0]

    # Calculate 4-Tier Action counts for scoped funds
    cut_count = sum(1 for f in scoped_funds if f["status_code"] == "CUT")
    review_count = sum(1 for f in scoped_funds if f["status_code"] == "REVIEW")
    watch_count = sum(1 for f in scoped_funds if f["status_code"] == "WATCH")
    hold_count = sum(1 for f in scoped_funds if f["status_code"] == "HOLD")

    # Calculate 4-Quadrant Timing counts for scoped funds
    tactical_exit_count = sum(1 for f in scoped_funds if f["timing_code"] == "TACTICAL_EXIT")
    buy_dip_count = sum(1 for f in scoped_funds if f["timing_code"] == "BUY_DIP")
    stop_loss_count = sum(1 for f in scoped_funds if f["timing_code"] == "STOP_LOSS")
    winning_hold_count = sum(1 for f in scoped_funds if f["timing_code"] == "WINNING_HOLD")

    # Apply Category & Status & Timing Filters if specified
    filtered_funds = scoped_funds
    if category_filter and category_filter != "ALL":
        filtered_funds = [f for f in filtered_funds if f["category"] == category_filter.upper()]

    if status_filter and status_filter != "ALL":
        filtered_funds = [f for f in filtered_funds if f["status_code"] == status_filter.upper()]

    if timing_filter and timing_filter != "ALL":
        filtered_funds = [f for f in filtered_funds if f["timing_code"] == timing_filter.upper()]

    # Apply requested sorting option
    if sort_by == "SCORE_ASC":
        filtered_funds.sort(key=lambda x: (x["composite_score"], -x.get("total_value", 0)))
    elif sort_by == "SCORE_DESC":
        filtered_funds.sort(key=lambda x: (-x["composite_score"], -x.get("total_value", 0)))
    elif sort_by == "PNL_ASC":
        filtered_funds.sort(key=lambda x: x.get("personal_pnl_pct", 0.0))
    elif sort_by == "PNL_DESC":
        filtered_funds.sort(key=lambda x: -x.get("personal_pnl_pct", 0.0))
    elif sort_by == "AUM_DESC":
        filtered_funds.sort(key=lambda x: -x.get("total_value", 0))
    elif sort_by == "MOMENTUM_ASC":
        filtered_funds.sort(key=lambda x: x.get("rel_roc_6m", 0))
    elif sort_by == "CAGR_ASC":
        filtered_funds.sort(key=lambda x: x.get("cagr", 0))
    elif sort_by == "CAGR_DESC":
        filtered_funds.sort(key=lambda x: -x.get("cagr", 0))
    elif sort_by == "SHARPE_ASC":
        filtered_funds.sort(key=lambda x: x.get("sharpe", 0))
    elif sort_by == "MDD_DESC":
        filtered_funds.sort(key=lambda x: -x.get("max_drawdown", 0))
    elif sort_by == "NAME_ASC":
        filtered_funds.sort(key=lambda x: x.get("scheme_name", "").upper())
    else:  # SCORE_ASC (default)
        filtered_funds.sort(key=lambda x: (x["composite_score"], -x.get("total_value", 0)))

    available_categories = sorted(list(category_benchmarks.keys()))

    return {
        "as_of_date": eval_end_date.strftime("%Y-%m-%d"),
        "timeframe": timeframe,
        "category_filter": category_filter,
        "status_filter": status_filter,
        "scope_filter": scope_filter,
        "timing_filter": timing_filter,
        "sort_by": sort_by,
        "total_scanned": len(scoped_funds),
        "cut_count": cut_count,
        "review_count": review_count,
        "watch_count": watch_count,
        "hold_count": hold_count,
        "tactical_exit_count": tactical_exit_count,
        "buy_dip_count": buy_dip_count,
        "stop_loss_count": stop_loss_count,
        "winning_hold_count": winning_hold_count,
        "funds": filtered_funds,
        "category_benchmarks": category_benchmarks,
        "available_categories": available_categories
    }
