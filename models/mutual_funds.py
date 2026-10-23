from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Text, ForeignKey, Enum, Index
from sqlalchemy.sql import func
from database import Base
from models.enums import TransactionStatus


class MFTransaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_mf_txn_person", "person_id"),
        Index("ix_mf_txn_date", "transaction_date"),
        {"schema": "mutual_funds"}
    )

    id = Column(Integer, primary_key=True)
    import_batch_id = Column(Integer, ForeignKey("audit.import_batches.id"))
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"), nullable=False)
    strategy_bucket_id = Column(Integer, ForeignKey("strategy_buckets.buckets.id"))
    folio_number = Column(String(50))
    scheme_name = Column(String(300), nullable=False)
    amfi_code = Column(String(20))
    isin = Column(String(20))
    transaction_date = Column(Date, nullable=False)
    transaction_type = Column(String(20), nullable=False)
    units = Column(Numeric(18, 4), nullable=False)
    nav = Column(Numeric(18, 4), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    status = Column(Enum(TransactionStatus), default=TransactionStatus.ACTIVE)
    reversal_of_id = Column(Integer, ForeignKey("mutual_funds.transactions.id"))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class MFHolding(Base):
    __tablename__ = "holdings"
    __table_args__ = {"schema": "mutual_funds"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.accounts.id"), nullable=False)
    folio_number = Column(String(50))
    scheme_name = Column(String(300), nullable=False)
    amfi_code = Column(String(20))
    isin = Column(String(20))
    units = Column(Numeric(18, 4), nullable=False)
    average_nav = Column(Numeric(18, 4))
    current_nav = Column(Numeric(18, 4))
    current_value = Column(Numeric(18, 2))
    as_of_date = Column(Date, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class MFDetail(Base):
    __tablename__ = "mf_details"
    __table_args__ = {"schema": "mutual_funds"}

    id = Column(Integer, primary_key=True)
    scheme_name = Column(String(300), nullable=False, unique=True)
    clean_name = Column(String(300))
    fund_type = Column(String(100))
    advisor = Column(String(100))
    exit_load_pct = Column(Numeric(5, 2), default=0.0)
    no_exit_load_tenure = Column(Integer, default=0) # in days
    long_term_tenure = Column(Integer, default=365) # in days (365 or 730)
    created_at = Column(DateTime, server_default=func.now())


class MFRealizedGain(Base):
    __tablename__ = "realized_gains"
    __table_args__ = (
        Index("ix_mf_gain_person_fy", "person_id", "financial_year"),
        {"schema": "mutual_funds"}
    )

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    purchase_txn_id = Column(Integer, ForeignKey("mutual_funds.transactions.id"))
    sale_txn_id = Column(Integer, ForeignKey("mutual_funds.transactions.id"), nullable=False)
    folio_number = Column(String(50))
    scheme_name = Column(String(300), nullable=False)
    advisor = Column(String(100))
    isin = Column(String(20))
    amfi_code = Column(String(20))
    purchase_date = Column(Date)
    purchase_rate = Column(Numeric(18, 4))
    sale_date = Column(Date, nullable=False)
    sale_rate = Column(Numeric(18, 4), nullable=False)
    units_sold = Column(Numeric(18, 4), nullable=False)
    cost_value = Column(Numeric(18, 2), nullable=False)
    sale_value = Column(Numeric(18, 2), nullable=False)
    gain_loss = Column(Numeric(18, 2), nullable=False)
    holding_days = Column(Integer, nullable=False)
    tax_category = Column(String(50), nullable=False)
    financial_year = Column(String(20), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class MFRemainingLot(Base):
    __tablename__ = "remaining_lots"
    __table_args__ = {"schema": "mutual_funds"}

    purchase_txn_id = Column(Integer, ForeignKey("mutual_funds.transactions.id"), primary_key=True)
    remaining_units = Column(Numeric(18, 4), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
