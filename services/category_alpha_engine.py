# services/category_alpha_engine.py
import math
import re
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from models.mutual_funds import MFHolding, MFDetail, MFTransaction
from models.market_data import MFNav
from models.enums import TransactionStatus
from services.benchmark_comparison_engine import fetch_or_load_scheme_navs, get_nav_on_or_before, calculate_risk_metrics
from services.fifo_engine import resolve_advisor

# Designated Category Benchmark Mapping (AMFI codes with long historical daily NAVs)
CATEGORY_BENCHMARK_MAP = {
    "GOLD": {
        "amfi": "152290", # Tata Gold ETF FoF / SBI Gold
        "backup_amfi": "150641",
        "name": "Domestic Gold Spot / Gold ETF",
        "risk_cap_pct": 25.0
    },
    "SILVER": {
        "amfi": "150737", # HDFC Silver ETF FoF Direct
        "backup_amfi": "150736",
        "name": "Domestic Silver Spot / Silver ETF",
        "risk_cap_pct": 10.0
    },
    "MIDCAP": {
        "amfi": "118666", # Nippon India Growth Mid Cap Fund Direct
        "backup_amfi": "118989", # HDFC Mid-Cap Direct
        "name": "Nifty Midcap 150 TRI",
        "risk_cap_pct": 20.0
    },
    "LARGE & MID": {
        "amfi": "120357", # Invesco Large & Mid Cap Direct
        "backup_amfi": "108466",
        "name": "Nifty LargeMidcap 250 TRI",
        "risk_cap_pct": 20.0
    },
    "LARGE CAP": {
        "amfi": "120716", # UTI Nifty 50 Index Fund Direct Growth
        "backup_amfi": "108466", # ICICI Large Cap
        "name": "Nifty 50 TRI",
        "risk_cap_pct": 25.0
    },
    "FINANCE": {
        "amfi": "120244", # ICICI Prudential Banking & Financial Direct
        "backup_amfi": "109445",
        "name": "Nifty Financial Services TRI",
        "risk_cap_pct": 10.0
    },
    "HEALTHCARE": {
        "amfi": "143873", # ICICI Prudential Pharma & Healthcare
        "backup_amfi": "152084",
        "name": "Nifty Healthcare TRI",
        "risk_cap_pct": 8.0
    },
    "AUTO": {
        "amfi": "102398", # UTI Transportation & Logistics Fund
        "backup_amfi": "152657",
        "name": "Nifty Auto TRI",
        "risk_cap_pct": 8.0
    },
    "IT": {
        "amfi": "100363", # ICICI Prudential Technology Fund
        "backup_amfi": "100363",
        "name": "Nifty IT TRI",
        "risk_cap_pct": 8.0
    },
    "SMALLCAP": {
        "amfi": "100177", # Quant Small Cap Fund
        "backup_amfi": "145139",
        "name": "Nifty Smallcap 250 TRI",
        "risk_cap_pct": 10.0
    },
    "MULTI-ASSET": {
        "amfi": "152056", # DSP Multi Asset Allocation Fund Direct
        "backup_amfi": "152065",
        "name": "CRISIL Multi Asset Hybrid Index",
        "risk_cap_pct": 15.0
    },
    "MULTICAP": {
        "amfi": "100631", # Quant Multi Cap Fund
        "backup_amfi": "151289",
        "name": "Nifty 500 Multicap 50:25:25 TRI",
        "risk_cap_pct": 15.0
    },
    "FLEXI": {
        "amfi": "141925", # Axis Flexi Cap Fund Direct
        "backup_amfi": "133836",
        "name": "Nifty 500 TRI",
        "risk_cap_pct": 15.0
    },
    "ARBITRAGE": {
        "amfi": "104683", # ICICI Prudential Arbitrage Fund
        "backup_amfi": "104075",
        "name": "Nifty 50 Arbitrage Index",
        "risk_cap_pct": 15.0
    },
    "LIQUID": {
        "amfi": "103340", # ICICI Prudential Liquid Fund
        "backup_amfi": "102012",
        "name": "CRISIL Liquid Debt Index",
        "risk_cap_pct": 15.0
    },
    "DEFENCE": {
        "amfi": "152711", # Motilal Oswal Nifty India Defence Index
        "backup_amfi": "151750",
        "name": "Nifty India Defence Index",
        "risk_cap_pct": 5.0
    },
    "INFRA": {
        "amfi": "102885", # ICICI Prudential Infrastructure Fund
        "backup_amfi": "103178",
        "name": "Nifty Infrastructure TRI",
        "risk_cap_pct": 8.0
    },
    "CONSUMER": {
        "amfi": "107579", # Mirae Asset Great Consumer Fund
        "backup_amfi": "103178",
        "name": "Nifty India Consumption TRI",
        "risk_cap_pct": 8.0
    },
    "ENERGY": {
        "amfi": "12047637", # Tata Resources & Energy Fund
        "backup_amfi": "102885",
        "name": "Nifty Energy TRI",
        "risk_cap_pct": 8.0
    }
}

