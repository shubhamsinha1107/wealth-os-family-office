from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Text, ForeignKey, Enum
from sqlalchemy.sql import func
from database import Base
from models.enums import FlowType, CashType


class InvestmentCashFlow(Base):
    __tablename__ = "investment_cash_flows"
    __table_args__ = {"schema": "cash_flows"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"))
    flow_date = Column(Date, nullable=False)
    flow_type = Column(Enum(FlowType), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    asset_class = Column(String(50))  # EQUITY, MF, INSURANCE, REAL_ESTATE, GOLD etc.
    reference_id = Column(Integer)    # id from the source table
    reference_table = Column(String(100))  # which table it came from
    description = Column(String(300))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class CashPosition(Base):
    __tablename__ = "cash_positions"
    __table_args__ = {"schema": "cash_flows"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"), nullable=False)
    cash_type = Column(Enum(CashType), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    as_of_date = Column(Date, nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())