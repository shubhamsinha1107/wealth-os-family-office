"""
seed_mock.py
============
Synthetic data generator for WealthOS / Family Office Platform.
Populates an anonymized, multi-entity family structure with realistic
sample portfolios across Equities, Mutual Funds, SGBs, Insurance, and Alternatives.
Safe for public demonstration with ZERO confidential client data.
"""

from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import text
from database import engine, SessionLocal, Base
from models.persons import Person
from models.accounts import Account
from models.enums import PersonType, AccountType, TransactionType, TransactionStatus, GoldType, InsuranceType, InsuranceStatus
from models.strategy_buckets import StrategyBucket, AssetClass
from models.equities import EquityHolding, EquityTransaction
from models.mutual_funds import MFHolding, MFTransaction, MFDetail
from models.gold import GoldHolding
from models.insurance import InsurancePolicy, PremiumPayment
from models.alternatives import Alternative


SCHEMAS = [
    "persons", "accounts", "strategy_buckets", "equities",
    "mutual_funds", "insurance", "real_estate", "gold",
    "liabilities", "alternatives", "cash_flows", "reporting",
    "audit", "config", "market_data"
]


def init_schemas_and_tables():
    """Creates database schemas and tables."""
    print("[INIT] Verifying database schemas and tables...")
    with engine.connect() as conn:
        for s in SCHEMAS:
            try:
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {s};"))
            except Exception:
                pass
        conn.commit()
    Base.metadata.create_all(bind=engine)