# Fallback default benchmark
DEFAULT_BENCHMARK = {"amfi": "120716", "name": "Nifty 50 TRI", "risk_cap_pct": 15.0}


def classify_category(scheme_name: str, dtl: Optional[MFDetail] = None) -> str:
    """Accurately classifies any mutual fund scheme into its standard asset category."""
    s_upper = (scheme_name or "").upper()
    
    if "GOLD" in s_upper:
        return "GOLD"
    if "SILVER" in s_upper:
        return "SILVER"
    if "ARBITRAGE" in s_upper:
        return "ARBITRAGE"
    if any(k in s_upper for k in ["LIQUID", "OVERNIGHT", "ULTRA SHORT", "LOW DURATION", "MONEY MARKET", "TREASURY"]):
        return "LIQUID"
    if any(k in s_upper for k in ["BANKING", "FINANCIAL"]):
        return "FINANCE"
    if any(k in s_upper for k in ["HEALTHCARE", "PHARMA"]):
        return "HEALTHCARE"
    if "DEFENCE" in s_upper:
        return "DEFENCE"
    if "MIDCAP" in s_upper or "MID CAP" in s_upper:
        return "MIDCAP"
    if "SMALLCAP" in s_upper or "SMALL CAP" in s_upper:
        return "SMALLCAP"
    if "LARGE & MID" in s_upper:
        return "LARGE & MID"
    if any(k in s_upper for k in ["LARGE CAP", "BLUECHIP", "NIFTY 50", "TOP 100"]):
        return "LARGE CAP"
    if "MULTI-ASSET" in s_upper or "MULTI ASSET" in s_upper:
        return "MULTI-ASSET"
    if "MULTICAP" in s_upper or "MULTI CAP" in s_upper:
        return "MULTICAP"
    if "FLEXI" in s_upper:
        return "FLEXI"
    if "INFRA" in s_upper:
        return "INFRA"
    if "CONSUMER" in s_upper or "CONSUMPTION" in s_upper:
        return "CONSUMER"
    if "ENERGY" in s_upper or "RESOURCES" in s_upper:
        return "ENERGY"
    if "AUTO" in s_upper or "LOGISTICS" in s_upper or "TRANSPORTATION" in s_upper:
        return "AUTO"
    if "TECH" in s_upper or "IT " in s_upper:
        return "IT"

    # Fallback to MFDetail fund_type
    if dtl and dtl.fund_type:
        ft = dtl.fund_type.strip().upper()
        if ft not in ["UNKNOWN", "NONE", "EQUITY"]:
            return ft

    return "EQUITY"


