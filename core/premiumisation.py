import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple


def normalize_brand(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("’", "'")
    return s


def build_segment_path(row: Dict[str, Any], fields: List[str]) -> str:
    parts: List[str] = []
    for f in fields:
        v = row.get(f)
        if v is None:
            continue
        v = str(v).strip()
        if not v:
            continue
        parts.append(v)
    return " > ".join(parts)


def longest_prefix_match(segment_path: str, rules: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    seg = (segment_path or "").strip().lower()
    best: Dict[str, Any] | None = None
    best_len = -1
    for r in rules or []:
        pref = str(r.get("segment_prefix", "")).strip().lower()
        if not pref:
            continue
        if seg.startswith(pref) and len(pref) > best_len:
            best = r
            best_len = len(pref)
    return best


@dataclass
class PremiumScoreResult:
    classification: str
    score: float
    brand_index: float
    ticket_index: float
    segment_multiplier: float
    segment_key: str
    avg_ticket: float
    p50: float | None
    p80: float | None
    p95: float | None
    known_brand_revenue_ratio: float
    top_brands: List[Dict[str, Any]]


def compute_brand_index(rows: Iterable[Dict[str, Any]], profile: Dict[str, Any]) -> Tuple[float, float, List[Dict[str, Any]]]:
    """Returns (brand_index in [1,3], known_revenue_ratio, top_brands breakdown)."""
    bref = profile.get("brand_reference", {})
    levels = bref.get("levels", {})
    lvl_score = {normalize_brand(k): float(v.get("score")) if isinstance(v, dict) else float(v)
                 for k, v in levels.items()}

    brands_list = bref.get("brands", [])
    mapping: Dict[str, Tuple[str, float]] = {}
    for b in brands_list:
        bn = normalize_brand(b.get("brand", ""))
        if not bn:
            continue
        level = str(b.get("level", "Mainstream"))
        w = float(b.get("weight", 1.0) or 1.0)
        mapping[bn] = (level, w)

    total_rev = 0.0
    known_rev = 0.0
    weighted_sum = 0.0
    by_brand: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        rev = r.get("subtotal")
        if rev is None:
            # fallback if subtotal missing
            price = r.get("price") or 0
            qty = r.get("quantity") or 0
            try:
                rev = float(price) * float(qty)
            except Exception:
                rev = 0.0
        try:
            rev = float(rev)
        except Exception:
            rev = 0.0
        if rev <= 0:
            continue
        total_rev += rev

        bname = normalize_brand(r.get("brand", ""))
        if bname in mapping:
            level, w = mapping[bname]
            score = lvl_score.get(normalize_brand(level), lvl_score.get(normalize_brand(level), None))
            # level keys are Mainstream/Premium/Prestige; normalize lookup
            if score is None:
                # try direct
                score = float(levels.get(level, 2.0)) if not isinstance(levels.get(level), dict) else float(levels.get(level, {}).get("score", 2.0))
            known_rev += rev
            weighted_sum += rev * float(score) * w
            bb = by_brand.setdefault(bname, {"brand": r.get("brand"), "level": level, "revenue": 0.0})
            bb["revenue"] += rev
        else:
            # unknown: neutral => ignored from numerator and denominator (known_rev)
            pass

    if known_rev <= 0:
        # no known brands => neutral midpoint
        brand_index = 2.0
    else:
        brand_index = weighted_sum / known_rev
        # clamp for safety
        brand_index = max(1.0, min(3.0, brand_index))

    ratio = (known_rev / total_rev) if total_rev > 0 else 0.0
    top = sorted(by_brand.values(), key=lambda x: x.get("revenue", 0.0), reverse=True)
    top_n = int(profile.get("output", {}).get("top_brands_to_return", 10) or 10)
    return brand_index, ratio, top[:top_n]


def compute_avg_ticket(rows: Iterable[Dict[str, Any]], guest_default: int = 1) -> float:
    by_order: Dict[str, float] = {}
    for r in rows:
        oid = r.get("order_id")
        if oid is None:
            continue
        oid = str(oid)
        sub = r.get("subtotal")
        if sub is None:
            try:
                sub = float(r.get("price", 0)) * float(r.get("quantity", 0))
            except Exception:
                sub = 0.0
        try:
            sub = float(sub)
        except Exception:
            sub = 0.0
        if sub <= 0:
            continue
        by_order[oid] = by_order.get(oid, 0.0) + sub
    if not by_order:
        return 0.0
    return sum(by_order.values()) / len(by_order)


def ticket_index_from_percentiles(avg_ticket: float, p50: float, p80: float) -> float:
    if avg_ticket <= 0:
        return 2.0
    if avg_ticket < p50:
        return 1.0
    if avg_ticket < p80:
        return 2.0
    return 3.0


def classify(score: float, thresholds: Dict[str, Any]) -> str:
    mainstream_max = float(thresholds.get("mainstream_max", 1.65))
    premium_max = float(thresholds.get("premium_max", 2.35))
    if score <= mainstream_max:
        return "Mainstream"
    if score <= premium_max:
        return "Premium"
    return "Prestige"


def score_outlet(
    rows: List[Dict[str, Any]],
    profile: Dict[str, Any],
    percentiles: Dict[str, Tuple[float, float, float]] | None = None,
) -> PremiumScoreResult:
    percentiles = percentiles or {}
    seg_fields = profile.get("segment_path_fields") or profile.get("segment_path_fields")
    seg_fields = seg_fields or profile.get("segment_path_fields")

    # segment path
    fields = profile.get("segment_path_fields")
    if not fields:
        # default to the 4 standard ones
        fields = ["market_segment_type0", "market_segment_type1", "market_segment_type2", "market_segment_type3"]

    segment_path = build_segment_path(rows[0] if rows else {}, fields)
    segment_key = segment_path.lower()

    # segment factor
    seg_conf = profile.get("scoring", {}).get("segment_factor", {})
    seg_rules = seg_conf.get("rules", [])
    rule = longest_prefix_match(segment_path, seg_rules)
    multiplier = float((rule or {}).get("multiplier", seg_conf.get("default_multiplier", 1.0)) or 1.0)
    max_score = (rule or {}).get("max_final_score")
    min_score = (rule or {}).get("min_final_score")

    # brand index
    brand_index, known_ratio, top_brands = compute_brand_index(rows, profile)

    # ticket percentiles lookup (longest prefix)
    p50 = p80 = p95 = None
    if percentiles:
        best = None
        best_len = -1
        for k, vals in percentiles.items():
            kk = (k or "").strip().lower()
            if kk and segment_key.startswith(kk) and len(kk) > best_len:
                best = vals
                best_len = len(kk)
        if best is None and "country_all" in percentiles:
            best = percentiles["country_all"]
        if best is not None:
            p50, p80, p95 = best

    # avg ticket
    guest_default = int(profile.get("ticket_rules", {}).get("guest_count_missing_default", 1) or 1)
    avg_ticket = compute_avg_ticket(rows, guest_default)
    if p50 is None or p80 is None:
        # fallback if no percentiles yet: use naive cutoffs
        p50, p80, p95 = (20.0, 35.0, 60.0)
    ticket_idx = ticket_index_from_percentiles(avg_ticket, float(p50), float(p80))

    # weighted sum
    w = profile.get("scoring", {}).get("weights", {})
    wb = float(w.get("brand_index", 0.6))
    wt = float(w.get("ticket_index", 0.3))
    ws = float(w.get("segment_factor", 0.1))

    base = wb * brand_index + wt * ticket_idx + ws * 2.0  # segment factor is applied as multiplier, keep neutral 2.0 here
    score = base * multiplier
    if max_score is not None:
        score = min(score, float(max_score))
    if min_score is not None:
        score = max(score, float(min_score))

    cls = classify(score, profile.get("thresholds", {}))
    return PremiumScoreResult(
        classification=cls,
        score=float(score),
        brand_index=float(brand_index),
        ticket_index=float(ticket_idx),
        segment_multiplier=float(multiplier),
        segment_key=segment_path,
        avg_ticket=float(avg_ticket),
        p50=float(p50) if p50 is not None else None,
        p80=float(p80) if p80 is not None else None,
        p95=float(p95) if p95 is not None else None,
        known_brand_revenue_ratio=float(known_ratio),
        top_brands=top_brands,
    )
