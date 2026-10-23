from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.sql import func
from database import Base
from models.enums import AccountType


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = {"schema": "accounts"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    account_type = Column(Enum(AccountType), nullable=False)
    institution_name = Column(String(200), nullable=False)
    account_number = Column(String(100))
    dp_id = Column(String(50))
    is_active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    