import json
import math
from .momentum import MOMENTA
from .buckets import BUCKETS

def _safe_get(d, *keys, default=0.0):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def euclidean_distance(sig_dims: dict, pat_dims: dict) -> float:
    # Both are expected to have revenue_by_momentum + category_mix_by_momentum
    s = 0.0

    for m in MOMENTA:
        a = _safe_get(sig_dims, "revenue_by_momentum", m, default=0.0)
        b = _safe_get(pat_dims, "revenue_by_momentum", m, default=0.0)
        s += (a - b) ** 2

    for m in MOMENTA:
        for bkt in BUCKETS:
            a = _safe_get(sig_dims, "category_mix_by_momentum", m, bkt, default=0.0)
            b = _safe_get(pat_dims, "category_mix_by_momentum", m, bkt, default=0.0)
            s += (a - b) ** 2

    return math.sqrt(s)

def softmax_prob_from_dist(distances, temperature=1.0):
    # distances: list of floats; smaller is better
    # prob ~ exp(-d / T)
    import math
    if not distances:
        return []
    exps = [math.exp(-(d / max(temperature, 1e-9))) for d in distances]
    Z = sum(exps)
    if Z <= 0:
        return [0.0 for _ in exps]
    return [e / Z for e in exps]
