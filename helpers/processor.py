# filename: processor.py
# Complete processor module: combining, cleaning, advisor mapping, STT merge, fund-type extraction.
# NOTE: deduplication has been removed as requested.

import re
from typing import List
import pandas as pd


def get_financial_year(date):
    """Calculates the financial year for a given date."""
    if pd.isna(date):
        return None
    year = date.year
    # FY starts on April 1
    if date.month < 4:
        return f"{year-1}-{year}"
    else:
        return f"{year}-{year+1}"


# -------------------------
# Advisor mapping helper
# -------------------------
def map_advisor_value(advisor) -> str:
    """
    Robust mapping for advisor/ARN values to handle:
      - None, pd.NA, np.nan, float('nan') => 'DIRECT'
      - string literals: '', 'NA', 'N/A', '-', 'NONE', 'NAN', 'UNKNOWN' => 'DIRECT'
      - any string containing 'DIRECT' => 'DIRECT'
      - name-based tokens and ARN fragments -> friendly names
      - otherwise => 'OTHER'
    """
    if pd.isna(advisor):
        return 'DIRECT'
    s = str(advisor).strip().upper()
    s = re.sub(r'\s+', ' ', s)

    if s in {'', 'NA', 'N/A', '-', 'NONE', 'NAN', 'UNKNOWN'}:
        return 'DIRECT'
    if 'DIRECT' in s:
        return 'DIRECT'

    # name-based lookup
    name_map = {
        'NEXUS': 'Nexus Capital',
        'PRIME': 'Prime Wealth',
    }
    for k, v in name_map.items():
        if k in s:
            return v

    # ARN fragments fallback
    arn_map = {
        '100001': 'Nexus Capital',
        'ARN-100001': 'Nexus Capital',
        '100002': 'Prime Wealth',
        'ARN-100002': 'Prime Wealth',
    }
    for k, v in arn_map.items():
        if k in s:
            return v

    return 'OTHER'


