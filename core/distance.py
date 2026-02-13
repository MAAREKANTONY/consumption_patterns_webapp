import json
import math
from typing import Any, Dict, List, Union

MOMENTA = ["breakfast", "lunch", "coffee", "apero", "dinner", "after"]
BUCKETS = ["food", "hot", "soft", "beer", "wine", "spirits"]


def l1_on_keys(v1: Dict[str, float], v2: Dict[str, float], keys: List[str]) -> float:
    """
    Normalized L1 distance on a canonical list of keys.
    Missing keys are treated as 0.

    We divide by 2 so that the distance is in [0, 1] when both vectors are
    valid probability distributions (sum to 1).
    """
    return sum(abs(v1.get(k, 0.0) - v2.get(k, 0.0)) for k in keys) / 2.0


def _load_patterns(patterns_source: Union[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    if isinstance(patterns_source, str):
        with open(patterns_source, "r", encoding="utf-8") as f:
            return json.load(f)
    return patterns_source


def score_against_patterns(signature: Dict[str, Any], patterns_source: Union[str, List[Dict[str, Any]]]) -> Dict[str, float]:
    """
    Compute normalized probabilities for each pattern_id.

    - Temporal distance: revenue distribution over momenta
    - Product mix distance: weighted by the pattern's momentum weights

    If BOTH signature and pattern provide `category_mix_by_momentum_taxonomy`,
    we score on taxonomy keys (union of keys per momentum). Otherwise we fall
    back to the 6-bucket mix (food/hot/soft/beer/wine/spirits).
    """
    patterns = _load_patterns(patterns_source)
    if not patterns:
        return {}

    scores: Dict[str, float] = {}

    for pattern in patterns:
        # 1) Temporal distance
        dt = l1_on_keys(
            signature.get("revenue_by_momentum", {}) or {},
            pattern.get("dimensions", {}).get("revenue_by_momentum", {}) or {},
            MOMENTA,
        )

        # 2) Product mix distance
        use_taxonomy = (
            isinstance(signature.get("category_mix_by_momentum_taxonomy"), dict)
            and isinstance(pattern.get("dimensions", {}).get("category_mix_by_momentum_taxonomy"), dict)
            and any((signature.get("category_mix_by_momentum_taxonomy") or {}).get(mm) for mm in MOMENTA)
        )

        dm = 0.0
        for m in MOMENTA:
            weight = (pattern.get("dimensions", {}).get("revenue_by_momentum", {}) or {}).get(m, 0.0)

            if use_taxonomy:
                outlet_mix = (signature.get("category_mix_by_momentum_taxonomy", {}) or {}).get(m, {}) or {}
                pattern_mix = (pattern.get("dimensions", {}).get("category_mix_by_momentum_taxonomy", {}) or {}).get(m, {}) or {}
                keys = sorted(set(outlet_mix.keys()) | set(pattern_mix.keys()))
                if not keys:
                    continue
                dm += weight * l1_on_keys(outlet_mix, pattern_mix, keys)
            else:
                outlet_mix = (signature.get("category_mix_by_momentum", {}) or {}).get(m, {}) or {}
                pattern_mix = (pattern.get("dimensions", {}).get("category_mix_by_momentum", {}) or {}).get(m, {}) or {}
                dm += weight * l1_on_keys(outlet_mix, pattern_mix, BUCKETS)

        # Global distance + score
        d_total = 0.6 * dt + 0.4 * dm
        scores[pattern.get("pattern_id", "")] = math.exp(-d_total)

    # Normalize to probabilities
    total_score = sum(scores.values())
    if total_score == 0:
        return {}

    return {k: v / total_score for k, v in scores.items() if k}
