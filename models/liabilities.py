from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.sql import func
from database import Base
from models.enums import LoanType


class Loan(Base):
    __tablename__ = "loans"
    __table_args__ = {"schema": "liabilities"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    loan_type = Column(Enum(LoanType), nullable=False)
    lender_name = Column(String(200), nullable=False)
    loan_account_number = Column(String(100))
    sanction_amount = Column(Numeric(18, 2), nullable=False)
    outstanding_amount = Column(Numeric(18, 2))
    interest_rate = Column(Numeric(6, 2))
    emi_amount = Column(Numeric(18, 2))
    disbursement_date = Column(Date)
    first_emi_date = Column(Date)
    last_emi_date = Column(Date)
    is_active = Column(Boolean, default=True)
    linked_property_id = Column(Integer, ForeignKey("real_estate.properties.id"))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class LoanRepayment(Base):
    __tablename__ = "repayments"
    __table_args__ = {"schema": "liabilities"}

    id = Column(Integer, primary_key=True)
    loan_id = Column(Integer, ForeignKey("liabilities.loans.id"), nullable=False)
    payment_date = Column(Date, nullable=False)
    total_payment = Column(Numeric(18, 2), nullable=False)
    principal_component = Column(Numeric(18, 2))
    interest_component = Column(Numeric(18, 2))
    is_prepayment = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())