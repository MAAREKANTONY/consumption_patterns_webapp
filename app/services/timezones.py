from zoneinfo import ZoneInfo

COUNTRY_TIMEZONE = {
    "FR": "Europe/Paris",
    "UK": "Europe/London",
    "ES": "Europe/Madrid",
    "IL": "Asia/Jerusalem",
}

def tz_for_country(country_profile: str) -> ZoneInfo:
    name = COUNTRY_TIMEZONE.get((country_profile or "").upper(), "UTC")
    return ZoneInfo(name)
