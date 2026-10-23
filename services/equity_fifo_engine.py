import json
import re
from datetime import datetime, date
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from database import SessionLocal
from models.equities import EquityTransaction, EquityHolding
from models.persons import Person
from models.accounts import Account
from models.enums import TransactionStatus, TransactionType

def get_financial_year(dt: date) -> str:
    """Calculates the financial year for a given date (starts April 1) like FY2025-26."""
    if not dt:
        return None
    year = dt.year
    if dt.month < 4:
        return f"FY{year-1}-{str(year)[2:]}"
    else:
        return f"FY{year}-{str(year+1)[2:]}"


# ─── Asset Classification for Tax Slab Bifurcation ───────────────────────
# Indian tax rules differ by asset class:
#   Listed Equity:    STCG @ 20% (Sec 111A), LTCG @ 12.5% (Sec 112A, ₹1.25L exemption)
#   Gold/Silver ETF:  STCG @ Slab Rate,      LTCG @ 12.5% (no exemption)
#   Debt/Liquid ETF:  Always @ Slab Rate (no LTCG benefit post Apr 2023)
#   Index ETF:        Same as Listed Equity
#   Intraday:         Speculative business income @ Slab Rate

GOLD_KEYWORDS = ['GOLD', 'GOLDBEES', 'GOLDETF']
SILVER_KEYWORDS = ['SILVER', 'SILVERBEES', 'SILVERIETF']
DEBT_LIQUID_KEYWORDS = ['LIQUIDBEES', 'LIQUID', 'SDL26BEES', 'ZERODHA NIFTY 1D RATE', 'MON100', 'MAFANG', 'NASDAQ', 'FANG']

def classify_asset_class(symbol: str, company_name: str = "") -> str:
    """
    Classify a stock symbol into an asset class for tax purposes.
    Returns: 'EQUITY', 'GOLD_ETF', 'SILVER_ETF', 'DEBT_ETF', or 'INDEX_ETF'
    """
    sym = (symbol or "").strip().upper()
    comp = (company_name or "").strip().upper()
    combined = sym + " " + comp

    # Check debt/liquid first (most specific)
    for kw in DEBT_LIQUID_KEYWORDS:
        if kw in combined:
            return "DEBT_ETF"

    # Check gold
    for kw in GOLD_KEYWORDS:
        if kw in combined and 'GOLDIAM' not in combined and 'SKY GOLD' not in combined:
            return "GOLD_ETF"

    # Check silver
    for kw in SILVER_KEYWORDS:
        if kw in combined:
            return "SILVER_ETF"

    # Default: listed equity (includes index ETFs which have same tax treatment)
    return "EQUITY"


def get_tax_rate_info(asset_class: str, gain_type: str, is_intraday: bool = False) -> dict:
    """
    Returns tax rate details based on asset class and gain type.
    Based on Indian Income Tax rules post-Budget 2024.
    """
    if is_intraday:
        return {"tax_rate": "Slab Rate", "section": "Sec 43(5)", "label": "Speculative Business Income"}

    if asset_class == "DEBT_ETF":
        return {"tax_rate": "Slab Rate", "section": "Sec 50AA", "label": "Taxed at Slab Rate (No LTCG)"}

    if asset_class in ("GOLD_ETF", "SILVER_ETF"):
        if gain_type == "LTCG":
            return {"tax_rate": "12.5%", "section": "Sec 112", "label": "LTCG @ 12.5%"}
        else:
            return {"tax_rate": "Slab Rate", "section": "Sec 112", "label": "STCG @ Slab Rate"}

    # Listed equity / Index ETF
    if gain_type == "LTCG":
        return {"tax_rate": "12.5%", "section": "Sec 112A", "label": "LTCG @ 12.5% (₹1.25L exempt)"}
    else:
        return {"tax_rate": "20%", "section": "Sec 111A", "label": "STCG @ 20%"}

