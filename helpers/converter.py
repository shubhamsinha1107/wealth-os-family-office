# filename: converter.py
# This module provides utilities for data conversion and caching.

import io
import os
import pandas as pd

def convert_cas_to_dataframe(cas_csv_data):
    """Converts the CSV string output from casparser to a pandas DataFrame."""
    if not cas_csv_data:
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(cas_csv_data))

def save_to_feather(df, file_path):
    """Saves a DataFrame to a feather file, creating the directory if it doesn't exist."""
    try:
        # Ensure the parent directory exists
        directory = os.path.dirname(file_path)
        if not os.path.exists(directory):
            os.makedirs(directory)
        
        # Save the dataframe to a feather file
        df.to_feather(file_path)
        print(f"Successfully saved data to {file_path}")
    except Exception as e:
        print(f"Error saving to feather file {file_path}: {e}")

def load_from_feather(file_path):
    """Loads a DataFrame from a feather file if it exists, otherwise returns None."""
    if os.path.exists(file_path):
        try:
            print(f"Loading data from cache: {file_path}...")
            return pd.read_feather(file_path)
        except Exception as e:
            print(f"Error loading from feather file {file_path}: {e}")
            return None
    return None
