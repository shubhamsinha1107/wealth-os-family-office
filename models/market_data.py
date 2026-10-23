from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Text
from sqlalchemy.sql import func
from database import Base


class IndexPrice(Base):
    __tablename__ = "index_prices"
    __table_args__ = {"schema": "market_data"}

    id = Column(Integer, primary_key=True)
    index_symbol = Column(String(50), nullable=False)
    price_date = Column(Date, nullable=False)
    open_price = Column(Numeric(18, 4))
    high_price = Column(Numeric(18, 4))
    low_price = Column(Numeric(18, 4))
    close_price = Column(Numeric(18, 4), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class MFNav(Base):
    __tablename__ = "mf_nav"
    __table_args__ = {"schema": "market_data"}

    id = Column(Integer, primary_key=True)
    amfi_code = Column(String(20), nullable=False)
    scheme_name = Column(String(300))
    nav_date = Column(Date, nullable=False)
    nav = Column(Numeric(18, 4), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class GoldPrice(Base):
    __tablename__ = "gold_prices"
    __table_args__ = {"schema": "market_data"}

    id = Column(Integer, primary_key=True)
    price_date = Column(Date, nullable=False, unique=True)
    price_per_gram_24k = Column(Numeric(18, 2), nullable=False)
    price_per_gram_22k = Column(Numeric(18, 2))
    source = Column(String(100))
    created_at = Column(DateTime, server_default=func.now())