def calculate_equity_fifo_gains(
    db: Session,
    person_id: int = None,
    financial_year: str = None,
    till_date: str = None
) -> dict:
    """
    Reads pre-calculated equity FIFO gains from the EquityRealizedGain cache table.
    If cache is empty, triggers recalculate_and_cache_equity_fifo() first.
    """
    from models.equities import EquityRealizedGain, EquityRemainingLot, EquityTransaction
    from models.accounts import Account
    from models.persons import Person
    
    # Try to add the price column to remaining_lots if it doesn't exist (for PG database migration)
    try:
        from sqlalchemy import text
        db.execute(text("ALTER TABLE equities.remaining_lots ADD COLUMN IF NOT EXISTS price NUMERIC(18, 4)"))
        db.commit()
    except Exception:
        pass

    # Check if cache is populated
    has_gains = db.query(EquityRealizedGain.id).first() is not None
    if not has_gains:
        recalculate_and_cache_equity_fifo(db)
        
    # Query gains from cache
    query = db.query(
        EquityRealizedGain, 
        Person.short_name.label("owner_name"),
        Account.institution_name.label("broker_name")
    ).join(
        Person, Person.id == EquityRealizedGain.person_id
    ).outerjoin(
        Account, Account.id == EquityRealizedGain.account_id
    )
    if person_id:
        query = query.filter(EquityRealizedGain.person_id == person_id)
    if financial_year:
        query = query.filter(EquityRealizedGain.financial_year == financial_year)
    if till_date:
        if isinstance(till_date, str):
            try:
                till_date_parsed = datetime.strptime(till_date, "%Y-%m-%d").date()
            except ValueError:
                till_date_parsed = datetime.strptime(till_date, "%d-%m-%Y").date()
        else:
            till_date_parsed = till_date
        query = query.filter(EquityRealizedGain.sale_date <= till_date_parsed)
        
    cached_gains = query.order_by(EquityRealizedGain.sale_date, EquityRealizedGain.id).all()
    
    pnl_records = []
    for g, owner, broker in cached_gains:
        pnl_records.append({
            'owner': owner,
            'broker': broker or "Unknown",
            'symbol': g.symbol,
            'account_id': g.account_id,
            'company_name': g.company_name,
            'trade_type': g.trade_type,
            'purchase_date': g.purchase_date,
            'purchase_rate': float(g.purchase_rate) if g.purchase_rate else 0.0,
            'sale_date': g.sale_date,
            'sale_rate': float(g.sale_rate),
            'units_sold': float(g.units_sold),
            'cost_value': float(g.cost_value),
            'sale_value': float(g.sale_value),
            'gain_loss': float(g.gain_loss),
            'holding_days': g.holding_days,
            'gain_type': g.gain_type,
            'tax_category': g.tax_category,
            'asset_class': g.asset_class,
            'tax_rate': g.tax_rate,
            'tax_section': g.tax_section,
            'tax_label': g.tax_label,
            'financial_year': g.financial_year
        })
        
    # Build holdings from remaining lots cache
    lots_query = db.query(
        EquityTransaction.symbol,
        EquityTransaction.account_id,
        Account.person_id,
        Person.short_name.label("owner_name"),
        EquityRemainingLot.remaining_units,
        EquityRemainingLot.price.label("adjusted_price"),
        EquityTransaction.price.label("orig_price"),
        EquityTransaction.brokerage,
        EquityTransaction.quantity.label("original_qty")
    ).join(
        EquityRemainingLot, EquityRemainingLot.purchase_txn_id == EquityTransaction.id
    ).join(
        Account, Account.id == EquityTransaction.account_id
    ).join(
        Person, Person.id == Account.person_id
    )
    if person_id:
        lots_query = lots_query.filter(Account.person_id == person_id)
        
    lots = lots_query.all()
    
    holdings_dict = {}
    for symbol, account_id, pid, owner_name, rem_units, adj_price, orig_price, brokerage, original_qty in lots:
        key = (symbol, account_id)
        if key not in holdings_dict:
            holdings_dict[key] = {
                "owner": owner_name,
                "person_id": pid,
                "account_id": account_id,
                "symbol": symbol,
                "total_qty": 0.0,
                "total_cost": 0.0
            }
        rem_units = float(rem_units)
        original_qty = float(original_qty)
        price = float(adj_price if adj_price is not None else orig_price)
        brokerage = float(brokerage or 0.0)
        
        # Adjust original quantity by split ratio for correct brokerage allocation
        if adj_price is not None and float(orig_price) > 0 and float(adj_price) > 0:
            ratio = float(orig_price) / float(adj_price)
            adj_original_qty = original_qty * ratio
        else:
            adj_original_qty = original_qty
            
        lot_cost = rem_units * price
        if adj_original_qty > 0:
            lot_cost += brokerage * (rem_units / adj_original_qty)
        holdings_dict[key]["total_qty"] += rem_units
        holdings_dict[key]["total_cost"] += lot_cost
        
    holdings_records = []
    for key, h in holdings_dict.items():
        total_qty = h["total_qty"]
        if total_qty > 0.001:
            holdings_records.append({
                "owner": h["owner"],
                "person_id": h["person_id"],
                "account_id": h["account_id"],
                "symbol": h["symbol"],
                "quantity": total_qty,
                "average_cost": round(h["total_cost"] / total_qty, 4),
                "total_cost": round(h["total_cost"], 2),
                "notes": "Remaining holding"
            })
            
    return {
        "gains": pnl_records,
        "holdings": holdings_records
    }


