from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.sql import func
from database import Base
from models.enums import InsuranceType, InsuranceStatus, PaymentMethod, PaymentStatus


class InsurancePolicy(Base):
    __tablename__ = "policies"
    __table_args__ = {"schema": "insurance"}

    id = Column(Integer, primary_key=True)
    insured_person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=True)
    holder_person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=True)
    policy_number = Column(String(100), unique=True, nullable=False)
    insurance_type = Column(Enum(InsuranceType), nullable=False)
    insurer_name = Column(String(200), nullable=False)
    plan_name = Column(String(200))
    sum_assured = Column(Numeric(18, 2))
    premium_amount = Column(Numeric(18, 2))
    premium_frequency = Column(String(20))  # MONTHLY, QUARTERLY, ANNUAL, SINGLE
    policy_start_date = Column(Date, nullable=False)
    policy_end_date = Column(Date)
    premium_end_date = Column(Date)
    maturity_amount = Column(Numeric(18, 2))
    surrendered_amount = Column(Numeric(18, 2))
    status = Column(Enum(InsuranceStatus), default=InsuranceStatus.ACTIVE)
    agent_name = Column(String(200))
    notes = Column(Text)
    
    # Tracking fields
    file_number = Column(String(50))
    fund_name = Column(String(200))
    fund_type = Column(String(100))
    premium_term = Column(Integer)  # in years
    payment_method = Column(Enum(PaymentMethod), default=PaymentMethod.MANUAL)
    lapse_after_missed_payments = Column(Integer, default=2) # Number of missed premiums after which it lapses
    
    created_at = Column(DateTime, server_default=func.now())


class PremiumPayment(Base):
    __tablename__ = "premium_payments"
    __table_args__ = {"schema": "insurance"}

    id = Column(Integer, primary_key=True)
    policy_id = Column(Integer, ForeignKey("insurance.policies.id"), nullable=False)
    transaction_type = Column(String(30), default="PREMIUM")  # PREMIUM, PARTIAL_WITHDRAWAL, SURRENDER, MATURITY_PAYOUT
    payment_date = Column(Date, nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    payment_mode = Column(String(50))  # NEFT, CHEQUE, AUTO_DEBIT etc.
    status = Column(Enum(PaymentStatus), default=PaymentStatus.PAID)
    receipt_number = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class InsuranceNominee(Base):
    __tablename__ = "nominees"
    __table_args__ = {"schema": "insurance"}

    id = Column(Integer, primary_key=True)
    policy_id = Column(Integer, ForeignKey("insurance.policies.id"), nullable=False)
    nominee_person_id = Column(Integer, ForeignKey("persons.persons.id"))
    nominee_name = Column(String(200), nullable=False)
    relationship = Column(String(50))
    share_percentage = Column(Numeric(5, 2))
    is_minor = Column(Boolean, default=False)
    appointee_name = Column(String(200))
    created_at = Column(DateTime, server_default=func.now())


class InsuranceValuation(Base):
    __tablename__ = "valuations"
    __table_args__ = {"schema": "insurance"}

    id = Column(Integer, primary_key=True)
    policy_id = Column(Integer, ForeignKey("insurance.policies.id"), nullable=False)
    valuation_date = Column(Date, nullable=False)
    surrender_value = Column(Numeric(18, 2))
    fund_value = Column(Numeric(18, 2))
    bonus_accrued = Column(Numeric(18, 2))
    total_value = Column(Numeric(18, 2))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())