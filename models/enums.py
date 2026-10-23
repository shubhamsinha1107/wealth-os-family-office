import enum


class PersonType(enum.Enum):
    INDIVIDUAL = "INDIVIDUAL"
    HUF = "HUF"
    COMPANY = "COMPANY"
    TRUST = "TRUST"


class AccountType(enum.Enum):
    DEMAT = "DEMAT"
    TRADING = "TRADING"
    BANK = "BANK"
    MF_FOLIO = "MF_FOLIO"
    NPS = "NPS"
    PMS = "PMS"
    AIF = "AIF"


class TransactionType(enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    BONUS = "BONUS"
    SPLIT = "SPLIT"
    RIGHTS = "RIGHTS"
    MERGER = "MERGER"
    DEMERGER = "DEMERGER"


class TransactionStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    REVERSED = "REVERSED"
    VOIDED = "VOIDED"


class FlowType(enum.Enum):
    INVESTED = "INVESTED"
    REDEEMED = "REDEEMED"
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class InsuranceType(enum.Enum):
    TERM = "TERM"
    ENDOWMENT = "ENDOWMENT"
    ULIP = "ULIP"
    WHOLE_LIFE = "WHOLE_LIFE"
    HEALTH = "HEALTH"
    VEHICLE = "VEHICLE"
    PROPERTY = "PROPERTY"


class InsuranceStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    LAPSED = "LAPSED"
    SURRENDERED = "SURRENDERED"
    MATURED = "MATURED"
    CLAIMED = "CLAIMED"


class LoanType(enum.Enum):
    HOME = "HOME"
    LAP = "LAP"
    PERSONAL = "PERSONAL"
    VEHICLE = "VEHICLE"
    BUSINESS = "BUSINESS"
    OVERDRAFT = "OVERDRAFT"


class GoldType(enum.Enum):
    PHYSICAL = "PHYSICAL"
    SGB = "SGB"
    DIGITAL = "DIGITAL"
    ETF = "ETF"


class CashType(enum.Enum):
    STRATEGIC = "STRATEGIC"
    TACTICAL = "TACTICAL"
    OPERATIONAL = "OPERATIONAL"
    IDLE = "IDLE"


class PropertyType(enum.Enum):
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    LAND = "LAND"
    INDUSTRIAL = "INDUSTRIAL"


class AlternativeType(enum.Enum):
    PMS = "PMS"
    AIF = "AIF"
    UNLISTED = "UNLISTED"
    PRE_IPO = "PRE_IPO"
    NPS = "NPS"


class PaymentMethod(enum.Enum):
    MANUAL = "MANUAL"
    ECS = "ECS"


class PaymentStatus(enum.Enum):
    PAID = "PAID"
    UNPAID = "UNPAID"
    SKIPPED = "SKIPPED"