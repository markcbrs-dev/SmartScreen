"""
main/utils/data_utils.py — CSV and data import helpers.

HOW TO MODIFY
-------------
  Import a CSV into SQLite  → use import_csv_to_sqlite(csv_path, sqlite_path, table_name)
  Import a CSV into a model → use import_csv_to_model(csv_path, model=MyModel, mapping={...})
  Change batch size         → adjust the batch_size parameter (default 500)

NOTE: These utilities are not called by any view. They are standalone helpers
intended for data setup / migration scripts or the Django shell.
"""

import logging
from typing import Optional, Type, Dict

import pandas as pd

logger = logging.getLogger(__name__)


def import_csv_to_sqlite(csv_path: str, sqlite_path: str = 'resumes.db', table_name: str = 'Resumes') -> None:
    """Read CSV and write to a sqlite database table (uses pandas.to_sql).

    This mirrors the original `sqlfilereader.py` behaviour but as a reusable helper.
    """
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()
    import sqlite3

    conn = sqlite3.connect(sqlite_path)
    try:
        df.to_sql(table_name, conn, if_exists='replace', index=False)
    finally:
        conn.close()


def import_csv_to_model(csv_path: str, model: Optional[Type] = None, mapping: Optional[Dict[str, str]] = None, batch_size: int = 500) -> int:
    """Import CSV rows into a Django model.

    Args:
        csv_path: path to CSV file.
        model: Django model class to create instances for. If None, function raises.
        mapping: Optional dict mapping CSV column -> model field name.
        batch_size: Number of objects to bulk_create per batch.

    Returns:
        Number of rows created.
    """
    if model is None:
        raise ValueError('model must be provided')

    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()
    created = 0
    objs = []

    for _, row in df.iterrows():
        fields = {}
        for col in df.columns:
            field_name = mapping.get(col, col) if mapping else col
            # only include fields that exist on the model
            if hasattr(model, '_meta') and field_name in {f.name for f in model._meta.get_fields()}:
                fields[field_name] = row[col]

        obj = model(**fields)
        objs.append(obj)
        if len(objs) >= batch_size:
            model.objects.bulk_create(objs)
            created += len(objs)
            objs = []

    if objs:
        model.objects.bulk_create(objs)
        created += len(objs)

    return created
