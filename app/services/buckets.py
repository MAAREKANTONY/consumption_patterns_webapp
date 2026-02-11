# Buckets per your strict taxonomy

BUCKETS = ["food", "hot", "soft", "beer", "wine", "spirits"]

def get_bucket(cat0: str, cat1: str, cat2: str):
    cat0 = (cat0 or "").strip()
    cat1 = (cat1 or "").strip()
    cat2 = (cat2 or "").strip()

    if not cat0:
        return None

    if cat0 == "Food":
        return "food"

    if cat0 == "Beverage" and cat1 == "Adult Beverages":
        if cat2 == "Beers & Ciders":
            return "beer"
        if cat2 == "Wines":
            return "wine"
        if cat2 == "Spirits":
            return "spirits"

    if cat0 == "Beverage" and cat1 == "Non-Alcoholic Beverages":
        return "soft"

    if cat0 == "Beverage" and cat1 == "Hot Beverages":
        return "hot"

    return None
