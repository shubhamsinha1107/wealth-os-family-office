# filename: analysis.py
# This module contains functions for performing financial analysis and generating reports.

import pandas as pd
import os
from datetime import datetime, date, timedelta
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from tqdm import tqdm
import re

def _normalize_for_match(s):
    if pd.isna(s): return ""
    s = str(s).upper()
    # Strip (NON DEMAT) or (NON-DEMAT) or (NON...) suffixes to handle holding type variations
    s = re.sub(r'\s*\(\s*NON\b[^)]*\)?', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bNON\s*-?\s*DEMAT\b', '', s, flags=re.IGNORECASE)
    # Remove common noise words that differ between CAMS PDFs and DB records
    # e.g. "EQUITY ARBITRAGE FUND" vs "ARBITRAGE FUND", "IDCW PLAN - Payout" vs "IDCW"
    # NOTE: Do NOT strip REGULAR, DIRECT, or PLAN as these distinguish fund variants
    noise_words = ['FUND', 'MF', 'OPTION', 'PAYOUT', 'EQUITY', 'SCHEME']
    for w in noise_words:
        s = re.sub(r'\b' + w + r'\b', '', s)
    # Strip "PLAN" only when it follows IDCW (to match "IDCW" vs "IDCW PLAN")
    s = re.sub(r'\bIDCW\s+PLAN\b', 'IDCW', s)
    # Remove all non-alphanumeric
    s = re.sub(r'[^A-Z0-9]', '', s)
    return s.strip()

def _normalize_folio(f):
    if pd.isna(f): return ""
    return str(f).split('/')[0].strip()


def _load_folder_data(file_path):
    """Loads and combines folder mapping data from all sheets of a single Excel file."""
    if not file_path or not os.path.exists(file_path):
        print("  -> Folder file not provided or not found. Proceeding without folder numbers.")
        return pd.DataFrame()

    try:
        print(f"  -> Reading all sheets from folder file: {file_path}")
        all_sheets = pd.read_excel(file_path, sheet_name=None)
        
        if not all_sheets:
            print("  -> No sheets found in the folder file.")
            return pd.DataFrame()

        combined_df = pd.concat(all_sheets.values(), ignore_index=True)
        print(f"  -> Combined {len(all_sheets)} sheets into a single DataFrame.")

        combined_df.columns = [str(col).strip() for col in combined_df.columns]
        folio_col = next((col for col in combined_df.columns if 'folio' in col.lower()), None)
        scheme_col = next((col for col in combined_df.columns if 'scheme' in col.lower()), None)
        folder_col = next((col for col in combined_df.columns if 'folder' in col.lower()), None)

        if not all([folio_col, scheme_col, folder_col]):
            print("  -> ERROR: Could not find required columns ('Folio', 'Scheme Name', 'FOLDER') in the folder file.")
            return pd.DataFrame()

        folder_map = combined_df[[folio_col, scheme_col, folder_col]].copy()
        folder_map.rename(columns={
            folio_col: 'Folio', scheme_col: 'Scheme', folder_col: 'Folder'
        }, inplace=True)

        folder_map.dropna(subset=['Folio', 'Scheme', 'Folder'], inplace=True)
        folder_map = folder_map[folder_map['Folder'].astype(str).str.lower().str.strip() != 'xxxx']
        
        folder_map['Folio'] = folder_map['Folio'].astype(str).str.split('/').str[0].str.strip()
        folder_map['Scheme'] = folder_map['Scheme'].astype(str).str.strip().str.lower()
        folder_map.drop_duplicates(subset=['Folio', 'Scheme'], keep='first', inplace=True)
        
        print(f"  -> Successfully loaded and processed {len(folder_map)} unique folder mappings.")
        return folder_map

    except Exception as e:
        print(f"  -> ERROR: Failed to load folder data from {file_path}. Error: {e}")
        return pd.DataFrame()

def _calculate_all_time_gains_and_redemptions(combined_df, merger_map=None):
    """
    Calculates gains across all time, respecting merger dates for FIFO calculation.
    Prioritizes sales from the same folio before considering merged folios.
    Returns a DataFrame of all gains, a list of redeemed portions, and the cleaned DataFrame.
    """
    if merger_map is None:
        merger_map = {}

    combined = combined_df.copy()

    required_cols = ['date', 'scheme', 'units', 'nav', 'advisor', 'folio']
    # Check if critical columns match (if missing, likely empty or malformed DF)
    missing_cols = [c for c in required_cols if c not in combined.columns]
    if missing_cols:
        if not combined.empty:
            print(f"  -> Warning: Missing keys {missing_cols} in transaction data. Skipping gains calculation.")
        return pd.DataFrame(), [], combined

    combined['date'] = pd.to_datetime(combined['date'])
    numeric_cols = ['amount', 'units', 'nav']
    for col in numeric_cols:
        combined[col] = pd.to_numeric(combined[col], errors='coerce')
    combined.dropna(subset=['date', 'scheme', 'units', 'nav', 'advisor', 'folio'], inplace=True)
    combined['folio'] = combined['folio'].apply(_normalize_folio)

    # --- REVERSAL NEUTRALIZATION LOGIC ---
    print("\n--- Starting Reversal Neutralization Process ---")

    # --- MODIFIED LOGIC START ---
    # 1. Find explicit REVERSAL types
    explicit_reversals = combined[combined['type'] == 'REVERSAL']

    # 2. Find REDEMPTIONs that are actually reversals by checking keywords
    # Added 'unrealised' and 'rejected' for more robust future-proofing
    reversal_keywords = ['payment not received', 'unrealised', 'rejected']
    mislabeled_redemptions = combined[
        (combined['type'] == 'REDEMPTION') &
        (combined['description'].str.contains('|'.join(reversal_keywords), case=False, na=False))
    ]

    # 3. Combine them into one list of transactions to neutralize
    transactions_to_neutralize = pd.concat([explicit_reversals, mislabeled_redemptions])
    # --- MODIFIED LOGIC END ---
    
    potential_purchase_matches = combined[combined['type'].isin(['PURCHASE', 'PURCHASE_SIP'])].copy()
    indices_to_drop = []

    if not transactions_to_neutralize.empty:
        for rev_idx, reversal in transactions_to_neutralize.iterrows():
            if rev_idx in indices_to_drop:
                continue

            date_window_start = reversal['date'] - pd.Timedelta(days=5)
            date_window_end = reversal['date'] + pd.Timedelta(days=5)

            match_conditions = (
                (potential_purchase_matches['date'] >= date_window_start) &
                (potential_purchase_matches['date'] <= date_window_end) &
                (potential_purchase_matches['scheme'] == reversal['scheme']) &
                (potential_purchase_matches['folio'] == reversal['folio']) &
                (potential_purchase_matches['advisor'] == reversal['advisor']) &
                (potential_purchase_matches['amount'].round(2) == -round(reversal['amount'], 2))
            )
            
            matched_purchases = potential_purchase_matches[match_conditions]
            
            if not matched_purchases.empty:
                match_idx = abs(matched_purchases['date'] - reversal['date']).idxmin()
                
                if match_idx not in indices_to_drop:
                    print(f"  - Matched and neutralizing Reversal/Failed Txn (Index: {rev_idx}) with Purchase (Index: {match_idx}) for scheme '{reversal['scheme']}'.")
                    indices_to_drop.extend([rev_idx, match_idx])
                    potential_purchase_matches.drop(match_idx, inplace=True)
                else:
                    print(f"  - WARNING: Found a potential purchase match for Reversal (Index: {rev_idx}) but it was already neutralized.")
            else:
                print(f"  - WARNING: No matching purchase found for Reversal/Failed Txn (Index: {rev_idx}). It will be ignored.")
                indices_to_drop.append(rev_idx)

    if indices_to_drop:
        unique_indices_to_drop = list(set(indices_to_drop))
        print(f"--- Neutralizing {len(unique_indices_to_drop)} transactions (reversals, failed payments, and their purchases) ---")
        combined.drop(unique_indices_to_drop, inplace=True)
    else:
        print("--- No reversals or failed payments found to neutralize. ---")

    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVEST', 'DIVIDEND_REINVESTMENT']
    sale_types = ['REDEMPTION', 'SWITCH_OUT'] 
    purchases = combined[combined['type'].isin(purchase_types)].copy()
    sales = combined[combined['type'].isin(sale_types)].copy()
    sales['orig_sale_index'] = sales.index
    
    # Associate STT with sales
    stt_tax_types = ['STT_TAX', 'STT_PAID', 'STT']
    stt_txns = combined[combined['type'].isin(stt_tax_types)].copy()
    
    sales['stt_amount'] = 0.0
    if not stt_txns.empty:
        group_cols = [c for c in ['date', 'scheme', 'folio'] if c in stt_txns.columns]
        if group_cols:
            stt_txns['stt_join_key'] = stt_txns.groupby(group_cols).cumcount()
            stt_to_merge = stt_txns[group_cols + ['stt_join_key', 'amount']].copy().rename(columns={'amount': 'matched_stt'})
            sales['stt_join_key'] = sales.groupby(group_cols).cumcount()
            sales = pd.merge(sales, stt_to_merge, on=group_cols + ['stt_join_key'], how='left')
            sales['stt_amount'] = sales['matched_stt'].fillna(0.0)
            sales.drop(columns=['stt_join_key', 'matched_stt'], inplace=True, errors='ignore')
            
    # Fallback: if stt_amount is 0 or missing, check if units * nav > abs(amount) and description indicates STT
    calc_sale_amt = (abs(sales['units']) * sales['nav']).round(2)
    diff = (calc_sale_amt - abs(sales['amount'])).round(2)
    desc_series = sales.get('description', pd.Series('', index=sales.index)).astype(str)
    has_stt_desc = desc_series.str.contains('less STT|STT', case=False, na=False)
    fallback_mask = (sales['stt_amount'] <= 0.001) & (diff > 0.01) & (diff < 10000) & has_stt_desc
    sales.loc[fallback_mask, 'stt_amount'] = diff[fallback_mask]
    
    purchases['remaining_units'] = purchases['units']
    gains_summary = []
    redeemed_portions = []

    merger_details = {}
    for (scheme, old_folio), details in merger_map.items():
        new_folio = details['new_folio']
        merger_date = pd.to_datetime(details['merger_date'])
        merger_details[(scheme, new_folio)] = {'old_folio': old_folio, 'merger_date': merger_date}

    sales_sort_cols = ['date']
    if 'temp_order_id' in sales.columns:
        sales_sort_cols.append('temp_order_id')
    
    for _, sale in sales.sort_values(by=sales_sort_cols).iterrows():
        units_to_sell = abs(sale['units'])
        total_sale_units = abs(sale['units'])
        sale_stt_amt = float(sale.get('stt_amount', 0.0))
        # Gross sale consideration: add STT to the sale amount
        gross_sale_amt = abs(sale['amount']) + sale_stt_amt
        sale_key = (sale['scheme'], sale['folio'])
        folios_to_search = [sale['folio']]
        
        if sale_key in merger_details and sale['date'] >= merger_details[sale_key]['merger_date']:
            folios_to_search.append(merger_details[sale_key]['old_folio'])

        # Allow same-day purchases IF they occurred before the sale (based on temp_order_id)
        # First get all candidates <= sale date
        potential_purchases = purchases[
            (purchases['scheme'] == sale['scheme']) & 
            (purchases['folio'].isin(folios_to_search)) &
            (purchases['date'] <= sale['date']) & 
            (purchases['remaining_units'] > 0.001)
        ].copy()

        # Refine for proper sequence
        if 'temp_order_id' in potential_purchases.columns and 'temp_order_id' in sale:
            potential_purchases = potential_purchases[
                (potential_purchases['date'] < sale['date']) |
                ((potential_purchases['date'] == sale['date']) & (potential_purchases['temp_order_id'] < sale['temp_order_id']))
            ]
        else:
            # Fallback if ID invalid/missing: allow same-day purchases
            potential_purchases = potential_purchases[potential_purchases['date'] <= sale['date']]

        potential_purchases['is_same_folio'] = potential_purchases['folio'] == sale['folio']
        
        # Sort purchases for FIFO: Date ASC, then ID ASC
        purch_sort_keys = ['is_same_folio', 'date']
        purch_asc = [False, True]
        if 'temp_order_id' in potential_purchases.columns:
            purch_sort_keys.append('temp_order_id')
            purch_asc.append(True)

        relevant_purchases = potential_purchases.sort_values(
            by=purch_sort_keys, 
            ascending=purch_asc
        )

        for p_idx, purchase in relevant_purchases.iterrows():
            if units_to_sell <= 0.001: break
            
            units_from_this_lot = min(units_to_sell, purchase['remaining_units'])
            cost_of_lot = (units_from_this_lot / purchase['units']) * purchase['amount'] if purchase['units'] != 0 else 0
            sale_value_of_lot = (units_from_this_lot / total_sale_units) * gross_sale_amt if total_sale_units != 0 else 0
            stt_of_lot = (units_from_this_lot / total_sale_units) * sale_stt_amt if total_sale_units != 0 else 0
            
            # Subtract STT in capital gain
            gain_loss = sale_value_of_lot - cost_of_lot - stt_of_lot
            holding_period = (sale['date'] - purchase['date']).days
            
            # Determine Long Term Tenure threshold (default 365 days / 1 yr, e.g. 730 days / 2 yrs for Silver/Gold/FoF)
            lt_threshold = sale.get('long_term_tenure')
            if pd.isna(lt_threshold) or not lt_threshold:
                scheme_str = str(sale.get('scheme', '')).upper()
                if re.search(r'GOLD|SILVER', scheme_str):
                    lt_threshold = 730
                else:
                    lt_threshold = 365
            else:
                try:
                    lt_threshold = int(lt_threshold)
                except (ValueError, TypeError):
                    lt_threshold = 365

            gain_type = 'LTCG' if holding_period >= lt_threshold else 'STCG'
            
            gains_summary.append({
                'sale_id': sale['orig_sale_index'], 'advisor': sale['advisor'], 'folio': sale['folio'], 
                'scheme': sale['scheme'], 'sale_date': sale['date'], 'units_sold': units_from_this_lot,
                'sale_nav': sale['nav'], 'sale_amount': sale_value_of_lot, 'cost_amount': cost_of_lot,
                'stt_amount': stt_of_lot, 'capital_gain': round(gain_loss, 2),
                'gain_type': gain_type, 'financial_year': sale['financial_year']
            })
            redeemed_portions.append({
                'purchase_index': p_idx, 'units_redeemed': units_from_this_lot, 'sale_date': sale['date']
            })
            
            purchases.loc[p_idx, 'remaining_units'] -= units_from_this_lot
            units_to_sell -= units_from_this_lot
            
    gains_df = pd.DataFrame(gains_summary) if gains_summary else pd.DataFrame()
    return gains_df, redeemed_portions, combined

def _create_report_data(combined_df, financial_year, cams_closing_df, cams_opening_df, merger_map=None, folder_map_df=None):
    """
    Prepares the data for both the main summary and reconciliation reports.
    Returns the main report data, its headers, and the reconciliation DataFrame.
    """
    if merger_map is None:
        merger_map = {}

    # Consolidate advisor per (normalized folio, scheme) group based on the latest transaction date/id
    if not combined_df.empty:
        combined_df = combined_df.copy()
        combined_df['_folio_norm'] = combined_df['folio'].apply(_normalize_folio)
        df_sorted = combined_df.sort_values(by=['date', 'id'])
        latest_advisors = {}
        for (folio_norm, scheme), group in df_sorted.groupby(['_folio_norm', 'scheme']):
            valid_advisors = group[group['advisor'].notna() & (group['advisor'].astype(str).str.strip() != '')]
            if not valid_advisors.empty:
                latest_advisor = valid_advisors['advisor'].iloc[-1]
            else:
                latest_advisor = group['advisor'].iloc[-1]
            latest_advisors[(folio_norm, scheme)] = latest_advisor
            
        for idx, row in combined_df.iterrows():
            key = (row['_folio_norm'], row['scheme'])
            if key in latest_advisors:
                combined_df.at[idx, 'advisor'] = latest_advisors[key]
        combined_df.drop(columns=['_folio_norm'], inplace=True)

    # Import here to avoid circular-import issues at module level
    from helpers.processor import add_fund_type_column as _add_fund_type
    import re

    try:
        fy_start_year = int(financial_year.split('-')[0])
        fy_start_date = pd.to_datetime(f"{fy_start_year}-04-01")
        fy_end_date = pd.to_datetime(f"{fy_start_year+1}-03-31")
    except (ValueError, IndexError):
        raise ValueError("Financial year must be in 'YYYY-YYYY' format.")

    if cams_closing_df is None:
        cams_closing_df = pd.DataFrame(columns=['Scheme Name', 'Folio', 'Advisor', 'Units', 'Cost Value', 'NAV', 'Fund Type', 'PAN', 'AMC', 'ISIN', 'AMFI Code', 'RTA', 'Current Value'])
    if cams_opening_df is None:
        cams_opening_df = pd.DataFrame(columns=['Scheme Name', 'Folio', 'Advisor', 'Units', 'Cost Value', 'NAV', 'Fund Type', 'PAN', 'AMC', 'ISIN', 'AMFI Code', 'RTA', 'Current Value'])

    gains_df, all_redeemed_purchases, cleaned_df = _calculate_all_time_gains_and_redemptions(combined_df, merger_map)
    reporting_df = _unify_folios_for_reporting(cleaned_df, merger_map)

    final_report_data = []
    reco_balances_list = []
    processed_funds = set()

    # --- Pre-process folder_map_df for robust fuzzy matching ---
    if folder_map_df is not None and not folder_map_df.empty:
        # Create normalized columns for matching
        folder_map_df = folder_map_df.copy()
        # Clean folio like: "123/45" -> "123"
        folder_map_df['_folio_norm'] = folder_map_df.get('Folio', '').astype(str).apply(lambda x: x.split('/')[0].strip())
        # Clean scheme with the new fuzzy logic
        folder_map_df['_scheme_norm'] = folder_map_df.get('Scheme', '').apply(_normalize_for_match)

    if not gains_df.empty:
        all_fund_gains_fy = gains_df[gains_df['financial_year'] == financial_year]
    else:
        all_fund_gains_fy = pd.DataFrame()
    
    merger_details = {}
    for (scheme, old_folio), details in merger_map.items():
        new_folio = details['new_folio']
        merger_details[(scheme, new_folio)] = {'old_folio': old_folio}
    
    purchase_types = ['PURCHASE', 'PURCHASE_SIP', 'SWITCH_IN', 'DIVIDEND_REINVEST', 'DIVIDEND_REINVESTMENT']

    # Safely handle transaction grouping
    sorted_groups = []
    if not reporting_df.empty and {'advisor', 'folio', 'scheme'}.issubset(reporting_df.columns):
        grouped = reporting_df.groupby(['advisor', 'folio', 'scheme'])
        sorted_groups = sorted(list(grouped), key=lambda x: x[0][2])

    for (advisor, folio, scheme), group_df in sorted_groups:
        fund_transactions = group_df.sort_values('date')
        folio_str = str(folio)
        
        # Look up CAMS closing balance for reconciliation columns
        cams_units_val = 0.0
        cams_cost_val = 0.0
        if cams_closing_df is not None and not cams_closing_df.empty:
            scheme_norm_lookup = _normalize_for_match(scheme)
            folio_lookup = _normalize_folio(folio_str)
            match_cams = cams_closing_df[
                (cams_closing_df['Folio'].apply(_normalize_folio) == folio_lookup) &
                (cams_closing_df['Scheme Name'].apply(_normalize_for_match) == scheme_norm_lookup)
            ]
            if not match_cams.empty:
                cams_units_val = pd.to_numeric(match_cams['Units'].iloc[0], errors='coerce') or 0.0
                cams_cost_val = pd.to_numeric(match_cams['Cost Value'].iloc[0], errors='coerce') or 0.0
        
        folder_num = 'N/A'
        if folder_map_df is not None and not folder_map_df.empty:
            folio_lookup = folio_str.split('/')[0].strip()
            scheme_lookup = _normalize_for_match(scheme)
            match = folder_map_df[
                (folder_map_df['_folio_norm'] == folio_lookup) &
                (folder_map_df['_scheme_norm'] == scheme_lookup)
            ]
            if not match.empty:
                folder_val = match['Folder'].iloc[0]
                folder_num = int(folder_val) if pd.notna(folder_val) and isinstance(folder_val, (int, float)) else str(folder_val)
        
        fund_name_str = f"{scheme}\nFOLIO NO: {folio_str} (Folder: {folder_num})\n{advisor}"

        opening_balance_lots = []
        all_purchases = fund_transactions[fund_transactions['type'].isin(purchase_types)]
        
        for idx, purchase in all_purchases.iterrows():
            if purchase['date'] < fy_start_date:
                redeemed_before_fy = sum(r['units_redeemed'] for r in all_redeemed_purchases if r['purchase_index'] == idx and r['sale_date'] < fy_start_date)
                redeemed_during_fy = sum(r['units_redeemed'] for r in all_redeemed_purchases if r['purchase_index'] == idx and fy_start_date <= r['sale_date'] <= fy_end_date)
                opening_units_for_lot = purchase['units'] - redeemed_before_fy
                
                original_units = purchase['units']
                original_amount = purchase['amount']
                
                if opening_units_for_lot > 0.001:
                    if redeemed_during_fy > 0.001:
                        prorated_redeemed_amt = (redeemed_during_fy / original_units) * original_amount if original_units > 0 else 0
                        opening_balance_lots.append({'Opening Balance Date': purchase['date'],'Opening Balance Unit': round(redeemed_during_fy, 3),'Opening Balance Rate': purchase['nav'],'Opening Balance Amt.': round(prorated_redeemed_amt, 2),'is_redeemed_opening': True})
                    
                    units_still_available = opening_units_for_lot - redeemed_during_fy
                    if units_still_available > 0.001:
                        prorated_available_amt = (units_still_available / original_units) * original_amount if original_units > 0 else 0
                        opening_balance_lots.append({'Opening Balance Date': purchase['date'],'Opening Balance Unit': round(units_still_available, 3),'Opening Balance Rate': purchase['nav'],'Opening Balance Amt.': round(prorated_available_amt, 2),'is_redeemed_opening': False})

        purchases_fy = fund_transactions[(fund_transactions['date'].between(fy_start_date, fy_end_date)) & (fund_transactions['type'].isin(purchase_types))]
        
        opening_units_total = sum(lot.get('Opening Balance Unit', 0) for lot in opening_balance_lots)
        opening_amt_total = sum(lot.get('Opening Balance Amt.', 0) for lot in opening_balance_lots)
        
        stt_tax_types = ['STAMP_DUTY_TAX', 'STT_TAX', 'STT_PAID']
        stt_transactions = fund_transactions[fund_transactions['type'].isin(stt_tax_types)]
        
        stt_opening = stt_transactions[stt_transactions['date'] < fy_start_date]['amount'].sum()
        stt_during_fy = stt_transactions[stt_transactions['date'].between(fy_start_date, fy_end_date)]['amount'].sum()
        stt_total = stt_opening + stt_during_fy
        
        merger_info = merger_details.get((scheme, folio))
        folios_for_gains = [folio]
        if merger_info:
            folios_for_gains.append(merger_info['old_folio'])

        fund_gains_fy = all_fund_gains_fy[
            (all_fund_gains_fy['advisor'] == advisor) & 
            (all_fund_gains_fy['scheme'] == scheme) & 
            (all_fund_gains_fy['folio'].isin(folios_for_gains))
        ] if not all_fund_gains_fy.empty else pd.DataFrame()
        
        has_transactions_fy = not purchases_fy.empty or not fund_gains_fy.empty
        if opening_units_total < 0.001 and opening_amt_total < 50 and not has_transactions_fy:
            continue
        
        processed_funds.add((_normalize_folio(folio), _normalize_for_match(scheme)))
        
        purchase_display_list = []
        for p_idx, purchase in purchases_fy.iterrows():
            redeemed_during_fy = sum(r['units_redeemed'] for r in all_redeemed_purchases if r['purchase_index'] == p_idx)
            
            original_units = purchase['units']
            original_amount = purchase['amount']

            if redeemed_during_fy > 0.001:
                prorated_redeemed_amt = (redeemed_during_fy / original_units) * original_amount if original_units > 0 else 0
                purchase_display_list.append({'Purchase Date': purchase['date'],'Purchase Unit': round(redeemed_during_fy, 3),'Purchase Rate': purchase['nav'],'Purchase Amt.': round(prorated_redeemed_amt, 2),'is_redeemed_purchase': True})
            
            units_still_available = original_units - redeemed_during_fy
            if units_still_available > 0.001:
                prorated_available_amt = (units_still_available / original_units) * original_amount if original_units > 0 else 0
                purchase_display_list.append({'Purchase Date': purchase['date'],'Purchase Unit': round(units_still_available, 3),'Purchase Rate': purchase['nav'],'Purchase Amt.': round(prorated_available_amt, 2),'is_redeemed_purchase': False})
        
        total_purchase_units_fy = purchases_fy['units'].sum()
        total_sale_units_fy = fund_gains_fy['units_sold'].sum() if not fund_gains_fy.empty else 0

        closing_units = opening_units_total + total_purchase_units_fy - total_sale_units_fy
        
        total_purchase_amt_fy = purchases_fy['amount'].sum()
        total_sale_amt_fy = abs(fund_gains_fy['sale_amount'].sum()) if not fund_gains_fy.empty else 0
        total_st_gain_fy = fund_gains_fy[fund_gains_fy['gain_type'] == 'STCG']['capital_gain'].sum() if not fund_gains_fy.empty else 0
        total_lt_gain_fy = fund_gains_fy[fund_gains_fy['gain_type'] == 'LTCG']['capital_gain'].sum() if not fund_gains_fy.empty else 0
        
        total_cost_of_sales_fy = fund_gains_fy['cost_amount'].sum() if ('cost_amount' in fund_gains_fy.columns and not fund_gains_fy.empty) else (total_sale_amt_fy - (total_st_gain_fy + total_lt_gain_fy))
        
        balance_amount = opening_amt_total + total_purchase_amt_fy - total_cost_of_sales_fy

        # Derive fund_type from scheme name (same logic as processor.add_fund_type_column)
        _ft_temp = pd.DataFrame({'Scheme Name': [scheme]})
        _ft_temp = _add_fund_type(_ft_temp)
        derived_fund_type = _ft_temp['Fund Type'].iloc[0] if not _ft_temp.empty else 'UNKNOWN'

        # Get latest NAV from transactions
        latest_nav = fund_transactions['nav'].iloc[-1] if not fund_transactions.empty else 0

        reco_balances_list.append({
            'folio': folio, 'scheme': scheme, 'folder': folder_num,
            'advisor': advisor,          # carry advisor from transaction group
            'fund_type': derived_fund_type,  # carry fund_type derived from scheme name
            'nav': latest_nav,
            'Calculated Opening Units': opening_units_total,
            'Calculated Opening Amount': opening_amt_total,
            'Calculated Units (Report)': closing_units, 
            'Calculated Amount (Report)': balance_amount,
            'STT Opening': stt_opening,
            'STT Total': stt_total,
            'Short Term Gain': round(total_st_gain_fy, 2),
            'Long Term Gain': round(total_lt_gain_fy, 2)
        })
        
        rows_for_this_fund = []
        sale_display_list = []
        if not fund_gains_fy.empty:
            fund_gains_fy_sorted = fund_gains_fy.sort_values(by='sale_date')
            grouped_sales = fund_gains_fy_sorted.groupby('sale_id')

            for sale_id, sale_group in grouped_sales:
                sale_date = sale_group['sale_date'].iloc[0]
                original_sale_transaction = cleaned_df[cleaned_df.index == sale_id].iloc[0]
                sale_nav = original_sale_transaction['nav']

                st_group = sale_group[sale_group['gain_type'] == 'STCG']
                if not st_group.empty:
                    st_units_sold = st_group['units_sold'].sum()
                    st_sale_amount = st_group['sale_amount'].sum()
                    st_gain = st_group['capital_gain'].sum()
                    
                    st_info = {
                        'Sale Date': sale_date, 'Sale Unit': round(st_units_sold, 3),
                        'Sale Rate': round(sale_nav, 3), 'Sale Amt.': round(st_sale_amount, 2),
                        'Short Term': round(st_gain, 2), 'Long Term': 0
                    }
                    sale_display_list.append(st_info)

                lt_group = sale_group[sale_group['gain_type'] == 'LTCG']
                if not lt_group.empty:
                    lt_units_sold = lt_group['units_sold'].sum()
                    lt_sale_amount = lt_group['sale_amount'].sum()
                    lt_gain = lt_group['capital_gain'].sum()

                    lt_info = {
                        'Sale Date': sale_date, 'Sale Unit': round(lt_units_sold, 3),
                        'Sale Rate': round(sale_nav, 3), 'Sale Amt.': round(lt_sale_amount, 2),
                        'Short Term': 0, 'Long Term': round(lt_gain, 2)
                    }
                    sale_display_list.append(lt_info)

        closing_balance_lots = []
        for lot in opening_balance_lots:
            if not lot.get('is_redeemed_opening', False):
                closing_balance_lots.append({
                    'Closing Balance Date': lot.get('Opening Balance Date'),
                    'Closing Balance Unit': lot.get('Opening Balance Unit'),
                    'Closing Balance Rate': lot.get('Opening Balance Rate'),
                    'Closing Balance Amt.': lot.get('Opening Balance Amt.')
                })
        
        for lot in purchase_display_list:
            if not lot.get('is_redeemed_purchase', False):
                closing_balance_lots.append({
                    'Closing Balance Date': lot.get('Purchase Date'),
                    'Closing Balance Unit': lot.get('Purchase Unit'),
                    'Closing Balance Rate': lot.get('Purchase Rate'),
                    'Closing Balance Amt.': lot.get('Purchase Amt.')
                })
        
        num_transaction_rows = max(len(opening_balance_lots), len(purchase_display_list), len(sale_display_list), len(closing_balance_lots))
        
        for i in range(num_transaction_rows):
            row_data = {}
            if i == 0:
                row_data['Fund Name'] = fund_name_str
                
            if i < len(opening_balance_lots):
                row_data.update({k: v for k, v in opening_balance_lots[i].items() if k.startswith('Opening Balance')})
            if i < len(purchase_display_list):
                row_data.update({k: v for k, v in purchase_display_list[i].items() if k.startswith('Purchase')})
            if i < len(sale_display_list):
                row_data.update({k: v for k, v in sale_display_list[i].items() if k.startswith('Sale') or k in ['Short Term', 'Long Term']})
            if i < len(closing_balance_lots):
                row_data.update({k: v for k, v in closing_balance_lots[i].items() if k.startswith('Closing Balance')})
                    
            rows_for_this_fund.append(row_data)

        while len(rows_for_this_fund) < 6:
            if not rows_for_this_fund:
                rows_for_this_fund.append({'Fund Name': fund_name_str})
            else:
                rows_for_this_fund.append({})
        
        rows_for_this_fund.append({'Fund Name': 'TOTAL', 'is_total': True,'Opening Balance Unit': round(opening_units_total, 3),'Opening Balance Amt.': round(opening_amt_total, 2),'Purchase Unit': round(total_purchase_units_fy, 3),'Purchase Amt.': round(total_purchase_amt_fy, 2),'Sale Unit': round(total_sale_units_fy, 3),'Sale Amt.': round(total_sale_amt_fy, 2),'Closing Balance Unit': round(closing_units, 3) if abs(closing_units) > 0.001 else 0, 'Closing Balance Amt.': round(balance_amount, 2),'Short Term': round(total_st_gain_fy, 2),'Long Term': round(total_lt_gain_fy, 2), 'Closing Units (CAMS)': round(cams_units_val, 3), 'Closing Unit Difference': round(closing_units - cams_units_val, 3), 'Closing Amount (CAMS)': round(cams_cost_val, 2), 'Closing Amount Difference': round(balance_amount - cams_cost_val, 2)})
        rows_for_this_fund.append({'Fund Name': 'NET BALANCE', 'is_net_balance': True,'Closing Balance Unit': round(closing_units, 3) if abs(closing_units) > 0.001 else 0,'Closing Balance Amt.': round(balance_amount, 2),'Balance Amount': round(balance_amount, 2), 'Closing Units (CAMS)': round(cams_units_val, 3), 'Closing Unit Difference': round(closing_units - cams_units_val, 3), 'Closing Amount (CAMS)': round(cams_cost_val, 2), 'Closing Amount Difference': round(balance_amount - cams_cost_val, 2)})
        rows_for_this_fund.append({})
        final_report_data.extend(rows_for_this_fund)

    cams_data_renamed = cams_closing_df.rename(columns={'Scheme Name': 'scheme', 'Folio': 'folio', 'Advisor': 'advisor', 'Units': 'units', 'Cost Value': 'value', 'NAV': 'nav'})
    for _, row in cams_data_renamed.iterrows():
        if (_normalize_folio(row['folio']), _normalize_for_match(row['scheme'])) not in processed_funds:
            closing_units = pd.to_numeric(row['units'], errors='coerce') or 0
            balance_amount = pd.to_numeric(row['value'], errors='coerce') or 0
            fund_nav = pd.to_numeric(row.get('nav'), errors='coerce') or 0

            if closing_units < 0.01 and balance_amount < 50:
                continue
            
            if closing_units > 0.001:
                folder_num = 'N/A'
                if folder_map_df is not None and not folder_map_df.empty:
                    folio_lookup = str(row['folio']).split('/')[0].strip()
                    scheme_lookup = _normalize_for_match(row['scheme'])
                    match = folder_map_df[
                        (folder_map_df['_folio_norm'] == folio_lookup) &
                        (folder_map_df['_scheme_norm'] == scheme_lookup)
                    ]
                    if not match.empty:
                        folder_val = match['Folder'].iloc[0]
                        folder_num = int(folder_val) if pd.notna(folder_val) and isinstance(folder_val, (int, float)) else str(folder_val)

                fund_name_str = f"{row['scheme']}\nFOLIO NO: {row['folio']} (Folder: {folder_num})\n{row['advisor']}"
                
                fund_block = []
                fund_block.append({
                    'Fund Name': fund_name_str, 
                    'Opening Balance Unit': round(closing_units, 3), 
                    'Opening Balance Rate': fund_nav,
                    'Opening Balance Amt.': round(balance_amount, 2), 
                    'Closing Balance Unit': round(closing_units, 3), 
                    'Closing Balance Rate': fund_nav,
                    'Closing Balance Amt.': round(balance_amount, 2)
                })
                while len(fund_block) < 6:
                    fund_block.append({})
                
                fund_block.append({'Fund Name': 'TOTAL', 'is_total': True, 'Opening Balance Unit': round(closing_units, 3), 'Opening Balance Amt.': round(balance_amount, 2), 'Closing Balance Unit': round(closing_units, 3), 'Closing Balance Amt.': round(balance_amount, 2), 'Closing Units (CAMS)': round(closing_units, 3), 'Closing Unit Difference': 0.0, 'Closing Amount (CAMS)': round(balance_amount, 2), 'Closing Amount Difference': 0.0})
                fund_block.append({'Fund Name': 'NET BALANCE', 'is_net_balance': True, 'Closing Balance Unit': round(closing_units, 3), 'Closing Balance Amt.': round(balance_amount, 2), 'Balance Amount': round(balance_amount, 2), 'Closing Units (CAMS)': round(closing_units, 3), 'Closing Unit Difference': 0.0, 'Closing Amount (CAMS)': round(balance_amount, 2), 'Closing Amount Difference': 0.0})
                fund_block.append({})
                final_report_data.extend(fund_block)
                reco_balances_list.append({
                    'folio': row['folio'], 'scheme': row['scheme'], 'folder': folder_num,
                    'advisor': row.get('advisor', ''),
                    'fund_type': row.get('fund_type', ''),
                    'nav': fund_nav,
                    'Calculated Opening Units': closing_units, 'Calculated Opening Amount': balance_amount,
                    'Calculated Units (Report)': closing_units, 'Calculated Amount (Report)': balance_amount,
                    'STT Opening': 0, 'STT Total': 0,
                    'Short Term Gain': 0,
                    'Long Term Gain': 0
                })

    column_headers = ['Sr. No.', 'Fund Name', 'Opening Balance Date', 'Opening Balance Unit', 'Opening Balance Rate', 'Opening Balance Amt.', 'Purchase Date', 'Purchase Unit', 'Purchase Rate', 'Purchase Amt.', 'Sale Date', 'Sale Unit', 'Sale Rate', 'Sale Amt.', 'Closing Balance Date', 'Closing Balance Unit', 'Closing Balance Rate', 'Closing Balance Amt.', 'Short Term', 'Long Term', 'Balance Amount', 'Closing Units (CAMS)', 'Closing Unit Difference', 'Closing Amount (CAMS)', 'Closing Amount Difference']
    
    report_balances_df = pd.DataFrame(reco_balances_list)
    if not report_balances_df.empty:
        report_balances_df['scheme_match_key'] = report_balances_df['scheme'].apply(_normalize_for_match)
        report_balances_df['folio'] = report_balances_df['folio'].apply(_normalize_folio)

    cams_closing_df_renamed = cams_closing_df.rename(columns={
        'Folio': 'folio', 'Scheme Name': 'scheme', 'Advisor': 'advisor',
        'Fund Type': 'fund_type', 'NAV': 'nav', 'Units': 'Closing Units (CAMS)',
        'Cost Value': 'Closing Amount (CAMS)'
    })
    # Ensure required join keys exist
    for k in ['folio', 'scheme']:
        if k not in cams_closing_df_renamed.columns:
            cams_closing_df_renamed[k] = ''
    
    cams_closing_df_renamed['scheme_match_key'] = cams_closing_df_renamed['scheme'].apply(_normalize_for_match)
    cams_closing_df_renamed['folio'] = cams_closing_df_renamed['folio'].apply(_normalize_folio)
    cams_closing_df_renamed['Closing Units (CAMS)'] = pd.to_numeric(cams_closing_df_renamed['Closing Units (CAMS)'], errors='coerce').fillna(0)
    cams_closing_df_renamed['Closing Amount (CAMS)'] = pd.to_numeric(cams_closing_df_renamed['Closing Amount (CAMS)'], errors='coerce').fillna(0)

    if cams_opening_df is not None and not cams_opening_df.empty:
        cams_opening_df_renamed = cams_opening_df.rename(columns={
            'Folio': 'folio', 'Scheme Name': 'scheme', 'Units': 'Opening Units (CAMS)',
            'Cost Value': 'Opening Amount (CAMS)'
        })
        for k in ['folio', 'scheme']:
            if k not in cams_opening_df_renamed.columns:
                cams_opening_df_renamed[k] = ''
        
        cams_opening_df_renamed['scheme_match_key'] = cams_opening_df_renamed['scheme'].apply(_normalize_for_match)
        cams_opening_df_renamed['folio'] = cams_opening_df_renamed['folio'].apply(_normalize_folio)
        cams_opening_df_renamed = cams_opening_df_renamed[['folio', 'scheme_match_key', 'scheme', 'Opening Units (CAMS)', 'Opening Amount (CAMS)']]
        cams_opening_df_renamed['Opening Units (CAMS)'] = pd.to_numeric(cams_opening_df_renamed['Opening Units (CAMS)'], errors='coerce').fillna(0)
        cams_opening_df_renamed['Opening Amount (CAMS)'] = pd.to_numeric(cams_opening_df_renamed['Opening Amount (CAMS)'], errors='coerce').fillna(0)
        
        # Merge cams closing and cams opening on folio and normalized match key
        reco_base = pd.merge(cams_closing_df_renamed, cams_opening_df_renamed, on=['folio', 'scheme_match_key'], how='outer', suffixes=('', '_open'))
    else:
        reco_base = cams_closing_df_renamed.copy()
        reco_base['Opening Units (CAMS)'] = 0
        reco_base['Opening Amount (CAMS)'] = 0

    # Merge reco_base (CAMS) with report_balances (Calculated)
    reco_data = pd.merge(reco_base, report_balances_df, on=['folio', 'scheme_match_key'], how='outer', suffixes=('', '_calc'))

    # If nav is missing from CAMS, use the one from Calculated report (latest transaction or opening)
    if 'nav' in reco_data.columns and 'nav_calc' in reco_data.columns:
        reco_data['nav'] = reco_data['nav'].fillna(reco_data['nav_calc'])
        reco_data.drop(columns=['nav_calc'], inplace=True)
    elif 'nav_calc' in reco_data.columns:
        reco_data.rename(columns={'nav_calc': 'nav'}, inplace=True)

    # Resolve human-readable 'scheme' name: prefer CAMS closing, then CAMS opening, then calculated report
    if 'scheme' in reco_data.columns:
        if 'scheme_open' in reco_data.columns:
            reco_data['scheme'] = reco_data['scheme'].fillna(reco_data['scheme_open'])
        if 'scheme_calc' in reco_data.columns:
            reco_data['scheme'] = reco_data['scheme'].fillna(reco_data['scheme_calc'])
    
    # Drop temporary scheme columns
    reco_data.drop(columns=['scheme_open', 'scheme_calc'], inplace=True, errors='ignore')

    # -----------------------------------------------------------------------
    # Resolve duplicate advisor/fund_type columns that arise from the outer
    # merge when both reco_base and report_balances_df contain those columns.
    # Strategy: prefer the value from reco_base (CAMS closing); fall back to
    # the value from report_balances_df (transaction-derived).
    # -----------------------------------------------------------------------
    for col in ['advisor', 'fund_type']:
        col_x = f'{col}_x'   # from reco_base (CAMS closing)
        col_y = f'{col}_y'   # from report_balances_df (transaction-derived)
        if col_x in reco_data.columns and col_y in reco_data.columns:
            # Use CAMS value where available; otherwise use transaction-derived value
            reco_data[col] = reco_data[col_x].where(
                reco_data[col_x].notna() & (reco_data[col_x].astype(str).str.strip() != ''),
                reco_data[col_y]
            )
            reco_data.drop(columns=[col_x, col_y], inplace=True)
        elif col_x in reco_data.columns:
            reco_data.rename(columns={col_x: col}, inplace=True)
        elif col_y in reco_data.columns:
            reco_data.rename(columns={col_y: col}, inplace=True)

    # -----------------------------------------------------------------------
    # Backfill advisor & fund_type from opening CAMS data for rows where
    # closing-balance data was absent (i.e. funds fully redeemed in the FY).
    # -----------------------------------------------------------------------
    if cams_opening_df is not None and not cams_opening_df.empty:
        open_lookup = cams_opening_df.rename(columns={
            'Folio': 'folio', 'Scheme Name': 'scheme',
            'Advisor': '_open_advisor', 'Fund Type': '_open_fund_type'
        })
        open_lookup['folio'] = open_lookup['folio'].astype(str).str.strip()
        open_lookup['scheme'] = open_lookup['scheme'].astype(str).str.strip()
        open_lookup['scheme_match_key'] = open_lookup['scheme'].apply(_normalize_for_match)
        open_lookup = open_lookup[['folio', 'scheme_match_key', '_open_advisor', '_open_fund_type']].drop_duplicates(subset=['folio', 'scheme_match_key'])

        reco_data = pd.merge(reco_data, open_lookup, on=['folio', 'scheme_match_key'], how='left')

        for col, fill_col in [('advisor', '_open_advisor'), ('fund_type', '_open_fund_type')]:
            if fill_col in reco_data.columns:
                if col not in reco_data.columns:
                    reco_data[col] = ''
                mask_empty = reco_data[col].isna() | (reco_data[col].astype(str).str.strip() == '')
                reco_data.loc[mask_empty, col] = reco_data.loc[mask_empty, fill_col]
                reco_data.drop(columns=[fill_col], inplace=True)

    # Also derive fund_type from the scheme name as a last-resort fallback
    if 'fund_type' in reco_data.columns:
        still_missing = reco_data['fund_type'].isna() | (reco_data['fund_type'].astype(str).str.strip() == '')
        if still_missing.any():
            _ft_temp2 = pd.DataFrame({'Scheme Name': reco_data.loc[still_missing, 'scheme'].values})
            _ft_temp2 = _add_fund_type(_ft_temp2)
            reco_data.loc[still_missing, 'fund_type'] = _ft_temp2['Fund Type'].values

    # Avoid forcing textual columns to 0. Only fill numeric columns with 0 where appropriate.
    numeric_cols = [
        'Opening Units (CAMS)', 'Opening Amount (CAMS)', 'Closing Units (CAMS)', 'Closing Amount (CAMS)',
        'Calculated Opening Units', 'Calculated Opening Amount', 'Calculated Units (Report)', 'Calculated Amount (Report)',
        'STT Opening', 'STT Total', 'Short Term Gain', 'Long Term Gain'
    ]
    for col in numeric_cols:
        if col in reco_data.columns:
            reco_data[col] = pd.to_numeric(reco_data[col], errors='coerce').fillna(0)

    # For textual columns like advisor, fund_type and nav (nav is numeric but should be blank if unknown),
    # do not replace missing values with 0. Keep them as empty strings for clarity when missing.
    text_cols = ['advisor', 'fund_type']
    for col in text_cols:
        if col in reco_data.columns:
            reco_data[col] = reco_data[col].where(reco_data[col].notna(), '')

    if 'nav' in reco_data.columns:
        # Keep nav blank when missing instead of 0; convert to numeric where present.
        reco_data['nav'] = pd.to_numeric(reco_data['nav'], errors='coerce')

    # -----------------------------------------------------------------------
    # GROUP BY Folio + Scheme + Advisor + Fund Type
    # Collapses duplicate rows that arise from slight scheme-name differences
    # across CAMS closing vs opening data (e.g. "AXIS ARBITRAGE - REGULAR
    # GROWTH" vs "AXIS ARBITRAGE FUND - REGULAR GROWTH").
    # Numeric columns are summed; text / nav are taken from the first valid row.
    # -----------------------------------------------------------------------
    group_keys = ['folio', 'scheme', 'advisor', 'fund_type']
    # Only group if all keys are present
    available_keys = [k for k in group_keys if k in reco_data.columns]
    if available_keys:
        numeric_agg_cols = [c for c in numeric_cols if c in reco_data.columns]
        text_agg_cols = [c for c in reco_data.columns if c not in available_keys + numeric_agg_cols]

        agg_dict = {c: 'sum' for c in numeric_agg_cols}
        for c in text_agg_cols:
            agg_dict[c] = 'first'

        reco_data = (
            reco_data
            .groupby(available_keys, as_index=False, dropna=False)
            .agg(agg_dict)
        )

    # Drop the match key before returning, as it's no longer needed
    if 'scheme_match_key' in reco_data.columns:
        reco_data.drop(columns=['scheme_match_key'], inplace=True)

    reco_data['Opening Unit Difference'] = reco_data.get('Opening Units (CAMS)', 0) - reco_data.get('Calculated Opening Units', 0)
    reco_data['Opening Amount Difference'] = reco_data.get('Opening Amount (CAMS)', 0) - reco_data.get('Calculated Opening Amount', 0)
    reco_data['Closing Unit Difference'] = reco_data.get('Closing Units (CAMS)', 0) - reco_data.get('Calculated Units (Report)', 0)
    reco_data['Closing Amount Difference'] = reco_data.get('Closing Amount (CAMS)', 0) - reco_data.get('Calculated Amount (Report)', 0)

    return final_report_data, column_headers, reco_data

def _write_report_to_sheet(ws, report_data, column_headers, financial_year, pan, holder_name):
    """
    Writes the fully formatted capital gains summary to a given worksheet.
    """
    if not report_data:
        ws['A1'] = f"No data to generate a report for {holder_name} (PAN: {pan})."
        return

    fy_start_year_short = financial_year.split('-')[0]
    fy_end_year_short = financial_year.split('-')[1]
    fy_start_date_str = f"01-04-{fy_start_year_short}"
    fy_end_date_str = f"31-03-{fy_end_year_short}"
    
    ws['A1'] = f"{holder_name} (PAN: {pan})"
    ws.merge_cells('A1:Y1')
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(bold=True, size=14)

    ws['A2'] = f"Detail of Amount Invested in Mutual Funds from {fy_start_date_str} to {fy_end_date_str}"
    ws.merge_cells('A2:Y2')
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(bold=True, size=12)

    ws.append([])
    header_row_1 = ["Sr. No.", "Fund Name", "Opening Balance", None, None, None, "Purchase During the Year", None, None, None, "Sale During the Year", None, None, None, "Closing Balance", None, None, None, "Capital Gain/(Loss)", None, "Balance Amount", "Closing Units (CAMS)", "Units Difference", "Closing Amount (CAMS)", "Closing Amount Difference"]
    ws.append(header_row_1)
    header_row_2 = [None, None, "Date", "Unit", "Rate", "Amt.", "Date", "Unit", "Rate", "Amt.", "Date", "Unit", "Rate", "Amt.", "Date", "Unit", "Rate", "Amt.", "Short Term", "Long Term", None, None, None, None, None]
    ws.append(header_row_2)
    
    start_row_num = ws.max_row - 1
    ws.merge_cells(start_row=start_row_num, start_column=1, end_row=start_row_num + 1, end_column=1)
    ws.merge_cells(start_row=start_row_num, start_column=2, end_row=start_row_num + 1, end_column=2)
    ws.merge_cells(start_row=start_row_num, start_column=3, end_row=start_row_num, end_column=6)
    ws.merge_cells(start_row=start_row_num, start_column=7, end_row=start_row_num, end_column=10)
    ws.merge_cells(start_row=start_row_num, start_column=11, end_row=start_row_num, end_column=14)
    ws.merge_cells(start_row=start_row_num, start_column=15, end_row=start_row_num, end_column=18)
    ws.merge_cells(start_row=start_row_num, start_column=19, end_row=start_row_num, end_column=20)
    ws.merge_cells(start_row=start_row_num, start_column=21, end_row=start_row_num + 1, end_column=21)
    ws.merge_cells(start_row=start_row_num, start_column=22, end_row=start_row_num + 1, end_column=22)
    ws.merge_cells(start_row=start_row_num, start_column=23, end_row=start_row_num + 1, end_column=23)
    ws.merge_cells(start_row=start_row_num, start_column=24, end_row=start_row_num + 1, end_column=24)
    ws.merge_cells(start_row=start_row_num, start_column=25, end_row=start_row_num + 1, end_column=25)
    
    header_font = Font(bold=True)
    center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    date_format = 'DD-MM-YY'

    red_fill = PatternFill(start_color="FFDDE1", end_color="FFDDE1", fill_type="solid")
    blue_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")

    today = date.today()
    start_date_for_blue = None
    end_date_for_blue = today

    if today.day <= 15:
        end_of_last_month = today.replace(day=1) - timedelta(days=1)
        start_date_for_blue = end_of_last_month.replace(day=1)
        end_date_for_blue = end_of_last_month
    else:
        first_of_this_month = today.replace(day=1)
        end_of_last_month = first_of_this_month - timedelta(days=1)
        first_of_last_month = end_of_last_month.replace(day=1)
        start_date_for_blue = first_of_last_month

    for row in ws.iter_rows(min_row=start_row_num, max_row=ws.max_row):
        for cell in row:
            if cell.value:
                cell.font = header_font
                cell.alignment = center_align
            cell.border = thin_border
            
    serial_number = 0
    for row_dict in report_data:
        if '\n' in row_dict.get('Fund Name', ''):
            is_first_row_of_fund = True
            for key in row_dict:
                if key not in ['Fund Name', 'Sr. No.'] and row_dict[key] != '':
                    is_first_row_of_fund = True
                    break
            if is_first_row_of_fund:
                serial_number += 1
                row_dict['Sr. No.'] = serial_number

        row_to_append = [row_dict.get(col, '') for col in column_headers]
        ws.append(row_to_append)
        current_row_idx = ws.max_row
        
        date_col_indices = [3, 7, 11, 15]
        for col_idx in date_col_indices:
            cell = ws.cell(row=current_row_idx, column=col_idx)
            if isinstance(cell.value, (datetime, pd.Timestamp)):
                cell.number_format = date_format

        if start_date_for_blue:
            ob_date = row_dict.get('Opening Balance Date')
            if isinstance(ob_date, (datetime, pd.Timestamp)) and start_date_for_blue <= ob_date.date() <= end_date_for_blue:
                for col_idx in range(3, 7): ws.cell(row=current_row_idx, column=col_idx).fill = blue_fill
            pur_date = row_dict.get('Purchase Date')
            if isinstance(pur_date, (datetime, pd.Timestamp)) and start_date_for_blue <= pur_date.date() <= end_date_for_blue:
                for col_idx in range(7, 11): ws.cell(row=current_row_idx, column=col_idx).fill = blue_fill
            sale_date = row_dict.get('Sale Date')
            if isinstance(sale_date, (datetime, pd.Timestamp)) and start_date_for_blue <= sale_date.date() <= end_date_for_blue:
                for col_idx in range(11, 15): ws.cell(row=current_row_idx, column=col_idx).fill = blue_fill
            closing_date = row_dict.get('Closing Balance Date')
            if isinstance(closing_date, (datetime, pd.Timestamp)) and start_date_for_blue <= closing_date.date() <= end_date_for_blue:
                for col_idx in range(15, 19): ws.cell(row=current_row_idx, column=col_idx).fill = blue_fill

        if row_dict.get('is_redeemed_opening', False):
            for col_idx in range(3, 7): ws.cell(row=current_row_idx, column=col_idx).fill = red_fill
        if row_dict.get('is_redeemed_purchase', False):
            for col_idx in range(7, 11): ws.cell(row=current_row_idx, column=col_idx).fill = red_fill
        
        if row_dict.get('is_total', False) or row_dict.get('is_net_balance', False):
            for cell in ws[current_row_idx]:
                cell.font = Font(bold=True)
                cell.fill = yellow_fill
            # If there's a discrepancy, override yellow with red fill for warnings
            try:
                udiff = float(row_dict.get('Closing Unit Difference') or 0.0)
                adiff = float(row_dict.get('Closing Amount Difference') or 0.0)
                if abs(udiff) > 0.001:
                    ws.cell(row=current_row_idx, column=23).fill = red_fill
                if abs(adiff) > 1.0:
                    ws.cell(row=current_row_idx, column=25).fill = red_fill
            except (ValueError, TypeError):
                pass

    data_start_row = start_row_num + 2
    for row_idx in range(data_start_row, ws.max_row + 1):
        for col_idx in range(1, len(column_headers) + 1):
            ws.cell(row=row_idx, column=col_idx).border = thin_border

    current_row_on_sheet = data_start_row
    i = 0
    while i < len(report_data):
        row_dict = report_data[i]
        if row_dict.get('Sr. No.'):
            block_start_row = current_row_on_sheet
            
            block_data_row_count = 0
            for j in range(i, len(report_data)):
                if report_data[j].get('is_total'):
                    break
                block_data_row_count += 1
            
            rows_to_merge = min(block_data_row_count, 6)
            block_end_row = block_start_row + rows_to_merge - 1
            
            if block_end_row >= block_start_row:
                ws.merge_cells(start_row=block_start_row, start_column=1, end_row=block_end_row, end_column=1)
                ws.cell(row=block_start_row, column=1).alignment = Alignment(horizontal='center', vertical='top')
                ws.merge_cells(start_row=block_start_row, start_column=2, end_row=block_end_row, end_column=2)
                fund_name_cell = ws.cell(row=block_start_row, column=2)
                fund_name_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                fund_name_cell.font = Font(bold=True)
            
            block_size_in_report_data = block_data_row_count + 3
            i += block_size_in_report_data
            current_row_on_sheet += block_size_in_report_data
        else:
            i += 1
            current_row_on_sheet += 1

    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 30
    for col_letter in ['C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y']:
        ws.column_dimensions[col_letter].width = 16

def _write_reconciliation_sheet(ws, reco_data, financial_year, pan, holder_name):
    """
    Writes the final reconciliation data to a worksheet.
    """
    ws.title = f"{holder_name} Reco"
    
    ws['A1'] = f"{holder_name} (PAN: {pan})"
    ws.merge_cells('A1:T1')
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(bold=True, size=14)

    ws['A2'] = f"Reconciliation Sheet as on 31-03-{financial_year.split('-')[1]}"
    ws.merge_cells('A2:T2')
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(bold=True, size=12)

    headers = [
        'Folio', 'Scheme Name', 'Folder', 'Advisor', 'Fund Type', 'NAV', 
        'Calculated Opening Units', 'Opening Units (CAMS)', 'Opening Unit Difference',
        'Calculated Opening Amount', 'STT Opening', 'Opening Amount (CAMS)', 'Opening Amount Difference',
        'Calculated Units (Report)', 'Closing Units (CAMS)', 'Closing Unit Difference',
        'Calculated Amount (Report)', 'STT Total', 'Short Term Gain', 'Long Term Gain', 'Closing Amount (CAMS)', 'Closing Amount Difference'
    ]
    ws.append([])
    ws.append(headers)
    
    for _, row in reco_data.iterrows():
        # Keep textual columns blank/N/A if missing; numeric columns default to 0
        advisor_val = row.get('advisor', '') if pd.notna(row.get('advisor', '')) else ''
        fund_type_val = row.get('fund_type', '') if pd.notna(row.get('fund_type', '')) else ''
        folder_val = row.get('folder', 'N/A') if pd.notna(row.get('folder', '')) else 'N/A'
        
        nav_val = row.get('nav', '')
        if pd.isna(nav_val):
            nav_val = ''

        ws.append([
            row.get('folio', ''), row.get('scheme', ''), folder_val, advisor_val,
            fund_type_val, nav_val,
            row.get('Calculated Opening Units', 0), row.get('Opening Units (CAMS)', 0), row.get('Opening Unit Difference', 0),
            row.get('Calculated Opening Amount', 0), row.get('STT Opening', 0), row.get('Opening Amount (CAMS)', 0), row.get('Opening Amount Difference', 0),
            row.get('Calculated Units (Report)', 0), row.get('Closing Units (CAMS)', 0), row.get('Closing Unit Difference', 0),
            row.get('Calculated Amount (Report)', 0), row.get('STT Total', 0), row.get('Short Term Gain', 0), row.get('Long Term Gain', 0), row.get('Closing Amount (CAMS)', 0), row.get('Closing Amount Difference', 0)
        ])

    # --- Add Total Row ---
    total_row = ['TOTAL', '', '', '', '', '']
    numeric_cols_to_sum = [
        'Calculated Opening Units', 'Opening Units (CAMS)', 'Opening Unit Difference',
        'Calculated Opening Amount', 'STT Opening', 'Opening Amount (CAMS)', 'Opening Amount Difference',
        'Calculated Units (Report)', 'Closing Units (CAMS)', 'Closing Unit Difference',
        'Calculated Amount (Report)', 'STT Total', 'Short Term Gain', 'Long Term Gain', 'Closing Amount (CAMS)', 'Closing Amount Difference'
    ]
    
    for col_name in numeric_cols_to_sum:
        total_row.append(reco_data[col_name].sum() if col_name in reco_data.columns else 0)
    
    ws.append(total_row)
    total_row_num = ws.max_row
    yellow_fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
    for cell in ws[total_row_num]:
        cell.font = Font(bold=True)
        cell.fill = yellow_fill

    header_font = Font(bold=True)
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    header_row_num = 4
    
    for cell in ws[header_row_num]:
        cell.font = header_font
        cell.border = thin_border
        
    for row_idx in range(header_row_num + 1, ws.max_row + 1):
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            # NAV (col 6) -> 4 decimals
            if col_idx == 6:
                cell.number_format = '#,##0.0000'
            # Units columns -> 3 decimals: Calculated Opening Units (7), Opening Units (CAMS) (8), Calculated Units (Report) (14), Closing Units (CAMS) (15)
            elif col_idx in [7, 8, 14, 15]:
                cell.number_format = '#,##0.000'
            # Amount and gain columns -> 2 decimals: Calculated Opening Amount (10), Opening Amount (CAMS) (12), Calculated Amount (Report) (17), STT/ Gains / Closing Amount -> 11,18-21
            elif col_idx in [10, 12, 17, 18, 19, 20, 21]:
                cell.number_format = '#,##0.00'

    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 40
    for col_idx in range(3, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 25

def _unify_folios_for_reporting(combined_df, merger_map):
    """
    Unifies folio numbers in a DataFrame purely for reporting purposes.
    """
    if not merger_map or combined_df.empty or 'folio' not in combined_df.columns:
        return combined_df

    print("\n--- Unifying Folios for Final Report ---")
    df_copy = combined_df.copy()

    for (scheme, old_folio), details in merger_map.items():
        new_folio = details['new_folio']
        filter_condition = (df_copy['scheme'] == scheme) & (df_copy['folio'] == old_folio)
        if filter_condition.any():
            print(f"  - Consolidating transactions from '{old_folio}' into '{new_folio}' for '{scheme}'.")
            df_copy.loc[filter_condition, 'folio'] = new_folio
            
    print("--- Folio Unification Complete ---\n")
    return df_copy

def generate_multi_account_report(accounts_data, financial_year, folder_file_path, filename="Capital_Gains_All_Accounts.xlsx"):
    """
    Generates a single Excel workbook with a separate sheet for each account.
    """
    wb = Workbook()
    wb.remove(wb.active)

    folder_map_df = _load_folder_data(folder_file_path)

    for account in tqdm(accounts_data, desc="Generating Reports"):
        holder_name = account['name']
        pan = account['pan']
        combined_df = account['df']
        cams_closing_df = account['cams_df']
        cams_opening_df = account.get('cams_opening_df')
        merger_map = account.get('merger_map', {})

        if combined_df.empty and (cams_closing_df is None or cams_closing_df.empty):
            print(f"Skipping report for {holder_name} as no transaction or holdings data was provided.")
            ws_report = wb.create_sheet(title=holder_name)
            _write_report_to_sheet(ws_report, [], [], financial_year, pan, holder_name)
            continue
        
        if combined_df.empty:
            print(f"No transaction data for {holder_name}, generating report from holdings only.")
        else:
            combined_df['date'] = pd.to_datetime(combined_df['date'])
            combined_df['financial_year'] = combined_df['date'].apply(
                lambda d: f"{d.year if d.month >= 4 else d.year - 1}-{d.year + 1 if d.month >= 4 else d.year}"
            )

        print(f"Processing report for {holder_name}...")

        report_data, report_headers, reco_df = _create_report_data(
            combined_df, financial_year, cams_closing_df, cams_opening_df, merger_map, folder_map_df
        )

        ws_report = wb.create_sheet(title=holder_name[:31])
        _write_report_to_sheet(ws_report, report_data, report_headers, financial_year, pan, holder_name)

        reco_sheet_title = f"{holder_name} Reco"[:31]
        ws_reco = wb.create_sheet(title=reco_sheet_title)
        _write_reconciliation_sheet(ws_reco, reco_df, financial_year, pan, holder_name)

    wb.save(filename)
    print(f"\nMulti-account report successfully generated and saved as '{filename}'")