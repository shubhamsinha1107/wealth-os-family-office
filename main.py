# main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from routers import insurance, mutual_funds, alternatives, gold, equities, portfolio
import os

app = FastAPI(title="Family Office")

# Ensure all model tables exist on startup (safe for existing tables)
@app.on_event("startup")
def ensure_tables_exist():
    from database import engine, Base
    from sqlalchemy import text
    import models  # noqa: F401 — registers all ORM models
    import subprocess
    import sys

    # If database is fresh and unseeded, automatically populate synthetic mock portfolio
    try:
        with engine.connect() as conn:
            has_persons = conn.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'persons'")
            ).scalar()
        if not has_persons:
            print("[STARTUP] Fresh database detected — seeding synthetic mock portfolio...")
            subprocess.run([sys.executable, "seed_mock.py"], check=False)
            engine.dispose()
    except Exception as e:
        print(f"[STARTUP] Notice during auto-seed check: {e}")

    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        print(f"create_all notice: {e}")

    # Auto-migrate: add columns that were added to models after table creation.
    # create_all() only creates NEW tables; it won't ALTER existing ones.
    migrations = [
        "ALTER TABLE insurance.policies ADD COLUMN IF NOT EXISTS surrendered_amount NUMERIC(18,2)",
        "ALTER TABLE insurance.premium_payments ADD COLUMN IF NOT EXISTS transaction_type VARCHAR(30) DEFAULT 'PREMIUM'",
        "UPDATE insurance.premium_payments SET transaction_type = 'PREMIUM' WHERE transaction_type IS NULL",
        "ALTER TABLE alternatives.unlisted ADD COLUMN IF NOT EXISTS account_id INTEGER REFERENCES accounts.accounts(id)",
        "ALTER TABLE gold.holdings ADD COLUMN IF NOT EXISTS account_id INTEGER REFERENCES accounts.accounts(id)",
        "ALTER TABLE gold.holdings ADD COLUMN IF NOT EXISTS isin VARCHAR(20)",
        "ALTER TABLE gold.holdings ADD COLUMN IF NOT EXISTS last_price NUMERIC(18,2)",
        "ALTER TABLE gold.holdings ADD COLUMN IF NOT EXISTS current_value NUMERIC(18,2)",
        "ALTER TABLE gold.holdings ADD COLUMN IF NOT EXISTS as_of_date DATE",
        "ALTER TABLE equities.transactions ADD COLUMN IF NOT EXISTS stt NUMERIC(18,2) DEFAULT 0",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
            except Exception as e:
                print(f"Migration note: {e}")
        conn.commit()

# Mount static files if directory exists
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(portfolio.router)
app.include_router(insurance.router)
app.include_router(mutual_funds.router)
app.include_router(equities.router)
app.include_router(alternatives.router)
app.include_router(gold.router)


@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/portfolio/")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)

# Reload trigger


