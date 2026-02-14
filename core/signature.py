from collections import defaultdict
from core.loader import load_sales
from core.momentum import get_momentum
from core.bucket import get_bucket
import re

MOMENTA = ["breakfast", "lunch", "coffee", "apero", "dinner", "after"]
BUCKETS = ["food", "hot", "soft", "beer", "wine", "spirits"]


def _is_wine_like(parts: list[str]) -> bool:
    # Wine-like if any segment contains "wine(s)", "champagne" or "sparkling"
    for p in parts:
        pl = p.lower()
        if "champagne" in pl or "sparkling" in pl:
            return True
        if re.search(r"\bwine(s)?\b", p, flags=re.IGNORECASE):
            return True
    return False


def rollup_taxonomy_parts(parts: list[str]) -> list[str]:
    """
    Preset 2 ("resto-friendly"):
      - Food: depth=3  -> category0 > category1 > category2
      - Beverage (non-wine): depth=3
      - Beverage (wine-like): depth=4  -> keep category3 (e.g. Wines > Red Wines)
    """
    parts = [p.strip() for p in parts if isinstance(p, str) and p.strip() != ""]
    if not parts:
        return []

    root = parts[0].lower()
    is_food = root == "food"
    is_beverage = root in ("beverage", "drink", "drinks")

    if is_food:
        depth = 3
    elif is_beverage:
        depth = 4 if _is_wine_like(parts) else 3
    else:
        depth = 3

    return parts[: min(depth, len(parts))]


def rollup_taxonomy_key_from_string(key: str) -> str:
    parts = [p.strip() for p in str(key).split(">")]
    parts = rollup_taxonomy_parts(parts)
    return " > ".join(parts) if parts else ""


def _taxonomy_key(cat0: str, cat1: str, cat2: str, cat3: str = "", cat4: str = "") -> str:
    """Build a rolled-up taxonomy path key from cat0..cat4 (Preset 2)."""
    parts = [cat0, cat1, cat2, cat3, cat4]
    parts = rollup_taxonomy_parts(parts)
    if not parts:
        return ""
    return " > ".join(parts)

def compute_outlet_signature(path: str, tz_name: str = "Europe/Paris"):
    """
    Compute outlet signature from a sales file (CSV or XLSX).

    Output:
      - revenue_by_momentum (shares, sum=1 over *bucket-classified* revenue)
      - category_mix_by_momentum (6 buckets per momentum)
      - category_mix_by_momentum_taxonomy (taxonomy path keys per momentum)
      - stats (data-quality)
    """
    rev_m = defaultdict(float)
    rev_mb = defaultdict(lambda: defaultdict(float))  # momentum -> bucket -> revenue

    rev_mt = defaultdict(lambda: defaultdict(float))  # momentum -> taxonomy_key -> revenue

    total_revenue_all = 0.0
    total_revenue_bucket_classified = 0.0
    total_revenue_taxonomy_classified = 0.0

    rows_total = 0
    rows_bucket_classified = 0
    rows_taxonomy_classified = 0
    rows_unclassified = 0

    for r in load_sales(path, tz_name=tz_name):
        rows_total += 1

        revenue = r["price"] * r["quantity"]
        total_revenue_all += revenue

        momentum = get_momentum(r["datetime"].hour)

        # --- taxonomy mix (cat0..cat4) ---
        tax_key = _taxonomy_key(r.get("cat0",""), r.get("cat1",""), r.get("cat2",""), r.get("cat3",""), r.get("cat4",""))
        if tax_key:
            total_revenue_taxonomy_classified += revenue
            rows_taxonomy_classified += 1
            rev_mt[momentum][tax_key] += revenue

        # --- bucket mix (legacy 6 categories) ---
        bucket = get_bucket(r.get("cat0",""), r.get("cat1",""), r.get("cat2",""))
        if not bucket:
            rows_unclassified += 1
            continue

        total_revenue_bucket_classified += revenue
        rows_bucket_classified += 1
        rev_m[momentum] += revenue
        rev_mb[momentum][bucket] += revenue

    signature = {
        "revenue_by_momentum": {m: 0.0 for m in MOMENTA},
        "category_mix_by_momentum": {m: {b: 0.0 for b in BUCKETS} for m in MOMENTA},
        "category_mix_by_momentum_taxonomy": {m: {} for m in MOMENTA},
        "stats": {
            "rows_total_parsed": rows_total,
            "rows_classified": rows_bucket_classified,
            "rows_unclassified": rows_unclassified,
            "revenue_total_all": total_revenue_all,
            "revenue_total_classified": total_revenue_bucket_classified,
            "classified_revenue_ratio": (total_revenue_bucket_classified / total_revenue_all) if total_revenue_all > 0 else 0.0,

            # Extra visibility for taxonomy coverage
            "rows_taxonomy_classified": rows_taxonomy_classified,
            "revenue_total_taxonomy_classified": total_revenue_taxonomy_classified,
            "taxonomy_classified_revenue_ratio": (total_revenue_taxonomy_classified / total_revenue_all) if total_revenue_all > 0 else 0.0,
        }
    }

    # Normalize bucket-based distributions (kept as the "main" signature stats, like the original CLI)
    if total_revenue_bucket_classified > 0:
        for m in MOMENTA:
            signature["revenue_by_momentum"][m] = rev_m[m] / total_revenue_bucket_classified
            denom = rev_m[m]
            if denom > 0:
                for b in BUCKETS:
                    signature["category_mix_by_momentum"][m][b] = rev_mb[m][b] / denom

    # Normalize taxonomy mix per momentum (denominator = revenue in that momentum for taxonomy-classified rows)
    for m in MOMENTA:
        denom = sum(rev_mt[m].values())
        if denom > 0:
            signature["category_mix_by_momentum_taxonomy"][m] = {
                k: (v / denom) for k, v in sorted(rev_mt[m].items(), key=lambda kv: (-kv[1], kv[0]))
            }

    return signature
