import re
from sqlalchemy.orm import Session
from models.mutual_funds import MFDetail
from services.fifo_engine import calculate_fifo_gains
from services.aum_performance_engine import AUM_FUND_TYPE_CATEGORIES, expand_category_to_types

def extract_fund_type_fallback(name: str) -> str:
    """Fallback classification if scheme not found in MFDetail."""
    name_upper = name.upper()
    if re.search(r'GOLD|SILVER', name_upper):
        return 'BULLION'
    if re.search(r'LIQUID|ULTRA SHORT|SHORT TERM|LOW DURATION|MONEY MARKET|OVERNIGHT', name_upper):
        return 'LIQUID'
    if re.search(r'ARBITRAGE', name_upper):
        return 'ARBITRAGE'
    return 'EQUITY'

def get_monthly_profits_report(db: Session, person_id: int = None, advisor: str = "ALL", fund_type = "ALL") -> dict:
    """
    Computes realized booked profits (realized capital gains) grouped by Year and Month,
    filtered by Account/Person, Advisor, and Fund Type.
    """
    # 1. Fetch ALL realized gains from the FIFO engine (with person filter applied at query level)
    gains = calculate_fifo_gains(db, person_id=person_id)
    
    # 2. Fetch all scheme details to build lookup mapping and filter option lists
    details = db.query(MFDetail).all()
    scheme_map = {}
    
    all_advisors = set()
    
    for d in details:
        adv = (d.advisor or "DIRECT").strip()
        ft = (d.fund_type or "EQUITY").strip()
        
        scheme_map[d.scheme_name.upper()] = (adv, ft)
        
        # Parse and add to advisors set
        if adv:
            for a in adv.split(","):
                val = a.strip()
                if val:
                    all_advisors.add(val)

                
    # Normalize filters for case-insensitive matching
    sel_adv = advisor.strip().upper()
    
    # Normalize fund types to a set of uppercase strings
    if fund_type is None:
        selected_types = {"ALL"}
    elif isinstance(fund_type, str):
        selected_types = {x.strip().upper() for x in fund_type.split(",") if x.strip()}
    else:
        selected_types = {str(x).strip().upper() for x in fund_type if str(x).strip()}
        
    if not selected_types:
        selected_types = {"ALL"}
    
    # Expand super-categories (EQUITY, LIQUID, BULLION) to granular types for matching
    if "ALL" not in selected_types:
        expanded_types = expand_category_to_types(selected_types)
        expanded_types.update(selected_types)
    else:
        expanded_types = selected_types
    
    filtered_gains = []
    
    # 3. Filter gains based on Advisor and Fund Type
    for g in gains:
        scheme_name = g["scheme"]
        scheme_key = scheme_name.upper()
        
        if scheme_key in scheme_map:
            _, ft = scheme_map[scheme_key]
        else:
            ft = extract_fund_type_fallback(scheme_name)
            
        adv = g.get("advisor", "DIRECT")
        
        # Ensure this advisor is in the overall set of advisors for dropdown display
        if adv:
            for a in adv.split(","):
                val = a.strip()
                if val:
                    all_advisors.add(val)
            
        # Apply Advisor Filter
        if sel_adv != "ALL":
            adv_list = [a.strip().upper() for a in adv.split(",")]
            if sel_adv not in adv_list:
                continue
                
        # Apply Fund Type Filter (supports super-categories expanded to granular types)
        if "ALL" not in selected_types:
            if ft.upper() not in expanded_types:
                continue
                
        filtered_gains.append(g)
        
    # 4. Aggregate by Year and Month
    profits_by_year_month = {}
    yearly_totals = {}
    monthly_totals = {m: 0.0 for m in range(1, 13)}
    grand_total = 0.0
    
    # Summary Metrics (Realized)
    total_realized = 0.0
    total_stcg = 0.0
    total_ltcg = 0.0
    
    for g in filtered_gains:
        sale_date = g["sale_date"]
        year = sale_date.year
        month = sale_date.month
        gain_loss = g["gain_loss"]
        
        if year not in profits_by_year_month:
            profits_by_year_month[year] = {m: 0.0 for m in range(1, 13)}
            yearly_totals[year] = 0.0
            
        profits_by_year_month[year][month] += gain_loss
        yearly_totals[year] += gain_loss
        monthly_totals[month] += gain_loss
        grand_total += gain_loss
        
        # Realized breakdown
        total_realized += gain_loss
        if g["gain_type"] == "STCG":
            total_stcg += gain_loss
        elif g["gain_type"] == "LTCG":
            total_ltcg += gain_loss
            
    # Sort years in descending order
    years = sorted(list(profits_by_year_month.keys()), reverse=True)
    
    # Map months to standard names
    month_names = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
    }
    
    return {
        "years": years,
        "profits_by_year_month": profits_by_year_month,
        "yearly_totals": yearly_totals,
        "monthly_totals": monthly_totals,
        "grand_total": grand_total,
        "total_realized": total_realized,
        "total_stcg": total_stcg,
        "total_ltcg": total_ltcg,
        "advisors": sorted(list(all_advisors)),
        "fund_types": AUM_FUND_TYPE_CATEGORIES,  # Simplified 3-category filter: EQUITY, LIQUID, BULLION
        "month_names": month_names
    }
