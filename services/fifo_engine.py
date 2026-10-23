import re
import pandas as pd
from datetime import datetime, date
from sqlalchemy.orm import Session
from models.mutual_funds import MFTransaction, MFDetail
from models.persons import Person
from models.enums import TransactionStatus

_remaining_units_cache = None
_advisor_cache_mf_details = None
_advisor_cache_folios = None
_excel_advisor_map = None
_advisor_cache_folio_owners = None

# --- Open-Core Showcase Edition ---
# Proprietary physical advisor maps and client folio tables are replaced
# with dynamic channel resolution for the public showcase edition.

FOLIO_ADVISOR_MAP = {}

def resolve_advisor(scheme_name: str, folio_number: str = None, notes_str: str = None, db = None, db_detail = None, owner_short_name: str = None) -> str:
    """
    Resolves distributor/advisor attribution for mutual fund folios.
    Direct plans are always mapped to 'DIRECT'. Regular plans are mapped
    to institutional sample broker entities.
    """
    scheme_upper = str(scheme_name or '').upper()
    if 'DIRECT' in scheme_upper:
        return 'DIRECT'
        
    notes_upper = str(notes_str or '').upper()
    if 'NEXUS' in notes_upper:
        return 'Nexus Capital Finverse'
    elif 'PRIME' in notes_upper:
        return 'Prime Wealth Securities'
        
    if folio_number:
        clean_f = str(folio_number).split('/')[0].strip()
        val = sum(ord(c) for c in clean_f)
        return 'Prime Wealth Securities' if val % 2 == 0 else 'Nexus Capital Finverse'
        
    return 'Prime Wealth Securities'


def invalidate_remaining_units_cache(db: Session = None):
    global _remaining_units_cache, _advisor_cache_mf_details, _advisor_cache_folios, _excel_advisor_map, _advisor_cache_folio_owners
    _remaining_units_cache = None
    _advisor_cache_mf_details = None
    _advisor_cache_folios = None
    _excel_advisor_map = None
    _advisor_cache_folio_owners = None

    # Invalidate and clear database cache tables
    from database import SessionLocal
    from models.mutual_funds import MFRealizedGain, MFRemainingLot
    
    local_session = False
    if db is None:
        db = SessionLocal()
        local_session = True
        
    try:
        db.query(MFRealizedGain).delete()
        db.query(MFRemainingLot).delete()
        db.commit()
        print("FIFO Engine: Cleared database cache tables (MFRealizedGain and MFRemainingLot) successfully.")
    except Exception as e:
        print(f"FIFO Engine: Error clearing database cache tables: {e}")
    finally:
        if local_session:
            db.close()

def get_financial_year(dt: date) -> str:
    """Returns the financial year (April 1 to March 31) for a given date in YYYY-YYYY format."""
    if dt is None:
        return "N/A"
    year = dt.year
    if dt.month < 4:
        return f"{year-1}-{year}"
    else:
        return f"{year}-{year+1}"

def get_fund_type(scheme_name: str) -> str:
    """Classifies a scheme name into types for tax grouping (Liquid, Arbitrage, Bullion, Equity, etc.)."""
    name = str(scheme_name).upper()
    if re.search(r'GOLD|SILVER', name):
        return 'BULLION'
    if re.search(r'LIQUID|ULTRA SHORT|SHORT TERM|LOW DURATION|MONEY MARKET|OVERNIGHT', name):
        return 'LIQUID'
    if re.search(r'ARBITRAGE', name):
        return 'ARBITRAGE'
    return 'EQUITY'

def get_tax_category(gain_type: str, fund_type: str) -> str:
    """Categorizes the tax rate and slab based on the gain type and fund type."""
    if gain_type == 'LTCG':
        return 'LTCG @ 12.5%'
    else:
        # STCG
        f_type = str(fund_type).upper()
        if f_type in ['BULLION', 'GOLD', 'SILVER'] or 'GOLD' in f_type or 'SILVER' in f_type:
            return 'STCG Bullion'
        elif f_type in ['LIQUID', 'ROW', 'LOW DURATION', 'ULTRA SHORT DURATION', 'SHORT DURATION', 'MONEY MARKET', 'OVERNIGHT', 'FLOATER', 'CORPORATE BOND', 'DEBT']:
            return 'STCG Slab Rate'
        else:
            return 'STCG @ 20%'