def recalculate_and_cache_equity_fifo(db: Session):
    """
    Computes equity FIFO lot-matching for ALL active transactions globally
    and saves results to EquityRealizedGain and EquityRemainingLot cache tables.
    
    Priority order:
    1. MERGER / DEMERGER (Priority 0)
    2. SPLIT (Priority 1)
    3. BONUS (Priority 2)
    4. BUY (Priority 3)
    5. SELL (Priority 4)
    """
    from models.equities import EquityRealizedGain, EquityRemainingLot
    
    # 1. Clear existing cache
    db.query(EquityRealizedGain).delete()
    db.query(EquityRemainingLot).delete()
    db.commit()
    
    # 2. Fetch all active transactions
    query = db.query(EquityTransaction, Person.short_name.label("owner_name")).join(
        Person, Person.id == EquityTransaction.person_id
    ).filter(
        EquityTransaction.status == TransactionStatus.ACTIVE
    )
        
    txns = query.all()
    if not txns:
        return

    records = []
    for item in txns:
        t, owner = item
        records.append({
            "id": t.id,
            "owner": owner,
            "person_id": t.person_id,
            "account_id": t.account_id,
            "symbol": t.symbol.strip().upper(),
            "company_name": t.company_name,
            "date": t.transaction_date,
            "type": t.transaction_type.name,
            "quantity": float(t.quantity),
            "price": float(t.price),
            "brokerage": float(t.brokerage or 0),
            "taxes": float(t.taxes or 0),
            "stt": float(t.stt or 0),
            "net_amount": float(t.net_amount),
            "notes": t.notes or ""
        })

    from collections import defaultdict

    # Group records by (symbol, account_id)
    grouped = defaultdict(list)
    for r in records:
        key = (r["symbol"], r["account_id"])
        grouped[key].append(r)
        
    adjusted_records = []
    gains_to_insert = []
    
    for key, group_txns in grouped.items():
        symbol, account_id = key
        
        # Group by date
        by_date = defaultdict(list)
        for r in group_txns:
            by_date[r["date"]].append(r)
            
        # Process each date in chronological order
        sorted_dates = sorted(by_date.keys())
        for d in sorted_dates:
            date_txns = by_date[d]
            
            # Separate buys, sells, and other types (like split/bonus/merger/demerger)
            buys = [dict(t) for t in date_txns if t["type"] == "BUY"]
            sells = [dict(t) for t in date_txns if t["type"] == "SELL"]
            others = [dict(t) for t in date_txns if t["type"] not in ("BUY", "SELL")]
            
            # Initialize remaining quantities
            for b in buys:
                b["quantity_rem"] = b["quantity"]
            for s in sells:
                s["quantity_rem"] = s["quantity"]
                
            # If we have both buys and sells on this day, perform intraday matching first
            if buys and sells:
                b_idx = 0
                s_idx = 0
                while b_idx < len(buys) and s_idx < len(sells):
                    b = buys[b_idx]
                    s = sells[s_idx]
                    
                    if b["quantity_rem"] <= 0.0001:
                        b_idx += 1
                        continue
                    if s["quantity_rem"] <= 0.0001:
                        s_idx += 1
                        continue
                        
                    match_qty = min(b["quantity_rem"], s["quantity_rem"])
                    b["quantity_rem"] -= match_qty
                    s["quantity_rem"] -= match_qty
                    
                    # Pro-rata buy cost and sell proceeds using net_amount directly
                    match_buy_cost = b["net_amount"] * (match_qty / b["quantity"]) if b["quantity"] > 0 else 0.0
                    match_sell_proceeds = s["net_amount"] * (match_qty / s["quantity"]) if s["quantity"] > 0 else 0.0
                    match_pnl = match_sell_proceeds - match_buy_cost
                    
                    b_net_rate = b["net_amount"] / b["quantity"] if b["quantity"] > 0 else b["price"]
                    s_net_rate = s["net_amount"] / s["quantity"] if s["quantity"] > 0 else s["price"]
                    
                    # Classify asset class and tax rate
                    asset_class = classify_asset_class(symbol, b["company_name"])
                    tax_info = get_tax_rate_info(asset_class, "STCG", is_intraday=True)
                    
                    gains_to_insert.append(EquityRealizedGain(
                        person_id=b["person_id"],
                        account_id=b["account_id"],
                        purchase_txn_id=b["id"],
                        sale_txn_id=s["id"],
                        symbol=symbol,
                        company_name=b["company_name"],
                        trade_type="INTRADAY",
                        purchase_date=d,
                        purchase_rate=float(round(b_net_rate, 4)),
                        sale_date=d,
                        sale_rate=float(round(s_net_rate, 4)),
                        units_sold=float(match_qty),
                        cost_value=float(round(match_buy_cost, 2)),
                        sale_value=float(round(match_sell_proceeds, 2)),
                        gain_loss=float(round(match_pnl, 2)),
                        holding_days=0,
                        gain_type="STCG",
                        tax_category="STCG",
                        asset_class=asset_class,
                        tax_rate=tax_info["tax_rate"],
                        tax_section=tax_info["section"],
                        tax_label=tax_info["label"],
                        financial_year=get_financial_year(d)
                    ))
            
            # Construct adjusted records for the standard FIFO loop
            for b in buys:
                if b["quantity_rem"] > 0.0001:
                    ratio = b["quantity_rem"] / b["quantity"]
                    b["quantity"] = b["quantity_rem"]
                    b["brokerage"] = b["brokerage"] * ratio
                    b["taxes"] = b["taxes"] * ratio
                    b["stt"] = b["stt"] * ratio
                    b["net_amount"] = b["net_amount"] * ratio
                    adjusted_records.append(b)
            for s in sells:
                if s["quantity_rem"] > 0.0001:
                    ratio = s["quantity_rem"] / s["quantity"]
                    s["quantity"] = s["quantity_rem"]
                    s["brokerage"] = s["brokerage"] * ratio
                    s["taxes"] = s["taxes"] * ratio
                    s["stt"] = s["stt"] * ratio
                    s["net_amount"] = s["net_amount"] * ratio
                    adjusted_records.append(s)
            for o in others:
                adjusted_records.append(o)

    df = pd.DataFrame(adjusted_records)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date']).dt.date
    else:
        # Create empty DataFrame with expected columns
        df = pd.DataFrame(columns=records[0].keys() if records else [])

    def get_priority(t_type):
        if t_type in ['MERGER', 'DEMERGER']: return 0
        if t_type == 'SPLIT': return 1
        if t_type == 'BONUS': return 2
        if t_type == 'BUY': return 3
        if t_type == 'SELL': return 4
        return 5

    df['Priority'] = df['type'].apply(get_priority)
    df = df.sort_values(by=['date', 'Priority', 'id'])

    inventory = {}
    pending_merged_cost = {}
    pending_shorts = {}

    for _, row in df.iterrows():
        symbol = row['symbol']
        account_id = row['account_id']
        key = (symbol, account_id)
        
        trans_type = row['type']
        qty = row['quantity']
        price = row['price']
        amount = row['net_amount']
        trade_date = row['date']
        owner = row['owner']

        if pd.isna(qty) or qty <= 0:
            continue

        if key not in inventory:
            inventory[key] = []
        if key not in pending_merged_cost:
            pending_merged_cost[key] = 0.0
        if key not in pending_shorts:
            pending_shorts[key] = []

        # --- PROCESS BUY / DEMERGER NEW ---
        if trans_type == 'BUY':
            # Use net unit price (inclusive of brokerage & taxes)
            net_unit_price = amount / qty if qty > 0 else price
            buy_qty_rem = qty
            
            # Check if there are pending short sales (e.g. auction / next-day contract note trades)
            if pending_shorts[key]:
                rem_shorts = []
                for short in pending_shorts[key]:
                    days_diff = (trade_date - short['sale_date']).days
                    if buy_qty_rem > 0.0001 and 0 <= days_diff <= 15:
                        match_qty = min(buy_qty_rem, short['remaining_qty'])
                        chunk_sell_proceeds = match_qty * short['unit_sell_proceeds']
                        chunk_buy_cost = match_qty * net_unit_price
                        chunk_pnl = chunk_sell_proceeds - chunk_buy_cost
                        
                        # Classified as DELIVERY (STCG) per tax guidelines & user directive
                        tax_info = get_tax_rate_info(short['asset_class'], 'STCG', is_intraday=False)
                        gains_to_insert.append(EquityRealizedGain(
                            person_id=short['person_id'],
                            account_id=short['account_id'],
                            purchase_txn_id=row['id'],
                            sale_txn_id=short['sale_txn_id'],
                            symbol=symbol,
                            company_name=short['company_name'],
                            trade_type='DELIVERY',
                            purchase_date=trade_date,
                            purchase_rate=float(round(net_unit_price, 4)),
                            sale_date=short['sale_date'],
                            sale_rate=short['sale_rate'],
                            units_sold=float(match_qty),
                            cost_value=float(round(chunk_buy_cost, 2)),
                            sale_value=float(round(chunk_sell_proceeds, 2)),
                            gain_loss=float(round(chunk_pnl, 2)),
                            holding_days=0,
                            gain_type='STCG',
                            tax_category='STCG',
                            asset_class=short['asset_class'],
                            tax_rate=tax_info['tax_rate'],
                            tax_section=tax_info['section'],
                            tax_label=tax_info['label'],
                            financial_year=short['financial_year']
                        ))
                        buy_qty_rem -= match_qty
                        short['remaining_qty'] -= match_qty
                    if short['remaining_qty'] > 0.0001:
                        rem_shorts.append(short)
                pending_shorts[key] = rem_shorts
                
            if buy_qty_rem > 0.0001:
                inventory[key].append({
                    'id': row['id'],
                    'date': trade_date,
                    'qty': buy_qty_rem,
                    'price': net_unit_price,
                    'original_qty': qty
                })

        # --- PROCESS BONUS ---
        elif trans_type == 'BONUS':
            inventory[key].append({
                'id': row['id'],
                'date': trade_date,
                'qty': qty,
                'price': 0.0,
                'original_qty': qty
            })

        # --- PROCESS MERGER (REMOVE SHARES AND SAVE COST BASIS) ---
        elif trans_type == 'MERGER':
            qty_to_remove = qty
            removed_cost = 0.0
            
            while qty_to_remove > 0 and inventory[key]:
                buy_txn = inventory[key][0]
                if buy_txn['qty'] > qty_to_remove:
                    consumed_qty = qty_to_remove
                    removed_cost += consumed_qty * buy_txn['price']
                    buy_txn['qty'] -= consumed_qty
                    qty_to_remove = 0
                else:
                    consumed_qty = buy_txn['qty']
                    removed_cost += consumed_qty * buy_txn['price']
                    qty_to_remove -= consumed_qty
                    inventory[key].pop(0)
                    
            pending_merged_cost[key] += removed_cost

        # --- PROCESS DEMERGER (REDUCE COST BASIS OF PARENT STOCK) ---
        elif trans_type == 'DEMERGER':
            # Amount column holds the new cost basis of parent stock lot
            current_total_cost = sum(item['qty'] * item['price'] for item in inventory[key])
                
            if current_total_cost > 0:
                ratio = amount / current_total_cost
                for item in inventory[key]:
                    item['price'] *= ratio

        # --- PROCESS SPLIT (RE-SCALE LOT SIZES) ---
        elif trans_type == 'SPLIT':
            current_total_qty = sum(item['qty'] for item in inventory[key])
            target_total_qty = qty
            
            if current_total_qty > 0:
                ratio = target_total_qty / current_total_qty
                for item in inventory[key]:
                    item['qty'] *= ratio
                    item['price'] /= ratio
                    item['original_qty'] *= ratio
            else:
                if pending_merged_cost[key] > 0:
                    cost_basis = pending_merged_cost[key]
                    pending_merged_cost[key] = 0.0
                    unit_price = cost_basis / target_total_qty
                    inventory[key].append({
                        'id': row['id'],
                        'date': trade_date,
                        'qty': target_total_qty,
                        'price': unit_price,
                        'original_qty': target_total_qty
                    })
                else:
                    inventory[key].append({
                        'id': row['id'],
                        'date': trade_date,
                        'qty': target_total_qty,
                        'price': 0.0,
                        'original_qty': target_total_qty
                    })
                    
        # SELL
        elif trans_type == 'SELL':
            qty_to_sell = qty
            matched_txns = []
            while qty_to_sell > 0:
                if not inventory[key]:
                    break
                buy_txn = inventory[key][0]
                if buy_txn['qty'] > qty_to_sell:
                    consumed_qty = qty_to_sell
                    matched_txns.append({
                        'id': buy_txn['id'],
                        'qty': consumed_qty,
                        'buy_price': buy_txn['price'],
                        'buy_date': buy_txn['date']
                    })
                    buy_txn['qty'] -= consumed_qty
                    qty_to_sell = 0.0
                else:
                    consumed_qty = buy_txn['qty']
                    matched_txns.append({
                        'id': buy_txn['id'],
                        'qty': consumed_qty,
                        'buy_price': buy_txn['price'],
                        'buy_date': buy_txn['date']
                    })
                    qty_to_sell -= consumed_qty
                    inventory[key].pop(0)
                    
            sell_proceeds = amount
            sell_fy = get_financial_year(trade_date)
            asset_class = classify_asset_class(symbol, row['company_name'])
            
            # Net unit sell rate (inclusive of charges)
            net_sell_rate = sell_proceeds / qty if qty > 0 else price
            
            # Create a separate realized gain record per matched buy lot
            # This ensures each lot gets the correct buy date, holding days,
            # intraday classification, and tax treatment
            for m in matched_txns:
                chunk_buy_cost = m['qty'] * m['buy_price']
                chunk_sell_proceeds = sell_proceeds * (m['qty'] / qty) if qty > 0 else 0.0
                chunk_pnl = chunk_sell_proceeds - chunk_buy_cost
                
                # Per-lot intraday: only if THIS specific lot was bought on the same day
                lot_is_intraday = (m['buy_date'] == trade_date) if m['buy_date'] is not None else False
                
                # Per-lot holding period and gain type
                if m['buy_date'] is not None:
                    lot_holding_days = (trade_date - m['buy_date']).days
                    lot_is_long_term = lot_holding_days > 365
                else:
                    lot_holding_days = 0
                    lot_is_long_term = False
                
                lot_gain_type = 'LTCG' if lot_is_long_term else 'STCG'
                lot_tax_info = get_tax_rate_info(asset_class, lot_gain_type, lot_is_intraday)
                
                gains_to_insert.append(EquityRealizedGain(
                    person_id=row['person_id'],
                    account_id=row['account_id'],
                    purchase_txn_id=m['id'],
                    sale_txn_id=row['id'],
                    symbol=symbol,
                    company_name=row['company_name'],
                    trade_type='INTRADAY' if lot_is_intraday else 'DELIVERY',
                    purchase_date=m['buy_date'],
                    purchase_rate=float(round(m['buy_price'], 4)),
                    sale_date=trade_date,
                    sale_rate=float(round(net_sell_rate, 4)),
                    units_sold=float(m['qty']),
                    cost_value=float(round(chunk_buy_cost, 2)),
                    sale_value=float(round(chunk_sell_proceeds, 2)),
                    gain_loss=float(round(chunk_pnl, 2)),
                    holding_days=lot_holding_days,
                    gain_type=lot_gain_type,
                    tax_category=lot_gain_type,
                    asset_class=asset_class,
                    tax_rate=lot_tax_info['tax_rate'],
                    tax_section=lot_tax_info['section'],
                    tax_label=lot_tax_info['label'],
                    financial_year=sell_fy
                ))
            
            # If any quantity remains unmatched, buffer in pending_shorts for next-day match
            if qty_to_sell > 0.0001:
                pending_shorts[key].append({
                    'person_id': row['person_id'],
                    'account_id': row['account_id'],
                    'sale_txn_id': row['id'],
                    'symbol': symbol,
                    'company_name': row['company_name'],
                    'sale_date': trade_date,
                    'sale_rate': float(round(net_sell_rate, 4)),
                    'orig_sale_qty': qty,
                    'remaining_qty': qty_to_sell,
                    'unit_sell_proceeds': sell_proceeds / qty if qty > 0 else net_sell_rate,
                    'asset_class': asset_class,
                    'financial_year': sell_fy
                })

    # Flush any unclosed pending shorts as unmatched sales (purchase_txn_id=None)
    for key, shorts in pending_shorts.items():
        for short in shorts:
            if short['remaining_qty'] > 0.0001:
                unmatched_proceeds = short['remaining_qty'] * short['unit_sell_proceeds']
                tax_info = get_tax_rate_info(short['asset_class'], 'STCG', is_intraday=False)
                gains_to_insert.append(EquityRealizedGain(
                    person_id=short['person_id'],
                    account_id=short['account_id'],
                    purchase_txn_id=None,
                    sale_txn_id=short['sale_txn_id'],
                    symbol=short['symbol'],
                    company_name=short['company_name'],
                    trade_type='DELIVERY',
                    purchase_date=None,
                    purchase_rate=0.0,
                    sale_date=short['sale_date'],
                    sale_rate=short['sale_rate'],
                    units_sold=float(short['remaining_qty']),
                    cost_value=0.0,
                    sale_value=float(round(unmatched_proceeds, 2)),
                    gain_loss=float(round(unmatched_proceeds, 2)),
                    holding_days=0,
                    gain_type='STCG',
                    tax_category='STCG',
                    asset_class=short['asset_class'],
                    tax_rate=tax_info['tax_rate'],
                    tax_section=tax_info['section'],
                    tax_label=tax_info['label'],
                    financial_year=short['financial_year']
                ))

    # Save active remaining units of purchase lots
    lots_to_insert = []
    for (symbol, account_id), batches in inventory.items():
        for b in batches:
            if b['qty'] > 0.001:
                lots_to_insert.append(EquityRemainingLot(
                    purchase_txn_id=b['id'],
                    remaining_units=float(b['qty']),
                    price=float(b['price'])
                ))
                
    db.bulk_save_objects(gains_to_insert)
    db.bulk_save_objects(lots_to_insert)
    db.commit()

