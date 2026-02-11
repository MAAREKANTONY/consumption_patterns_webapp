# MVP fixed momenta (can be country-specific later)
# hour is LOCAL hour in outlet timezone

MOMENTA = ["breakfast", "lunch", "coffee", "apero", "dinner", "after"]

def get_momentum(local_hour: int) -> str:
    h = int(local_hour)
    if 6 <= h < 11:
        return "breakfast"
    if 11 <= h < 14:
        return "lunch"
    if 14 <= h < 17:
        return "coffee"
    if 17 <= h < 20:
        return "apero"
    if 20 <= h < 23:
        return "dinner"
    return "after"
