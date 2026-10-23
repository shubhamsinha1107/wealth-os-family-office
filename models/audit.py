from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.sql import func
from database import Base


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = {"schema": "audit"}

    id = Column(Integer, primary_key=True)
    import_date = Column(DateTime, server_default=func.now())
    asset_class = Column(String(50), nullable=False)
    source = Column(String(100))  # MPROFIT, MANUAL etc.
    file_name = Column(String(300))
    date_range_from = Column(Date)
    date_range_to = Column(Date)
    records_imported = Column(Integer, default=0)
    status = Column(String(20), default="PENDING")  # PENDING, COMPLETE, VOIDED
    voided_at = Column(DateTime)
    voided_reason = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class CorrectionJournal(Base):
    __tablename__ = "correction_journals"
    __table_args__ = {"schema": "audit"}

    id = Column(Integer, primary_key=True)
    journal_date = Column(Date, nullable=False)
    reason = Column(Text, nullable=False)
    posted_by = Column(String(100))
    is_dry_run = Column(Boolean, default=False)
    status = Column(String(20), default="DRAFT")  # DRAFT, POSTED, REVERSED
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class CorrectionLine(Base):
    __tablename__ = "correction_lines"
    __table_args__ = {"schema": "audit"}

    id = Column(Integer, primary_key=True)
    journal_id = Column(Integer, ForeignKey("audit.correction_journals.id"), nullable=False)
    asset_class = Column(String(50), nullable=False)
    reference_id = Column(Integer, nullable=False)
    reference_table = Column(String(100), nullable=False)
    action = Column(String(20), nullable=False)  # REVERSE, VOID, CORRECT
    old_value = Column(Text)
    new_value = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class MonthlyClose(Base):
    __tablename__ = "monthly_close"
    __table_args__ = {"schema": "audit"}

    id = Column(Integer, primary_key=True)
    month_year = Column(String(7), nullable=False, unique=True)  # YYYY-MM
    closed_at = Column(DateTime)
    closed_by = Column(String(100))
    is_locked = Column(Boolean, default=False)
    unlocked_at = Column(DateTime)
    unlock_reason = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())    