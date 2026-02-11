import json
from collections import defaultdict
from .loader import load_sales_strict
from .timezones import tz_for_country
from .momentum import MOMENTA, get_momentum
from .buckets import BUCKETS, get_bucket

def compute_signature(csv_path: str, country_profile: str):
    tz = tz_for_country(country_profile)

    rev_m = defaultdict(float)
    rev_mb = defaultdict(lambda: defaultdict(float))

    total_all = 0.0
    total_classified = 0.0
    rows_total = 0
    rows_classified = 0
    rows_unclassified = 0

    for r in load_sales_strict(csv_path):
        rows_total += 1
        revenue = r["price"] * r["quantity"]
        total_all += revenue

        bucket = get_bucket(r["cat0"], r["cat1"], r["cat2"])
        if not bucket:
            rows_unclassified += 1
            continue

        local_dt = r["datetime_utc"].astimezone(tz)
        m = get_momentum(local_dt.hour)

        total_classified += revenue
        rows_classified += 1
        rev_m[m] += revenue
        rev_mb[m][bucket] += revenue

    sig = {
        "revenue_by_momentum": {m: 0.0 for m in MOMENTA},
        "category_mix_by_momentum": {m: {b: 0.0 for b in BUCKETS} for m in MOMENTA},
        "stats": {
            "rows_total_parsed": rows_total,
            "rows_classified": rows_classified,
            "rows_unclassified": rows_unclassified,
            "revenue_total_all": total_all,
            "revenue_total_classified": total_classified,
            "classified_revenue_ratio": (total_classified / total_all) if total_all > 0 else 0.0,
        },
    }

    if total_classified <= 0:
        return sig

    for m in MOMENTA:
        sig["revenue_by_momentum"][m] = rev_m[m] / total_classified
        denom = rev_m[m]
        if denom > 0:
            for b in BUCKETS:
                sig["category_mix_by_momentum"][m][b] = rev_mb[m][b] / denom

    return sig
