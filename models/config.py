from sqlalchemy import Column, Integer, String, Numeric, DateTime, Boolean, Text
from sqlalchemy.sql import func
from database import Base


class AssetAllocationTarget(Base):
    __tablename__ = "asset_allocation_targets"
    __table_args__ = {"schema": "config"}

    id = Column(Integer, primary_key=True)
    asset_class = Column(String(50), nullable=False, unique=True)
    target_pct = Column(Numeric(5, 2), nullable=False)
    min_pct = Column(Numeric(5, 2))
    max_pct = Column(Numeric(5, 2))
    is_active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class BenchmarkConfig(Base):
    __tablename__ = "benchmarks"
    __table_args__ = {"schema": "config"}

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    index_symbol = Column(String(50), nullable=False)
    is_primary = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class SystemParameter(Base):
    __tablename__ = "system_parameters"
    __table_args__ = {"schema": "config"}

    id = Column(Integer, primary_key=True)
    key = Column(String(100), nullable=False, unique=True)
    value = Column(Text, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())