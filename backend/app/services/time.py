from datetime import datetime, timezone


def utc_for_api(value: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC value for JSON/API responses.

    SQLite stores the application's UTC timestamps as naive datetimes. Leaving
    them naive in JSON makes browsers interpret them as local time, shifting
    every displayed task and log timestamp on non-UTC machines.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
