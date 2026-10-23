from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.sql import func
from database import Base


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = {"schema": "reporting"}

    id = Column(Integer, primary_key=True)
    snapshot_date = Column(Date, nullable=False)
    person_id = Column(Integer, ForeignKey("persons.persons.id"))  # null = consolidated
    equities_value = Column(Numeric(18, 2), default=0)
    mutual_funds_value = Column(Numeric(18, 2), default=0)
    insurance_value = Column(Numeric(18, 2), default=0)
    real_estate_value = Column(Numeric(18, 2), default=0)
    gold_value = Column(Numeric(18, 2), default=0)
    alternatives_value = Column(Numeric(18, 2), default=0)
    cash_value = Column(Numeric(18, 2), default=0)
    total_assets = Column(Numeric(18, 2), default=0)
    total_liabilities = Column(Numeric(18, 2), default=0)
    net_worth = Column(Numeric(18, 2), default=0)
    is_final = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class MonthlyMetrics(Base):
    __tablename__ = "metrics_monthly"
    __table_args__ = {"schema": "reporting"}

    id = Column(Integer, primary_key=True)
    month_year = Column(String(7), nullable=False)  # YYYY-MM
    bucket_id = Column(Integer, ForeignKey("strategy_buckets.buckets.id"))
    person_id = Column(Integer, ForeignKey("persons.persons.id"))
    xirr = Column(Numeric(8, 4))
    sharpe_ratio = Column(Numeric(8, 4))
    cash_drag_pct = Column(Numeric(6, 2))
    turnover_ratio = Column(Numeric(8, 4))
    max_drawdown = Column(Numeric(8, 4))
    alpha_vs_nifty = Column(Numeric(8, 4))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class ReconciliationLog(Base):
    __tablename__ = "reconciliation_log"
    __table_args__ = {"schema": "reporting"}

    id = Column(Integer, primary_key=True)
    reconciliation_date = Column(Date, nullable=False)
    asset_class = Column(String(50), nullable=False)
    symbol_or_scheme = Column(String(300))
    person_id = Column(Integer, ForeignKey("persons.persons.id"))
    mprofit_value = Column(Numeric(18, 2))
    database_value = Column(Numeric(18, 2))
    difference = Column(Numeric(18, 2))
    status = Column(String(20))  # MATCHED, FLAGGED, RESOLVED
    resolution_notes = Column(Text)
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())