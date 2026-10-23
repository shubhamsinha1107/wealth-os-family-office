from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from env_loader import load_local_env
import os

load_local_env()

DATABASE_URL = os.getenv("DATABASE_URL")
if os.path.exists('/.dockerenv') and DATABASE_URL:
    if "@postgres" not in DATABASE_URL and "@db" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("@localhost", "@host.docker.internal").replace("@127.0.0.1", "@host.docker.internal")

engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=3600)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