# -------------------------
# Main processing pipeline
# -------------------------
def process_combined_df(df_list: List[pd.DataFrame]) -> pd.DataFrame:
    """
    Combine a list of transaction DataFrames, clean & normalize them, map advisor,
    merge STT into sales, compute financial year.

    Important behaviors:
    - Advisor values are normalized early and written to 'advisor' column.
    - STT rows are merged into corresponding sale rows when possible (date, scheme, folio, occurrence).
    - No automatic deduplication is performed (per user request).
    """
    print("\n--- Processing Combined DataFrame ---")
    if not isinstance(df_list, list) or not df_list:
        print("  -> Input is not a valid list of DataFrames. Returning empty DataFrame.")
        return pd.DataFrame()

    # Collect valid dataframes
    valid_dfs = []
    for i, df in enumerate(df_list):
        if isinstance(df, pd.DataFrame) and not df.empty:
            tmp = df.copy()
            # keep source info if you want later tracing (optional)
            tmp['_src_index'] = i
            tmp['_src_row'] = range(len(tmp))
            valid_dfs.append(tmp)

    if not valid_dfs:
        print("  -> No valid, non-empty DataFrames to process. Returning empty DataFrame.")
        return pd.DataFrame()

    print(f"  -> Combining {len(valid_dfs)} DataFrame(s).")
    combined_df = pd.concat(valid_dfs, ignore_index=True, sort=False)

    # Standardize date and numeric columns
    combined_df["date"] = pd.to_datetime(combined_df.get("date"), errors='coerce')
    combined_df.dropna(subset=["date"], inplace=True)

    numeric_cols = ['amount', 'units', 'nav']
    for col in numeric_cols:
        if col in combined_df.columns:
            combined_df[col] = pd.to_numeric(combined_df[col], errors='coerce')

    # -------------------------
    # Normalize & map advisor early
    # -------------------------
    advisor_candidates = [c for c in combined_df.columns if c.lower() in ('advisor', 'arn')]
    if advisor_candidates:
        chosen = 'advisor' if 'advisor' in combined_df.columns else advisor_candidates[0]
        try:
            combined_df['advisor'] = combined_df[chosen].apply(map_advisor_value)
        except Exception:
            combined_df['advisor'] = combined_df[chosen].map(lambda x: map_advisor_value(x) if pd.notna(x) else 'DIRECT')
    else:
        combined_df['advisor'] = 'DIRECT'

    # Normalize scheme names for transaction matching
    if 'scheme' in combined_df.columns:
        # First coerce to string and collapse whitespace
        combined_df['scheme'] = combined_df['scheme'].astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())
        # Fix common misspelling 'PALN' or 'PLN' -> 'PLAN' (case-insensitive)
        combined_df['scheme'] = combined_df['scheme'].str.replace(r'\bPALN\b', 'PLAN', flags=re.IGNORECASE, regex=True)
        combined_df['scheme'] = combined_df['scheme'].str.replace(r'\bPLN\b', 'PLAN', flags=re.IGNORECASE, regex=True)
        # Some PDFs have 'PALN' joined to other words, fix those occurrences too
        combined_df['scheme'] = combined_df['scheme'].str.replace(r'PALN', 'PLAN', flags=re.IGNORECASE, regex=True)
        # Remove 'FUND' if it appears before a hyphen (e.g. 'INFRASTRUCTURE FUND -' -> 'INFRASTRUCTURE -')
        combined_df['scheme'] = combined_df['scheme'].str.replace(r'\bFUND\s+(?=-)', '', flags=re.IGNORECASE, regex=True)
        # Finally uppercase for consistent matching downstream
        combined_df['scheme'] = combined_df['scheme'].str.upper()

    # --- Additional purchase missing amount handling ---
    additional_purchase_mask = (
        (combined_df.get('type') == 'PURCHASE') &
        (combined_df.get('description', '').str.contains('additional allotment|additional purchase', case=False, na=False)) &
        ((combined_df.get('amount').isna()) | (combined_df.get('amount') <= 0)) &
        (combined_df.get('units').notna() & (combined_df.get('units') > 0))
    )

    if isinstance(additional_purchase_mask, pd.Series) and additional_purchase_mask.any():
        count = additional_purchase_mask.sum()
        print(f"  -> Found {count} 'Additional Purchase/Allotment' transaction(s) with missing amounts.")
        for idx, row in combined_df[additional_purchase_mask].iterrows():
            if pd.notna(row.get('nav')) and row['nav'] > 0:
                calculated_amount = row['units'] * row['nav']
                combined_df.at[idx, 'amount'] = calculated_amount
                print(f"     - Calculated amount for txn on {row['date'].date()} using its own NAV.")
            else:
                time_window_start = row['date'] - pd.Timedelta(days=15)
                time_window_end = row['date'] + pd.Timedelta(days=15)
                nearby_txns = combined_df[
                    (combined_df['scheme'] == row['scheme']) &
                    (combined_df.get('folio') == row.get('folio')) &
                    (combined_df['date'].between(time_window_start, time_window_end)) &
                    (pd.notna(combined_df['nav'])) & (combined_df['nav'] > 0)
                ]
                if not nearby_txns.empty:
                    closest_idx = (nearby_txns['date'] - row['date']).abs().idxmin()
                    closest_nav = nearby_txns.loc[closest_idx, 'nav']
                    calculated_amount = row['units'] * closest_nav
                    combined_df.at[idx, 'nav'] = closest_nav
                    combined_df.at[idx, 'amount'] = calculated_amount
                    print(f"     - Calculated amount for txn on {row['date'].date()} using NAV from closest txn on {nearby_txns.loc[closest_idx,'date'].date()}.")
                else:
                    print(f"     - WARNING: Could not find a nearby transaction to estimate NAV for txn on {row['date'].date()}. Amount remains missing.")

    # Preserve original order id (useful for stable sorting later)
    combined_df = combined_df.reset_index(drop=True).reset_index().rename(columns={'index': 'temp_order_id'})

    # =========================
    # DEDUPE HAS BEEN DISABLED
    # (Intentionally skipped: do not drop duplicate rows here)
    # =========================

    # Re-split STT transactions
    stt_mask = False
    if 'type' in combined_df.columns:
        stt_mask = combined_df['type'].isin(['STT_TAX', 'STT_PAID', 'STT'])
    stt_txns = combined_df.loc[stt_mask].copy() if isinstance(stt_mask, (pd.Series, pd.Index)) else pd.DataFrame()
    main_txns = combined_df.loc[~stt_mask].copy() if isinstance(stt_mask, (pd.Series, pd.Index)) else combined_df.copy()

    # If STT exists, join carefully by (date, scheme, folio) and by occurrence count
    if not stt_txns.empty:
        print(f"  -> Found {len(stt_txns)} STT transactions to process.")
        group_cols = [c for c in ['date', 'scheme', 'folio'] if c in stt_txns.columns]
        if group_cols:
            stt_txns['stt_join_key'] = stt_txns.groupby(group_cols).cumcount()
            stt_to_merge = stt_txns[['date', 'scheme', 'folio', 'stt_join_key', 'amount']].copy().rename(columns={'amount': 'stt_amount'})

            sale_mask = False
            if 'type' in main_txns.columns:
                sale_mask = main_txns['type'].isin(['REDEMPTION', 'SWITCH_OUT'])
            if isinstance(sale_mask, (pd.Series, pd.Index)) and sale_mask.any():
                group_cols_main = [c for c in ['date', 'scheme', 'folio'] if c in main_txns.columns]
                if group_cols_main:
                    main_txns.loc[sale_mask, 'stt_join_key'] = main_txns.loc[sale_mask].groupby(group_cols_main).cumcount()

            merge_on = [c for c in ['date', 'scheme', 'folio', 'stt_join_key'] if c in main_txns.columns and c in stt_to_merge.columns]
            if merge_on:
                main_txns = pd.merge(main_txns, stt_to_merge, on=merge_on, how='left')
                main_txns['stt_amount'] = main_txns['stt_amount'].fillna(0)

                # Subtract STT from sale amounts only where units present
                non_zero_units_mask = (main_txns.get('units').notna()) & (main_txns.get('units') != 0)
                update_mask = main_txns.get('type', pd.Series()).isin(['REDEMPTION', 'SWITCH_OUT']) & non_zero_units_mask
                if isinstance(update_mask, pd.Series) and update_mask.any():
                    main_txns.loc[update_mask, 'amount'] = main_txns.loc[update_mask, 'amount'] - main_txns.loc[update_mask, 'stt_amount']
                    nonzero_units_and_update = update_mask & (main_txns['units'] != 0)
                    main_txns.loc[nonzero_units_and_update, 'nav'] = main_txns.loc[nonzero_units_and_update, 'amount'] / main_txns.loc[nonzero_units_and_update, 'units']

                main_txns.drop(columns=['stt_join_key', 'stt_amount'], inplace=True, errors='ignore')
                combined_df = main_txns.sort_values('temp_order_id').reset_index(drop=True)
                print("  -> Successfully merged STT into individual sale transactions.")
            else:
                combined_df = main_txns.sort_values('temp_order_id').reset_index(drop=True)
                print("  -> Could not construct merge keys for STT merging; skipped STT merge.")
        else:
            combined_df = main_txns.sort_values('temp_order_id').reset_index(drop=True)
            print("  -> STT found but no grouping columns present; skipped STT merge.")
    else:
        combined_df = main_txns.sort_values('temp_order_id').reset_index(drop=True)

    # Compute financial year and filter out stamp duty rows if needed
    combined_df["financial_year"] = combined_df["date"].apply(get_financial_year)
    filtered_df = combined_df[~combined_df.get("type", pd.Series()).isin(["STAMP_DUTY_TAX"])].copy()
    print(f"  -> Final DataFrame has {len(filtered_df)} rows after processing.")
    print("--- Finished Processing ---")
    # Sort by date and then by temp_order_id to preserve CAMS sequence for same-day transactions
    return filtered_df.sort_values(by=['date', 'temp_order_id']).reset_index(drop=True)


