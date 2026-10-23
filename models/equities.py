from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Text, ForeignKey, Enum, Index
from sqlalchemy.sql import func
from database import Base
from models.enums import TransactionType, TransactionStatus


class EquityTransaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_eq_txn_person", "person_id"),
        Index("ix_eq_txn_symbol", "symbol"),
        Index("ix_eq_txn_date", "transaction_date"),
        {"schema": "equities"}
    )

    id = Column(Integer, primary_key=True)
    import_batch_id = Column(Integer, ForeignKey("audit.import_batches.id"))
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"), nullable=False)
    strategy_bucket_id = Column(Integer, ForeignKey("strategy_buckets.buckets.id"))
    symbol = Column(String(50), nullable=False)
    company_name = Column(String(200))
    isin = Column(String(20))
    transaction_date = Column(Date, nullable=False)
    transaction_type = Column(Enum(TransactionType), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=False)
    price = Column(Numeric(18, 4), nullable=False)
    brokerage = Column(Numeric(18, 2), default=0)
    taxes = Column(Numeric(18, 2), default=0)
    stt = Column(Numeric(18, 2), default=0)
    net_amount = Column(Numeric(18, 2), nullable=False)
    status = Column(Enum(TransactionStatus), default=TransactionStatus.ACTIVE)
    reversal_of_id = Column(Integer, ForeignKey("equities.transactions.id"))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class EquityHolding(Base):
    __tablename__ = "holdings"
    __table_args__ = {"schema": "equities"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"), nullable=False)
    symbol = Column(String(50), nullable=False)
    company_name = Column(String(200))
    isin = Column(String(20))
    quantity = Column(Numeric(18, 4), nullable=False)
    average_cost = Column(Numeric(18, 4))
    last_price = Column(Numeric(18, 4))
    last_price_date = Column(Date)
    current_value = Column(Numeric(18, 2))
    as_of_date = Column(Date, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = {"schema": "equities"}

    id = Column(Integer, primary_key=True)
    symbol = Column(String(50), nullable=False)
    isin = Column(String(20))
    action_type = Column(String(50), nullable=False)
    action_date = Column(Date, nullable=False)
    ratio_from = Column(Numeric(10, 4))
    ratio_to = Column(Numeric(10, 4))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class EquityRealizedGain(Base):
    __tablename__ = "realized_gains"
    __table_args__ = (
        Index("ix_eq_gain_person_fy", "person_id", "financial_year"),
        {"schema": "equities"}
    )

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    purchase_txn_id = Column(Integer, ForeignKey("equities.transactions.id"))
    sale_txn_id = Column(Integer, ForeignKey("equities.transactions.id"), nullable=False)
    symbol = Column(String(50), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"))
    company_name = Column(String(200))
    trade_type = Column(String(20), nullable=False)
    purchase_date = Column(Date)
    purchase_rate = Column(Numeric(18, 4))
    sale_date = Column(Date, nullable=False)
    sale_rate = Column(Numeric(18, 4), nullable=False)
    units_sold = Column(Numeric(18, 4), nullable=False)
    cost_value = Column(Numeric(18, 2), nullable=False)
    sale_value = Column(Numeric(18, 2), nullable=False)
    gain_loss = Column(Numeric(18, 2), nullable=False)
    holding_days = Column(Integer, nullable=False)
    gain_type = Column(String(20), nullable=False)
    tax_category = Column(String(50), nullable=False)
    asset_class = Column(String(50), nullable=False)
    tax_rate = Column(String(20), nullable=False)
    tax_section = Column(String(20), nullable=False)
    tax_label = Column(String(100), nullable=False)
    financial_year = Column(String(20), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class EquityRemainingLot(Base):
    __tablename__ = "remaining_lots"
    __table_args__ = {"schema": "equities"}

    purchase_txn_id = Column(Integer, ForeignKey("equities.transactions.id"), primary_key=True)
    remaining_units = Column(Numeric(18, 4), nullable=False)
    price = Column(Numeric(18, 4), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
