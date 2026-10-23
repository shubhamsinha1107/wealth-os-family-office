from sqlalchemy import Column, Integer, String, Date, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.sql import func
from database import Base
from models.enums import PersonType


class Person(Base):
    __tablename__ = "persons"
    __table_args__ = {"schema": "persons"}

    id = Column(Integer, primary_key=True)
    full_name = Column(String(200), nullable=False)
    short_name = Column(String(50))
    person_type = Column(Enum(PersonType), nullable=False, default=PersonType.INDIVIDUAL)
    pan = Column(String(10), unique=True)
    date_of_birth = Column(Date)
    is_active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = {"schema": "persons"}

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    related_to_id = Column(Integer, ForeignKey("persons.persons.id"), nullable=False)
    relationship_type = Column(String(50))
    created_at = Column(DateTime, server_default=func.now())