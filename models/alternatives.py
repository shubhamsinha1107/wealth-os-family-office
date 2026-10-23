from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Text, ForeignKey, Enum
from sqlalchemy.sql import func
from database import Base
from models.enums import AlternativeType

class Alternative(Base):
    __tablename__ = "pms_aif"
    __table_args__ = {"schema": "alternatives"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"), nullable=False)
    alternative_type = Column(Enum(AlternativeType), nullable=False)
    manager_name = Column(String(200), nullable=False)
    scheme_name = Column(String(300))
    strategy = Column(String(200))
    investment_date = Column(Date)
    amount_invested = Column(Numeric(18, 2))
    current_value = Column(Numeric(18, 2))
    last_valuation_date = Column(Date)
    lock_in_end_date = Column(Date)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class UnlistedShare(Base):
    __tablename__ = "unlisted"
    __table_args__ = {"schema": "alternatives"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"), nullable=True)
    company_name = Column(String(200), nullable=False)
    isin = Column(String(20))
    quantity = Column(Numeric(18, 4), nullable=False)
    purchase_date = Column(Date)
    purchase_price = Column(Numeric(18, 4))
    total_cost = Column(Numeric(18, 2))
    current_price = Column(Numeric(18, 4))
    current_value = Column(Numeric(18, 2))
    last_valuation_date = Column(Date)
    is_pre_ipo = Column(String(10))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class AlternativeTransaction(Base):
    __tablename__ = "pms_aif_transactions"
    __table_args__ = {"schema": "alternatives"}

    id = Column(Integer, primary_key=True)
    alternative_id = Column(Integer, ForeignKey("alternatives.pms_aif.id"), nullable=False)
    transaction_date = Column(Date, nullable=False)
    transaction_type = Column(String(50), nullable=False) # e.g. DRAWDOWN, REDEMPTION
    amount = Column(Numeric(18, 2), nullable=False)
    nav = Column(Numeric(18, 4))
    units = Column(Numeric(18, 4))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class AlternativeValuation(Base):
    __tablename__ = "pms_aif_valuations"
    __table_args__ = {"schema": "alternatives"}

    id = Column(Integer, primary_key=True)
    alternative_id = Column(Integer, ForeignKey("alternatives.pms_aif.id"), nullable=False)
    valuation_date = Column(Date, nullable=False)
    valuation_amount = Column(Numeric(18, 2), nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class UnlistedTransaction(Base):
    __tablename__ = "unlisted_transactions"
    __table_args__ = {"schema": "alternatives"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"), nullable=True)
    company_name = Column(String(200), nullable=False)
    transaction_date = Column(Date, nullable=False)
    transaction_type = Column(String(50), nullable=False)  # BUY, SELL, SPLIT, BONUS, etc.
    quantity = Column(Numeric(18, 4), nullable=False)
    price = Column(Numeric(18, 4))
    amount = Column(Numeric(18, 2), nullable=False)
    asset_category = Column(String(100))  # Private Equity, Traded Bonds
    broker = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class NPS(Base):
    __tablename__ = "nps"
    __table_args__ = {"schema": "alternatives"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    pran = Column(String(20), unique=True)
    account_type = Column(String(10))  # TIER1, TIER2
    fund_manager = Column(String(200))
    total_contributed = Column(Numeric(18, 2))
    current_value = Column(Numeric(18, 2))
    last_valuation_date = Column(Date)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())