import json
import math
from typing import Any, Dict, List, Union

MOMENTA = ["breakfast", "lunch", "coffee", "apero", "dinner", "after"]
BUCKETS = ["food", "hot", "soft", "beer", "wine", "spirits"]

def l1_on_keys(v1, v2, keys):
    """
    Normalized L1 distance on a canonical list of keys.
    Missing keys are treated as 0.
    """
    return sum(abs(v1.get(k, 0.0) - v2.get(k, 0.0)) for k in keys) / 2


def _load_patterns(patterns_source: Union[str, List[Dict[str, Any]]]):
    if isinstance(patterns_source, str):
        with open(patterns_source, "r", encoding="utf-8") as f:
            return json.load(f)
    return patterns_source


def score_against_patterns(signature: Dict[str, Any], patterns_source: Union[str, List[Dict[str, Any]]]):
    """
    Compute normalized probabilities for each pattern_id.
    Supports:
      - patterns_source as a JSON file path (CLI)
      - patterns_source as an in-memory list (WebApp / DB)
    """
    patterns = _load_patterns(patterns_source)

    if not patterns:
        return {}

    scores = {}

    for pattern in patterns:
        # 1) Temporal distance (revenue distribution over momenta)
        dt = l1_on_keys(
            signature["revenue_by_momentum"],
            pattern["dimensions"]["revenue_by_momentum"],
            MOMENTA
        )

        # 2) Product mix distance weighted by pattern's momentum weights
        dm = 0.0
        for m in MOMENTA:
            weight = pattern["dimensions"]["revenue_by_momentum"].get(m, 0.0)

            outlet_mix = signature["category_mix_by_momentum"].get(m, {})
            pattern_mix = pattern["dimensions"]["category_mix_by_momentum"].get(m, {})

            dm += weight * l1_on_keys(outlet_mix, pattern_mix, BUCKETS)

        # Global distance
        d_total = 0.6 * dt + 0.4 * dm
        scores[pattern["pattern_id"]] = math.exp(-d_total)

    # Normalize to probabilities
    total_score = sum(scores.values())
    if total_score == 0:
        return {}

    return {k: v / total_score for k, v in scores.items()}