def calculate_fifo_gains(db: Session, person_id: int = None, financial_year: str = None, till_date: str = None, from_date: str = None) -> list:
    """
    Retrieves mutual fund transactions from the database cache,
    and returns a list of capital gain records filtered by financial year or date range if specified.
    """
    from models.mutual_funds import MFRealizedGain
    from models.persons import Person
    
    # Check if cached gains exist in table
    has_gains = db.query(MFRealizedGain.id).first() is not None
    if not has_gains:
        # Warm up the cache by running FIFO lot matching globally
        recalculate_and_cache_mf_fifo(db)
        
    # Query from cache
    query = db.query(MFRealizedGain, Person.short_name.label("owner_name")).join(
        Person, Person.id == MFRealizedGain.person_id
    )
    
    if person_id:
        if isinstance(person_id, (list, tuple, set)):
            query = query.filter(MFRealizedGain.person_id.in_(person_id))
        else:
            query = query.filter(MFRealizedGain.person_id == person_id)
    if financial_year:
        query = query.filter(MFRealizedGain.financial_year == financial_year)
    if from_date:
        if isinstance(from_date, str):
            try:
                from_date_parsed = datetime.strptime(from_date, "%Y-%m-%d").date()
            except ValueError:
                try:
                    from_date_parsed = datetime.strptime(from_date, "%d-%m-%Y").date()
                except ValueError:
                    from_date_parsed = None
        else:
            from_date_parsed = from_date
        if from_date_parsed:
            query = query.filter(MFRealizedGain.sale_date >= from_date_parsed)
    if till_date:
        if isinstance(till_date, str):
            try:
                till_date_parsed = datetime.strptime(till_date, "%Y-%m-%d").date()
            except ValueError:
                try:
                    till_date_parsed = datetime.strptime(till_date, "%d-%m-%Y").date()
                except ValueError:
                    till_date_parsed = datetime.today().date()
        else:
            till_date_parsed = till_date
        query = query.filter(MFRealizedGain.sale_date <= till_date_parsed)
        
    cached_gains = query.order_by(MFRealizedGain.sale_date, MFRealizedGain.id).all()
    
    # Format back to expected dictionary list
    res = []
    for g, owner in cached_gains:
        res.append({
            "owner": owner,
            "folio": g.folio_number,
            "scheme": g.scheme_name,
            "advisor": g.advisor,
            "purchase_date": g.purchase_date,
            "purchase_rate": float(g.purchase_rate) if g.purchase_rate else 0.0,
            "sale_date": g.sale_date,
            "sale_rate": float(g.sale_rate),
            "units_sold": float(g.units_sold),
            "cost_value": float(g.cost_value),
            "sale_value": float(g.sale_value),
            "gain_loss": float(g.gain_loss),
            "holding_days": g.holding_days,
            "gain_type": 'LTCG' if str(g.tax_category).startswith('LTCG') else 'STCG',
            "tax_category": g.tax_category,
            "financial_year": g.financial_year
        })
    return res


def get_purchase_lots_remaining_units(db: Session) -> dict:
    """
    Returns a dictionary of {transaction_id: remaining_units} from cached remaining lots,
    including 0.0 for fully sold active purchase transactions.
    """
    global _remaining_units_cache
    if _remaining_units_cache is not None:
        return _remaining_units_cache
        
    from models.mutual_funds import MFRemainingLot, MFTransaction
    from models.enums import TransactionStatus
    
    cached = db.query(MFRemainingLot).all()
    if not cached:
        recalculate_and_cache_mf_fifo(db)
        cached = db.query(MFRemainingLot).all()
        
    # Start with all active purchase transactions set to 0.0
    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT']
    all_purchases = db.query(MFTransaction.id).filter(
        MFTransaction.status == TransactionStatus.ACTIVE,
        MFTransaction.transaction_type.in_(purchase_types)
    ).all()
    
    res = {txn_id: 0.0 for (txn_id,) in all_purchases}
    
    # Override with actual remaining units
    for lot in cached:
        res[lot.purchase_txn_id] = float(lot.remaining_units)
        
    _remaining_units_cache = res
    return _remaining_units_cache


