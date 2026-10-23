# filename: utils.py
# This module contains general utility functions.

import pandas as pd

def inr_format(x: float) -> str:
    """Format number into a readable INR format."""
    if pd.isna(x) or not isinstance(x, (int, float)):
        return "N/A"
    return f"{x:,.2f}"