def get_category_alpha_matrix(
    db: Session,
    person_id: Optional[int] = None,
    advisor: str = "ALL",
    as_of_date: Optional[date] = None,
    benchmark_mode: str = "CATEGORY"
) -> Dict[str, Any]:
    """
    Computes point-in-time category-wise multi-horizon performance, dedicated benchmark returns,
    alpha matrix (1M, 3M, 6M, 1Y), Sharpe ratios, and tactical Move In / Move Out signals.
    Supports historical backtesting for any given as_of_date.
    benchmark_mode options: 'CATEGORY', 'NIFTY', 'HURDLE_12'
    """
    eval_date = as_of_date or date.today()
    is_historical = as_of_date is not None and as_of_date < date.today()
    bm_mode = (benchmark_mode or "CATEGORY").upper()

    # 1. Query MF details mapping
    details = {d.scheme_name.upper(): d for d in db.query(MFDetail).all()}
    sel_adv = advisor.strip().upper() if advisor else "ALL"

    cat_holdings_map = {}
    total_portfolio_value = 0.0

    inflow_types = {'PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT', 'DIVIDEND_REINVEST'}
    outflow_types = {'REDEMPTION', 'SWITCH_OUT'}

    if is_historical:
        # Reconstruct exact point-in-time holdings via FIFO lot accounting as of historical eval_date
        txns_query = db.query(MFTransaction).filter(
            MFTransaction.status == TransactionStatus.ACTIVE,
            MFTransaction.transaction_date <= eval_date
        )
        if person_id:
            txns_query = txns_query.filter(MFTransaction.person_id == person_id)
        historical_txns = txns_query.order_by(MFTransaction.transaction_date.asc(), MFTransaction.id.asc()).all()

        lots_by_key = {}
        for t in historical_txns:
            s_name = t.scheme_name.upper()
            folio = (t.folio_number or "").strip()
            key = (s_name, folio)
            t_type = (t.transaction_type or '').upper()
            u = abs(float(t.units or 0.0))
            amt = abs(float(t.amount or 0.0))

            adv = resolve_advisor(t.scheme_name, t.folio_number, t.notes, db)
            if sel_adv != "ALL":
                if sel_adv not in adv.upper():
                    continue

            if t_type in inflow_types:
                if key not in lots_by_key:
                    lots_by_key[key] = []
                lots_by_key[key].append({
                    'units': u,
                    'rem_units': u,
                    'amount': amt,
                    'amfi': t.amfi_code,
                    'raw_name': t.scheme_name,
                    'person_id': t.person_id,
                    'folio': folio,
                    'advisor': adv
                })
            elif t_type in outflow_types:
                units_to_sell = u
                if key in lots_by_key:
                    for lot in lots_by_key[key]:
                        if lot['rem_units'] <= 0.001:
                            continue
                        deduct = min(units_to_sell, lot['rem_units'])
                        lot['rem_units'] -= deduct
                        units_to_sell -= deduct
                        if units_to_sell <= 0.001:
                            break

        active_schemes = {}
        for key, lots in lots_by_key.items():
            for lot in lots:
                if lot['rem_units'] > 0.001:
                    s_name = key[0]
                    if s_name not in active_schemes:
                        active_schemes[s_name] = {
                            'units': 0.0,
                            'invested': 0.0,
                            'amfi': lot['amfi'],
                            'raw_name': lot['raw_name'],
                            'advisor': lot['advisor']
                        }
                    cost_of_rem = (lot['rem_units'] / lot['units']) * lot['amount'] if lot['units'] > 0 else 0
                    active_schemes[s_name]['units'] += lot['rem_units']
                    active_schemes[s_name]['invested'] += cost_of_rem

        for s_name, s_data in active_schemes.items():
            rem_units = s_data["units"]
            if rem_units <= 0.001:
                continue

            amfi = s_data["amfi"]
            h_nav = 0.0
            if amfi:
                s_series = fetch_or_load_scheme_navs(amfi, db)
                if s_series:
                    h_nav = get_nav_on_or_before(s_series, eval_date)

            h_val = rem_units * h_nav if h_nav > 0 else s_data["invested"]
            h_inv = s_data["invested"]

            dtl = details.get(s_name)
            cat_key = classify_category(s_name, dtl)

            if cat_key not in cat_holdings_map:
                cat_holdings_map[cat_key] = {
                    "category": cat_key,
                    "current_value": 0.0,
                    "invested_amount": 0.0,
                    "schemes_count": 0,
                    "schemes": []
                }

            cat_holdings_map[cat_key]["current_value"] += h_val
            cat_holdings_map[cat_key]["invested_amount"] += h_inv
            cat_holdings_map[cat_key]["schemes_count"] += 1
            cat_holdings_map[cat_key]["schemes"].append({
                "name": s_data["raw_name"],
                "val": h_val,
                "inv": h_inv,
                "gain_pct": ((h_val - h_inv) / h_inv * 100.0) if h_inv > 0 else 0.0,
                "amfi": amfi
            })
            total_portfolio_value += h_val

    else:
        # Current holdings query
        holdings_query = db.query(MFHolding).filter(MFHolding.current_value > 1000)
        if person_id:
            holdings_query = holdings_query.filter(MFHolding.person_id == person_id)
        raw_holdings = holdings_query.all()

        for h in raw_holdings:
            s_upper = h.scheme_name.upper()
            dtl = details.get(s_upper)
            h_adv = resolve_advisor(h.scheme_name, h.folio_number, "", db, db_detail=dtl)
            
            if sel_adv != "ALL":
                if sel_adv not in h_adv.upper():
                    continue

            h_val = float(h.current_value or 0.0)
            h_units = float(h.units or 0.0)
            h_avg_nav = float(h.average_nav or 0.0)
            h_invested = h_units * h_avg_nav if h_avg_nav > 0 else h_val
            
            cat_key = classify_category(s_upper, dtl)

            if cat_key not in cat_holdings_map:
                cat_holdings_map[cat_key] = {
                    "category": cat_key,
                    "current_value": 0.0,
                    "invested_amount": 0.0,
                    "schemes_count": 0,
                    "schemes": []
                }

            cat_holdings_map[cat_key]["current_value"] += h_val
            cat_holdings_map[cat_key]["invested_amount"] += h_invested
            cat_holdings_map[cat_key]["schemes_count"] += 1
            cat_holdings_map[cat_key]["schemes"].append({
                "name": h.scheme_name,
                "val": h_val,
                "inv": h_invested,
                "gain_pct": ((h_val - h_invested) / h_invested * 100.0) if h_invested > 0 else 0.0,
                "amfi": h.amfi_code
            })
            total_portfolio_value += h_val

    # 4. Evaluation Horizons ending on point-in-time eval_date
    horizons = [
        {"key": "1m", "label": "1M", "days": 30},
        {"key": "3m", "label": "3M", "days": 90},
        {"key": "6m", "label": "6M", "days": 180},
        {"key": "1y", "label": "1Y", "days": 365}
    ]

    # Pre-fetch Nifty 50 series if in NIFTY benchmark mode
    nifty_series = None
    if bm_mode == "NIFTY":
        nifty_series = fetch_or_load_scheme_navs("120716", db)

    category_results = []
    bullion_combined_weight = 0.0

    # Sort categories by current valuation descending
    sorted_cats = sorted(cat_holdings_map.values(), key=lambda x: x["current_value"], reverse=True)

    for c in sorted_cats:
        cat_name = c["category"]
        c_val = c["current_value"]
        c_inv = c["invested_amount"]
        weight_pct = (c_val / total_portfolio_value * 100.0) if total_portfolio_value > 0 else 0.0
        total_unrealized_gain = c_val - c_inv
        unrealized_gain_pct = (total_unrealized_gain / c_inv * 100.0) if c_inv > 0 else 0.0

        if cat_name in ["GOLD", "SILVER"]:
            bullion_combined_weight += weight_pct

        # Fetch benchmark configuration
        bm_info = CATEGORY_BENCHMARK_MAP.get(cat_name, DEFAULT_BENCHMARK)
        bm_series = fetch_or_load_scheme_navs(bm_info["amfi"], db)
        if not bm_series or len(bm_series) < 15:
            backup_code = bm_info.get("backup_amfi")
            if backup_code:
                bm_series = fetch_or_load_scheme_navs(backup_code, db)
        if not bm_series:
            bm_series = fetch_or_load_scheme_navs(DEFAULT_BENCHMARK["amfi"], db)

        # Determine benchmark display name
        if bm_mode == "NIFTY":
            display_bm_name = "Nifty 50 TRI"
        elif bm_mode == "HURDLE_12":
            display_bm_name = "12.0% Annual Hurdle"
        else:
            display_bm_name = bm_info["name"]

        # Sort schemes within category by valuation descending to pick the most significant representative scheme
        c["schemes"].sort(key=lambda x: x.get("val", 0.0), reverse=True)

        cat_primary_series = None
        for sch in c["schemes"]:
            if sch.get("amfi"):
                s = fetch_or_load_scheme_navs(sch["amfi"], db)
                if s and len(s) > 30:
                    cat_primary_series = s
                    break
        if not cat_primary_series:
            cat_primary_series = bm_series

        horizon_perf = {}
        alphas = {}

        for h in horizons:
            h_days = h["days"]
            s_date = eval_date - timedelta(days=h_days)
            e_date = eval_date

            # Category Return
            c_s_nav = get_nav_on_or_before(cat_primary_series, s_date)
            c_e_nav = get_nav_on_or_before(cat_primary_series, e_date)
            cat_ret = ((c_e_nav - c_s_nav) / c_s_nav * 100.0) if c_s_nav > 0 else 0.0

            # Benchmark Return based on bm_mode
            if bm_mode == "HURDLE_12":
                # Fixed 12% p.a. absolute hurdle
                if h["key"] == "1m":
                    bm_ret = 1.00
                elif h["key"] == "3m":
                    bm_ret = 3.00
                elif h["key"] == "6m":
                    bm_ret = 6.00
                elif h["key"] == "1y":
                    bm_ret = 12.00
                else:
                    bm_ret = (h_days / 365.0) * 12.0
            elif bm_mode == "NIFTY":
                # Nifty 50 TRI
                bm_s_nav = get_nav_on_or_before(nifty_series, s_date) if nifty_series else 0.0
                bm_e_nav = get_nav_on_or_before(nifty_series, e_date) if nifty_series else 0.0
                bm_ret = ((bm_e_nav - bm_s_nav) / bm_s_nav * 100.0) if bm_s_nav > 0 else 0.0
            else:
                # Category designated benchmark
                bm_s_nav = get_nav_on_or_before(bm_series, s_date)
                bm_e_nav = get_nav_on_or_before(bm_series, e_date)
                bm_ret = ((bm_e_nav - bm_s_nav) / bm_s_nav * 100.0) if bm_s_nav > 0 else 0.0

            alpha_val = cat_ret - bm_ret
            horizon_perf[f"ret_{h['key']}"] = round(cat_ret, 2)
            horizon_perf[f"bm_{h['key']}"] = round(bm_ret, 2)
            alphas[h['key']] = round(alpha_val, 2)

        # Calculate 1Y Category Sharpe as of eval_date
        risk_metrics = calculate_risk_metrics(
            cat_primary_series,
            eval_date - timedelta(days=365),
            eval_date,
            horizon_perf.get("ret_1y", 0.0)
        )
        cat_sharpe = risk_metrics.get("sharpe", 0.0)
        cat_vol = risk_metrics.get("volatility", 0.0)

        # Tactical Decision Classifier
        a_1m = alphas.get("1m", 0.0)
        a_3m = alphas.get("3m", 0.0)
        a_6m = alphas.get("6m", 0.0)
        a_1y = alphas.get("1y", 0.0)
        risk_cap = bm_info.get("risk_cap_pct", 15.0)

        # Rotation Logic:
        if weight_pct > risk_cap and unrealized_gain_pct > 20.0:
            action_badge = "TRIM_PROFIT"
            action_label = "🟠 Take Profits / Trim"
            action_desc = f"Allocation ({weight_pct:.1f}%) exceeds recommended cap ({risk_cap:.0f}%). Lock in gains into Arbitrage/Liquid."
        elif a_3m >= 0.5 and a_6m >= 0.0 and weight_pct < risk_cap * 1.2:
            action_badge = "ACCUMULATE"
            action_label = "🟢 Move In / Accumulate"
            action_desc = f"Strong expanding 3M Alpha (+{a_3m:.2f}%) and solid momentum. Favorable entry zone."
        elif a_3m < -1.0 and a_6m < -1.0:
            action_badge = "ROTATE_OUT"
            action_label = "🔴 Move Out / Rotate"
            action_desc = f"Negative Alpha drag (-{abs(a_3m):.2f}% in 3M vs {display_bm_name}). Rotate capital to higher-alpha categories."
        else:
            action_badge = "HOLD"
            action_label = "🟡 Hold / Neutral"
            action_desc = f"Performing in-line with {display_bm_name}. Maintain current allocation."

        category_results.append({
            "category": cat_name,
            "benchmark_name": display_bm_name,
            "current_value": round(c_val, 2),
            "invested_amount": round(c_inv, 2),
            "unrealized_profit": round(total_unrealized_gain, 2),
            "unrealized_gain_pct": round(unrealized_gain_pct, 2),
            "weight_pct": round(weight_pct, 2),
            "risk_cap_pct": risk_cap,
            "schemes_count": c["schemes_count"],
            "top_scheme": c["schemes"][0]["name"] if c["schemes"] else "-",
            "perf": horizon_perf,
            "alpha": alphas,
            "sharpe": cat_sharpe,
            "volatility": cat_vol,
            "action_badge": action_badge,
            "action_label": action_label,
            "action_desc": action_desc
        })

    # Summary Statistics
    accumulate_count = sum(1 for c in category_results if c["action_badge"] == "ACCUMULATE")
    trim_count = sum(1 for c in category_results if c["action_badge"] == "TRIM_PROFIT")
    rotate_out_count = sum(1 for c in category_results if c["action_badge"] == "ROTATE_OUT")
    hold_count = sum(1 for c in category_results if c["action_badge"] == "HOLD")

    return {
        "categories": category_results,
        "total_portfolio_value": round(total_portfolio_value, 2),
        "bullion_combined_weight": round(bullion_combined_weight, 2),
        "eval_date": eval_date.strftime("%Y-%m-%d"),
        "is_historical": is_historical,
        "benchmark_mode": bm_mode,
        "counts": {
            "accumulate": accumulate_count,
            "trim": trim_count,
            "rotate_out": rotate_out_count,
            "hold": hold_count,
            "total_categories": len(category_results)
        }
    }

