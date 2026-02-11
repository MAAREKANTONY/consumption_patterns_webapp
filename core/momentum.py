def get_momentum(hour):
    if 6 <= hour < 11: return "breakfast"
    if 11 <= hour < 14: return "lunch"
    if 14 <= hour < 17: return "coffee"
    if 17 <= hour < 20: return "apero"
    if 20 <= hour < 23: return "dinner"
    return "after"
