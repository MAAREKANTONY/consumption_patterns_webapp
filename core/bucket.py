def get_bucket(cat0, cat1, cat2):
    """
    Strict taxonomy mapping (no guessing).
    Returns one of: food, hot, soft, beer, wine, spirits
    Returns None if unclassified / not matching.
    """

    # if any taxonomy piece is missing, treat as unclassified
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

