# filename: pdf_reader.py
# This module is responsible for reading and parsing data from CAS PDF files.

import casparser
import json
import pandas as pd
from typing import Dict, Any

def read_pdf_with_password(file_path: str, password: str, output: str = "csv") -> Any:
    """
    Reads a password-protected CAS PDF file, trying both uppercase and lowercase passwords.

    Args:
        file_path: The path to the PDF file.
        password: The password for the PDF file.
        output: The desired output format ('csv', 'json', etc.).

    Returns:
        The parsed PDF data in the specified format.
    """
    try:
        # First attempt: use the password in uppercase
        return casparser.read_cas_pdf(file_path, password.upper(), output=output)
    except Exception:
        # Fallback: try with the password in lowercase
        return casparser.read_cas_pdf(file_path, password.lower(), output=output)

def parse_cas_pdf_to_df(file_path: str, password: str, expected_pan: str) -> pd.DataFrame:
    """
    Parses a CAS PDF and converts it to a pandas DataFrame, filtering by PAN.
    This function is primarily used to extract holdings information.

    Args:
        file_path: The path to the PDF file.
        password: The password for the PDF file.
        expected_pan: The PAN to filter the data by.

    Returns:
        A pandas DataFrame containing the parsed and filtered data.
    """
    raw_data = read_pdf_with_password(file_path, password, output="json")
    data = json.loads(raw_data)

    scheme_rows = []
    for folio in data.get('folios', []):
        pan = folio.get('PAN', '').strip().upper()
        if pan != expected_pan.upper():
            continue
        amc = folio.get('amc', '')
        folio_number = folio.get('folio', '')
        for scheme in folio.get('schemes', []):
            scheme_rows.append({
                "PAN": pan,
                "AMC": amc,
                "Folio": folio_number,
                "Scheme Name": scheme.get('scheme', ''),
                "ISIN": scheme.get('isin', ''),
                "AMFI Code": scheme.get('amfi', ''),
                "RTA": scheme.get('rta', ''),
                "Advisor": scheme.get('advisor', ''),
                "NAV": scheme.get('valuation', {}).get('nav', ''),
                "Units": scheme.get('close', ''),
                "Current Value": scheme.get('valuation', {}).get('value', ''),
                "Cost Value": scheme.get('valuation', {}).get('cost', '')
            })
    return pd.DataFrame(scheme_rows)
