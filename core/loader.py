import csv
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

def _to_float(x):
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    # support "12,34"
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

def _pick(row, *names):
    """Return first matching column value among possible names (case-sensitive)."""
    for n in names:
        if n in row:
            return row.get(n)
    return None

def _parse_datetime(row, tz_name: str):
    """
    Accepts:
      - datetime in format 'YYYY-MM-DD HH:MM:SS UTC'
      - ISO datetime
      - purchase_date + purchase_hour

    Strategy:
      - Treat naive datetimes as UTC (unless already tz-aware)
      - Convert to tz_name (country profile timezone)
    """
    dt_raw = _pick(row, "datetime", "purchase_datetime")

    target_tz = ZoneInfo(tz_name)
    if dt_raw:
        dt_raw = str(dt_raw).strip()
        try:
            # Format: 2025-04-18 11:48:00 UTC
            if dt_raw.endswith(" UTC"):
                dt_raw = dt_raw.replace(" UTC", "")
                dt = datetime.fromisoformat(dt_raw)
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                return dt.astimezone(target_tz)

            # ISO with timezone or naive
            dt = datetime.fromisoformat(dt_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return dt.astimezone(target_tz)
        except Exception:
            return None

    # fallback legacy format
    date_raw = _pick(row, "purchase_date")
    hour_raw = _pick(row, "purchase_hour")
    try:
        if date_raw and hour_raw:
            dt = datetime.fromisoformat(f"{date_raw} {int(hour_raw):02d}:00:00")
            # legacy assumed local time in target tz
            dt = dt.replace(tzinfo=target_tz)
            return dt
    except Exception:
        return None

    return None

def _iter_rows_from_csv(path: str):
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        # robust delimiter detection (, ; \t)
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            dialect = csv.excel  # default comma

        reader = csv.DictReader(f, dialect=dialect)
        for row in reader:
            yield row

def _iter_rows_from_excel(path: str):
    # Lazy import to keep base install lighter if excel not needed
    import pandas as pd

    df = pd.read_excel(path, engine="openpyxl")
    # Normalize columns to strings
    df.columns = [str(c) for c in df.columns]
    for _, r in df.iterrows():
        # Convert NaN to None
        row = {k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in r.to_dict().items()}
        yield row

def load_sales(path: str, tz_name: str = "Europe/Paris"):
    """
    Yields dict rows in canonical shape:
      datetime, price, quantity, datetime, price, quantity, cat0, cat1, cat2, cat3, cat4

    Input accepted:
      - CSV (delimiter auto-detected among , ; \t)
      - XLSX (via pandas/openpyxl)

    Accepts taxonomy columns named:
      - cat0..cat4
      - category0..category4
      - category_produit0..category_produit4

    Accepts datetime columns named:
      - datetime / purchase_datetime
      - purchase_date + purchase_hour

    Accepts price/quantity columns named:
      - price, quantity (default)
      - unit_price, qty (fallback)
      - unitPrice, qte (fallback)
    """
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in [".xlsx", ".xls"]:
        rows_iter = _iter_rows_from_excel(path)
    else:
        rows_iter = _iter_rows_from_csv(path)

    for row in rows_iter:
        dt = _parse_datetime(row, tz_name=tz_name)
        if dt is None:
            continue

        price = _to_float(_pick(row, "price", "unit_price", "unitPrice"))
        qty = _to_float(_pick(row, "quantity", "qty", "qte"))

        if price is None or qty is None:
            continue

        cat0 = _pick(row, "cat0", "category0", "category_produit0")
        cat1 = _pick(row, "cat1", "category1", "category_produit1")
        cat2 = _pick(row, "cat2", "category2", "category_produit2")
        cat3 = _pick(row, "cat3", "category3", "category_produit3")
        cat4 = _pick(row, "cat4", "category4", "category_produit4")

        # normalize taxonomy strings (strip only; no guessing)
        cat0 = (str(cat0).strip() if cat0 is not None else "")
        cat1 = (str(cat1).strip() if cat1 is not None else "")
        cat2 = (str(cat2).strip() if cat2 is not None else "")
        cat3 = (str(cat3).strip() if cat3 is not None else "")
        cat4 = (str(cat4).strip() if cat4 is not None else "")

        yield {
            "datetime": dt,
            "price": price,
            "quantity": qty,
            "cat0": cat0,
            "cat1": cat1,
            "cat2": cat2,
            "cat3": cat3,
            "cat4": cat4,
        }
