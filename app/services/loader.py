import csv
from datetime import datetime
from zoneinfo import ZoneInfo

def _to_float(x):
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

def load_sales_strict(csv_path: str):
    '''
    Strict columns expected:
    fyre_id,product_name,cat0,cat1,cat2,price,quantity,datetime
    datetime: "YYYY-MM-DD HH:MM:SS UTC"
    '''
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dt_raw = (row.get("datetime") or "").strip()
            if not dt_raw:
                continue
            # accept "... UTC" or ISO forms
            try:
                if dt_raw.endswith(" UTC"):
                    dt = datetime.fromisoformat(dt_raw.replace(" UTC",""))
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                else:
                    dt = datetime.fromisoformat(dt_raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            except Exception:
                continue

            price = _to_float(row.get("price"))
            qty = _to_float(row.get("quantity"))
            if price is None or qty is None:
                continue

            yield {
                "datetime_utc": dt,
                "price": price,
                "quantity": qty,
                "cat0": (row.get("cat0") or "").strip(),
                "cat1": (row.get("cat1") or "").strip(),
                "cat2": (row.get("cat2") or "").strip(),
            }