def update_equity_holdings_in_db(db: Session, person_id: int = None):
    """
    Recalculates current equity holdings using the FIFO engine snapshot,
    and updates the equities.holdings table.
    """
    # Delete current holdings in scope
    query = db.query(EquityHolding)
    if person_id:
        query = query.filter(EquityHolding.person_id == person_id)
    query.delete()
    
    # Calculate fresh holdings snapshot using the engine
    snapshot = calculate_equity_fifo_gains(db, person_id=person_id)
    holdings = snapshot["holdings"]
    
    today_date = datetime.today().date()
    
    for h in holdings:
        # Default last_price to average_cost (will be synced to market price below)
        last_price = h["average_cost"]
        current_val = h["quantity"] * last_price
        
        db_holding = EquityHolding(
            person_id=h["person_id"],
            account_id=h["account_id"],
            symbol=h["symbol"],
            quantity=h["quantity"],
            average_cost=h["average_cost"],
            last_price=last_price,
            last_price_date=today_date,
            current_value=current_val,
            as_of_date=today_date
        )
        db.add(db_holding)
        
    db.commit()
    
    # Sync prices from feather file if available
    sync_equity_prices_from_feather(db, person_id=person_id)

def sync_equity_prices_from_feather(db: Session, person_id: int = None):
    """
    Loads latest market prices from DATA/portfolio_prices.feather 
    and updates CMP and Current Value for all active equity holdings.
    """
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    price_path = os.path.join(base_dir, "DATA", "portfolio_prices.feather")
    
    if not os.path.exists(price_path):
        print(f"Warning: portfolio_prices.feather not found at {price_path}. Skipping price sync.")
        return
        
    try:
        df = pd.read_feather(price_path)
        if df.empty:
            return
            
        # Get latest row
        latest_date = pd.to_datetime(df.iloc[-1].get('ts')).date() if 'ts' in df.columns else datetime.today().date()
        
        # Get all holdings
        query = db.query(EquityHolding)
        if person_id:
            query = query.filter(EquityHolding.person_id == person_id)
        holdings = query.all()
        
        updated_count = 0
        for h in holdings:
            symbol = h.symbol.strip().upper()
            
            # Find matching column - try multiple Fyers symbol patterns
            col = None
            symbol_patterns = [
                symbol,
                f"NSE:{symbol}-EQ",
                f"NSE:{symbol}-RR",
                f"NSE:{symbol}-BE",
                f"BSE:{symbol}-EQ",
                f"BSE:{symbol}-A",
                f"BSE:{symbol}-B",
            ]
            
            for pattern in symbol_patterns:
                for c in df.columns:
                    if c.upper() == pattern:
                        col = c
                        break
                if col:
                    break
            
            if not col:
                # Fallback: search case insensitive for symbol inside column name
                for c in df.columns:
                    if symbol in c.upper():
                        col = c
                        break
            
            if col:
                # Get last non-null value for this column
                non_nulls = df[col].dropna()
                if not non_nulls.empty:
                    mkt_price = float(non_nulls.iloc[-1])
                    if mkt_price > 0:
                        h.last_price = mkt_price
                        h.current_value = float(h.quantity) * mkt_price
                        h.last_price_date = latest_date
                        updated_count += 1
                        
        db.commit()
        print(f"Successfully synced market prices for {updated_count} equity holdings.")
    except Exception as e:
        print(f"Error syncing equity prices from feather: {e}")
