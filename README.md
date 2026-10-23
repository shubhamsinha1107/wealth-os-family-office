# 🏛️ WealthOS: Multi-Asset Family Office Investment Ledger & Analytics Engine

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%2B-336791.svg)](https://www.postgresql.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-red.svg)](https://www.sqlalchemy.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![Architecture: Open-Core](https://img.shields.io/badge/Architecture-Open--Core%20Showcase-purple.svg)](#-open-core-architecture--security)
[![License: Source-Available](https://img.shields.io/badge/License-Source--Available%20%7C%20Showcase%20Only-red.svg)](LICENSE)

An enterprise-grade, multi-asset portfolio accounting and tax-intelligence engine built from scratch for **Multi-Entity High-Net-Worth (HNW) Family Offices and Private Trusts**.

> ⚠️ **Portfolio Showcase Edition**: This repository is made source-available solely for technical evaluation, architectural review, and demonstration purposes. Commercial deployment, resale, or unauthorized redistribution is strictly prohibited. See [LICENSE](LICENSE) for details.

Consolidates fragmented investments across multiple family members, holding companies, demat accounts, and custodians into a unified, sovereign source of truth—without relying on third-party cloud aggregators.

---

## 🖥️ Platform Previews (Synthetically Seeded UI)

The screenshots below are rendered directly from the platform's production HTML/CSS glassmorphic UI templates populated with 100% synthetic mock numbers:

### 1. Consolidated Multi-Entity Portfolio Balance Sheet
Consolidates cross-asset allocations (Equities, Mutual Funds, SGBs, Alternatives, Real Estate) across all family entities with multi-year realized & unrealized capital gains.
![Consolidated Portfolio Dashboard](assets/consolidated_portfolio_dashboard.png)

### 2. Listed Equities Portfolio & P&L Engine
Tracks multi-broker demat holdings, FIFO average purchase costs, real-time market value, and percentage returns.
![Equities Portfolio Overview](assets/equities_portfolio_overview.png)

### 3. Mutual Funds Portfolio & Distribution Analytics
Tracks folios, Direct vs Regular plan categorization, advisor distribution, and benchmark outperformance.
![Mutual Funds Analytics Dashboard](assets/mutual_funds_analytics_dashboard.png)

### 4. AMFI-Synced TER Fee Drag & Leakage Calculator
Quantifies distributor commission leakage, blended portfolio expense ratios, and 5-year fee drag projections across broker channels.
![TER Expense Drag Analytics](assets/ter_expense_drag_analytics.png)

### 5. Sovereign Gold Bonds (SGB) Tranche & Secondary Market Tracker
Tracks primary issuances and secondary market purchases across all RBI tranches with maturity schedules and yield-to-maturity (YTM) modeling.
![Sovereign Gold Bonds Portfolio](assets/gold_sgb_portfolio.png)

---

## 📌 Problem: Why Off-the-Shelf Tools Break for Family Offices

1. **Multi-Entity Ownership**: Wealth is rarely held by one person; it spans individuals, holding companies, private trusts, and HUFs.
2. **Hidden Expense Drag (TER Leakage)**: Traditional advisors rarely quantify how much wealth is lost every year to distributor commissions in **Regular vs. Direct mutual fund plans**.
3. **Complex Tax Lot Matching**: Calculating true Short-Term (STCG) and Long-Term (LTCG) Capital Gains across decades of corporate actions (bonuses, splits, mergers) requires strict **First-In-First-Out (FIFO)** lot matching.
4. **Data Sovereignty**: HNW families cannot risk feeding sensitive balance sheets and trade books into consumer SaaS platforms.

---

## 🚀 Key Architectural Capabilities

### 1. 5-Tier FIFO Capital Gains & Tax Engine
- Custom-engineered lot-matching algorithms for realized and unrealized capital gains.
- Dynamic categorization of Short-Term and Long-Term Capital Gains matching Indian tax standards.
- Handles corporate actions (stock splits, bonus issues, face-value adjustments) without corrupting historical cost baselines.

### 2. AMFI-Synced TER Fee Drag & Advisor Analyzer
- Scrapes and syncs live AMFI Total Expense Ratio (TER) benchmarks.
- Dynamically pairs Regular mutual fund folios with their Direct plan equivalents.
- Computes the exact rupee-value fee leakage and annualized drag across the consolidated portfolio.

### 3. Automated Ingestion & CAS Normalization
- Automated parser for **CAMS / KFintech Consolidated Account Statements (CAS)**.
- Reconciles trade books across major institutional brokers.
- Live market data integration with automated NAV synchronizer.

### 4. Multi-Entity Unified Balance Sheet
- Hierarchical structure: `Family Entity / Holding Co. -> Broker Accounts -> Folios / Holdings`.
- Comprehensive multi-asset support:
  - **Listed Equities**: Real-time P&L, sector weights, dividend yields.
  - **Mutual Funds**: Direct vs Regular split, category alpha, CAGR/XIRR.
  - **Gold & SGB**: Sovereign Gold Bond tranche tracking, redemption dates, yield-to-maturity.
  - **Insurance**: Term life, ULIP surrender values, premium payment schedules.
  - **Alternatives & Real Estate**: AIF Cat II/III, Pre-IPO shares, and commercial real estate.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph Data Ingestion Layer
        A[CAMS / KFintech CAS PDFs] --> P1[CAMS Parser & CAS Normalizer]
        B[Broker Trade Books - Excel/CSV] --> P2[Equity / Gold Ingester]
        C[AMFI & Market Data Feeds] --> P3[Live NAV & Pricing Synchronizer]
    end

    subgraph Core Financial Calculation Engines
        P1 --> FIFO[5-Tier FIFO Tax-Lot Matching Engine]
        P2 --> FIFO
        P3 --> FIFO
        FIFO --> TER[TER Expense Drag Analyzer]
        FIFO --> PERF[AUM & Performance Analytics Engine]
        FIFO --> ALPHA[Category Alpha & Benchmark Comparator]
    end

    subgraph Persistence & Application
        TER --> DB[(PostgreSQL 16 Multi-Schema DB)]
        PERF --> DB
        ALPHA --> DB
        DB --> API[FastAPI Backend Services]
        API --> UI[Consolidated Multi-Entity Dashboard]
    end
```

---

## 📂 Repository Structure

```
wealth-os-family-office/
├── models/                  # SQLAlchemy 2.0 multi-schema database models
│   ├── persons.py           # Entities, individuals, and relationship graphs
│   ├── accounts.py          # Trading and demat accounts across brokers
│   ├── equities.py          # Listed equity transactions, holdings, corporate actions
│   ├── mutual_funds.py      # MF folios, transactions, and AMFI scheme details
│   ├── gold.py              # Sovereign Gold Bonds (SGB) and physical gold
│   ├── insurance.py         # Life, ULIP, and term policies + surrender values
│   ├── alternatives.py      # AIF, pre-IPO, and private unlisted shares
│   └── strategy_buckets.py  # Strategic asset allocation buckets
├── routers/                 # FastAPI REST and web route controllers
│   ├── portfolio.py         # Consolidated family office balance sheet
│   ├── mutual_funds.py      # Mutual fund analytics, SIPs, and PnL reports
│   ├── equities.py          # Listed equity holdings and FIFO capital gains
│   ├── gold.py              # SGB tracking and valuation
│   └── insurance.py         # Policy status and premium calendar
├── services/                # Algorithmic calculation engines
│   ├── fifo_engine.py       # First-In-First-Out capital gains matching engine
│   ├── ter_engine.py        # Mutual fund expense ratio drag calculator
│   ├── cams_ingester.py     # CAMS / KFintech CAS parser
│   ├── benchmark_comparison_engine.py # Relative alpha vs Nifty 50 / 500
│   └── xlsx_exporter.py     # High-fidelity Excel report compiler
├── templates/               # Responsive dashboard UI (Jinja2 + Tailwind)
├── static/                  # CSS styling and client-side filtering scripts
├── seed_mock.py             # Synthetic multi-entity portfolio generator
├── main.py                  # Application entry point with schema auto-init
├── database.py              # SQLAlchemy engine and connection pooling
├── docker-compose.yml       # Production-ready multi-container orchestration
└── Dockerfile               # Container packaging
```

---

## ⚡ Quickstart (Run in 2 Minutes)

### Option A: Via Docker Compose (Recommended)

1. **Clone the repository**:
   ```bash
   git clone https://github.com/<your-username>/wealth-os-family-office.git
   cd wealth-os-family-office
   ```

2. **Launch with Docker Compose**:
   ```bash
   docker compose up --build
   ```

3. **Open the Dashboard**:
   - Web Dashboard: `http://localhost:8000/portfolio/`
   - Interactive API Docs: `http://localhost:8000/docs`

> *Note: On first startup, the database automatically initializes all PostgreSQL schemas and populates a synthetic family office portfolio with mock equities, mutual funds, SGBs, and insurance policies.*

---

### Option B: Local Virtualenv Setup

1. **Install dependencies**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Set your environment variable** (or use local SQLite/PostgreSQL):
   ```bash
   export DATABASE_URL="postgresql://postgres:postgrespassword@localhost:5432/family_office"
   ```

3. **Seed synthetic data**:
   ```bash
   python seed_mock.py
   ```

4. **Start the server**:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

---

## 🔒 Open-Core Architecture & Security

This public repository is published as an **Open-Core Showcase Edition**:
- **Full Operational Core**: The PostgreSQL 16 multi-entity schema, SQLAlchemy 2.0 ORM models, FastAPI backend endpoints, responsive glassmorphic dark-theme UI templates, and the synthetic portfolio generator (`seed_mock.py`) are 100% open, reproducible, and run out of the box via Docker Compose.
- **Reference Calculation Engines**: Algorithmic modules (`services/fifo_engine.py`, `services/ter_engine.py`, `services/cams_ingester.py`) are structured as clean Open-Core reference implementations and standard mathematical models. Proprietary client-specific mapping rules, private AMFI scraping caches, and internal decryption routines remain protected.
- **Strict Zero-PII Guarantee**: All names, PAN cards, folios, trade books, and portfolio figures displayed across the repository and screenshots are 100% synthetic mock numbers.

---

## 🛡️ Data Privacy & Synthetic Data Notice

All personal names, PAN cards, account numbers, and holdings included in this public repository are **100% synthetic mock data** generated by `seed_mock.py`. No real client records, confidential statements, or proprietary keys have been included.

---

## 📄 License & Intellectual Property

Copyright (c) 2026 Shubham Sinha. All rights reserved.

This project is licensed under a custom **[Source-Available Portfolio Showcase License](LICENSE)**:
- **Evaluation & Demonstration Only**: Permitted for viewing, learning, architectural critique, and private non-commercial evaluation.
- **Strictly Prohibited**: Commercial deployment, reselling, sublicensing, SaaS operation, or incorporating any portion into commercial software or family office operations without prior written authorization.
- **Commercial Licensing**: For commercial licensing, consulting engagements, or enterprise deployments, please contact the author directly via GitHub [@shubhamsinha1107](https://github.com/shubhamsinha1107).
