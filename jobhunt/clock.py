"""Hong Kong time, whatever machine the run happens on (the morning routine runs in UTC)."""

from datetime import date, datetime, timedelta, timezone

HKT = timezone(timedelta(hours=8))


def now() -> datetime:
    return datetime.now(HKT).replace(tzinfo=None)


def today() -> date:
    return now().date()