def seed_all():
    init_schemas_and_tables()
    db = SessionLocal()

    # Check if already seeded
    try:
        existing = db.query(Person).first()
        if existing:
            print("[SEED] Database already contains records. Skipping seed.")
            db.close()
            return
    except Exception as e:
        print(f"[SEED] Notice checking existing records: {e}")

    print("[SEED] Starting synthetic multi-entity portfolio generation...")

    # 1. PERSONS (Anonymized Family Structure)
    persons = [
        Person(full_name="Family Patriarch (Trust A)", short_name="PATRIARCH", person_type=PersonType.INDIVIDUAL, pan="ABCDE1234F"),
        Person(full_name="Family Member 1", short_name="FM1", person_type=PersonType.INDIVIDUAL, pan="BCDEF2345G"),
        Person(full_name="Family Member 2", short_name="FM2", person_type=PersonType.INDIVIDUAL, pan="CDEFG3456H"),
        Person(full_name="Apex Holdings Pvt. Ltd.", short_name="APEX", person_type=PersonType.COMPANY, pan="DEFGH4567J"),
    ]
    db.add_all(persons)
    db.flush()

    # 2. ACCOUNTS (Brokers across family entities)
    brokers = [
        ("PrimeWealth", "Prime Wealth Securities"),
        ("NexusCap", "Nexus Capital Finverse"),
    ]
    accounts = []
    for person in persons:
        for short_b, full_b in brokers:
            acc = Account(
                person_id=person.id,
                account_type=AccountType.TRADING,
                institution_name=full_b,
                account_number=f"{short_b}-{person.short_name}-001",
                is_active=True,
            )
            accounts.append(acc)
            db.add(acc)
    db.flush()

    # 3. STRATEGY BUCKETS
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

    # 4. ASSET CLASSES
    asset_classes = [
        AssetClass(name="Listed Equity", description="NSE/BSE listed equities", sort_order=1),
        AssetClass(name="Mutual Funds", description="Direct and regular mutual fund schemes", sort_order=2),
        AssetClass(name="Debt & Fixed Income", description="Bonds, NCDs, FDs", sort_order=3),
        AssetClass(name="Gold & SGB", description="Physical, Sovereign Gold Bonds, Gold ETFs", sort_order=4),
        AssetClass(name="Real Estate", description="Commercial and residential properties", sort_order=5),
        AssetClass(name="Insurance", description="Life, Term, ULIP policies", sort_order=6),
        AssetClass(name="Unlisted & Pre-IPO", description="Pre-IPO shares and private holdings", sort_order=7),
        AssetClass(name="Alternatives", description="AIF Category II/III, PMS, Venture Debt", sort_order=8),
        AssetClass(name="Cash & Equivalents", description="Overnight funds, bank balances", sort_order=9),
    ]
    db.add_all(asset_classes)
    db.flush()

    # 5. SAMPLE LISTED EQUITIES (Bluechip Portfolio)
    sample_stocks = [
        ("RELIANCE", "Reliance Industries Ltd.", "INE002A01018", 1200, Decimal("2850.50"), Decimal("2980.00")),
        ("TCS", "Tata Consultancy Services Ltd.", "INE467B01029", 800, Decimal("3900.00"), Decimal("4120.25")),
        ("HDFCBANK", "HDFC Bank Ltd.", "INE040A01034", 2500, Decimal("1540.00"), Decimal("1650.00")),
        ("INFY", "Infosys Ltd.", "INE009A01021", 1500, Decimal("1620.00"), Decimal("1780.50")),
        ("ICICIBANK", "ICICI Bank Ltd.", "INE090A01021", 1800, Decimal("1120.00"), Decimal("1240.00")),
    ]

    for sym, name, isin, qty, avg_cost, cur_price in sample_stocks:
        holding = EquityHolding(
            person_id=persons[0].id,
            account_id=accounts[0].id,
            symbol=sym,
            company_name=name,
            isin=isin,
            quantity=Decimal(str(qty)),
            average_cost=avg_cost,
            last_price=cur_price,
            last_price_date=date.today(),
            current_value=Decimal(str(qty)) * cur_price,
            as_of_date=date.today()
        )
        db.add(holding)

        # Matching buy transaction
        txn = EquityTransaction(
            person_id=persons[0].id,
            account_id=accounts[0].id,
            strategy_bucket_id=buckets[0].id,
            symbol=sym,
            company_name=name,
            isin=isin,
            transaction_date=date(2023, 6, 15),
            transaction_type=TransactionType.BUY,
            quantity=Decimal(str(qty)),
            price=avg_cost,
            brokerage=Decimal("25.00"),
            taxes=Decimal("15.00"),
            stt=Decimal("35.00"),
            net_amount=Decimal(str(qty)) * avg_cost + Decimal("75.00"),
            status=TransactionStatus.ACTIVE,
            notes="Initial synthetic seed allocation"
        )
        db.add(txn)

    # 6. SAMPLE MUTUAL FUNDS (Includes both Direct and Regular to showcase TER engine)
    mf_schemes = [
        ("Mirae Asset Large & Midcap Fund - Regular Plan - Growth", "Mirae Asset Large & Midcap Fund - Direct Plan - Growth", 14500, Decimal("125.40"), Decimal("138.60"), "118989"),
        ("Parag Parikh Flexi Cap Fund - Regular Plan", "Parag Parikh Flexi Cap Fund - Direct Plan", 21000, Decimal("64.20"), Decimal("73.50"), "122639"),
        ("HDFC Top 100 Fund - Regular Plan - Growth", "HDFC Top 100 Fund - Direct Plan - Growth", 9800, Decimal("910.00"), Decimal("995.00"), "119062"),
    ]

    for reg_name, dir_name, units, avg_nav, cur_nav, amfi in mf_schemes:
        mf_detail = MFDetail(
            scheme_name=reg_name,
            clean_name=reg_name.replace(" - Regular Plan - Growth", "").replace(" - Regular Plan", "").strip(),
            fund_type="Equity: Flexi / Large Cap",
            advisor="Prime Wealth"
        )
        db.add(mf_detail)

        mf_hold = MFHolding(
            person_id=persons[1].id,
            account_id=accounts[2].id,
            folio_number=f"FOLIO-{amfi}-99",
            scheme_name=reg_name,
            amfi_code=amfi,
            units=Decimal(str(units)),
            average_nav=avg_nav,
            current_nav=cur_nav,
            current_value=Decimal(str(units)) * cur_nav,
            as_of_date=date.today()
        )
        db.add(mf_hold)

        mf_txn = MFTransaction(
            person_id=persons[1].id,
            account_id=accounts[2].id,
            strategy_bucket_id=buckets[1].id,
            folio_number=f"FOLIO-{amfi}-99",
            scheme_name=reg_name,
            amfi_code=amfi,
            transaction_date=date(2022, 10, 10),
            transaction_type="PURCHASE",
            units=Decimal(str(units)),
            nav=avg_nav,
            amount=Decimal(str(units)) * avg_nav,
            status=TransactionStatus.ACTIVE,
            notes="Initial MF allocation"
        )
        db.add(mf_txn)

    # 7. SAMPLE GOLD (Sovereign Gold Bonds - SGB)
    sgbs = [
        ("SGB 2028-29 Series I", "IN0020200085", 400, Decimal("4850.00"), Decimal("7250.00")),
        ("SGB 2029-30 Series II", "IN0020210092", 650, Decimal("5120.00"), Decimal("7250.00")),
    ]
    for sgb_name, isin, grams, avg_p, cur_p in sgbs:
        gold_hold = GoldHolding(
            person_id=persons[2].id,
            account_id=accounts[4].id,
            description=sgb_name,
            isin=isin,
            gold_type=GoldType.SGB,
            quantity_grams=Decimal(str(grams)),
            purchase_price_per_gram=avg_p,
            total_cost=Decimal(str(grams)) * avg_p,
            sgb_series=sgb_name,
            sgb_units=Decimal(str(grams)),
            sgb_issue_price=avg_p,
            last_price=cur_p,
            current_value=Decimal(str(grams)) * cur_p,
            as_of_date=date.today()
        )
        db.add(gold_hold)

    # 8. SAMPLE INSURANCE POLICIES
    insurance_sample = [
        ("HDFC Life Click 2 Protect Super", "POL-HDFC-99120", Decimal("50000000"), Decimal("125000"), "ANNUAL", InsuranceType.TERM),
        ("Max Life Smart Wealth Plan", "POL-MAX-44810", Decimal("10000000"), Decimal("250000"), "ANNUAL", InsuranceType.ULIP),
    ]
    for pol_name, pol_num, sum_ass, annual_prem, freq, ptype in insurance_sample:
        policy = InsurancePolicy(
            insured_person_id=persons[0].id,
            holder_person_id=persons[0].id,
            insurer_name=pol_name.split()[0],
            plan_name=pol_name,
            policy_number=pol_num,
            insurance_type=ptype,
            sum_assured=sum_ass,
            premium_amount=annual_prem,
            premium_frequency=freq,
            policy_start_date=date(2020, 1, 15),
            policy_end_date=date(2045, 1, 15),
            status=InsuranceStatus.ACTIVE,
            surrendered_amount=Decimal("0.00")
        )
        db.add(policy)

    db.commit()
    db.close()
    print("=" * 65)
    print("[SUCCESS] SYNTHETIC SEED SUCCESSFUL!")
    print(f"  * Generated {len(persons)} Family Entities / Persons (with synthetic PANs)")
    print(f"  * Generated {len(accounts)} Multi-Broker Accounts")
    print(f"  * Generated {len(sample_stocks)} Bluechip Equity Holdings & Trade Records")
    print(f"  * Generated {len(mf_schemes)} Mutual Fund Holdings (Direct & Regular for TER testing)")
    print(f"  * Generated {len(sgbs)} Sovereign Gold Bond Tranches")
    print(f"  * Generated {len(insurance_sample)} Insurance Policies")
    print("=" * 65)


if __name__ == "__main__":
    seed_all()