def recalculate_and_cache_mf_fifo(db: Session):
    """
    Computes mutual fund FIFO lot-matching for the entire database and saves the results
    to MFRealizedGain and MFRemainingLot tables to cache calculations.
    """
    from models.mutual_funds import MFRealizedGain, MFRemainingLot, MFTransaction, MFDetail
    from models.persons import Person
    
    # Pre-fetch mf_details for custom tenure and advisor overrides
    details_rows = db.query(MFDetail).all()
    details_map = {d.scheme_name.upper(): d for d in details_rows}

    # 1. Clear existing cache
    db.query(MFRealizedGain).delete()
    db.query(MFRemainingLot).delete()
    db.commit()
    
    # 2. Run the FIFO lot-matching on all active transactions globally
    txns = db.query(MFTransaction, Person.short_name.label("owner_name"), Person.id.label("person_id")).join(
        Person, Person.id == MFTransaction.person_id
    ).filter(
        MFTransaction.status == TransactionStatus.ACTIVE
    ).order_by(MFTransaction.transaction_date, MFTransaction.id).all()
    
    if not txns:
        return
        
    rows = []
    for item in txns:
        t, owner, pid = item
        advisor = resolve_advisor(t.scheme_name, t.folio_number, t.notes, db, owner_short_name=owner)
        rows.append({
            "id": t.id,
            "person_id": pid,
            "owner": owner,
            "folio": t.folio_number,
            "scheme": t.scheme_name,
            "advisor": advisor,
            "isin": t.isin,
            "amfi": t.amfi_code,
            "date": t.transaction_date,
            "amount": float(t.amount),
            "units": float(t.units),
            "nav": float(t.nav),
            "type": t.transaction_type.upper(),
            "description": t.notes or ""
        })
        
    # Reversal neutralization
    purchases_by_group = {}
    for r in rows:
        if r['type'] in ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN']:
            key = (r['scheme'], r['folio'], r['advisor'])
            purchases_by_group.setdefault(key, []).append(r)
            
    neutralized_ids = set()
    reversal_keywords = ['payment not received', 'unrealised', 'rejected']
    
    for r in rows:
        is_reversal = r['type'] == 'REVERSAL'
        if not is_reversal and r['type'] == 'REDEMPTION':
            desc = r['description'].lower()
            if any(kw in desc for kw in reversal_keywords):
                is_reversal = True
                
        if is_reversal:
            key = (r['scheme'], r['folio'], r['advisor'])
            candidates = purchases_by_group.get(key, [])
            match_found = False
            for p in candidates:
                if p['id'] in neutralized_ids:
                    continue
                days_diff = abs((r['date'] - p['date']).days)
                if days_diff <= 5 and abs(p['amount'] - abs(r['amount'])) < 0.01:
                    neutralized_ids.add(r['id'])
                    neutralized_ids.add(p['id'])
                    match_found = True
                    break
            if not match_found:
                neutralized_ids.add(r['id'])
                
    filtered_rows = [r for r in rows if r['id'] not in neutralized_ids]
    
    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVESTMENT']
    sale_types = ['REDEMPTION', 'SWITCH_OUT']
    stt_types = ['STT_TAX', 'STT_PAID', 'STT']
    
    stt_by_group = {}
    for r in filtered_rows:
        if r['type'] in stt_types:
            key = (r['date'], r['scheme'], r['folio'])
            stt_by_group.setdefault(key, []).append(r)
    
    purchases_list = []
    sales_list = []
    
    sales_by_group_count = {}
    for r in filtered_rows:
        if r['type'] in purchase_types:
            r['remaining_units'] = r['units']
            purchases_list.append(r)
        elif r['type'] in sale_types:
            # Match STT
            key = (r['date'], r['scheme'], r['folio'])
            idx = sales_by_group_count.get(key, 0)
            sales_by_group_count[key] = idx + 1
            
            stt_list = stt_by_group.get(key, [])
            stt_amt = 0.0
            if idx < len(stt_list):
                stt_amt = float(stt_list[idx]['amount'] or 0.0)
            else:
                # Fallback: check if units * nav > abs(amount) and description has STT
                calc_val = round(abs(r['units']) * r['nav'], 2)
                diff = round(calc_val - abs(r['amount']), 2)
                if diff > 0.01 and diff < 10000 and any(w in r['description'].upper() for w in ['LESS STT', 'STT']):
                    stt_amt = diff
            r['stt_amount'] = stt_amt
            sales_list.append(r)
            
    purchases_by_group = {}
    for p in purchases_list:
        key = (p['scheme'], p['folio'])
        purchases_by_group.setdefault(key, []).append(p)
        
    sales_by_group = {}
    for s in sales_list:
        key = (s['scheme'], s['folio'])
        sales_by_group.setdefault(key, []).append(s)
        
    gains_to_insert = []
    
    for key, group_sales in sales_by_group.items():
        group_sales = sorted(group_sales, key=lambda x: (x['date'], x['id']))
        group_purchases = purchases_by_group.get(key, [])
        group_purchases = sorted(group_purchases, key=lambda x: (x['date'], x['id']))
        
        purchase_pointer = 0
        num_purchases = len(group_purchases)
        
        for sale in group_sales:
            units_to_sell = abs(sale['units'])
            total_sale_units = abs(sale['units'])
            sale_date = sale['date']
            sale_stt_amt = float(sale.get('stt_amount', 0.0))
            gross_sale_amt = abs(sale['amount']) + sale_stt_amt
            
            while units_to_sell > 0.001 and purchase_pointer < num_purchases:
                purchase = group_purchases[purchase_pointer]
                if purchase['date'] > sale_date:
                    break
                if purchase['remaining_units'] <= 0.001:
                    purchase_pointer += 1
                    continue
                    
                units_from_lot = min(units_to_sell, purchase['remaining_units'])
                sale_fy = get_financial_year(sale_date)
                cost_of_lot = (units_from_lot / purchase['units']) * purchase['amount'] if purchase['units'] != 0 else 0
                sale_value_of_lot = (units_from_lot / total_sale_units) * gross_sale_amt if total_sale_units != 0 else 0
                stt_of_lot = (units_from_lot / total_sale_units) * sale_stt_amt if total_sale_units != 0 else 0
                gain_loss = sale_value_of_lot - cost_of_lot - stt_of_lot
                
                holding_period = (sale_date - purchase['date']).days
                scheme_upper = str(sale['scheme']).upper()
                db_detail = details_map.get(scheme_upper)
                if db_detail and db_detail.long_term_tenure:
                    lt_tenure = int(db_detail.long_term_tenure)
                elif re.search(r'GOLD|SILVER', scheme_upper):
                    lt_tenure = 730
                else:
                    lt_tenure = 365

                gain_type = 'LTCG' if holding_period >= lt_tenure else 'STCG'
                
                fund_type = db_detail.fund_type if (db_detail and db_detail.fund_type) else get_fund_type(sale['scheme'])
                tax_category = get_tax_category(gain_type, fund_type)
                
                gains_to_insert.append(MFRealizedGain(
                    person_id=sale['person_id'],
                    purchase_txn_id=purchase['id'],
                    sale_txn_id=sale['id'],
                    folio_number=sale['folio'],
                    scheme_name=sale['scheme'],
                    advisor=sale['advisor'],
                    isin=sale['isin'],
                    amfi_code=sale['amfi'],
                    purchase_date=purchase['date'],
                    purchase_rate=float(purchase['nav']),
                    sale_date=sale_date,
                    sale_rate=float(sale['nav']),
                    units_sold=float(units_from_lot),
                    cost_value=float(cost_of_lot),
                    sale_value=float(sale_value_of_lot),
                    gain_loss=float(round(gain_loss, 2)),
                    holding_days=holding_period,
                    tax_category=tax_category,
                    financial_year=sale_fy
                ))
                
                purchase['remaining_units'] -= units_from_lot
                units_to_sell -= units_from_lot
                
                if purchase['remaining_units'] <= 0.001:
                    purchase_pointer += 1
                    
    # Save active remaining units of purchase lots
    lots_to_insert = []
    for p in purchases_list:
        if p['remaining_units'] > 0.001:
            lots_to_insert.append(MFRemainingLot(
                purchase_txn_id=p['id'],
                remaining_units=float(p['remaining_units'])
            ))
            
    # Perform bulk inserts
    db.bulk_save_objects(gains_to_insert)
    db.bulk_save_objects(lots_to_insert)
    db.commit()
    
    # Invalidate in-memory cache to force refresh on next retrieval
    global _remaining_units_cache
    _remaining_units_cache = None