# -------------------------
# Holdings cleaning pipeline
# -------------------------
def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Cleans and standardizes the holdings (CAMS) DataFrame."""
    if df is None or not isinstance(df, pd.DataFrame):
        return pd.DataFrame()

    columns_to_remove = ['AMFI Code', 'RTA']
    df = df.drop(columns=columns_to_remove, errors='ignore').copy()

    # Normalize scheme name column: fix common typos (paln -> plan) and uppercase
    # Some PDFs have 'PALN' / 'PLN' typos; correct them before uppercasing.
    if 'Scheme Name' not in df.columns:
        df['Scheme Name'] = ''
    df['Scheme Name'] = df['Scheme Name'].astype(str)
    # collapse whitespace
    df['Scheme Name'] = df['Scheme Name'].apply(lambda x: re.sub(r'\s+', ' ', x).strip())
    # replace common misspellings / OCR artifacts related to 'plan'
    df['Scheme Name'] = df['Scheme Name'].str.replace(r'\bPALN\b', 'PLAN', flags=re.IGNORECASE, regex=True)
    df['Scheme Name'] = df['Scheme Name'].str.replace(r'\bPLN\b', 'PLAN', flags=re.IGNORECASE, regex=True)
    # replace any remaining 'PALN' occurrences inside words (defensive)
    df['Scheme Name'] = df['Scheme Name'].str.replace(r'PALN', 'PLAN', flags=re.IGNORECASE, regex=True)
    # Remove 'FUND' if it appears before a hyphen
    df['Scheme Name'] = df['Scheme Name'].str.replace(r'\bFUND\s+(?=-)', '', flags=re.IGNORECASE, regex=True)
    # final normalization to uppercase for consistent downstream matching
    df['Scheme Name'] = df['Scheme Name'].str.strip().str.upper()

    # Normalize Advisor / advisor / ARN into 'Advisor' using module-level map_advisor_value
    if 'Advisor' in df.columns:
        df['Advisor'] = df['Advisor'].apply(map_advisor_value)
    else:
        if 'advisor' in df.columns:
            df['Advisor'] = df['advisor'].apply(map_advisor_value)
        elif 'ARN' in df.columns:
            df['Advisor'] = df['ARN'].apply(map_advisor_value)
        else:
            df['Advisor'] = 'DIRECT'

    # Convert numeric columns safely
    # Convert numeric columns safely
    for col in ['Current Value', 'Cost Value', 'Units', 'NAV']:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Filter out trivial holdings
    df = df[df['Current Value'] >= 100].copy()

    df.reset_index(drop=True, inplace=True)
    return df


# -------------------------
# Fund type extraction
# -------------------------
def add_fund_type_column(df: pd.DataFrame) -> pd.DataFrame:
    """Adds a 'Fund Type' column based on scheme name matching."""
    if df is None or 'Scheme Name' not in df.columns:
        return df

    fund_type_dict = {
        r'\bCHINA\b': 'ROW',
        r'\bGLOBAL\b': 'ROW',
        r'\bCANARA ROBECO SAVINGS\b': 'LOW DURATION',
        r'quant active': 'MULTICAP', r'BANDHAN CORE EQUITY': 'LARGE & MID',
        r'DSP LARGE & MID CAP': 'LARGE & MID', r'DSP LARGE CAP': 'LARGE CAP',
        r'HDFC LARGE AND MID CAP': 'LARGE & MID', r'HDFC MID CAP': 'MIDCAP',
        r'CANARA ROBECO EMERGING EQUITIES': 'LARGE & MID',
        r'KOTAK EQUITY OPPORTUNITIES': 'LARGE & MID', r'tata equity p/?e': 'VALUE',
        r'manufactur': 'MANUFACTURE', r'multicap': 'MULTICAP', r'multi\s*cap': 'MULTICAP',
        r'bank': 'FINANCE', r'finance': 'FINANCE', r'kotak emerging equity': 'MIDCAP',
        r'healthcare': 'HEALTHCARE', r'consumption': 'CONSUMER', r'silver': 'SILVER',
        r'motilal oswal large and midcap': 'LARGE & MID',
        r'mirae asset large\s*(and|&)\s*midcap': 'LARGE & MID',
        r'large\s*(and|&)\s*mid': 'LARGE & MID', r'infrastructure|infra': 'INFRA',
        r'mid\s*cap': 'MIDCAP', r'small\s*cap': 'SMALLCAP',
        r'blue\s*chip|bluechip': 'LARGE CAP', r'large\s*cap': 'LARGE CAP',
        r'flexi': 'FLEXI', r'energy': 'ENERGY',
        r'power\s*(and|&)\s*infra': 'ENERGY & INFRA', r'multi\s*asset': 'MULTI-ASSET',
        r'arbitrage': 'ARBITRAGE', r'technology|\bit\b|\btech\b': 'IT', r'consumer': 'CONSUMER',
        r'defence': 'DEFENCE', r'focused': 'FOCUSED FUND', r'psu': 'PSU FUND',
        r'value': 'VALUE', r'momentum': 'MOMENTUM', r'gold': 'GOLD',
        r'overnight': 'OVERNIGHT',
        r'money\s*market': 'MONEY MARKET',
        r'low\s*duration': 'LOW DURATION',
        r'ultra\s*short': 'ULTRA SHORT DURATION',
        r'short\s*term|short\s*duration': 'SHORT DURATION',
        r'floater|floating': 'FLOATER',
        r'corporate\s*bond': 'CORPORATE BOND',
        r'liquid': 'LIQUID',
        r'core equity': 'LARGE & MID', r'emerging': 'LARGE & MID',
        r'franklin india prima': 'MIDCAP', r'nippon india': 'MIDCAP',
        r'dsp top 100 equity': 'LARGE CAP', r'\bMulti-Asset\b': 'MULTI-ASSET',
        r'dsp equity opportunities': 'LARGE & MID',
    }

    def extract_fund_type(name: str) -> str:
        for pat, ft in fund_type_dict.items():
            if re.search(pat, name, flags=re.IGNORECASE):
                return ft
        return 'UNKNOWN'

    df['Fund Type'] = df['Scheme Name'].apply(lambda x: extract_fund_type(str(x)))
    return df
