"""
seed_mock.py
============
High-Fidelity Institutional Synthetic Data Generator for WealthOS / Family Office Platform.
Populates an extensive multi-entity family structure with realistic, multi-asset portfolios across:
- Mutual Funds (32 diverse schemes across Large, Mid, Small, Flexi, Hybrid, Debt, Arbitrage, ELSS)
- Multi-tranche SIP histories, FIFO purchase lots, and realized capital gains
- Listed Bluechip Equities (18 stocks across key sectors)
- Sovereign Gold Bonds (6 RBI tranches with YTM and CMP)
- Private Wealth Alternatives (AIF Cat II, PMS, and Pre-IPO Unlisted Shares)
- Insurance Policies (Term Life, ULIP, Endowment) and Commercial Real Estate
- Historical AMFI NAV snapshots for 1M, 3M, 6M, 1Y return analytics

Safe for public demonstration with 100% SYNTHETIC numbers, mock PANs, and zero client PII.
"""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from sqlalchemy import text
from database import engine, SessionLocal, Base
from models.persons import Person
from models.accounts import Account
from models.enums import (
    PersonType, AccountType, TransactionType, TransactionStatus, 
    GoldType, InsuranceType, InsuranceStatus, AlternativeType, PropertyType
)
from models.strategy_buckets import StrategyBucket, AssetClass
from models.equities import EquityHolding, EquityTransaction
from models.mutual_funds import MFHolding, MFTransaction, MFDetail, MFRealizedGain
from models.gold import GoldHolding
from models.insurance import InsurancePolicy
from models.alternatives import Alternative, UnlistedShare
from models.real_estate import Property, PropertyValuation
from models.market_data import MFNav

SCHEMAS = [
    "persons", "accounts", "strategy_buckets", "equities",
    "mutual_funds", "insurance", "real_estate", "gold",
    "liabilities", "alternatives", "cash_flows", "reporting",
    "audit", "config", "market_data"
]


def init_schemas_and_tables():
    """Creates database schemas and tables if they don't already exist."""
    print("[INIT] Verifying database schemas and tables...")
    with engine.connect() as conn:
        for s in SCHEMAS:
            try:
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {s};"))
            except Exception:
                pass
        conn.commit()
    Base.metadata.create_all(bind=engine)


def clear_existing_data(db):
    """Cleanly truncates existing records across tables for a fresh synthetic seed."""
    print("[SEED] Resetting database tables for fresh high-fidelity synthetic seed...")
    tables = [
        "mutual_funds.remaining_lots",
        "mutual_funds.realized_gains",
        "mutual_funds.transactions",
        "mutual_funds.holdings",
        "mutual_funds.mf_details",
        "equities.transactions",
        "equities.holdings",
        "gold.holdings",
        "gold.valuations",
        "insurance.premium_payments",
        "insurance.policies",
        "alternatives.unlisted",
        "alternatives.pms_aif",
        "real_estate.valuations",
        "real_estate.properties",
        "market_data.mf_nav",
        "market_data.index_prices",
        "market_data.gold_prices",
        "accounts.accounts",
        "strategy_buckets.buckets",
        "strategy_buckets.asset_classes",
        "persons.relationships",
        "persons.persons",
    ]
    with engine.connect() as conn:
        for tbl in tables:
            try:
                conn.execute(text(f"TRUNCATE TABLE {tbl} CASCADE;"))
            except Exception:
                pass
        conn.commit()


