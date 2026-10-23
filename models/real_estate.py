from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.sql import func
from database import Base
from models.enums import PropertyType


class Property(Base):
    __tablename__ = "properties"
    __table_args__ = {"schema": "real_estate"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    property_type = Column(Enum(PropertyType), nullable=False)
    name = Column(String(200), nullable=False)
    address = Column(Text)
    city = Column(String(100))
    state = Column(String(100))
    area_sqft = Column(Numeric(10, 2))
    purchase_date = Column(Date)
    purchase_price = Column(Numeric(18, 2))
    stamp_duty_registration = Column(Numeric(18, 2))
    total_cost = Column(Numeric(18, 2))
    loan_linked = Column(Boolean, default=False)
    is_rented = Column(Boolean, default=False)
    monthly_rental = Column(Numeric(18, 2))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class PropertyValuation(Base):
    __tablename__ = "valuations"
    __table_args__ = {"schema": "real_estate"}

    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("real_estate.properties.id"), nullable=False)
    valuation_date = Column(Date, nullable=False)
    estimated_value = Column(Numeric(18, 2), nullable=False)
    valuation_source = Column(String(100))  # SELF, AGENT, REGISTERED_VALUER
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())