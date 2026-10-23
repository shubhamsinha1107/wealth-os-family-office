from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Text, ForeignKey, Enum
from sqlalchemy.sql import func
from database import Base
from models.enums import GoldType


class GoldHolding(Base):
    __tablename__ = "holdings"
    __table_args__ = {"schema": "gold"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    gold_type = Column(Enum(GoldType), nullable=False)
    description = Column(String(200))
    quantity_grams = Column(Numeric(10, 4), nullable=False)
    purchase_date = Column(Date)
    purchase_price_per_gram = Column(Numeric(18, 2))
    total_cost = Column(Numeric(18, 2))
    purity = Column(String(10))  # 24K, 22K, 999 etc.
    storage_location = Column(String(200))
    # SGB specific
    sgb_series = Column(String(50))
    sgb_maturity_date = Column(Date)
    sgb_units = Column(Numeric(10, 4))
    sgb_issue_price = Column(Numeric(18, 2))
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"))
    isin = Column(String(20))
    last_price = Column(Numeric(18, 2))
    current_value = Column(Numeric(18, 2))
    as_of_date = Column(Date)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())



class GoldValuation(Base):
    __tablename__ = "valuations"
    __table_args__ = {"schema": "gold"}

    id = Column(Integer, primary_key=True)
    valuation_date = Column(Date, nullable=False)
    price_per_gram_24k = Column(Numeric(18, 2), nullable=False)
    price_per_gram_22k = Column(Numeric(18, 2))
    source = Column(String(100))
    created_at = Column(DateTime, server_default=func.now())
    