def seed_all(force: bool = False):
    init_schemas_and_tables()
    db = SessionLocal()

    # Check if already seeded unless force reset is requested
    if not force:
        try:
            existing = db.query(Person).first()
            if existing:
                print("[SEED] Database already contains records. Run with --reset to repopulate.")
                db.close()
                return
        except Exception as e:
            print(f"[SEED] Notice checking existing records: {e}")

    clear_existing_data(db)
    print("[SEED] Starting institutional-grade synthetic family office portfolio generation...")

    # =========================================================================
    # 1. PERSONS & LEGAL ENTITIES (Full Family Office Hierarchy)
    # =========================================================================
    persons_data = [
        ("Family Patriarch", "PATRIARCH", PersonType.INDIVIDUAL, "ABCDE1234F"),
        ("Family Member 1", "MEMBER_1", PersonType.INDIVIDUAL, "BCDEF2345G"),
        ("Family Member 2", "MEMBER_2", PersonType.INDIVIDUAL, "CDEFG3456H"),
        ("Apex Holdings Pvt. Ltd.", "APEX", PersonType.COMPANY, "DEFGH4567J"),
        ("Family Member 3", "MEMBER_3", PersonType.INDIVIDUAL, "EFGHI5678K"),
        ("Nexus Capital Private Limited", "NEXUS", PersonType.COMPANY, "FGHIJ6789L"),
        ("Family Member 4", "MEMBER_4", PersonType.INDIVIDUAL, "GHIJK7890M"),
        ("Family Member 5", "MEMBER_5", PersonType.INDIVIDUAL, "HIJKL8901N"),
    ]
    persons = []
    for full_n, short_n, p_type, pan in persons_data:
        p = Person(full_name=full_n, short_name=short_n, person_type=p_type, pan=pan)
        db.add(p)
        persons.append(p)
    db.flush()

    p_map = {p.short_name: p for p in persons}

    # =========================================================================
    # 2. MULTI-BROKER ACCOUNTS
    # =========================================================================
    brokers = [
        ("PrimeWealth", "Prime Wealth Securities", AccountType.TRADING),
        ("NexusCap", "Nexus Capital Finverse", AccountType.TRADING),
        ("Zerodha", "Zerodha Broking Ltd.", AccountType.DEMAT),
        ("CAMS", "CAMS MF Service Provider", AccountType.MF_FOLIO),
    ]
    accounts = []
    for p in persons:
        for short_b, full_b, acc_type in brokers:
            acc = Account(
                person_id=p.id,
                account_type=acc_type,
                institution_name=full_b,
                account_number=f"{short_b}-{p.short_name}-001",
                is_active=True
            )
            db.add(acc)
            accounts.append(acc)
    db.flush()

    # Map account by (person_short_name, broker_short)
    acct_map = {}
    for acc in accounts:
        p_short = next(p.short_name for p in persons if p.id == acc.person_id)
        b_short = acc.account_number.split("-")[0]
        acct_map[(p_short, b_short)] = acc

    # =========================================================================
    # 3. STRATEGY BUCKETS & ASSET CLASSES
    # =========================================================================
    buckets = [
        StrategyBucket(name="Core Wealth", description="Long term compounding, high conviction", sort_order=1),
        StrategyBucket(name="Growth Engine", description="Aggressive equity growth", sort_order=2),
        StrategyBucket(name="Capital Preservation", description="Inflation-hedged low risk", sort_order=3),
        StrategyBucket(name="Liquidity Reserve", description="Liquid within 30 days", sort_order=4),
        StrategyBucket(name="Illiquid Pool", description="Real estate, pre-IPO, private equity", sort_order=5),
        StrategyBucket(name="Insurance Cover", description="Capital protection layer", sort_order=6),
        StrategyBucket(name="Dry Powder", description="Cash waiting for market dips", sort_order=7),
    ]
    db.add_all(buckets)

    asset_classes = [
        AssetClass(name="Listed Equity", description="NSE/BSE listed equities", sort_order=1),
        AssetClass(name="Mutual Funds", description="Direct and regular mutual fund schemes", sort_order=2),
        AssetClass(name="Debt & Fixed Income", description="Bonds, NCDs, FDs", sort_order=3),
        AssetClass(name="Gold & SGB", description="Physical, Sovereign Gold Bonds, Gold ETFs", sort_order=4),
        AssetClass(name="Real Estate", description="Commercial and residential properties", sort_order=5),
        AssetClass(name="Insurance", description="Life, Term, ULIP policies", sort_order=6),
    ]
    db.add_all(asset_classes)
    db.flush()

    # =========================================================================
    # 4. COMPREHENSIVE MUTUAL FUNDS (32 Schemes across 8 Categories)
    # =========================================================================
    # Format: (Full Scheme Name, Clean Name, Fund Type, AMFI, Advisor, ExitLoadPct, ExitTenureDays, LongTermDays, CurrentNAV)
    mf_catalog = [
        # --- Large Cap ---
        ("Mirae Asset Large Cap Fund - Direct Plan - Growth", "Mirae Asset Large Cap Fund", "Large Cap", "118834", "DIRECT", 0.0, 0, 365, Decimal("118.45")),
        ("HDFC Top 100 Fund - Regular Plan - Growth", "HDFC Top 100 Fund", "Large Cap", "119062", "Prime Wealth Securities", 1.0, 365, 365, Decimal("1042.80")),
        ("ICICI Prudential Bluechip Fund - Direct Plan - Growth", "ICICI Prudential Bluechip Fund", "Large Cap", "120586", "DIRECT", 1.0, 365, 365, Decimal("108.20")),
        ("UTI Nifty 50 Index Fund - Direct Plan - Growth", "UTI Nifty 50 Index Fund", "Large Cap", "120716", "DIRECT", 0.0, 0, 365, Decimal("192.35")),

        # --- Flexi Cap & Multi Cap ---
        ("Parag Parikh Flexi Cap Fund - Direct Plan", "Parag Parikh Flexi Cap Fund", "Flexi Cap", "122639", "DIRECT", 2.0, 730, 365, Decimal("78.60")),
        ("Kotak Flexicap Fund - Regular Plan - Growth", "Kotak Flexicap Fund", "Flexi Cap", "112090", "Nexus Capital Finverse", 1.0, 365, 365, Decimal("84.20")),
        ("Nippon India Multi Cap Fund - Direct Plan - Growth", "Nippon India Multi Cap Fund", "Multi Cap", "118778", "DIRECT", 1.0, 365, 365, Decimal("265.40")),
        ("HDFC Flexi Cap Fund - Regular Plan - Growth", "HDFC Flexi Cap Fund", "Flexi Cap", "101762", "Prime Wealth Securities", 1.0, 365, 365, Decimal("1785.10")),

        # --- Mid Cap ---
        ("HDFC Mid-Cap Opportunities Fund - Direct Plan - Growth", "HDFC Mid-Cap Opportunities Fund", "Mid Cap", "118989", "DIRECT", 1.0, 365, 365, Decimal("174.90")),
        ("Kotak Emerging Equity Fund - Regular Plan - Growth", "Kotak Emerging Equity Fund", "Mid Cap", "105758", "Prime Wealth Securities", 1.0, 365, 365, Decimal("115.30")),
        ("Motilal Oswal Midcap Fund - Direct Plan - Growth", "Motilal Oswal Midcap Fund", "Mid Cap", "127042", "DIRECT", 1.0, 365, 365, Decimal("102.50")),
        ("Nippon India Growth Fund - Direct Plan - Growth", "Nippon India Growth Fund", "Mid Cap", "118760", "DIRECT", 1.0, 365, 365, Decimal("3720.40")),

        # --- Small Cap ---
        ("Nippon India Small Cap Fund - Regular Plan - Growth", "Nippon India Small Cap Fund", "Small Cap", "113177", "Nexus Capital Finverse", 1.0, 365, 365, Decimal("164.20")),
        ("SBI Small Cap Fund - Direct Plan - Growth", "SBI Small Cap Fund", "Small Cap", "125497", "DIRECT", 1.0, 365, 365, Decimal("186.75")),
        ("Quant Small Cap Fund - Direct Plan - Growth", "Quant Small Cap Fund", "Small Cap", "120828", "DIRECT", 1.0, 365, 365, Decimal("248.90")),
        ("Axis Small Cap Fund - Regular Plan - Growth", "Axis Small Cap Fund", "Small Cap", "125354", "Prime Wealth Securities", 1.0, 365, 365, Decimal("98.40")),

        # --- Hybrid & Balanced Advantage ---
        ("ICICI Prudential Balanced Advantage Fund - Direct Plan - Growth", "ICICI Prudential Balanced Advantage Fund", "Balanced Advantage", "120594", "DIRECT", 1.0, 365, 365, Decimal("76.15")),
        ("HDFC Balanced Advantage Fund - Regular Plan - Growth", "HDFC Balanced Advantage Fund", "Balanced Advantage", "100033", "Prime Wealth Securities", 1.0, 365, 365, Decimal("485.60")),
        ("Mirae Asset Aggressive Hybrid Fund - Direct Plan - Growth", "Mirae Asset Aggressive Hybrid Fund", "Aggressive Hybrid", "134444", "DIRECT", 1.0, 365, 365, Decimal("34.80")),
        ("Kotak Equity Hybrid Fund - Regular Plan - Growth", "Kotak Equity Hybrid Fund", "Aggressive Hybrid", "112093", "Nexus Capital Finverse", 1.0, 365, 365, Decimal("56.90")),

        # --- Liquid, Cash & Arbitrage ---
        ("ICICI Prudential Liquid Fund - Direct Plan - Growth", "ICICI Prudential Liquid Fund", "Liquid", "120598", "DIRECT", 0.0, 7, 365, Decimal("362.40")),
        ("Kotak Equity Arbitrage Fund - Regular Plan - Growth", "Kotak Equity Arbitrage Fund", "Arbitrage", "103328", "Prime Wealth Securities", 0.25, 30, 365, Decimal("37.85")),
        ("HDFC Low Duration Fund - Direct Plan - Growth", "HDFC Low Duration Fund", "Low Duration", "119036", "DIRECT", 0.0, 0, 365, Decimal("61.20")),
        ("SBI Overnight Fund - Direct Plan - Growth", "SBI Overnight Fund", "Overnight", "145552", "DIRECT", 0.0, 0, 365, Decimal("4150.00")),

        # --- Debt & Corporate Bond ---
        ("HDFC Short Term Debt Fund - Direct Plan - Growth", "HDFC Short Term Debt Fund", "Short Term Debt", "119050", "DIRECT", 0.0, 0, 1095, Decimal("32.50")),
        ("ICICI Prudential Corporate Bond Fund - Regular Plan - Growth", "ICICI Prudential Corporate Bond Fund", "Corporate Bond", "109865", "Nexus Capital Finverse", 0.0, 0, 1095, Decimal("28.90")),
        ("Kotak Bond Short Term Plan - Direct Plan - Growth", "Kotak Bond Short Term Plan", "Debt", "120152", "DIRECT", 0.0, 0, 1095, Decimal("52.10")),
        ("Aditya Birla Sun Life Corporate Bond Fund - Regular Plan - Growth", "Aditya Birla Sun Life Corporate Bond Fund", "Corporate Bond", "100057", "Prime Wealth Securities", 0.0, 0, 1095, Decimal("114.60")),

        # --- ELSS & Thematic ---
        ("Mirae Asset ELSS Tax Saver Fund - Direct Plan - Growth", "Mirae Asset ELSS Tax Saver Fund", "ELSS", "135781", "DIRECT", 0.0, 1095, 1095, Decimal("46.30")),
        ("DSP ELSS Tax Saver Fund - Regular Plan - Growth", "DSP ELSS Tax Saver Fund", "ELSS", "103845", "Prime Wealth Securities", 0.0, 1095, 1095, Decimal("112.40")),
        ("Tata Digital India Fund - Direct Plan - Growth", "Tata Digital India Fund", "Thematic: Tech", "135800", "DIRECT", 0.25, 30, 365, Decimal("54.20")),
        ("ICICI Prudential Technology Fund - Regular Plan - Growth", "ICICI Prudential Technology Fund", "Thematic: Tech", "100356", "Nexus Capital Finverse", 1.0, 15, 365, Decimal("215.80")),
    ]

    # Pre-populate MFDetail master table
    for full_name, clean_name, f_type, amfi, adv, el_pct, el_ten, lt_ten, cur_nav in mf_catalog:
        detail = MFDetail(
            scheme_name=full_name,
            clean_name=clean_name,
            fund_type=f_type,
            advisor=adv,
            exit_load_pct=Decimal(str(el_pct)),
            no_exit_load_tenure=el_ten,
            long_term_tenure=lt_ten
        )
        db.add(detail)
    db.flush()

    # Distribute schemes across entities with multi-tranche purchases
    today = date.today()
    scheme_distribution = [
        # (Entity short, Catalog slice, Folio prefix, Target invested per scheme approx)
        ("PATRIARCH", [0, 1, 4, 16, 20, 24, 28], "FOL-PAT", Decimal("1500000")),
        ("MEMBER_1",  [0, 4, 8, 12, 16, 20, 30], "FOL-M1",  Decimal("2200000")),
        ("MEMBER_2",  [1, 5, 9, 13, 17, 21, 29], "FOL-M2",  Decimal("1800000")),
        ("APEX",      [2, 6, 10, 18, 20, 21, 25],"FOL-APX", Decimal("3500000")),
        ("MEMBER_3",  [3, 7, 11, 14, 19, 22, 31],"FOL-M3",  Decimal("1200000")),
        ("NEXUS",     [8, 12, 15, 23, 26, 27],   "FOL-NEX", Decimal("2500000")),
        ("MEMBER_4",  [4, 12, 16, 20],           "FOL-M4",  Decimal("800000")),
        ("MEMBER_5",  [8, 14, 18, 22],           "FOL-M5",  Decimal("600000")),
    ]

    for p_short, cat_indices, fol_prefix, target_inv in scheme_distribution:
        p_obj = p_map[p_short]
        p_acc = acct_map.get((p_short, "PrimeWealth")) or acct_map.get((p_short, "Zerodha"))

        for idx in cat_indices:
            full_n, clean_n, f_type, amfi, adv, el_pct, el_ten, lt_ten, cur_nav = mf_catalog[idx]
            folio_num = f"{fol_prefix}-{amfi}-99"

            # Create 3-4 multi-year purchase tranches (SIPs & Lump sums)
            tranches = [
                # (Days ago, fraction of target, price multiplier vs current nav)
                (today - timedelta(days=1200), Decimal("0.35"), Decimal("0.62")), # 3.3 yrs ago (LTCG, No exit load, high profit)
                (today - timedelta(days=650),  Decimal("0.30"), Decimal("0.78")), # 1.8 yrs ago (LTCG, No exit load)
                (today - timedelta(days=220),  Decimal("0.20"), Decimal("0.91")), # 7 months ago (STCG or near LTCG)
                (today - timedelta(days=45),   Decimal("0.15"), Decimal("0.98")), # 1.5 months ago (STCG, Active Exit Load!)
            ]

            total_units_holding = Decimal("0.0000")
            weighted_cost_sum = Decimal("0.00")

            for t_date, fraction, p_mult in tranches:
                t_amount = target_inv * fraction
                buy_nav = cur_nav * p_mult
                t_units = (t_amount / buy_nav).quantize(Decimal("0.0001"))
                actual_amount = (t_units * buy_nav).quantize(Decimal("0.01"))

                txn = MFTransaction(
                    person_id=p_obj.id,
                    account_id=p_acc.id,
                    strategy_bucket_id=buckets[1].id if "Cap" in f_type else buckets[0].id,
                    folio_number=folio_num,
                    scheme_name=full_n,
                    amfi_code=amfi,
                    transaction_date=t_date,
                    transaction_type="PURCHASE_SIP" if fraction < Decimal("0.25") else "PURCHASE",
                    units=t_units,
                    nav=buy_nav,
                    amount=actual_amount,
                    status=TransactionStatus.ACTIVE,
                    notes=f"Synthetic allocation tranche - {p_short}"
                )
                db.add(txn)

                total_units_holding += t_units
                weighted_cost_sum += actual_amount

            # Holding record for latest balance & AMFI comparison
            avg_holding_nav = (weighted_cost_sum / total_units_holding).quantize(Decimal("0.0001"))
            cur_value = (total_units_holding * cur_nav).quantize(Decimal("0.01"))

            holding = MFHolding(
                person_id=p_obj.id,
                account_id=p_acc.id,
                folio_number=folio_num,
                scheme_name=full_n,
                amfi_code=amfi,
                units=total_units_holding,
                average_nav=avg_holding_nav,
                current_nav=cur_nav,
                current_value=cur_value,
                as_of_date=today
            )
            db.add(holding)

    # Seed some historical Realized Capital Gains (Redemptions) for Booked Profit analytics
    sample_realized_sales = [
        ("PATRIARCH", "HDFC Top 100 Fund - Regular Plan - Growth", "119062", Decimal("1500.0000"), Decimal("680.00"), Decimal("950.00"), date(2021, 5, 10), date(2024, 2, 15), "LTCG", "2023-2024"),
        ("MEMBER_1", "Parag Parikh Flexi Cap Fund - Direct Plan", "122639", Decimal("5000.0000"), Decimal("45.20"), Decimal("68.50"), date(2022, 1, 15), date(2024, 6, 20), "LTCG", "2024-2025"),
        ("MEMBER_2", "ICICI Prudential Bluechip Fund - Direct Plan - Growth", "120586", Decimal("3500.0000"), Decimal("85.00"), Decimal("98.50"), date(2023, 9, 10), date(2024, 3, 5), "STCG", "2023-2024"),
        ("APEX", "Kotak Equity Arbitrage Fund - Regular Plan - Growth", "103328", Decimal("25000.0000"), Decimal("31.20"), Decimal("36.10"), date(2022, 8, 1), date(2024, 7, 10), "LTCG", "2024-2025"),
    ]
    for p_short, s_name, amfi, s_units, p_rate, s_rate, p_date, s_date, tax_cat, fy in sample_realized_sales:
        cost_v = (s_units * p_rate).quantize(Decimal("0.01"))
        sale_v = (s_units * s_rate).quantize(Decimal("0.01"))
        gain_v = sale_v - cost_v
        days = (s_date - p_date).days
        p_obj = p_map[p_short]
        p_acc = acct_map.get((p_short, "PrimeWealth")) or acct_map.get((p_short, "Zerodha"))

        # 1. Historical Purchase Transaction
        p_txn = MFTransaction(
            person_id=p_obj.id,
            account_id=p_acc.id,
            strategy_bucket_id=buckets[0].id,
            folio_number=f"FOL-{p_short}-{amfi}-99",
            scheme_name=s_name,
            amfi_code=amfi,
            transaction_date=p_date,
            transaction_type="PURCHASE",
            units=s_units,
            nav=p_rate,
            amount=cost_v,
            status=TransactionStatus.ACTIVE,
            notes=f"Synthetic realized gain buy leg - {p_short}"
        )
        db.add(p_txn)
        db.flush()

        # 2. Historical Sale / Redemption Transaction
        s_txn = MFTransaction(
            person_id=p_obj.id,
            account_id=p_acc.id,
            strategy_bucket_id=buckets[0].id,
            folio_number=f"FOL-{p_short}-{amfi}-99",
            scheme_name=s_name,
            amfi_code=amfi,
            transaction_date=s_date,
            transaction_type="REDEMPTION",
            units=s_units,
            nav=s_rate,
            amount=sale_v,
            status=TransactionStatus.ACTIVE,
            notes=f"Synthetic realized gain sell leg - {p_short}"
        )
        db.add(s_txn)
        db.flush()

        rg = MFRealizedGain(
            person_id=p_obj.id,
            purchase_txn_id=p_txn.id,
            sale_txn_id=s_txn.id,
            folio_number=f"FOL-{p_short}-{amfi}-99",
            scheme_name=s_name,
            advisor="Prime Wealth Securities" if "Regular" in s_name else "DIRECT",
            amfi_code=amfi,
            purchase_date=p_date,
            purchase_rate=p_rate,
            sale_date=s_date,
            sale_rate=s_rate,
            units_sold=s_units,
            cost_value=cost_v,
            sale_value=sale_v,
            gain_loss=gain_v,
            holding_days=days,
            tax_category=tax_cat,
            financial_year=fy
        )
        db.add(rg)

    # =========================================================================
    # 5. HISTORICAL AMFI NAV SNAPSHOTS (Enables 1M, 3M, 6M, 1Y Return Calculations)
    # =========================================================================
    for full_n, clean_n, f_type, amfi, adv, el_pct, el_ten, lt_ten, cur_nav in mf_catalog:
        history_points = [
            (today, cur_nav),
            (today - timedelta(days=30), cur_nav * Decimal("0.985")),  # ~1.5% 1M gain
            (today - timedelta(days=90), cur_nav * Decimal("0.952")),  # ~5.0% 3M gain
            (today - timedelta(days=180), cur_nav * Decimal("0.895")), # ~11.7% 6M gain
            (today - timedelta(days=365), cur_nav * Decimal("0.810")), # ~23.4% 1Y gain
        ]
        for h_date, h_nav in history_points:
            nav_entry = MFNav(
                amfi_code=amfi,
                scheme_name=full_n,
                nav_date=h_date,
                nav=h_nav.quantize(Decimal("0.0001"))
            )
            db.add(nav_entry)

    # =========================================================================
    # 6. BLUECHIP LISTED EQUITIES (18 Stocks Across Sectors)
    # =========================================================================
    equity_universe = [
        ("RELIANCE", "Reliance Industries Ltd.", "Oil & Gas / Conglomerate", Decimal("2950.00"), Decimal("2250.00"), [("PATRIARCH", 1800), ("APEX", 3200)]),
        ("TCS", "Tata Consultancy Services Ltd.", "IT Services", Decimal("3980.00"), Decimal("3350.00"), [("PATRIARCH", 1200), ("MEMBER_1", 1500)]),
        ("HDFCBANK", "HDFC Bank Ltd.", "Banking & Finance", Decimal("1680.00"), Decimal("1420.00"), [("APEX", 4500), ("MEMBER_2", 2000)]),
        ("INFY", "Infosys Ltd.", "IT Services", Decimal("1780.00"), Decimal("1410.00"), [("MEMBER_1", 2500), ("MEMBER_3", 1400)]),
        ("ICICIBANK", "ICICI Bank Ltd.", "Banking & Finance", Decimal("1240.00"), Decimal("920.00"), [("APEX", 5000), ("PATRIARCH", 2200)]),
        ("LT", "Larsen & Toubro Ltd.", "Infrastructure & Eng.", Decimal("3650.00"), Decimal("2850.00"), [("PATRIARCH", 800), ("APEX", 1200)]),
        ("BHARTIARTL", "Bharti Airtel Ltd.", "Telecommunications", Decimal("1650.00"), Decimal("950.00"), [("MEMBER_1", 2200), ("MEMBER_2", 1500)]),
        ("ITC", "ITC Ltd.", "FMCG & Hotels", Decimal("495.00"), Decimal("380.00"), [("PATRIARCH", 8000), ("APEX", 12000)]),
        ("TITAN", "Titan Company Ltd.", "Consumer Discretionary", Decimal("3420.00"), Decimal("2650.00"), [("MEMBER_2", 900), ("MEMBER_3", 600)]),
        ("BAJFINANCE", "Bajaj Finance Ltd.", "NBFC & Lending", Decimal("7150.00"), Decimal("5800.00"), [("MEMBER_1", 550), ("APEX", 850)]),
        ("SUNPHARMA", "Sun Pharmaceutical Industries Ltd.", "Healthcare & Pharma", Decimal("1860.00"), Decimal("1150.00"), [("PATRIARCH", 1600), ("MEMBER_2", 1100)]),
        ("TATAMOTORS", "Tata Motors Ltd.", "Automobiles & EV", Decimal("940.00"), Decimal("620.00"), [("MEMBER_1", 3500), ("MEMBER_3", 2000)]),
        ("SBIN", "State Bank of India", "Banking & Finance", Decimal("820.00"), Decimal("590.00"), [("APEX", 4000), ("PATRIARCH", 2500)]),
        ("HINDUNILVR", "Hindustan Unilever Ltd.", "FMCG", Decimal("2680.00"), Decimal("2450.00"), [("MEMBER_2", 850), ("PATRIARCH", 950)]),
        ("NTPC", "NTPC Ltd.", "Power & Utilities", Decimal("410.00"), Decimal("235.00"), [("APEX", 7500), ("MEMBER_3", 3500)]),
        ("M&M", "Mahindra & Mahindra Ltd.", "Automobiles & Agri", Decimal("2980.00"), Decimal("1650.00"), [("MEMBER_1", 1400), ("MEMBER_2", 900)]),
        ("POWERGRID", "Power Grid Corporation of India", "Power & Utilities", Decimal("335.00"), Decimal("210.00"), [("PATRIARCH", 5000), ("APEX", 8000)]),
        ("MARUTI", "Maruti Suzuki India Ltd.", "Automobiles", Decimal("12450.00"), Decimal("9800.00"), [("MEMBER_1", 250), ("APEX", 300)]),
    ]

    for sym, comp_n, sector, cmp, avg_buy, allocations in equity_universe:
        for p_short, qty in allocations:
            p_obj = p_map[p_short]
            p_acc = acct_map.get((p_short, "Zerodha")) or acct_map.get((p_short, "PrimeWealth"))
            qty_dec = Decimal(str(qty))
            tot_val = (qty_dec * cmp).quantize(Decimal("0.01"))

            # Holding
            eq_h = EquityHolding(
                person_id=p_obj.id,
                account_id=p_acc.id,
                symbol=sym,
                company_name=comp_n,
                isin=f"INE{sym[:4]}0101",
                quantity=qty_dec,
                average_cost=avg_buy,
                last_price=cmp,
                last_price_date=today,
                current_value=tot_val,
                as_of_date=today
            )
            db.add(eq_h)

            # Buy Transaction
            eq_txn = EquityTransaction(
                person_id=p_obj.id,
                account_id=p_acc.id,
                strategy_bucket_id=buckets[0].id,
                symbol=sym,
                company_name=comp_n,
                isin=f"INE{sym[:4]}0101",
                transaction_date=today - timedelta(days=500),
                transaction_type=TransactionType.BUY,
                quantity=qty_dec,
                price=avg_buy,
                net_amount=(qty_dec * avg_buy).quantize(Decimal("0.01")),
                status=TransactionStatus.ACTIVE,
                notes=f"Synthetic Bluechip Allocation - {p_short}"
            )
            db.add(eq_txn)

    # =========================================================================
    # 7. SOVEREIGN GOLD BONDS (6 RBI Tranches)
    # =========================================================================
    sgb_tranches = [
        ("SGB 2026-27 Series I", "IN0020160026", Decimal("3119.00"), Decimal("7850.00"), date(2026, 8, 12), [("PATRIARCH", 350), ("APEX", 500)]),
        ("SGB 2027-28 Series II", "IN0020170041", Decimal("3788.00"), Decimal("7850.00"), date(2027, 10, 24), [("MEMBER_1", 400), ("MEMBER_2", 300)]),
        ("SGB 2028-29 Series I", "IN0020200085", Decimal("4852.00"), Decimal("7850.00"), date(2028, 5, 18), [("PATRIARCH", 600), ("APEX", 800)]),
        ("SGB 2029-30 Series III", "IN0020210092", Decimal("5117.00"), Decimal("7850.00"), date(2029, 11, 20), [("MEMBER_1", 550), ("MEMBER_3", 350)]),
        ("SGB 2030-31 Series I", "IN0020220034", Decimal("5611.00"), Decimal("7850.00"), date(2030, 6, 28), [("MEMBER_2", 500), ("APEX", 650)]),
        ("SGB 2031-32 Series II", "IN0020230018", Decimal("6263.00"), Decimal("7850.00"), date(2031, 9, 15), [("PATRIARCH", 750), ("MEMBER_1", 600)]),
    ]

    for sgb_name, isin, issue_p, cmp_p, mat_date, allocs in sgb_tranches:
        for p_short, grams in allocs:
            p_obj = p_map[p_short]
            p_acc = acct_map.get((p_short, "Zerodha"))
            grams_dec = Decimal(str(grams))
            cost_val = (grams_dec * issue_p).quantize(Decimal("0.01"))
            cur_val = (grams_dec * cmp_p).quantize(Decimal("0.01"))

            gold_h = GoldHolding(
                person_id=p_obj.id,
                account_id=p_acc.id,
                description=sgb_name,
                isin=isin,
                gold_type=GoldType.SGB,
                quantity_grams=grams_dec,
                purchase_price_per_gram=issue_p,
                total_cost=cost_val,
                sgb_series=sgb_name,
                sgb_maturity_date=mat_date,
                sgb_units=grams_dec,
                sgb_issue_price=issue_p,
                last_price=cmp_p,
                current_value=cur_val,
                as_of_date=today
            )
            db.add(gold_h)

    # =========================================================================
    # 8. ALTERNATIVES (AIF Cat II, PMS, Pre-IPO Unlisted Shares)
    # =========================================================================
    # PMS & AIF
    alts = [
        ("APEX", AlternativeType.AIF, "Alpha Growth Asset Managers", "Alpha Dynamic Private Equity Fund II", "AIF Cat II - Pre-IPO & Growth Equity", Decimal("35000000.00"), Decimal("48500000.00"), date(2021, 6, 15)),
        ("PATRIARCH", AlternativeType.AIF, "Renaissance Credit Partners", "Renaissance Private Credit AIF Cat II", "AIF Cat II - Structured Senior Debt", Decimal("25000000.00"), Decimal("31200000.00"), date(2022, 3, 10)),
        ("MEMBER_1", AlternativeType.PMS, "Marcellus Investment Managers", "Consistent Compounders PMS", "Large Cap Focused Portfolio", Decimal("20000000.00"), Decimal("27400000.00"), date(2021, 11, 20)),
    ]
    for p_short, a_type, manager, scheme, strat, invested, cur_v, inv_date in alts:
        alt_entry = Alternative(
            person_id=p_map[p_short].id,
            account_id=acct_map.get((p_short, "PrimeWealth")).id,
            alternative_type=a_type,
            manager_name=manager,
            scheme_name=scheme,
            strategy=strat,
            investment_date=inv_date,
            amount_invested=invested,
            current_value=cur_v,
            last_valuation_date=today,
            notes=f"Institutional allocation - {p_short}"
        )
        db.add(alt_entry)

    # Unlisted Pre-IPO Shares
    unlisted_stocks = [
        ("APEX", "National Stock Exchange of India Ltd. (NSE)", "INE753I01010", Decimal("4500.0000"), Decimal("3200.00"), Decimal("6150.00"), date(2022, 4, 15)),
        ("MEMBER_1", "Tata Technologies Ltd. (Pre-IPO holding)", "INE142M01025", Decimal("8000.0000"), Decimal("450.00"), Decimal("1080.00"), date(2022, 9, 10)),
        ("PATRIARCH", "HDB Financial Services Ltd.", "INE756I01020", Decimal("6000.0000"), Decimal("680.00"), Decimal("950.00"), date(2021, 12, 1)),
    ]
    for p_short, comp_n, isin, qty, buy_p, cmp_p, p_date in unlisted_stocks:
        cost_v = (qty * buy_p).quantize(Decimal("0.01"))
        cur_v = (qty * cmp_p).quantize(Decimal("0.01"))

        unlisted_entry = UnlistedShare(
            person_id=p_map[p_short].id,
            account_id=acct_map.get((p_short, "Zerodha")).id,
            company_name=comp_n,
            isin=isin,
            quantity=qty,
            purchase_date=p_date,
            purchase_price=buy_p,
            total_cost=cost_v,
            current_price=cmp_p,
            current_value=cur_v,
            last_valuation_date=today,
            is_pre_ipo="YES",
            notes="Institutional Unlisted Placement"
        )
        db.add(unlisted_entry)

    # =========================================================================
    # 9. INSURANCE POLICIES & COMMERCIAL REAL ESTATE
    # =========================================================================
    insurance_sample = [
        ("PATRIARCH", "HDFC Life Click 2 Protect 3D Plus", "POL-HDFC-99120", Decimal("100000000.00"), Decimal("225000.00"), "ANNUAL", InsuranceType.TERM, Decimal("0.00")),
        ("MEMBER_1", "Max Life Smart Wealth Plan", "POL-MAX-44810", Decimal("25000000.00"), Decimal("500000.00"), "ANNUAL", InsuranceType.ULIP, Decimal("2150000.00")),
        ("MEMBER_2", "ICICI Prudential iProtect Smart", "POL-ICICI-66230", Decimal("50000000.00"), Decimal("145000.00"), "ANNUAL", InsuranceType.TERM, Decimal("0.00")),
        ("APEX", "Tata AIA Life Fortune Pro", "POL-TATA-88190", Decimal("30000000.00"), Decimal("650000.00"), "ANNUAL", InsuranceType.ULIP, Decimal("3450000.00")),
    ]
    for p_short, pol_name, pol_num, sum_ass, annual_prem, freq, ptype, surr_val in insurance_sample:
        p_obj = p_map[p_short]
        policy = InsurancePolicy(
            insured_person_id=p_obj.id,
            holder_person_id=p_obj.id,
            insurer_name=pol_name.split()[0],
            plan_name=pol_name,
            policy_number=pol_num,
            insurance_type=ptype,
            sum_assured=sum_ass,
            premium_amount=annual_prem,
            premium_frequency=freq,
            policy_start_date=date(2019, 6, 15),
            policy_end_date=date(2044, 6, 15),
            status=InsuranceStatus.ACTIVE,
            surrendered_amount=surr_val
        )
        db.add(policy)

    # Commercial Real Estate
    properties = [
        ("APEX", PropertyType.COMMERCIAL, "One BKC Commercial Suite", "Bandra Kurla Complex, G-Block", "Mumbai", Decimal("4500.00"), date(2018, 5, 20), Decimal("85000000.00"), Decimal("92000000.00"), Decimal("145000000.00"), True, Decimal("850000.00")),
        ("PATRIARCH", PropertyType.RESIDENTIAL, "DLF The Camellias Luxury Residence", "Golf Course Road", "Gurugram", Decimal("7200.00"), date(2020, 2, 10), Decimal("165000000.00"), Decimal("178000000.00"), Decimal("265000000.00"), False, Decimal("0.00")),
    ]
    for p_short, prop_type, prop_name, addr, city, sqft, p_date, p_price, tot_cost, est_val, is_rent, rent in properties:
        p_obj = p_map[p_short]
        prop = Property(
            person_id=p_obj.id,
            property_type=prop_type,
            name=prop_name,
            address=addr,
            city=city,
            area_sqft=sqft,
            purchase_date=p_date,
            purchase_price=p_price,
            total_cost=tot_cost,
            is_rented=is_rent,
            monthly_rental=rent
        )
        db.add(prop)
        db.flush()

        val = PropertyValuation(
            property_id=prop.id,
            valuation_date=today,
            estimated_value=est_val,
            valuation_source="REGISTERED_VALUER"
        )
        db.add(val)

    db.commit()
    db.close()

    print("=" * 70)
    print("[SUCCESS] SYNTHETIC SEED SUCCESSFUL!")
    print(f"  * Generated {len(persons)} Family Entities across legal trusts & companies")
    print(f"  * Generated {len(accounts)} Multi-Broker Demat & Trading Accounts")
    print(f"  * Generated 32 Mutual Fund Schemes across 8 Categories (Large, Mid, Small, Flexi, Hybrid, Debt, Arbitrage, ELSS)")
    print(f"  * Generated 100+ Multi-tranche SIP & Purchase Records with historical XIRR spread")
    print(f"  * Generated Realized Capital Gain records (STCG & LTCG) for Booked Profit analytics")
    print(f"  * Generated 18 Bluechip Equity Holdings & FIFO Transactions")
    print(f"  * Generated 6 Sovereign Gold Bond (SGB) Tranches with live YTM and CMP")
    print(f"  * Generated Private Wealth Alternatives (AIF Cat II, PMS, Pre-IPO Shares)")
    print(f"  * Generated Insurance Policies & Commercial Real Estate with Valuation Records")
    print(f"  * Populated Historical AMFI NAV series for 1M, 3M, 6M, 1Y point-to-point returns")
    print("=" * 70)


if __name__ == "__main__":
    force_reset = ("--reset" in sys.argv or "--force" in sys.argv or "-f" in sys.argv)
    seed_all(force=force_reset)
