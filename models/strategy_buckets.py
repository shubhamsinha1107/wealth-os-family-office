from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.sql import func
from database import Base


class StrategyBucket(Base):
    __tablename__ = "buckets"
    __table_args__ = {"schema": "strategy_buckets"}

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    sort_order = Column(Integer)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class AssetClass(Base):
    __tablename__ = "asset_classes"
    __table_args__ = {"schema": "strategy_buckets"}

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    sort_order = Column(Integer)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class BucketAllocation(Base):
    __tablename__ = "bucket_allocations"
    __table_args__ = {"schema": "strategy_buckets"}

    id = Column(Integer, primary_key=True)
    asset_class_id = Column(Integer, ForeignKey("strategy_buckets.asset_classes.id"))
    strategy_bucket_id = Column(Integer, ForeignKey("strategy_buckets.buckets.id"))
    reference_id = Column(Integer, nullable=False)
    reference_table = Column(String(100), nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())