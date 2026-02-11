from collections import defaultdict
from core.loader import load_sales
from core.momentum import get_momentum
from core.bucket import get_bucket

MOMENTA = ["breakfast", "lunch", "coffee", "apero", "dinner", "after"]
BUCKETS = ["food", "hot", "soft", "beer", "wine", "spirits"]

def compute_outlet_signature(path: str, tz_name: str = "Europe/Paris"):
    """
    Compute outlet signature from a sales file (CSV or XLSX).

    Output structure is the same as the CLI:
      - revenue_by_momentum (shares, sum=1 over classified revenue)
      - category_mix_by_momentum (shares per momentum, sum=1 if momentum revenue > 0)
      - stats (data-quality)
    """
    rev_m = defaultdict(float)
    rev_mb = defaultdict(lambda: defaultdict(float))

    total_revenue_all = 0.0
    total_revenue_classified = 0.0

    rows_total = 0
    rows_classified = 0
    rows_unclassified = 0

    for r in load_sales(path, tz_name=tz_name):
        rows_total += 1

        revenue = r["price"] * r["quantity"]
        total_revenue_all += revenue

        bucket = get_bucket(r["cat0"], r["cat1"], r["cat2"])
        if not bucket:
            rows_unclassified += 1
            continue

        momentum = get_momentum(r["datetime"].hour)

        total_revenue_classified += revenue
        rows_classified += 1
        rev_m[momentum] += revenue
        rev_mb[momentum][bucket] += revenue

    signature = {
        "revenue_by_momentum": {m: 0.0 for m in MOMENTA},
        "category_mix_by_momentum": {m: {b: 0.0 for b in BUCKETS} for m in MOMENTA},
        "stats": {
            "rows_total_parsed": rows_total,
            "rows_classified": rows_classified,
            "rows_unclassified": rows_unclassified,
            "revenue_total_all": total_revenue_all,
            "revenue_total_classified": total_revenue_classified,
            "classified_revenue_ratio": (total_revenue_classified / total_revenue_all) if total_revenue_all > 0 else 0.0,
        }
    }

    if total_revenue_classified == 0:
        # signature remains zeros; caller will decide "UNCLASSIFIABLE"
        return signature

    for m in MOMENTA:
        signature["revenue_by_momentum"][m] = rev_m[m] / total_revenue_classified

        denom = rev_m[m]
        if denom > 0:
            for b in BUCKETS:
                signature["category_mix_by_momentum"][m][b] = rev_mb[m][b] / denom

    return signature
